"""Driver for the Agilent BenchCel 4R microplate handler.

The BenchCel exposes a binary TCP protocol on port 7612 by default. This module
implements the command set reverse-engineered from Agilent VWorks packet
captures and live tests against a BenchCel / 4-stacker system running firmware
3.2.20.0.

This is NOT vendor protocol documentation. The public Agilent Quick Guide
(G5400-90003) documents operation, safety, stacker clamps/shelves, labware
requirements, and diagnostic workflows, but not the Ethernet wire protocol.

Protocol summary
----------------
Every application frame observed so far has the shape::

  [1 byte command_id][2 byte little-endian payload_length][payload]

Host -> BenchCel commands implemented here:

* ``0x47`` home motors. Live tests showed this drops the TCP session while the
  device homes, then accepts connections again when homing is complete.
* ``0x48`` home.
* ``0x60`` / ``0x61`` stacker load / unload. These operate the stacker
  mechanism, not the robot grippers, and are what the VWorks "Load"/"Unload"
  buttons emit (confirmed from captures). ``0x60`` payload is ``01 <idx>``;
  ``0x61`` payload is ``01 <idx> 00 00 00 00``. Their ``0x69`` ACK echoes the
  command id and the stacker index, e.g. ``60 <idx>`` / ``61 <idx>``.
* ``0x62`` / ``0x63`` pick/downstack and place/upstack for stackers or
  teachpoints. Stackers are target IDs ``0x00``..``0x03``. Captures confirm the
  VWorks "Downstack" button emits a single ``0x62`` and "Upstack" a single
  ``0x63``, each with payload ``01 <target> 00 01`` (so ``0x62``/``0x63`` ARE
  the VWorks downstack/upstack tasks when the target is a stacker; with a
  teachpoint target they are a plain pick/place at that taught position).
* ``0x65`` move to stacker/teachpoint.
* ``0x66`` relative jog: axis 0 theta, 1 X, 2 Z, 3 robot gripper.
* ``0x67`` open/close pneumatic stacker grippers/clamps. These are diagnostics
  and can drop plates if used when a stack is unsupported.
* ``0x6a`` full-open/full-close robot grippers.
* ``0x73`` save teachpoint. Captures did not show a command-specific ACK.
* ``0x7e`` stacker sensor query.
* ``0x85`` current-position read. Live tests showed the selector is ignored;
  this is not a stored teachpoint reader.
* ``0x87`` general/arm status query. The decoded float32 fields are theta, X,
  Z, and robot gripper position at offsets 4, 12, 20, and 28.
* ``0x99`` axis bounds query. Live tests decoded this as theta/X/Z/gripper
  min/max limits, not as stored teachpoint data.

Device errors are returned as ``0x02`` frames containing an ASCII message, for
example ``"X position out of bounds"``. Successful motion commands return a
``0x69`` ACK after motion is complete. Plate load/unload ACKs include the
stacker index in addition to the command ID.

Safety
------
Keep the robot and stacker area clear, make sure E-stop/power-off is available,
and ensure VWorks or any other control client is disconnected before using this
driver. The BenchCel appears to allow only one effective control client at a
time; if another client owns the session, connections may be accepted and then
immediately closed.

Manual notes from Agilent G5400-90003A that matter for automation:

* The pendant has a red robot-disable button that cuts power to the motors.
* Compressed air drives the stacker-head mechanisms; air must be on for normal
  operation and for rack install/removal workflows.
* Stacker clamps/grippers hold/release the bottom plate at the rack base. Opening
  clamps can release/drop a plate stack. The manual says clamps normally open and
  close automatically during loading, unloading, downstacking, and stacking; use
  manual open/close only for diagnostics/recovery.
* Stacker shelves temporarily support/level the stack during downstack/upstack.
  Retracting shelves can drop plates. We have not exposed a shelf command because
  the captured command is not yet confidently mapped.
* Labware should be ANSI/SBS-compatible. The BenchCel typically grips plates
  about 5-10 mm above the bottom, between the top of the plate and the skirt.
  Deep lids/flexible skirts can be problematic.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import struct
from typing import Callable, Dict, List, Literal, Optional, Sequence, Tuple, Union

from pylabrobot.events import evented_operation, resource_reference
from pylabrobot.io.socket import Socket
from pylabrobot.resources import Coordinate, Plate, PlateHolder, Resource, ResourceStack, Rotation

from .labware import BenchCelLabwareSettings, resolve_benchcel_labware_settings
from .stacks import benchcel_4r_stacks

logger = logging.getLogger(__name__)

# Stackers use target IDs 0x00..0x03 (the zero-based stacker indexes). Numeric
# teachpoint slots can be written with command 0x73; an undefined teachpoint
# slot may instead send the arm to a home-like position.


class BenchCelProtocolError(RuntimeError):
  """Raised when the BenchCel sends malformed or unexpected protocol data."""


class BenchCelTimeoutError(TimeoutError):
  """Raised when an expected BenchCel frame is not received in time."""


class BenchCelDeviceError(RuntimeError):
  """Raised when the BenchCel returns a ``0x02`` error frame.

  Attributes:
    message: The decoded ASCII error string returned by the BenchCel.
    frame: The raw error frame.
  """

  def __init__(self, message: str, frame: "Frame") -> None:
    super().__init__(message)
    self.message = message
    self.frame = frame


class EmptyStackError(RuntimeError):
  """Raised when a transfer requests a plate from an empty stack."""


class LoadingTrayOccupiedError(RuntimeError):
  """Raised when a transfer would collide with a plate on the loading tray."""


class LoadingTrayEmptyError(RuntimeError):
  """Raised when an upstack operation requires a plate on the loading tray."""


@dataclasses.dataclass(frozen=True)
class Frame:
  """One application-level BenchCel protocol frame."""

  command_id: int
  payload: bytes = b""

  @property
  def length(self) -> int:
    return len(self.payload)

  def to_bytes(self) -> bytes:
    """Serialize frame as ``[cmd][uint16le length][payload]``."""
    return make_frame(self.command_id, self.payload)

  def hex(self) -> str:
    """Return full serialized frame bytes as lowercase hex."""
    return self.to_bytes().hex()

  def __str__(self) -> str:
    return f"Frame(cmd=0x{self.command_id:02x}, len={self.length}, payload={self.payload.hex()})"


@dataclasses.dataclass(frozen=True)
class SensorStatus:
  """Decoded ``0x7e`` stacker sensor/status response.

  Fields are named according to the reverse-engineered interpretation. The four
  notch sensor names A-D are arbitrary labels until the physical sensor positions
  are mapped. ``plate_presence`` is analog-ish; observed empty stackers were
  around 0-1 and stackers with plates around 116-129.
  """

  stacker: int
  stacker_index: int
  constant_08: int
  air_pressure: int
  notch_sensor_a: int
  notch_sensor_b: int
  unknown_a: int
  plate_presence: int
  unknown_b: int
  notch_sensor_c: int
  notch_sensor_d: int
  raw_payload: bytes

  def plate_present(self, threshold: int = 50) -> bool:
    """Return a rough plate-present boolean from the analog presence value."""
    return self.plate_presence >= threshold

  def notch_values(self) -> Tuple[int, int, int, int]:
    """Return the four binary notch sensor fields as currently mapped."""
    return (
      self.notch_sensor_a,
      self.notch_sensor_b,
      self.notch_sensor_c,
      self.notch_sensor_d,
    )


@dataclasses.dataclass(frozen=True)
class BenchCelArmPose:
  """Partially decoded BenchCel arm pose from a ``0x87`` status response.

  The remaining bytes are still unknown and preserved in ``raw_payload``.
  """

  location: Coordinate
  rotation: Rotation
  gripper_position: float
  raw_payload: bytes


@dataclasses.dataclass(frozen=True)
class GeneralStatus:
  """``0x87`` general status response."""

  raw_payload: bytes
  arm_pose: Optional[BenchCelArmPose] = None


@dataclasses.dataclass(frozen=True)
class BenchCelStatusSnapshot:
  """One complete client-side BenchCel status polling cycle."""

  stackers: Dict[int, SensorStatus]
  general: GeneralStatus


@dataclasses.dataclass(frozen=True)
class Teachpoint:
  """Numeric teachpoint data for command ``0x73``.

  The human-readable name is metadata only and is not serialized. It did not
  appear in the VWorks packet captures.
  """

  theta: float
  x: float
  z: float
  approach_height: float
  cavity_depth: float
  gripper_open_limit: float
  respect_approach_height_when_not_holding_plate: bool
  something_above_this_point: bool
  teachpoint_id: int
  name: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class AxisBoundsResponse:
  """Decoded response to command ``0x99`` (axis min/max travel limits)."""

  theta_min: float
  x_min: float
  z_min: float
  gripper_min: float
  theta_max: float
  x_max: float
  z_max: float
  gripper_max: float
  raw_payload: bytes
  float_values: Tuple[float, ...]


@dataclasses.dataclass(frozen=True)
class CurrentPositionResponse:
  """Response to command ``0x85``.

  Live tests showed the selector byte is ignored and the response is the current
  arm position/config payload, not a stored teachpoint reader. Raw bytes are
  preserved because only the status ``0x87`` layout is currently decoded.
  """

  selector: int
  raw_payload: bytes


AxisName = Literal["theta", "x", "z", "gripper"]


# ---------------------------------------------------------------------------
# Low-level protocol helpers
# ---------------------------------------------------------------------------


def make_frame(command_id: int, payload: bytes = b"") -> bytes:
  """Build a BenchCel protocol frame."""
  if not 0 <= command_id <= 0xFF:
    raise ValueError(f"command_id must fit in one byte, got {command_id!r}")
  if len(payload) > 0xFFFF:
    raise ValueError(f"payload too large: {len(payload)} bytes")
  return bytes([command_id]) + len(payload).to_bytes(2, "little") + payload


def parse_frame_from_buffer(buffer: bytearray) -> Optional[Frame]:
  """Parse one complete frame from the front of ``buffer``, if available.

  TCP packet boundaries are not protocol boundaries. This function removes one
  full frame from ``buffer`` only when the complete payload is available.
  """
  if len(buffer) < 3:
    return None
  command_id = buffer[0]
  length = int.from_bytes(buffer[1:3], "little")
  total = 3 + length
  if len(buffer) < total:
    return None
  payload = bytes(buffer[3:total])
  del buffer[:total]
  return Frame(command_id, payload)


def split_frames(data: bytes) -> List[Frame]:
  """Split a byte string containing one or more complete frames."""
  buffer = bytearray(data)
  frames: List[Frame] = []
  while buffer:
    frame = parse_frame_from_buffer(buffer)
    if frame is None:
      raise BenchCelProtocolError(f"partial/truncated frame data: {bytes(buffer).hex()}")
    frames.append(frame)
  return frames


def _u16le(payload: bytes, offset: int) -> int:
  return int.from_bytes(payload[offset : offset + 2], "little")


def _f32le(payload: bytes, offset: int) -> float:
  return float(struct.unpack("<f", payload[offset : offset + 4])[0])


# Axis IDs for the 0x66 relative jog command.
AXIS_THETA = 0
AXIS_X = 1
AXIS_Z = 2
AXIS_GRIPPER = 3
AXIS_NAMES = {
  AXIS_THETA: "theta",
  AXIS_X: "x",
  AXIS_Z: "z",
  AXIS_GRIPPER: "gripper",
}

# VWorks UI jog presets. The low-level jog API intentionally allows arbitrary
# deltas; these constants are exposed for callers that want UI-like validation.
VWORKS_ROTATION_DEGREES = (0.1, 0.5, 1.0, 5.0, 10.0, 20.0, 45.0, 90.0)
VWORKS_Z_MM = (0.1, 0.5, 1.0, 5.0, 10.0, 20.0, 50.0)
VWORKS_X_MM = (0.1, 0.5, 1.0, 5.0, 10.0, 20.0, 50.0, 100.0)
VWORKS_GRIPPER_UNITS = (0.05, 0.1, 0.5, 1.0, 5.0)


def _stacker_index(stacker: int) -> int:
  """Validate human 1-based stacker number and return zero-based protocol index."""
  if stacker not in (1, 2, 3, 4):
    raise ValueError(f"stacker must be 1, 2, 3, or 4; got {stacker!r}")
  return stacker - 1


def _target_id(target_id: int) -> int:
  if not 0 <= target_id <= 0xFF:
    raise ValueError(f"target_id must fit in one byte, got {target_id!r}")
  return target_id


# Command IDs from VWorks captures/live tests. The device methods construct
# frames directly, using private payload helpers for nontrivial binary layouts.
CMD_ERROR = 0x02
CMD_HOME_MOTORS = 0x47
CMD_HOME = 0x48
CMD_LOAD_PLATE = 0x60
CMD_UNLOAD_PLATE = 0x61
CMD_PICK = 0x62
CMD_PLACE = 0x63
CMD_MOVE_TO_TARGET = 0x65
CMD_JOG = 0x66
CMD_STACKER_GRIPPER = 0x67
CMD_ROBOT_GRIPPER = 0x6A
CMD_ACK = 0x69
CMD_SAVE_TEACHPOINT = 0x73
CMD_SET_LABWARE = 0x7D
CMD_SENSOR_STATUS = 0x7E
CMD_CURRENT_POSITION = 0x85
CMD_GENERAL_STATUS = 0x87
CMD_SETTINGS_COMMIT = 0x9F
CMD_AXIS_BOUNDS = 0x99


def _target_payload(target_id: int) -> bytes:
  """Shared payload for pick/place target commands."""
  return bytes([0x01, _target_id(target_id), 0x00, 0x01])


def _move_to_target_payload(target_id: int, approach_height: float) -> bytes:
  """Payload for command ``0x65`` (move to stacker/teachpoint target)."""
  return struct.pack("<BBff", 0x01, _target_id(target_id), 10.0, float(approach_height))


def _teachpoint_payload(teachpoint: Teachpoint) -> bytes:
  """Payload for command ``0x73`` (save teachpoint)."""
  if not 0 <= teachpoint.teachpoint_id <= 0xFF:
    raise ValueError(f"teachpoint_id must fit in one byte, got {teachpoint.teachpoint_id!r}")
  return struct.pack(
    "<BfffBBfff",
    teachpoint.teachpoint_id,
    teachpoint.theta,
    teachpoint.x,
    teachpoint.z,
    1 if teachpoint.something_above_this_point else 0,
    1 if teachpoint.respect_approach_height_when_not_holding_plate else 0,
    teachpoint.approach_height,
    teachpoint.cavity_depth,
    teachpoint.gripper_open_limit,
  )


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------


def parse_error_frame(frame: Frame) -> str:
  """Parse a ``0x02`` device error frame and return the ASCII message."""
  if frame.command_id != CMD_ERROR:
    raise BenchCelProtocolError(f"not an error frame: {frame}")
  return frame.payload.decode("ascii", errors="replace")


def parse_ack_frame(frame: Frame) -> int:
  """Parse a standard ``0x69`` command completion ACK and return the command id."""
  if frame.command_id != CMD_ACK or len(frame.payload) < 1:
    raise BenchCelProtocolError(f"not an ACK frame: {frame}")
  return frame.payload[0]


def parse_sensor_response(frame: Frame) -> SensorStatus:
  """Parse a ``0x7e`` / 18-byte stacker sensor response."""
  if frame.command_id != CMD_SENSOR_STATUS:
    raise BenchCelProtocolError(f"expected 0x7e sensor frame, got {frame}")
  if len(frame.payload) != 18:
    raise BenchCelProtocolError(
      f"expected 18-byte sensor payload, got {len(frame.payload)}: {frame}"
    )

  p = frame.payload
  stacker_index = p[0]
  if stacker_index not in (0, 1, 2, 3):
    raise BenchCelProtocolError(f"unexpected stacker index in sensor payload: {stacker_index}")

  return SensorStatus(
    stacker=stacker_index + 1,
    stacker_index=stacker_index,
    constant_08=p[1],
    air_pressure=_u16le(p, 2),
    notch_sensor_a=_u16le(p, 4),
    notch_sensor_b=_u16le(p, 6),
    unknown_a=_u16le(p, 8),
    plate_presence=_u16le(p, 10),
    unknown_b=_u16le(p, 12),
    notch_sensor_c=_u16le(p, 14),
    notch_sensor_d=_u16le(p, 16),
    raw_payload=p,
  )


def parse_arm_pose_from_87_payload(payload: bytes) -> BenchCelArmPose:
  """Decode known arm-position fields from a 66-byte ``0x87`` payload."""
  if len(payload) != 66:
    raise BenchCelProtocolError(f"expected 66-byte 0x87 payload, got {len(payload)} bytes")
  return BenchCelArmPose(
    location=Coordinate(x=_f32le(payload, 12), z=_f32le(payload, 20)),
    rotation=Rotation(z=_f32le(payload, 4)),
    gripper_position=_f32le(payload, 28),
    raw_payload=payload,
  )


def parse_general_status_response(frame: Frame) -> GeneralStatus:
  """Parse the ``0x87`` general status response."""
  if frame.command_id != CMD_GENERAL_STATUS:
    raise BenchCelProtocolError(f"expected 0x87 general status frame, got {frame}")
  arm_pose = parse_arm_pose_from_87_payload(frame.payload) if len(frame.payload) == 66 else None
  return GeneralStatus(raw_payload=frame.payload, arm_pose=arm_pose)


def parse_axis_bounds_response(frame: Frame) -> AxisBoundsResponse:
  """Parse ``0x99`` response into per-axis min/max travel limits."""
  if frame.command_id != CMD_AXIS_BOUNDS:
    raise BenchCelProtocolError(f"expected 0x99 axis bounds response, got {frame}")
  if len(frame.payload) != 32:
    raise BenchCelProtocolError(f"expected 32-byte 0x99 payload, got {len(frame.payload)}")
  f = struct.unpack("<8f", frame.payload)
  return AxisBoundsResponse(
    theta_min=f[0],
    x_min=f[1],
    z_min=f[2],
    gripper_min=f[3],
    theta_max=f[4],
    x_max=f[5],
    z_max=f[6],
    gripper_max=f[7],
    raw_payload=frame.payload,
    float_values=f,
  )


def parse_current_position_response(frame: Frame, *, selector: int = 1) -> CurrentPositionResponse:
  """Preserve the observed ``0x85`` response as raw bytes."""
  if frame.command_id != CMD_CURRENT_POSITION:
    raise BenchCelProtocolError(f"expected 0x85 current-position response, got {frame}")
  return CurrentPositionResponse(selector=selector, raw_payload=frame.payload)


def _downstack_event_context(
  benchcel: "BenchCel4R", stack: ResourceStack, *, timeout: float = 30.0
) -> dict:
  plate = stack.get_top_item() if len(stack.children) > 0 else None
  return {
    "device": resource_reference(benchcel),
    "resources": [] if plate is None else [resource_reference(plate)],
    "source": resource_reference(stack),
    "destination": resource_reference(benchcel.loading_tray),
  }


def _upstack_event_context(
  benchcel: "BenchCel4R", stack: ResourceStack, *, timeout: float = 30.0
) -> dict:
  plate = benchcel.loading_tray.resource
  return {
    "device": resource_reference(benchcel),
    "resources": [] if plate is None else [resource_reference(plate)],
    "source": resource_reference(benchcel.loading_tray),
    "destination": resource_reference(stack),
  }


def _stack_transfer_event_context(
  benchcel: "BenchCel4R",
  source: ResourceStack,
  destination: ResourceStack,
  *,
  timeout: float = 30.0,
) -> dict:
  plate = source.get_top_item() if len(source.children) > 0 else None
  return {
    "device": resource_reference(benchcel),
    "resources": [] if plate is None else [resource_reference(plate)],
    "source": resource_reference(source),
    "destination": resource_reference(destination),
  }


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------


DEFAULT_PORT = 7612
NUM_STACKERS = 4


class BenchCel4R(Resource):
  """Agilent BenchCel 4R microplate handler.

  The BenchCel is a sequential ("stacking access") storage device: each of its four stackers is a
  single-ended LIFO stack of plates. The device exposes ``downstack``/``upstack`` transfers and
  tracks the expected plate order in four :class:`~pylabrobot.resources.ResourceStack` objects.

  The four ``ResourceStack`` stacks track the expected plate order/content of each stacker.
  """

  def __init__(
    self,
    name: str,
    host: str,
    *,
    port: int = DEFAULT_PORT,
    timeout: float = 30.0,
    read_poll_timeout: float = 0.25,
    loading_tray_teachpoint_id: Optional[int] = None,
    source_ip: Optional[str] = None,
    stacks: Optional[Sequence[ResourceStack]] = None,
    labware: Optional[Union[Plate, BenchCelLabwareSettings, dict]] = None,
    loading_tray_location: Optional[Coordinate] = None,
    size_x: float = 0.0,
    size_y: float = 0.0,
    size_z: float = 0.0,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = "Agilent BenchCel 4R",
  ):
    """
    Args:
      name: Resource name for the device.
      host: IP address or DNS name of the BenchCel Ethernet interface.
      port: TCP port. Defaults to 7612, as observed in VWorks captures.
      timeout: Default command timeout in seconds.
      read_poll_timeout: Per-read timeout used while assembling framed replies.
      loading_tray_teachpoint_id: Teachpoint target ID used as the transfer
        (loading/unloading) point by :meth:`downstack` and :meth:`upstack`.
        There is no fixed loading position on the
        BenchCel: the transfer point is a teachpoint you taught in VWorks (or
        with :meth:`set_teachpoint`). This is intentionally not defaulted --
        transfers raise unless a teachpoint is configured here, because an
        unset or wrong teachpoint can send the arm to a home-like pose. The
        captured VWorks transfer teachpoint was ``0x1e``, but do not rely on
        that without verifying it on your device.
      source_ip: Optional local/source IP to bind, useful on hosts with multiple
        network interfaces connected to different subnets.
      labware: Optional PLR plate, calculated settings object, or serialized
        settings dict. The device must still be configured with matching VWorks
        labware settings; this value is used for PLR metadata and validation.
      stacks: Optionally provide custom ``ResourceStack`` stacks; defaults to four generic stacks.
      loading_tray_location: Cosmetic ``Coordinate`` of the loading-tray resource (resource tree /
        visualization only; the real transfer position is the teachpoint on the device).
    """

    self.host = host
    self.port = port
    self.timeout = timeout
    self.read_poll_timeout = read_poll_timeout
    self.loading_tray_teachpoint_id = loading_tray_teachpoint_id
    self.source_ip = source_ip
    self.labware_settings = (
      resolve_benchcel_labware_settings(labware) if labware is not None else None
    )
    self.io = Socket(
      human_readable_device_name="Agilent BenchCel 4R",
      host=host,
      port=port,
      read_timeout=read_poll_timeout,
      write_timeout=timeout,
      source_ip=source_ip,
    )
    self._lock = asyncio.Lock()
    self._rx_buffer = bytearray()
    self._stacks: List[ResourceStack] = []

    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=model,
    )

    self.loading_tray = PlateHolder(
      name=f"{name}_tray", size_x=127.76, size_y=85.48, size_z=0, pedestal_size_z=0
    )
    self.assign_child_resource(
      self.loading_tray, location=loading_tray_location or Coordinate.zero()
    )

    self._stacks = (
      list(stacks) if stacks is not None else benchcel_4r_stacks(name_prefix=f"{name}_stacker")
    )
    if len(self._stacks) != NUM_STACKERS:
      raise ValueError(f"BenchCel4R requires exactly {NUM_STACKERS} stacks")
    for stack in self._stacks:
      self.assign_child_resource(stack, location=None)

  async def setup(self) -> None:
    """Open the TCP connection to the BenchCel."""
    logger.info("[benchcel] connecting to %s:%d", self.host, self.port)
    await asyncio.wait_for(self.io.setup(), timeout=self.timeout)
    logger.info("[benchcel] connected to %s:%d", self.host, self.port)

  async def stop(self) -> None:
    """Close the TCP connection. Safe to call even if never set up."""
    await self.io.stop()
    self._rx_buffer.clear()
    logger.info("[benchcel] disconnected from %s:%d", self.host, self.port)

  # ------------------------------------------------------------------ wire IO

  async def _write_frame(self, frame: Frame, *, timeout: Optional[float] = None) -> None:
    data = frame.to_bytes()
    logger.debug("[benchcel] >>> %s raw=%s", frame, data.hex())
    await self.io.write(data, timeout=self.timeout if timeout is None else timeout)

  async def _read_frame(self, *, timeout: Optional[float] = None) -> Frame:
    effective_timeout = self.timeout if timeout is None else timeout
    loop = asyncio.get_running_loop()
    deadline = loop.time() + effective_timeout

    while True:
      frame = parse_frame_from_buffer(self._rx_buffer)
      if frame is not None:
        logger.debug("[benchcel] <<< %s raw=%s", frame, frame.hex())
        return frame

      remaining = deadline - loop.time()
      if remaining <= 0:
        raise BenchCelTimeoutError(f"timed out after {effective_timeout}s waiting for frame")

      try:
        chunk = await self.io.read(4096, timeout=min(self.read_poll_timeout, remaining))
      except TimeoutError:
        continue
      if not chunk:
        raise BenchCelProtocolError("socket closed while waiting for frame")
      logger.debug("[benchcel] <<< chunk %d bytes: %s", len(chunk), chunk.hex())
      self._rx_buffer.extend(chunk)

  async def _read_until(
    self,
    predicate: Callable[[Frame], bool],
    *,
    timeout: Optional[float] = None,
  ) -> Frame:
    effective_timeout = self.timeout if timeout is None else timeout
    loop = asyncio.get_running_loop()
    deadline = loop.time() + effective_timeout
    while True:
      remaining = deadline - loop.time()
      if remaining <= 0:
        raise BenchCelTimeoutError(
          f"timed out after {effective_timeout}s waiting for matching frame"
        )
      frame = await self._read_frame(timeout=remaining)
      if frame.command_id == CMD_ERROR:
        raise BenchCelDeviceError(parse_error_frame(frame), frame)
      if predicate(frame):
        return frame

  async def _wait_for_ack_payload(
    self,
    ack_payload: bytes,
    *,
    timeout: Optional[float] = None,
  ) -> Frame:
    """Wait for an exact ``0x69`` ACK payload or raise on device error."""
    return await self._read_until(
      lambda f: f.command_id == CMD_ACK and f.payload == ack_payload,
      timeout=timeout,
    )

  async def _wait_for_command_ack(
    self,
    command_id: int,
    *,
    timeout: Optional[float] = None,
  ) -> Frame:
    return await self._wait_for_ack_payload(bytes([command_id]), timeout=timeout)

  async def _send_frame_expect_ack_no_lock(
    self,
    frame: Frame,
    *,
    ack_payload: Optional[bytes] = None,
    timeout: Optional[float] = None,
  ) -> Frame:
    await self._write_frame(frame, timeout=timeout)
    if ack_payload is None:
      return await self._wait_for_command_ack(frame.command_id, timeout=timeout)
    return await self._wait_for_ack_payload(ack_payload, timeout=timeout)

  async def _send_frame(
    self,
    frame: Frame,
    *,
    ack_payload: Optional[bytes] = None,
    timeout: Optional[float] = None,
  ) -> Frame:
    """Send one frame and wait for its completion ACK.

    Most motion commands ACK with ``69 01 00 <command_id>``. Plate load/unload
    ACKs include the stacker index too; pass that exact payload via
    ``ack_payload`` for those commands.
    """
    async with self._lock:
      return await self._send_frame_expect_ack_no_lock(
        frame,
        ack_payload=ack_payload,
        timeout=timeout,
      )

  # --------------------------------------------------------------- movements

  async def home(self, *, timeout: float = 90.0) -> None:
    """Home all BenchCel motors and reconnect when the device becomes responsive.

    Live testing showed this command drops the TCP control session while the
    motors home. This method reconnects and polls status until the device
    responds again.
    """
    logger.info("[benchcel] homing motors")
    async with self._lock:
      cmd = Frame(CMD_HOME_MOTORS, b"\x01")
      try:
        await self._write_frame(cmd, timeout=min(self.timeout, 5.0))
      except OSError:
        pass

      try:
        await self._wait_for_command_ack(cmd.command_id, timeout=5.0)
      except (BenchCelProtocolError, BenchCelTimeoutError, OSError, ConnectionError):
        pass

      loop = asyncio.get_running_loop()
      deadline = loop.time() + timeout
      while loop.time() < deadline:
        try:
          await self.io.stop()
        except Exception:  # pragma: no cover - defensive cleanup
          pass
        self._rx_buffer.clear()
        try:
          await asyncio.wait_for(self.io.setup(), timeout=min(self.timeout, 5.0))
          await self._write_frame(Frame(CMD_GENERAL_STATUS), timeout=2.0)
          await self._read_until(lambda f: f.command_id == CMD_GENERAL_STATUS, timeout=2.0)
          logger.info("[benchcel] motors homed")
          return
        except (
          BenchCelProtocolError,
          BenchCelTimeoutError,
          TimeoutError,
          OSError,
          ConnectionError,
          asyncio.TimeoutError,
        ):
          await asyncio.sleep(2.0)
      raise BenchCelTimeoutError(f"home-motors: device not responsive within {timeout}s")

  async def _send_home_command(self, *, timeout: float = 15.0) -> Frame:
    """Send the lower-level ``0x48`` home command and wait for completion."""
    return await self._send_frame(Frame(CMD_HOME, b"\x01"), timeout=timeout)

  async def move_to_stacker(self, stack: ResourceStack, *, timeout: float = 20.0) -> None:
    """Move the arm to one of this BenchCel's configured stacks."""
    stacker = self._stack_to_stacker(stack)
    await self._move_to_target(_stacker_index(stacker), timeout=timeout)
    logger.info("[benchcel] moved arm to stacker %d", stacker)

  async def _move_to_target(
    self,
    target_id: int,
    *,
    approach_height: float = 0.0,
    timeout: float = 20.0,
  ) -> Frame:
    """Move to a one-byte BenchCel target id using command ``0x65``."""
    return await self._send_frame(
      Frame(CMD_MOVE_TO_TARGET, _move_to_target_payload(target_id, approach_height)),
      timeout=timeout,
    )

  async def move_to_teachpoint(
    self,
    teachpoint_id: int,
    *,
    approach_height: float = 20.0,
    timeout: float = 20.0,
  ) -> None:
    """Move to a teachpoint target id using command ``0x65``."""
    await self._move_to_target(
      teachpoint_id,
      approach_height=approach_height,
      timeout=timeout,
    )
    logger.info("[benchcel] moved arm to teachpoint %d", teachpoint_id)

  async def _pick_up_at_target(
    self,
    target_id: int,
    *,
    timeout: float = 30.0,
  ) -> Frame:
    """Pick/downstack a plate from a target id using command ``0x62``."""
    return await self._send_frame(
      Frame(CMD_PICK, _target_payload(target_id)),
      timeout=timeout,
    )

  async def _drop_at_target(
    self,
    target_id: int,
    *,
    timeout: float = 30.0,
  ) -> Frame:
    """Place/upstack a plate at a target id using command ``0x63``."""
    return await self._send_frame(
      Frame(CMD_PLACE, _target_payload(target_id)),
      timeout=timeout,
    )

  async def _pick_up_at_teachpoint(
    self,
    teachpoint_id: int,
    *,
    timeout: float = 30.0,
  ) -> Frame:
    """Pick a plate from a teachpoint target id using command ``0x62``."""
    return await self._pick_up_at_target(teachpoint_id, timeout=timeout)

  async def _drop_at_teachpoint(
    self,
    teachpoint_id: int,
    *,
    timeout: float = 30.0,
  ) -> Frame:
    """Place a plate at a teachpoint target id using command ``0x63``."""
    return await self._drop_at_target(teachpoint_id, timeout=timeout)

  async def _load_stacker(self, stacker: int, *, timeout: float = 30.0) -> Frame:
    """Send the ``0x60`` stacker load command for stacker 1-4.

    Confirmed from VWorks captures: pressing "Load" emits a single ``0x60`` with
    payload ``01 <stacker_index>`` and the device replies ``0x69`` ``60 <index>``.
    This operates the whole-stacker mechanism, not the robot grippers, and is
    distinct from the ``0x62``/``0x63`` per-plate robot pick/place commands.
    """
    stacker_index = _stacker_index(stacker)
    cmd = Frame(CMD_LOAD_PLATE, bytes([0x01, stacker_index]))
    return await self._send_frame(
      cmd,
      ack_payload=bytes([cmd.command_id, stacker_index]),
      timeout=timeout,
    )

  async def _unload_stacker(self, stacker: int, *, timeout: float = 30.0) -> Frame:
    """Send the ``0x61`` stacker unload command for stacker 1-4.

    Confirmed from VWorks captures: pressing "Unload" emits a single ``0x61``
    with payload ``01 <stacker_index> 00 00 00 00`` and the device replies
    ``0x69`` ``61 <index>``. Like :meth:`_load_stacker` this drives the
    whole-stacker mechanism, not the robot grippers.
    """
    stacker_index = _stacker_index(stacker)
    cmd = Frame(CMD_UNLOAD_PLATE, bytes([0x01, stacker_index]) + b"\x00\x00\x00\x00")
    return await self._send_frame(
      cmd,
      ack_payload=bytes([cmd.command_id, stacker_index]),
      timeout=timeout,
    )

  async def open_stacker_clamps(
    self,
    stack: ResourceStack,
    *,
    timeout: float = 15.0,
  ) -> None:
    """Open one stacker's pneumatic clamps using command ``0x67``.

    Caution: this diagnostic command can release/drop a plate stack if it is not
    physically supported.
    """
    stacker = self._stack_to_stacker(stack)
    payload = bytes([_stacker_index(stacker), 0x01])
    await self._send_frame(
      Frame(CMD_STACKER_GRIPPER, payload),
      timeout=timeout,
    )
    logger.warning("[benchcel] opened clamps on stacker %d", stacker)

  async def close_stacker_clamps(
    self,
    stack: ResourceStack,
    *,
    timeout: float = 15.0,
  ) -> None:
    """Close one stacker's pneumatic clamps using command ``0x67``."""
    stacker = self._stack_to_stacker(stack)
    payload = bytes([_stacker_index(stacker), 0x00])
    await self._send_frame(
      Frame(CMD_STACKER_GRIPPER, payload),
      timeout=timeout,
    )
    logger.info("[benchcel] closed clamps on stacker %d", stacker)

  async def _pick_from_stacker(self, stacker: int, *, timeout: float = 30.0) -> Frame:
    """Send the raw one-plate pick command for stacker 1-4."""
    return await self._pick_up_at_target(_stacker_index(stacker), timeout=timeout)

  async def _place_on_stacker(self, stacker: int, *, timeout: float = 30.0) -> Frame:
    """Send the raw one-plate place command for stacker 1-4."""
    return await self._drop_at_target(_stacker_index(stacker), timeout=timeout)

  async def _jog(self, axis: int, delta: float, *, timeout: float = 10.0) -> Frame:
    """Send a relative jog command on one axis and wait for ACK/error."""
    if axis not in AXIS_NAMES:
      raise ValueError(f"axis must be one of {sorted(AXIS_NAMES)}, got {axis!r}")
    return await self._send_frame(
      Frame(CMD_JOG, struct.pack("<Bf", axis, float(delta))),
      timeout=timeout,
    )

  async def _rotate_theta_relative(self, delta_degrees: float, *, timeout: float = 10.0) -> Frame:
    """Relative theta jog. Positive is CCW/left in observed tests."""
    return await self._jog(AXIS_THETA, delta_degrees, timeout=timeout)

  async def _move_x_relative(self, delta_mm: float, *, timeout: float = 10.0) -> Frame:
    """Relative X jog. Positive is right in observed tests."""
    return await self._jog(AXIS_X, delta_mm, timeout=timeout)

  async def _move_z_relative(self, delta_mm: float, *, timeout: float = 10.0) -> Frame:
    """Relative Z jog. Positive is up in observed tests."""
    return await self._jog(AXIS_Z, delta_mm, timeout=timeout)

  async def _move_gripper_relative(
    self,
    delta: float,
    *,
    timeout: float = 10.0,
  ) -> Frame:
    """Relative robot-gripper jog in internal units. Positive closes grippers."""
    return await self._jog(AXIS_GRIPPER, delta, timeout=timeout)

  async def close_robot_gripper(self, *, timeout: float = 10.0) -> None:
    """Fully close the robot gripper."""
    await self._send_frame(Frame(CMD_ROBOT_GRIPPER, b"\x00"), timeout=timeout)
    logger.info("[benchcel] closed robot gripper")

  async def open_robot_gripper(self, *, timeout: float = 10.0) -> None:
    """Fully open the robot gripper."""
    await self._send_frame(Frame(CMD_ROBOT_GRIPPER, b"\x01"), timeout=timeout)
    logger.info("[benchcel] opened robot gripper")

  async def set_teachpoint(
    self,
    teachpoint: Teachpoint,
    *,
    timeout: float = 5.0,
  ) -> None:
    """Write a numeric teachpoint with command ``0x73``.

    Captures did not show a command-specific ACK after ``0x73``, so
    the method returns after writing. The device cannot read teachpoints back;
    keep the numeric teachpoints in your own protocol or configuration.
    """
    cmd = Frame(CMD_SAVE_TEACHPOINT, _teachpoint_payload(teachpoint))
    async with self._lock:
      await self._write_frame(cmd, timeout=timeout)
    logger.info("[benchcel] wrote teachpoint %d", teachpoint.teachpoint_id)

  @evented_operation("benchcel.move_plate_between_stacks", _stack_transfer_event_context)
  async def move_plate_between_stacks(
    self,
    source: ResourceStack,
    destination: ResourceStack,
    *,
    timeout: float = 30.0,
  ) -> Plate:
    """Move the accessible plate between two configured stacks and update PLR state."""
    source_stacker = self._stack_to_stacker(source)
    destination_stacker = self._stack_to_stacker(destination)
    if source is destination:
      raise ValueError("Source and destination stacks must be different")
    if len(source.children) == 0:
      raise EmptyStackError(f"Stack {source.name!r} is empty")
    plate = source.get_top_item()
    if not isinstance(plate, Plate):
      raise TypeError(f"Top item in stack {source.name!r} is not a Plate")

    async with self._lock:
      await self._send_frame_expect_ack_no_lock(
        Frame(CMD_ROBOT_GRIPPER, b"\x01"),
        timeout=timeout,
      )
      await self._send_frame_expect_ack_no_lock(
        Frame(CMD_PICK, _target_payload(_stacker_index(source_stacker))),
        timeout=timeout,
      )
      await self._send_frame_expect_ack_no_lock(
        Frame(CMD_PLACE, _target_payload(_stacker_index(destination_stacker))),
        timeout=timeout,
      )
    plate.unassign()
    destination.assign_child_resource(plate)
    logger.info(
      "[benchcel] moved %s from stacker %d to stacker %d",
      plate.name,
      source_stacker,
      destination_stacker,
    )
    return plate

  # --------------------------------------------------------------- labware config

  async def set_labware(
    self,
    labware: Union[Plate, BenchCelLabwareSettings],
    *,
    timeout: float = 10.0,
  ) -> None:
    """Push labware geometry to the BenchCel using the ``0x7d`` settings command.

    VWorks sends the labware settings as a 77-byte ``0x7d`` frame followed by an
    empty ``0x9f`` commit, which the device echoes back. Invalid geometry (for
    example, gripper hold positions that are too close) is rejected with a
    ``0x02`` error. ``labware`` may be a PLR :class:`~pylabrobot.resources.Plate`
    (settings are calculated from its dimensions), a
    :class:`~pylabrobot.agilent.benchcel.labware.BenchCelLabwareSettings`
    object.

    On success, the resolved settings are stored on ``self.labware_settings``.
    """
    settings = resolve_benchcel_labware_settings(labware)
    payload = settings.to_device_payload()
    async with self._lock:
      await self._write_frame(Frame(CMD_SET_LABWARE, payload), timeout=timeout)
      await self._write_frame(Frame(CMD_SETTINGS_COMMIT), timeout=timeout)
      # The device replies with a 0x9f commit echo. On invalid geometry it first
      # sends a 0x02 error, then still echoes 0x9f; consume through the echo so
      # the stream is not left out of sync, and raise the error afterwards.
      error: Optional[str] = None
      loop = asyncio.get_running_loop()
      deadline = loop.time() + timeout
      while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
          raise BenchCelTimeoutError(f"timed out after {timeout}s waiting for 0x9f settings commit")
        frame = await self._read_frame(timeout=remaining)
        if frame.command_id == CMD_ERROR:
          error = parse_error_frame(frame)
        elif frame.command_id == CMD_SETTINGS_COMMIT:
          break
      if error is not None:
        raise BenchCelDeviceError(error, Frame(CMD_ERROR, error.encode("ascii", "replace")))
    self.labware_settings = settings
    logger.info("[benchcel] configured labware %s", settings.name)

  # --------------------------------------------------------------- status APIs

  async def _request_stacker_sensor_status(
    self, stacker: int, *, timeout: float = 5.0
  ) -> SensorStatus:
    expected_index = _stacker_index(stacker)
    query = Frame(CMD_SENSOR_STATUS, bytes([expected_index]))

    def is_matching_sensor_response(frame: Frame) -> bool:
      return (
        frame.command_id == CMD_SENSOR_STATUS
        and len(frame.payload) == 18
        and frame.payload[0] == expected_index
      )

    async with self._lock:
      await self._write_frame(query, timeout=timeout)
      frame = await self._read_until(is_matching_sensor_response, timeout=timeout)
      return parse_sensor_response(frame)

  async def request_stacker_sensor_status(
    self, stack: ResourceStack, *, timeout: float = 5.0
  ) -> SensorStatus:
    """Return the sensor status for one configured stack."""
    return await self._request_stacker_sensor_status(self._stack_to_stacker(stack), timeout=timeout)

  async def request_all_stacker_sensor_statuses(
    self,
    *,
    timeout_per_stacker: float = 5.0,
  ) -> Dict[int, SensorStatus]:
    """Return sensor status keyed by human stacker number 1-4."""
    sensors: Dict[int, SensorStatus] = {}
    for stacker in (1, 2, 3, 4):
      sensors[stacker] = await self._request_stacker_sensor_status(
        stacker, timeout=timeout_per_stacker
      )
    return sensors

  async def request_status(self, *, timeout: float = 5.0) -> GeneralStatus:
    """Send ``87 00 00`` and return decoded/raw general status."""
    async with self._lock:
      await self._write_frame(Frame(CMD_GENERAL_STATUS), timeout=timeout)
      frame = await self._read_until(
        lambda f: f.command_id == CMD_GENERAL_STATUS,
        timeout=timeout,
      )
      return parse_general_status_response(frame)

  async def request_arm_pose(self, *, timeout: float = 5.0) -> BenchCelArmPose:
    """Return the decoded arm location, rotation, and robot-gripper position."""
    status = await self.request_status(timeout=timeout)
    if status.arm_pose is None:
      raise BenchCelProtocolError(
        f"0x87 response did not contain a decoded 66-byte arm pose: len={len(status.raw_payload)}"
      )
    return status.arm_pose

  async def request_axis_limits(
    self, *, timeout: float = 5.0
  ) -> Dict[AxisName, Tuple[float, float]]:
    """Return per-axis travel limits as ``{axis: (minimum, maximum)}``."""
    async with self._lock:
      await self._write_frame(Frame(CMD_AXIS_BOUNDS), timeout=timeout)
      frame = await self._read_until(lambda f: f.command_id == CMD_AXIS_BOUNDS, timeout=timeout)
    bounds = parse_axis_bounds_response(frame)
    return {
      "theta": (bounds.theta_min, bounds.theta_max),
      "x": (bounds.x_min, bounds.x_max),
      "z": (bounds.z_min, bounds.z_max),
      "gripper": (bounds.gripper_min, bounds.gripper_max),
    }

  async def _request_raw_current_position(
    self,
    selector: int = 1,
    *,
    timeout: float = 5.0,
  ) -> CurrentPositionResponse:
    """Send ``0x85`` query and return the raw response.

    The selector is preserved for diagnostics, but live tests showed it is
    ignored by the device.
    """
    if not 0 <= selector <= 0xFF:
      raise ValueError(f"selector must fit in one byte, got {selector!r}")
    async with self._lock:
      await self._write_frame(Frame(CMD_CURRENT_POSITION, bytes([selector])), timeout=timeout)
      frame = await self._read_until(
        lambda f: f.command_id == CMD_CURRENT_POSITION,
        timeout=timeout,
      )
      return parse_current_position_response(frame, selector=selector)

  async def request_status_snapshot(
    self,
    *,
    timeout_per_response: float = 5.0,
  ) -> BenchCelStatusSnapshot:
    """Return all stacker sensors and the general status from one polling cycle."""
    stackers = await self.request_all_stacker_sensor_statuses(
      timeout_per_stacker=timeout_per_response
    )
    general = await self.request_status(timeout=timeout_per_response)
    return BenchCelStatusSnapshot(stackers=stackers, general=general)

  # -------------------------------------------------------------- stacker API

  @property
  def stacks(self) -> Sequence[ResourceStack]:
    """The four configured stacks, ordered by human stacker number."""
    return tuple(self._stacks)

  def _stack_to_stacker(self, stack: ResourceStack) -> int:
    """Map a configured ``ResourceStack`` to its human stacker number (1-4)."""
    for index, configured_stack in enumerate(self._stacks, start=1):
      if configured_stack is stack:
        return index
    raise ValueError(f"Stack {stack.name!r} is not configured on this BenchCel")

  def _loading_tray_target(self) -> int:
    """Return the configured loading-tray teachpoint target."""
    target = self.loading_tray_teachpoint_id
    if target is None:
      raise ValueError(
        "No BenchCel loading/transfer teachpoint configured. The BenchCel has no "
        "fixed loading position; set loading_tray_teachpoint_id on the device. "
        "Make sure the teachpoint is taught on the device with VWorks or set_teachpoint first."
      )
    if not 0 <= target <= 0xFF:
      raise ValueError(f"teachpoint_id must fit in one byte, got {target!r}")
    return target

  @evented_operation("benchcel.downstack", _downstack_event_context)
  async def downstack(
    self,
    stack: ResourceStack,
    *,
    timeout: float = 30.0,
  ) -> Plate:
    """Move the accessible plate of ``stack`` to the loading teachpoint.

    The BenchCel firmware addresses whole stackers; ``stack`` selects which configured stacker
    (1-4) to downstack from. The device picks from the stacker (``0x62``), places at the transfer
    teachpoint (``0x63``), and updates the resource model after the motion succeeds.
    """
    source_stacker = self._stack_to_stacker(stack)
    destination_target = self._loading_tray_target()
    if len(stack.children) == 0:
      raise EmptyStackError(f"Stack {stack.name!r} is empty")
    plate = stack.get_top_item()
    if not isinstance(plate, Plate):
      raise TypeError(f"Top item in stack {stack.name!r} is not a Plate")
    if self.loading_tray.resource is not None:
      raise LoadingTrayOccupiedError(
        f"Loading tray already holds {self.loading_tray.resource.name!r}"
      )
    async with self._lock:
      await self._send_frame_expect_ack_no_lock(
        Frame(CMD_ROBOT_GRIPPER, b"\x01"),
        timeout=timeout,
      )
      await self._send_frame_expect_ack_no_lock(
        Frame(CMD_PICK, _target_payload(_stacker_index(source_stacker))),
        timeout=timeout,
      )
      await self._send_frame_expect_ack_no_lock(
        Frame(CMD_PLACE, _target_payload(destination_target)),
        timeout=timeout,
      )
    plate.unassign()
    self.loading_tray.assign_child_resource(plate)
    logger.info("[benchcel] downstacked %s from stacker %d", plate.name, source_stacker)
    return plate

  @evented_operation("benchcel.upstack", _upstack_event_context)
  async def upstack(
    self,
    stack: ResourceStack,
    *,
    timeout: float = 30.0,
  ) -> None:
    """Move a plate from the loading teachpoint onto ``stack``.

    The device picks from the transfer teachpoint (``0x62``), places onto the selected stacker
    (``0x63``), and updates the resource model after the motion succeeds.
    """
    source_target = self._loading_tray_target()
    plate = self.loading_tray.resource
    if plate is None:
      raise LoadingTrayEmptyError("BenchCel loading tray is empty")
    if not isinstance(plate, Plate):
      raise TypeError(f"Resource {plate.name!r} on the BenchCel loading tray is not a Plate")
    destination_stacker = self._stack_to_stacker(stack)
    async with self._lock:
      await self._send_frame_expect_ack_no_lock(
        Frame(CMD_ROBOT_GRIPPER, b"\x01"),
        timeout=timeout,
      )
      await self._send_frame_expect_ack_no_lock(
        Frame(CMD_PICK, _target_payload(source_target)),
        timeout=timeout,
      )
      await self._send_frame_expect_ack_no_lock(
        Frame(CMD_PLACE, _target_payload(_stacker_index(destination_stacker))),
        timeout=timeout,
      )
    plate.unassign()
    stack.assign_child_resource(plate)
    logger.info("[benchcel] upstacked %s onto stacker %d", plate.name, destination_stacker)
