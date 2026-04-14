from pylabrobot.capabilities.arms.orientable_arm import OrientableArm
from pylabrobot.device import Device
from pylabrobot.paa.kx2.kx2_backend import KX2ArmBackend, KX2Driver
from pylabrobot.paa.kx2.kx2_canopen_driver import KX2CanopenDriver
from pylabrobot.resources.resource import Resource


class KX2(Device):
  """PAA KX2 robotic plate handler (legacy hand-rolled CAN driver)."""

  def __init__(self) -> None:
    driver = KX2Driver()
    super().__init__(driver=driver)
    self.driver: KX2Driver = driver
    backend = KX2ArmBackend(driver=driver)
    self.reference = Resource(name="KX2", size_x=200, size_y=200, size_z=200)
    self.arm = OrientableArm(backend=backend, reference_resource=self.reference)
    self._capabilities = [self.arm]


class KX2Canopen(Device):
  """PAA KX2 robotic plate handler (canopen-library driver).

  Drop-in replacement for :class:`KX2` using :class:`KX2CanopenDriver`
  underneath. Public API is identical — both wrap the same
  `KX2ArmBackend` capability backend. Prefer this once the hello-world
  notebook runs clean against it on hardware; the legacy class will be
  removed afterwards.
  """

  def __init__(self) -> None:
    driver = KX2CanopenDriver()
    super().__init__(driver=driver)
    self.driver: KX2CanopenDriver = driver
    backend = KX2ArmBackend(driver=driver)
    self.reference = Resource(name="KX2", size_x=200, size_y=200, size_z=200)
    self.arm = OrientableArm(backend=backend, reference_resource=self.reference)
    self._capabilities = [self.arm]
