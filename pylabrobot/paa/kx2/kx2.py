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
    has_rail: bool = False,
    has_servo_gripper: bool = True,
  ) -> None:
    # The non-default topologies (rail-mounted KX2, gripper-less KX2)
    # are accepted by the driver but the backend / capability layer has
    # only been exercised against the standard 4-axis arm + servo
    # gripper. setup() currently calls servo_gripper_initialize
    # unconditionally, so has_servo_gripper=False crashes during init;
    # rail support hasn't been validated end-to-end either. Surface that
    # as a clear error rather than letting users hit cryptic failures
    # downstream.
    if has_rail or not has_servo_gripper:
      raise NotImplementedError(
        "KX2 has only been tested with the default 4-axis arm + servo "
        "gripper topology (has_rail=False, has_servo_gripper=True). "
        "Other configurations are wired through to the driver but the "
        "backend setup path needs work — see KX2ArmBackend._on_setup "
        "and servo_gripper_initialize."
      )
    driver = KX2Driver(has_rail=has_rail, has_servo_gripper=has_servo_gripper)
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
