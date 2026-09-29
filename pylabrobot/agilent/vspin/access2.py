from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal

from pylabrobot.agilent.vspin import _access2_protocol as protocol
from pylabrobot.agilent.vspin._state import (
  Access2Activity,
  Access2MachineState,
  Access2Operation,
  ConnectionState,
  TransferDirection,
  TransferPhase,
  TransferProgress,
  TransitionToken,
)
from pylabrobot.agilent.vspin.errors import (
  BucketHasPlateError,
  BucketNoPlateError,
  LoaderNoPlateError,
  NotAtBucketError,
)
from pylabrobot.agilent.vspin.vspin import VSpin
from pylabrobot.events import evented_operation, resource_reference
from pylabrobot.io.ftdi import FTDI, is_ftdi_transport_error
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

Access2Speed = Literal["slow", "medium", "fast"]


def _speed_code(speed: Access2Speed) -> int:
  """Translate a named controller speed preset before starting an operation."""
  speeds = {
    "slow": protocol.SPEED_SLOW,
    "medium": protocol.SPEED_MEDIUM,
    "fast": protocol.SPEED_FAST,
  }
  try:
    return speeds[speed]
  except (KeyError, TypeError) as error:
    raise ValueError("Access2 speed must be 'slow', 'medium', or 'fast'") from error


@dataclasses.dataclass(frozen=True)
class TransferRoute:
  """Teachpoints for one transfer direction."""

  source_teachpoint: int
  destination_teachpoint: int
  source_name: str


def _transfer_route(
  direction: TransferDirection,
  bucket_teachpoint: int,
) -> TransferRoute:
  """Build one transfer route from existing Access2 protocol constants."""
  if bucket_teachpoint not in (
    protocol.TEACHPOINT_BUCKET_1,
    protocol.TEACHPOINT_BUCKET_2,
  ):
    raise ValueError(f"Invalid bucket teachpoint: {bucket_teachpoint}")

  if direction is TransferDirection.INTO_CENTRIFUGE:
    return TransferRoute(
      source_teachpoint=protocol.TEACHPOINT_PICK,
      destination_teachpoint=bucket_teachpoint,
      source_name="stage",
    )
  if direction is TransferDirection.OUT_OF_CENTRIFUGE:
    return TransferRoute(
      source_teachpoint=bucket_teachpoint,
      destination_teachpoint=protocol.TEACHPOINT_PICK,
      source_name="centrifuge",
    )
  raise ValueError(f"Invalid transfer direction: {direction}")


def _loader_lifecycle_event_context(self: "Access2") -> dict[str, object]:
  """Identify the loader whose connection lifecycle is changing."""
  return {"device": resource_reference(self)}


def _loader_load_event_context(self: "Access2", **parameters: float | str) -> dict:
  plate = self.resource
  return {
    "device": resource_reference(self),
    "resources": [] if plate is None else [resource_reference(plate)],
    "source": resource_reference(self),
    "destination": resource_reference(self._vspin.at_bucket),
    "parameters": parameters,
  }


def _loader_unload_event_context(self: "Access2", **parameters: float | str) -> dict:
  bucket = self._vspin.at_bucket
  plate = None if bucket is None else bucket.resource
  return {
    "device": resource_reference(self),
    "resources": [] if plate is None else [resource_reference(plate)],
    "source": resource_reference(bucket),
    "destination": resource_reference(self),
    "parameters": parameters,
  }


