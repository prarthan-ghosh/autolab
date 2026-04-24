"""
Pipette hardware driver.

Talks to the pipette Arduino (firmware/pipette/pipette.ino) over serial.
Protocol is line-based: every command returns exactly one 'OK' or 'ERR <msg>'
terminator, optionally preceded by '# ' info lines.
"""

import asyncio
import time
from typing import List, Optional

import serial


class PipetteError(RuntimeError):
    """Raised when the pipette reports ERR or times out."""


class Pipette:
    """
    Async wrapper around the pipette serial CLI.

    All command methods return the list of '# ...' info lines collected before
    the OK terminator. An ERR reply raises PipetteError.
    """

    def __init__(self, serial_device: str, baud_rate: int = 115200,
                 default_timeout: float = 10.0):
        self.serial_device = serial_device
        self.baud_rate = baud_rate
        self.default_timeout = default_timeout
        self._ser: Optional[serial.Serial] = None
        self._lock = asyncio.Lock()
        # Last-known values cached from firmware commands.
        self.upper_limit: Optional[int] = None  # negative step count, None if unset
        self.lower_limit: int = 0

    # ------------------------------------------------------------------

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()
        self._ser = await loop.run_in_executor(
            None,
            lambda: serial.Serial(port=self.serial_device, baudrate=self.baud_rate, timeout=0.5),
        )
        # DTR reset: Arduino reboots on open. Wait, then consume boot banner.
        await asyncio.sleep(2.0)
        await loop.run_in_executor(None, self._ser.reset_input_buffer)

    async def close(self) -> None:
        if self._ser and self._ser.is_open:
            await asyncio.get_event_loop().run_in_executor(None, self._ser.close)

    # ------------------------------------------------------------------

    async def _send(self, command: str, timeout: Optional[float] = None) -> List[str]:
        if not self._ser or not self._ser.is_open:
            raise PipetteError("pipette not connected")

        timeout = timeout if timeout is not None else self.default_timeout
        loop = asyncio.get_event_loop()

        async with self._lock:
            await loop.run_in_executor(None, self._ser.reset_input_buffer)
            await loop.run_in_executor(
                None, lambda: self._ser.write((command + "\n").encode("utf-8"))
            )

            info_lines: List[str] = []
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                line = await loop.run_in_executor(
                    None, lambda: self._ser.readline().decode("utf-8", errors="replace").strip()
                )
                if not line:
                    continue
                if line == "OK":
                    return info_lines
                if line.startswith("ERR"):
                    msg = line[3:].strip() or "unknown error"
                    raise PipetteError(f"{command!r}: {msg}")
                if line.startswith("# "):
                    info_lines.append(line[2:])
                else:
                    # Unexpected line — capture but keep reading.
                    info_lines.append(line)

            raise PipetteError(f"{command!r}: timeout after {timeout}s")

    # ------------------------------------------------------------------
    # High-level API

    async def home(self) -> None:
        """Zero the current position (call at mechanical lower limit)."""
        await self._send("HOME")

    async def set_limit(self, steps: int) -> None:
        """Set upper travel limit to `steps` above 0. `steps` > 0."""
        await self._send(f"LIMIT {int(steps)}")
        self.upper_limit = -int(steps)

    async def aspirate(self, timeout: float = 30.0) -> None:
        """One full up-stroke to the upper limit. Blocks until done."""
        await self._send("ASPIRATE", timeout=timeout)

    async def dispense(self, timeout: float = 30.0) -> None:
        """One full down-stroke back to 0. Blocks until done."""
        await self._send("DISPENSE", timeout=timeout)

    async def move(self, coord: int, timeout: float = 30.0) -> None:
        """Absolute move to `coord` steps (negative = up)."""
        await self._send(f"MOVE {int(coord)}", timeout=timeout)

    async def jog(self, delta: int, timeout: float = 30.0) -> None:
        """Relative move by `delta` steps (negative = up). Limit-enforced."""
        await self._send(f"JOG {int(delta)}", timeout=timeout)

    async def free(self, delta: int, timeout: float = 30.0) -> None:
        """Unchecked relative move — bypasses all bounds. Calibration only."""
        await self._send(f"FREE {int(delta)}", timeout=timeout)

    async def position(self) -> int:
        """Current position in steps."""
        lines = await self._send("POS")
        for line in lines:
            if line.startswith("POS "):
                return int(line.split()[1])
        raise PipetteError("POS response missing coordinate")

    async def set_speed(self, steps_per_sec: float) -> None:
        await self._send(f"SPEED {steps_per_sec}")

    async def set_acceleration(self, steps_per_sec2: float) -> None:
        await self._send(f"ACCEL {steps_per_sec2}")

    async def stop(self) -> None:
        await self._send("STOP", timeout=2.0)


class NullPipette:
    """No-op pipette for test mode. Matches Pipette's async API."""

    def __init__(self):
        self.upper_limit: Optional[int] = None
        self.lower_limit: int = 0
        self._pos: int = 0

    async def connect(self) -> None: pass
    async def close(self) -> None: pass
    async def home(self) -> None: self._pos = 0
    async def set_limit(self, steps: int) -> None:
        self.upper_limit = -int(steps)
    async def aspirate(self, timeout: float = 30.0) -> None:
        await asyncio.sleep(0.3)
        if self.upper_limit is not None:
            self._pos = self.upper_limit
    async def dispense(self, timeout: float = 30.0) -> None:
        await asyncio.sleep(0.3)
        self._pos = 0
    async def move(self, coord: int, timeout: float = 30.0) -> None:
        await asyncio.sleep(0.1)
        self._pos = int(coord)
    async def jog(self, delta: int, timeout: float = 30.0) -> None:
        await asyncio.sleep(0.1)
        self._pos += int(delta)
    async def free(self, delta: int, timeout: float = 30.0) -> None:
        await asyncio.sleep(0.1)
        self._pos += int(delta)
    async def position(self) -> int: return self._pos
    async def set_speed(self, steps_per_sec: float) -> None: pass
    async def set_acceleration(self, steps_per_sec2: float) -> None: pass
    async def stop(self) -> None: pass
