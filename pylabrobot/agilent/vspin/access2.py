import asyncio
import logging
import time

from pylabrobot.agilent.vspin.errors import (
  BucketHasPlateError,
  BucketNoPlateError,
  CentrifugeDoorError,
  LoaderNoPlateError,
  NotAtBucketError,
)
from pylabrobot.agilent.vspin.vspin import VSpin
from pylabrobot.events import evented_operation, resource_reference
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import Coordinate, ResourceHolder

logger = logging.getLogger(__name__)


def _loader_load_event_context(self: "Access2") -> dict:
  plate = self.resource
  return {
    "device": resource_reference(self),
    "resources": [] if plate is None else [resource_reference(plate)],
    "source": resource_reference(self),
    "destination": resource_reference(self._vspin.at_bucket),
  }


def _loader_unload_event_context(self: "Access2") -> dict:
  bucket = self._vspin.at_bucket
  plate = None if bucket is None else bucket.resource
  return {
    "device": resource_reference(self),
    "resources": [] if plate is None else [resource_reference(plate)],
    "source": resource_reference(bucket),
    "destination": resource_reference(self),
  }


class Access2Driver:
  """FTDI driver for the Agilent Access2 centrifuge loader."""

  def __init__(self, device_id: str, timeout: int = 60):
    """
    Args:
      device_id: The libftdi id for the loader. Find using
        `python3 -m pylibftdi.examples.list_devices`
    """
    super().__init__()
    self.io = FTDI(human_readable_device_name="Agilent Access2 Loader", device_id=device_id)
    self.timeout = timeout

  async def _read(self) -> bytes:
    x = b""
    r = None
    start = time.time()
    while r != b"" or x == b"":
      r = await self.io.read(1)
      x += r
      if r == b"":
        await asyncio.sleep(0.1)
      if x == b"" and (time.time() - start) > self.timeout:
        raise TimeoutError("No data received within the specified timeout period")
    return x

  async def send_command(self, command: bytes) -> bytes:
    logger.debug("[loader] Sending %s", command.hex())
    await self.io.write(command)
    return await self._read()

  async def setup(self):
    logger.debug("[loader] setup")

    await self.io.setup()
    await self.io.set_baudrate(115384)

    status = await self.request_status()
    if not status.startswith(bytes.fromhex("1105")):
      raise RuntimeError("Failed to get status")

    await self.send_command(bytes.fromhex("110500030014000072b1"))
    await self.send_command(bytes.fromhex("1105000300100000ae71"))
    await self.send_command(bytes.fromhex("110500070024040000008000be89"))
    await self.send_command(bytes.fromhex("11050007002404008000800063b1"))
    await self.send_command(bytes.fromhex("11050007002404000001800089b9"))
    await self.send_command(bytes.fromhex("1105000700240400800180005481"))
    await self.send_command(bytes.fromhex("110500070024040000024000c6bd"))
    await self.send_command(bytes.fromhex("1105000300400000f0bf"))
    await self.send_command(bytes.fromhex("1105000a004607000100000000020235bf"))
    await self.send_command(bytes.fromhex("1105000e00440b00000000000000007041020203c7"))

  async def stop(self):
    logger.debug("[loader] stop")
    await self.io.stop()

  async def request_status(self) -> bytes:
    logger.debug("[loader] request_status")
    return await self.send_command(bytes.fromhex("11050003002000006bd4"))

  async def park(self):
    logger.debug("[loader] park")
    await self.send_command(bytes.fromhex("1105000e00440b0000000000410000704103007539"))

  async def close(self):
    logger.debug("[loader] close")
    await self.send_command(bytes.fromhex("1105000a00420700010000803f02008c64"))

  async def open(self):
    logger.debug("[loader] open")
    await self.send_command(bytes.fromhex("1105000a0042070001000080bf0200b73e"))

  async def load(self):
    """Only tested for 1cm plate, 3mm pickup height."""
    logger.debug("[loader] load")

    await self.send_command(bytes.fromhex("1105000a004607000100000000020235bf"))
    await self.send_command(bytes.fromhex("1105000e00440b000100004040000020410200a5cb"))

    r = await self.send_command(bytes.fromhex("1105000300500000b3dc"))
    if r == bytes.fromhex("1105000800510500000300000079f1"):
      raise RuntimeError("no plate found on stage")

    await self.send_command(bytes.fromhex("1105000a00460700018fc2b540020023dc"))
    await self.send_command(bytes.fromhex("1105000e00440b000200004040000020410300ee00"))
    await self.send_command(bytes.fromhex("1105000a004607000100000000020015fd"))
    await self.send_command(bytes.fromhex("1105000e00440b0000000040400000204102007d82"))

  async def unload(self):
    """Only tested for 1cm plate, 3mm pickup height."""
    logger.debug("[loader] unload")

    await self.send_command(bytes.fromhex("1105000a004607000100000000020235bf"))
    await self.send_command(bytes.fromhex("1105000e00440b000200004040000020410200dd31"))

    r = await self.send_command(bytes.fromhex("1105000300500000b3dc"))
    if r == bytes.fromhex("1105000800510500000300000079f1"):
      raise RuntimeError("no plate found in centrifuge")

    await self.send_command(bytes.fromhex("1105000a00460700017b14b6400200d57a"))
    await self.send_command(bytes.fromhex("1105000e00440b00010000404000002041030096fa"))
    await self.send_command(bytes.fromhex("1105000a004607000100000000020015fd"))
    await self.send_command(bytes.fromhex("1105000e00440b00000000000000002041020056be"))


class Access2(ResourceHolder):
  """Agilent Access2 centrifuge loader."""

  def __init__(
    self,
    name: str,
    device_id: str,
    vspin: VSpin,
    size_x: float = 0.0,
    size_y: float = 0.0,
    size_z: float = 0.0,
  ):
    driver = Access2Driver(device_id=device_id)
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="Agilent Access2",
      category="loader",
      child_location=Coordinate.zero(),
    )
    self.driver: Access2Driver = driver
    self._vspin = vspin

  @evented_operation("centrifuge_loader.load", _loader_load_event_context)
  async def load(self) -> None:
    if not self._vspin.door_open:
      raise CentrifugeDoorError("Centrifuge door must be open to load a plate.")
    if self._vspin.at_bucket is None:
      raise NotAtBucketError(
        "Centrifuge must be at a bucket to load a plate. "
        "Use vspin.go_to_bucket1() or vspin.go_to_bucket2()."
      )
    if self.resource is None:
      raise LoaderNoPlateError("Loader must have a plate to load.")
    if self._vspin.at_bucket.resource is not None:
      raise BucketHasPlateError("Bucket must be empty to load a plate.")

    await self.driver.load()

    self._vspin.at_bucket.assign_child_resource(self.resource, location=Coordinate.zero())

  @evented_operation("centrifuge_loader.unload", _loader_unload_event_context)
  async def unload(self) -> None:
    if not self._vspin.door_open:
      raise CentrifugeDoorError("Centrifuge door must be open to unload a plate.")
    if self._vspin.at_bucket is None:
      raise NotAtBucketError(
        "Centrifuge must be at a bucket to unload a plate. "
        "Use vspin.go_to_bucket1() or vspin.go_to_bucket2()."
      )
    if self._vspin.at_bucket.resource is None:
      raise BucketNoPlateError("Bucket must have a plate to unload.")

    await self.driver.unload()

    self.assign_child_resource(self._vspin.at_bucket.resource)
