# Technical Reference

## Architecture

```
Browser
  │  HTTP GET /            → serves index.html (Jinja2)
  │  HTTP GET /stream      → MJPEG StreamingResponse (async generator)
  │  HTTP POST /capture    → high-res JPEG capture
  │  HTTP GET /config      → safe limits JSON
  │  WebSocket (SocketIO)  → commands + telemetry
  ▼
Uvicorn (ASGI)
  └─ socketio.ASGIApp
       ├─ socketio.AsyncServer   ← WebSocket events
       └─ FastAPI app            ← HTTP routes
            └─ HardwareInterface (abstract)
                 ├─ TestHardware       (simulation, no serial)
                 └─ ConnectedHardware  (pigpio + pyserial + picamera2)
```

Hardware is initialized inside a FastAPI `lifespan` context manager so startup failures abort the server cleanly. The telemetry loop runs as an `asyncio.Task` created on the first WebSocket connection.

---

## FSM — States and Transitions

The `HardwareInterface` base class embeds an `AsyncMachine` from the `transitions` library. Trigger methods are `await`-able.

### States

| State | Meaning |
|-------|---------|
| `idle` | Ready for commands |
| `moving` | Executing a G1 move |
| `homing` | Executing G28 |
| `emergency_stop` | M112 sent; all motion halted |
| `error` | Move or home failed |

### Transitions

| Trigger | Source | Destination |
|---------|--------|-------------|
| `begin_move` | idle | moving |
| `complete_move` | moving | idle |
| `fail_move` | moving | error |
| `begin_homing` | idle | homing |
| `complete_homing` | homing | idle |
| `fail_homing` | homing | error |
| `trigger_estop` | idle, moving, homing | emergency_stop |
| `clear_estop` | emergency_stop | idle |
| `clear_error` | error | idle |

`ignore_invalid_triggers=False` means an invalid transition raises `transitions.core.MachineError`, which is caught in command handlers and returned as an error `CommandAck`.

`self.state` (a string) is the canonical state. The `SystemStatus` enum is used only for wire serialization; `_STATE_TO_STATUS` maps between the two.

---

## Hardware Abstraction Layer (HAL)

`hw/abstract_hardware.py` defines `HardwareInterface`. Both mode implementations must implement every abstract method. The base class provides:

- FSM setup (`AsyncMachine`)
- `capture_high_res()` — delegates to the injected `CameraStream`
- `is_ready()` — returns `self.state == 'idle'`
- `validate_coordinates()` — standalone function; raises `ValueError` on non-numeric input

### Adding a New Control

1. **Abstract method** — add `@abstractmethod async def my_cmd(self, ...) -> CommandAck` to `HardwareInterface`.
2. **ConnectedHardware** — implement with G-code over serial; use FSM triggers if it changes motion state.
3. **TestHardware** — implement with `asyncio.sleep` simulation.
4. **server.py** — add `@sio.on('cmd.my_cmd') async def handle_my_cmd(sid, data): ...` and call `hardware.my_cmd(...)`.
5. **UI** — add button in `ui/templates/index.html` and emit `cmd.my_cmd` from `ui/static/js/app.js`.

---

## HTTP API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web UI (HTML) |
| `GET` | `/stream` | MJPEG camera stream |
| `POST` | `/capture` | Capture high-res image → `{"filename": "...", "success": true}` |
| `GET` | `/config` | Printer limits → `{x_min, x_max, y_min, y_max, z_min, z_max, move_feedrate_default}` |

---

## WebSocket Events (SocketIO)

### Client → Server (commands)

| Event | Payload | Description |
|-------|---------|-------------|
| `cmd.move_nozzle` | `{x, y, z, feedrate?}` | Move nozzle to absolute position |
| `cmd.move_nozzle_xy` | `{x, y, feedrate?}` | Move XY only (Z unchanged) |
| `cmd.move_nozzle_z` | `{z, feedrate?}` | Move Z only (XY unchanged) |
| `cmd.home_nozzle` | `{}` | Run G28 homing sequence |
| `cmd.emergency_stop` | `{}` | Send M112; enter emergency_stop state |
| `cmd.clear_emergency_stop` | `{}` | Send M999; return to idle |

### Server → Client (telemetry)

