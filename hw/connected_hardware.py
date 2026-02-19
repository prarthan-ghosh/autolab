"""
Connected hardware implementation for real hardware control.

Controls actual hardware via pigpio, pyserial, and picamera2.
"""

import asyncio
import re
import time
import serial
import pigpio
from typing import Dict, Any, Optional
from .abstract_hardware import (
    HardwareInterface, Position, CommandAck,
    CommandStatus, SystemStatus, TelemetryData, _STATE_TO_STATUS,
)


class ConnectedHardware(HardwareInterface):
    """Connected hardware implementation for real hardware control."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.pi: Optional[pigpio.pi] = None
        self.printer_serial: Optional[serial.Serial] = None
        self.nozzle_pos = Position(0.0, 0.0, 0.0)
        self._serial_lock = asyncio.Lock()
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> bool:
        """Initialize connected hardware following Anycubic Kobra 2 Neo pattern."""
        self._event_loop = asyncio.get_event_loop()

        # Initialize pigpio
        self.pi = pigpio.pi()
        if not self.pi.connected:
            raise RuntimeError("Failed to connect to pigpio daemon")

        # Initialize serial connection to printer
        serial_port = self.config['printer']['serial_device']
        baud_rate = self.config['printer']['baud_rate']

        print(f"Connecting to {serial_port} at {baud_rate}...")

        def _open_serial():
            return serial.Serial(port=serial_port, baudrate=baud_rate, timeout=1)

        self.printer_serial = await asyncio.get_event_loop().run_in_executor(None, _open_serial)

        # DTR reset: printer reboots when port opens; wait for it to be ready.
        print("Waiting for printer to initialize after connection (3 seconds)...")
        await asyncio.sleep(3)

        await asyncio.get_event_loop().run_in_executor(
            None, self.printer_serial.reset_input_buffer
        )
        print("Printer connected and ready.\n")

        # Set safe modes
        print("Setting safe modes (G21: millimeters, G90: absolute positioning)...")
        ack1 = await self._send_gcode("G21")
        ack2 = await self._send_gcode("G90")
        if ack1.status != CommandStatus.OK or ack2.status != CommandStatus.OK:
            print(f"WARNING: Failed to set safe modes. G21: {ack1.status}, G90: {ack2.status}")

        # Register GPIO emergency stop callback
        self._setup_gpio_pins()

        # Home nozzle on startup
        print("\nHoming nozzle to origin (0, 0, 0)...")
        print("(This may take 30-60 seconds - please wait...)")
        ack = await self.home_nozzle()
        if ack.status != CommandStatus.OK:
            print(f"WARNING: Homing failed during initialization: {ack.message}")
        else:
            print("Nozzle homed successfully")

        return True

    async def shutdown(self) -> bool:
        """Shutdown connected hardware."""
        if self.printer_serial and self.printer_serial.is_open:
            await asyncio.get_event_loop().run_in_executor(None, self.printer_serial.close)
        if self.pi:
            self.pi.stop()
        return True

    def _setup_gpio_pins(self):
        """Configure GPIO pins and register emergency stop callback."""
        pin = self.config['emergency_stop']['gpio_pin']
        self.pi.set_mode(pin, pigpio.INPUT)
        self.pi.set_pull_up_down(pin, pigpio.PUD_UP)
        self.pi.callback(pin, pigpio.FALLING_EDGE, self._gpio_estop_callback)

    def _gpio_estop_callback(self, gpio, level, tick):
        """Called from pigpio thread on GPIO falling edge; schedules emergency_stop coroutine."""
        asyncio.run_coroutine_threadsafe(self.emergency_stop(), self._event_loop)

    # ------------------------------------------------------------------
    # Serial communication
    # ------------------------------------------------------------------

    async def _send_gcode(self, command: str, timeout: float = 5.0) -> CommandAck:
        """
        Send a G-code command and wait for 'ok' response, serialized by a lock.

        All blocking serial operations run in a thread executor.
        """
        async with self._serial_lock:
            if not self.printer_serial or not self.printer_serial.is_open:
                return CommandAck(
                    id=f"gcode_{int(time.time() * 1000)}",
                    status=CommandStatus.ERROR,
                    message="Printer serial port not open",
                    timestamp=time.time(),
                )

            command_id = f"gcode_{int(time.time() * 1000)}"
            start_time = time.time()
            full_command = f"{command}\n"

            def _write():
                self.printer_serial.write(full_command.encode('utf-8'))

            await asyncio.get_event_loop().run_in_executor(None, _write)

            response_lines = []
            while (time.time() - start_time) < timeout:
                try:
                    def _readline():
                        return self.printer_serial.readline().decode('utf-8').strip()

                    line = await asyncio.get_event_loop().run_in_executor(None, _readline)

                    if line:
                        if line.lower().startswith('ok'):
                            # Include any lines captured before 'ok' (e.g. M114 position data)
                            message = ' '.join(response_lines) if response_lines else f"Command '{command}' completed"
                            return CommandAck(
                                id=command_id,
                                status=CommandStatus.OK,
                                message=message,
                                timestamp=time.time(),
                            )
                        if 'error' in line.lower() or 'resend' in line.lower():
                            return CommandAck(
                                id=command_id,
                                status=CommandStatus.ERROR,
                                message=f"Printer error: {line}",
                                timestamp=time.time(),
                            )
                        response_lines.append(line)
                except Exception:
                    await asyncio.sleep(0.1)

            return CommandAck(
                id=command_id,
                status=CommandStatus.ERROR,
                message=f"Timeout waiting for 'ok' to '{command}'",
                timestamp=time.time(),
            )

    async def _query_position(self) -> Optional[Position]:
        """
        Query actual position from printer using M114.

        Returns Position on success, None on parse failure.
        """
        ack = await self._send_gcode("M114", timeout=5.0)
        if ack.status != CommandStatus.OK:
            return None
        # M114 response: "X:10.00 Y:20.00 Z:5.00 E:0.00 Count X:0 Y:0 Z:0"
        # or embedded in the "ok" line itself on some firmware versions.
        # We parse the 'ok' message text for X/Y/Z values.
        m = re.search(r'X:([\d.]+)\s+Y:([\d.]+)\s+Z:([\d.]+)', ack.message)
        if m:
            return Position(float(m.group(1)), float(m.group(2)), float(m.group(3)))
        return None

    # ------------------------------------------------------------------
    # Nozzle control
    # ------------------------------------------------------------------

    async def move_nozzle(self, x: float, y: float, z: float, feedrate: int) -> CommandAck:
        """Move printer nozzle to specified position using G1 → M400 → M114."""
        if not self.check_nozzle_limits(x, y, z):
            return CommandAck(
                id=f"move_nozzle_{int(time.time() * 1000)}",
                status=CommandStatus.ERROR,
                message="Position outside safe limits",
                timestamp=time.time(),
            )

        if self.state == 'emergency_stop':
            return CommandAck(
                id=f"move_nozzle_{int(time.time() * 1000)}",
                status=CommandStatus.ERROR,
                message="Emergency stop active",
                timestamp=time.time(),
            )

        await self.begin_move()
        try:
            swap_yz = self.config['printer']['swap_yz_axes']
            if swap_yz:
                gcode = f"G1 X{x:.3f} Y{z:.3f} Z{y:.3f} F{feedrate}"
            else:
                gcode = f"G1 X{x:.3f} Y{y:.3f} Z{z:.3f} F{feedrate}"

            ack = await self._send_gcode(gcode, timeout=30.0)
            if ack.status != CommandStatus.OK:
                await self.fail_move()
                return ack

            # Wait for all buffered moves to finish
            ack_m400 = await self._send_gcode("M400", timeout=60.0)
            if ack_m400.status != CommandStatus.OK:
                await self.fail_move()
                return ack_m400

            # Read back actual position
            pos = await self._query_position()
            if pos is not None:
                self.nozzle_pos = pos
            else:
                # Fallback: trust commanded position
                self.nozzle_pos = Position(x, y, z)

            await self.complete_move()
            ack.message = f"Move to ({x:.3f}, {y:.3f}, {z:.3f}) completed"
            return ack

        except Exception as e:
            self.logger.error(f"move_nozzle failed: {e}", exc_info=True)
            await self.fail_move()
            return CommandAck(
                id=f"move_nozzle_{int(time.time() * 1000)}",
                status=CommandStatus.ERROR,
                message=str(e),
                timestamp=time.time(),
            )

    async def move_nozzle_xy(self, x: float, y: float, feedrate: int) -> CommandAck:
        """Move printer nozzle XY only (Z unchanged)."""
        return await self.move_nozzle(x, y, self.nozzle_pos.z, feedrate)

    async def move_nozzle_z(self, z: float, feedrate: int) -> CommandAck:
        """Move printer nozzle Z only (XY unchanged)."""
        return await self.move_nozzle(self.nozzle_pos.x, self.nozzle_pos.y, z, feedrate)

    async def get_nozzle_position(self) -> Position:
        """Return last known nozzle position."""
        return Position(self.nozzle_pos.x, self.nozzle_pos.y, self.nozzle_pos.z)

    async def home_nozzle(self) -> CommandAck:
        """Home the nozzle to origin (0, 0, 0) using G28."""
        if self.state == 'emergency_stop':
            return CommandAck(
                id=f"home_nozzle_{int(time.time() * 1000)}",
                status=CommandStatus.ERROR,
                message="Emergency stop active",
                timestamp=time.time(),
            )

        await self.begin_homing()
        try:
            ack = await self._send_gcode("G28", timeout=120.0)
            if ack.status == CommandStatus.OK:
                self.nozzle_pos = Position(0.0, 0.0, 0.0)
                await self.complete_homing()
                ack.message = "Homing completed"
            else:
                await self.fail_homing()
            return ack
        except Exception as e:
            self.logger.error(f"home_nozzle failed: {e}", exc_info=True)
            await self.fail_homing()
            return CommandAck(
                id=f"home_nozzle_{int(time.time() * 1000)}",
                status=CommandStatus.ERROR,
                message=str(e),
                timestamp=time.time(),
            )

    # ------------------------------------------------------------------
    # Emergency stop
    # ------------------------------------------------------------------

    async def emergency_stop(self) -> CommandAck:
        """Emergency stop all movement using M112."""
        try:
            await self.trigger_estop()
        except Exception:
            # Already in emergency_stop state (e.g. called twice); ignore.
            pass

        if self.printer_serial and self.printer_serial.is_open:
            def _write_estop():
                self.printer_serial.write(b"M112\n")
            await asyncio.get_event_loop().run_in_executor(None, _write_estop)

        return CommandAck(
            id=f"emergency_stop_{int(time.time() * 1000)}",
            status=CommandStatus.OK,
            message="Emergency stop activated",
            timestamp=time.time(),
        )

    async def clear_emergency_stop(self) -> CommandAck:
        """
        Clear emergency stop: send M999 to restart firmware, then clear FSM state.
        """
        if self.printer_serial and self.printer_serial.is_open:
            def _write_m999():
                self.printer_serial.write(b"M999\n")
            await asyncio.get_event_loop().run_in_executor(None, _write_m999)
            # Wait for firmware restart
            await asyncio.sleep(2)
            await asyncio.get_event_loop().run_in_executor(
                None, self.printer_serial.reset_input_buffer
            )

        await self.clear_estop()

        return CommandAck(
            id=f"clear_emergency_stop_{int(time.time() * 1000)}",
            status=CommandStatus.OK,
            message="Emergency stop cleared",
            timestamp=time.time(),
        )

    # ------------------------------------------------------------------
    # Telemetry and status
    # ------------------------------------------------------------------

    async def get_telemetry(self) -> TelemetryData:
        """Get current system telemetry."""
        return TelemetryData(
            timestamp=time.time(),
            nozzle=Position(self.nozzle_pos.x, self.nozzle_pos.y, self.nozzle_pos.z),
            status=_STATE_TO_STATUS[self.state],
        )

    async def is_ready(self) -> bool:
        """Check if hardware is ready for commands."""
        if self.state not in ('idle',):
            return False
        if not (self.pi and self.pi.connected and self.printer_serial and self.printer_serial.is_open):
            return False
        return True

    # ------------------------------------------------------------------
    # Safety and limits
    # ------------------------------------------------------------------

    def check_nozzle_limits(self, x: float, y: float, z: float) -> bool:
        """Check if nozzle position is within safe limits."""
        limits = self.config['printer']['safe_limits']
        return (limits['x_min'] <= x <= limits['x_max'] and
                limits['y_min'] <= y <= limits['y_max'] and
                limits['z_min'] <= z <= limits['z_max'])

    # ------------------------------------------------------------------
    # Diagnostic helpers (not part of abstract interface)
    # ------------------------------------------------------------------

    async def get_temperature(self) -> Optional[Dict[str, Any]]:
        """Query printer temperature using M105."""
        if not self.printer_serial or not self.printer_serial.is_open:
            return None

        ack = await self._send_gcode("M105", timeout=2.0)
        if ack.status != CommandStatus.OK:
            return None

        temp_match = re.search(r'T:([\d.]+)', ack.message)
        bed_match = re.search(r'B:([\d.]+)', ack.message)
        if temp_match and bed_match:
            return {
                'nozzle_temp': float(temp_match.group(1)),
                'bed_temp': float(bed_match.group(1)),
            }
        return None

    async def get_firmware_info(self) -> Optional[str]:
        """Query printer firmware information using M115."""
        if not self.printer_serial or not self.printer_serial.is_open:
            return None

        ack = await self._send_gcode("M115", timeout=3.0)
        return ack.message if ack.status == CommandStatus.OK else None
