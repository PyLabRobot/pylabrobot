"""PreciseFlex device front-ends - the user-facing per-model device classes."""

from pylabrobot.capabilities.arms.orientable_arm import OrientableGripperArm
from pylabrobot.device import Device
from pylabrobot.resources.resource import Resource

from .arm_backend import PreciseFlexArmBackend
from .driver import PreciseFlexDriver

# -- PreciseFlex 400 -------------------------------------------------------


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
    self.driver: PreciseFlexDriver
    backend = PreciseFlexArmBackend(
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


# -- PreciseFlex 3400 ------------------------------------------------------


class PreciseFlex3400(Device):
  """Device wrapper for the PreciseFlex 3400 robotic arm.

  Mirrors :class:`PreciseFlex400`. The 3400 is the same two-link SCARA family (its link
  lengths are read from the controller at setup), with a taller reach.
  """

  def __init__(
    self,
    host: str,
    closed_gripper_position: float,
    gripper_length: float,
    port: int = 10100,
    has_rail: bool = False,
    timeout: int = 20,
    gripper_z_offset: float = 0.0,
  ) -> None:
    """
    Args:
      closed_gripper_position: firmware-unit value at which the jaws are at the backend's
        :attr:`~PreciseFlexArmBackend.min_gripper_width`. Depends on the mounted gripper;
        calibrate before first use.
      gripper_length: wrist-axis → TCP distance in mm. Required - unlike the PF400 there is
        no stock default, because the 3400 ships with an IntelliGuide gripper whose length
        differs; set it for the gripper actually mounted.
      gripper_z_offset: vertical offset in mm from the wrist plate to the tool tip.
    """
    driver = PreciseFlexDriver(host=host, port=port, timeout=timeout)
    super().__init__(driver=driver)
    self.driver: PreciseFlexDriver
    backend = PreciseFlexArmBackend(
      driver=driver,
      has_rail=has_rail,
      gripper_length=gripper_length,
      gripper_z_offset=gripper_z_offset,
      closed_gripper_position=closed_gripper_position,
    )
    self.reference = Resource(name="PreciseFlex3400", size_x=200, size_y=200, size_z=200)
    self.arm = OrientableGripperArm(backend=backend, reference_resource=self.reference)
    self._capabilities = [self.arm]