| Event | Payload | Description |
|-------|---------|-------------|
| `status` | `{mode, connected}` | Sent once on connection |
| `telemetry.position` | `{timestamp, nozzle: {x,y,z}, status}` | Broadcast every 500 ms |
| `telemetry.command_ack` | `{id, status, message, timestamp}` | Sent to requesting client after each command |

`status` values: `"idle"`, `"moving"`, `"homing"`, `"emergency_stop"`, `"error"`.
`command_ack.status` values: `"ok"`, `"err"`.

---

## Config File Reference

Both `config_connected.yml` and `config_test.yml` share this schema:

```yaml
printer:
  serial_device: "/dev/ttyUSB0"   # Serial port path
  baud_rate: 115200                # Must match printer firmware
  swap_yz_axes: false              # true if Y/Z motors are physically swapped
  safe_limits:
    x_min: 0.0    # mm
    x_max: 220.0
    y_min: 0.0
    y_max: 220.0
    z_min: 0.0
    z_max: 250.0
  move_feedrate_default: 1500     # mm/min

camera:
  sharpness: 2.0    # 0.0–16.0; adjust lens focus ring physically
  jpeg_quality: 85  # 1–100

stream:
  preview_width: 1920   # px
  preview_height: 1080  # px
  preview_fps: 15       # frames/s

emergency_stop:
  gpio_pin: 27    # BCM pin number

# Test mode only:
simulation:
  movement_delay: 0.1   # seconds between interpolation steps
```

---

## Protocols

The protocol system allows for complex, automated sequences of movements and pipette actions. Protocols are written as Python scripts in the `protocols/` directory.

### Writing a Protocol

A protocol is a Python file that defines an `async def run(p: Protocol)` function. It may also define metadata constants:

```python
NAME = "My Protocol"
DESCRIPTION = "Moves the nozzle in a square and dispenses."

async def run(p):
    await p.log("Starting protocol...")
    await p.home()
    
    # 3D Movement
    await p.move(x=100, y=100, z=20)  # Simultaneous XYZ
    await p.move_z(5)                # Z-only move
    
    # Pipette Control
    await p.pipette_home()           # Reset pipette to 0 (bottom)
    await p.set_stroke(1000)         # Set stroke length to 1000 steps
    await p.aspirate()               # Pull up to stroke limit
    await p.dispense()               # Push back down to 0
    
    await p.sleep(1.0)               # Pause for 1 second
```

### Protocol API (`p` object)

| Method | Description |
|--------|-------------|
| `p.home()` | Runs the G28 homing sequence for the nozzle. |
| `p.move(x, y, z?, f?)` | Moves to absolute coordinates. `z` and `f` (feedrate) are optional. |
| `p.move_z(z, f?)` | Moves the Z axis only. |
| `p.pos()` | Returns the current `Position` (x, y, z). |
| `p.log(msg)` | Logs a message to the console and the Web UI protocol log. |
| `p.sleep(secs)` | Pauses execution for the specified duration. |
| `p.pipette_home()` | Resets the pipette stepper position to 0. |
| `p.set_stroke(steps)` | Sets the upper limit for aspiration. |
| `p.aspirate()` | Moves the pipette to the upper stroke limit. |
| `p.dispense()` | Moves the pipette back to the home (0) position. |

### Accessing Safety Limits

Scripts can access the safe limits defined in the configuration:
- `p.x_min`, `p.x_max`
- `p.y_min`, `p.y_max`
- `p.z_min`, `p.z_max`

### Running Protocols

**Via Web UI:**
Select a protocol from the dropdown in the "Protocols" section and click "Run Protocol". Progress and logs will appear in the black console box below.

**Via CLI:**
```bash
python protocol.py protocols/my_script.py --mode test
python protocol.py protocols/my_script.py --mode connected
```

---

## Deployment

### systemd

`setup_pi.sh` generates and installs a systemd service unit. Key fields:

```ini
[Service]
Environment=SECRET_KEY=<your_secret>
Environment=MODE=connected
ExecStart=/path/to/venv/bin/python /path/to/server.py
Restart=on-failure
```

### pigpiod

Must run as root before the server starts:

```bash
sudo systemctl enable --now pigpiod
```

### Tailscale (optional remote access)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
# Access the Pi by its Tailscale IP; no port-forwarding needed.
```
