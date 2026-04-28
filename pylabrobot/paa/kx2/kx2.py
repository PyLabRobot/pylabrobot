import logging
from typing import Optional

from pylabrobot.capabilities.arms.orientable_arm import OrientableArm
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Device
from pylabrobot.paa.kx2.arm_backend import KX2ArmBackend
from pylabrobot.paa.kx2.config import GripperFingerSide
from pylabrobot.paa.kx2.driver import KX2Driver
from pylabrobot.resources.resource import Resource

logger = logging.getLogger(__name__)


class KX2(Device):
  """PAA KX2 robotic plate handler."""

  def __init__(
    self,
    gripper_length: float = 0.0,
    gripper_z_offset: float = 0.0,
    gripper_finger_side: GripperFingerSide = "barcode_reader",
  ) -> None:
    driver = KX2Driver()
    super().__init__(driver=driver)
    self.driver: KX2Driver = driver
    backend = KX2ArmBackend(
      driver=driver,
      gripper_length=gripper_length,
      gripper_z_offset=gripper_z_offset,
      gripper_finger_side=gripper_finger_side,
    )
    self.reference = Resource(name="KX2", size_x=200, size_y=200, size_z=200)
    self.arm = OrientableArm(backend=backend, reference_resource=self.reference)
    self._capabilities = [self.arm]

  async def setup(self, backend_params: Optional[BackendParams] = None):
    # Idempotent: re-running setup on a live KX2 used to crash partway through
    # the homing/PDO sequence. If already up, stay up — call stop() first to
    # force a fresh init.
    if self.setup_finished:
      logger.info("KX2.setup: already set up; skipping. Call stop() first to re-init.")
      return
    await super().setup(backend_params=backend_params)
