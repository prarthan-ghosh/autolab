"""
Protocol DSL for pi-autolab.

A Protocol script is a Python module that defines:

    async def run(p: Protocol): ...

and optionally:

    NAME = "Human-readable name"
    DESCRIPTION = "What this does."

The Protocol object wraps the printer + pipette with a compact API:

    await p.home()                       # home nozzle (G28)
    await p.move(x, y, z, feedrate=...)  # absolute XYZ move
    await p.move_z(z)                    # Z only
    await p.pos()                        # -> Position
    await p.sleep(seconds)
    await p.log("message")

    # Pipette
    await p.pipette_home()               # zero the pipette at lower stop
    await p.set_stroke(steps)            # upper limit = -steps
    await p.aspirate()
    await p.dispense()

Run a protocol standalone:

    python protocol.py protocols/example.py --mode connected
    python protocol.py protocols/example.py --mode test

Or import and call `run_protocol(path, mode, config, on_event=...)` from the
server to execute one in an already-running event loop.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

import yaml

from hw.abstract_hardware import CommandStatus, Position
from hw.hardware_factory import create_hardware

logger = logging.getLogger(__name__)

PROTOCOLS_DIR = Path(__file__).parent / "protocols"

EventHandler = Callable[[dict], Awaitable[None]]


class ProtocolError(RuntimeError):
    """Raised when a step in a protocol fails."""


@dataclass
class ProtocolInfo:
    """Metadata discovered about a protocol module."""
    path: str
    name: str
    description: str


class Protocol:
    """Thin script-friendly wrapper around hardware + pipette."""

    def __init__(self, hardware, on_event: Optional[EventHandler] = None):
        self.hw = hardware
        self.pipette = hardware.pipette
        self._on_event = on_event
        self._default_feedrate = hardware.config['printer']['move_feedrate_default']
        self._limits = hardware.config['printer']['safe_limits']

    @property
    def x_min(self) -> float: return float(self._limits['x_min'])
    @property
    def x_max(self) -> float: return float(self._limits['x_max'])
    @property
    def y_min(self) -> float: return float(self._limits['y_min'])
    @property
    def y_max(self) -> float: return float(self._limits['y_max'])
    @property
    def z_min(self) -> float: return float(self._limits['z_min'])
    @property
    def z_max(self) -> float: return float(self._limits['z_max'])

    # ------------------------------------------------------------------
    # Events (used by the server to stream progress to the UI)

    async def _emit(self, kind: str, **data) -> None:
        if self._on_event is None:
            return
        await self._on_event({'kind': kind, 'timestamp': time.time(), **data})

    async def log(self, message: str) -> None:
        logger.info("[protocol] %s", message)
        await self._emit('log', message=message)

    # ------------------------------------------------------------------
    # Printer

    async def home(self) -> None:
        await self._emit('step', action='home')
        ack = await self.hw.home_nozzle()
        self._check(ack, "home_nozzle")

    async def move(self, x: float, y: float, z: Optional[float] = None,
                   feedrate: Optional[int] = None) -> None:
        f = feedrate if feedrate is not None else self._default_feedrate
        if z is None:
            await self._emit('step', action='move_xy', x=x, y=y)
            ack = await self.hw.move_nozzle_xy(float(x), float(y), int(f))
        else:
            await self._emit('step', action='move', x=x, y=y, z=z)
            ack = await self.hw.move_nozzle(float(x), float(y), float(z), int(f))
        self._check(ack, f"move({x},{y},{z})")

    async def move_z(self, z: float, feedrate: Optional[int] = None) -> None:
        f = feedrate if feedrate is not None else self._default_feedrate
        await self._emit('step', action='move_z', z=z)
        ack = await self.hw.move_nozzle_z(float(z), int(f))
        self._check(ack, f"move_z({z})")

    async def pos(self) -> Position:
        return await self.hw.get_nozzle_position()

    async def sleep(self, seconds: float) -> None:
        await self._emit('step', action='sleep', seconds=seconds)
        await asyncio.sleep(seconds)

    # ------------------------------------------------------------------
    # Pipette

    async def pipette_home(self) -> None:
        await self._emit('step', action='pipette_home')
        await self.pipette.home()

    async def set_stroke(self, steps: int) -> None:
        await self._emit('step', action='set_stroke', steps=steps)
        await self.pipette.set_limit(int(steps))

    async def aspirate(self) -> None:
        await self._emit('step', action='aspirate')
        await self.pipette.aspirate()

    async def dispense(self) -> None:
        await self._emit('step', action='dispense')
        await self.pipette.dispense()

    # ------------------------------------------------------------------

    def _check(self, ack, context: str) -> None:
        if ack.status != CommandStatus.OK:
            raise ProtocolError(f"{context} failed: {ack.message}")


# ---------------------------------------------------------------------------
# Loading + discovery
# ---------------------------------------------------------------------------

def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"protocol_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ProtocolError(f"cannot load protocol: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "run") or not asyncio.iscoroutinefunction(module.run):
        raise ProtocolError(f"{path}: must define `async def run(p)`")
    return module


def list_protocols(directory: Path = PROTOCOLS_DIR) -> List[ProtocolInfo]:
    """Enumerate available protocol scripts (*.py, excluding __init__.py)."""
    if not directory.exists():
        return []
    out: List[ProtocolInfo] = []
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_") or path.name.startswith("."):
            continue
        try:
            mod = _load_module(path)
            out.append(ProtocolInfo(
                path=str(path.relative_to(Path(__file__).parent)),
                name=getattr(mod, "NAME", path.stem),
                description=getattr(mod, "DESCRIPTION", ""),
            ))
        except Exception as e:
            logger.warning("skipping protocol %s: %s", path, e)
    return out


async def run_protocol(script_path: str, hardware,
                       on_event: Optional[EventHandler] = None) -> None:
    """Load and execute a protocol script against a live hardware instance."""
    path = Path(script_path)
    if not path.is_absolute():
        path = Path(__file__).parent / path
    module = _load_module(path)
    p = Protocol(hardware, on_event=on_event)
    await p.log(f"running {path.name}")
    try:
        await module.run(p)
        await p.log(f"finished {path.name}")
    except Exception as e:
        await p.log(f"ERROR in {path.name}: {e}")
        raise


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

async def _standalone(script_path: str, mode: str, config_path: Optional[str]) -> int:
    config_file = config_path or f"config_{mode}.yml"
    with open(config_file) as f:
        config = yaml.safe_load(f)

    hardware = create_hardware(mode, config)
    if not await hardware.initialize():
        print("hardware init failed", file=sys.stderr)
        return 1

    async def stdout_events(event: dict) -> None:
        k = event.pop('kind')
        event.pop('timestamp', None)
        detail = " ".join(f"{k}={v}" for k, v in event.items())
        print(f"[{k}] {detail}" if detail else f"[{k}]")

    try:
        await run_protocol(script_path, hardware, on_event=stdout_events)
    except ProtocolError as e:
        print(f"protocol failed: {e}", file=sys.stderr)
        return 2
    finally:
        await hardware.shutdown()
    return 0


def main():
    parser = argparse.ArgumentParser(description="Run a pi-autolab protocol script.")
    parser.add_argument("script", help="Path to protocol .py file")
    parser.add_argument("--mode", choices=["test", "connected"],
                        default=os.getenv("MODE", "test"))
    parser.add_argument("--config", default=None,
                        help="Path to config YAML (default: config_<mode>.yml)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    sys.exit(asyncio.run(_standalone(args.script, args.mode, args.config)))


if __name__ == "__main__":
    main()
