from pylabrobot.capabilities.arms.orientable_arm import OrientableArm
from pylabrobot.device import Device
from pylabrobot.paa.kx2.kx2_backend import KX2ArmBackend, KX2Driver
from pylabrobot.resources.resource import Resource


class KX2(Device):
  """PAA KX2 robotic plate handler."""

  def __init__(self) -> None:
    driver = KX2Driver()
    super().__init__(driver=driver)
    self.driver: KX2Driver = driver
    backend = KX2ArmBackend(driver=driver)
    self.reference = Resource(name="KX2", size_x=200, size_y=200, size_z=200)
    self.arm = OrientableArm(backend=backend, reference_resource=self.reference)
    self._capabilities = [self.arm]
