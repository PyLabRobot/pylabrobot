import asyncio
import logging

from pylabrobot.agilent.vspin import _access2_protocol as protocol
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
    self._command_lock = asyncio.Lock()

  async def _read_exact(self, length: int) -> bytes:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + self.timeout
    response = bytearray()
    while len(response) < length:
      chunk = await self.io.read(length - len(response))
      if chunk:
        response.extend(chunk)
        continue
      if loop.time() >= deadline:
        raise TimeoutError(
          f"Access2 sent {len(response)} of {length} expected bytes within "
          f"{self.timeout} seconds: {bytes(response).hex()}"
        )
      await asyncio.sleep(0)
    return bytes(response)

  async def _read_frame(self) -> bytes:
    header = await self._read_exact(5)
    inner_length = protocol.parse_ftdi_header(header)
    return header + await self._read_exact(inner_length + 2)

  async def send_command(self, command: bytes) -> protocol.Access2Reply:
    """Send one transport-independent command through the FTDI envelope."""
    frame = protocol.build_ftdi_frame(command)
    logger.debug("[loader] Sending %s", frame.hex())
    async with self._command_lock:
      written = await self.io.write(frame)
      if written != len(frame):
        raise RuntimeError(f"Access2 wrote {written} of {len(frame)} command bytes")
      response_frame = await self._read_frame()
    logger.debug("[loader] Received %s", response_frame.hex())
    return protocol.parse_ftdi_reply(response_frame, request_id=command[0])

  async def setup(self):
    logger.debug("[loader] setup")

    await self.io.setup()
    await self.io.set_baudrate(115384)

    self._raise_on_fault(await self.request_status())

    await self.send_command(protocol.build_ping())
    await self.send_command(protocol.build_initialize())
    for address, length in ((0, 128), (128, 128), (256, 128), (384, 128), (512, 64)):
      await self.send_command(protocol.build_read_flash(address, length))
    await self.home()
    await self._wait_until_homed()
    await self._move_to_position(
      protocol.AXIS_GRIPPER,
      0,
      profile=protocol.PROFILE_DYNAMIC_EMPTY,
      speed=protocol.SPEED_FAST,
    )
    await self._move_to_location(
      protocol.LOCATION_PARK,
      0,
      15,
      profile=protocol.PROFILE_DYNAMIC_EMPTY,
      speed=protocol.SPEED_FAST,
    )

  async def stop(self):
    logger.debug("[loader] stop")
    await self.io.stop()

  async def request_status(self) -> protocol.Access2Status:
    logger.debug("[loader] request_status")
    response = await self.send_command(protocol.build_get_status())
    return protocol.decode_status(response.data)

  @staticmethod
  def _raise_on_fault(status: protocol.Access2Status) -> None:
    if status.estop_active or status.estop_set:
      raise RuntimeError(f"Access2 emergency stop is active (status 0x{status.access2_status:02x})")
    if status.motor_power_fault:
      raise RuntimeError(
        f"Access2 motor power fault is active (status 0x{status.access2_status:02x})"
      )

  async def _require_ready(self) -> protocol.Access2Status:
    status = await self.request_status()
    self._raise_on_fault(status)
    if not status.initialized or not status.homed:
      raise RuntimeError(
        f"Access2 is not initialized and homed: status 0x{status.access2_status:02x}"
      )
    return status

  async def home(self) -> None:
    logger.debug("[loader] home")
    await self.send_command(protocol.build_home())

  async def _wait_until_homed(self) -> protocol.Access2Status:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + self.timeout
    status = await self.request_status()
    while True:
      self._raise_on_fault(status)
      if status.homed:
        return status
      if loop.time() >= deadline:
        raise TimeoutError(
          f"Access2 did not report homed within {self.timeout} seconds; "
          f"last status was 0x{status.access2_status:02x}"
        )
      await asyncio.sleep(0.1)
      status = await self.request_status()

  async def _move_to_location(
    self,
    location: int,
    z_offset_mm: float,
    plate_height_mm: float,
    profile: int = protocol.PROFILE_DYNAMIC_EMPTY,
    speed: int = protocol.SPEED_SLOW,
  ) -> None:
    await self.send_command(
      protocol.build_move_to_location(
        location,
        z_offset_mm,
        plate_height_mm,
        profile,
        speed,
      )
    )

  async def _move_to_position(
    self,
    axis: int,
    position_mm: float,
    profile: int = protocol.PROFILE_DYNAMIC_EMPTY,
    speed: int = protocol.SPEED_SLOW,
  ) -> None:
    await self.send_command(protocol.build_move_to_position(axis, position_mm, profile, speed))

  async def request_sensor_values(self) -> int:
    response = await self.send_command(protocol.build_get_sensor_values())
    return protocol.decode_sensor_values(response.data)

  async def park(self):
    logger.debug("[loader] park")
    await self._move_to_location(
      protocol.LOCATION_PARK,
      8,
      15,
      profile=protocol.PROFILE_DYNAMIC_FULL,
      speed=protocol.SPEED_SLOW,
    )

  async def close(self):
    logger.debug("[loader] close")
    await self.send_command(
      protocol.build_jog_axis(
        protocol.AXIS_GRIPPER,
        1,
        protocol.PROFILE_DYNAMIC_EMPTY,
        protocol.SPEED_SLOW,
      )
    )

  async def open(self):
    logger.debug("[loader] open")
    await self.send_command(
      protocol.build_jog_axis(
        protocol.AXIS_GRIPPER,
        -1,
        protocol.PROFILE_DYNAMIC_EMPTY,
        protocol.SPEED_SLOW,
      )
    )

  async def load(self):
    """Only tested for 1cm plate, 3mm pickup height."""
    logger.debug("[loader] load")
    await self._require_ready()

    await self._move_to_position(
      protocol.AXIS_GRIPPER,
      0,
      profile=protocol.PROFILE_DYNAMIC_EMPTY,
      speed=protocol.SPEED_FAST,
    )
    await self._move_to_location(protocol.LOCATION_PICK, 3, 10)

    if await self.request_sensor_values() == protocol.SENSOR_NO_PLATE:
      raise RuntimeError("no plate found on stage")

    await self._move_to_position(protocol.AXIS_GRIPPER, 5.68)
    await self._move_to_location(
      protocol.LOCATION_BUCKET_1,
      3,
      10,
      profile=protocol.PROFILE_DYNAMIC_FULL,
    )
    await self._move_to_position(protocol.AXIS_GRIPPER, 0)
    await self._move_to_location(protocol.LOCATION_PARK, 3, 10)
    await self._require_ready()

  async def unload(self):
    """Only tested for 1cm plate, 3mm pickup height."""
    logger.debug("[loader] unload")
    await self._require_ready()

    await self._move_to_position(
      protocol.AXIS_GRIPPER,
      0,
      profile=protocol.PROFILE_DYNAMIC_EMPTY,
      speed=protocol.SPEED_FAST,
    )
    await self._move_to_location(protocol.LOCATION_BUCKET_1, 3, 10)

    if await self.request_sensor_values() == protocol.SENSOR_NO_PLATE:
      raise RuntimeError("no plate found in centrifuge")

    await self._move_to_position(protocol.AXIS_GRIPPER, 5.69)
    await self._move_to_location(
      protocol.LOCATION_PICK,
      3,
      10,
      profile=protocol.PROFILE_DYNAMIC_FULL,
    )
    await self._move_to_position(protocol.AXIS_GRIPPER, 0)
    await self._move_to_location(protocol.LOCATION_PARK, 0, 10)
    await self._require_ready()


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