class Access2Driver:
  """FTDI driver for the Agilent Access2 centrifuge loader."""

  def __init__(
    self,
    device_id: str,
    timeout: int = 60,
  ):
    """
    Args:
      device_id: The libftdi id for the loader. Find using
        `python3 -m pylibftdi.examples.list_devices`
      timeout: Communication and operation timeout in seconds.
    """
    super().__init__()
    self.io = FTDI(human_readable_device_name="Agilent Access2 Loader", device_id=device_id)
    self.timeout = timeout
    self._command_lock = asyncio.Lock()
    self._operation_lock = asyncio.Lock()
    self._state = Access2MachineState()

  @property
  def state(self) -> Access2MachineState:
    """Return the current Access2 semantic-state snapshot."""
    return self._state

  def _mark_recovery_required(self, *, position_uncertain: bool) -> None:
    """Make recovery sticky and invalidate only an uncertain arm teachpoint."""
    self._state = dataclasses.replace(
      self._state,
      recovery_required=True,
      last_teachpoint=None if position_uncertain else self._state.last_teachpoint,
    )

  def _record_disconnected(self) -> None:
    """Record transport closure without copying controller status facts."""
    self._state = dataclasses.replace(
      self._state,
      connection=ConnectionState.DISCONNECTED,
      last_teachpoint=None,
    )

  @asynccontextmanager
  async def _operation_scope(
    self,
    operation: Access2Operation,
  ) -> AsyncIterator[TransitionToken]:
    """Own the operation lock and apply actuation-aware state changes."""
    async with self._operation_lock:
      previous_operation = self._state.operation
      transition = TransitionToken()
      self._state = dataclasses.replace(self._state, operation=operation)
      try:
        yield transition
      except BaseException as error:
        if is_ftdi_transport_error(error):
          self._record_disconnected()
        if transition.actuated:
          self._mark_recovery_required(position_uncertain=transition.position_uncertain)
        else:
          self._state = dataclasses.replace(self._state, operation=previous_operation)
        raise
      else:
        self._state = dataclasses.replace(self._state, operation=Access2Activity.IDLE)

  def _set_transfer_phase(self, phase: TransferPhase) -> None:
    """Advance the active transfer while retaining its direction and bucket."""
    transfer = self._state.operation
    if not isinstance(transfer, TransferProgress):
      raise RuntimeError("No Access2 transfer is active")
    self._state = dataclasses.replace(
      self._state,
      operation=dataclasses.replace(transfer, phase=phase),
    )

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
    try:
      async with self._command_lock:
        written = await self.io.write(frame)
        if written != len(frame):
          raise RuntimeError(f"Access2 wrote {written} of {len(frame)} command bytes")
        response_frame = await self._read_frame()
    except BaseException as error:
      if is_ftdi_transport_error(error):
        self._record_disconnected()
      raise
    logger.debug("[loader] Received %s", response_frame.hex())
    response = protocol.parse_ftdi_reply(response_frame, request_id=command[0])
    if raise_on_error and response.result != 0:
      raise protocol.Access2ProtocolError(
        f"Access2 command 0x{command[0]:02x} returned result 0x{response.result:02x}; "
        f"response data: {response.data.hex()}"
      )
    return response

  async def setup(self) -> None:
    """Connect, initialize, home, open, and park the Access2 loader."""
    logger.debug("[loader] setup")
    async with self._operation_scope(Access2Activity.INITIALIZING) as transition:
      if self._state.recovery_required:
        raise RuntimeError("Access2 requires recovery during setup")
      self._state = dataclasses.replace(self._state, connection=ConnectionState.CONNECTING)
      try:
        await self.io.setup()
      except BaseException:
        self._record_disconnected()
        raise
      self._state = dataclasses.replace(self._state, connection=ConnectionState.CONNECTED)
      await self.io.set_baudrate(115384)

      self._raise_on_fault(await self.request_status(), operation="setup precondition")

      await self.send_command(protocol.build_ping())
      transition.mark_actuated()
      await self.send_command(protocol.build_initialize())
      self._state = dataclasses.replace(
        self._state,
        operation=Access2Activity.HOMING,
        last_teachpoint=None,
      )
      transition.mark_actuated(position_uncertain=True)
      await self._home()
      transition.confirm_position()
      self._state = dataclasses.replace(self._state, operation=Access2Activity.MOVING)
      transition.mark_actuated()
      await self._move_axis_to_position(
        protocol.AXIS_GRIPPER,
        _DEFAULT_GRIPPER_OPEN_POSITION,
        profile=protocol.PROFILE_DYNAMIC_EMPTY,
        speed=protocol.SPEED_FAST,
      )
      self._state = dataclasses.replace(self._state, last_teachpoint=None)
      transition.mark_actuated(position_uncertain=True)
      await self._move_to_teachpoint(
        protocol.TEACHPOINT_PARK,
        0,
        15,
        profile=protocol.PROFILE_DYNAMIC_EMPTY,
        speed=protocol.SPEED_FAST,
      )
      self._state = dataclasses.replace(
        self._state,
        last_teachpoint=protocol.TEACHPOINT_PARK,
      )
      transition.confirm_position()
      await self._require_ready(operation="setup postcondition")

  async def stop(self) -> None:
    """Close the Access2 transport and invalidate session-scoped state."""
    logger.debug("[loader] stop")
    async with self._operation_lock:
      self._state = dataclasses.replace(self._state, connection=ConnectionState.DISCONNECTING)
      try:
        await self.io.stop()
      finally:
        self._record_disconnected()

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

  async def _require_ready(
    self,
    operation: str = "readiness check",
  ) -> protocol.Access2Status:
    """Return fresh ready status after semantic and controller checks."""
    if self._state.connection is not ConnectionState.CONNECTED:
      raise RuntimeError(f"Access2 is not connected during {operation}")
    if self._state.recovery_required:
      raise RuntimeError(f"Access2 requires recovery during {operation}")
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
    async with self._operation_scope(Access2Activity.HOMING) as transition:
      if self._state.connection is not ConnectionState.CONNECTED:
        raise RuntimeError("Access2 is not connected during home")
      if self._state.recovery_required:
        raise RuntimeError("Access2 requires recovery during home")
      self._state = dataclasses.replace(self._state, last_teachpoint=None)
      transition.mark_actuated(position_uncertain=True)
      status = await self._home()
      if not status.initialized:
        raise RuntimeError(
          f"Access2 lost initialized state during home: status 0x{status.access2_status:02x}"
        )
      transition.confirm_position()

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

  @staticmethod
  def _gripper_is_closed(
    status: protocol.Access2Status,
    *,
    gripper_closed_position: float,
    gripper_close_threshold: float,
  ) -> bool:
    at_closed_position = (
      status.gripper_position is not None
      and abs(status.gripper_position - gripper_closed_position) <= _AXIS_POSITION_TOLERANCE
    )
    return (
      status.gripper_status is not None
      and bool(status.gripper_status & protocol.AXIS_STATUS_MOVE_DONE)
      and status.gripper_position is not None
      and status.gripper_position >= gripper_close_threshold
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

  async def _close_gripper(
    self,
    *,
    gripper_closed_position: float = _DEFAULT_GRIPPER_CLOSED_POSITION,
    gripper_close_threshold: float = _DEFAULT_GRIPPER_CLOSE_THRESHOLD,
    speed: int = protocol.SPEED_SLOW,
  ) -> protocol.Access2Status:
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
        gripper_closed_position,
        protocol.PROFILE_DYNAMIC_EMPTY,
        speed,
      ),
      raise_on_error=False,
    )
    status = await self._wait_until_motion_complete(
      (protocol.AXIS_GRIPPER,),
      operation="close gripper",
    )
    if not self._gripper_is_closed(
      status,
      gripper_closed_position=gripper_closed_position,
      gripper_close_threshold=gripper_close_threshold,
    ) or (response.result != 0 and not status.optical_plate_sensor):
      assert status.gripper_position is not None
      assert status.gripper_status is not None
      error = (
        "Access2 did not close the gripper: "
        f"position {status.gripper_position:.3f}, threshold {gripper_close_threshold:.3f}, "
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

  async def park(
    self, *, plate_height: float = 15, z_offset: float = 8, speed: Access2Speed = "slow"
  ) -> None:
    """Move the Access2 arm to its park teachpoint.

    Args:
      plate_height: Plate height sent to the controller, in millimeters.
      z_offset: Z offset at the park teachpoint, in millimeters.
      speed: Controller speed preset: ``slow``, ``medium``, or ``fast``.
    """
    speed_code = _speed_code(speed)
    if not math.isfinite(plate_height) or plate_height <= 0:
      raise ValueError("Plate height must be finite and positive")
    if not math.isfinite(z_offset):
      raise ValueError("Park Z offset must be finite")
    logger.debug("[loader] park")
    async with self._operation_scope(Access2Activity.MOVING) as transition:
      await self._require_ready(operation="park precondition")
      self._state = dataclasses.replace(self._state, last_teachpoint=None)
      transition.mark_actuated(position_uncertain=True)
      await self._move_to_teachpoint(
        protocol.TEACHPOINT_PARK,
        z_offset,
        plate_height,
        profile=protocol.PROFILE_DYNAMIC_FULL,
        speed=speed_code,
      )
      self._state = dataclasses.replace(
        self._state,
        last_teachpoint=protocol.TEACHPOINT_PARK,
      )
      transition.confirm_position()
      await self._require_ready(operation="park postcondition")

  async def close_gripper(
    self,
    *,
    gripper_closed_position: float = _DEFAULT_GRIPPER_CLOSED_POSITION,
    gripper_close_threshold: float = _DEFAULT_GRIPPER_CLOSE_THRESHOLD,
    speed: Access2Speed = "slow",
  ) -> None:
    """Close the gripper with per-call position, contact threshold, and speed.

    Args:
      gripper_closed_position: Absolute gripper-axis target in millimeters.
      gripper_close_threshold: Minimum axis position accepted as contact, in millimeters.
      speed: Controller speed preset: ``slow``, ``medium``, or ``fast``.
    """
    speed_code = _speed_code(speed)
    if not (
      math.isfinite(gripper_closed_position)
      and math.isfinite(gripper_close_threshold)
      and 0 < gripper_close_threshold <= gripper_closed_position
    ):
      raise ValueError("Gripper positions must be finite with 0 < threshold <= closed position")
    logger.debug("[loader] close gripper")
    async with self._operation_scope(Access2Activity.MOVING) as transition:
      status = await self._require_ready(operation="gripper-close precondition")
      if self._gripper_is_closed(
        status,
        gripper_closed_position=gripper_closed_position,
        gripper_close_threshold=gripper_close_threshold,
      ):
        return
      transition.mark_actuated()
      await self._close_gripper(
        gripper_closed_position=gripper_closed_position,
        gripper_close_threshold=gripper_close_threshold,
        speed=speed_code,
      )
      await self._require_ready(operation="gripper-close postcondition")

  async def open_gripper(
    self,
    *,
    gripper_open_position: float = _DEFAULT_GRIPPER_OPEN_POSITION,
    speed: Access2Speed = "slow",
  ) -> None:
    """Open the gripper with per-call position and speed.

    Args:
      gripper_open_position: Absolute gripper-axis target in millimeters.
      speed: Controller speed preset: ``slow``, ``medium``, or ``fast``.
    """
    speed_code = _speed_code(speed)
    if not math.isfinite(gripper_open_position):
      raise ValueError("Gripper open position must be finite")
    logger.debug("[loader] open gripper")
    async with self._operation_scope(Access2Activity.MOVING) as transition:
      status = await self._require_ready(operation="gripper-open precondition")
      if self._gripper_is_at_position(status, gripper_open_position):
        return
      transition.mark_actuated()
      await self._move_axis_to_position(
        protocol.AXIS_GRIPPER,
        gripper_open_position,
        profile=protocol.PROFILE_DYNAMIC_EMPTY,
        speed=speed_code,
      )
      await self._require_ready(operation="gripper-open postcondition")

  async def _transfer(
    self,
    direction: TransferDirection,
    bucket_teachpoint: int,
    *,
    plate_height: float,
    source_z_offset: float,
    destination_z_offset: float,
    park_z_offset: float,
    gripper_open_position: float,
    gripper_closed_position: float,
    gripper_close_threshold: float,
    source_speed: Access2Speed,
    destination_speed: Access2Speed,
    park_speed: Access2Speed,
    gripper_open_speed: Access2Speed,
    gripper_close_speed: Access2Speed,
    gripper_release_speed: Access2Speed,
  ) -> None:
    """Run the load/unload hardware sequence through one stateful path."""
    if not all(
      math.isfinite(value)
      for value in (
        plate_height,
        source_z_offset,
        destination_z_offset,
        park_z_offset,
        gripper_open_position,
        gripper_closed_position,
        gripper_close_threshold,
      )
    ):
      raise ValueError("Transfer parameters must be finite")
    if plate_height <= 0:
      raise ValueError("Plate height must be positive")
    if not gripper_open_position < gripper_close_threshold <= gripper_closed_position:
      raise ValueError(
        "Gripper positions must satisfy open position < close threshold <= closed position"
      )
    source_speed_code = _speed_code(source_speed)
    destination_speed_code = _speed_code(destination_speed)
    park_speed_code = _speed_code(park_speed)
    gripper_open_speed_code = _speed_code(gripper_open_speed)
    gripper_close_speed_code = _speed_code(gripper_close_speed)
    gripper_release_speed_code = _speed_code(gripper_release_speed)
    route = _transfer_route(direction, bucket_teachpoint)
    progress = TransferProgress(
      direction=direction,
      bucket_teachpoint=bucket_teachpoint,
    )
    async with self._operation_scope(progress) as transition:
      await self._require_ready(operation="transfer precondition")
      if self._state.last_teachpoint != protocol.TEACHPOINT_PARK:
        raise RuntimeError("Access2 must be confirmed parked before a transfer")

      transition.mark_actuated()
      await self._move_axis_to_position(
        protocol.AXIS_GRIPPER,
        gripper_open_position,
        profile=protocol.PROFILE_DYNAMIC_EMPTY,
        speed=gripper_open_speed_code,
      )

      self._state = dataclasses.replace(self._state, last_teachpoint=None)
      transition.mark_actuated(position_uncertain=True)
      await self._move_to_teachpoint(
        route.source_teachpoint, source_z_offset, plate_height, speed=source_speed_code
      )
      self._state = dataclasses.replace(
        self._state,
        last_teachpoint=route.source_teachpoint,
      )
      transition.confirm_position()
      self._set_transfer_phase(TransferPhase.AT_SOURCE)

      sensor_values = await self.request_sensor_values()
      if not sensor_values & protocol.STATUS_OPTICAL_PLATE_SENSOR:
        raise RuntimeError(f"no plate found on {route.source_name}")

      self._set_transfer_phase(TransferPhase.GRIPPING)
      transition.mark_actuated()
      await self._close_gripper(
        gripper_closed_position=gripper_closed_position,
        gripper_close_threshold=gripper_close_threshold,
        speed=gripper_close_speed_code,
      )
      self._set_transfer_phase(TransferPhase.HOLDING)

      self._set_transfer_phase(TransferPhase.MOVING_TO_DESTINATION)
      self._state = dataclasses.replace(self._state, last_teachpoint=None)
      transition.mark_actuated(position_uncertain=True)
      await self._move_to_teachpoint(
        route.destination_teachpoint,
        destination_z_offset,
        plate_height,
        profile=protocol.PROFILE_DYNAMIC_FULL,
        speed=destination_speed_code,
      )
      self._state = dataclasses.replace(
        self._state,
        last_teachpoint=route.destination_teachpoint,
      )
      transition.confirm_position()
      self._set_transfer_phase(TransferPhase.AT_DESTINATION)

      self._set_transfer_phase(TransferPhase.RELEASING)
      transition.mark_actuated()
      await self._move_axis_to_position(
        protocol.AXIS_GRIPPER,
        gripper_open_position,
        profile=protocol.PROFILE_DYNAMIC_EMPTY,
        speed=gripper_release_speed_code,
      )

      self._set_transfer_phase(TransferPhase.RETURNING_TO_PARK)
      self._state = dataclasses.replace(self._state, last_teachpoint=None)
      transition.mark_actuated(position_uncertain=True)
      await self._move_to_teachpoint(
        protocol.TEACHPOINT_PARK,
        park_z_offset,
        plate_height,
        speed=park_speed_code,
      )
      self._state = dataclasses.replace(
        self._state,
        last_teachpoint=protocol.TEACHPOINT_PARK,
      )
      transition.confirm_position()
      await self._require_ready(operation="transfer postcondition")

  async def load(
    self,
    bucket_teachpoint: int = protocol.TEACHPOINT_BUCKET_1,
    *,
    plate_height: float = 10,
    source_z_offset: float = 3,
    destination_z_offset: float = 3,
    park_z_offset: float = 3,
    gripper_open_position: float = _DEFAULT_GRIPPER_OPEN_POSITION,
    gripper_closed_position: float = _DEFAULT_GRIPPER_CLOSED_POSITION,
    gripper_close_threshold: float = _DEFAULT_GRIPPER_CLOSE_THRESHOLD,
    source_speed: Access2Speed = "slow",
    destination_speed: Access2Speed = "slow",
    park_speed: Access2Speed = "slow",
    gripper_open_speed: Access2Speed = "fast",
    gripper_close_speed: Access2Speed = "slow",
    gripper_release_speed: Access2Speed = "slow",
  ) -> None:
    """Move a plate from the stage into the selected bucket.

    Args:
      bucket_teachpoint: Controller teachpoint for the target bucket.
      plate_height: Plate height sent to the controller, in millimeters.
      source_z_offset: Z offset at the pickup teachpoint, in millimeters.
      destination_z_offset: Z offset at the placement teachpoint, in millimeters.
      park_z_offset: Z offset when returning to park, in millimeters.
      gripper_open_position: Absolute gripper-axis position when opening, in millimeters.
      gripper_closed_position: Absolute gripper-axis target when closing, in millimeters.
      gripper_close_threshold: Minimum axis position accepted as plate contact, in millimeters.
      source_speed: Approach to the pickup teachpoint; ``slow``, ``medium``, or ``fast``.
      destination_speed: Move carrying the plate to placement; ``slow``, ``medium``, or ``fast``.
      park_speed: Return to park after release; ``slow``, ``medium``, or ``fast``.
      gripper_open_speed: Initial opening before pickup; ``slow``, ``medium``, or ``fast``.
      gripper_close_speed: Closing on the plate; ``slow``, ``medium``, or ``fast``.
      gripper_release_speed: Opening to release the plate; ``slow``, ``medium``, or ``fast``.
    """
    logger.debug("[loader] load")
    await self._transfer(
      TransferDirection.INTO_CENTRIFUGE,
      bucket_teachpoint,
      plate_height=plate_height,
      source_z_offset=source_z_offset,
      destination_z_offset=destination_z_offset,
      park_z_offset=park_z_offset,
      gripper_open_position=gripper_open_position,
      gripper_closed_position=gripper_closed_position,
      gripper_close_threshold=gripper_close_threshold,
      source_speed=source_speed,
      destination_speed=destination_speed,
      park_speed=park_speed,
      gripper_open_speed=gripper_open_speed,
      gripper_close_speed=gripper_close_speed,
      gripper_release_speed=gripper_release_speed,
    )

  async def unload(
    self,
    bucket_teachpoint: int = protocol.TEACHPOINT_BUCKET_1,
    *,
    plate_height: float = 10,
    source_z_offset: float = 3,
    destination_z_offset: float = 3,
    park_z_offset: float = 0,
    gripper_open_position: float = _DEFAULT_GRIPPER_OPEN_POSITION,
    gripper_closed_position: float = _DEFAULT_GRIPPER_CLOSED_POSITION,
    gripper_close_threshold: float = _DEFAULT_GRIPPER_CLOSE_THRESHOLD,
    source_speed: Access2Speed = "slow",
    destination_speed: Access2Speed = "slow",
    park_speed: Access2Speed = "slow",
    gripper_open_speed: Access2Speed = "fast",
    gripper_close_speed: Access2Speed = "slow",
    gripper_release_speed: Access2Speed = "slow",
  ) -> None:
    """Move a plate from the selected bucket onto the stage.

    Args:
      bucket_teachpoint: Controller teachpoint for the target bucket.
      plate_height: Plate height sent to the controller, in millimeters.
      source_z_offset: Z offset at the pickup teachpoint, in millimeters.
      destination_z_offset: Z offset at the placement teachpoint, in millimeters.
      park_z_offset: Z offset when returning to park, in millimeters.
      gripper_open_position: Absolute gripper-axis position when opening, in millimeters.
      gripper_closed_position: Absolute gripper-axis target when closing, in millimeters.
      gripper_close_threshold: Minimum axis position accepted as plate contact, in millimeters.
      source_speed: Approach to the pickup teachpoint; ``slow``, ``medium``, or ``fast``.
      destination_speed: Move carrying the plate to placement; ``slow``, ``medium``, or ``fast``.
      park_speed: Return to park after release; ``slow``, ``medium``, or ``fast``.
      gripper_open_speed: Initial opening before pickup; ``slow``, ``medium``, or ``fast``.
      gripper_close_speed: Closing on the plate; ``slow``, ``medium``, or ``fast``.
      gripper_release_speed: Opening to release the plate; ``slow``, ``medium``, or ``fast``.
    """
    logger.debug("[loader] unload")
    await self._transfer(
      TransferDirection.OUT_OF_CENTRIFUGE,
      bucket_teachpoint,
      plate_height=plate_height,
      source_z_offset=source_z_offset,
      destination_z_offset=destination_z_offset,
      park_z_offset=park_z_offset,
      gripper_open_position=gripper_open_position,
      gripper_closed_position=gripper_closed_position,
      gripper_close_threshold=gripper_close_threshold,
      source_speed=source_speed,
      destination_speed=destination_speed,
      park_speed=park_speed,
      gripper_open_speed=gripper_open_speed,
      gripper_close_speed=gripper_close_speed,
      gripper_release_speed=gripper_release_speed,
    )


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
    """Create an Access2 loader paired with a VSpin centrifuge.

    Args:
      name: Resource name.
      device_id: The libftdi identifier for the loader.
      vspin: Paired VSpin centrifuge.
      size_x: Resource width in millimeters.
      size_y: Resource depth in millimeters.
      size_z: Resource height in millimeters.
    """
    driver = Access2Driver(
      device_id=device_id,
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

  @evented_operation("centrifuge_loader.setup", _loader_lifecycle_event_context)
  async def setup(self) -> None:
    """Connect, initialize, home, and park the loader through its driver."""
    await self.driver.setup()

  @evented_operation("centrifuge_loader.stop", _loader_lifecycle_event_context)
  async def stop(self) -> None:
    """Close the loader transport and invalidate session-scoped driver state."""
    await self.driver.stop()

  def _teachpoint_for_bucket(self, bucket: ResourceHolder) -> int:
    """Map a VSpin bucket resource to its Access2 protocol teachpoint."""
    if bucket is self._vspin.bucket1:
      return protocol.TEACHPOINT_BUCKET_1
    if bucket is self._vspin.bucket2:
      return protocol.TEACHPOINT_BUCKET_2
    raise NotAtBucketError("Unknown VSpin bucket")

  async def _run_driver_transfer(
    self,
    direction: TransferDirection,
    bucket: ResourceHolder,
    *,
    plate_height: float,
    source_z_offset: float,
    destination_z_offset: float,
    park_z_offset: float,
    gripper_open_position: float,
    gripper_closed_position: float,
    gripper_close_threshold: float,
    source_speed: Access2Speed,
    destination_speed: Access2Speed,
    park_speed: Access2Speed,
    gripper_open_speed: Access2Speed,
    gripper_close_speed: Access2Speed,
    gripper_release_speed: Access2Speed,
  ) -> None:
    """Reserve VSpin and ask Access2Driver to perform one physical transfer."""
    if self.driver.state.recovery_required:
      raise RuntimeError("Access2 requires recovery")
    bucket_teachpoint = self._teachpoint_for_bucket(bucket)
    async with self._vspin.reserve_transfer(bucket) as vspin_transition:
      try:
        if direction is TransferDirection.INTO_CENTRIFUGE:
          await self.driver.load(
            bucket_teachpoint,
            plate_height=plate_height,
            source_z_offset=source_z_offset,
            destination_z_offset=destination_z_offset,
            park_z_offset=park_z_offset,
            gripper_open_position=gripper_open_position,
            gripper_closed_position=gripper_closed_position,
            gripper_close_threshold=gripper_close_threshold,
            source_speed=source_speed,
            destination_speed=destination_speed,
            park_speed=park_speed,
            gripper_open_speed=gripper_open_speed,
            gripper_close_speed=gripper_close_speed,
            gripper_release_speed=gripper_release_speed,
          )
        else:
          await self.driver.unload(
            bucket_teachpoint,
            plate_height=plate_height,
            source_z_offset=source_z_offset,
            destination_z_offset=destination_z_offset,
            park_z_offset=park_z_offset,
            gripper_open_position=gripper_open_position,
            gripper_closed_position=gripper_closed_position,
            gripper_close_threshold=gripper_close_threshold,
            source_speed=source_speed,
            destination_speed=destination_speed,
            park_speed=park_speed,
            gripper_open_speed=gripper_open_speed,
            gripper_close_speed=gripper_close_speed,
            gripper_release_speed=gripper_release_speed,
          )
      except BaseException:
        if self.driver.state.recovery_required:
          vspin_transition.mark_actuated()
        raise

  @evented_operation("centrifuge_loader.load", _loader_load_event_context)
  async def load(
    self,
    *,
    plate_height: float = 10,
    source_z_offset: float = 3,
    destination_z_offset: float = 3,
    park_z_offset: float = 3,
    gripper_open_position: float = _DEFAULT_GRIPPER_OPEN_POSITION,
    gripper_closed_position: float = _DEFAULT_GRIPPER_CLOSED_POSITION,
    gripper_close_threshold: float = _DEFAULT_GRIPPER_CLOSE_THRESHOLD,
    source_speed: Access2Speed = "slow",
    destination_speed: Access2Speed = "slow",
    park_speed: Access2Speed = "slow",
    gripper_open_speed: Access2Speed = "fast",
    gripper_close_speed: Access2Speed = "slow",
    gripper_release_speed: Access2Speed = "slow",
  ) -> None:
    """Move the loader's plate into the currently presented VSpin bucket.

    Settings apply only to this transfer; plate dimensions are not inferred from the resource.
    Gripper positions describe axis travel, not plate width or jaw separation.

    Args:
      plate_height: Plate height sent to the controller, in millimeters.
      source_z_offset: Z offset at the pickup teachpoint, in millimeters.
      destination_z_offset: Z offset at the placement teachpoint, in millimeters.
      park_z_offset: Z offset when returning to park, in millimeters.
      gripper_open_position: Absolute gripper-axis position when opening, in millimeters.
      gripper_closed_position: Absolute gripper-axis target when closing, in millimeters.
      gripper_close_threshold: Minimum axis position accepted as plate contact, in millimeters.
      source_speed: Approach to the pickup teachpoint; ``slow``, ``medium``, or ``fast``.
      destination_speed: Move carrying the plate to placement; ``slow``, ``medium``, or ``fast``.
      park_speed: Return to park after release; ``slow``, ``medium``, or ``fast``.
      gripper_open_speed: Initial opening before pickup; ``slow``, ``medium``, or ``fast``.
      gripper_close_speed: Closing on the plate; ``slow``, ``medium``, or ``fast``.
      gripper_release_speed: Opening to release the plate; ``slow``, ``medium``, or ``fast``.
    """
    bucket = self._vspin.at_bucket
    if bucket is None:
      raise NotAtBucketError(
        "Centrifuge must be at a bucket to load a plate. "
        "Use vspin.go_to_bucket1() or vspin.go_to_bucket2()."
      )
    if self.resource is None:
      raise LoaderNoPlateError("Loader must have a plate to load.")
    if bucket.resource is not None:
      raise BucketHasPlateError("Bucket must be empty to load a plate.")

    await self._run_driver_transfer(
      TransferDirection.INTO_CENTRIFUGE,
      bucket,
      plate_height=plate_height,
      source_z_offset=source_z_offset,
      destination_z_offset=destination_z_offset,
      park_z_offset=park_z_offset,
      gripper_open_position=gripper_open_position,
      gripper_closed_position=gripper_closed_position,
      gripper_close_threshold=gripper_close_threshold,
      source_speed=source_speed,
      destination_speed=destination_speed,
      park_speed=park_speed,
      gripper_open_speed=gripper_open_speed,
      gripper_close_speed=gripper_close_speed,
      gripper_release_speed=gripper_release_speed,
    )

    bucket.assign_child_resource(self.resource, location=Coordinate.zero())

  @evented_operation("centrifuge_loader.unload", _loader_unload_event_context)
  async def unload(
    self,
    *,
    plate_height: float = 10,
    source_z_offset: float = 3,
    destination_z_offset: float = 3,
    park_z_offset: float = 0,
    gripper_open_position: float = _DEFAULT_GRIPPER_OPEN_POSITION,
    gripper_closed_position: float = _DEFAULT_GRIPPER_CLOSED_POSITION,
    gripper_close_threshold: float = _DEFAULT_GRIPPER_CLOSE_THRESHOLD,
    source_speed: Access2Speed = "slow",
    destination_speed: Access2Speed = "slow",
    park_speed: Access2Speed = "slow",
    gripper_open_speed: Access2Speed = "fast",
    gripper_close_speed: Access2Speed = "slow",
    gripper_release_speed: Access2Speed = "slow",
  ) -> None:
    """Move the presented VSpin bucket's plate onto the loader.

    Settings apply only to this transfer; plate dimensions are not inferred from the resource.
    Gripper positions describe axis travel, not plate width or jaw separation.

    Args:
      plate_height: Plate height sent to the controller, in millimeters.
      source_z_offset: Z offset at the pickup teachpoint, in millimeters.
      destination_z_offset: Z offset at the placement teachpoint, in millimeters.
      park_z_offset: Z offset when returning to park, in millimeters.
      gripper_open_position: Absolute gripper-axis position when opening, in millimeters.
      gripper_closed_position: Absolute gripper-axis target when closing, in millimeters.
      gripper_close_threshold: Minimum axis position accepted as plate contact, in millimeters.
      source_speed: Approach to the pickup teachpoint; ``slow``, ``medium``, or ``fast``.
      destination_speed: Move carrying the plate to placement; ``slow``, ``medium``, or ``fast``.
      park_speed: Return to park after release; ``slow``, ``medium``, or ``fast``.
      gripper_open_speed: Initial opening before pickup; ``slow``, ``medium``, or ``fast``.
      gripper_close_speed: Closing on the plate; ``slow``, ``medium``, or ``fast``.
      gripper_release_speed: Opening to release the plate; ``slow``, ``medium``, or ``fast``.
    """
    bucket = self._vspin.at_bucket
    if bucket is None:
      raise NotAtBucketError(
        "Centrifuge must be at a bucket to unload a plate. "
        "Use vspin.go_to_bucket1() or vspin.go_to_bucket2()."
      )
    if bucket.resource is None:
      raise BucketNoPlateError("Bucket must have a plate to unload.")

    await self._run_driver_transfer(
      TransferDirection.OUT_OF_CENTRIFUGE,
      bucket,
      plate_height=plate_height,
      source_z_offset=source_z_offset,
      destination_z_offset=destination_z_offset,
      park_z_offset=park_z_offset,
      gripper_open_position=gripper_open_position,
      gripper_closed_position=gripper_closed_position,
      gripper_close_threshold=gripper_close_threshold,
      source_speed=source_speed,
      destination_speed=destination_speed,
      park_speed=park_speed,
      gripper_open_speed=gripper_open_speed,
      gripper_close_speed=gripper_close_speed,
      gripper_release_speed=gripper_release_speed,
    )

    self.assign_child_resource(bucket.resource)
