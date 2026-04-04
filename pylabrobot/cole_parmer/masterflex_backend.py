import logging

try:
  import serial  # type: ignore

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

from typing import Optional

logger = logging.getLogger(__name__)

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.pumping.backend import PumpBackend
from pylabrobot.capabilities.pumping.calibration import PumpCalibration
from pylabrobot.capabilities.pumping.pumping import Pump
from pylabrobot.device import Device, Driver
from pylabrobot.io.serial import Serial


class MasterflexDriver(Driver):
  """Serial driver for Cole Parmer Masterflex L/S pumps.

  tested on:
  07551-20

  should be same as:
  07522-20
  07522-30
  07551-30
  07575-30
  07575-40

  Documentation available at:
    - https://pim-resources.coleparmer.com/instruction-manual/a-1299-1127b-en.pdf
    - https://web.archive.org/web/20210924061132/https://pim-resources.coleparmer.com/
      instruction-manual/a-1299-1127b-en.pdf
  """

  def __init__(self, com_port: str):
    super().__init__()
    if not HAS_SERIAL:
      raise RuntimeError(
        "pyserial is not installed. Install with: pip install pylabrobot[serial]. "
        f"Import error: {_SERIAL_IMPORT_ERROR}"
      )
    self.com_port = com_port
    self.io = Serial(
      port=self.com_port,
      baudrate=4800,
      timeout=1,
      parity=serial.PARITY_ODD,
      stopbits=serial.STOPBITS_ONE,
      bytesize=serial.SEVENBITS,
      human_readable_device_name="Masterflex Pump",
    )

  async def setup(self, backend_params: Optional[BackendParams] = None):
    await self.io.setup()
    await self.io.write(b"\x05")  # Enquiry; ready to send.
    await self.io.write(b"\x05P02\r")
    logger.info("[Masterflex %s] connected", self.com_port)

  async def stop(self):
    await self.io.stop()
    logger.info("[Masterflex %s] disconnected", self.com_port)

  async def send_command(self, command: str):
    command = "\x02P02" + command + "\x0d"
    await self.io.write(command.encode())
    return await self.io.read()

  def serialize(self):
    return {"type": self.__class__.__name__, "com_port": self.com_port}


class MasterflexBackend(PumpBackend):
  """Pump capability backend for Masterflex L/S pumps."""

  def __init__(self, driver: MasterflexDriver):
    self.driver = driver

  async def run_revolutions(self, num_revolutions: float):
    num_revolutions = round(num_revolutions, 2)
    logger.info("[Masterflex %s] dispensing %.2f revolutions", self.driver.com_port, num_revolutions)
    cmd = f"V{num_revolutions}G"
    await self.driver.send_command(cmd)

  async def run_continuously(self, speed: float):
    if speed == 0:
      await self.halt()
      return

    logger.info("[Masterflex %s] pumping continuously at speed=%s direction=%s", self.driver.com_port, abs(speed), "forward" if speed > 0 else "reverse")
    direction = "+" if speed > 0 else "-"
    speed_int = int(abs(speed))
    cmd = f"S{direction}{speed_int}G0"
    await self.driver.send_command(cmd)

  async def halt(self):
    logger.info("[Masterflex %s] halting", self.driver.com_port)
    await self.driver.send_command("H")

  def serialize(self):
    return {
      "com_port": self.driver.com_port,
    }


class MasterflexPump(Device):
  """Cole Parmer Masterflex L/S pump."""

  def __init__(
    self,
    com_port: str,
    calibration: Optional[PumpCalibration] = None,
  ):
    driver = MasterflexDriver(com_port=com_port)
    super().__init__(driver=driver)
    self.driver: MasterflexDriver
    self.pumping = Pump(backend=MasterflexBackend(driver), calibration=calibration)
    self._capabilities = [self.pumping]
