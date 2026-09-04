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

_MOTION_POLL_INTERVAL = 0.1
_AXIS_POSITION_TOLERANCE = 0.1
_DEFAULT_GRIPPER_OPEN_POSITION = 0.0
_DEFAULT_GRIPPER_CLOSE_THRESHOLD = 1.5
_DEFAULT_GRIPPER_CLOSED_POSITION = 5.68
_AXIS_NAMES: dict[int, str] = {
  protocol.AXIS_GRIPPER: "gripper",
  protocol.AXIS_Y: "Y",
  protocol.AXIS_Z: "Z",
}


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

  def __init__(
    self,
    device_id: str,
    timeout: int = 60,
    gripper_open_position: float = _DEFAULT_GRIPPER_OPEN_POSITION,
    gripper_closed_position: float = _DEFAULT_GRIPPER_CLOSED_POSITION,
    gripper_close_threshold: float = _DEFAULT_GRIPPER_CLOSE_THRESHOLD,
  ):
    """
    Args:
      device_id: The libftdi id for the loader. Find using
        `python3 -m pylibftdi.examples.list_devices`
      timeout: Communication and operation timeout in seconds.
      gripper_open_position: Absolute gripper-axis position used when opening.
      gripper_closed_position: Absolute gripper-axis position used when closing.
      gripper_close_threshold: Smallest gripper-axis position considered closed around a plate.
    """
    super().__init__()
    if not gripper_open_position < gripper_close_threshold <= gripper_closed_position:
      raise ValueError(
        "Gripper positions must satisfy open position < close threshold <= closed position"
      )
    self.io = FTDI(human_readable_device_name="Agilent Access2 Loader", device_id=device_id)
    self.timeout = timeout
    self.gripper_open_position = gripper_open_position
    self.gripper_closed_position = gripper_closed_position
    self.gripper_close_threshold = gripper_close_threshold
    self._command_lock = asyncio.Lock()
    self._operation_lock = asyncio.Lock()

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

  async def send_command(
    self, command: bytes, raise_on_error: bool = True
  ) -> protocol.Access2Reply:
    """Send one transport-independent command through the FTDI envelope."""
    frame = protocol.build_ftdi_frame(command)
    logger.debug("[loader] Sending %s", frame.hex())
    async with self._command_lock:
      written = await self.io.write(frame)
      if written != len(frame):
        raise RuntimeError(f"Access2 wrote {written} of {len(frame)} command bytes")
      response_frame = await self._read_frame()
    logger.debug("[loader] Received %s", response_frame.hex())
    response = protocol.parse_ftdi_reply(response_frame, request_id=command[0])
    if raise_on_error and response.result != 0:
      raise protocol.Access2ProtocolError(
        f"Access2 command 0x{command[0]:02x} returned result 0x{response.result:02x}; "
        f"response data: {response.data.hex()}"
      )
    return response

  async def setup(self):
    logger.debug("[loader] setup")
    async with self._operation_lock:
      await self.io.setup()
      await self.io.set_baudrate(115384)

      self._raise_on_fault(await self.request_status(), operation="setup precondition")

      await self.send_command(protocol.build_ping())
      await self.send_command(protocol.build_initialize())
      await self._home()
      await self._move_axis_to_position(
        protocol.AXIS_GRIPPER,
        self.gripper_open_position,
        profile=protocol.PROFILE_DYNAMIC_EMPTY,
        speed=protocol.SPEED_FAST,
      )
      await self._move_to_teachpoint(
        protocol.TEACHPOINT_PARK,
        0,
        15,
        profile=protocol.PROFILE_DYNAMIC_EMPTY,
        speed=protocol.SPEED_FAST,
      )
      await self._require_ready(operation="setup postcondition")

  async def stop(self):
    logger.debug("[loader] stop")
    await self.io.stop()

  async def request_status(self) -> protocol.Access2Status:
    logger.debug("[loader] request_status")
    response = await self.send_command(protocol.build_get_status())
    return protocol.decode_status(response.data)

  async def request_firmware_version(self) -> str:
    """Return the Access2 controller firmware version."""
    response = await self.send_command(protocol.build_get_firmware_version())
    return protocol.decode_firmware_version(response.data)

  async def request_hardware_version(self) -> int:
    """Return the Access2 controller hardware version."""
    response = await self.send_command(protocol.build_get_hardware_version())
    return protocol.decode_hardware_version(response.data)

  @staticmethod
  def _raise_on_fault(status: protocol.Access2Status, *, operation: str | None = None) -> None:
    context = "" if operation is None else f" during {operation}"
    if status.estop_active or status.estop_set:
      raise RuntimeError(
        f"Access2 emergency stop is active{context} (status 0x{status.access2_status:02x})"
      )
    if status.motor_power_fault:
      raise RuntimeError(
        f"Access2 motor power fault is active{context} (status 0x{status.access2_status:02x})"
      )

  async def _require_ready(self, operation: str = "readiness check") -> protocol.Access2Status:
    status = await self.request_status()
    self._raise_on_fault(status, operation=operation)
    if not status.initialized or not status.homed:
      raise RuntimeError(
        f"Access2 is not initialized and homed during {operation}: "
        f"status 0x{status.access2_status:02x}"
      )
    return status

  async def _home(self) -> protocol.Access2Status:
    logger.debug("[loader] home")
    await self.send_command(protocol.build_home())
    return await self._wait_until_homed()

  async def home(self) -> None:
    """Home all Access2 axes and wait for the controller to confirm completion."""
    async with self._operation_lock:
      await self._home()

  async def _wait_until_homed(self) -> protocol.Access2Status:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + self.timeout
    status = await self.request_status()
    while True:
      self._raise_on_fault(status, operation="homing")
      if status.homed:
        return status
      if loop.time() >= deadline:
        raise TimeoutError(
          f"Access2 did not report homed within {self.timeout} seconds; "
          f"last status was 0x{status.access2_status:02x}"
        )
      await asyncio.sleep(0.1)
      status = await self.request_status()

  @staticmethod
  def _axis_name(axis: int) -> str:
    try:
      return _AXIS_NAMES[axis]
    except KeyError as error:
      raise ValueError(f"Unknown Access2 axis: {axis}") from error

  @staticmethod
  def _gripper_is_at_position(status: protocol.Access2Status, position: float) -> bool:
    return (
      status.gripper_status is not None
      and bool(status.gripper_status & protocol.AXIS_STATUS_MOVE_DONE)
      and status.gripper_position is not None
      and abs(status.gripper_position - position) <= _AXIS_POSITION_TOLERANCE
    )

  def _gripper_is_closed(self, status: protocol.Access2Status) -> bool:
    axis_faults = (
      protocol.AXIS_STATUS_CHECKSUM_ERROR
      | protocol.AXIS_STATUS_OVERCURRENT
      | protocol.AXIS_STATUS_POSITION_ERROR
      | protocol.AXIS_STATUS_HOMING
    )
    at_closed_position = (
      status.gripper_position is not None
      and abs(status.gripper_position - self.gripper_closed_position) <= _AXIS_POSITION_TOLERANCE
    )
    return (
      status.gripper_status is not None
      and bool(status.gripper_status & protocol.AXIS_STATUS_MOVE_DONE)
      and not bool(status.gripper_status & axis_faults)
      and status.gripper_position is not None
      and status.gripper_position >= self.gripper_close_threshold
      and (at_closed_position or status.optical_plate_sensor)
    )

  async def _wait_until_motion_complete(
    self,
    axes: tuple[int, ...],
    operation: str,
    *,
    target_axis: int | None = None,
    target_position: float | None = None,
  ) -> protocol.Access2Status:
    """Wait for full status to confirm that the selected axes finished moving."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + self.timeout
    status = await self.request_status()
    while True:
      self._raise_on_fault(status, operation=operation)
      if not status.initialized or not status.homed:
        raise RuntimeError(
          f"Access2 lost initialized/homed state during {operation}: "
          f"status 0x{status.access2_status:02x}"
        )

      axis_details: list[str] = []
      motion_done = True
      for axis in axes:
        axis_status = status.axis_status(axis)
        if axis_status is None:
          raise RuntimeError(
            f"Access2 cannot confirm {operation}: full axis status was not returned"
          )
        axis_details.append(f"{self._axis_name(axis)}=0x{axis_status:02x}")
        motion_done = motion_done and bool(axis_status & protocol.AXIS_STATUS_MOVE_DONE)

      position_matches = True
      if target_axis is not None and target_position is not None:
        position = status.axis_position(target_axis)
        if position is None:
          raise RuntimeError(
            f"Access2 cannot confirm {operation}: full axis position was not returned"
          )
        axis_details.append(f"{self._axis_name(target_axis)}={position:.3f} mm")
        position_matches = abs(position - target_position) <= _AXIS_POSITION_TOLERANCE

      if motion_done and position_matches:
        return status
      if loop.time() >= deadline:
        details = ", ".join(axis_details)
        raise TimeoutError(
          f"Access2 did not complete {operation} within {self.timeout} seconds; "
          f"last status: {details}"
        )
      await asyncio.sleep(_MOTION_POLL_INTERVAL)
      status = await self.request_status()

  async def _move_to_teachpoint(
    self,
    teachpoint: int,
    z_offset: float,
    plate_height: float,
    profile: int = protocol.PROFILE_DYNAMIC_EMPTY,
    speed: int = protocol.SPEED_SLOW,
  ) -> None:
    await self.send_command(
      protocol.build_move_to_teachpoint(
        teachpoint,
        z_offset,
        plate_height,
        profile,
        speed,
      )
    )
    await self._wait_until_motion_complete(
      (protocol.AXIS_Y, protocol.AXIS_Z),
      operation=f"move to teachpoint {teachpoint}",
    )

  async def _move_axis_to_position(
    self,
    axis: int,
    position: float,
    profile: int = protocol.PROFILE_DYNAMIC_EMPTY,
    speed: int = protocol.SPEED_SLOW,
  ) -> None:
    await self.send_command(protocol.build_move_axis_to_position(axis, position, profile, speed))
    await self._wait_until_motion_complete(
      (axis,),
      operation=f"{self._axis_name(axis)} move to {position:.3f} mm",
      target_axis=axis,
      target_position=position,
    )

  async def _jog_axis(
    self,
    axis: int,
    displacement: float,
    profile: int = protocol.PROFILE_DYNAMIC_EMPTY,
    speed: int = protocol.SPEED_SLOW,
  ) -> None:
    await self.send_command(protocol.build_jog_axis(axis, displacement, profile, speed))
    await self._wait_until_motion_complete(
      (axis,),
      operation=f"{self._axis_name(axis)} jog by {displacement:.3f} mm",
    )

  async def _tighten_grip(self) -> None:
    """Move the gripper one relative step toward a tighter grip."""
    await self._jog_axis(
      protocol.AXIS_GRIPPER,
      1,
      protocol.PROFILE_DYNAMIC_EMPTY,
      protocol.SPEED_SLOW,
    )

  async def _loosen_grip(self) -> None:
    """Move the gripper one relative step toward a looser grip."""
    await self._jog_axis(
      protocol.AXIS_GRIPPER,
      -1,
      protocol.PROFILE_DYNAMIC_EMPTY,
      protocol.SPEED_SLOW,
    )

  async def request_sensor_values(self) -> int:
    response = await self.send_command(protocol.build_get_sensor_values())
    return protocol.decode_sensor_values(response.data)

  async def _close_gripper(self) -> protocol.Access2Status:
    """Close until the configured threshold, allowing normal plate contact.

    The FTDI controller can return a nonzero result when a plate stops the
    gripper before its unobstructed target. The result is retained for
    diagnostics. Full controller status must confirm completion and either the
    unobstructed close position or plate-sensor-backed contact past the close
    threshold.
    """
    response = await self.send_command(
      protocol.build_move_axis_to_position(
        protocol.AXIS_GRIPPER,
        self.gripper_closed_position,
        protocol.PROFILE_DYNAMIC_EMPTY,
        protocol.SPEED_SLOW,
      ),
      raise_on_error=False,
    )
    status = await self._wait_until_motion_complete(
      (protocol.AXIS_GRIPPER,),
      operation="close gripper",
    )
    if not self._gripper_is_closed(status) or (
      response.result != 0 and not status.optical_plate_sensor
    ):
      assert status.gripper_position is not None
      assert status.gripper_status is not None
      error = (
        "Access2 did not close the gripper: "
        f"position {status.gripper_position:.3f}, threshold {self.gripper_close_threshold:.3f}, "
        f"axis status 0x{status.gripper_status:02x}, "
        f"optical plate sensor {status.optical_plate_sensor}, "
        f"command result 0x{response.result:02x}"
      )
      if response.result != 0:
        raise protocol.Access2ProtocolError(error)
      raise RuntimeError(error)
    if response.result != 0:
      logger.debug(
        "[loader] Gripper-close command returned 0x%02x; full status confirmed closed",
        response.result,
      )
    return status

  async def park(self):
    logger.debug("[loader] park")
    async with self._operation_lock:
      await self._require_ready(operation="park precondition")
      await self._move_to_teachpoint(
        protocol.TEACHPOINT_PARK,
        8,
        15,
        profile=protocol.PROFILE_DYNAMIC_FULL,
        speed=protocol.SPEED_SLOW,
      )
      await self._require_ready(operation="park postcondition")

  async def close_gripper(self) -> None:
    """Move the gripper to its normal closed position."""
    logger.debug("[loader] close gripper")
    async with self._operation_lock:
      status = await self._require_ready(operation="gripper-close precondition")
      if self._gripper_is_closed(status):
        return
      await self._close_gripper()
      await self._require_ready(operation="gripper-close postcondition")

  async def open_gripper(self) -> None:
    """Move the gripper to its open position."""
    logger.debug("[loader] open gripper")
    async with self._operation_lock:
      status = await self._require_ready(operation="gripper-open precondition")
      if self._gripper_is_at_position(status, self.gripper_open_position):
        return
      await self._move_axis_to_position(
        protocol.AXIS_GRIPPER,
        self.gripper_open_position,
        profile=protocol.PROFILE_DYNAMIC_EMPTY,
      )
      await self._require_ready(operation="gripper-open postcondition")

  async def load(self):
    """Only tested for 1cm plate, 3mm pickup height."""
    logger.debug("[loader] load")
    async with self._operation_lock:
      await self._require_ready(operation="load precondition")

      await self._move_axis_to_position(
        protocol.AXIS_GRIPPER,
        self.gripper_open_position,
        profile=protocol.PROFILE_DYNAMIC_EMPTY,
        speed=protocol.SPEED_FAST,
      )
      await self._move_to_teachpoint(protocol.TEACHPOINT_PICK, 3, 10)

      if not (await self.request_sensor_values() & protocol.STATUS_OPTICAL_PLATE_SENSOR):
        raise RuntimeError("no plate found on stage")

      await self._close_gripper()
      await self._move_to_teachpoint(
        protocol.TEACHPOINT_BUCKET_1,
        3,
        10,
        profile=protocol.PROFILE_DYNAMIC_FULL,
      )
      await self._move_axis_to_position(
        protocol.AXIS_GRIPPER,
        self.gripper_open_position,
        profile=protocol.PROFILE_DYNAMIC_EMPTY,
      )
      await self._move_to_teachpoint(protocol.TEACHPOINT_PARK, 3, 10)
      await self._require_ready(operation="load postcondition")

  async def unload(self):
    """Only tested for 1cm plate, 3mm pickup height."""
    logger.debug("[loader] unload")
    async with self._operation_lock:
      await self._require_ready(operation="unload precondition")

      await self._move_axis_to_position(
        protocol.AXIS_GRIPPER,
        self.gripper_open_position,
        profile=protocol.PROFILE_DYNAMIC_EMPTY,
        speed=protocol.SPEED_FAST,
      )
      await self._move_to_teachpoint(protocol.TEACHPOINT_BUCKET_1, 3, 10)

      if not (await self.request_sensor_values() & protocol.STATUS_OPTICAL_PLATE_SENSOR):
        raise RuntimeError("no plate found in centrifuge")

      await self._close_gripper()
      await self._move_to_teachpoint(
        protocol.TEACHPOINT_PICK,
        3,
        10,
        profile=protocol.PROFILE_DYNAMIC_FULL,
      )
      await self._move_axis_to_position(
        protocol.AXIS_GRIPPER,
        self.gripper_open_position,
        profile=protocol.PROFILE_DYNAMIC_EMPTY,
      )
      await self._move_to_teachpoint(protocol.TEACHPOINT_PARK, 0, 10)
      await self._require_ready(operation="unload postcondition")


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
    gripper_open_position: float = _DEFAULT_GRIPPER_OPEN_POSITION,
    gripper_closed_position: float = _DEFAULT_GRIPPER_CLOSED_POSITION,
    gripper_close_threshold: float = _DEFAULT_GRIPPER_CLOSE_THRESHOLD,
  ):
    """Create an Access2 loader with configurable absolute gripper positions.

    Args:
      name: Resource name.
      device_id: The libftdi identifier for the loader.
      vspin: Paired VSpin centrifuge.
      size_x: Resource width in millimeters.
      size_y: Resource depth in millimeters.
      size_z: Resource height in millimeters.
      gripper_open_position: Absolute gripper-axis position used when opening.
      gripper_closed_position: Absolute gripper-axis position used when closing.
      gripper_close_threshold: Smallest gripper-axis position considered closed around a plate.
    """
    driver = Access2Driver(
      device_id=device_id,
      gripper_open_position=gripper_open_position,
      gripper_closed_position=gripper_closed_position,
      gripper_close_threshold=gripper_close_threshold,
    )
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

  async def _require_vspin_ready_for_transfer(self) -> None:
    """Confirm the paired VSpin is physically safe for loader motion."""
    if not await self._vspin.request_door_open():
      raise CentrifugeDoorError("Centrifuge door-open sensor must be active for plate transfer.")
    if not await self._vspin.request_bucket_locked():
      raise RuntimeError("Centrifuge bucket must be physically locked for plate transfer.")
    if await self._vspin.request_spinning():
      raise RuntimeError("Centrifuge must be stopped for plate transfer.")

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

    await self._require_vspin_ready_for_transfer()

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

    await self._require_vspin_ready_for_transfer()

    await self.driver.unload()

    self.assign_child_resource(self._vspin.at_bucket.resource)
