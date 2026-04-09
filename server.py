"""
Main server for printer interface system.

Supports both test and connected modes with hardware abstraction.
Stack: FastAPI + python-socketio (native async ASGI) + Uvicorn.
"""

import sys
import os
import asyncio
import argparse
import io
import time
import logging
import threading
from contextlib import asynccontextmanager
from typing import Optional

import yaml
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
import socketio

from hw.hardware_factory import create_hardware
from hw.abstract_hardware import CommandStatus, validate_coordinates

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def load_config(config_file: str) -> dict:
    """Load configuration from YAML file."""
    with open(config_file, 'r') as f:
        return yaml.safe_load(f)


def _check_path(config: dict, path: str) -> bool:
    """Return True if a dot-separated key path exists in config."""
    node = config
    for part in path.split('.'):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def validate_config(config: dict, mode: str) -> None:
    """
    Validate that all required config keys are present.

    Raises ValueError with a descriptive message on the first missing key.
    """
    shared_paths = [
        'printer.safe_limits.x_min', 'printer.safe_limits.x_max',
        'printer.safe_limits.y_min', 'printer.safe_limits.y_max',
        'printer.safe_limits.z_min', 'printer.safe_limits.z_max',
        'printer.move_feedrate_default',
        'camera.sharpness', 'camera.jpeg_quality',
        'stream.preview_width', 'stream.preview_height', 'stream.preview_fps',
    ]
    connected_paths = [
        'printer.serial_device', 'printer.baud_rate', 'printer.swap_yz_axes',
    ]
    test_paths = ['simulation.movement_delay']

    mode_paths = connected_paths if mode == 'connected' else test_paths

    for path in shared_paths + mode_paths:
        if not _check_path(config, path):
            raise ValueError(f"Missing required config key: '{path}'")


# ---------------------------------------------------------------------------
# Camera stream
# ---------------------------------------------------------------------------

class CameraStream:
    """Camera streaming and capture handler."""

    def __init__(self, config: dict, mode: str):
        self.config = config
        self.mode = mode
        self.picam2 = None
        self._lock = threading.Lock()

    def get_camera(self):
        """Lazily initialize and return the picamera2 instance."""
        if self.picam2 is None:
            with self._lock:
                if self.picam2 is None:
                    self._init_camera()
        return self.picam2

    def _init_camera(self):
        print("Initializing camera (this may take a few seconds)...", flush=True)
        from picamera2 import Picamera2

        try:
            self.picam2 = Picamera2(camera_num=0)
            print("  Camera object created (camera 0), configuring...", flush=True)
        except Exception as e:
            print(f"  Warning: Failed to initialize camera 0: {e}", flush=True)
            self.picam2 = Picamera2()
            print("  Camera object created (auto-detect), configuring...", flush=True)

        try:
            camera_info = self.picam2.camera_properties
            print(f"  Camera model: {camera_info.get('Model', 'Unknown')}", flush=True)
        except Exception:
            pass

        preview_width = self.config['stream']['preview_width']
        preview_height = self.config['stream']['preview_height']
        preview_fps = self.config['stream']['preview_fps']

        try:
            video_config = self.picam2.create_video_configuration(
                main={"size": (preview_width, preview_height)},
                controls={"FrameRate": preview_fps},
            )
        except Exception as e:
            print(f"  Warning: {e}. Falling back to 1280x720.", flush=True)
            preview_width, preview_height, preview_fps = 1280, 720, 15
            video_config = self.picam2.create_video_configuration(
                main={"size": (preview_width, preview_height)},
                controls={"FrameRate": preview_fps},
            )

        self.picam2.configure(video_config)
        self.picam2.start()
        time.sleep(1)  # Stabilization

        # Apply quality controls (sharpness, noise reduction, JPEG quality)
        try:
            available = self.picam2.camera_controls
            controls = {}
            if 'Sharpness' in available:
                controls['Sharpness'] = float(self.config['camera']['sharpness'])
            if 'NoiseReductionMode' in available:
                controls['NoiseReductionMode'] = 2
            if 'Quality' in available:
                controls['Quality'] = int(self.config['camera']['jpeg_quality'])
            if controls:
                self.picam2.set_controls(controls)
        except Exception as e:
            print(f"  Note: Could not set camera controls: {e}", flush=True)

        print("Camera initialization complete.", flush=True)

    async def generate_frames(self):
        """Async generator that yields MJPEG frames."""
        try:
            camera = self.get_camera()
        except Exception as e:
            logger.error(f"Failed to get camera: {e}")
            return

        fps = self.config['stream']['preview_fps']
        frame_count = 0
        print("Starting frame generation loop...", flush=True)

        while True:
            try:
                buf = io.BytesIO()
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: camera.capture_file(buf, format='jpeg')
                )
                buf.seek(0)
                frame_bytes = buf.getvalue()

                if not frame_bytes:
                    await asyncio.sleep(0.1)
                    continue

                frame_count += 1
                if frame_count == 1:
                    print(f"First frame captured: {len(frame_bytes)} bytes", flush=True)

                yield (
                    b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n'
                    + frame_bytes
                    + b'\r\n'
                )
                await asyncio.sleep(1.0 / fps)

            except Exception as e:
                logger.error(f"Frame capture error: {e}")
                await asyncio.sleep(1.0)

    async def capture(self) -> str:
        """Capture a high-resolution image and return the filename."""
        camera = self.get_camera()
        filename = f"capture_{int(time.time() * 1000)}.jpg"
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: camera.capture_file(filename, format='jpeg')
        )
        return filename

    def cleanup(self):
        """Release camera resources."""
        if self.picam2:
            try:
                self.picam2.stop()
                self.picam2.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Module-level state (set in main() before uvicorn starts)
