"""PAA KX2 arm control, calibrated kinematics, and optional serial barcode reader."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import struct
import sys
import time
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Dict, List, Literal, Optional, Sequence, Tuple, Union

from pylabrobot.io.canopen import CANopen, FrameCallback, finish_io
from pylabrobot.paa.kx2 import kinematics
from pylabrobot.paa.kx2.barcode_reader import KX2BarcodeReader
from pylabrobot.paa.kx2.config import (
  Axis,
  AxisConfig,
  GripperFingerSide,
  GripperParams,
  KX2Config,
  ServoGripperConfig,
)
from pylabrobot.paa.kx2.gripper import KX2Gripper
from pylabrobot.paa.kx2.kinematics import CartesianPose
from pylabrobot.paa.kx2.protocol import (
  _BI_REQUEST_COB_BASE,
  _BI_RESPONSE_COB_BASE,
  _EMCY_COB_BASE,
  _GROUP_NODE_ID,
  _TPDO3_COB_BASE,
  RPDO,
  TPDO,
  CanError,
  COBType,
  EmcyFrame,
  JointMoveDirection,
  MotorMoveParam,
  MotorsMovePlan,
  PDOTransmissionType,
  RPDOMappedObject,
  TPDOMappedObject,
  TPDOTrigger,
  _decode_emcy,
  _ElmoObjectDataType,
  _NodeEmcyState,
  _u32_le,
)
from pylabrobot.resources import Coordinate, Rotation
from pylabrobot.resources.barcode import Barcode

logger = logging.getLogger(__name__)


@dataclass
class _Registers:
  """Exact values to restore after an operation or connection lifecycle."""

  sdo: Dict[Tuple[int, int, int], bytes] = field(default_factory=dict)
  integers: Dict[Tuple[int, str, int], int] = field(default_factory=dict)
  floats: Dict[Tuple[int, str, int], float] = field(default_factory=dict)


@dataclass
class _PDOSettings:
  """A PDO's original communication fields and mapping entries."""

  node: int
  communication: int
  mapping: int
  fields: Dict[int, bytes]
  entries: Dict[int, bytes]
  event_mask: Optional[bytes] = None


class HomeStatus(IntEnum):
  """Jaw calibration state stored in the gripper drive."""

  NotHomed = 0
  Homed = 1
  InitializedWithoutHoming = 2


# Tuple of motion axes — derived from `Axis.is_motion`, kept around because
# iteration sites (setup, halt, freedrive) want a stable ordering.
MOTION_AXES = tuple(a for a in Axis if a.is_motion)


class _MotionLimits(Dict[Axis, tuple]):
  """Pretty-printing dict for `KX2.motion_limits()`. Dict access
  still works (`limits[Axis.Z]` -> `(max_speed, max_accel)`); `__repr__`
  formats it as an aligned ASCII table for both terminals and notebooks.
  """

  def __repr__(self) -> str:
    """Format calibrated motion limits with units for each axis."""
    rows = []
    for ax, (v, a) in self.items():
      unit = "mm" if ax.is_linear else "deg"
      rows.append((ax.name, f"{v:.2f} {unit}/s", f"{a:.2f} {unit}/s^2"))
    headers = ("axis", "max speed", "max acceleration")
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(3)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    out = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    out.extend(fmt.format(*r) for r in rows)
    return "\n".join(out)


# UI[5..10] code -> digital input role.
_DIGITAL_INPUT_NAMES: Dict[int, str] = {
  101: "ProximitySensor",
  102: "TeachButton",
}

# UI[13..16] code -> output role.
_OUTPUT_NAMES: Dict[int, str] = {
  101: "IndicatorLightRed",
  102: "IndicatorLightGreen",
  103: "IndicatorLightBlue",
  104: "IndicatorLight",
  105: "Buzzer",
}


