"""Abstract controller interface for the Bravo.

The interface that every Bravo controller implements, whether it drives real
hardware over a transport or simulates the instrument in software. Anything
that operates the Bravo goes through this interface, so a new backend only
has to satisfy these methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Union

from ..errors import BravoError
from ..protocol.commands import LightCommandData
from ..transport import Transport
from ..types import Axis, DeviceStateFlag, GripperDetectionState, HeadType, SpeedLevel


@dataclass
class AxisMoveInfo:
  """A single axis's target in a coordinated move, in engineering units.

  Attributes:
    axis: The axis to move.
    position: Target position, in mm (or uL for the W axis).
    velocity: Move velocity, in mm/s. ``0`` leaves the controller's current
      velocity setting unchanged.
    acceleration: Move acceleration, in mm/s^2. ``0`` leaves the
      controller's current acceleration setting unchanged.
    absolute: Whether ``position`` is an absolute target (``True``) or a
      relative offset from the axis's current position (``False``).
  """

  axis: Axis
  position: float
  velocity: float = 0.0
  acceleration: float = 0.0
  absolute: bool = True


@dataclass
class MultiAxisMove:
  """A coordinated move across one or more axes.

  Attributes:
    moves: The per-axis targets to move to together.
    wait_for_complete: Whether to block until the move finishes.
    timeout: Maximum time to wait for the move to finish, in seconds.
  """

  moves: list[AxisMoveInfo] = field(default_factory=list)
  wait_for_complete: bool = True
  timeout: float = 30.0


@dataclass
class JogParams:
  """Parameters for a force-controlled jog move.

  Attributes:
    axis: The axis to jog.
    velocity: Jog velocity, in mm/s.
    acceleration: Jog acceleration, in mm/s^2.
    max_position: The position limit the jog will not move past, in mm.
    tolerance: Position tolerance for detecting that the jog has stalled
      against an obstruction, in mm.
    peak_current: Current limit for the jog move, in amps, written directly
      to the peak-current register. For a tips-on jog, interpolate this by
      tip count from the appropriate tip-current table rather than passing
      a fixed value.
  """

  axis: Axis
  velocity: float
  acceleration: float
  max_position: float
  tolerance: float
  peak_current: float


@dataclass
class FirmwareVersion:
  """Firmware version information reported by the device.

  Attributes:
    master: The master processor's firmware version string.
    sub1: The first sub-controller's firmware version string.
    sub2: The second sub-controller's firmware version string.
  """

  master: str = ""
  sub1: str = ""
  sub2: str = ""


class BravoController(ABC):
  """Abstract interface for controlling a Bravo liquid handler.

  A controller is constructed around an already-connected
  :class:`~pylabrobot.agilent.bravo.transport.Transport`; it never opens or
  closes that transport itself. Call :meth:`initialize` once the transport
  is up to bring the controller into a usable state before issuing any
  motion or state-changing commands.

  Attributes:
    has_gripper: Whether this controller's hardware has a gripper
      accessory. A subclass without one should still implement every
      gripper method the interface declares, typically by raising
      :class:`NotImplementedError` naming the model, rather than omitting
      them -- callers can check this flag first to avoid the exception
      entirely.
    model_name: The human-readable model name, used in diagnostic and
      error messages.
  """

  has_gripper: bool = True
  model_name: str = "Bravo"

  def __init__(self, transport: Transport):
    """Create a controller bound to an already-connected transport.

    Args:
      transport: The transport this controller communicates over. The
        caller is responsible for setting it up before construction and
        tearing it down afterwards.
    """
    self._transport = transport

  # -- Lifecycle --

  @abstractmethod
  def initialize(self) -> None:
    """Bring the controller into a usable state.

    Performs whatever generation-specific startup the hardware needs before
    it will accept motion or state-changing commands -- for example
    commutation or homing -- using the transport supplied to the
    constructor. Called once, after the transport has been set up.
    """

  def deinitialize(self) -> None:
    """Release whatever generation-specific resources :meth:`initialize` acquired.

    The counterpart to :meth:`initialize`: stops any background work a
    subclass started (for example a wire-protocol engine's receive thread)
    without touching the transport itself, which the caller still owns and
    may tear down or reuse afterward. The default implementation is a no-op,
    for subclasses with nothing to release.
    """

  @abstractmethod
  def ping(self) -> bool:
    """Return whether the device responds to a liveness check."""

  @property
  @abstractmethod
  def is_connected(self) -> bool:
    """Whether the underlying transport is currently connected."""

  # -- Firmware --

  @abstractmethod
  def get_firmware_version(self) -> FirmwareVersion:
    """Return the device's reported firmware version."""

  # -- Motion --

  @abstractmethod
  def move(self, moves: list[AxisMoveInfo], wait: bool = True, timeout: float = 30.0) -> None:
    """Execute a coordinated multi-axis move.

    Args:
      moves: The per-axis targets to move to together.
      wait: Whether to block until the move finishes.
      timeout: Maximum time to wait for the move to finish, in seconds.
    """

  @abstractmethod
  def home_axes(self, axes: list[Axis], *, force: bool = False) -> None:
    """Home one or more axes.

    Args:
      axes: The axes to home.
      force: Re-runs the homing routine on an axis that already reports
        itself homed. Backends that always home unconditionally may ignore
        it. Use it for an explicit operator "home this axis" request, where
        doing nothing because the axis looks homed is the wrong answer.
    """

  @abstractmethod
  def jog(self, params: JogParams) -> float:
    """Execute a force-controlled jog.

    Args:
      params: The jog parameters.

    Returns:
      The axis's final position, in mm.
    """

  @abstractmethod
  def get_position(self, axis: Axis) -> float:
    """Return the current position of an axis, in engineering units (mm or uL)."""

  @abstractmethod
  def is_axis_homed(self, axis: Axis) -> bool:
    """Return whether an axis currently reports itself homed."""

  @abstractmethod
  def get_park_position(self, axis: Axis) -> float:
    """Return the configured park position for an axis, in mm."""

  # -- Motor control --

  @abstractmethod
  def enable_motor(self, axis: Axis) -> None:
    """Enable the motor drive for an axis."""

  @abstractmethod
  def disable_motor(self, axis: Axis) -> None:
    """Disable the motor drive for an axis."""

  @abstractmethod
  def reset_faults(self, axes: list[Axis]) -> None:
    """Clear any latched fault state on the given axes."""

  # -- Device state --

  @abstractmethod
  def query_state(self) -> DeviceStateFlag:
    """Return the device's current state flags."""

  @abstractmethod
  def is_go_button_pressed(self) -> bool:
    """Return whether the Go button is currently pressed."""

  @abstractmethod
  def clear_go_button(self) -> None:
    """Clear the latched Go-button-pressed state."""

  # -- Lights --

  @abstractmethod
  def set_light(self, command: LightCommandData) -> None:
    """Set the indicator light to the given color, blink period, and duty cycle."""

  @abstractmethod
  def clear_lights(self) -> None:
    """Turn the indicator light off."""

  # -- Head detection --

  @abstractmethod
  def read_head_adc(self) -> int:
    """Read the raw ADC value used for resistor-based head detection."""

  @abstractmethod
  def detect_smart_head(self) -> bool:
    """Return whether a smart head (with onboard PIC/EEPROM) is present."""

  @abstractmethod
  def read_smart_head_type(self) -> int:
    """Read the head type code stored in the smart head's EEPROM."""

  def get_head_type(self) -> HeadType:
    """Return the head type this controller currently has cached.

    A caller that needs to know the installed head but was not itself
    given one (for example a task constructed without an explicit
    ``head_type`` argument) falls back to this. The default is the
    all-around common head; a controller that tracks a detected or
    assigned head type overrides this to return it.

    Returns:
      The cached head type, or ``"96_d_70"`` if this controller does not
      track one.
    """
    return "96_d_70"

  # -- Unit conversion --

  def ul_to_mm(self, volume_ul: float) -> float:
    """Convert a pipette volume, in microliters, to W-axis millimetres.

    The W (plunger) axis's native engineering unit varies by controller
    generation: some express W positions directly in microliters, others
    in the millimetres of physical plunger travel that volume requires
    for the currently installed head. This lets a caller building a W-axis
    move convert once, in a controller-appropriate way, instead of
    special-casing the generation itself.

    Args:
      volume_ul: The volume to convert, in microliters.

    Returns:
      ``volume_ul`` unchanged, for a controller whose W axis is already
      microliter-native. A controller whose W axis is millimetre-native
      overrides this to apply its head-specific conversion factor.
    """
    return volume_ul

  # -- Gripper --

  @abstractmethod
  def detect_gripper(self) -> GripperDetectionState:
    """Return whether the gripper accessory is currently detected."""

  @abstractmethod
  def grip(self, speed: SpeedLevel, position: float, grip_lid: bool = False) -> None:
    """Close the gripper jaws to the given position.

    Args:
      speed: The speed profile to grip at.
      position: Target jaw position, in mm.
      grip_lid: Whether this grip is closing on a plate lid rather than a
        plate body.
    """

  @abstractmethod
  def open_gripper(self, position: Optional[float] = None) -> None:
    """Open the gripper jaws.

    Args:
      position: Target jaw position, in mm. Defaults to the standard open
        position when omitted.
    """

  @abstractmethod
  def is_plate_in_gripper(self) -> bool:
    """Return whether a plate is currently held in the gripper."""

  def read_plate_sensor(self, transient: float = 0.0) -> bool:
    """Read the physical plate-presence sensor, where the hardware supports it.

    Args:
      transient: How long to allow for a transient sensor reading to settle,
        in seconds, before treating it as final.

    Returns:
      Whether the sensor currently reports a plate present.

    Raises:
      NotImplementedError: If this controller does not support direct
        plate-sensor reads.
    """
    raise NotImplementedError(
      f"{self.__class__.__name__} does not support direct plate-sensor reads"
    )

  def scan_stack_with_gripper(
    self,
    *,
    start_zg: float,
    end_zg: float,
    speed: SpeedLevel,
    transient: float = 0.0,
  ) -> dict[str, Union[float, bool, None]]:
    """Scan the Zg axis between two heights until the plate sensor detects a stack top.

    Args:
      start_zg: Zg position to start the scan from, in mm.
      end_zg: Zg position to stop the scan at if nothing is detected, in mm.
      speed: The speed profile to scan at.
      transient: How long to allow for a transient sensor reading to settle,
        in seconds, before treating it as final.

    Returns:
      A dict describing the scan result, at minimum ``"detected"`` (bool)
      and ``"final_zg"`` (float).

    Raises:
      NotImplementedError: If this controller does not support gripper
        stack scanning.
    """
    raise NotImplementedError(f"{self.__class__.__name__} does not support gripper stack scanning")

  # -- Generic command dispatch --

  @abstractmethod
  def send_command(self, command_id: int, data: bytes = b"", timeout: float = 2.0) -> bytes:
    """Send a low-level command directly, for extensibility beyond this interface.

    Args:
      command_id: The wire command ID to send.
      data: The command payload.
      timeout: Maximum time to wait for a response, in seconds.

    Returns:
      The response payload.
    """

  # -- Last error --

  @property
  @abstractmethod
  def last_error(self) -> Optional[BravoError]:
    """The most recent error this controller recorded, if any."""
