from pylabrobot.capabilities.arms.articulated_arm import ArticulatedArm
from pylabrobot.device import Device
from pylabrobot.resources import Resource
from pylabrobot.ufactory.xarm6.backend import XArm6ArmBackend
from pylabrobot.ufactory.xarm6.driver import XArm6Driver


class XArm6(Device):
  """UFACTORY xArm 6 robotic arm with bio-gripper.

  Composes an :class:`XArm6Driver`, a default :class:`XArm6ArmBackend`, and an
  :class:`ArticulatedArm` frontend.  The arm capability is exposed as
  ``self.arm``; joint-space and freedrive operations live on
  ``self.arm.backend``.

  Args:
    driver: Pre-configured :class:`XArm6Driver` (holds IP, speed/accel
      defaults, TCP offset/load, etc.).
  """

  def __init__(self, driver: XArm6Driver) -> None:
    super().__init__(driver=driver)
    self.driver: XArm6Driver = driver
    backend = XArm6ArmBackend(driver=driver)
    self.reference = Resource(name="XArm6", size_x=200, size_y=200, size_z=200)
    self.arm = ArticulatedArm(backend=backend, reference_resource=self.reference)
    self._capabilities = [self.arm]
