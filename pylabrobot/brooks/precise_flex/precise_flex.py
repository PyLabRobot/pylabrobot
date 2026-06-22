"""PreciseFlex device front-ends - the user-facing per-model device classes."""

from typing import Optional

from pylabrobot.capabilities.arms.orientable_arm import OrientableGripperArm
from pylabrobot.capabilities.arms.standard import JointPose
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Device
from pylabrobot.resources.resource import Resource

from .arm_backend import PreciseFlexArmBackend
from .config import Axis
from .driver import PreciseFlexDriver


class PreciseFlex400Backend(PreciseFlexArmBackend):
  """Backend for the PreciseFlex 400 robotic arm."""

  _PARKING_POSITION: JointPose = {
    Axis.BASE: 170.0,
    Axis.SHOULDER: 0.0,
    Axis.ELBOW: 180.0,
    Axis.WRIST: -180.0,
  }

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    """Move the PF400 to its parking position via joint move.

    Sends an explicit joint target instead of the firmware ``movetosafe`` command.
    """
    await self.move_to_joint_position(position=self._PARKING_POSITION)


class PreciseFlex400(Device):
  """Device wrapper for the PreciseFlex 400 robotic arm."""

  def __init__(
    self,
    host: str,
    closed_gripper_position: float,
    port: int = 10100,
    has_rail: bool = False,
    timeout: int = 20,
    gripper_length: float = 162.0,
    gripper_z_offset: float = 0.0,
    recover_out_of_range_at_setup: bool = True,
  ) -> None:
    """
    Args:
      closed_gripper_position: firmware-unit value at which the jaws are at the
        backend's :attr:`~PreciseFlexArmBackend.min_gripper_width`. Depends on
        the specific gripper mounted; calibrate before first use.
      gripper_length: wrist-axis → TCP distance in mm. Defaults to 162 mm, which
        matches the stock single gripper on the PF400.
      gripper_z_offset: vertical offset in mm from the wrist plate to the tool tip.
        Defaults to 0 mm.
      recover_out_of_range_at_setup: when True (the default), setup tries to drive a
        small out-of-range excursion back inside the soft limits; set False to skip
        that. Either way setup raises if an axis is still out of range afterward.
    """
    driver = PreciseFlexDriver(host=host, port=port, timeout=timeout)
    super().__init__(driver=driver)
    self.driver: PreciseFlexDriver = driver
    backend = PreciseFlex400Backend(
      driver=driver,
      has_rail=has_rail,
      gripper_length=gripper_length,
      gripper_z_offset=gripper_z_offset,
      closed_gripper_position=closed_gripper_position,
      recover_out_of_range_at_setup=recover_out_of_range_at_setup,
    )
    self.reference = Resource(name="PreciseFlex400", size_x=200, size_y=200, size_z=200)
    self.arm = OrientableGripperArm(backend=backend, reference_resource=self.reference)
    self._capabilities = [self.arm]


class PreciseFlex3400Backend(PreciseFlexArmBackend):
  """Backend for the PreciseFlex 3400 robotic arm."""

  def __init__(
    self,
    driver: PreciseFlexDriver,
    gripper_length: float,
    gripper_z_offset: float,
    closed_gripper_position: float,
    has_rail: bool = False,
  ) -> None:
    super().__init__(
      driver=driver,
      has_rail=has_rail,
      gripper_length=gripper_length,
      gripper_z_offset=gripper_z_offset,
      closed_gripper_position=closed_gripper_position,
    )