class KX2:
  """PAA KX2 robotic plate handler.

  A single class spanning CAN transport + Elmo drive primitives and the
  robot-level arm/gripper/homing/kinematics logic. Construct it, ``await
  setup()``, drive it, ``await stop()``.
  """

  # IR breakbeam between the gripper fingers, wired to the Z-drive's IO.
  # True = beam interrupted (object present).
  _PROXIMITY_SENSOR_AXIS: Axis = Axis.Z
  _PROXIMITY_SENSOR_INPUT: int = 4

  _PARK_SPEED: float = 80.0
  _PARK_ACCEL: float = 400.0

  # PVT streaming cadence. 8 ms / 125 Hz: small enough that the 16-deep drive
  # buffer holds ~125 ms of motion, long enough that producer scheduling
  # jitter doesn't underflow the queue. Buffered up to PRELOAD frames ahead
  # of the drive's read pointer.
  #
  # If you bump these, check: (a) DT_MS fits UINT8 — 0x60C2:01 is UNSIGNED8;
  # `ipm_set_time_interval` validates this at the wire level. (b) PRELOAD <
  # drive IP buffer depth (16, set by the 24772:2=16 write at setup) —
  # otherwise the (PRELOAD+1)th frame queue_fulls immediately.
  _PVT_DT_MS: int = 8
  _PVT_PRELOAD: int = 8

  _parking_position: Optional[Dict[Axis, float]]

  def _validate_parking_position(self, position: Dict[Axis, float]) -> None:
    """Require finite positioning-axis targets inside discovered travel limits."""
    if not position:
      raise ValueError("parking_position must contain at least one arm axis")
    for axis, value in position.items():
      if not isinstance(axis, Axis) or not axis.is_motion or not math.isfinite(value):
        raise ValueError("parking_position requires motion Axis keys and finite values")
      if self._config is not None:
        cfg = self._config.axes[axis]
        if not cfg.unlimited_travel and not cfg.min_travel <= value <= cfg.max_travel:
          raise ValueError(f"Parking target for {axis.name} is outside its travel")

  @property
  def parking_position(self) -> Optional[Dict[Axis, float]]:
    """An explicit park target in this arm's calibrated joint coordinates."""
    return None if self._parking_position is None else dict(self._parking_position)

  @parking_position.setter
  def parking_position(self, position: Optional[Dict[Axis, float]]) -> None:
    """Store a validated, arm-specific parking target."""
    if position is not None:
      self._validate_parking_position(position)
    self._parking_position = None if position is None else dict(position)

  def __init__(
    self,
    has_rail: bool = False,
    has_servo_gripper: bool = True,
    interface: Optional[str] = None,
    channel: Optional[str] = None,
    bitrate: int = 500000,
    gripper_length: float = 0.0,
    gripper_z_offset: float = 0.0,
    gripper_finger_side: GripperFingerSide = "barcode_reader",
    barcode_port: Optional[str] = None,
    barcode_baudrate: int = KX2BarcodeReader.default_baudrate,
    parking_position: Optional[Dict[Axis, float]] = None,
  ) -> None:
    """
    Args:
      has_rail: True if the arm is mounted on the optional linear rail (axis 5).
      has_servo_gripper: True if a servo-driven plate gripper is on the bus
        (axis 6).
      interface: python-can adapter name. Defaults to SocketCAN on Linux
        and PCAN on other platforms.
      channel: python-can channel; ``None`` lets the interface pick its default.
      bitrate: CAN bitrate in bit/s (default 500 kbit/s).
      gripper_length: distance from the wrist axis to the gripper's *grip
        center* (geometric midpoint of the jaws) in mm. Non-negative; the side
        is encoded in ``gripper_finger_side``, not the sign.
      gripper_z_offset: vertical offset from the wrist plate to the grip center
        in mm. Positive = grip center below the wrist plate.
      gripper_finger_side: which finger is treated as the gripper's "front". The
        world yaw reported by :meth:`request_gripper_pose` (and the yaw accepted
        by :meth:`move_to_location`) points at this finger. Flipping side is a
        180° relabel of which finger is "front" — for the same joints the grip
        center is unchanged and only the reported yaw shifts by 180°.
      barcode_port: optional serial port of the onboard barcode reader. When
        set, :meth:`setup` brings it up and :meth:`scan_barcode` is usable.
      barcode_baudrate: barcode-reader serial baud rate.
    """
    if has_rail or not has_servo_gripper:
      raise NotImplementedError("Only the four-axis KX2 with a servo gripper is supported")
    if not all(math.isfinite(v) for v in (gripper_length, gripper_z_offset)) or gripper_length < 0:
      raise ValueError("Tool offsets must be finite and gripper_length non-negative")
    if gripper_finger_side not in ("barcode_reader", "proximity_sensor"):
      raise ValueError("Unknown gripper_finger_side")
    if interface is None:
      interface = "socketcan" if sys.platform.startswith("linux") else "pcan"
    if channel is None and interface == "socketcan":
      channel = "can0"
    self._interface = interface
    self._channel = channel
    self._bitrate = bitrate

    self.has_rail = has_rail
    self.has_servo_gripper = has_servo_gripper

    self.node_id_list: List[int] = [1, 2, 3, 4]
    if has_rail:
      self.node_id_list.append(5)
    if has_servo_gripper:
      self.node_id_list.append(6)

    # Motion axes = shoulder/Z/elbow/wrist, keyed by CANopen node ID;
    # axis-level names live in the `Axis` enum (config.py).
    self.motion_node_ids: List[int] = [1, 2, 3, 4]

    self.io = CANopen(interface=interface, channel=channel, bitrate=bitrate)
    self._lifecycle_lock = asyncio.Lock()
    self._session_registers = _Registers()
    self._session_pdos: List[_PDOSettings] = []
    self._session_configured = False
    self._session_nmt: Dict[int, int] = {}
    self._session_enabled: Dict[int, bool] = {}
    self._faulted = False
    self._stopping = False
    self._parking_position = None
    self._background_tasks: set[asyncio.Task] = set()
    self._loop: Optional[asyncio.AbstractEventLoop] = None

    # Pending binary-interpreter response futures keyed by
    # (node_id, msg_type, msg_index). Set from the canopen listener thread
    # via loop.call_soon_threadsafe; only the event-loop thread touches
    # this dict directly.
    self._pending_bi: Dict[Tuple[int, str, int], asyncio.Future] = {}

    # Serialize requests on each drive's binary-interpreter channel.
    self._bi_locks: Dict[int, asyncio.Lock] = {}
    self._invalid_bi_keys: set[Tuple[int, str, int]] = set()

    self._ipm_mode: bool = False
    self._ipm_axes: List[int] = []

    # EMCY (CANopen Emergency, COB-ID 0x80 + node_id) state. Subscribed in
    # setup(); fires on the canopen listener thread, marshalled into the
    # asyncio loop via _make_emcy_callback. Per-node `_NodeEmcyState` is
    # the single source of truth (queue counters, sticky fault, last raw
    # frame); `emcy_move_error` and `last_emcy` are derived properties.
    self._emcy: Dict[int, _NodeEmcyState] = {}
    # Tracks which node fired the most recent EMCY frame, so `last_emcy`
    # can resolve to the right node's `last_frame`. Cheaper than scanning
    # the dict on every read.
    self._last_emcy_node_id: Optional[int] = None
    self._emcy_callbacks: List[Callable[[int, EmcyFrame, str, bool], None]] = []

    # StatusWord (0x6041) push-cache from TPDO3, COB-ID 0x380+node_id. The
    # canopen listener thread parses the 2-byte SW out of each TPDO3 frame and
    # marshals (sw, set_event) onto the asyncio loop. _wait_setpoint_ack reads
    # the cache + waits on the event instead of polling 0x6041 via SDO.
    self._statusword: Dict[int, int] = {}
    self._statusword_event: Dict[int, asyncio.Event] = {}

    # --- tooling + arm-level state ---
    # Tooling is user-supplied and known at construction; KX2Config (drive-read
    # calibration) doesn't exist until setup runs.
    self._gripper_params = GripperParams(
      length=float(gripper_length),
      z_offset=float(gripper_z_offset),
      finger_side=gripper_finger_side,
    )
    self._config: Optional[KX2Config] = None
    self.parking_position = parking_position
    self._freedrive_axes: Dict[int, bool] = {}
    # Reentrant motion guard. Two callers staging move setpoints (target/vel/
    # accel SDOs) on the same drives interleave and the drives execute some
    # random mix. Compound ops (pick_up_at_location, find_z_with_proximity_sensor,
    # _home_servo_gripper) hold this for their full duration; the inner motion
    # primitive (motors_move_joint / motors_move_absolute_execute) re-enters via
    # _motion_owner. halt() deliberately skips the lock — emergency-stop must
    # interrupt regardless.
    self._motion_lock = asyncio.Lock()
    self._motion_owner: Optional[asyncio.Task] = None

    # --- optional onboard barcode reader (independent serial device) ---
    # The onboard barcode reader is a serial device on a separate USB-serial
    # port, entirely independent of the CAN bus that drives the motors. Wire it
    # in only when a port is given; the standalone `KX2BarcodeReader` class
    # remains available for users who prefer to manage it as a sibling.
    self._bcr: Optional[KX2BarcodeReader] = None
    if barcode_port is not None:
      self._bcr = KX2BarcodeReader(port=barcode_port, baudrate=barcode_baudrate)

    self._setup_finished = False
    self.gripper = KX2Gripper(self)

  # --- SDO -----------------------------------------------------------------

  async def _can_sdo_upload(self, node_id: int, index: int, sub_index: int) -> bytes:
    """Read the exact object bytes over the node's serialized SDO channel."""
    return await self.io.upload(node_id, index, sub_index)

  # --- drive-read properties -----------------------------------------------

  @property
  def loop(self) -> asyncio.AbstractEventLoop:
    """Event loop captured in setup(). Raises if accessed before setup()."""
    if self._loop is None:
      raise RuntimeError("KX2 event loop not initialized; call setup() first.")
    return self._loop

  async def _send_bi(
    self,
    node_id: int,
    cmd: str,
    cmd_index: int,
    *,
    is_query: bool,
    is_execute: bool,
    is_float: bool,
    value: Union[int, float] = 0,
  ) -> List[Union[int, float]]:
    """Frame + send an 8-byte binary-interpreter request; await one response
    per target node. Each response is decoded to its native type (int or
    float) by :meth:`_dispatch_bi_response`.
    """
    if not self.io.connected:
      raise CanError("binary interpreter called before setup()")

    timeout = 10.0 if cmd.upper() == "SV" else 1.0

    byte0 = ord(cmd[0]) & 0xFF
    byte1 = ord(cmd[-1]) & 0xFF
    byte2 = cmd_index & 0xFF
    byte3 = (cmd_index >> 8) & 0x3F
    if is_query:
      byte3 |= 0x40
    if is_float:
      byte3 |= 0x80

    val_bytes = struct.pack("<f", float(value)) if is_float else struct.pack("<i", int(value))
    payload = bytes([byte0, byte1, byte2, byte3]) + val_bytes
    data_to_send = payload[:4] if is_execute else payload

    targets = list(self.motion_node_ids) if node_id == _GROUP_NODE_ID else [node_id]
    keys = [(nid, cmd, cmd_index) for nid in targets]

    # Each drive has one interpreter request/response channel. Serialize even
    # different commands on that drive; distinct nodes can still run concurrently.
    async with contextlib.AsyncExitStack() as stack:
      for nid in sorted(targets):
        await stack.enter_async_context(self._bi_locks.setdefault(nid, asyncio.Lock()))

      if any(key in self._invalid_bi_keys for key in keys):
        raise CanError("A timed-out CAN reply is ambiguous; reconnect before reusing this command")
      futures: List[asyncio.Future] = []
      for key in keys:
        fut = self.loop.create_future()
        self._pending_bi[key] = fut
        futures.append(fut)

      async def exchange() -> List[Union[int, float]]:
        """Drain the response before allowing another request with the same address."""
        try:
          await self.io.write(_BI_REQUEST_COB_BASE + node_id, data_to_send)
          return await asyncio.wait_for(asyncio.gather(*futures), timeout=timeout)
        except BaseException:
          self._invalid_bi_keys.update(keys)
          self._faulted = True
          raise

      try:
        return await finish_io(asyncio.create_task(exchange()))
      except asyncio.TimeoutError:
        raise CanError(f"Timeout waiting for response to {cmd}[{cmd_index}] from node {node_id}")
      finally:
        # Defensive: clear pending futures on any exit (success, timeout,
        # other exception). Without finally a non-Timeout exception could
        # leave stale futures keyed on (nid, cmd, cmd_index) that the next
        # caller would await indefinitely — the dispatch resolves only the
        # most recent future at a given key.
        for key in keys:
          self._pending_bi.pop(key, None)

  async def _query_int(self, node_id: int, cmd: str, cmd_index: int) -> int:
    """Query an int-typed Elmo parameter. Returns the drive's current value."""
    if node_id == _GROUP_NODE_ID:
      raise CanError("Group queries are not supported")
    resps = await self._send_bi(
      node_id,
      cmd,
      cmd_index,
      is_query=True,
      is_execute=False,
      is_float=False,
    )
    return int(resps[0])

  async def _query_float(self, node_id: int, cmd: str, cmd_index: int) -> float:
    """Query a float-typed Elmo parameter. Returns the drive's current value."""
    if node_id == _GROUP_NODE_ID:
      raise CanError("Group queries are not supported")
    resps = await self._send_bi(
      node_id,
      cmd,
      cmd_index,
      is_query=True,
      is_execute=False,
      is_float=True,
    )
    return float(resps[0])

  async def _snapshot_registers(
    self,
    *,
    sdo: Sequence[Tuple[int, int, int]] = (),
    integers: Sequence[Tuple[int, str, int]] = (),
    floats: Sequence[Tuple[int, str, int]] = (),
  ) -> _Registers:
    """Read all requested settings before the caller changes any of them."""
    saved = _Registers()
    for sdo_key in sdo:
      saved.sdo[sdo_key] = await self._can_sdo_upload(*sdo_key)
    for key in integers:
      saved.integers[key] = await self._query_int(*key)
    for key in floats:
      saved.floats[key] = await self._query_float(*key)
    return saved

  async def _can_sdo_download(
    self, node_id: int, index: int, sub_index: int, data: List[int]
  ) -> None:
    """Write an object and await acknowledgement."""
    await self.io.download(node_id, index, sub_index, bytes(data))

  async def _write(
    self,
    node_id: int,
    cmd: str,
    cmd_index: int,
    value: Union[int, float],
  ) -> None:
    """Write an Elmo parameter. The type of ``value`` selects int vs float
    framing on the wire. The drive echoes the accepted value back, which we
    verify — a mismatch raises :class:`CanError`.
    """
    is_float = isinstance(value, float)
    resps = await self._send_bi(
      node_id,
      cmd,
      cmd_index,
      is_query=False,
      is_execute=False,
      is_float=is_float,
      value=value,
    )
    targets = list(self.motion_node_ids) if node_id == _GROUP_NODE_ID else [node_id]
    for nid, resp in zip(targets, resps):
      if is_float:
        # Elmo stores floats as float32; the echo may drift slightly relative
        # to our float64 input — accept within ~1% ratio.
        exp, act = float(value), float(resp)
        ok = exp == act or (act != 0.0 and 0.99 < exp / act < 1.01)
      else:
        ok = int(resp) == int(value)
      if not ok:
        raise CanError(
          f"Unexpected CAN response: sent {cmd}[{cmd_index}]={value}, got {resp} from node {nid}"
        )

  async def _restore_registers(self, saved: _Registers) -> None:
    """Restore exact values, trying all writes even when a prior write fails."""
    errors: List[BaseException] = []
    for key, value in reversed(tuple(saved.integers.items())):
      try:
        await self._write(*key, value)
      except BaseException as exc:
        errors.append(exc)
    # CL must stay below PL throughout restoration, including a partially failed close.
    currents = {nid for nid, cmd, index in saved.floats if cmd in ("CL", "PL")}
    for nid in currents:
      try:
        cl, pl = saved.floats[(nid, "CL", 1)], saved.floats[(nid, "PL", 1)]
        current_cl = await self._query_float(nid, "CL", 1)
        await self._write(nid, "PL", 1, max(pl, cl, current_cl))
        await self._write(nid, "CL", 1, cl)
        await self._write(nid, "PL", 1, pl)
      except BaseException as exc:
        errors.append(exc)
    for key, float_value in saved.floats.items():
      if key[1] in ("CL", "PL"):
        continue
      try:
        await self._write(*key, float_value)
      except BaseException as exc:
        errors.append(exc)
    # Interpolation type determines the permitted record size (60C4:5).
    sdo_items = sorted(saved.sdo.items(), key=lambda item: item[0][1] != 0x60C0)
    for sdo_key, data in sdo_items:
      try:
        actual = await self._can_sdo_upload(*sdo_key)
        # Zero is an unselectable power-on state. Preserve it while untouched;
        # after motion, leave the drive in idle profile-position mode.
        if sdo_key[1:] == (0x6060, 0) and data == b"\x00" and actual != data:
          data = b"\x01"
        if actual != data:
          await self._can_sdo_download(*sdo_key, list(data))
          restored = await self._can_sdo_upload(*sdo_key)
          if restored != data:
            node, index, sub = sdo_key
            raise CanError(
              f"Node {node} did not restore 0x{index:04X}:{sub}: "
              f"expected {data.hex()}, read {restored.hex()}"
            )
      except BaseException as exc:
        errors.append(exc)
    if errors:
      self._faulted = True
      logger.error("KX2 could not restore register settings: %s", errors)
      raise errors[0]

  @asynccontextmanager
  async def _preserve_settings(
    self,
    *,
    sdo: Sequence[Tuple[int, int, int]] = (),
    integers: Sequence[Tuple[int, str, int]] = (),
    floats: Sequence[Tuple[int, str, int]] = (),
  ) -> AsyncIterator[None]:
    """Restore temporary settings on success, failure, and cancellation."""
    saved = await self._snapshot_registers(sdo=sdo, integers=integers, floats=floats)
    try:
      yield
    finally:
      await finish_io(asyncio.ensure_future(self._restore_registers(saved)))

  async def _snapshot_pdo(self, node: int, number: int, *, transmit: bool) -> _PDOSettings:
    """Read communication fields and all mapping entries that setup can overwrite."""
    communication = (0x1800 if transmit else 0x1400) + number - 1
    mapping = (0x1A00 if transmit else 0x1600) + number - 1
    fields = {}
    for sub in (1, 2, 3, 5) if transmit else (1, 2):
      fields[sub] = await self._can_sdo_upload(node, communication, sub)
    count = await self._can_sdo_upload(node, mapping, 0)
    entries = {0: count}
    for sub in range(
      1, max(int.from_bytes(count, "little"), 2 if number == 3 and not transmit else 1) + 1
    ):
      entries[sub] = await self._can_sdo_upload(node, mapping, sub)
    event_mask = await self._can_sdo_upload(node, 0x2F20, number) if transmit else None
    return _PDOSettings(node, communication, mapping, fields, entries, event_mask)

  def _has_session_pdo(self, node: int, number: int, *, transmit: bool) -> bool:
    """Whether setup owns a reversible mapping for this PDO."""
    communication = (0x1800 if transmit else 0x1400) + number - 1
    return any(
      pdo.node == node and pdo.communication == communication for pdo in self._session_pdos
    )

  async def _save_restorable_pdo(self, node: int, number: int, *, transmit: bool) -> None:
    """Save active PDO configuration and writable descriptor contents."""
    saved = await self._snapshot_pdo(node, number, transmit=transmit)
    self._session_pdos.append(saved)

  async def _restore_pdo(self, saved: _PDOSettings) -> None:
    """Disable a PDO, restore its mapping and timing, then restore its original COB-ID."""
    cob = int.from_bytes(saved.fields[1], "little")
    await self._can_sdo_download(saved.node, saved.communication, 1, _u32_le(cob | 0x80000000))
    await self._can_sdo_download(saved.node, saved.mapping, 0, [0])
    for sub, data in saved.entries.items():
      # Unused zero descriptors are not writable on Gold firmware. Restoring
      # the original mapping count keeps these descriptors inactive.
      if sub and any(data):
        await self._can_sdo_download(saved.node, saved.mapping, sub, list(data))
    await self._can_sdo_download(saved.node, saved.mapping, 0, list(saved.entries[0]))
    for sub, data in saved.fields.items():
      if sub != 1:
        await self._can_sdo_download(saved.node, saved.communication, sub, list(data))
    if saved.event_mask is not None:
      await self._can_sdo_download(
        saved.node, 0x2F20, saved.communication - 0x1800 + 1, list(saved.event_mask)
      )
    await self._can_sdo_download(saved.node, saved.communication, 1, list(saved.fields[1]))

  async def _execute(self, node_id: int, cmd: str, cmd_index: int = 0) -> None:
    """Fire-and-forget execute (e.g. ``BG``). Awaits the drive's response so
    the caller sees the command completed on the wire, but no echo-check."""
    await self._send_bi(
      node_id,
      cmd,
      cmd_index,
      is_query=False,
      is_execute=True,
      is_float=False,
    )

  async def _motor_get_motion_status(self, node_id: int) -> int:
    """Read the drive motion-status code."""
    return await self._query_int(node_id, "MS", 0)

  async def _stop_axes(self, axes: List[int]) -> None:
    """Stop without dropping a held load; confirm each axis has ceased motion."""
    errors: List[BaseException] = []
    for axis in axes:
      try:
        await self._execute(axis, "ST")
      except BaseException as exc:
        errors.append(exc)
      try:
        # A disabled drive may retain an enabled controlword, so do not replay it.
        if await self.request_axis_enabled(axis):
          controlword = int.from_bytes(await self._can_sdo_upload(axis, 0x6040, 0), "little")
          await self._control_word_set(axis, (controlword | 0x100) & ~0x10)
      except BaseException as exc:
        errors.append(exc)
    deadline = time.monotonic() + 3.0
    for axis in axes:
      try:
        reference: Optional[int] = None
        settled_since: Optional[float] = None
        while await self._motor_get_motion_status(axis) not in (0, 1, 3):
          # A reverse-limit stop can leave MS=2 latched. Require a stationary
          # position reference, zero profiler velocity and matching feedback.
          desired = await self._query_int(axis, "DV", 3)
          velocity = await self._query_int(axis, "DV", 5)
          actual = await self._query_int(axis, "PX", 0)
          tolerance = self._encoder_tolerance(Axis(axis)) if self._config is not None else 1
          if velocity == 0 and abs(actual - desired) <= tolerance:
            if desired != reference or settled_since is None:
              reference, settled_since = desired, time.monotonic()
            elif time.monotonic() - settled_since >= 0.15:
              break
          else:
            reference, settled_since = None, None
          if time.monotonic() >= deadline:
            raise CanError(f"Axis {axis} did not stop")
          await asyncio.sleep(0.02)
      except BaseException as exc:
        errors.append(exc)
    if errors:
      self._faulted = True
      raise errors[0]

  @property
  def _cfg(self) -> KX2Config:
    """Return discovered calibration or reject use before setup."""
    if self._config is None:
      raise RuntimeError("KX2 not set up — call setup() first")
    return self._config

  @property
  def configuration(self) -> KX2Config:
    """Calibration and limits read from this arm during setup."""
    return self._cfg

  @property
  def barcode_reader(self) -> Optional[KX2BarcodeReader]:
    """The independent serial reader, if fitted."""
    return self._bcr

  async def _can_sdo_download_elmo_object(
    self,
    node_id: int,
    elmo_object_int: int,
    sub_index: int,
    data: Union[int, float],
    data_type: _ElmoObjectDataType,
  ) -> None:
    # Byte width + signedness derived from data_type; float inputs truncate to int.
    """Encode a vendor object value using its declared wire width."""
    _SDO_ELMO_PACK = {
      _ElmoObjectDataType.UNSIGNED8: (1, False),
      _ElmoObjectDataType.UNSIGNED16: (2, False),
      _ElmoObjectDataType.UNSIGNED32: (4, False),
      _ElmoObjectDataType.UNSIGNED64: (8, False),
      _ElmoObjectDataType.INTEGER8: (1, True),
      _ElmoObjectDataType.INTEGER16: (2, True),
      _ElmoObjectDataType.INTEGER32: (4, True),
      _ElmoObjectDataType.INTEGER64: (8, True),
    }
    spec = _SDO_ELMO_PACK.get(data_type)
    if spec is None:
      raise CanError(f"Unsupported data type for SDO Write: {data_type.name}")
    width, signed = spec
    data_bytes = list(int(data).to_bytes(width, "little", signed=signed))
    await self._can_sdo_download(node_id, elmo_object_int, sub_index, data_bytes)

  async def _motor_set_move_direction(self, node_id: int, direction: JointMoveDirection) -> None:
    # Elmo modulo mode register: bit0 enables modulo; bits6..7 encode the
    # direction (0=Normal, 1=CW, 2=CCW, 3=Shortest). Packs to 1 + 64*direction
    # = 1/65/129/193.
    """Set modulo positioning direction for a rotary drive."""
    val = 1 + 64 * int(direction)
    await self._can_sdo_download_elmo_object(node_id, 24818, 0, val, _ElmoObjectDataType.UNSIGNED16)

  # --- motion primitives --------------------------------------------------

  async def _set_op_mode(self, node_id: int, mode: int, timeout_s: float = 0.05) -> None:
    """Write 0x6060 (modes_of_operation) and poll 0x6061 (modes_of_operation_display)
    until the drive acknowledges. CiA 402 §6.2: 0x6060 is the request, 0x6061 is
    the actual mode — issuing a move (0x607A or 0x60C1 write) before the drive
    flips reads the actual mode races the mode change. Drives typically ack in
    <5 ms; timeout is generous so a busy bus doesn't false-fail.

    See https://www.stober.jp/manual/manual-commissioning-instruction-cia402-443080-01-en.pdf
    for a CiA 402 commissioning reference (object table, mode codes, state machine).
    """
    await self._can_sdo_download(node_id, 0x6060, 0x00, [mode])
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
      raw = await self._can_sdo_upload(node_id, 0x6061, 0x00)
      actual = struct.unpack("<b", raw[:1])[0] if raw else None  # INTEGER8
      if actual == mode:
        return
      if asyncio.get_event_loop().time() >= deadline:
        raise CanError(
          f"node {node_id}: 0x6061 modes_of_operation_display = {actual}, "
          f"expected {mode} after {timeout_s * 1000:.0f}ms — drive didn't ack mode change"
        )
      await asyncio.sleep(0.005)

  @property
  def emcy_move_error(self) -> Optional[str]:
    """First pending fault across all nodes; ``None`` if no fault.

    Pre-formatted with axis context (``"Axis {nid} {description}"``).
    `motor_check_if_move_done` raises on this; recovery paths clear it
    via `clear_emcy_state`.
    """
    for st in self._emcy.values():
      if st.move_error is not None:
        return st.move_error
    return None

  async def request_axis_fault(self, node_id: int) -> Optional[str]:
    """Read and decode the drive motor-fault register."""
    val = await self._query_int(node_id, "MF", 0)
    if val == 0:
      return None
    # Elmo MF register: most faults are independent single bits. Bits 13/14/15
    # are different — they form a 3-bit selector (b15<<2 | b14<<1 | b13) where
    # only four combinations are real faults; the rest mean nothing.
    bit_msgs = {
      0x0001: "Motor Hall sensor feedback angle not found yet.",
      0x0004: "Feedback loss: no match between encoder and Hall location.",
      0x0008: "The peak current has been exceeded.",
      0x0010: "Inhibit.",
      0x0040: "Two digital Hall sensors were changed at the same time.",
      0x0080: "Speed tracking error.",
      0x0100: "Position tracking error.",
      0x0200: "Inconsistent drive database.",
      0x0400: "Too large a difference in ECAM table.",
      0x0800: "CAN heartbeat failure.",
      0x1000: "Servo drive fault.",
      0x010000: "Failed to find the electrical zero of the motor during startup.",
      0x020000: "Speed limit exceeded.",
      0x040000: "Drive CPU stack overflow.",
      0x080000: "Drive CPU exception.",
      0x200000: "Motor stuck.",
      0x400000: "Position limit exceeded.",
      0x20000000: "Cannot start motor.",
    }
    triplet_msgs = {
      0b001: "Power supply under voltage.",  # b13 only
      0b010: "Power supply over voltage.",  # b14 only
      0b101: "Motor lead short circuit or faulty drive.",  # b13 + b15
      0b110: "Drive overheated.",  # b14 + b15
    }
    faults = [msg for bit, msg in bit_msgs.items() if val & bit]
    triplet = (val >> 13) & 0b111
    if triplet in triplet_msgs:
      faults.append(triplet_msgs[triplet])
    if not faults:
      return f"Unknown fault code: {val} (0x{val:08X})"
    return "  ".join(faults)

  async def _motor_check_if_move_done(self, node_id: int) -> bool:
    # E-stop and some fault paths leave MS pinned at 2 ("stopping in
    # progress") indefinitely, so gating fault-surfacing on ms==1 misses
    # them — the poll loop times out before ever consulting EMCY state.
    # Check sticky EMCY first so any fatal frame raises on the next poll
    # iteration regardless of MS.
    """Check completion while surfacing pending motor faults."""
    pending = self.emcy_move_error
    if pending is not None:
      raise RuntimeError(f"Motor Fault: {pending}")
    ms_val = await self._query_int(node_id, "MS", 0)
    if ms_val == 0:
      return True
    if ms_val in (1, 3):
      mo_val = await self._query_int(node_id, "MO", 0)
      if mo_val == 1:
        # MS=1 means the profiler stopped; MS=0 also confirms feedback settled.
        return False
      fault = await self.request_axis_fault(node_id)
      if fault is not None:
        raise RuntimeError(f"Motor Fault: Axis {node_id} {fault}")
      raise RuntimeError(f"Motor Fault: Axis {node_id} (Unknown)")
    return False

  async def _wait_for_moves_done(self, node_ids: List[int], timeout: float) -> None:
    # Poll MS every 30ms after a 50ms warm-up. The warm-up avoids reading
    # MS=0 in the window between CW=63 and motion actually starting.
    """Poll axis completion and propagate drive faults."""
    assert self._loop is not None
    loop = self._loop

    async def _poll_axis(nid: int) -> None:
      """Poll a single axis until completion or its deadline."""
      deadline = loop.time() + timeout
      await asyncio.sleep(0.05)
      while loop.time() < deadline:
        try:
          if await self._motor_check_if_move_done(int(nid)):
            return
        except CanError as e:
          if self._faulted:
            raise
          # Transient bus error — keep polling. Visible at DEBUG so a wedged
          # bus shows up in logs instead of just burning the full timeout.
          logger.debug("wait_for_moves_done node %d: transient CAN error: %s", nid, e)
        await asyncio.sleep(0.03)
      # Final authoritative check; propagates CanError / motor-fault.
      if not await self._motor_check_if_move_done(int(nid)):
        raise CanError(f"Node {nid} move did not complete within {timeout}s")

    await asyncio.gather(*(_poll_axis(n) for n in node_ids))

  async def request_axis_enabled(self, node_id: int) -> bool:
    """Return True if the motor is energized (Elmo MO=1).

    Faulted drives report MO=0 — use motor_get_fault to distinguish a
    plain disable from a fault.
    """
    return await self._query_int(node_id, "MO", 0) == 1

  # --- raw CANopen sends (SYNC + RPDO1 controlword) -----------------------

  async def _can_sync(self) -> None:
    """Send the synchronous PDO latch frame."""
    if not self.io.connected:
      raise CanError("_can_sync called before setup()")
    await self.io.write(0x80, b"")

  async def _control_word_set(self, node_id: int, value: int, sync: bool = True) -> None:
    """Write the controlword through the mapped RPDO, or SDO for the gripper."""
    payload = value.to_bytes(2, byteorder="little")
    if not self._has_session_pdo(node_id, 1, transmit=False):
      self._statusword.pop(node_id, None)
      await self.io.download(node_id, 0x6040, 0, payload)
      return
    await self.io.write(0x200 + node_id, payload)
    if sync:
      await self._can_sync()

  async def _wait_sw_bit(
    self,
    node_id: int,
    *,
    bit_mask: int,
    want_high: bool,
    timeout_s: float = 0.05,
  ) -> bool:
    """Wait until ``self._statusword[node_id] & bit_mask`` matches ``want_high``.

    TPDO3 maps StatusWord (0x6041) with the StatusWordEvent trigger; the
    canopen listener thread parses each frame into self._statusword[node_id]
    and signals self._statusword_event[node_id]. We wait on the event, with
    a 5 ms grace before falling back to an SDO probe — covers the case
    where the drive's event-trigger config didn't take and TPDO3 is silent.
    Returns True on a match, False on timeout.
    """
    assert self._loop is not None
    ev = self._statusword_event.get(node_id)
    deadline = self._loop.time() + timeout_s
    while self._loop.time() < deadline:
      sw = self._statusword.get(node_id)
      if sw is not None and bool(sw & bit_mask) == want_high:
        return True
      if ev is None:
        # No subscription (drive outside motion_node_ids); SDO poll only.
        raw = await self._can_sdo_upload(node_id, 0x6041, 0x00)
        sw = int.from_bytes(raw[:2], "little")
        self._statusword[node_id] = sw
        if bool(sw & bit_mask) == want_high:
          return True
        await asyncio.sleep(0.001)
        continue
      ev.clear()
      try:
        remaining = max(0.0, deadline - self._loop.time())
        await asyncio.wait_for(ev.wait(), timeout=min(remaining, 0.005))
      except asyncio.TimeoutError:
        # TPDO3 didn't fire within 5 ms — probe via SDO and update the
        # cache so subsequent waits start from the latest known SW.
        raw = await self._can_sdo_upload(node_id, 0x6041, 0x00)
        sw = int.from_bytes(raw[:2], "little")
        self._statusword[node_id] = sw
    return False

  # ======================================================================
  # Robot-level logic: homing, gripper, kinematics, motion
  # ======================================================================

  # -- robot-level homing / estop ---------------------------------------

  async def request_estop(self) -> bool:
    """Return True if the arm is in estop, False otherwise.

    Reads the shoulder drive's SR (status register) via the binary
    interpreter. Bits 14/15 encode the stop/safety state.
    """
    r = await self._query_int(Axis.SHOULDER, "SR", 0)
    return (r & 0x4000) == 0 or (r & 0x8000) == 0

  async def _motor_enable(self, node_id: int, state: bool, *, use_ds402: bool) -> None:
    """Enable or disable a single drive.

    - ``use_ds402=True``: DS402 controlword sequence over RPDO1 (Fault ->
      Shutdown -> Switched On -> Op Enabled on enable; reverse on disable).
      Used for the four motion axes (shoulder/Z/elbow/wrist).
    - ``use_ds402=False``: vendor binary-interpreter ``MO=1/0``. Used for the
      rail and the servo gripper.

    Caller picks the path; the driver does not know about robot topology.

    Drives sometimes need several seconds after a fault / power-rail bounce
    before they accept enable, and disable can lag past a single 100 ms
    settle for the same reason — the retry budget covers both directions
    so a slow drive doesn't leave the arm half-enabled mid-freedrive.
    """
    if state:
      fault = await self.request_axis_fault(node_id)
      if self._faulted or self.emcy_move_error is not None or fault is not None:
        raise CanError(
          f"Cannot enable node {node_id} while faulted: {fault or self.emcy_move_error}"
        )
      if await self.request_estop():
        raise CanError("Cannot enable a motor while e-stop is active")
      if await self.request_axis_enabled(node_id):
        return

    want = 1 if state else 0
    max_attempts = 20
    inter_attempt_sleep_s = 0.5
    for attempt in range(1, max_attempts + 1):
      if not use_ds402:
        await self._write(node_id, "MO", 0, want)
      elif state:
        # Move through the enable states, confirming each statusword transition.
        await self._control_word_set(node_id=node_id, value=0x00)  # clear bits
        await self._control_word_set(node_id=node_id, value=0x06)  # Shutdown
        await self._wait_sw_bit(node_id, bit_mask=1 << 0, want_high=True)
        await self._control_word_set(node_id=node_id, value=0x07)  # Switch on
        await self._wait_sw_bit(node_id, bit_mask=1 << 1, want_high=True)
        await self._control_word_set(node_id=node_id, value=0x0F)  # Enable op
        # Bit 2 (Operation enabled) is confirmed by the MO query below.
      else:
        # DS402 disable: Op Enabled -> Switched On -> Ready to Switch On.
        await self._control_word_set(node_id=node_id, value=7)
        await self._control_word_set(node_id=node_id, value=6)
      await asyncio.sleep(0.1)
      mo = await self._query_int(node_id, "MO", 0)
      if mo == want:
        return
      logger.warning(
        "motor_enable(state=%s) attempt %d/%d failed for node %d (MO=%s); retrying",
        state,
        attempt,
        max_attempts,
        node_id,
        mo,
      )
      await asyncio.sleep(inter_attempt_sleep_s)
    verb = "enable" if state else "disable"
    raise CanError(f"Motor failed to {verb} (node_id = {node_id}) after {max_attempts} attempts")

  async def _motors_ensure_enabled(
    self,
    node_ids: List[int],
    *,
    use_ds402: bool = True,
  ) -> None:
    """Enable every drive in ``node_ids`` that isn't already enabled.

    One ``motor_is_enabled`` SDO read per axis (~5 ms); only drives reading
    MO=0 pay the full DS402 enable cycle. Per-axis work runs concurrently —
    each node ID has its own SDO channel so they don't serialize on the bus.

    The cheap path (drive already enabled) is the common case after the
    first move; the slow path covers post-halt, post-fault, post-freedrive
    where the drive deliberately landed in Switch On Disabled. Used by
    every motion-trigger site (PPM, IPM begin) and lifecycle transitions
    (setup, stop_freedrive_mode, find_z preflight) so they all share one
    recovery contract.
    """
    # Sequential, not gather: motor_enable's DS402 ladder writes intermediate
    # CW values (0x06/0x07/0x0F) interleaved with SW reads. If one axis
    # raises mid-cycle, gather cancels the others mid-sequence — leaving
    # them in an indeterminate state (e.g. 0x07 written but 0x0F never sent)
    # that the per-call retry budget can't reliably recover from. Sequential
    # finishes one axis fully before touching the next.
    if self._faulted or self._stopping:
      raise CanError("KX2 stopped or faulted; reconnect before commanding motion")
    for nid in node_ids:
      nid_int = int(nid)
      if await self.request_axis_enabled(nid_int):
        continue
      logger.warning("node %d: re-enabling (was disabled)", nid_int)
      await self._motor_enable(
        node_id=nid_int, state=True, use_ds402=use_ds402 and nid_int in self.motion_node_ids
      )

  async def _wait_setpoint_ack(
    self, node_id: int, *, want_high: bool, timeout: float = 0.05
  ) -> bool:
    """Wait until 0x6041 bit 12 (setpoint_ack) matches ``want_high``.

    Thin specialization of :meth:`_wait_sw_bit` for the PPM trigger
    handshake. 50 ms total is plenty: bit 12 flips within a servo cycle
    (~1-2 ms) once the drive sees the CW bit-4 edge.
    """
    return await self._wait_sw_bit(
      node_id, bit_mask=1 << 12, want_high=want_high, timeout_s=timeout
    )

  async def _trigger_new_setpoint(
    self,
    node_id: int,
    cw_low: int,
    cw_high: int,
    *,
    max_attempts: int = 10,
  ) -> None:
    """Run the CiA 402 PPM new-setpoint handshake on one drive.

    Each attempt: drop CW bit 4, wait SW bit 12 low, set CW bit 4, wait
    SW bit 12 high. Retries up to ``max_attempts`` if bit 12 doesn't go
    high (= drive missed the rising edge). Raises on persistent failure
    rather than letting motion silently drop."""
    for attempt in range(1, max_attempts + 1):
      await self._control_word_set(node_id, cw_low, sync=True)
      cleared = await self._wait_setpoint_ack(node_id, want_high=False)
      if not cleared:
        logger.debug(
          "node %d: setpoint_ack didn't clear (attempt %d/%d)",
          node_id,
          attempt,
          max_attempts,
        )
        continue
      await self._control_word_set(node_id, cw_high, sync=True)
      raised = await self._wait_setpoint_ack(node_id, want_high=True)
      if raised:
        if attempt > 1:
          logger.debug("node %d: new setpoint accepted on attempt %d", node_id, attempt)
        return
      logger.debug(
        "node %d: setpoint_ack didn't go high (attempt %d/%d); retrying",
        node_id,
        attempt,
        max_attempts,
      )
    raise CanError(
      f"Axis {node_id}: drive did not accept new PPM setpoint after "
      f"{max_attempts} attempts (SW bit 12 never went high after CW bit 4 "
      f"rising edge)"
    )

  async def _ppm_begin_motion(self, node_ids: List[int], *, relative: bool = False) -> None:
    # CiA 402 Profile Position Mode trigger handshake. Per drive:
    #   1. CW bit 4 = 0 (new_setpoint cleared) -- bit 5 stays high so the
    #      drive treats the trigger as "change set immediately".
    #   2. wait SW bit 12 (setpoint_ack) low -- drive ack of step 1.
    #   3. CW bit 4 = 1 -- rising edge latches 0x607A; motion starts.
    #   4. wait SW bit 12 high -- drive ack of step 3. If it doesn't go
    #      high, the rising edge was missed (RPDO/SDO race or drive busy)
    #      and we retry the cycle.
    # Without (4) the failure rate is ~5-10% on this Elmo firmware: bit 4
    # falls and rises within milliseconds and the drive doesn't always see
    # the edge. Polling bit 12 high is the only authoritative confirmation
    # that the new setpoint was actually latched.
    """Enable participating drives and latch their absolute profile targets."""
    relative_bit = 0x40 if relative else 0
    cw_low = 47 + relative_bit
    cw_high = 47 + 0x10 + relative_bit
    # A disabled drive cannot acknowledge a setpoint. Faults prohibit enabling.
    await self._motors_ensure_enabled([int(n) for n in node_ids])
    for nid in node_ids:
      await self._trigger_new_setpoint(int(nid), cw_low, cw_high)

  async def _start_joint_plan(self, plan: MotorsMovePlan) -> None:
    """Configure and start the profile; caller holds the motion guard and owns completion."""
    for nid in {move.node_id for move in plan.moves}:
      await self._set_op_mode(nid, 1)
    self._ipm_mode = False

    if logger.isEnabledFor(logging.DEBUG):
      logger.debug("move plan: move_time=%.3fs, %d axes:", plan.move_time, len(plan.moves))
      for move in plan.moves:
        logger.debug(
          "  node=%d pos=%s vel=%s acc=%s dir=%s",
          move.node_id,
          move.position,
          move.velocity,
          move.acceleration,
          move.direction.name,
        )

    for move in plan.moves:
      nid = move.node_id
      await self._motor_set_move_direction(nid, move.direction)
      # 0x607A = Target Position (24698 decimal)
      await self._can_sdo_download_elmo_object(
        nid,
        24698,
        0,
        int(move.position),
        _ElmoObjectDataType.INTEGER32,
      )
      # 0x6081 = Profile Velocity (24705 decimal)
      await self._can_sdo_download_elmo_object(
        nid,
        24705,
        0,
        int(move.velocity),
        _ElmoObjectDataType.UNSIGNED32,
      )
      # A preceding JP jog can leave a nonzero end velocity: point moves must stop.
      await self._can_sdo_download_elmo_object(nid, 0x6082, 0, 0, _ElmoObjectDataType.UNSIGNED32)
      acc = max(int(move.acceleration), 100)
      # 0x6083 = Profile Acceleration (24707 decimal)
      await self._can_sdo_download_elmo_object(nid, 24707, 0, acc, _ElmoObjectDataType.UNSIGNED32)
      # 0x6084 = Profile Deceleration (24708 decimal)
      await self._can_sdo_download_elmo_object(nid, 24708, 0, acc, _ElmoObjectDataType.UNSIGNED32)

    node_ids = [move.node_id for move in plan.moves]
    await self._ppm_begin_motion(node_ids)

  async def _execute_joint_plan(self, plan: MotorsMovePlan) -> None:
    """Run a profile to completion while the caller holds the motion guard."""
    await self._start_joint_plan(plan)
    await self._wait_for_moves_done(
      [move.node_id for move in plan.moves], timeout=plan.move_time + 2
    )

  async def _motors_move_absolute_execute_locked(self, plan: MotorsMovePlan) -> None:
    """Execute a plan with scoped velocity, acceleration, direction and operation mode."""
    axes = [move.node_id for move in plan.moves]
    registers = [
      (nid, index, 0) for nid in axes for index in (0x60F2, 0x6081, 0x6082, 0x6083, 0x6084)
    ]
    registers += [(nid, 0x6060, 0) for nid in dict.fromkeys(axes)]
    async with self._preserve_settings(sdo=registers):
      try:
        await self._execute_joint_plan(plan)
      except BaseException:
        await finish_io(asyncio.ensure_future(self._stop_axes(axes)))
        raise

  async def _motor_read_position_raw(self, node_id: int, pu: bool = False) -> int:
    """Read signed encoder counts from the drive."""
    cmd = "PU" if pu else "PX"
    return await self._query_int(node_id, cmd, 0)

  async def _ipm_clear_queue(self, node_id: int) -> None:
    """Reset the drive's interpolation buffer head/tail (0x60C4 sub 6 = 0).
    Used by `ipm_select_mode` re-arm and post-cancel cleanup so a stale tail
    pointer doesn't replay old points on the next IPM enable."""
    await self._can_sdo_download(int(node_id), 0x60C4, 0x06, [0])

  async def _ipm_select_mode(self, enable: bool, node_ids: Optional[List[int]] = None) -> None:
    """Enable/disable interpolation on the participating motion axes.

    The ``_ipm_mode`` bookkeeping flag is set *pessimistically*: True before
    the SDO writes on enable, False after them on disable. So a partial
    failure mid-sequence still leaves the next caller with an accurate
    "we tried to be in IPM" signal — they can re-arm rather than assume
    we cleanly stayed in PPM.
    """
    if enable:
      ids = list(self.motion_node_ids if node_ids is None else node_ids)
      if any(
        not self._has_session_pdo(nid, number, transmit=False) for nid in ids for number in (1, 3)
      ):
        raise CanError(
          "Linear motion requires initialized RPDO1/RPDO3 mappings that can be restored"
        )
      self._ipm_mode = True
      self._ipm_axes = ids
      # First, latch CW=0x0F on every axis (op-enabled, NO ip-enable/new-
      # setpoint trigger). PPM leaves CW bit 4 high; in mode 7 bit 4 means
      # "interpolation enabled" so an unreset CW makes the drive try to
      # interpolate an empty buffer the moment we flip to mode 7, and the next
      # RPDO3 preload hits EMCY 0x34 / 0xBA (queue_full on first write).
      # RPDO1 is SynchronousCyclic so the writes need a SYNC to take effect.
      for nid in ids:
        await self._control_word_set(nid, 0x0F, sync=False)
      await self._can_sync()
      # Now do the mode-bounce + buffer clear per axis.
      for nid in ids:
        await self._set_op_mode(nid, 1)
        await self._ipm_clear_queue(nid)
        await self._set_op_mode(nid, 7)
        # Clearing disables buffer access; re-enable it before loading points.
        await self._can_sdo_download(nid, 0x60C4, 0x06, [1])
      # Let drives finish ingesting mode 7 + buffer reset before any RPDO3
      # write — without this, back-to-back IPM moves intermittently see the
      # first preload write hit EMCY 0x34 / 0xBA (queue_full) against a
      # buffer the drive still treats as "previous state".
      await asyncio.sleep(0.05)
    else:
      # Always attempt to revert — the cheap mode-display poll inside
      # _set_op_mode is the authoritative confirmation. Skip the writes only
      # if we know we never armed (cleaner test semantics; idempotent on
      # the wire either way).
      if self._ipm_mode:
        try:
          for nid in self._ipm_axes:
            await self._set_op_mode(nid, 1)  # profile position mode
        finally:
          # Clear bookkeeping even on partial failure — the alternative is
          # leaving _ipm_mode=True after a teardown attempt, which would
          # cause the next select_mode(True) to take the re-arm branch
          # against drives possibly already in PPM.
          self._ipm_mode = False
          self._ipm_axes = []

  async def _ipm_set_time_interval(self, ms: int, node_ids: Optional[List[int]] = None) -> None:
    """Program 0x60C2:01 = ms on participating axes. The 0x60C2:02 = -3 written
    at setup means the unit is milliseconds; this just sets the count.

    Drive-level: integer ms only (UNSIGNED8). Caller picks dt; runtime passes
    the same value used to build the trajectory so position/velocity scaling
    matches what the drive integrates between points."""
    if not 0 <= int(ms) <= 255:
      raise ValueError(f"ipm_set_time_interval: ms must fit in UINT8, got {ms}")
    for nid in self.motion_node_ids if node_ids is None else node_ids:
      await self._can_sdo_download_elmo_object(
        nid,
        24770,
        1,
        int(ms),
        _ElmoObjectDataType.UNSIGNED8,
      )

  async def _ipm_send_pvt_point(
    self,
    node_id: int,
    position_enc: int,
    velocity_enc_per_s: int,
  ) -> None:
    """Append a position/velocity record through RPDO3.

    Wire layout (8 bytes, little-endian, RPDO3 COB-ID = 0x400 + node_id):
      [0..3] TargetPositionIP (INT32 encoder counts)
      [4..7] TargetVelocityIP (INT32 encoder counts/sec)

    RPDO3 is event driven; the caller paces records to match the interpolation period.
    """
    if not self.io.connected:
      raise CanError("ipm_send_pvt_point called before setup()")
    payload = int(position_enc).to_bytes(4, "little", signed=True) + int(
      velocity_enc_per_s
    ).to_bytes(4, "little", signed=True)
    cob_id = (int(COBType.RPDO3) << 7) | (int(node_id) & 0x7F)
    await self.io.write(cob_id, payload)

  async def _ipm_begin_motion(self, node_ids: List[int]) -> None:
    """Start IPM streaming on the listed axes. CW=0x1F (op-enabled +
    ip-enable) per axis via RPDO1; one SYNC at the end so the
    SynchronousCyclic-mapped RPDO1s latch together.

    Auto-recovers from a prior disable (post-halt, post-fault) via
    ``motors_ensure_enabled``. Single-shot 0x1F to a drive in Switch On
    Disabled is silently dropped — the state machine needs the 6→7→15
    transitions visible to its poll loop.

    Queue errors collected during preload remain available to the runtime."""
    ids = [int(n) for n in node_ids]
    if not ids:
      return
    await self._motors_ensure_enabled(ids)
    for nid in ids[:-1]:
      await self._control_word_set(nid, 0x1F, sync=False)
    await self._control_word_set(ids[-1], 0x1F, sync=True)

  async def _ipm_stop(self, node_ids: Optional[List[int]] = None) -> None:
    """Drop ip-enable on the listed axes (CW=0x0F via RPDO1, SYNC on last).
    The drive consumes any already-buffered points before halting — coast
    can run up to (queued_points * dt_ms) ms past the request. Defaults to
    every motion axis when ``node_ids`` is None."""
    ids = [int(n) for n in node_ids] if node_ids is not None else list(self.motion_node_ids)
    if not ids:
      return
    for nid in ids[:-1]:
      await self._control_word_set(nid, 0x0F, sync=False)
    await self._control_word_set(ids[-1], 0x0F, sync=True)

  def _ipm_check_queue_fault(self, node_ids: List[int], *, allow_empty: bool = False) -> None:
    """Raise CanError if any axis has a fatal IPM queue condition.

    Inspects the EMCY-driven ``_emcy`` state for ``queue_full`` (drive
    rejected our point) or ``underflow`` (drive ran the buffer dry).
    Either condition means the trajectory is no longer trustworthy — the
    streaming runtime calls this after each send-batch to surface the fault
    promptly instead of letting the move silently degrade.
    """
    bad: List[str] = []
    for nid in node_ids:
      st = self._emcy.get(int(nid))
      if st is None:
        continue
      if st.queue_full:
        bad.append(f"Axis {nid} queue_full (failed write @ {st.queue_full_failed_write_pointer})")
      if st.underflow and not allow_empty:
        bad.append(f"Axis {nid} underflow")
    if bad:
      raise CanError("IPM queue fault: " + "; ".join(bad))

  async def _finish_stream(self, active_axes: List[int]) -> None:
    """Try both streaming cleanup steps and surface any failure."""
    try:
      await self._ipm_stop(active_axes)
    finally:
      await self._ipm_select_mode(False)

  def _encoder_tolerance(self, axis: Axis) -> int:
    """Allow 0.01 drive units of settling error, or one encoder count."""
    return max(1, round(abs(self._cfg.axes[axis].motor_conversion_factor) * 0.01))

  async def _check_encoder_targets(self, targets: Dict[Axis, int]) -> None:
    """Reject a settled move that did not reach its absolute encoder targets."""
    for axis, target in targets.items():
      actual = await self._motor_read_position_raw(axis)
      if abs(actual - target) > self._encoder_tolerance(axis):
        raise CanError(
          f"{axis.name} did not reach its target: expected {target}, measured {actual} counts"
        )

  async def _wait_for_encoder_targets(self, targets: Dict[Axis, int], timeout: float = 3.0) -> None:
    """Keep the final position reference active until feedback reaches the endpoint."""
    deadline = time.monotonic() + timeout
    while True:
      try:
        await self._check_encoder_targets(targets)
        return
      except CanError:
        if time.monotonic() >= deadline:
          raise
      await asyncio.sleep(0.02)

  async def _stream_samples_unscoped(self, samples: "List[kinematics.LinearPathSample]") -> None:
    """Stream a pre-built list of LinearPathSamples through the drive's IPM
    buffer. Caller MUST hold ``_motion_guard``. The samples' encoder positions
    and velocities are fed to each axis 8 frames ahead of the drive's read
    pointer at the fixed IPM cadence (``_PVT_DT_MS``).

    On cancel/exception the drive is brought back to PPM through a
    ``finally`` block. Coast on a cancel can run up to ``_PVT_PRELOAD
    * dt_ms`` (~64 ms) past the cancel — see ``halt()`` for zero-coast stop.
    """
    if len(samples) < 2:
      await self._ipm_select_mode(False)
      return

    all_axes = [int(ax) for ax in (Axis.SHOULDER, Axis.Z, Axis.ELBOW, Axis.WRIST)]
    active_axes: List[int] = []
    for nid in all_axes:
      seq = [s.encoder_position[Axis(nid)] for s in samples]
      if max(seq) != min(seq):
        active_axes.append(nid)

    # Only complete revolutions are equivalent absolute rotary positions.
    # A stale start position must not translate the requested path or endpoint.
    offsets: Dict[Axis, int] = {}
    for nid in all_axes:
      axis = Axis(nid)
      actual = await self._motor_read_position_raw(nid)
      planned = samples[0].encoder_position[axis]
      cfg = self._cfg.axes[axis]
      offset = 0
      if axis in (Axis.SHOULDER, Axis.WRIST) and cfg.unlimited_travel:
        revolution = cfg.motor_conversion_factor * 360
        offset = round(round((actual - planned) / revolution) * revolution)
      if abs(actual - planned - offset) > self._encoder_tolerance(axis):
        raise ValueError(f"Streamed path does not start at the measured {axis.name} position")
      offsets[axis] = offset
    if not active_axes:
      return
    await self._motors_ensure_enabled(active_axes)

    interval = self._PVT_DT_MS
    dt_s = interval / 1000.0
    preload = min(self._PVT_PRELOAD, len(samples))

    # The interpolation period is latched when the drive enters mode 7.
    for nid in active_axes:
      state = self._emcy.get(nid)
      if state is not None:
        state.queue_full = False
        state.underflow = False
    await self._ipm_set_time_interval(interval, active_axes)
    await self._ipm_select_mode(True, active_axes)
    try:
      # Preload PRELOAD frames per axis. The drive starts consuming as soon
      # as begin_motion fires; preload lets the producer fall behind by up
      # to (PRELOAD-1) * dt without underflowing the queue.
      for i in range(preload):
        for nid in active_axes:
          ax = Axis(nid)
          await self._ipm_send_pvt_point(
            nid,
            samples[i].encoder_position[ax] + offsets[ax],
            samples[i].encoder_velocity[ax],
          )
      self._ipm_check_queue_fault(active_axes)

      # Capture pacing reference *before* begin_motion (drive starts consuming
      # on SYNC; capturing after underestimates elapsed drive-time and pace
      # too late, eating into the underflow margin).
      start = time.monotonic()
      await self._ipm_begin_motion(active_axes)

      for i in range(preload, len(samples)):
        target_t = (i - (preload - 1)) * dt_s
        while time.monotonic() - start < target_t:
          await asyncio.sleep(0)
        for nid in active_axes:
          ax = Axis(nid)
          await self._ipm_send_pvt_point(
            nid,
            samples[i].encoder_position[ax] + offsets[ax],
            samples[i].encoder_velocity[ax],
          )
        self._ipm_check_queue_fault(active_axes)

      # Let the terminal zero-velocity point settle before disabling interpolation.
      await self._wait_for_moves_done(active_axes, timeout=3.0)
      # An exhausted FIFO disables interpolation and reports underflow even at
      # the terminal zero-velocity point. Only accept it after feeding the whole
      # path, waiting for settled feedback, and verifying every endpoint below.
      self._ipm_check_queue_fault(active_axes, allow_empty=True)
      await self._wait_for_encoder_targets(
        {
          Axis(nid): samples[-1].encoder_position[Axis(nid)] + offsets[Axis(nid)]
          for nid in all_axes
        }
      )
      await self._ipm_stop(active_axes)
    finally:
      await finish_io(asyncio.ensure_future(self._finish_stream(active_axes)))

  async def _stream_samples(self, samples: List[kinematics.LinearPathSample]) -> None:
    """Stream with the caller's operation modes and interpolation interval restored afterwards."""
    registers = [
      (nid, index, sub)
      for nid in self.motion_node_ids
      for index, sub in (
        (0x6060, 0),
        (0x60C2, 1),
        (0x60C2, 2),
      )
    ]
    async with self._preserve_settings(sdo=registers):
      try:
        await self._stream_samples_unscoped(samples)
      except BaseException:
        await finish_io(asyncio.ensure_future(self._stop_axes(list(MOTION_AXES))))
        raise

  @property
  def setup_finished(self) -> bool:
    """Whether connection and calibration discovery completed."""
    return self._setup_finished

  async def _bus_stop(self) -> None:
    """Restore configuration before disconnecting, without re-enabling a faulted motor."""
    errors: List[BaseException] = []
    for task in tuple(self._background_tasks):
      try:
        await task
      except BaseException as exc:
        errors.append(exc)
    if self.io.connected:
      for nid, enabled in self._session_enabled.items():
        try:
          current_enabled = await self.request_axis_enabled(nid)
          if current_enabled != enabled and (not enabled or not self._faulted):
            if enabled and await self.request_estop():
              raise CanError("Cannot restore enabled motor while e-stop is active")
            await self._motor_enable(nid, enabled, use_ds402=False)
        except BaseException as exc:
          errors.append(exc)
      try:
        if self.gripper._hold_settings is not None:
          await self._stop_axes([Axis.SERVO_GRIPPER])
        await self.gripper._restore_current_limits()
      except BaseException as exc:
        errors.append(exc)
      try:
        await self._restore_registers(self._session_registers)
      except BaseException as exc:
        errors.append(exc)
      for pdo in reversed(self._session_pdos if self._session_configured else []):
        try:
          await self._restore_pdo(pdo)
        except BaseException as exc:
          errors.append(exc)
      for nid, state in self._session_nmt.items():
        try:
          if state != 5:
            await self.io.set_nmt_state(nid, state)
        except BaseException as exc:
          errors.append(exc)
    self._loop = None
    for future in self._pending_bi.values():
      if not future.done():
        future.set_exception(CanError("KX2 connection closed"))
    try:
      await self.io.stop()
    finally:
      self._pending_bi.clear()
      self._bi_locks.clear()
      self._invalid_bi_keys.clear()
      self._emcy.clear()
      self._last_emcy_node_id = None
      self._emcy_callbacks.clear()
      self._statusword.clear()
      self._statusword_event.clear()
      self._session_nmt.clear()
      self._session_enabled.clear()
      self._session_pdos.clear()
      self._session_configured = False
      self._session_registers = _Registers()
      self._ipm_mode = False
      self._ipm_axes = []
    if errors:
      logger.error("KX2 restoration failed: %s", errors)
      raise errors[0]

  async def _stop_session(self) -> None:
    """Try every cleanup step, retaining the first failure for the caller."""
    error: Optional[BaseException] = None
    try:
      await self._bus_stop()
    except BaseException as exc:
      error = exc
    try:
      if self._bcr is not None:
        await self._bcr.stop()
    except BaseException as exc:
      error = error or exc
    finally:
      self._setup_finished = False
      self._config = None
      self._freedrive_axes.clear()
      self._stopping = False
    if error is not None:
      raise error

  async def _read_io_names(
    self, nid: int, start: int, end: int, named: Dict[int, str]
  ) -> Dict[int, str]:
    """Read UI[start..end-1] as a channel -> human name map.

    Channel index is 1-based. Codes in `named` map to fixed labels; positive
    unknowns become "AuxPinN"; non-positive means unassigned.
    """
    out: Dict[int, str] = {}
    for ui_idx in range(start, end):
      code = await self._query_int(nid, "UI", ui_idx)
      ch = ui_idx - start + 1
      if code in named:
        out[ch] = named[code]
      else:
        out[ch] = "" if code <= 0 else f"AuxPin{code}"
    return out

  async def _read_axis_config(self, nid: int) -> AxisConfig:
    """Read one axis calibration, travel limits and I/O assignments."""
    logger.debug("Reading parameters for axis %s", nid)

    digital_inputs = await self._read_io_names(nid, 5, 11, _DIGITAL_INPUT_NAMES)
    analog_inputs = await self._read_io_names(nid, 11, 13, {})
    outputs = await self._read_io_names(nid, 13, 17, _OUTPUT_NAMES)

    await self._query_int(nid, "UI", 24)  # serial — read for parity, unused

    uf1 = await self._query_float(nid, "UF", 1)
    uf2 = await self._query_float(nid, "UF", 2)
    if uf1 == 0.0 or uf2 == 0.0:
      raise CanError(f"Invalid motor conversion factor for axis {nid}: UF[1]={uf1}, UF[2]={uf2}")
    motor_conversion_factor = uf1 / uf2

    xm1 = await self._query_int(nid, "XM", 1)
    xm2 = await self._query_int(nid, "XM", 2)
    max_travel = await self._query_float(nid, "UF", 3)
    min_travel = await self._query_float(nid, "UF", 4)
    vh3 = await self._query_int(nid, "VH", 3)
    vl3 = await self._query_int(nid, "VL", 3)

    joint_move_direction = JointMoveDirection.Normal
    if (xm1 == 0 and xm2 == 0) or (xm1 <= vl3 and xm2 >= vh3):
      unlimited_travel = False
    elif xm1 > vl3 and xm2 < vh3:
      unlimited_travel = True
      if Axis(nid).is_motion:
        joint_move_direction = JointMoveDirection.ShortestWay
    else:
      raise CanError(
        f"Invalid travel limits or modulo settings for axis {nid}: "
        f"VH[3]={vh3}, VL[3]={vl3}, XM[1]={xm1}, XM[2]={xm2}"
      )

    ca45 = await self._query_int(nid, "CA", 45)
    if not (0 < ca45 <= 4):
      raise CanError(f"Invalid encoder socket for axis {nid}: CA[45]={ca45}")
    enc_type = await self._query_int(nid, "CA", 40 + ca45)
    if enc_type in (1, 2):
      absolute_encoder = False
    elif enc_type == 24:
      absolute_encoder = True
    else:
      raise CanError(f"Unsupported encoder type for axis {nid}: CA[4{ca45}]={enc_type}")

    ca46 = await self._query_int(nid, "CA", 46)
    num3 = 1.0 if ca45 == ca46 else await self._query_float(nid, "FF", 3)
    denom = motor_conversion_factor * num3

    sp2 = await self._query_int(nid, "SP", 2)
    if sp2 == 100000:
      max_vel = await self._query_int(nid, "VH", 2) / 1.01 / denom
    else:
      max_vel = sp2 / denom
    max_accel = await self._query_int(nid, "SD", 0) / 1.01 / denom

    return AxisConfig(
      motor_conversion_factor=motor_conversion_factor,
      max_travel=max_travel,
      min_travel=min_travel,
      unlimited_travel=unlimited_travel,
      absolute_encoder=absolute_encoder,
      max_vel=max_vel,
      max_accel=max_accel,
      joint_move_direction=joint_move_direction,
      digital_inputs=digital_inputs,
      analog_inputs=analog_inputs,
      outputs=outputs,
    )

  async def _read_servo_gripper_config(self) -> ServoGripperConfig:
    """Read homing and current settings for the servo gripper."""
    sg = Axis.SERVO_GRIPPER
    return ServoGripperConfig(
      home_pos=int(await self._query_float(sg, "UF", 6)),
      home_search_vel=int(await self._query_float(sg, "UF", 7)),
      home_search_accel=int(await self._query_float(sg, "UF", 8)),
      home_default_position_error=int(await self._query_float(sg, "UF", 9)),
      home_hard_stop_position_error=int(await self._query_float(sg, "UF", 10)),
      home_hard_stop_offset=int(await self._query_float(sg, "UF", 11)),
      home_index_offset=int(await self._query_float(sg, "UF", 12)),
      home_offset_vel=int(await self._query_float(sg, "UF", 13)),
      home_offset_accel=int(await self._query_float(sg, "UF", 14)),
      home_timeout_msec=int(await self._query_float(sg, "UF", 15)),
      continuous_current=await self._query_float(sg, "UF", 16),
      peak_current=await self._query_float(sg, "UF", 17),
    )

  async def _read_config(self) -> KX2Config:
    """Read the per-arm configuration from the drives.

    Driver discovery has already populated `node_id_list` with everything
    on the bus; here we just verify the required motion axes are present
    and read each drive's parameters.
    """
    nodes = self.node_id_list
    for required in MOTION_AXES:
      if required not in nodes:
        raise CanError(f"Missing required axis {required}")
    has_rail = Axis.RAIL in nodes
    has_servo_gripper = Axis.SERVO_GRIPPER in nodes
    if has_rail:
      warnings.warn("Rails has not been tested for KX2 robots.")

    axes: Dict[Axis, AxisConfig] = {}
    for nid in nodes:
      axes[Axis(nid)] = await self._read_axis_config(nid)

    sh = Axis.SHOULDER
    return KX2Config(
      wrist_offset=await self._query_float(sh, "UF", 8),
      elbow_offset=await self._query_float(sh, "UF", 9),
      elbow_zero_offset=await self._query_float(sh, "UF", 10),
      axes=axes,
      base_to_gripper_clearance_z=await self._query_float(sh, "UF", 6),
      base_to_gripper_clearance_arm=await self._query_float(sh, "UF", 7),
      robot_on_rail=has_rail,
      servo_gripper=await self._read_servo_gripper_config() if has_servo_gripper else None,
    )

  async def _arm_setup(self) -> None:
    """Read the arm's calibration without enabling or moving it."""
    self._config = await self._read_config()
    if self.parking_position is not None:
      self._validate_parking_position(self.parking_position)

  # --- DS402 / motor control ----------------------------------------------

  async def _motor_emergency_stop(self, node_id: int) -> None:
    """Disable one motor immediately through the binary interpreter."""
    await self._write(node_id, "MO", 0, 0)

  # --- EMCY auto-disable ---------------------------------------------------

  def _on_emcy(
    self, node_id: int, frame: EmcyFrame, description: str, disable_motors: bool
  ) -> None:
    """Latch fatal faults and stop positioning motors without acquiring the motion lock."""
    logger.error("KX2 EMCY axis=%d code=0x%04X: %s", node_id, frame.err_code, description)
    if not disable_motors:
      return
    self._faulted = True
    owner = self._motion_owner
    if owner is not None:
      owner.cancel()

    async def disable_all() -> None:
      """Attempt every positioning motor even if one node is unavailable."""
      for axis in MOTION_AXES:
        try:
          await self._motor_emergency_stop(axis)
        except Exception:
          logger.exception("EMCY auto-disable failed for axis %s", axis.name)

    task = self.loop.create_task(disable_all())
    self._background_tasks.add(task)
    task.add_done_callback(self._background_tasks.discard)

  # ======================================================================
  # CAN transport + Elmo drive primitives
  # ======================================================================

  # --- PDO configuration (pure SDO writes; no library-PDO machinery) ------

  async def _can_tpdo_unmap(self, tpdo: TPDO, node_id: int) -> None:
    """Disable a transmit PDO and clear its mapping."""
    cob_type_int = {
      TPDO.TPDO1: COBType.TPDO1.value,
      TPDO.TPDO3: COBType.TPDO3.value,
      TPDO.TPDO4: COBType.TPDO4.value,
    }[tpdo]
    node_id &= 0x7F
    num1 = ((cob_type_int & 0x01) << 7) | node_id
    num2 = (cob_type_int >> 1) & 0x07
    await self._can_sdo_download(node_id, 0x1800 + tpdo.value - 1, 1, [num1, num2, 0, 0xC0])
    await self._can_sdo_download(node_id, 0x1A00 + tpdo.value - 1, 0, [0])

  async def _rpdo_map(
    self,
    rpdo: RPDO,
    node_id: int,
    mapped_objects: List[RPDOMappedObject],
    transmission_type: PDOTransmissionType,
  ) -> None:
    """Configure the objects received by a drive in one PDO."""
    rpdo_idx = (int(rpdo) - 1) & 0xFF
    cob_type = {
      RPDO.RPDO1: COBType.RPDO1,
      RPDO.RPDO3: COBType.RPDO3,
      RPDO.RPDO4: COBType.RPDO4,
    }[rpdo]
    cob_id_11 = ((int(cob_type) & 0x0F) << 7) | (node_id & 0x7F)

    # Disable PDO (bit 31 set)
    await self._can_sdo_download(node_id, 0x1400 + rpdo_idx, 1, _u32_le(0x80000000 | cob_id_11))
    # Clear mapping count
    await self._can_sdo_download(node_id, 0x1600 + rpdo_idx, 0, [0])
    # Transmission type
    await self._can_sdo_download(node_id, 0x1400 + rpdo_idx, 2, [int(transmission_type)])
    # Mapped objects
    for i, mo in enumerate(mapped_objects):
      await self._can_sdo_download(node_id, 0x1600 + rpdo_idx, i + 1, _u32_le(int(mo)))
    # Mapping count
    await self._can_sdo_download(node_id, 0x1600 + rpdo_idx, 0, [len(mapped_objects)])
    # Re-enable (clear bit 31)
    await self._can_sdo_download(node_id, 0x1400 + rpdo_idx, 1, _u32_le(cob_id_11))

  async def _tpdo_map(
    self,
    tpdo: TPDO,
    node_id: int,
    mapped_objects: List[TPDOMappedObject],
    event_trigger: TPDOTrigger,
    event_timer_ms: int = 0,
    delay_100_us: int = 0,
    transmission_type: PDOTransmissionType = PDOTransmissionType.EventDrivenDev,
  ) -> None:
    """Configure the objects and events transmitted by a drive."""
    tpdo_idx = (int(tpdo) - 1) & 0xFF
    cob_type = {
      TPDO.TPDO1: COBType.TPDO1,
      TPDO.TPDO3: COBType.TPDO3,
      TPDO.TPDO4: COBType.TPDO4,
    }[tpdo]
    cob_id_11 = ((int(cob_type) & 0x0F) << 7) | (node_id & 0x7F)
    event_mask = 1 << int(event_trigger)

    # Disable TPDO (bit 30 + 31)
    await self._can_sdo_download(node_id, 0x1800 + tpdo_idx, 1, _u32_le(0xC0000000 | cob_id_11))
    # Clear mapping count
    await self._can_sdo_download(node_id, 0x1A00 + tpdo_idx, 0, [0])
    # Transmission type
    await self._can_sdo_download(node_id, 0x1800 + tpdo_idx, 2, [int(transmission_type)])
    # Inhibit / delay 100us
    await self._can_sdo_download(
      node_id, 0x1800 + tpdo_idx, 3, list(delay_100_us.to_bytes(2, "little"))
    )
    # Event timer (ms)
    await self._can_sdo_download(
      node_id, 0x1800 + tpdo_idx, 5, list(event_timer_ms.to_bytes(2, "little"))
    )
    # Vendor event mask at 0x2F20:<tpdo_num>
    await self._can_sdo_download(node_id, 0x2F20, int(tpdo) & 0xFF, _u32_le(event_mask))
    # Mapped objects
    for i, mo in enumerate(mapped_objects):
      await self._can_sdo_download(node_id, 0x1A00 + tpdo_idx, i + 1, _u32_le(int(mo)))
    # Mapping count
    await self._can_sdo_download(node_id, 0x1A00 + tpdo_idx, 0, [len(mapped_objects)])
    # Re-enable (clear bits 30 + 31)
    await self._can_sdo_download(node_id, 0x1800 + tpdo_idx, 1, _u32_le(cob_id_11))

  # --- EMCY (CANopen Emergency, COB-ID 0x80 + node_id) --------------------

  def add_emcy_callback(self, cb: Callable[[int, EmcyFrame, str, bool], None]) -> None:
    """Register a callback fired on every (non-suppressed) EMCY frame.

    Callback signature: ``cb(node_id, frame, description, disable_motors)``.
    Always invoked on the asyncio loop thread captured in :meth:`setup`.
    Exceptions raised by the callback are logged and swallowed so one bad
    handler can't poison the rest.
    """
    if cb not in self._emcy_callbacks:
      self._emcy_callbacks.append(cb)

  def _dispatch_emcy(self, node_id: int, data: bytes) -> None:
    """Decode an emergency frame and notify registered fault observers."""
    if len(data) < 8:
      logger.warning("EMCY frame too short from node %d: %s", node_id, data.hex())
      return
    err_code, err_reg, elmo_err, d1, d2 = struct.unpack("<HBBHH", data[:8])
    frame = EmcyFrame(err_code, err_reg, elmo_err, d1, d2)

    state = self._emcy.setdefault(node_id, _NodeEmcyState())
    state.last_frame = frame
    self._last_emcy_node_id = node_id
    desc, disable_motors, suppress = _decode_emcy(frame, state)
    # Tier the level so IPM housekeeping (queue-low/underflow) doesn't drown
    # ops logs while real faults stay loud. Unknown codes warn so we notice.
    if disable_motors:
      level = logging.ERROR
    elif desc.startswith(("Unknown EMCY", "Unknown vendor EMCY", "DS402 IP Error")):
      level = logging.WARNING
    elif suppress:
      level = logging.DEBUG
    else:
      level = logging.INFO
    logger.log(
      level,
      "EMCY node=%d code=0x%04X reg=0x%02X elmo=0x%02X d1=0x%04X d2=0x%04X: %s",
      node_id,
      err_code,
      err_reg,
      elmo_err,
      d1,
      d2,
      desc,
    )

    if disable_motors:
      # Pre-format with axis context: motor_check_if_move_done just raises
      # `f"Motor Fault: {emcy_move_error}"` and the consumer expects the
      # axis to be named.
      state.move_error = f"Axis {node_id} {desc}"

    if suppress:
      return

    for cb in list(self._emcy_callbacks):
      try:
        cb(node_id, frame, desc, disable_motors)
      except Exception:
        logger.exception("EMCY user callback raised; continuing")

  def _make_emcy_callback(self, node_id: int) -> FrameCallback:
    """Return a `canopen.Network.subscribe` callback bound to a specific node."""

    def _cb(cob_id: int, data: bytes, timestamp: float) -> None:
      # PLR CANopen has already delivered this frame to the owning loop.
      """Deliver a received frame on the owning event loop."""
      if self._loop is None:
        return
      self._dispatch_emcy(node_id, bytes(data))

    return _cb

  def _dispatch_statusword(self, node_id: int, sw: int) -> None:
    """Publish a received statusword and wake transition waiters."""
    self._statusword[node_id] = sw
    ev = self._statusword_event.get(node_id)
    if ev is not None:
      ev.set()

  def _make_tpdo3_callback(self, node_id: int) -> FrameCallback:
    """Return a callback that decodes TPDO3 (StatusWord) and signals waiters."""

    def _cb(cob_id: int, data: bytes, timestamp: float) -> None:
      """Deliver a received frame on the owning event loop."""
      if self._loop is None:
        return
      if len(data) < 2:
        # StatusWord is 2 bytes — a shorter frame means the drive's TPDO3
        # mapping is wrong or a different sender is squatting on the COB-ID.
        # _wait_setpoint_ack would silently fall back to SDO probing forever.
        logger.warning(
          "TPDO3 frame too short from node %d: %s (expected >=2 bytes)",
          node_id,
          bytes(data).hex(),
        )
        return
      sw = int.from_bytes(bytes(data[:2]), "little")
      self._dispatch_statusword(node_id, sw)

    return _cb

  def _dispatch_bi_response(self, node_id: int, data: bytes) -> None:
    """Decode a typed interpreter reply and resolve its pending request."""
    if len(data) < 8:
      logger.warning("Binary interpreter response too short from node %d: %s", node_id, data.hex())
      return
    msg_type = chr(data[0]) + chr(data[1])
    msg_index = ((data[3] & 0x3F) << 8) | data[2]
    is_int = (data[3] & 0x80) == 0
    fmt = "<i" if is_int else "<f"
    (val,) = struct.unpack(fmt, data[4:8])

    fut = self._pending_bi.pop((node_id, msg_type, msg_index), None)
    if fut is not None and not fut.done():
      if data[3] & 0x40:
        fut.set_exception(
          CanError(f"Node {node_id} rejected {msg_type}[{msg_index}]: Elmo error {data[4]}")
        )
      else:
        fut.set_result(val)

  # --- Elmo binary interpreter (vendor protocol on TPDO2/RPDO2) ------------

  def _make_bi_callback(self, node_id: int) -> FrameCallback:
    """Return a `canopen.Network.subscribe` callback bound to a specific node."""

    def _cb(cob_id: int, data: bytes, timestamp: float) -> None:
      # PLR CANopen has already delivered this frame to the owning loop.
      """Deliver a received frame on the owning event loop."""
      if self._loop is None:
        return
      self._dispatch_bi_response(node_id, bytes(data))

    return _cb

  async def _bus_setup(self) -> None:
    """Connect, save node state, and configure reversible PDO mappings."""
    self._loop = asyncio.get_running_loop()
    await self.io.setup(self.node_id_list)
    self.add_emcy_callback(self._on_emcy)
    for nid in self.node_id_list:
      self._emcy[nid] = _NodeEmcyState()
      self.io.subscribe(_EMCY_COB_BASE + nid, self._make_emcy_callback(nid))
      self.io.subscribe(_BI_RESPONSE_COB_BASE + nid, self._make_bi_callback(nid))
      state = await self.io.request_nmt_state(nid)
      if state not in (4, 5, 127):
        raise CanError(f"Node {nid} has unsupported NMT state {state}")
      self._session_nmt[nid] = state
      if state != 5:
        await self.io.set_nmt_state(nid, 5)
      # Gold drives report 3 when disabled, with no active motion profiler.
      if await self._motor_get_motion_status(nid) not in (0, 1, 3):
        raise CanError(f"Node {nid} is moving; setup will not interrupt it")
      self._session_enabled[nid] = await self.request_axis_enabled(nid)

    # Snapshot every changed setting before writing any mappings.
    for nid in self.node_id_list:
      for number in (1, 3, 4):
        await self._save_restorable_pdo(nid, number, transmit=True)
    for nid in self.motion_node_ids:
      for number in (1, 3):
        await self._save_restorable_pdo(nid, number, transmit=False)
    self._session_registers = await self._snapshot_registers(
      sdo=[
        (nid, index, sub)
        for nid in self.motion_node_ids
        for index, sub in (
          (0x60C0, 0),
          (0x60C4, 2),
          (0x60C4, 3),
          (0x60C4, 5),
          (0x60C2, 2),
          (0x605D, 0),
          (0x6060, 0),
        )
      ],
    )

    self._session_configured = True
    # TPDO3 push for StatusWord (0x6041) so _wait_setpoint_ack can wait on
    # the bit-12 transition without an SDO round-trip per poll. Subscribe
    # before remapping so we don't lose the first frame the drive emits
    # when the new event-trigger arms. 1 ms inhibit (10 * 100 us) caps
    # bus traffic — SW changes happen at the ~1-2 ms servo cycle, so the
    # inhibit doesn't lose edges in practice and keeps the bus quiet
    # during IPM streaming.
    for nid in self.motion_node_ids:
      if not self._has_session_pdo(nid, 3, transmit=True):
        continue
      self._statusword_event[nid] = asyncio.Event()
      self.io.subscribe(_TPDO3_COB_BASE + nid, self._make_tpdo3_callback(nid))

    # Unmap TPDO1, map TPDO3 (StatusWord, triggered on any SW change) and
    # TPDO4 (DigitalInputs, triggered on edge).
    for node_id in self.node_id_list:
      if self._has_session_pdo(node_id, 1, transmit=True):
        await self._can_tpdo_unmap(TPDO.TPDO1, node_id)
      if self._has_session_pdo(node_id, 3, transmit=True):
        await self._tpdo_map(
          TPDO.TPDO3,
          node_id,
          [TPDOMappedObject.StatusWord],
          TPDOTrigger.StatusWordEvent,
          delay_100_us=10,
        )
      if self._has_session_pdo(node_id, 4, transmit=True):
        await self._tpdo_map(
          TPDO.TPDO4, node_id, [TPDOMappedObject.DigitalInputs], TPDOTrigger.DigitalInputEvent
        )

    # Elmo vendor objects: interpolation config for IPM.
    for nid in self.motion_node_ids:
      await self._can_sdo_download_elmo_object(nid, 24768, 0, -1, _ElmoObjectDataType.INTEGER16)
      await self._can_sdo_download_elmo_object(nid, 24772, 2, 16, _ElmoObjectDataType.UNSIGNED32)
      await self._can_sdo_download_elmo_object(nid, 24772, 3, 0, _ElmoObjectDataType.UNSIGNED8)
      await self._can_sdo_download_elmo_object(nid, 24772, 5, 8, _ElmoObjectDataType.UNSIGNED8)
      await self._can_sdo_download_elmo_object(nid, 24770, 2, -3, _ElmoObjectDataType.INTEGER8)
      await self._can_sdo_download_elmo_object(nid, 24669, 0, 1, _ElmoObjectDataType.INTEGER16)

    # Map synchronous controlwords and event-driven interpolation records.
    for nid in self.motion_node_ids:
      if self._has_session_pdo(nid, 1, transmit=False):
        await self._rpdo_map(
          RPDO.RPDO1, nid, [RPDOMappedObject.ControlWord], PDOTransmissionType.SynchronousCyclic
        )
      if self._has_session_pdo(nid, 3, transmit=False):
        await self._rpdo_map(
          RPDO.RPDO3,
          nid,
          [RPDOMappedObject.TargetPositionIP, RPDOMappedObject.TargetVelocityIP],
          PDOTransmissionType.EventDrivenDev,
        )

  # --- lifecycle -----------------------------------------------------------

  async def setup(self, skip_home: bool = True) -> None:
    """Connect and read calibration. Homing is explicit unless ``skip_home=False``.

    Setup does not reset the network, enable motors, or close the jaws. The optional
    gripper homing moves hardware and updates its encoder calibration.
    """
    async with self._lifecycle_lock:
      if self._setup_finished:
        return
      self._faulted = False
      self._stopping = False
      try:
        if self._bcr is not None:
          await self._bcr.setup()
        await self._bus_setup()
        await self._arm_setup()
        self._setup_finished = True
        if not skip_home:
          await self.gripper.home()
      except BaseException:
        await finish_io(asyncio.ensure_future(self._stop_session()))
        raise
      logger.info(
        "KX2 connected on %s:%s; axes=%s", self._interface, self._channel, self.node_id_list
      )

  async def _join_motion(self, task: asyncio.Task) -> None:
    """Join cancelled work before its transports are closed."""
    try:
      await task
    except asyncio.CancelledError:
      pass

  async def stop(self) -> None:
    """Finish active work, restore session settings, and close both transports.

    Gold firmware cannot restore unused mapping descriptors to zero; their original
    mapping counts and communication settings are restored, leaving them inactive.
    An untouched power-on mode 0 is preserved. After motion, it becomes idle profile
    position mode 1 because mode 0 cannot be selected through the drive protocol.
    """
    async with self._lifecycle_lock:
      self._stopping = True
      try:
        owner = self._motion_owner
        if owner is not None and owner is not asyncio.current_task():
          owner.cancel()
          await finish_io(asyncio.ensure_future(self._join_motion(owner)))
      finally:
        await finish_io(asyncio.ensure_future(self._stop_session()))

  async def scan_barcode(self, read_time: Optional[float] = None) -> Optional[Barcode]:
    """Fire the onboard barcode reader and return the decoded ``Barcode`` (or
    ``None`` on a no-read). Raises if the KX2 was constructed without a
    ``barcode_port``.
    """
    if self._bcr is None:
      raise RuntimeError("KX2 was constructed without barcode_port; no barcode reader configured.")
    return await self._bcr.scan_barcode(read_time)

  # --- reentrant motion guard ----------------------------------------------

  @asynccontextmanager
  async def _motion_guard(self) -> AsyncIterator[None]:
    """Serialize complete operations and allow their same-task suboperations."""
    current = asyncio.current_task()
    if self._motion_owner is current:
      yield
      return
    async with self._motion_lock:
      if self._stopping or self._faulted:
        raise CanError("KX2 is stopped or faulted; reconnect after resolving the fault")
      self._motion_owner = current
      try:
        yield
      finally:
        self._motion_owner = None

  @property
  def last_emcy(self) -> Optional[EmcyFrame]:
    """Most recent EMCY frame received from any node; ``None`` if none yet.

    Diagnostic only — motion logic uses `emcy_move_error` for fault
    detection and `_emcy[nid]` for per-axis IPM queue state."""
    if self._last_emcy_node_id is None:
      return None
    st = self._emcy.get(self._last_emcy_node_id)
    return st.last_frame if st is not None else None

  def _clear_emcy_state(self, node_id: Optional[int] = None) -> None:
    """Clear EMCY state.

    With ``node_id``: reset that one node's `_NodeEmcyState` (queue
    counters + sticky fault + last_frame). Without: clear the sticky
    fault on every node — leaves queue counters intact since they're
    stream-scoped and reset by `ipm_begin_motion`.
    """
    if node_id is not None:
      if node_id in self._emcy:
        self._emcy[node_id] = _NodeEmcyState()
      return
    for st in self._emcy.values():
      st.move_error = None

  async def request_drive_version(self, node_id: int) -> str:
    """Read the standard firmware-version object without changing interpreter settings."""
    async with self._lifecycle_lock:
      reply = await self.io.upload(node_id, 0x100A, 0)
      return reply.decode("ascii", errors="strict").rstrip("\x00").rstrip()

  async def _ipm_wait_motion_complete(
    self,
    node_ids: List[int],
    timeout_s: float,
  ) -> None:
    """Wait until SW bit-10 (motion_complete / target_reached) goes high on
    every listed axis. The bit goes high once the IP buffer drains and the
    drive's trajectory generator settles on the last commanded position.

    Polls via the TPDO3-backed StatusWord cache; falls back to SDO probe
    if the cache hasn't seen a frame within 5 ms. Raises CanError on
    timeout, naming the offending axis."""

    async def _wait_one(nid: int) -> None:
      """Wait for one drive to report its target reached."""
      ok = await self._wait_sw_bit(
        int(nid),
        bit_mask=1 << 10,
        want_high=True,
        timeout_s=timeout_s,
      )
      if not ok:
        raise CanError(
          f"Axis {nid}: SW bit-10 (target reached) never went high within "
          f"{timeout_s:.1f}s — IPM trajectory did not complete"
        )

    await asyncio.gather(*(_wait_one(int(n)) for n in node_ids))

  # --- I/O -----------------------------------------------------------------

  async def _read_digital_input(self, node_id: int, input_num: int) -> bool:
    """Read one drive input bit."""
    return await self._query_int(node_id, "IB", input_num) == 1

  async def request_output(self, node_id: int, output_num: int) -> bool:
    """Read the current state of an output bit."""
    val = await self._query_int(node_id, "OP", 0)
    mask = 1 << (output_num - 1)
    return (val & mask) == mask

  async def set_output(self, node_id: int, output_num: int, state: bool) -> None:
    """Set an output to the requested state."""
    await self._write(node_id, "OB", output_num, 1 if state else 0)

  async def _motor_stop(self, node_id: int, settle: float = 0.1) -> None:
    """Stop one drive without changing its operation mode."""
    await self._stop_axes([node_id])

  async def _read_input_logic(self, node_id: int, input_num: int) -> int:
    """Read the action assigned to an input."""
    return await self._query_int(node_id, "IL", input_num)

  async def _configure_input_logic(
    self,
    node_id: int,
    input_num: int,
    logic: int,
    logic_high: bool = False,
  ) -> None:
    """Set IL[input_num]: drive auto-acts on input edges (e.g. halt motion).

    ``logic`` is the drive's active-low IL code; ``logic_high`` selects high polarity.
    Skips the write if the value already matches;
    settles 250ms after a real change (Elmo IL needs time to apply).
    """
    value = int(logic) + (1 if logic_high else 0)
    if await self._read_input_logic(node_id, input_num) == value:
      return
    await self._write(node_id, "IL", input_num, value)
    await asyncio.sleep(0.25)

  async def _gripper_get_homed_status(self) -> HomeStatus:
    """Read the jaw calibration state from the drive."""
    return HomeStatus(await self._query_int(Axis.SERVO_GRIPPER, "UI", 3))

  async def _gripper_set_homed_status(self, status: HomeStatus) -> None:
    """Store the jaw calibration state."""
    await self._write(Axis.SERVO_GRIPPER, "UI", 3, int(status))

  async def _gripper_reset_encoder_position(self, position: float) -> None:
    """Set the calibrated encoder datum using the homing latch."""
    sg = Axis.SERVO_GRIPPER
    await self._write(sg, "HM", 1, 0)
    await self._write(sg, "HM", 3, 0)
    await self._write(sg, "HM", 4, 0)
    await self._write(sg, "HM", 5, 0)
    # The encoder datum is a signed integer count.
    await self._write(sg, "HM", 2, int(round(position)))
    await self._write(sg, "HM", 1, 1)

  async def _gripper_hard_stop_search(
    self,
    srch_vel: int,
    srch_acc: int,
    max_pe: int,
    hs_pe: int,
    timeout: float,
  ) -> None:
    """Jog to the hard stop, observing position error and stopping on the host."""
    sg = Axis.SERVO_GRIPPER
    await self._write(sg, "ER", 3, max_pe * 10)
    await self._write(sg, "AC", 0, srch_acc)
    await self._write(sg, "DC", 0, srch_acc)
    for index in (1, 2, 3, 4, 5):
      await self._write(sg, "HM", index, 0)
    # Gold JV uses velocity control, where PE is always zero. JP retains position control.
    await self._write(sg, "JP", 0, srch_vel)
    try:
      await self._execute(sg, "BG")
      deadline = time.monotonic() + timeout
      contact_since: Optional[float] = None
      contact_position = 0
      while True:
        fault = await self.request_axis_fault(sg)
        if fault is not None:
          raise CanError(fault)
        position = await self._motor_read_position_raw(sg)
        if abs(await self._query_int(sg, "PE", 0)) >= abs(hs_pe):
          if contact_since is None or abs(position - contact_position) > max(1, abs(hs_pe) // 10):
            contact_since, contact_position = time.monotonic(), position
          elif time.monotonic() - contact_since >= 0.1:
            break
        else:
          contact_since = None
        if time.monotonic() >= deadline:
          raise TimeoutError("Gripper did not reach its hard stop")
        await asyncio.sleep(0.01)
    finally:
      await finish_io(asyncio.ensure_future(self._stop_axes([sg])))
    await self._can_sdo_download(sg, 0x6082, 0, [0, 0, 0, 0])
    # The following absolute backoff is relative to this hard-stop datum.
    await self._gripper_reset_encoder_position(0)

  async def _gripper_index_search(
    self,
    srch_vel: int,
    srch_acc: int,
    positive_direction: bool,
    timeout: float,
  ) -> tuple:
    """Search for the encoder index and return its captured position."""
    sg = Axis.SERVO_GRIPPER
    await self._write(sg, "HM", 1, 0)

    one_revolution = await self._query_int(sg, "CA", 18)
    if not positive_direction:
      one_revolution *= -1

    await self._write(sg, "PR", 1, one_revolution)
    await self._write(sg, "SP", 0, srch_vel)
    await self._write(sg, "AC", 0, srch_acc)
    await self._write(sg, "DC", 0, srch_acc)

    await self._write(sg, "HM", 3, 3)  # index only
    await self._write(sg, "HM", 4, 0)
    await self._write(sg, "HM", 5, 0)
    await self._write(sg, "HM", 2, 0)
    await self._write(sg, "HM", 1, 1)  # arm

    await self._execute(sg, "BG", 0)
    await self._wait_for_moves_done([sg], timeout)

    left = await self._query_int(sg, "HM", 1)
    if left != 0:
      raise RuntimeError("Homing Failure: Failed to finish index pulse search.")

    captured_position = await self._query_int(sg, "HM", 7)
    return one_revolution, captured_position

  async def _home_servo_gripper(self, sgc: ServoGripperConfig) -> None:
    """Hard-stop + index-pulse home for the servo gripper."""
    sg = Axis.SERVO_GRIPPER
    timeout = sgc.home_timeout_msec / 1000

    async with self._motion_guard():
      ca41 = await self._query_int(sg, "CA", 41)
      if ca41 == 24:
        raise RuntimeError(
          f"Servo gripper not ready to home (drive reports CA[41]={ca41}). "
          "Power-cycle the drive or re-check homing config."
        )

      try:
        await self._gripper_hard_stop_search(
          sgc.home_search_vel,
          sgc.home_search_accel,
          sgc.home_default_position_error,
          sgc.home_hard_stop_position_error,
          timeout,
        )
      except Exception as e:
        # motor_check_if_move_done already raised with the rich EMCY
        # description ("Motor Fault: ..."). Don't overwrite it with the
        # duller MF-bit register read.
        if str(e).startswith("Motor Fault:"):
          raise
        fault = await self.request_axis_fault(sg)
        if fault is not None:
          raise RuntimeError(fault) from e
        raise

      await self._motor_enable(node_id=sg, state=True, use_ds402=False)

      await self._motors_move_absolute_execute_locked(
        plan=MotorsMovePlan(
          moves=[
            MotorMoveParam(
              node_id=sg,
              position=sgc.home_hard_stop_offset,
              velocity=sgc.home_offset_vel,
              acceleration=sgc.home_offset_accel,
              relative=False,
              direction=JointMoveDirection.Normal,
            )
          ]
        )
      )

      is_positive = sgc.home_hard_stop_offset > 0
      await self._gripper_index_search(
        abs(sgc.home_search_vel),
        sgc.home_search_accel,
        is_positive,
        timeout,
      )

      await self._motors_move_absolute_execute_locked(
        plan=MotorsMovePlan(
          moves=[
            MotorMoveParam(
              node_id=sg,
              position=sgc.home_index_offset,
              velocity=sgc.home_offset_vel,
              acceleration=sgc.home_offset_accel,
              relative=False,
              direction=JointMoveDirection.Normal,
            )
          ]
        )
      )
      await self._gripper_reset_encoder_position(sgc.home_pos)
      await self._gripper_set_homed_status(HomeStatus.Homed)

  async def _set_servo_gripper_force_limit(self, max_force_percent: int) -> None:
    """Scale CL (continuous) and PL (peak) current limits to the given
    percentage of the gripper's full current rating. Clamped to [10, 100].

    The drive enforces an interlock: PL can be lowered to a value below CL
    only if CL is lowered first, and CL can only be raised when PL is at or
    above. So: bump PL to full → set CL → set PL to scaled.
    """
    sgc = self._cfg.servo_gripper
    if sgc is None:
      raise RuntimeError("Servo gripper not present")
    max_force_percent = max(10, min(max_force_percent, 100))

    cont_current = sgc.continuous_current * max_force_percent / 100.0
    peak_current = sgc.peak_current * max_force_percent / 100.0

    sg = Axis.SERVO_GRIPPER
    await self._write(sg, "PL", 1, sgc.peak_current)
    await self._write(sg, "CL", 1, cont_current)
    await self._write(sg, "PL", 1, peak_current)

  # -- servo gripper ------------------------------------------------------

  async def _home_gripper(self) -> None:
    """Calibrate the jaw encoder against the hard stop and index; restore temporary settings."""
    sg = Axis.SERVO_GRIPPER
    sgc = self._cfg.servo_gripper
    if sgc is None:
      raise RuntimeError("Servo gripper not present")
    async with self._motion_guard():
      if await self._query_int(sg, "HM", 1) != 0:
        raise CanError("Another gripper homing sequence is armed")
      integer_keys = [
        (sg, cmd, index)
        for cmd, index in (
          ("ER", 3),
          ("AC", 0),
          ("DC", 0),
          ("JP", 0),
          ("PR", 1),
          ("HM", 1),
          ("HM", 2),
          ("HM", 3),
          ("HM", 4),
          ("HM", 5),
        )
      ]
      async with self._preserve_settings(
        sdo=[(sg, 0x6081, 0), (sg, 0x6082, 0), (sg, 0x6060, 0)],
        integers=integer_keys,
        floats=[(sg, "PL", 1), (sg, "CL", 1)],
      ):
        await self._gripper_set_homed_status(HomeStatus.NotHomed)
        await self._motor_enable(sg, True, use_ds402=False)
        await self._set_servo_gripper_force_limit(100)
        try:
          await self._home_servo_gripper(sgc)
        finally:
          await finish_io(asyncio.ensure_future(self._stop_axes([sg])))
      # Reinitialize the position reference after restoring interpreter settings
      # so subsequent CANopen targets use the calibrated encoder datum.
      await self._motor_enable(sg, False, use_ds402=False)
      await self._motor_enable(sg, True, use_ds402=False)
      logger.info("KX2 gripper homed")

  async def _get_servo_gripper_force_fraction(self) -> float:
    """Return |IQ| / CL clamped to [0, 1] — the fraction of the configured
    continuous current the gripper is currently drawing."""
    sg = Axis.SERVO_GRIPPER
    cl = await self._query_float(sg, "CL", 1)
    iq = await self._query_float(sg, "IQ", 0)

    if cl == 0:
      return 0.0

    return max(0.0, min(abs(iq / cl), 1.0))

  async def request_axis_position(self, axis: Axis) -> float:
    """Read one calibrated axis position in millimeters or degrees."""
    raw = await self._motor_read_position_raw(
      node_id=axis,
      pu=self._cfg.axes[axis].unlimited_travel,
    )
    c = self._cfg.axes[axis].motor_conversion_factor
    if axis == Axis.ELBOW:
      return kinematics.convert_elbow_angle_to_position(self._cfg, raw / c)
    if c == 0:
      logger.warning("Axis %s has conversion factor of 0", axis)
      return 0.0
    return raw / c

  async def request_input(self, axis: Axis, input_num: int) -> bool:
    """Read the assigned digital input on an axis."""
    return await self._read_digital_input(node_id=axis, input_num=0x10 + input_num)

  async def sense_proximity(self) -> bool:
    """Read the interrupted-beam sensor between the gripper fingers."""
    return await self.request_input(self._PROXIMITY_SENSOR_AXIS, self._PROXIMITY_SENSOR_INPUT)

  async def wait_for_proximity_sensor(
    self,
    state: bool = True,
    timeout: float = 5.0,
    poll: float = 0.01,
  ) -> bool:
    """Poll until the sensor reads `state`. Returns True on trip, False on timeout."""
    deadline = time.monotonic() + timeout
    while True:
      if await self.sense_proximity() == state:
        return True
      if time.monotonic() >= deadline:
        return False
      await asyncio.sleep(poll)

  async def _finish_search(self, task: Optional[asyncio.Task], axes: List[int]) -> None:
    """Join the motion producer before stopping its axes and releasing temporary settings."""
    try:
      if task is not None:
        if not task.done():
          task.cancel()
        try:
          await task
        except asyncio.CancelledError:
          pass
    finally:
      await self._stop_axes(axes)

  async def request_joint_state(self) -> Dict[Axis, float]:
    """Read all calibrated axis positions in millimeters or degrees."""
    # Each motor_get_current_position is one BI query (one CAN round-trip,
    # ~5ms). Distinct (node_id, msg_type, msg_index) keys mean concurrent
    # in-flight requests can't collide in _send_bi's pending-future map, so
    # gather pipelines all five into one round-trip's worth of latency.
    axes = (Axis.SHOULDER, Axis.Z, Axis.ELBOW, Axis.WRIST, Axis.SERVO_GRIPPER)
    positions = await asyncio.gather(*(self.request_axis_position(ax) for ax in axes))
    return dict(zip(axes, positions))

  async def _motors_move_joint_locked(
    self,
    cmd_pos: Dict[Axis, float],
    *,
    max_gripper_speed: Optional[float] = None,
    max_gripper_acceleration: Optional[float] = None,
  ) -> None:
    """Caller MUST hold _motion_guard. Used by find_z's spawned poll/move
    task, which can't re-enter the guard from a fresh asyncio Task."""
    logger.debug("motors_move_joint cmd_pos=%s", cmd_pos)
    current = {Axis(k): v for k, v in (await self.request_joint_state()).items()}
    plan = kinematics.plan_joint_move(
      current=current,
      target=cmd_pos,
      cfg=self._cfg,
      gripper_params=self._gripper_params,
      max_gripper_speed=max_gripper_speed,
      max_gripper_acceleration=max_gripper_acceleration,
    )
    if plan is None:  # every axis a no-op
      return
    await self._motors_move_absolute_execute_locked(plan)

  async def find_z_with_proximity_sensor(
    self,
    z_start: float,
    z_end: float,
    max_gripper_speed: float = 25.0,
    max_gripper_acceleration: float = 100.0,
  ) -> float:
    """Descend between absolute Z bounds, stopping on the beam and restoring its input logic."""
    if not math.isfinite(z_start) or not math.isfinite(z_end) or z_end >= z_start:
      raise ValueError("z_end must be below finite z_start")
    async with self._motion_guard():
      await self._motors_move_joint_locked(
        {Axis.Z: z_start},
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )
      if await self.sense_proximity():
        return await self.request_axis_position(Axis.Z)
      async with self._preserve_settings(
        integers=[(self._PROXIMITY_SENSOR_AXIS, "IL", self._PROXIMITY_SENSOR_INPUT)]
      ):
        task: Optional[asyncio.Task] = None
        tripped = False
        try:
          await self._configure_input_logic(
            # Active-low reverse limit: stop descent while allowing upward withdrawal.
            self._PROXIMITY_SENSOR_AXIS,
            self._PROXIMITY_SENSOR_INPUT,
            8,
          )
          task = asyncio.create_task(
            self._motors_move_joint_locked(
              {Axis.Z: z_end},
              max_gripper_speed=max_gripper_speed,
              max_gripper_acceleration=max_gripper_acceleration,
            )
          )
          while not task.done():
            if await self.sense_proximity():
              tripped = True
              break
            await asyncio.sleep(0.01)
          if task.done():
            await task
            tripped = tripped or await self.sense_proximity()
        finally:
          await finish_io(asyncio.ensure_future(self._finish_search(task, [Axis.Z])))
      if not tripped:
        raise RuntimeError(f"Proximity sensor never tripped on Z {z_start} to {z_end}")
      return await self.request_axis_position(Axis.Z)

  async def _run_linear_path(
    self,
    target_pose: CartesianPose,
    *,
    max_gripper_speed: Optional[float],
    max_gripper_acceleration: Optional[float],
    max_gripper_angular_speed: float = 30.0,
    max_gripper_angular_acceleration: float = 100.0,
  ) -> None:
    """Sample a straight tool-tip path to ``target_pose`` and stream it.
    Caller MUST hold ``_motion_guard``."""
    if max_gripper_speed is None or max_gripper_acceleration is None:
      raise ValueError(
        "move_to_location(path='linear') requires max_gripper_speed and "
        "max_gripper_acceleration: the Cartesian profile is built from them "
        "directly (no firmware fallback for streamed motion)."
      )
    current_joints = {Axis(k): v for k, v in (await self.request_joint_state()).items()}
    start_pose = kinematics.fk(current_joints, self._cfg, self._gripper_params)
    samples = kinematics.sample_linear_path(
      cfg=self._cfg,
      gripper_params=self._gripper_params,
      start_pose=start_pose,
      end_pose=target_pose,
      vel_mm_per_s=max_gripper_speed,
      accel_mm_per_s2=max_gripper_acceleration,
      dt_s=self._PVT_DT_MS / 1000.0,
      current_joints=current_joints,
      angular_speed=max_gripper_angular_speed,
      angular_acceleration=max_gripper_angular_acceleration,
    )
    await self._stream_samples(samples)

  async def request_gripper_pose(self) -> CartesianPose:
    """Compute the grip-center pose from measured calibrated joints."""
    joints = {Axis(k): v for k, v in (await self.request_joint_state()).items()}
    return kinematics.fk(joints, self._cfg, self._gripper_params)

  async def _cart_to_joints(self, pose: CartesianPose) -> Dict[Axis, float]:
    """Cartesian -> joints, snapping rotary axes to whichever 360° wrap is
    closest to the current joint position."""
    current = {Axis(k): v for k, v in (await self.request_joint_state()).items()}
    ik_joints = kinematics.ik(pose, self._cfg, self._gripper_params)
    return kinematics.snap_to_current(ik_joints, current)

  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    *,
    max_gripper_speed: Optional[float] = None,
    max_gripper_acceleration: Optional[float] = None,
    path: Literal["joint", "linear"] = "joint",
    max_gripper_angular_speed: Optional[float] = None,
    max_gripper_angular_acceleration: Optional[float] = None,
  ) -> None:
    """Move the gripper to a Cartesian ``location`` with yaw ``direction`` (deg).

    ``max_gripper_speed`` / ``max_gripper_acceleration`` cap the worst-case
    Cartesian speed/acceleration at the gripper (mm/s, mm/s^2). ``None`` runs
    joints at firmware max with no Cartesian cap; otherwise joint speeds are
    scaled uniformly so the gripper stays under the cap.

    ``path`` selects the trajectory shape:
      - ``"joint"`` (default): every axis ramps in parallel through its own
        trapezoid. Tool tip traces a curvy path; speed is firmware-bounded
        with the optional gripper-speed cap.
      - ``"linear"``: tool tip traces a straight line from current to target
        pose, sampled and streamed via the drive's interpolation buffer (PVT).
        Requires ``max_gripper_speed`` and ``max_gripper_acceleration`` — they
        directly drive the Cartesian profile (no cap == no speed to stream).
        Yaw is independently limited by ``max_gripper_angular_speed`` (deg/s,
        default 30) and ``max_gripper_angular_acceleration`` (deg/s², default 100).
        Rotation-only moves hold the grip center fixed. Angular limits apply
        only to ``path="linear"``.
    """
    if path not in ("joint", "linear") or not all(
      math.isfinite(v) for v in (location.x, location.y, location.z, direction)
    ):
      raise ValueError("A finite Cartesian target and path='joint' or 'linear' are required")
    if path == "joint" and (
      max_gripper_angular_speed is not None or max_gripper_angular_acceleration is not None
    ):
      raise ValueError("Angular limits require path='linear'")
    pose = CartesianPose(location=location, rotation=Rotation(z=direction))
    if path == "linear":
      async with self._motion_guard():
        await self._run_linear_path(
          pose,
          max_gripper_speed=max_gripper_speed,
          max_gripper_acceleration=max_gripper_acceleration,
          max_gripper_angular_speed=(
            30.0 if max_gripper_angular_speed is None else max_gripper_angular_speed
          ),
          max_gripper_angular_acceleration=(
            100.0 if max_gripper_angular_acceleration is None else max_gripper_angular_acceleration
          ),
        )
      return
    async with self._motion_guard():
      joint_pos = await self._cart_to_joints(pose)
      await self._motors_move_joint_locked(
        cmd_pos=joint_pos,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )

  async def find_with_proximity_sensor(
    self,
    start: Coordinate,
    end: Coordinate,
    *,
    max_gripper_speed: float = 25.0,
    max_gripper_acceleration: float = 100.0,
  ) -> Coordinate:
    """Sweep a straight Cartesian segment until the beam trips; retain the measured yaw.

    Sensor polling and adapter latency affect stopping distance. Use the Z search when the
    drive's local input stop is needed. No unrelated fault is cleared after a search.
    """
    async with self._motion_guard():
      yaw = (await self.request_gripper_pose()).rotation.z
      await self.move_to_location(
        start,
        yaw,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )
      if await self.sense_proximity():
        return (await self.request_gripper_pose()).location
      task = asyncio.create_task(
        self._run_linear_path(
          CartesianPose(end, Rotation(z=yaw)),
          max_gripper_speed=max_gripper_speed,
          max_gripper_acceleration=max_gripper_acceleration,
        )
      )
      tripped = False
      try:
        while not task.done():
          if await self.sense_proximity():
            tripped = True
            break
          await asyncio.sleep(0.01)
        if task.done():
          await task
          tripped = tripped or await self.sense_proximity()
      finally:
        await finish_io(asyncio.ensure_future(self._finish_search(task, list(MOTION_AXES))))
      if not tripped:
        raise RuntimeError("Proximity sensor never tripped along the Cartesian search")
      return (await self.request_gripper_pose()).location

  async def _motors_move_joint(
    self,
    cmd_pos: Dict[Axis, float],
    *,
    max_gripper_speed: Optional[float] = None,
    max_gripper_acceleration: Optional[float] = None,
  ) -> None:
    """Serialize and execute absolute calibrated joint targets."""
    async with self._motion_guard():
      await self._motors_move_joint_locked(
        cmd_pos,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )

  async def _motors_move_absolute_execute(self, plan: MotorsMovePlan) -> None:
    """Serialize and execute a prepared encoder-space motion plan."""
    async with self._motion_guard():
      await self._motors_move_absolute_execute_locked(plan)

  async def move_parametric(
    self,
    path_fn: "Callable[[float], CartesianPose]",
    duration: float,
  ) -> None:
    """Stream a parametric Cartesian trajectory through IPM.

    ``path_fn(t)`` is called at every IPM sample time ``t ∈ [0, duration]``
    (seconds, evaluated at the drive's interpolation cadence, currently
    8 ms) and must return the absolute :class:`CartesianPose` the gripper
    should occupy at that instant. Use the start pose if you need offsets::

      start = await arm.request_gripper_pose()
      def fig8(t):
        s = t / duration
        theta = 2 * math.pi * s
        return CartesianPose(
          location=Coordinate(start.location.x + 50 * math.sin(theta),
                              start.location.y + 12.5 * math.sin(2*theta),
                              start.location.z + 30 * math.sin(math.pi * s)),
          rotation=start.rotation,
        )
      await arm.move_parametric(fig8, duration=8.0)

    No speed/accel cap — the parametrization sets the velocity profile;
    callers are responsible for keeping derivatives within drive limits.
    Joint travel limits are enforced via IK; out-of-range raises IKError
    before any motion. Path continuity is the caller's responsibility —
    discontinuities surface as drive faults.
    """
    if not math.isfinite(duration) or duration <= 0:
      raise ValueError(f"duration must be > 0, got {duration}")
    async with self._motion_guard():
      current_joints = {Axis(k): v for k, v in (await self.request_joint_state()).items()}
      samples = kinematics.sample_parametric_path(
        cfg=self._cfg,
        gripper_params=self._gripper_params,
        path_fn=path_fn,
        duration_s=duration,
        dt_s=self._PVT_DT_MS / 1000.0,
        current_joints=current_joints,
      )
      await self._stream_samples(samples)

  async def move_through_waypoints(
    self,
    waypoints: "List[CartesianPose]",
    *,
    speed: float,
    acceleration: float,
  ) -> None:
    """Stream a smooth Catmull-Rom spline through ``waypoints``.

    The curve passes through every waypoint with C¹ continuity (no stop at
    intermediate waypoints). Tangents at each interior waypoint are derived
    from neighbours; endpoint tangents are extrapolated from the first/last
    segment. The whole spline is time-reparametrized into a trapezoidal
    arc-length profile with peak Cartesian speed ``speed`` (mm/s) and peak
    acceleration ``acceleration`` (mm/s²), then sampled at the IPM cadence.

    Args:
      waypoints: at least 2 absolute Cartesian poses. The first should
        match the current pose closely (or the path will start with a
        Cartesian-linear segment to get there).
      speed: peak Cartesian speed along the spline, mm/s.
      acceleration: peak Cartesian acceleration, mm/s².

    Raises:
      ValueError if waypoints < 2 or speed/acceleration <= 0.
      IKError if any sample's joints are out of range.
    """
    if len(waypoints) < 2:
      raise ValueError(f"need at least 2 waypoints, got {len(waypoints)}")
    if not all(math.isfinite(v) and v > 0 for v in (speed, acceleration)):
      raise ValueError(f"speed and accel must be > 0, got {speed}, {acceleration}")
    async with self._motion_guard():
      current_joints = {Axis(k): v for k, v in (await self.request_joint_state()).items()}
      samples = kinematics.sample_waypoint_path(
        cfg=self._cfg,
        gripper_params=self._gripper_params,
        waypoints=waypoints,
        speed_mm_per_s=speed,
        accel_mm_per_s2=acceleration,
        dt_s=self._PVT_DT_MS / 1000.0,
        current_joints=current_joints,
      )
      await self._stream_samples(samples)

  # -- high-level arm API --

  async def halt(self) -> None:
    """Cancel active motion and stop the positioning axes, preserving gripper holding power."""
    self._faulted = True
    try:
      owner = self._motion_owner
      if owner is not None and owner is not asyncio.current_task():
        owner.cancel()
        await finish_io(asyncio.ensure_future(self._join_motion(owner)))
    finally:
      await finish_io(asyncio.ensure_future(self._stop_axes(list(MOTION_AXES))))

  async def move_to_joint_state(
    self,
    joint_state: Dict[Axis, float],
    *,
    max_gripper_speed: Optional[float] = None,
    max_gripper_acceleration: Optional[float] = None,
    close_gripper_without_force_sensing: bool = False,
  ) -> None:
    """Move absolute joint targets; omitted axes stay at their measured positions.

    Z and elbow use millimeters; shoulder and wrist use degrees. A jaw-closing target
    requires an explicit override because coordinated joint moves do not sense contact.
    """
    if any(
      not isinstance(axis, Axis) or axis == Axis.RAIL or not math.isfinite(value)
      for axis, value in joint_state.items()
    ):
      raise ValueError("joint_state requires Axis keys and finite values")
    async with self._motion_guard():
      current = await self.request_joint_state()
      target = {**current, **joint_state}
      if (
        not close_gripper_without_force_sensing
        and target[Axis.SERVO_GRIPPER] < current[Axis.SERVO_GRIPPER]
      ):
        raise ValueError(
          "Close the jaws using gripper.move_to_jaw_position, or explicitly allow a position close"
        )
      await self._motors_move_joint_locked(
        joint_state,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )

  async def park(self) -> None:
    """Move to the configured park target, leaving the gripper unchanged."""
    target = self.parking_position
    if target is None:
      raise ValueError("Set parking_position to a calibrated, clear pose before calling park()")
    await self.move_to_joint_state(
      target, max_gripper_speed=self._PARK_SPEED, max_gripper_acceleration=self._PARK_ACCEL
    )

  async def pick_up_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    *,
    max_gripper_speed: Optional[float] = None,
    max_gripper_acceleration: Optional[float] = None,
    check_plate_gripped: bool = True,
    max_force_percent: Optional[int] = None,
    max_jaw_speed: Optional[float] = None,
    max_jaw_acceleration: Optional[float] = None,
  ) -> None:
    """Move to the Cartesian target and grip using the supplied jaw width."""
    async with self._motion_guard():
      await self.move_to_location(
        location,
        direction,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )
      await self.gripper.move_to_jaw_position(
        resource_width,
        max_force_percent=max_force_percent,
        check_plate_gripped=check_plate_gripped,
        max_speed=max_jaw_speed,
        max_acceleration=max_jaw_acceleration,
      )

  async def drop_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    *,
    max_gripper_speed: Optional[float] = None,
    max_gripper_acceleration: Optional[float] = None,
    max_jaw_speed: Optional[float] = None,
    max_jaw_acceleration: Optional[float] = None,
  ) -> None:
    """Move to the Cartesian target and release to the supplied jaw width."""
    async with self._motion_guard():
      await self.move_to_location(
        location,
        direction,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )
      await self.gripper.move_to_jaw_position(
        resource_width,
        force_sensing=False,
        max_speed=max_jaw_speed,
        max_acceleration=max_jaw_acceleration,
      )

  async def pick_up_at_joint_state(
    self,
    joint_state: Dict[Axis, float],
    resource_width: float,
    *,
    max_gripper_speed: Optional[float] = None,
    max_gripper_acceleration: Optional[float] = None,
    check_plate_gripped: bool = True,
    max_force_percent: Optional[int] = None,
    max_jaw_speed: Optional[float] = None,
    max_jaw_acceleration: Optional[float] = None,
  ) -> None:
    """Move to the joint target and grip using the supplied jaw width."""
    async with self._motion_guard():
      await self.move_to_joint_state(
        joint_state,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )
      await self.gripper.move_to_jaw_position(
        resource_width,
        max_force_percent=max_force_percent,
        check_plate_gripped=check_plate_gripped,
        max_speed=max_jaw_speed,
        max_acceleration=max_jaw_acceleration,
      )

  async def drop_at_joint_state(
    self,
    joint_state: Dict[Axis, float],
    resource_width: float,
    *,
    max_gripper_speed: Optional[float] = None,
    max_gripper_acceleration: Optional[float] = None,
    max_jaw_speed: Optional[float] = None,
    max_jaw_acceleration: Optional[float] = None,
  ) -> None:
    """Move to the joint target and release to the supplied jaw width."""
    async with self._motion_guard():
      await self.move_to_joint_state(
        joint_state,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )
      await self.gripper.move_to_jaw_position(
        resource_width,
        force_sensing=False,
        max_speed=max_jaw_speed,
        max_acceleration=max_jaw_acceleration,
      )

  def motion_limits(self) -> "_MotionLimits":
    """Per-axis (max_speed, max_acceleration) read from the drives at setup.

    Linear axes (Z, rail, servo gripper) are mm/s, mm/s^2; rotary axes
    (shoulder, elbow, wrist) are deg/s, deg/s^2. These are the upper bounds the
    move methods' ``max_gripper_speed`` / ``max_gripper_acceleration`` get
    clamped to. Returned as a dict subclass that renders as a table in Jupyter
    and plain-text columns in a terminal.
    """
    return _MotionLimits(
      {k: (cfg.max_vel, cfg.max_accel) for k, cfg in self._cfg.axes.items()},
    )

  async def _restore_freedrive(self) -> None:
    """Restore motors affected by teaching without clearing a fault or e-stop."""
    if self._faulted or await self.request_estop():
      raise CanError("Cannot restore freedrive while stopped or faulted")
    for axis, enabled in tuple(self._freedrive_axes.items()):
      await self._motor_enable(axis, enabled, use_ds402=True)
      del self._freedrive_axes[axis]

  async def start_freedrive_mode(self, free_axes: Optional[List[Axis]] = None) -> None:
    """Disable the selected positioning motors for teaching, retaining their prior enable states."""
    axes = list(MOTION_AXES) if free_axes is None else free_axes
    if any(axis not in MOTION_AXES for axis in axes):
      raise ValueError("Freedrive accepts only the four positioning axes")
    async with self._motion_guard():
      try:
        for axis in axes:
          if axis not in self._freedrive_axes:
            self._freedrive_axes[axis] = await self.request_axis_enabled(axis)
          await self._motor_enable(axis, False, use_ds402=True)
      except BaseException:
        await finish_io(asyncio.ensure_future(self._restore_freedrive()))
        raise

  async def stop_freedrive_mode(self) -> None:
    """Return each taught axis to its enable state from before freedrive."""
    async with self._motion_guard():
      if self._freedrive_axes:
        await self._restore_freedrive()
