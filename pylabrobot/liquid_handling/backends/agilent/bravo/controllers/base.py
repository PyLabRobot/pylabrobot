"""Abstract controller interface for the Bravo.

Ported from IHomewoodController.h -- the pure virtual interface that both
Agile, Darwin, and Simulation controllers implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.commands import LightCommandData
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.errors import BravoError
from pylabrobot.liquid_handling.backends.agilent.bravo.types import Axis, DeviceStateFlag, GripperDetectionState, HeadType, SpeedLevel


@dataclass
class AxisMoveInfo:
    """High-level move parameters in engineering units (mm, mm/s, mm/s^2)."""
    axis: Axis
    position: float             # mm (or uL for W-axis)
    velocity: float = 0.0      # mm/s
    acceleration: float = 0.0  # mm/s^2
    absolute: bool = True


@dataclass
class MultiAxisMove:
    """Coordinated multi-axis move."""
    moves: list[AxisMoveInfo] = field(default_factory=list)
    wait_for_complete: bool = True
    timeout_ms: int = 30000


@dataclass
class JogParams:
    """Parameters for a force-controlled jog move."""
    axis: Axis
    velocity: float        # mm/s
    acceleration: float    # mm/s^2
    max_position: float    # mm (limit)
    tolerance: float       # mm
    peak_current: float    # amps — written directly to I2T_PEAK_CURRENT. For
                           # Tips On, interpolate by tip count from
                           # LT_TIP_CURRENT_TABLE / ST_TIP_CURRENT_TABLE.


@dataclass
class FirmwareVersion:
    """Firmware version information."""
    master: str = ""
    sub1: str = ""
    sub2: str = ""


class BravoController(ABC):
    """Abstract interface for controlling a Bravo liquid handler.

    Subclassed by AgileController, DarwinController, and SimulationController.
    """

    # -- Connection --

    @abstractmethod
    def open_serial(self, port: str) -> None: ...

    @abstractmethod
    def open_tcp(self, address: str) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def ping(self) -> bool: ...

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...

    # -- Firmware --

    @abstractmethod
    def get_firmware_version(self) -> FirmwareVersion: ...

    # -- Motion --

    @abstractmethod
    def move(self, moves: list[AxisMoveInfo], wait: bool = True, timeout_ms: int = 30000) -> None:
        """Execute a coordinated multi-axis move."""

    @abstractmethod
    def home_axes(self, axes: list[Axis]) -> None:
        """Home one or more axes."""

    @abstractmethod
    def jog(self, params: JogParams) -> float:
        """Execute a force-controlled jog. Returns final position."""

    @abstractmethod
    def get_position(self, axis: Axis) -> float:
        """Read current position of an axis in engineering units (mm or uL)."""

    @abstractmethod
    def is_axis_homed(self, axis: Axis) -> bool: ...

    @abstractmethod
    def get_park_position(self, axis: Axis) -> float:
        """Return the controller/profile park position for an axis."""

    # -- Motor control --

    @abstractmethod
    def enable_motor(self, axis: Axis) -> None: ...

    @abstractmethod
    def disable_motor(self, axis: Axis) -> None: ...

    @abstractmethod
    def reset_faults(self, axes: list[Axis]) -> None: ...

    # -- Device state --

    @abstractmethod
    def query_state(self) -> DeviceStateFlag: ...

    @abstractmethod
    def is_go_button_pressed(self) -> bool: ...

    @abstractmethod
    def clear_go_button(self) -> None: ...

    # -- Lights --

    @abstractmethod
    def set_light(self, command: LightCommandData) -> None: ...

    @abstractmethod
    def clear_lights(self) -> None: ...

    # -- Head detection --

    @abstractmethod
    def read_head_adc(self) -> int:
        """Read the ADC value for resistor-based head detection."""

    @abstractmethod
    def detect_smart_head(self) -> bool:
        """Returns True if a smart head (with PIC/EEPROM) is present."""

    @abstractmethod
    def read_smart_head_type(self) -> int:
        """Read head type code from smart head EEPROM."""

    # -- Gripper --

    @abstractmethod
    def detect_gripper(self) -> GripperDetectionState: ...

    @abstractmethod
    def grip(self, speed: SpeedLevel, position: float, grip_lid: bool = False) -> None: ...

    @abstractmethod
    def open_gripper(self, position: float | None = None) -> None: ...

    @abstractmethod
    def is_plate_in_gripper(self) -> bool: ...

    def read_plate_sensor(self, transient_ms: int = 0) -> bool:
        """Read the physical plate-presence sensor when available."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support direct plate-sensor reads")

    def scan_stack_with_gripper(
        self,
        *,
        start_zg: float,
        end_zg: float,
        speed: SpeedLevel,
        transient_ms: int = 0,
    ) -> dict[str, float | bool | None]:
        """Scan Zg until the plate sensor detects a stack top."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support gripper stack scanning")

    # -- Generic command dispatch --

    @abstractmethod
    def send_command(self, command_id: int, data: bytes = b"",
                     timeout_ms: int = 2000) -> bytes:
        """Low-level command send (for extensibility)."""

    # -- Last error --

    @property
    @abstractmethod
    def last_error(self) -> BravoError | None: ...