# ---------------------------------------------------------------------------

mode: Optional[str] = None
config: Optional[dict] = None
hardware = None
camera_stream: Optional[CameraStream] = None

_telemetry_task: Optional[asyncio.Task] = None

ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', '*').split(',')


# ---------------------------------------------------------------------------
# SocketIO + FastAPI setup
# ---------------------------------------------------------------------------

sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins=ALLOWED_ORIGINS,
    logger=False,
    engineio_logger=False,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start hardware and camera; shut down on exit."""
    validate_config(config, mode)

    logger.info(f"Initializing {mode} hardware...")
    if not await hardware.initialize():
        logger.error(f"Failed to initialize {mode} hardware")
        sys.exit(1)

    # Inject camera stream into hardware for high-res capture
    hardware._camera_stream = camera_stream

    logger.info(f"{mode.capitalize()} hardware initialized")
    yield

    logger.info("Shutting down hardware...")
    await hardware.shutdown()
    camera_stream.cleanup()


fastapi_app = FastAPI(lifespan=lifespan)
fastapi_app.mount('/static', StaticFiles(directory='ui/static'), name='static')
templates = Jinja2Templates(directory='ui/templates')

# ASGIApp wraps FastAPI; uvicorn targets this
app = socketio.ASGIApp(sio, fastapi_app)


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

@fastapi_app.get('/', response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse('index.html', {'request': request, 'mode': mode})


@fastapi_app.get('/test')
async def test_route():
    return {'status': 'ok'}


@fastapi_app.get('/stream')
async def stream():
    return StreamingResponse(
        camera_stream.generate_frames(),
        media_type='multipart/x-mixed-replace; boundary=frame',
        headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
        },
    )


@fastapi_app.post('/capture')
async def capture():
    filename = await hardware.capture_high_res()
    return {'filename': filename, 'success': True}


@fastapi_app.get('/config')
async def get_config():
    """Return printer safe limits and settings for 3D visualization."""
    limits = config['printer']['safe_limits']
    return {
        'x_min': limits['x_min'], 'x_max': limits['x_max'],
        'y_min': limits['y_min'], 'y_max': limits['y_max'],
        'z_min': limits['z_min'], 'z_max': limits['z_max'],
        'move_feedrate_default': config['printer']['move_feedrate_default'],
    }


# ---------------------------------------------------------------------------
# Telemetry loop
# ---------------------------------------------------------------------------

async def telemetry_loop():
    """Broadcast telemetry to all connected clients every 0.5 s."""
    while True:
        try:
            telemetry = await hardware.get_telemetry()
            await sio.emit('telemetry.position', {
                'timestamp': telemetry.timestamp,
                'nozzle': {
                    'x': telemetry.nozzle.x,
                    'y': telemetry.nozzle.y,
                    'z': telemetry.nozzle.z,
                },
                'status': telemetry.status.value,
            })
        except Exception as e:
            logger.error(f"Telemetry error: {e}")
        await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# WebSocket helpers
# ---------------------------------------------------------------------------

def _ack_dict(ack) -> dict:
    return {
        'id': ack.id,
        'status': ack.status.value,
        'message': ack.message,
        'timestamp': ack.timestamp,
    }


def _error_ack(msg: str) -> dict:
    return {
        'id': 'validation_error',
        'status': CommandStatus.ERROR.value,
        'message': msg,
        'timestamp': time.time(),
    }


# ---------------------------------------------------------------------------
# WebSocket events
# ---------------------------------------------------------------------------

@sio.event
async def connect(sid, environ):
    global _telemetry_task
    logger.info(f"Client connected: {sid}")
    if _telemetry_task is None or _telemetry_task.done():
        _telemetry_task = asyncio.create_task(telemetry_loop())
    await sio.emit('status', {'mode': mode, 'connected': True}, to=sid)


@sio.event
async def disconnect(sid):
    logger.info(f"Client disconnected: {sid}")


@sio.on('cmd.move_nozzle')
async def handle_move_nozzle(sid, data):
    try:
        x = data['x']
        y = data['y']
        z = data['z']
        feedrate = data.get('feedrate', config['printer']['move_feedrate_default'])
        validate_coordinates(x, y, z, feedrate)
    except (KeyError, ValueError) as e:
        await sio.emit('telemetry.command_ack', _error_ack(str(e)), to=sid)
        return

    ack = await hardware.move_nozzle(x, y, z, feedrate)
    await sio.emit('telemetry.command_ack', _ack_dict(ack), to=sid)


@sio.on('cmd.move_nozzle_xy')
async def handle_move_nozzle_xy(sid, data):
    try:
        x = data['x']
        y = data['y']
        feedrate = data.get('feedrate', config['printer']['move_feedrate_default'])
        validate_coordinates(x, y, 0.0, feedrate)
    except (KeyError, ValueError) as e:
        await sio.emit('telemetry.command_ack', _error_ack(str(e)), to=sid)
        return

    ack = await hardware.move_nozzle_xy(x, y, feedrate)
    await sio.emit('telemetry.command_ack', _ack_dict(ack), to=sid)


@sio.on('cmd.move_nozzle_z')
async def handle_move_nozzle_z(sid, data):
    try:
        z = data['z']
        feedrate = data.get('feedrate', config['printer']['move_feedrate_default'])
        validate_coordinates(0.0, 0.0, z, feedrate)
    except (KeyError, ValueError) as e:
        await sio.emit('telemetry.command_ack', _error_ack(str(e)), to=sid)
        return

    ack = await hardware.move_nozzle_z(z, feedrate)
    await sio.emit('telemetry.command_ack', _ack_dict(ack), to=sid)


@sio.on('cmd.home_nozzle')
async def handle_home_nozzle(sid, data=None):
    ack = await hardware.home_nozzle()
    await sio.emit('telemetry.command_ack', _ack_dict(ack), to=sid)


@sio.on('cmd.emergency_stop')
async def handle_emergency_stop(sid, data=None):
    ack = await hardware.emergency_stop()
    await sio.emit('telemetry.command_ack', _ack_dict(ack), to=sid)


@sio.on('cmd.clear_emergency_stop')
async def handle_clear_emergency_stop(sid, data=None):
    ack = await hardware.clear_emergency_stop()
    await sio.emit('telemetry.command_ack', _ack_dict(ack), to=sid)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    global mode, config, hardware, camera_stream

    parser = argparse.ArgumentParser(description='Printer Interface Server')
    parser.add_argument(
        '--mode', choices=['test', 'connected'],
        default=os.getenv('MODE', 'test'),
        help='Hardware mode: test (simulation) or connected (real hardware)',
    )
    parser.add_argument('--config', default=None,
                        help='Configuration file path (default: config_{mode}.yml)')
    parser.add_argument('--port', type=int, default=5000, help='Port to run server on')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind server to')
    args = parser.parse_args()

    # Require SECRET_KEY in environment (security baseline)
    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key:
        print(
            "ERROR: SECRET_KEY environment variable not set. "
            "Set it before starting the server.",
            file=sys.stderr,
        )
        sys.exit(1)

    mode = args.mode
    config = load_config(args.config or f'config_{mode}.yml')
    hardware = create_hardware(mode, config)
    camera_stream = CameraStream(config, mode)

    print(f"Starting {mode} mode server on {args.host}:{args.port}")

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
