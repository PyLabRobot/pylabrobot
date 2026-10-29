"""The PreciseFlex gripper: the end-effector at the arm's wrist, driven through the controller.

Reached as `driver.gripper`. The jaws are the controller's gripper axis (`PreciseFlexAxis.GRIPPER`), commanded
through their open and close positions; a force-controlled grasp for `pickplate` is set up here.
"""

import dataclasses
from functools import wraps
import logging
from typing import (
  TYPE_CHECKING,
  Awaitable,
  Callable,
  Concatenate,
  Optional,
  ParamSpec,
  Tuple,
  TypeVar,
  cast,
)

from pylabrobot.events import evented_operation

if TYPE_CHECKING:
  from ..master import PreciseFlexDriver

logger = logging.getLogger(__name__)

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _admitted_motion(
  method: Callable[Concatenate["PreciseFlexGripper", _P], Awaitable[_R]],
) -> Callable[Concatenate["PreciseFlexGripper", _P], Awaitable[_R]]:
  """Hold finite-motion admission for one public operation, including nested calls."""

  @wraps(method)
  async def wrapper(self: "PreciseFlexGripper", *args: _P.args, **kwargs: _P.kwargs) -> _R:
    async with self._driver._motion_admission():
      return await method(self, *args, **kwargs)

  return cast(Callable[Concatenate["PreciseFlexGripper", _P], Awaitable[_R]], wrapper)

# Measured on a bench PF400: the servo overshoots, so a target at an axis end lands past it.
_GRIPPER_LIMIT_HEADROOM = 0.5
_GRIPPER_UNIT_EPS = 1e-6


@dataclasses.dataclass(frozen=True)
class PreciseFlexGripperConfiguration:
  """The gripper axis's facts, read at setup."""

  soft_limit_range: tuple
  hard_limit_range: tuple
  max_speed: float
  max_acceleration: float
  max_deceleration: float
  is_dual_gripper: bool = False


