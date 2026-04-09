"""
Test hardware implementation for simulation mode.

Simulates realistic hardware behavior with timing, limits, and state management.
Does NOT send actual G-code commands — only simulates behavior.
"""

import asyncio
import time
from typing import Dict, Any
from .abstract_hardware import (
    HardwareInterface, Position, CommandAck,
    CommandStatus, TelemetryData, _STATE_TO_STATUS,
)


class TestHardware(HardwareInterface):
    """Test hardware implementation with realistic simulation (no actual G-code sent)."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.nozzle_pos = Position(0.0, 0.0, 0.0)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> bool:
        """Initialize test hardware simulation."""
        print("Initializing test hardware simulation (no G-code will be sent)...", flush=True)
        return True

    async def shutdown(self) -> bool:
        """Shutdown test hardware simulation."""
        print("Shutting down test hardware simulation...")
        return True

    # ------------------------------------------------------------------
    # Nozzle control
    # ------------------------------------------------------------------

    async def move_nozzle(self, x: float, y: float, z: float, feedrate: int) -> CommandAck:
        """Simulate nozzle movement (no G-code sent)."""
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

        # Simulate realistic movement time
        distance = ((x - self.nozzle_pos.x) ** 2 +
                    (y - self.nozzle_pos.y) ** 2 +
                    (z - self.nozzle_pos.z) ** 2) ** 0.5
        move_time = (distance / feedrate) * 60  # mm/min → seconds
        movement_delay = self.config['simulation']['movement_delay']
        steps = max(1, int(move_time * 10))  # ~10 position updates per second

        start = Position(self.nozzle_pos.x, self.nozzle_pos.y, self.nozzle_pos.z)
        for i in range(steps):
            if self.state == 'emergency_stop':
                # E-stop was triggered externally during movement
                return CommandAck(
                    id=f"move_nozzle_{int(time.time() * 1000)}",
                    status=CommandStatus.ERROR,
                    message="Emergency stop during movement",
                    timestamp=time.time(),
                )

            progress = (i + 1) / steps
            self.nozzle_pos.x = start.x + (x - start.x) * progress
            self.nozzle_pos.y = start.y + (y - start.y) * progress
            self.nozzle_pos.z = start.z + (z - start.z) * progress
            await asyncio.sleep(movement_delay)

        await self.complete_move()

        print(
            f"[TEST MODE] Simulated nozzle movement to "
            f"({x:.2f}, {y:.2f}, {z:.2f}) — no G-code sent"
        )

        return CommandAck(
            id=f"move_nozzle_{int(time.time() * 1000)}",
            status=CommandStatus.OK,
            message="Movement completed (simulated)",
            timestamp=time.time(),
        )

    async def move_nozzle_xy(self, x: float, y: float, feedrate: int) -> CommandAck:
        """Move printer nozzle XY only (Z unchanged)."""
        return await self.move_nozzle(x, y, self.nozzle_pos.z, feedrate)

    async def move_nozzle_z(self, z: float, feedrate: int) -> CommandAck:
        """Move printer nozzle Z only (XY unchanged)."""
        return await self.move_nozzle(self.nozzle_pos.x, self.nozzle_pos.y, z, feedrate)

    async def get_nozzle_position(self) -> Position:
        """Return current simulated nozzle position."""
        return Position(self.nozzle_pos.x, self.nozzle_pos.y, self.nozzle_pos.z)

    async def home_nozzle(self) -> CommandAck:
        """Simulate homing to origin (0, 0, 0)."""
        if self.state == 'emergency_stop':
            return CommandAck(
                id=f"home_nozzle_{int(time.time() * 1000)}",
                status=CommandStatus.ERROR,
                message="Emergency stop active",
                timestamp=time.time(),
            )

        await self.begin_homing()

        distance = (self.nozzle_pos.x ** 2 +
                    self.nozzle_pos.y ** 2 +
                    self.nozzle_pos.z ** 2) ** 0.5

        if distance < 0.01:
            self.nozzle_pos = Position(0.0, 0.0, 0.0)
            print("[TEST MODE] Nozzle already at origin — homing skipped", flush=True)
        else:
            default_feedrate = self.config['printer']['move_feedrate_default']
            movement_delay = self.config['simulation']['movement_delay']
            move_time = (distance / default_feedrate) * 60
            steps = max(1, int(move_time * 10))

            start = Position(self.nozzle_pos.x, self.nozzle_pos.y, self.nozzle_pos.z)
            for i in range(steps):
                progress = (i + 1) / steps
                self.nozzle_pos.x = start.x * (1 - progress)
                self.nozzle_pos.y = start.y * (1 - progress)
                self.nozzle_pos.z = start.z * (1 - progress)
                await asyncio.sleep(movement_delay)

            self.nozzle_pos = Position(0.0, 0.0, 0.0)
            print(
                f"[TEST MODE] Simulated homing from distance {distance:.2f}mm — no G-code sent"
            )

        await self.complete_homing()

        return CommandAck(
            id=f"home_nozzle_{int(time.time() * 1000)}",
            status=CommandStatus.OK,
            message="Homing completed (simulated)",
            timestamp=time.time(),
        )

    # ------------------------------------------------------------------
    # Emergency stop
    # ------------------------------------------------------------------

    async def emergency_stop(self) -> CommandAck:
        """Simulate emergency stop."""
        try:
            await self.trigger_estop()
        except Exception:
            # Already in emergency_stop state; ignore.
            pass

        print("EMERGENCY STOP ACTIVATED [TEST MODE]")

        return CommandAck(
            id=f"emergency_stop_{int(time.time() * 1000)}",
            status=CommandStatus.OK,
            message="Emergency stop activated",
            timestamp=time.time(),
        )

    async def clear_emergency_stop(self) -> CommandAck:
        """Clear simulated emergency stop."""
        await self.clear_estop()

        print("Emergency stop cleared [TEST MODE]")

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
        """Get current simulated system telemetry."""
        return TelemetryData(
            timestamp=time.time(),
            nozzle=Position(self.nozzle_pos.x, self.nozzle_pos.y, self.nozzle_pos.z),
            status=_STATE_TO_STATUS[self.state],
        )

    # ------------------------------------------------------------------
    # Safety and limits
    # ------------------------------------------------------------------

    def check_nozzle_limits(self, x: float, y: float, z: float) -> bool:
        """Check if nozzle position is within safe limits."""
        limits = self.config['printer']['safe_limits']
        return (limits['x_min'] <= x <= limits['x_max'] and
                limits['y_min'] <= y <= limits['y_max'] and
                limits['z_min'] <= z <= limits['z_max'])
