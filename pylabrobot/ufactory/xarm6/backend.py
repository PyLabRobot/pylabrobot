from dataclasses import dataclass
from typing import Dict, List, Optional

from pylabrobot.capabilities.arms.backend import (
  ArticulatedGripperArmBackend,
  CanFreedrive,
  HasJoints,
)
from pylabrobot.capabilities.arms.standard import GripperLocation
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Coordinate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.ufactory.xarm6.driver import XArm6Driver


class XArm6ArmBackend(ArticulatedGripperArmBackend, HasJoints, CanFreedrive):
  """Arm capability backend for the UFACTORY xArm 6 with bio-gripper.

  Implements :class:`ArticulatedGripperArmBackend` (full roll/pitch/yaw control)
  together with the :class:`HasJoints` and :class:`CanFreedrive` mixins.  All
  xArm SDK calls are issued directly against ``driver._arm`` via
  ``driver._call_sdk``.

  Motion profile (speed / acceleration) is supplied per-call via
  :class:`CartesianMoveParams` or :class:`JointMoveParams`; if the caller does
  not supply ``backend_params``, the dataclass defaults are used.

  All lengths are millimeters and all angles are degrees.  The xArm bio-gripper
  uses integer SDK units in the range [0, 850]; gripper widths passed in
  millimeters are converted via ``mm_per_gripper_unit`` (default 0.1 mm/unit,
  i.e. a fully-open width of 85 mm).

  Args:
    driver: :class:`XArm6Driver` instance.
    mm_per_gripper_unit: Conversion factor from SDK gripper units to mm.
    closed_threshold_mm: Gripper widths at or below this value are considered
      closed when :meth:`is_gripper_closed` is called.
    park_location: Optional Cartesian location used by :meth:`park`.  If
      ``None``, :meth:`park` falls back to the xArm SDK home position.
    park_rotation: Rotation used together with ``park_location``.
  """

  _MAX_GRIPPER_UNITS = 850

  @dataclass
  class CartesianMoveParams(BackendParams):
    """Cartesian motion profile for :meth:`move_to_location`, :meth:`pick_up_at_location`,
    and :meth:`drop_at_location`.

    Args:
      speed: Cartesian move speed in mm/s.
      mvacc: Cartesian move acceleration in mm/s^2.
    """

    speed: float = 100.0
    mvacc: float = 2000.0

  @dataclass
  class JointMoveParams(BackendParams):
    """Joint-space motion profile for :meth:`move_to_joint_position`,
    :meth:`pick_up_at_joint_position`, and :meth:`drop_at_joint_position`.

    Args:
      speed: Joint move speed in deg/s.
      mvacc: Joint move acceleration in deg/s^2.
    """

    speed: float = 50.0
    mvacc: float = 500.0

  def __init__(
    self,
    driver: XArm6Driver,
    mm_per_gripper_unit: float = 0.1,
    closed_threshold_mm: float = 1.0,
    park_location: Optional[Coordinate] = None,
    park_rotation: Optional[Rotation] = None,
  ) -> None:
    super().__init__()
    self._driver = driver
    self.mm_per_gripper_unit = mm_per_gripper_unit
    self.closed_threshold_mm = closed_threshold_mm
    self.park_location = park_location
    self.park_rotation = park_rotation or Rotation()

  # -- Param coercion --------------------------------------------------------

  def _cart_params(self, backend_params: Optional[BackendParams]) -> "XArm6ArmBackend.CartesianMoveParams":
    if isinstance(backend_params, XArm6ArmBackend.CartesianMoveParams):
      return backend_params
    return XArm6ArmBackend.CartesianMoveParams()

  def _joint_params(self, backend_params: Optional[BackendParams]) -> "XArm6ArmBackend.JointMoveParams":
    if isinstance(backend_params, XArm6ArmBackend.JointMoveParams):
      return backend_params
    return XArm6ArmBackend.JointMoveParams()

  # -- Conversion helpers ----------------------------------------------------

  def _mm_to_gripper_units(self, width_mm: float) -> int:
    units = int(round(width_mm / self.mm_per_gripper_unit))
    return max(0, min(self._MAX_GRIPPER_UNITS, units))

  def _gripper_units_to_mm(self, units: int) -> float:
    return units * self.mm_per_gripper_unit

  async def _set_gripper_units(self, units: int) -> None:
    await self._driver._call_sdk(
      self._driver._arm.set_gripper_position,
      units,
      wait=True,
      speed=0,
      op="set_gripper_position",
    )

  # -- CanGrip ---------------------------------------------------------------

  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    """Open the bio-gripper to the specified width (mm)."""
    await self._set_gripper_units(self._mm_to_gripper_units(gripper_width))

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    """Close the bio-gripper to the specified width (mm)."""
    await self._set_gripper_units(self._mm_to_gripper_units(gripper_width))

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    """Return True if the gripper width is at or below ``closed_threshold_mm``."""
    units = await self._driver._call_sdk(
      self._driver._arm.get_gripper_position, op="get_gripper_position"
    )
    return self._gripper_units_to_mm(int(units)) <= self.closed_threshold_mm

  # -- _BaseArmBackend -------------------------------------------------------

  async def halt(self, backend_params: Optional[BackendParams] = None) -> None:
    """Emergency stop all motion."""
    await self._driver._call_sdk(self._driver._arm.emergency_stop, op="emergency_stop")

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    """Move to ``park_location`` if set, otherwise to the SDK home position.

    If the SDK fails on the first ``move_gohome`` call, the driver clears
    errors and retries once automatically.
    """
    if self.park_location is not None:
      await self.move_to_location(self.park_location, self.park_rotation)
      return
    await self._driver._call_sdk(
      self._driver._arm.move_gohome,
      speed=50,
      mvacc=5000,
      wait=True,
      op="move_gohome",
      num_retries=1,
    )

  async def request_gripper_location(
    self, backend_params: Optional[BackendParams] = None
  ) -> GripperLocation:
    """Get the current gripper location and rotation."""
    pose = await self._driver._call_sdk(self._driver._arm.get_position, op="get_position")
    return GripperLocation(
      location=Coordinate(x=pose[0], y=pose[1], z=pose[2]),
      rotation=Rotation(x=pose[3], y=pose[4], z=pose[5]),
    )

  # -- ArticulatedGripperArmBackend ------------------------------------------

  async def move_to_location(
    self,
    location: Coordinate,
    rotation: Rotation,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Move to a Cartesian location with the given rotation."""
    params = self._cart_params(backend_params)
    await self._driver._call_sdk(
      self._driver._arm.set_position,
      x=location.x,
      y=location.y,
      z=location.z,
      roll=rotation.x,
      pitch=rotation.y,
      yaw=rotation.z,
      speed=params.speed,
      mvacc=params.mvacc,
      wait=True,
      op="set_position",
    )

  async def pick_up_at_location(
    self,
    location: Coordinate,
    rotation: Rotation,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Move to ``location`` and close the gripper to ``resource_width``."""
    await self.move_to_location(location, rotation, backend_params=backend_params)
    await self.close_gripper(resource_width)

  async def drop_at_location(
    self,
    location: Coordinate,
    rotation: Rotation,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Move to ``location`` and fully open the gripper."""
    await self.move_to_location(location, rotation, backend_params=backend_params)
    await self._set_gripper_units(self._MAX_GRIPPER_UNITS)

  # -- HasJoints -------------------------------------------------------------

  async def move_to_joint_position(
    self, position: Dict[int, float], backend_params: Optional[BackendParams] = None
  ) -> None:
    """Move to the specified joint angles.

    Missing joint indices are filled in from the current joint position.
    """
    params = self._joint_params(backend_params)
    current_angles = await self._driver._call_sdk(
      self._driver._arm.get_servo_angle, op="get_servo_angle"
    )
    angles = list(current_angles)
    for axis, value in position.items():
      angles[int(axis) - 1] = value
    await self._driver._call_sdk(
      self._driver._arm.set_servo_angle,
      angle=angles,
      speed=params.speed,
      mvacc=params.mvacc,
      wait=True,
      op="set_servo_angle",
    )

  async def request_joint_position(
    self, backend_params: Optional[BackendParams] = None
  ) -> Dict[int, float]:
    """Get current joint angles as ``{1: j1_deg, 2: j2_deg, ...}``."""
    angles = await self._driver._call_sdk(
      self._driver._arm.get_servo_angle, op="get_servo_angle"
    )
    return {i + 1: angles[i] for i in range(6)}

  async def pick_up_at_joint_position(
    self,
    position: Dict[int, float],
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Move to the joint target and close the gripper to ``resource_width``."""
    await self.move_to_joint_position(position, backend_params=backend_params)
    await self.close_gripper(resource_width)

  async def drop_at_joint_position(
    self,
    position: Dict[int, float],
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Move to the joint target and fully open the gripper."""
    await self.move_to_joint_position(position, backend_params=backend_params)
    await self._set_gripper_units(self._MAX_GRIPPER_UNITS)

  # -- CanFreedrive ----------------------------------------------------------

  async def start_freedrive_mode(
    self, free_axes: List[int], backend_params: Optional[BackendParams] = None
  ) -> None:
    """Enter freedrive (manual teaching) mode.

    The xArm SDK only supports freeing all axes at once, so ``free_axes`` is
    accepted for interface compatibility but ignored.
    """
    await self._driver._call_sdk(self._driver._arm.set_mode, 2, op="set_mode")
    await self._driver._call_sdk(self._driver._arm.set_state, 0, op="set_state")

  async def stop_freedrive_mode(
    self, backend_params: Optional[BackendParams] = None
  ) -> None:
    """Exit freedrive mode and return to position control."""
    await self._driver._call_sdk(self._driver._arm.set_mode, 0, op="set_mode")
    await self._driver._call_sdk(self._driver._arm.set_state, 0, op="set_state")
