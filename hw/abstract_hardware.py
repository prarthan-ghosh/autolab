"""
Abstract hardware interface for printer control system.

Defines the common interface that both test and connected hardware implementations
must follow. This ensures the web UI works identically regardless of mode.

Uses a formal FSM (via the `transitions` library) to manage system state, replacing
scattered boolean flags and ad-hoc enum assignments.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any, Optional
from enum import Enum
import time
import logging

from transitions.extensions.asyncio import AsyncMachine


class CommandStatus(Enum):
    """Status of a hardware command."""
    PENDING = "pending"
    OK = "ok"
    ERROR = "err"


class SystemStatus(Enum):
    """Overall system status (used for wire serialization)."""
    IDLE = "idle"
    MOVING = "moving"
    ERROR = "error"
    HOMING = "homing"
    EMERGENCY_STOP = "emergency_stop"


@dataclass
class Position:
    """3D position coordinates."""
    x: float
    y: float
    z: float


@dataclass
class CommandAck:
    """Command acknowledgment."""
    id: str
    status: CommandStatus
    message: str
    timestamp: float
    stack_trace: Optional[str] = None  # For error diagnostics


@dataclass
class TelemetryData:
    """Telemetry data structure."""
    timestamp: float
    nozzle: Position
    status: SystemStatus
    error_message: Optional[str] = None


# Maps FSM state strings to wire-serializable SystemStatus values
_STATE_TO_STATUS: Dict[str, SystemStatus] = {
    'idle':            SystemStatus.IDLE,
    'moving':          SystemStatus.MOVING,
    'homing':          SystemStatus.HOMING,
    'emergency_stop':  SystemStatus.EMERGENCY_STOP,
    'error':           SystemStatus.ERROR,
}


def validate_coordinates(x, y, z, feedrate=None):
    """
    Validate coordinate types. Raises ValueError on non-numeric inputs.

    Args:
        x, y, z: Axis coordinates (must be int or float)
        feedrate: Optional feedrate (must be int or float if provided)
    """
    for name, val in [('x', x), ('y', y), ('z', z)]:
        if not isinstance(val, (int, float)):
            raise ValueError(f"'{name}' must be numeric, got {type(val).__name__}")
    if feedrate is not None and not isinstance(feedrate, (int, float)):
        raise ValueError(f"'feedrate' must be numeric, got {type(feedrate).__name__}")


class HardwareInterface(ABC):
    """
    Abstract base class for hardware implementations.

    Both test and connected modes must implement this interface to ensure
    consistent behavior and API compatibility.

    State Management:
    - Uses AsyncMachine FSM; self.state is the canonical state string.
    - FSM triggers (begin_move, complete_move, etc.) are async methods.
    - Invalid state transitions raise transitions.core.MachineError.

    Blocking Hardware Calls:
    - pigpio, pyserial, and picamera2 operations may be blocking.
    - Implementations must use loop.run_in_executor() for blocking calls.
    """

    _states = ['idle', 'moving', 'homing', 'emergency_stop', 'error']

    _transitions = [
        {'trigger': 'begin_move',      'source': 'idle',                       'dest': 'moving'},
        {'trigger': 'complete_move',   'source': 'moving',                     'dest': 'idle'},
        {'trigger': 'fail_move',       'source': 'moving',                     'dest': 'error'},
        {'trigger': 'begin_homing',    'source': 'idle',                       'dest': 'homing'},
        {'trigger': 'complete_homing', 'source': 'homing',                     'dest': 'idle'},
        {'trigger': 'fail_homing',     'source': 'homing',                     'dest': 'error'},
        {'trigger': 'trigger_estop',   'source': ['idle', 'moving', 'homing', 'error'], 'dest': 'emergency_stop'},
        {'trigger': 'trigger_estop',   'source': 'emergency_stop',             'dest': None},  # no-op: re-pressing e-stop is safe
        {'trigger': 'clear_estop',     'source': 'emergency_stop',             'dest': 'idle'},
        {'trigger': 'clear_error',     'source': 'error',                      'dest': 'idle'},
    ]

    def __init__(self, config: Dict[str, Any]):
        """Initialize hardware interface with configuration and FSM."""
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self._camera_stream = None  # Injected by server after initialization

        # Build the async FSM. Triggers become awaitable methods on self.
        self.machine = AsyncMachine(
            model=self,
            states=self._states,
            transitions=self._transitions,
            initial='idle',
            ignore_invalid_triggers=False,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def initialize(self) -> bool:
        """
        Initialize hardware and perform startup checks.

        Returns:
            bool: True if initialization successful
        """
        pass

    @abstractmethod
    async def shutdown(self) -> bool:
        """
        Shutdown hardware gracefully.

        Returns:
            bool: True if shutdown successful
        """
        pass

    # ------------------------------------------------------------------
    # Nozzle control
    # ------------------------------------------------------------------

    @abstractmethod
    async def move_nozzle(self, x: float, y: float, z: float, feedrate: int) -> CommandAck:
        """Move printer nozzle to specified position."""
        pass

    @abstractmethod
    async def move_nozzle_xy(self, x: float, y: float, feedrate: int) -> CommandAck:
        """Move printer nozzle XY only (Z unchanged)."""
        pass

    @abstractmethod
    async def move_nozzle_z(self, z: float, feedrate: int) -> CommandAck:
        """Move printer nozzle Z only (XY unchanged)."""
        pass

    @abstractmethod
    async def get_nozzle_position(self) -> Position:
        """Get current nozzle position."""
        pass

    @abstractmethod
    async def home_nozzle(self) -> CommandAck:
        """Home the nozzle to origin (0, 0, 0)."""
        pass

    # ------------------------------------------------------------------
    # Emergency stop
    # ------------------------------------------------------------------

    @abstractmethod
    async def emergency_stop(self) -> CommandAck:
        """Emergency stop all movement."""
        pass

    @abstractmethod
    async def clear_emergency_stop(self) -> CommandAck:
        """Clear emergency stop condition."""
        pass

    # ------------------------------------------------------------------
    # Camera
    # ------------------------------------------------------------------

    async def capture_high_res(self) -> str:
        """
        Capture high-resolution image via the injected CameraStream.

        Returns:
            str: Filename of captured image
        """
        if self._camera_stream is None:
            raise RuntimeError("Camera stream not initialized")
        return await self._camera_stream.capture()

    # ------------------------------------------------------------------
    # Telemetry and status
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_telemetry(self) -> TelemetryData:
        """Get current system telemetry."""
        pass

    async def is_ready(self) -> bool:
        """Check if hardware is ready for commands (state must be idle)."""
        return self.state == 'idle'

    # ------------------------------------------------------------------
    # Safety and limits
    # ------------------------------------------------------------------

    @abstractmethod
    def check_nozzle_limits(self, x: float, y: float, z: float) -> bool:
        """Check if nozzle position is within safe limits."""
        pass