class PreciseFlexGripper:
  """The gripper fitted to a PreciseFlex arm: a servoed single gripper, or a dual one.

  Reached as `driver.gripper`. Sends through the driver's `send_command`.
  """

  # What the driver sends when a caller leaves the value out. Tune one gripper by assigning on the
  # instance, or every gripper by assigning on the class.
  default_finger_speed_percent: float = 50.0
  default_grasp_force: float = 10.0

  # Physical jaw range for the PF400 servoed gripper, in mm. Its narrow end is the calibration
  # anchor for closed_gripper_position; setup converts the axis soft limits into it.
  jaw_width_range: Tuple[float, float] = (60.0, 145.0)

  def __init__(
    self, driver: "PreciseFlexDriver", closed_gripper_position: float, is_dual_gripper: bool = False
  ) -> None:
    """
    Args:
      driver: the driver to send commands through.
      closed_gripper_position: firmware-unit value (passed to ``GripClosePos`` /
        ``GripOpenPos``) at which the jaws are at the narrow end of :attr:`jaw_width_range`.
      is_dual_gripper: whether two grippers are fitted. Discovery overrides it at setup.
    """
    self._driver = driver
    self.configuration: Optional[PreciseFlexGripperConfiguration] = None
    self.closed_gripper_position = closed_gripper_position
    # closed_gripper_position was calibrated against this width, so discovery must not move it.
    self._anchor_width_mm = self.jaw_width_range[0]
    self._is_dual_gripper = is_dual_gripper

  # -- session / discovery -------------------------------------------------------------------------

  def _adopt_configuration(self, configuration: PreciseFlexGripperConfiguration) -> None:
    """Take the jaw range from the gripper-axis soft limits, and whether two are fitted."""
    self.configuration = configuration
    gmin, gmax = configuration.soft_limit_range
    # The limits are in the axis's units; both ends convert to mm through the calibration anchor.
    self.jaw_width_range = (
      self._anchor_width_mm + (gmin - self.closed_gripper_position),
      self._anchor_width_mm + (gmax - self.closed_gripper_position),
    )
    self._is_dual_gripper = configuration.is_dual_gripper

  # -- grasp, for the force-controlled pickplate ---------------------------------------------------

  async def _set_grasp_data(
    self, plate_width: float, finger_speed_percent: float, grasp_force: float
  ) -> None:
    """Set the data to be used for the next force-controlled PickPlate command grip operation.

    This data remains in effect until the next GraspData command or the system is restarted.

    Args:
      plate_width: The plate width in mm.
      finger_speed_percent: The finger speed during grasp as a percentage (0-100). 100 = full speed.
      grasp_force: The gripper squeezing force, in Newtons.
      A positive value indicates the fingers must close to grasp.
      A negative value indicates the fingers must open to grasp.

    Raises:
      ValueError: If finger_speed_percent is not between 0 and 100.
    """
    if not 0 <= finger_speed_percent <= 100:
      raise ValueError(
        f"finger_speed_percent must be between 0 and 100, got {finger_speed_percent}"
      )
    await self._driver.send_command(f"GraspData {plate_width} {finger_speed_percent} {grasp_force}")

  # -- conversions: the wire counts in the axis's units, the driver speaks mm ----------------------

  def _mm_to_firmware_units(self, width_mm: float) -> float:
    """Convert a jaw width (mm) to the firmware's native position unit.

    Anchored on the construction-time calibration pair, so a width commands the same jaw travel
    before and after setup. Slope is 1 (1 mm = 1 unit).
    """
    return self.closed_gripper_position + (width_mm - self._anchor_width_mm)

  def _firmware_units_to_mm(self, units: float) -> float:
    """Convert the firmware's native position unit to a jaw width (mm): `_mm_to_firmware_units`
    the other way."""
    return self._anchor_width_mm + (units - self.closed_gripper_position)

  def _get_gripper_limits(self) -> Tuple[float, float]:
    """The gripper axis's soft limits, refusing before setup has read them.

    Raises:
      RuntimeError: If setup has not read the limits; a target past the axis end strands it.
    """
    if self.configuration is None:
      raise RuntimeError(
        "the gripper axis's limits have not been read, so a target cannot be held inside them; "
        "run setup() before moving the gripper"
      )
    return self.configuration.soft_limit_range

  def _hold_within_gripper_limits(self, units: float) -> float:
    """A gripper target held a little inside the axis's soft limits."""
    soft_min, soft_max = self._get_gripper_limits()
    low = soft_min + _GRIPPER_LIMIT_HEADROOM
    high = soft_max - _GRIPPER_LIMIT_HEADROOM
    held = min(max(units, low), high)
    if held != units:
      logger.warning(
        "[PreciseFlex %s] gripper target %s held to %s, inside [%s, %s]",
        self._driver.io._host,
        units,
        held,
        soft_min,
        soft_max,
      )
    return held

  # -- jaw motion --------------------------------------------------------------------------------

  async def _unchecked_fw_move_jaws(self, units: float, force_sensing: bool) -> None:
    """Drive the jaws to ``units``, closing or opening. Nothing is guarded.

    Sets the close position and closes (``gripper 2``), or sets the open position and opens
    (``gripper 1``). Both are position moves.
    """
    self._driver._reject_motion_during_freedrive_scope()
    if force_sensing:
      await self._driver.send_command(f"GripClosePos {units}")
      await self._driver.send_command("gripper 2")
    else:
      await self._driver.send_command(f"GripOpenPos {units}")
      await self._driver.send_command("gripper 1")

  async def _move_jaws(self, units: float, force_sensing: bool) -> None:
    """The one path that drives the jaws: to `units` on the gripper axis, once the arm has stopped.

    Args:
      units: the gripper axis position, in the controller's units.
      force_sensing: True closes (``gripper 2``), False opens (``gripper 1``).
    """
    units = self._hold_within_gripper_limits(units)
    await self._driver.arm._wait_for_eom()
    await self._unchecked_fw_move_jaws(units, force_sensing)

  @_admitted_motion
  @evented_operation(
    "precise_flex.move_gripper",
    lambda self, width, force_sensing=False: {
      "device": self._driver._controller_reference(),
      "width": float(width),
      "force_sensing": force_sensing,
    },
  )
  async def move_to_jaw_position(
    self,
    width: float,
    force_sensing: bool = False,
  ):
    """Move the PreciseFlex gripper jaws.

    ``force_sensing=False`` drives to the open position (``gripper 1``); ``force_sensing=True``
    drives to the close position (``gripper 2``). Both are position moves: ``gripper 2`` limits the
    force it applies getting there, but it does not stop the jaws where they meet something.

    A target is held half a unit inside the axis: the jaws settle up to 0.2 units past a target,
    and a target past the axis's end left a bench arm in error -3104 until homed.

    Args:
      width: the jaw width to move to, in mm.
      force_sensing: whether to close (True) or open (False) to the width.

    Not interruptible while a ``gripper`` command runs: it blocks the controller's command
    interpreter until the jaws finish (hardware-verified, like ``waitForEom``).
    """
    self._driver._reject_motion_during_freedrive_scope()
    logger.info(
      "[PreciseFlex %s] move_to_jaw_position: width_mm=%s force_sensing=%s",
      self._driver.io._host,
      width,
      force_sensing,
    )
    units = self._mm_to_firmware_units(width)
    soft_min, soft_max = self._get_gripper_limits()
    # An advertised end converts back through a subtract and an add, so allow float dust.
    if not (soft_min - _GRIPPER_UNIT_EPS <= units <= soft_max + _GRIPPER_UNIT_EPS):
      raise ValueError(
        f"gripper width {width} mm maps to firmware units {units:.1f}, outside the gripper "
        f"axis range [{soft_min}, {soft_max}] - check closed_gripper_position "
        f"(currently {self.closed_gripper_position})."
      )
    await self._move_jaws(units, force_sensing)

  @_admitted_motion
  @evented_operation(
    "precise_flex.move_gripper_joint_position",
    lambda self, position, force_sensing=False: {
      "device": self._driver._controller_reference(),
      "gripper_joint_position": float(position),
      "force_sensing": force_sensing,
    },
  )
  async def move_to_jaw_position_firmware_units(
    self,
    position: float,
    force_sensing: bool = False,
  ) -> None:
    """Move the gripper to a controller-native joint position.

    This is the counterpart to :meth:`move_to_jaw_position` for integrations with
    taught joint-space routes. The caller owns the joint calibration.

    Args:
      position: the gripper axis position, in the controller's units.
      force_sensing: whether to close (True) or open (False) to the position.
    """
    self._driver._reject_motion_during_freedrive_scope()
    await self._move_jaws(position, force_sensing)

  # -- sensors -------------------------------------------------------------------------------------

  async def sense_fully_closed(self) -> bool:
    """(Single gripper only) Whether the end-of-travel sensor reads the gripper fully closed.

    Returns:
      For standard gripper: True if the gripper is within 2mm of fully closed, otherwise False.
    """
    if self._is_dual_gripper:
      raise ValueError("IsGripperClosed command is only valid for single gripper robots.")
    response = await self._driver.send_command("IsFullyClosed")
    return int(response) == -1

  async def sense_each_fully_closed(self) -> tuple[bool, bool]:
    """(Dual gripper only) Whether each gripper's end-of-travel sensor reads it fully closed."""
    if not self._is_dual_gripper:
      raise ValueError("AreGrippersClosed command is only valid for dual gripper robots.")
    response = await self._driver.send_command("IsFullyClosed")
    ret_int = int(response)
    gripper_1_closed = (ret_int & 1) != 0
    gripper_2_closed = (ret_int & 2) != 0
    return (gripper_1_closed, gripper_2_closed)
