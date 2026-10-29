"""The PreciseFlex gripper: the end-effector at the arm's wrist, driven through the controller.

Reached as `driver.gripper`. The jaws are the controller's gripper axis (`PreciseFlexAxis.GRIPPER`):
a close limits its force (`GraspPlate`), an open lifts that limit (`ReleasePlate`), and a position
move is asked for (`MoveOneAxis`). The grasp `pickplate` closes with is set up here too.
"""

import dataclasses
import logging
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from pylabrobot.events import evented_operation
from pylabrobot.resources.end_effector import MechanicalGripper

from ...kinematics import JointState, PreciseFlexAxis

if TYPE_CHECKING:
  from ..master import PreciseFlexDriver

logger = logging.getLogger(__name__)

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
      closed_gripper_position: the gripper axis's position, in the controller's units, at which
        the jaws are at the narrow end of :attr:`jaw_width_range`.
      is_dual_gripper: whether two grippers are fitted. Discovery overrides it at setup.
    """
    self._driver = driver
    self.configuration: Optional[PreciseFlexGripperConfiguration] = None
    # What models the gripper. None until setup hangs it, only for a driver given a base plate.
    self.resource: Optional[MechanicalGripper] = None
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
      plate_width: The plate width in mm. It is sent as a position of the gripper axis, which is
        what `pickplate` closes the jaws to.
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
    units = self._mm_to_firmware_units(plate_width)
    await self._driver.send_command(f"GraspData {units} {finger_speed_percent} {grasp_force}")

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

  def update_width(self, width: float) -> None:
    """Stand the jaws on the resource that models them `width` apart.

    Does nothing until the gripper is modelled. A width outside what the model says the fingers do
    is left unset, and is logged.

    Args:
      width: how far apart the jaws stand, in mm.
    """
    if self.resource is None:
      return
    low, high = self.resource.jaw_range
    if not low <= width <= high:
      logger.warning(
        "the gripper reports its jaws %.1f mm apart, outside the %.1f to %.1f mm the model says "
        "they travel, so the model is left where it is",
        width,
        low,
        high,
      )
      return
    self.resource.jaw_width = width

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

  async def _unchecked_fw_grasp_plate(
    self, units: float, speed_percent: float, force: float
  ) -> bool:
    """Close the jaws to a little narrower than ``units`` under a capped force (``GraspPlate``).

    Nothing is guarded. The jaws stop on what they meet and the cap stays until ``ReleasePlate``;
    on nothing the controller lifts it. Returns whether something is held.
    """
    return (await self._driver.send_command(f"GraspPlate {units} {speed_percent} {force}")) == "-1"

  async def _unchecked_fw_release_plate(self, units: float, speed_percent: float) -> None:
    """Move the jaws to ``units`` and lift the force cap (``ReleasePlate``). Nothing is guarded."""
    await self._driver.send_command(f"ReleasePlate {units} {speed_percent}")

  async def _move_jaws(self, units: float, force_sensing: bool) -> None:
    """The one path that drives the jaws: to `units` on the gripper axis, once the arm has stopped,
    and reading where they did.

    Args:
      units: the gripper axis position, in the controller's units.
      force_sensing: True limits the force of a closing move (``GraspPlate``) and lifts it on an
        opening one (``ReleasePlate``); False makes a position move (``MoveOneAxis``).

    Raises:
      ValueError: If opening the jaws would carry one into the column or the base plate, on a
        modelled arm.
    """
    units = self._hold_within_gripper_limits(units)
    arm = self._driver.arm
    closing = False

    def build_targets(current: JointState) -> List[JointState]:
      nonlocal closing
      closing = units < current[PreciseFlexAxis.GRIPPER]
      return [{**current, PreciseFlexAxis.GRIPPER: units}]

    async def unchecked_fw_move(_targets: Sequence[JointState]) -> None:
      speed_percent = self.default_finger_speed_percent
      if not force_sensing:
        await arm._unchecked_fw_move_one_axis(PreciseFlexAxis.GRIPPER, units)
      elif not closing:
        await self._unchecked_fw_release_plate(units, speed_percent)
      elif not await self._unchecked_fw_grasp_plate(units, speed_percent, self.default_grasp_force):
        logger.warning(
          "[PreciseFlex %s] the jaws closed on nothing, so their force is no longer limited",
          self._driver.io._host,
        )

    # Held within the gripper's limits above; another axis out of range does not stop the jaws.
    await arm._guarded_move(
      build_targets, unchecked_fw_move, check_soft_limits=False, retry_out_of_range=False
    )

  @evented_operation(
    "precise_flex.move_gripper",
    lambda self, width, force_sensing=True: {
      "device": self._driver._controller_reference(),
      "width": float(width),
      "force_sensing": force_sensing,
    },
  )
  async def move_to_jaw_position(
    self,
    width: float,
    force_sensing: bool = True,
  ):
    """Move the PreciseFlex gripper jaws.

    A move that closes them limits its force (``GraspPlate``, at ``default_grasp_force`` and
    ``default_finger_speed_percent``): the jaws stop on what they meet, and with nothing between
    them end a little narrower than the width. A move that opens them lifts that limit
    (``ReleasePlate``). With ``force_sensing=False`` it is a position move (``MoveOneAxis``) either
    way, with no limit on force: closed onto something rigid, the motor stalls (-3105).

    A target is held half a unit inside the axis: the jaws settle up to 0.2 units past a target,
    and a target past the axis's end left a bench arm in error -3104 until homed.

    Args:
      width: the jaw width to move to, in mm.
      force_sensing: False only to make a position move on purpose.

    Not interruptible while ``GraspPlate`` runs: it blocks the controller's command interpreter
    until the jaws stop.
    """
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

  @evented_operation(
    "precise_flex.move_gripper_joint_position",
    lambda self, position, force_sensing=True: {
      "device": self._driver._controller_reference(),
      "gripper_joint_position": float(position),
      "force_sensing": force_sensing,
    },
  )
  async def move_to_jaw_position_firmware_units(
    self,
    position: float,
    force_sensing: bool = True,
  ) -> None:
    """Move the gripper to a controller-native joint position.

    This is the counterpart to :meth:`move_to_jaw_position` for integrations with
    taught joint-space routes. The caller owns the joint calibration.

    Args:
      position: the gripper axis position, in the controller's units.
      force_sensing: False only to make a position move on purpose.
    """
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
