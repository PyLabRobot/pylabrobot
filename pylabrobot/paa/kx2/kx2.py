import logging
from typing import Optional

from pylabrobot.capabilities.arms.orientable_arm import OrientableArm
from pylabrobot.capabilities.barcode_scanning import BarcodeScanner
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Device
from pylabrobot.paa.kx2.arm_backend import KX2ArmBackend
from pylabrobot.paa.kx2.barcode_reader import (
  KX2BarcodeReaderBackend,
  KX2BarcodeReaderDriver,
)
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
    barcode_port: Optional[str] = None,
    barcode_baudrate: int = KX2BarcodeReaderDriver.default_baudrate,
  ) -> None:
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

    # Onboard barcode reader is a serial device on a separate USB-serial port,
    # entirely independent of the CAN bus that drives the motors. Wire it in
    # only when a port is given; the standalone `KX2BarcodeReader` Device
    # remains available for users who prefer to manage it as a sibling.
    self._bcr_driver: Optional[KX2BarcodeReaderDriver] = None
    self.barcode_scanning: Optional[BarcodeScanner] = None
    if barcode_port is not None:
      self._bcr_driver = KX2BarcodeReaderDriver(
        port=barcode_port, baudrate=barcode_baudrate,
      )
      bcr_backend = KX2BarcodeReaderBackend(self._bcr_driver)
      self.barcode_scanning = BarcodeScanner(backend=bcr_backend)
      self._capabilities.append(self.barcode_scanning)

  async def setup(self, backend_params: Optional[BackendParams] = None):
    # Idempotent: re-running setup on a live KX2 used to crash partway through
    # the homing/PDO sequence. If already up, stay up — call stop() first to
    # force a fresh init.
    if self.setup_finished:
      logger.info("KX2.setup: already set up; skipping. Call stop() first to re-init.")
      return
    # Bring the barcode reader's serial port up before super().setup() so the
    # barcode_scanning capability's _on_setup (version handshake) has an open
    # port. If the BCR fails, abort before touching the CAN bus.
    if self._bcr_driver is not None:
      await self._bcr_driver.setup()
    try:
      await super().setup(backend_params=backend_params)
    except BaseException:
      if self._bcr_driver is not None:
        try:
          await self._bcr_driver.stop()
        except Exception:
          logger.exception("KX2.setup cleanup: BCR driver stop failed; ignoring")
      raise

  async def stop(self):
    await super().stop()
    if self._bcr_driver is not None:
      await self._bcr_driver.stop()
