"""PAA KX2 robotic plate handler — single self-contained hardware class.

One class, :class:`KX2`, owns the whole robot: the CANopen/DS402 CAN transport
and Elmo binary-interpreter primitives (formerly ``KX2Driver``), the
robot-level motion/homing/gripper logic and kinematics glue (formerly
``KX2ArmBackend``), and the top-level ``setup``/``stop`` lifecycle. It is not a
PLR ``Device``/``Driver``/capability backend — it is a plain class written
specifically for this hardware.

Pure wire-protocol definitions live in :mod:`~pylabrobot.paa.kx2.protocol`;
frame/joint kinematics in :mod:`~pylabrobot.paa.kx2.kinematics`; drive-read
calibration in :mod:`~pylabrobot.paa.kx2.config`. The onboard barcode reader is
a separate serial device (:mod:`~pylabrobot.paa.kx2.barcode_reader`); when a
port is given, :class:`KX2` owns one and manages its lifecycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
import sys
import time
import warnings
from contextlib import asynccontextmanager
from enum import IntEnum
from typing import Callable, Dict, List, Literal, Optional, Tuple, Union

from pylabrobot.capabilities.arms.standard import CartesianPose
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
from pylabrobot.paa.kx2.protocol import (
  COBType,
  CanError,
  EmcyFrame,
  JointMoveDirection,
  MotorMoveParam,
  MotorsMovePlan,
  PDOTransmissionType,
  RPDO,
  RPDOMappedObject,
  TPDO,
  TPDOMappedObject,
  TPDOTrigger,
  _BI_REQUEST_COB_BASE,
  _BI_RESPONSE_COB_BASE,
  _ElmoObjectDataType,
  _EMCY_COB_BASE,
  _GROUP_NODE_ID,
  _InputLogic,
  _NodeEmcyState,
  _TPDO3_COB_BASE,
  _decode_emcy,
  _u32_le,
)
from pylabrobot.resources import Coordinate, Rotation

try:
  import canopen

  _HAS_CANOPEN = True
except ImportError as _e:
  _HAS_CANOPEN = False
  _CANOPEN_IMPORT_ERROR = _e

logger = logging.getLogger(__name__)


class HomeStatus(IntEnum):
  NotHomed = 0
  Homed = 1
  InitializedWithoutHoming = 2


# Tuple of motion axes — derived from `Axis.is_motion`, kept around because
# iteration sites (setup, halt, freedrive) want a stable ordering.
MOTION_AXES = tuple(a for a in Axis if a.is_motion)


class KX2:
  """PAA KX2 robotic plate handler.

  A single class spanning CAN transport + Elmo drive primitives and the
  robot-level arm/gripper/homing/kinematics logic. Construct it, ``await
  setup()``, drive it, ``await stop()``.
  """

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
  ) -> None:
    """
    Args:
      has_rail: True if the arm is mounted on the optional linear rail (axis 5).
      has_servo_gripper: True if a servo-driven plate gripper is on the bus
        (axis 6).
      interface: python-can interface name for the CANopen bus (default
        ``"pcan"``).
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
    # The non-default topologies (rail-mounted KX2, gripper-less KX2)
    # have shim code paths in this class, but neither
    # has been exercised against real hardware. KX2._arm_setup
    # also calls servo_gripper_initialize unconditionally. Refuse the
    # configuration up front rather than letting users hit cryptic
    # failures downstream.
    if has_rail or not has_servo_gripper:
      raise NotImplementedError(
        "KX2 has only been tested with the default 4-axis arm + servo "
        "gripper topology (has_rail=False, has_servo_gripper=True). "
        "Other configurations have shim code paths but the setup / "
        "homing layer needs work — see KX2._arm_setup and "
        "servo_gripper_initialize."
      )
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

    self._network: Optional[canopen.Network] = None
    self._nodes: Dict[int, canopen.RemoteNode] = {}
    self._loop: Optional[asyncio.AbstractEventLoop] = None

    # Pending binary-interpreter response futures keyed by
    # (node_id, msg_type, msg_index). Set from the canopen listener thread
    # via loop.call_soon_threadsafe; only the event-loop thread touches
    # this dict directly.
    self._pending_bi: Dict[Tuple[int, str, int], asyncio.Future] = {}

    # Per-(node, cmd, idx) lock around the future-install + send + await
    # cycle in _send_bi. Without it, two coroutines firing the same
    # request would clobber each other's pending-future entry; with the
    # motion lock above mitigating most of it but direct driver callers
    # (notebook diagnostics) and gathered queries with overlapping keys
    # still hit the race. setdefault(asyncio.Lock()) is fine because the
    # garbage Lock from a missed-race insert is immediately discarded.
    self._bi_locks: Dict[Tuple[int, str, int], asyncio.Lock] = {}

    self._ipm_mode: bool = False

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
    self._emcy_callbacks: List[
      Callable[[int, EmcyFrame, str, bool], None]
    ] = []

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
    self._freedrive_axes: List[int] = []
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

  @property
  def setup_finished(self) -> bool:
    return self._setup_finished

  # --- lifecycle -----------------------------------------------------------

  async def setup(self) -> None:
    """Bring the whole KX2 up: barcode reader (if any) -> CAN bus -> arm.

    Idempotent: re-running setup on a live KX2 stays up. Call stop() first to
    force a fresh init.
    """
    if self._setup_finished:
      logger.info("KX2.setup: already set up; skipping. Call stop() first to re-init.")
      return
    # Bring the barcode reader up first (open port + version handshake) so a
    # bad reader aborts before we touch the CAN bus. It rides on a separate
    # serial port with no dependency on the drives.
    if self._bcr is not None:
      await self._bcr.setup()
    try:
      await self._bus_setup()
      await self._arm_setup()
    except BaseException:
      if self._bcr is not None:
        try:
          await self._bcr.stop()
        except Exception:
          logger.exception("KX2.setup cleanup: barcode reader stop failed; ignoring")
      raise
    self._setup_finished = True

  async def stop(self) -> None:
    """Tear down the CAN bus and the barcode reader (if any)."""
    try:
      await self._bus_stop()
    finally:
      if self._bcr is not None:
        await self._bcr.stop()
      self._setup_finished = False

  async def _arm_setup(self) -> None:
    """Robot bring-up after the CAN bus is live: read per-drive config, enable
    the motion axes, and home + close the servo gripper.

    If anything below raises, tear the CAN bus down so a retry can re-init.
    Otherwise the second setup() trips PcanCanInitializationError because the
    channel is still half-claimed from the first attempt.
    """
    try:
      self._config = await self._read_config()
      await asyncio.sleep(2)  # let drives settle before motor enables

      # Subscribe to drive EMCY frames so faults log immediately and motor
      # disables are scheduled the moment a fault is reported, rather than
      # waiting for the next motion call to poll motor_get_fault. Mirrors
      # KX2RobotControl.cs:15384-15425.
      self.add_emcy_callback(self._on_emcy)

      # E-stop check: front-load a clear error before motor_enable's retry
      # loop times out with a cryptic message.
      if await self.get_estop_state():
        raise RuntimeError(
          "KX2 setup failed: E-stop is engaged. Twist the red button to "
          "release, then call setup() again. (If the button is out, the "
          "safety-interlock loop or motor-power switch may also be open.)"
        )

      await self.motors_ensure_enabled([int(a) for a in MOTION_AXES])
      await self.servo_gripper_initialize()
    except BaseException:
      try:
        await self._bus_stop()
      except Exception:
        logger.exception("KX2 setup cleanup: _bus_stop() failed; ignoring")
      raise

  async def scan_barcode(self, read_time: Optional[float] = None):
    """Fire the onboard barcode reader and return the decoded ``Barcode`` (or
    ``None`` on a no-read). Raises if the KX2 was constructed without a
    ``barcode_port``.
    """
    if self._bcr is None:
      raise RuntimeError(
        "KX2 was constructed without barcode_port; no barcode reader configured."
      )
    return await self._bcr.scan_barcode(read_time)

  # --- reentrant motion guard ----------------------------------------------

  @asynccontextmanager
  async def _motion_guard(self):
    current = asyncio.current_task()
    if self._motion_owner is current:
      yield
      return
    async with self._motion_lock:
      self._motion_owner = current
      try:
        yield
      finally:
        self._motion_owner = None

  # --- EMCY auto-disable ---------------------------------------------------

  def _on_emcy(
    self, node_id: int, frame: EmcyFrame, description: str, disable_motors: bool
  ) -> None:
    """EMCY callback. Runs on the asyncio loop thread.

    Logs every EMCY at error level. When the drive flagged a fatal fault
    (``disable_motors=True``), schedules an MO=0 on every motion axis as a
    coroutine — mirrors the C# motor-disable in KX2RobotControl.cs:15395-15404
    minus the indicator-light I/O (no PLR analog).
    """
    logger.error(
      "KX2 EMCY axis=%d code=0x%04X elmo=0x%02X: %s",
      node_id, frame.err_code, frame.elmo_err_code, description,
    )
    if not disable_motors:
      return

    async def _disable_all() -> None:
      for axis in MOTION_AXES:
        try:
          await self.motor_emergency_stop(node_id=axis)
        except Exception:
          logger.exception("EMCY auto-disable failed for axis %s", axis.name)

    # _on_emcy is invoked from _dispatch_emcy, which already runs on the
    # asyncio loop (it's the target of call_soon_threadsafe). Schedule on the
    # driver's captured loop — get_event_loop() is deprecated and unreliable.
    self.loop.create_task(_disable_all())

  # --- drive-read properties -----------------------------------------------

  @property
  def loop(self) -> asyncio.AbstractEventLoop:
    """Event loop captured in setup(). Raises if accessed before setup()."""
    if self._loop is None:
      raise RuntimeError("KX2 event loop not initialized; call setup() first.")
    return self._loop

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

  @property
  def last_emcy(self) -> Optional[EmcyFrame]:
    """Most recent EMCY frame received from any node; ``None`` if none yet.

    Diagnostic only — motion logic uses `emcy_move_error` for fault
    detection and `_emcy[nid]` for per-axis IPM queue state."""
    if self._last_emcy_node_id is None:
      return None
    st = self._emcy.get(self._last_emcy_node_id)
    return st.last_frame if st is not None else None

  async def _bus_setup(self) -> None:
    """Bring up the CAN bus, reset/start nodes, and configure PDO mapping."""
    if not _HAS_CANOPEN:
      raise ImportError(
        "canopen is not installed. Install with `pip install pylabrobot[canopen]` "
        f"(import error: {_CANOPEN_IMPORT_ERROR})"
      )
    if self._network is not None:
      await self._bus_stop()

    self._loop = asyncio.get_running_loop()

    network = canopen.Network()
    network.connect(interface=self._interface, channel=self._channel, bitrate=self._bitrate)
    self._network = network

    # Subscribe to EMCY before Start All Nodes — bootup / fault frames
    # emitted between NMT start and per-node setup would otherwise be lost
    # (canopen's listener doesn't buffer pre-subscribe messages). Mirrors
    # the C# event handler at KX2RobotControl.cs:15384-15425 /
    # clscanmotor.cs:1057-1284.
    for nid in self.node_id_list:
      self._emcy[nid] = _NodeEmcyState()
      network.subscribe(_EMCY_COB_BASE + nid, self._make_emcy_callback(nid))

    # Reset all nodes, then start scanner so bootup messages populate it,
    # then start all nodes.
    network.nmt.send_command(0x82)
    await asyncio.sleep(0.5)
    network.scanner.search()
    network.nmt.send_command(0x01)
    await asyncio.sleep(0.5)

    discovered = sorted(network.scanner.nodes)
    if discovered != self.node_id_list:
      raise CanError(
        f"Node IDs on CAN bus do not match expected list: "
        f"{discovered} != {self.node_id_list}"
      )

    for nid in self.node_id_list:
      node = network.add_node(nid, canopen.ObjectDictionary())
      # canopen's default SDO response timeout is 0.3s, which is tight for
      # drives that queue vendor objects (Elmo 0x20xx/0x30xx). Match the 1s
      # the legacy driver used for its own futures.
      node.sdo.RESPONSE_TIMEOUT = 1.0
      self._nodes[nid] = node
      # Elmo binary-interpreter response subscription. BI traffic only
      # happens after explicit user commands, so subscribing here is fine.
      network.subscribe(_BI_RESPONSE_COB_BASE + nid, self._make_bi_callback(nid))

    logger.info("canopen: connected, nodes=%s", discovered)

    # TPDO3 push for StatusWord (0x6041) so _wait_setpoint_ack can wait on
    # the bit-12 transition without an SDO round-trip per poll. Subscribe
    # before remapping so we don't lose the first frame the drive emits
    # when the new event-trigger arms. 1 ms inhibit (10 * 100 us) caps
    # bus traffic — SW changes happen at the ~1-2 ms servo cycle, so the
    # inhibit doesn't lose edges in practice and keeps the bus quiet
    # during IPM streaming if anyone resurrects the IPM runtime.
    for nid in self.motion_node_ids:
      self._statusword_event[nid] = asyncio.Event()
      network.subscribe(_TPDO3_COB_BASE + nid, self._make_tpdo3_callback(nid))

    # Unmap TPDO1, map TPDO3 (StatusWord, triggered on any SW change) and
    # TPDO4 (DigitalInputs, triggered on edge).
    for node_id in self.node_id_list:
      await self._can_tpdo_unmap(TPDO.TPDO1, node_id)
      await self._tpdo_map(
        TPDO.TPDO3, node_id, [TPDOMappedObject.StatusWord],
        TPDOTrigger.StatusWordEvent, delay_100_us=10,
      )
      await self._tpdo_map(
        TPDO.TPDO4, node_id, [TPDOMappedObject.DigitalInputs], TPDOTrigger.DigitalInputEvent
      )

    # Elmo vendor objects: interpolation config for IPM.
    for nid in self.motion_node_ids:
      await self.can_sdo_download_elmo_object(nid, 24768, 0, -1, _ElmoObjectDataType.INTEGER16)
      await self.can_sdo_download_elmo_object(nid, 24772, 2, 16, _ElmoObjectDataType.UNSIGNED32)
      await self.can_sdo_download_elmo_object(nid, 24772, 3, 0, _ElmoObjectDataType.UNSIGNED8)
      await self.can_sdo_download_elmo_object(nid, 24772, 5, 8, _ElmoObjectDataType.UNSIGNED8)
      await self.can_sdo_download_elmo_object(nid, 24770, 2, -3, _ElmoObjectDataType.INTEGER8)
      await self.can_sdo_download_elmo_object(nid, 24669, 0, 1, _ElmoObjectDataType.INTEGER16)

    # RPDO1 = ControlWord (for DS402 enable), RPDO3 = interpolated target.
    for nid in self.motion_node_ids:
      await self._rpdo_map(
        RPDO.RPDO1, nid, [RPDOMappedObject.ControlWord],
        PDOTransmissionType.SynchronousCyclic,
      )
      await self._rpdo_map(
        RPDO.RPDO3, nid,
        [RPDOMappedObject.TargetPositionIP, RPDOMappedObject.TargetVelocityIP],
        PDOTransmissionType.EventDrivenDev,
      )

    self._ipm_mode = True
    await self.ipm_select_mode(False)

  async def _bus_stop(self) -> None:
    if self._network is not None:
      # Drop _loop first so racing listener-thread _cb()s no-op at their
      # `if self._loop is None: return` guard before they try to schedule
      # onto a torn-down loop. Network reference clears after disconnect.
      self._loop = None
      self._network.disconnect()
      self._network = None
      self._nodes = {}
      self._emcy = {}
      self._last_emcy_node_id = None
      # Clear callbacks too: _on_setup re-registers on each retry, so leaving
      # them would compound N copies of the same handler across attempts.
      self._emcy_callbacks = []
      self._statusword = {}
      self._statusword_event = {}


  # ======================================================================
  # CAN transport + Elmo drive primitives
  # ======================================================================

  # --- PDO configuration (pure SDO writes; no library-PDO machinery) ------

  async def _can_tpdo_unmap(self, tpdo: TPDO, node_id: int) -> None:
    cob_type_int = {
      TPDO.TPDO1: COBType.TPDO1.value,
      TPDO.TPDO3: COBType.TPDO3.value,
      TPDO.TPDO4: COBType.TPDO4.value,
    }[tpdo]
    node_id &= 0x7F
    num1 = ((cob_type_int & 0x01) << 7) | node_id
    num2 = (cob_type_int >> 1) & 0x07
    await self._can_sdo_download(node_id, 0x1800 + tpdo.value - 1, 1, [num1, num2, 0, 0xC0])
    await self._can_sdo_download(node_id, 0x1A00 + tpdo.value - 1, 0, [0, 0, 0, 0])

  async def _rpdo_map(
    self,
    rpdo: RPDO,
    node_id: int,
    mapped_objects: List[RPDOMappedObject],
    transmission_type: PDOTransmissionType,
  ) -> None:
    rpdo_idx = (int(rpdo) - 1) & 0xFF
    cob_type = {
      RPDO.RPDO1: COBType.RPDO1, RPDO.RPDO3: COBType.RPDO3, RPDO.RPDO4: COBType.RPDO4,
    }[rpdo]
    cob_id_11 = ((int(cob_type) & 0x0F) << 7) | (node_id & 0x7F)

    # Disable PDO (bit 31 set)
    await self._can_sdo_download(node_id, 0x1400 + rpdo_idx, 1, _u32_le(0x80000000 | cob_id_11))
    # Clear mapping count
    await self._can_sdo_download(node_id, 0x1600 + rpdo_idx, 0, [0, 0, 0, 0])
    # Transmission type
    await self._can_sdo_download(
      node_id, 0x1400 + rpdo_idx, 2, [int(transmission_type) & 0xFF, 0, 0, 0]
    )
    # Mapped objects
    for i, mo in enumerate(mapped_objects):
      await self._can_sdo_download(node_id, 0x1600 + rpdo_idx, i + 1, _u32_le(int(mo)))
    # Mapping count
    await self._can_sdo_download(
      node_id, 0x1600 + rpdo_idx, 0, [len(mapped_objects) & 0xFF, 0, 0, 0]
    )
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
    tpdo_idx = (int(tpdo) - 1) & 0xFF
    cob_type = {
      TPDO.TPDO1: COBType.TPDO1, TPDO.TPDO3: COBType.TPDO3, TPDO.TPDO4: COBType.TPDO4,
    }[tpdo]
    cob_id_11 = ((int(cob_type) & 0x0F) << 7) | (node_id & 0x7F)
    event_mask = 1 << int(event_trigger)

    # Disable TPDO (bit 30 + 31)
    await self._can_sdo_download(node_id, 0x1800 + tpdo_idx, 1, _u32_le(0xC0000000 | cob_id_11))
    # Clear mapping count
    await self._can_sdo_download(node_id, 0x1A00 + tpdo_idx, 0, [0, 0, 0, 0])
    # Transmission type
    await self._can_sdo_download(
      node_id, 0x1800 + tpdo_idx, 2, [int(transmission_type) & 0xFF, 0, 0, 0]
    )
    # Inhibit / delay 100us
    await self._can_sdo_download(node_id, 0x1800 + tpdo_idx, 3, [delay_100_us & 0xFF, 0, 0, 0])
    # Event timer (ms)
    await self._can_sdo_download(node_id, 0x1800 + tpdo_idx, 5, [event_timer_ms & 0xFF, 0, 0, 0])
    # Vendor event mask at 0x2F20:<tpdo_num>
    await self._can_sdo_download(node_id, 0x2F20, int(tpdo) & 0xFF, _u32_le(event_mask))
    # Mapped objects
    for i, mo in enumerate(mapped_objects):
      await self._can_sdo_download(node_id, 0x1A00 + tpdo_idx, i + 1, _u32_le(int(mo)))
    # Mapping count
    await self._can_sdo_download(
      node_id, 0x1A00 + tpdo_idx, 0, [len(mapped_objects) & 0xFF, 0, 0, 0]
    )
    # Re-enable (clear bits 30 + 31)
    await self._can_sdo_download(node_id, 0x1800 + tpdo_idx, 1, _u32_le(cob_id_11))

  # --- SDO -----------------------------------------------------------------

  async def _can_sdo_upload(
    self, node_id: int, index: int, sub_index: int,
  ) -> bytes:
    # node.sdo.upload is blocking I/O (library handles expedited + segmented
    # transfers + abort codes); run off the event loop.
    return await asyncio.to_thread(self._nodes[node_id].sdo.upload, index, sub_index)

  async def _can_sdo_download(
    self, node_id: int, index: int, sub_index: int, data: List[int],
  ) -> None:
    await asyncio.to_thread(
      self._nodes[node_id].sdo.download, index, sub_index, bytes(data),
    )

  async def can_sdo_download_elmo_object(
    self,
    node_id: int,
    elmo_object_int: int,
    sub_index: int,
    data: Union[int, float],
    data_type: _ElmoObjectDataType,
  ) -> None:
    # Byte width + signedness derived from data_type; float inputs truncate to int.
    _SDO_ELMO_PACK = {
      _ElmoObjectDataType.UNSIGNED8:  (1, False),
      _ElmoObjectDataType.UNSIGNED16: (2, False),
      _ElmoObjectDataType.UNSIGNED32: (4, False),
      _ElmoObjectDataType.UNSIGNED64: (8, False),
      _ElmoObjectDataType.INTEGER8:   (1, True),
      _ElmoObjectDataType.INTEGER16:  (2, True),
      _ElmoObjectDataType.INTEGER32:  (4, True),
      _ElmoObjectDataType.INTEGER64:  (8, True),
    }
    spec = _SDO_ELMO_PACK.get(data_type)
    if spec is None:
      raise CanError(f"Unsupported data type for SDO Write: {data_type.name}")
    width, signed = spec
    data_bytes = list(int(data).to_bytes(width, "little", signed=signed))
    await self._can_sdo_download(node_id, elmo_object_int, sub_index, data_bytes)

  # --- EMCY (CANopen Emergency, COB-ID 0x80 + node_id) --------------------

  def add_emcy_callback(
    self, cb: Callable[[int, EmcyFrame, str, bool], None]
  ) -> None:
    """Register a callback fired on every (non-suppressed) EMCY frame.

    Callback signature: ``cb(node_id, frame, description, disable_motors)``.
    Always invoked on the asyncio loop thread captured in :meth:`setup`.
    Exceptions raised by the callback are logged and swallowed so one bad
    handler can't poison the rest.
    """
    self._emcy_callbacks.append(cb)

  def clear_emcy_state(self, node_id: Optional[int] = None) -> None:
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

  def _make_emcy_callback(self, node_id: int):
    """Return a `canopen.Network.subscribe` callback bound to a specific node."""

    def _cb(cob_id: int, data: bytes, timestamp: float) -> None:
      # Fires on canopen's listener thread. Marshal decoding into the loop.
      if self._loop is None:
        return
      self._loop.call_soon_threadsafe(self._dispatch_emcy, node_id, bytes(data))

    return _cb

  def _dispatch_emcy(self, node_id: int, data: bytes) -> None:
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
      node_id, err_code, err_reg, elmo_err, d1, d2, desc,
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

  def _make_tpdo3_callback(self, node_id: int):
    """Return a callback that decodes TPDO3 (StatusWord) and signals waiters."""

    def _cb(cob_id: int, data: bytes, timestamp: float) -> None:
      if self._loop is None:
        return
      if len(data) < 2:
        # StatusWord is 2 bytes — a shorter frame means the drive's TPDO3
        # mapping is wrong or a different sender is squatting on the COB-ID.
        # _wait_setpoint_ack would silently fall back to SDO probing forever.
        logger.warning(
          "TPDO3 frame too short from node %d: %s (expected >=2 bytes)",
          node_id, bytes(data).hex(),
        )
        return
      sw = int.from_bytes(bytes(data[:2]), "little")
      self._loop.call_soon_threadsafe(self._dispatch_statusword, node_id, sw)

    return _cb

  def _dispatch_statusword(self, node_id: int, sw: int) -> None:
    self._statusword[node_id] = sw
    ev = self._statusword_event.get(node_id)
    if ev is not None:
      ev.set()

  # --- Elmo binary interpreter (vendor protocol on TPDO2/RPDO2) ------------

  def _make_bi_callback(self, node_id: int):
    """Return a `canopen.Network.subscribe` callback bound to a specific node."""

    def _cb(cob_id: int, data: bytes, timestamp: float) -> None:
      # Fires on canopen's listener thread. Marshal decoding into the loop.
      if self._loop is None:
        return
      self._loop.call_soon_threadsafe(self._dispatch_bi_response, node_id, bytes(data))

    return _cb

  def _dispatch_bi_response(self, node_id: int, data: bytes) -> None:
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
      fut.set_result(val)  # native int or float, no stringification

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
    if self._network is None:
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

    val_bytes = (
      struct.pack("<f", float(value)) if is_float
      else struct.pack("<i", int(value))
    )
    payload = bytes([byte0, byte1, byte2, byte3]) + val_bytes
    data_to_send = payload[:4] if is_execute else payload

    targets = (
      list(self.motion_node_ids) if node_id == _GROUP_NODE_ID else [node_id]
    )
    keys = [(nid, cmd, cmd_index) for nid in targets]

    # Acquire one lock per target key first, so a second caller with the
    # same key waits at the gate instead of clobbering our pending future.
    # AsyncExitStack releases the locks in LIFO order on any exit path.
    async with contextlib.AsyncExitStack() as stack:
      for key in keys:
        await stack.enter_async_context(
          self._bi_locks.setdefault(key, asyncio.Lock())
        )

      futures: List[asyncio.Future] = []
      for key in keys:
        fut = self.loop.create_future()
        self._pending_bi[key] = fut
        futures.append(fut)

      self._network.send_message(_BI_REQUEST_COB_BASE + node_id, data_to_send)

      try:
        return await asyncio.wait_for(asyncio.gather(*futures), timeout=timeout)
      except asyncio.TimeoutError:
        raise CanError(
          f"Timeout waiting for response to {cmd}[{cmd_index}] from node {node_id}"
        )
      finally:
        # Defensive: clear pending futures on any exit (success, timeout,
        # other exception). Without finally a non-Timeout exception could
        # leave stale futures keyed on (nid, cmd, cmd_index) that the next
        # caller would await indefinitely — the dispatch resolves only the
        # most recent future at a given key.
        for key in keys:
          self._pending_bi.pop(key, None)

  async def query_int(self, node_id: int, cmd: str, cmd_index: int) -> int:
    """Query an int-typed Elmo parameter. Returns the drive's current value."""
    if node_id == _GROUP_NODE_ID:
      raise CanError("Group queries are not supported")
    resps = await self._send_bi(
      node_id, cmd, cmd_index, is_query=True, is_execute=False, is_float=False,
    )
    return int(resps[0])

  async def query_float(self, node_id: int, cmd: str, cmd_index: int) -> float:
    """Query a float-typed Elmo parameter. Returns the drive's current value."""
    if node_id == _GROUP_NODE_ID:
      raise CanError("Group queries are not supported")
    resps = await self._send_bi(
      node_id, cmd, cmd_index, is_query=True, is_execute=False, is_float=True,
    )
    return float(resps[0])

  async def write(
    self, node_id: int, cmd: str, cmd_index: int, value: Union[int, float],
  ) -> None:
    """Write an Elmo parameter. The type of ``value`` selects int vs float
    framing on the wire. The drive echoes the accepted value back, which we
    verify — a mismatch raises :class:`CanError`.
    """
    is_float = isinstance(value, float)
    resps = await self._send_bi(
      node_id, cmd, cmd_index,
      is_query=False, is_execute=False, is_float=is_float, value=value,
    )
    targets = (
      list(self.motion_node_ids) if node_id == _GROUP_NODE_ID else [node_id]
    )
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
          f"Unexpected CAN response: sent {cmd}[{cmd_index}]={value}, "
          f"got {resp} from node {nid}"
        )

  async def execute(self, node_id: int, cmd: str, cmd_index: int = 0) -> None:
    """Fire-and-forget execute (e.g. ``BG``). Awaits the drive's response so
    the caller sees the command completed on the wire, but no echo-check."""
    await self._send_bi(
      node_id, cmd, cmd_index, is_query=False, is_execute=True, is_float=False,
    )

  async def _os_interpreter(
    self,
    node_id: int,
    cmd: str,
    *,
    query: bool = False,
  ) -> str:
    """Run an OS interpreter command via standard CiA-301 OS Command objects.

    Uses 0x1024 (OS Command Mode) + 0x1023 (OSCommand record) — the library
    handles the expedited vs. segmented SDO choice and toggle-bit dance
    automatically, replacing ~260 lines of hand-rolled segmented SDO in the
    legacy driver.
    """
    if node_id not in self._nodes:
      raise CanError(f"os_interpreter: unknown node {node_id}")
    node = self._nodes[node_id]

    # 0x1024:0 = OS Command Mode. Elmo/legacy code sets this to 0 ("evaluate
    # immediately") before each command.
    await asyncio.to_thread(node.sdo.download, 0x1024, 0, bytes([0]))

    # 0x1023:1 = OSCommand.Command. ASCII-encoded; library segments if >4 bytes.
    await asyncio.to_thread(node.sdo.download, 0x1023, 1, cmd.encode("ascii"))

    # 0x1023:2 = OSCommand.Status (U8). This is the CiA-301 OS-command lifecycle
    # byte, not an error flag:
    #   0x00 no reply yet / no error   0x01 command is being executed
    #   0x02 completed, no reply       0x03 completed with reply
    #   0xFF no command
    # For async `XQ##` dispatches the drive returns 0x01 immediately, which is
    # expected — the caller (e.g. `user_program_run`) polls PS/UI afterward for
    # completion. SDO abort codes surface as `SdoAbortedError` from the upload
    # itself; we don't need to inspect the byte. Log at debug for diagnostics.
    status_bytes = await asyncio.to_thread(node.sdo.upload, 0x1023, 2)
    logger.debug(
      "os_interpreter node=%d cmd=%r status=0x%02X",
      node_id, cmd, int.from_bytes(status_bytes[:1], "little"),
    )

    if not query:
      return ""

    # 0x1023:3 = OSCommand.Reply (DOMAIN / string). Library handles segmented.
    reply: bytes = await asyncio.to_thread(node.sdo.upload, 0x1023, 3)
    return reply.decode("ascii", errors="replace").rstrip("\x00").rstrip()

  # --- raw CANopen sends (SYNC + RPDO1 controlword) -----------------------

  async def _can_sync(self) -> None:
    if self._network is None:
      raise CanError("_can_sync called before setup()")
    # SYNC object (0x080), no data.
    self._network.send_message(0x80, b"")

  async def _control_word_set(self, node_id: int, value: int, sync: bool = True) -> None:
    if self._network is None:
      raise CanError("_control_word_set called before setup()")
    val_bytes = value.to_bytes(2, byteorder="little")
    # RPDO1 COB-ID = (4 << 7) | node_id = 0x200 + node_id
    self._network.send_message(0x200 + node_id, val_bytes)
    if sync:
      await self._can_sync()

  async def request_drive_version(self, node_id: int) -> str:
    """Query Elmo drive firmware version (VR) via the OS interpreter."""
    return await self._os_interpreter(node_id, "VR", query=True)

  # --- DS402 / motor control ----------------------------------------------

  async def motor_emergency_stop(self, node_id: int) -> None:
    await self.write(node_id, "MO", 0, 0)

  async def motor_is_enabled(self, node_id: int) -> bool:
    """Return True if the motor is energized (Elmo MO=1).

    Faulted drives report MO=0 — use motor_get_fault to distinguish a
    plain disable from a fault.
    """
    return await self.query_int(node_id, "MO", 0) == 1

  async def _motor_read_position_raw(self, node_id: int, pu: bool = False) -> int:
    cmd = "PU" if pu else "PX"
    return await self.query_int(node_id, cmd, 0)

  async def motor_get_motion_status(self, node_id: int) -> int:
    return await self.query_int(node_id, "MS", 0)

  async def motor_set_move_direction(
    self, node_id: int, direction: JointMoveDirection
  ) -> None:
    # Elmo modulo mode register: bit0 enables modulo; bits6..7 encode the
    # direction (0=Normal, 1=CW, 2=CCW, 3=Shortest). Packs to 1 + 64*direction
    # = 1/65/129/193.
    val = 1 + 64 * int(direction)
    await self.can_sdo_download_elmo_object(node_id, 24818, 0, val, _ElmoObjectDataType.UNSIGNED16)

  async def motor_check_if_move_done(self, node_id: int) -> bool:
    # E-stop and some fault paths leave MS pinned at 2 ("stopping in
    # progress") indefinitely, so gating fault-surfacing on ms==1 misses
    # them — the poll loop times out before ever consulting EMCY state.
    # Check sticky EMCY first so any fatal frame raises on the next poll
    # iteration regardless of MS.
    pending = self.emcy_move_error
    if pending is not None:
      raise RuntimeError(f"Motor Fault: {pending}")
    ms_val = await self.query_int(node_id, "MS", 0)
    if ms_val == 0:
      return True
    if ms_val == 1:
      mo_val = await self.query_int(node_id, "MO", 0)
      if mo_val == 1:
        return True
      fault = await self.motor_get_fault(node_id)
      if fault is not None:
        raise RuntimeError(f"Motor Fault: Axis {node_id} {fault}")
      raise RuntimeError(f"Motor Fault: Axis {node_id} (Unknown)")
    return False

  async def motor_get_fault(self, node_id: int) -> Optional[str]:
    val = await self.query_int(node_id, "MF", 0)
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
      0b001: "Power supply under voltage.",                # b13 only
      0b010: "Power supply over voltage.",                 # b14 only
      0b101: "Motor lead short circuit or faulty drive.",  # b13 + b15
      0b110: "Drive overheated.",                          # b14 + b15
    }
    faults = [msg for bit, msg in bit_msgs.items() if val & bit]
    triplet = (val >> 13) & 0b111
    if triplet in triplet_msgs:
      faults.append(triplet_msgs[triplet])
    if not faults:
      return f"Unknown fault code: {val} (0x{val:08X})"
    return "  ".join(faults)

  async def motor_enable(self, node_id: int, state: bool, *, use_ds402: bool) -> None:
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
      # Clear sticky EMCY state from any prior fault on this drive so the
      # post-enable motion path doesn't re-surface stale errors. Mirrors
      # clscanmotor.cs:4481 ("EmcyMoveErrorReceived = false" before re-enable)
      # plus the per-axis IPM-queue clear at clscanmotor.cs:4050-4051.
      self.clear_emcy_state(node_id=node_id)

    want = 1 if state else 0
    max_attempts = 20
    inter_attempt_sleep_s = 0.5
    for attempt in range(1, max_attempts + 1):
      if not use_ds402:
        await self.write(node_id, "MO", 0, want)
      elif state:
        # DS402 enable: edge-pulsed CW writes with SW confirmation between
        # transitions. CiA 402 §6.1: Fault -> Switch on disabled fires on
        # the rising edge of CW bit 7 (Fault Reset). RPDO1 is mapped
        # SynchronousCyclic, so back-to-back writes within one servo cycle
        # can be coalesced and the drive never sees the edge — fault never
        # clears and the retry loop spins. Polling SW between transitions
        # forces each CW write to land on the wire before the next one.
        await self._control_word_set(node_id=node_id, value=0x00)  # clear bits
        await self._control_word_set(node_id=node_id, value=0x80)  # fault reset
        await self._wait_sw_bit(node_id, bit_mask=1 << 3, want_high=False)
        await self._control_word_set(node_id=node_id, value=0x06)  # Shutdown
        await self._wait_sw_bit(node_id, bit_mask=1 << 0, want_high=True)
        await self._control_word_set(node_id=node_id, value=0x07)  # Switch on
        await self._wait_sw_bit(node_id, bit_mask=1 << 1, want_high=True)
        await self._control_word_set(node_id=node_id, value=0x0F)  # Enable op
        # Bit 2 (Operation enabled) is confirmed by the MO query below.
      else:
        # DS402 disable: Op Enabled -> Switched On -> Ready to Switch On.
        # Matches C# (clscanmotor.cs:4540-4543) — back-to-back, no inter-CW sleep.
        await self._control_word_set(node_id=node_id, value=7)
        await self._control_word_set(node_id=node_id, value=6)
      await asyncio.sleep(0.1)
      mo = await self.query_int(node_id, "MO", 0)
      if mo == want:
        return
      logger.warning(
        "motor_enable(state=%s) attempt %d/%d failed for node %d (MO=%s); retrying",
        state, attempt, max_attempts, node_id, mo,
      )
      await asyncio.sleep(inter_attempt_sleep_s)
    verb = "enable" if state else "disable"
    raise CanError(f"Motor failed to {verb} (node_id = {node_id}) after {max_attempts} attempts")

  async def motors_ensure_enabled(
    self, node_ids: List[int], *, use_ds402: bool = True,
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
    for nid in node_ids:
      nid_int = int(nid)
      if await self.motor_is_enabled(nid_int):
        continue
      logger.warning("node %d: re-enabling (was disabled)", nid_int)
      await self.motor_enable(node_id=nid_int, state=True, use_ds402=use_ds402)

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

  async def ipm_select_mode(self, enable: bool) -> None:
    """Enable/disable IPM (Interpolated Position Mode, 0x6060=7) on all motion
    axes via standard SDO writes.

    The ``_ipm_mode`` bookkeeping flag is set *pessimistically*: True before
    the SDO writes on enable, False after them on disable. So a partial
    failure mid-sequence still leaves the next caller with an accurate
    "we tried to be in IPM" signal — they can re-arm rather than assume
    we cleanly stayed in PPM.
    """
    if enable:
      self._ipm_mode = True
      # First, latch CW=0x0F on every axis (op-enabled, NO ip-enable/new-
      # setpoint trigger). PPM leaves CW bit 4 high; in mode 7 bit 4 means
      # "interpolation enabled" so an unreset CW makes the drive try to
      # interpolate an empty buffer the moment we flip to mode 7, and the next
      # RPDO3 preload hits EMCY 0x34 / 0xBA (queue_full on first write).
      # RPDO1 is SynchronousCyclic so the writes need a SYNC to take effect.
      for nid in self.motion_node_ids:
        await self._control_word_set(nid, 0x0F, sync=False)
      await self._can_sync()
      # Now do the mode-bounce + buffer clear per axis.
      for nid in self.motion_node_ids:
        await self._set_op_mode(nid, 1)
        await self.ipm_clear_queue(nid)
        await self._set_op_mode(nid, 7)
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
          for nid in self.motion_node_ids:
            await self._set_op_mode(nid, 1)  # profile position mode
        finally:
          # Clear bookkeeping even on partial failure — the alternative is
          # leaving _ipm_mode=True after a teardown attempt, which would
          # cause the next select_mode(True) to take the re-arm branch
          # against drives possibly already in PPM.
          self._ipm_mode = False

  async def ipm_clear_queue(self, node_id: int) -> None:
    """Reset the drive's interpolation buffer head/tail (0x60C4 sub 6 = 0).
    Used by `ipm_select_mode` re-arm and post-cancel cleanup so a stale tail
    pointer doesn't replay old points on the next IPM enable."""
    await self._can_sdo_download(int(node_id), 0x60C4, 0x06, [0])

  async def ipm_set_time_interval(self, ms: int) -> None:
    """Program 0x60C2:01 = ms on every motion axis. The 0x60C2:02 = -3 written
    at setup means the unit is milliseconds; this just sets the count.

    Drive-level: integer ms only (UNSIGNED8). Caller picks dt; runtime passes
    the same value used to build the trajectory so position/velocity scaling
    matches what the drive integrates between points."""
    if not 0 <= int(ms) <= 255:
      raise ValueError(f"ipm_set_time_interval: ms must fit in UINT8, got {ms}")
    for nid in self.motion_node_ids:
      await self.can_sdo_download_elmo_object(
        nid, 24770, 1, int(ms), _ElmoObjectDataType.UNSIGNED8,
      )

  def ipm_send_pvt_point(
    self, node_id: int, position_enc: int, velocity_enc_per_s: int,
  ) -> None:
    """Append one PVT (P, V) data point to the drive's interpolation buffer
    via RPDO3.

    Wire layout (8 bytes, little-endian, RPDO3 COB-ID = 0x400 + node_id):
      [0..3] TargetPositionIP (INT32 encoder counts)
      [4..7] TargetVelocityIP (INT32 encoder counts/sec)

    Synchronous — `network.send_message` queues to the kernel CAN buffer in
    microseconds with no I/O wait, so there's nothing to await. Marking it
    `async` would mislead callers about the cost (no event-loop yield) and
    add a coroutine-frame allocation per send.

    RPDO3 is mapped EventDriven (no SYNC needed) at setup. Caller paces the
    feed; sending faster than the buffer drains raises EMCY 0x34 from the
    drive (queue-full)."""
    if self._network is None:
      raise CanError("ipm_send_pvt_point called before setup()")
    payload = (
      int(position_enc).to_bytes(4, "little", signed=True)
      + int(velocity_enc_per_s).to_bytes(4, "little", signed=True)
    )
    cob_id = (int(COBType.RPDO3) << 7) | (int(node_id) & 0x7F)
    self._network.send_message(cob_id, payload)

  async def ipm_begin_motion(self, node_ids: List[int]) -> None:
    """Start IPM streaming on the listed axes. CW=0x1F (op-enabled +
    ip-enable) per axis via RPDO1; one SYNC at the end so the
    SynchronousCyclic-mapped RPDO1s latch together.

    Auto-recovers from a prior disable (post-halt, post-fault) via
    ``motors_ensure_enabled``. Single-shot 0x1F to a drive in Switch On
    Disabled is silently dropped — the state machine needs the 6→7→15
    transitions visible to its poll loop.

    Resets the per-axis IPM queue counters BEFORE issuing the CW edge so a
    queue_full/underflow fired between preload and begin (e.g. malformed
    first point) is preserved for the runtime to inspect and surface."""
    ids = [int(n) for n in node_ids]
    if not ids:
      return
    await self.motors_ensure_enabled(ids)
    for nid in ids:
      if nid in self._emcy:
        self._emcy[nid] = _NodeEmcyState()
    for nid in ids[:-1]:
      await self._control_word_set(nid, 0x1F, sync=False)
    await self._control_word_set(ids[-1], 0x1F, sync=True)

  async def ipm_stop(self, node_ids: Optional[List[int]] = None) -> None:
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

  def ipm_check_queue_fault(self, node_ids: List[int]) -> None:
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
      if st.underflow:
        bad.append(f"Axis {nid} underflow")
    if bad:
      raise CanError("IPM queue fault: " + "; ".join(bad))

  async def ipm_wait_motion_complete(
    self, node_ids: List[int], timeout_s: float,
  ) -> None:
    """Wait until SW bit-10 (motion_complete / target_reached) goes high on
    every listed axis. The bit goes high once the IP buffer drains and the
    drive's trajectory generator settles on the last commanded position.

    Polls via the TPDO3-backed StatusWord cache; falls back to SDO probe
    if the cache hasn't seen a frame within 5 ms. Raises CanError on
    timeout, naming the offending axis."""
    async def _wait_one(nid: int) -> None:
      ok = await self._wait_sw_bit(
        int(nid), bit_mask=1 << 10, want_high=True, timeout_s=timeout_s,
      )
      if not ok:
        raise CanError(
          f"Axis {nid}: SW bit-10 (target reached) never went high within "
          f"{timeout_s:.1f}s — IPM trajectory did not complete"
        )
    await asyncio.gather(*(_wait_one(int(n)) for n in node_ids))

  async def wait_for_moves_done(
    self, node_ids: List[int], timeout: float
  ) -> None:
    # Poll MS every 30ms after a 50ms warm-up. The warm-up avoids reading
    # MS=0 in the window between CW=63 and motion actually starting.
    assert self._loop is not None
    loop = self._loop

    async def _poll_axis(nid: int) -> None:
      deadline = loop.time() + timeout
      await asyncio.sleep(0.05)
      while loop.time() < deadline:
        try:
          if await self.motor_check_if_move_done(int(nid)):
            return
        except CanError as e:
          # Transient bus error — keep polling. Visible at DEBUG so a wedged
          # bus shows up in logs instead of just burning the full timeout.
          logger.debug("wait_for_moves_done node %d: transient CAN error: %s", nid, e)
        await asyncio.sleep(0.03)
      # Final authoritative check; propagates CanError / motor-fault.
      if not await self.motor_check_if_move_done(int(nid)):
        raise CanError(f"Node {nid} move did not complete within {timeout}s")

    await asyncio.gather(*(_poll_axis(n) for n in node_ids))

  async def ppm_begin_motion(
    self, node_ids: List[int], *, relative: bool = False
  ) -> None:
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
    relative_bit = 0x40 if relative else 0
    cw_low = 47 + relative_bit
    cw_high = 47 + 0x10 + relative_bit
    # Auto-recover from prior disable (post-E-stop, post-find_z IL halt,
    # post-freedrive). A disabled drive never raises SW bit 12, so the PPM
    # trigger spins all 10 attempts before failing.
    await self.motors_ensure_enabled([int(n) for n in node_ids])
    for nid in node_ids:
      await self._trigger_new_setpoint(int(nid), cw_low, cw_high)

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
          node_id, attempt, max_attempts,
        )
        continue
      await self._control_word_set(node_id, cw_high, sync=True)
      raised = await self._wait_setpoint_ack(node_id, want_high=True)
      if raised:
        if attempt > 1:
          logger.debug(
            "node %d: new setpoint accepted on attempt %d", node_id, attempt
          )
        return
      logger.debug(
        "node %d: setpoint_ack didn't go high (attempt %d/%d); retrying",
        node_id, attempt, max_attempts,
      )
    raise CanError(
      f"Axis {node_id}: drive did not accept new PPM setpoint after "
      f"{max_attempts} attempts (SW bit 12 never went high after CW bit 4 "
      f"rising edge)"
    )

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

  async def user_program_run(
    self,
    node_id: int,
    user_function: str,
    params: Optional[List[Union[int, float]]] = None,
    timeout_sec: int = 0,
    wait_until_done: bool = False,
  ) -> int:
    if node_id < 0 or node_id > 255:
      raise ValueError("node_id must be in [0, 255]")

    ps = await self.query_int(node_id, "PS", 0)
    if ps == -2:
      raise CanError(f"Node {node_id}: controller reported PS=-2 (not ready / unavailable)")

    if ps != -1:
      await self.write(node_id, "UI", 1, 0)
      t0 = time.monotonic()
      while (time.monotonic() - t0) < 3.0:
        ps = await self.query_int(node_id, "PS", 0)
        if ps == -1:
          break
        await asyncio.sleep(0.01)
      else:
        raise CanError(f"Node {node_id}: did not reach idle state (PS=-1) within 3s (last PS={ps})")

    arg_str = f"({','.join(str(p) for p in params)})" if params else ""

    await self.write(node_id, "UI", 1, 1)

    cmd = f"XQ##{user_function}{arg_str}"
    logger.debug("user_program_run: %s", cmd)
    await self._os_interpreter(node_id, cmd, query=False)

    last_line_completed = 0
    if wait_until_done:
      t0 = time.monotonic()
      ps = 1
      ui1 = 1
      while ps == 1 and ui1 == 1 and (time.monotonic() - t0) < timeout_sec:
        ps = await self.query_int(node_id, "PS", 0)
        ui1 = await self.query_int(node_id, "UI", 1)
        await asyncio.sleep(0.01)

      last_line_completed = await self.query_int(node_id, "UI", 2)

      if ps == 1 and ui1 == 1:
        raise CanError(
          f"Node {node_id}: timeout waiting for '{user_function}' after {timeout_sec}s, "
          f"last_line={last_line_completed}"
        )
      if ui1 != 0:
        raise CanError(
          f"Node {node_id}: user program ended with UI[1]={ui1} (expected 0), "
          f"last_line={last_line_completed}"
        )

    return 0

  # --- I/O -----------------------------------------------------------------

  async def _read_digital_input(self, node_id: int, input_num: int) -> bool:
    return await self.query_int(node_id, "IB", input_num) == 1

  async def read_output(self, node_id: int, output_num: int) -> bool:
    val = await self.query_int(node_id, "OP", 0)
    mask = 1 << (output_num - 1)
    return (val & mask) == mask

  async def set_output(self, node_id: int, output_num: int, state: bool) -> None:
    await self.write(node_id, "OB", output_num, 1 if state else 0)

  async def motor_stop(self, node_id: int, settle: float = 0.1) -> None:
    """Controlled halt of one axis (port of C# MotorStop, clscanmotor.cs:5517).

    Sends CW=271 (Op Enabled + Halt — controlled deceleration, no power drop),
    waits `settle` seconds for the drive to come to rest, then writes 0x6060 = 7
    then = 1 to clear the post-halt status-word state. Used after an IL-induced
    auto-halt so the next move doesn't see a hung MS register.

    The C# version polls a TPDO-event flag with a 2.5s timeout. We can't reuse
    `wait_for_moves_done` here because MS never goes to 0 after a halt — the
    poll would just burn the full timeout. Drive deceleration is sub-100ms for
    the search velocities used here, so a fixed sleep is fine.
    """
    await self._control_word_set(node_id, 271)
    await asyncio.sleep(settle)
    await self._can_sdo_download(node_id, 0x6060, 0x00, [7])
    await self._can_sdo_download(node_id, 0x6060, 0x00, [1])

  async def read_input_logic(self, node_id: int, input_num: int) -> int:
    return await self.query_int(node_id, "IL", input_num)

  async def configure_input_logic(
    self, node_id: int, input_num: int, logic: int, logic_high: bool = False,
  ) -> None:
    """Set IL[input_num]: drive auto-acts on input edges (e.g. halt motion).

    Pass an `_InputLogic` member or raw int for `logic`. With `StopForward` the
    drive halts the motor itself the instant the input trips during forward
    motion — no software in the loop. Skips the write if value already matches;
    settles 250ms after a real change (Elmo IL needs time to apply).
    """
    value = int(logic) + (1 if logic_high else 0)
    if await self.read_input_logic(node_id, input_num) == value:
      return
    await self.write(node_id, "IL", input_num, value)
    await asyncio.sleep(0.25)


  # ======================================================================
  # Robot-level logic: homing, gripper, kinematics, motion
  # ======================================================================

  # -- robot-level homing / estop (moved from driver) ---------------------

  async def get_estop_state(self) -> bool:
    """Return True if the arm is in estop, False otherwise.

    Reads the shoulder drive's SR (status register) via the binary
    interpreter. Bits 14/15 encode the stop/safety state.
    """
    r = await self.query_int(Axis.SHOULDER, "SR", 1)
    return (r & 0x4000) == 0 or (r & 0x8000) == 0

  async def gripper_get_homed_status(self) -> HomeStatus:
    return HomeStatus(await self.query_int(Axis.SERVO_GRIPPER, "UI", 3))

  async def _gripper_set_homed_status(self, status: HomeStatus) -> None:
    await self.write(Axis.SERVO_GRIPPER, "UI", 3, int(status))

  async def _gripper_reset_encoder_position(self, position: float) -> None:
    sg = Axis.SERVO_GRIPPER
    await self.write(sg, "HM", 1, 0)
    await self.write(sg, "HM", 3, 0)
    await self.write(sg, "HM", 4, 0)
    await self.write(sg, "HM", 5, 0)
    # Old code packed `position` as int32 via `int(round(float(str(position))))`;
    # preserve that rounding semantic for callers that pass fractional values.
    await self.write(sg, "HM", 2, int(round(position)))
    await self.write(sg, "HM", 1, 1)

  async def _gripper_hard_stop_search(
    self, srch_vel: int, srch_acc: int, max_pe: int, hs_pe: int, timeout: float,
  ) -> None:
    sg = Axis.SERVO_GRIPPER
    await self.write(sg, "ER", 3, max_pe * 10)
    await self.write(sg, "AC", 0, srch_acc)
    await self.write(sg, "DC", 0, srch_acc)
    for i in [3, 4, 5, 2]:
      await self.write(sg, "HM", i, 0)
    await self.write(sg, "JV", 0, srch_vel)

    try:
      params: List[Union[int, float]] = [int(hs_pe), int(timeout * 1000)]
      last_line = await self.user_program_run(
        sg, "Home", params, int(timeout), True
      )
      if last_line in [1, 2, 3]:
        raise RuntimeError(f"Homing Script Error {34 + last_line}")

      curr_pos = await self._motor_read_position_raw(sg)
      await self.write(sg, "PA", 0, curr_pos)
      await self.write(sg, "SP", 0, srch_vel)
      await self.write(sg, "AC", 0, srch_acc)
      await self.write(sg, "DC", 0, srch_acc)
    finally:
      await asyncio.sleep(0.3)
      await self.execute(sg, "BG", 0)
      await asyncio.sleep(0.3)
      await self.write(sg, "ER", 3, int(max_pe))

  async def _gripper_index_search(
    self, srch_vel: int, srch_acc: int, positive_direction: bool, timeout: float,
  ) -> tuple:
    sg = Axis.SERVO_GRIPPER
    await self.write(sg, "HM", 1, 0)

    one_revolution = await self.query_int(sg, "CA", 18)
    if not positive_direction:
      one_revolution *= -1

    await self.write(sg, "PR", 1, one_revolution)
    await self.write(sg, "SP", 0, srch_vel)
    await self.write(sg, "AC", 0, srch_acc)
    await self.write(sg, "DC", 0, srch_acc)

    await self.write(sg, "HM", 3, 3)  # index only
    await self.write(sg, "HM", 4, 0)
    await self.write(sg, "HM", 5, 0)
    await self.write(sg, "HM", 2, 0)
    await self.write(sg, "HM", 1, 1)  # arm

    await self.execute(sg, "BG", 0)
    await self.wait_for_moves_done([sg], timeout)

    left = await self.query_int(sg, "HM", 1)
    if left != 0:
      raise RuntimeError("Homing Failure: Failed to finish index pulse search.")

    captured_position = await self.query_int(sg, "HM", 7)
    return one_revolution, captured_position

  async def _home_servo_gripper(self, sgc: ServoGripperConfig) -> None:
    """Hard-stop + index-pulse home for the servo gripper."""
    sg = Axis.SERVO_GRIPPER
    timeout = sgc.home_timeout_msec / 1000

    async with self._motion_guard():
      ca41 = await self.query_int(sg, "CA", 41)
      if ca41 == 24:
        raise RuntimeError(
          f"Servo gripper not ready to home (drive reports CA[41]={ca41}). "
          "Power-cycle the drive or re-check homing config."
        )

      try:
        await self._gripper_hard_stop_search(
          sgc.home_search_vel, sgc.home_search_accel,
          sgc.home_default_position_error, sgc.home_hard_stop_position_error,
          timeout,
        )
      except Exception as e:
        # motor_check_if_move_done already raised with the rich EMCY
        # description ("Motor Fault: ..."). Don't overwrite it with the
        # duller MF-bit register read.
        if str(e).startswith("Motor Fault:"):
          raise
        fault = await self.motor_get_fault(sg)
        if fault is not None:
          raise RuntimeError(fault) from e
        raise

      await self.motor_enable(node_id=sg, state=True, use_ds402=False)

      await self._motors_move_absolute_execute_locked(
        plan=MotorsMovePlan(moves=[MotorMoveParam(
          node_id=sg, position=sgc.home_hard_stop_offset,
          velocity=sgc.home_offset_vel, acceleration=sgc.home_offset_accel,
          relative=False, direction=JointMoveDirection.ShortestWay,
        )])
      )

      is_positive = sgc.home_hard_stop_offset > 0
      await self._gripper_index_search(
        abs(sgc.home_search_vel), sgc.home_search_accel, is_positive, timeout,
      )

      await self._motors_move_absolute_execute_locked(
        plan=MotorsMovePlan(moves=[MotorMoveParam(
          node_id=sg, position=sgc.home_index_offset,
          velocity=sgc.home_offset_vel, acceleration=sgc.home_offset_accel,
          relative=False, direction=JointMoveDirection.ShortestWay,
        )])
      )
      await self._gripper_reset_encoder_position(sgc.home_pos)
      await self._gripper_set_homed_status(HomeStatus.Homed)

  # -- servo gripper ------------------------------------------------------

  async def servo_gripper_initialize(self):
    # Don't swallow motor_enable failures here — homing is the next step
    # and will fault with a confusing "homing failure" error if the motor
    # never came up. Better to surface the real cause.
    await self.motor_enable(
      node_id=Axis.SERVO_GRIPPER, state=True, use_ds402=False
    )
    await self.servo_gripper_home()
    await self.servo_gripper_close()

  async def servo_gripper_home(self) -> None:
    sgc = self._cfg.servo_gripper
    if sgc is None:
      raise RuntimeError("Servo gripper not present")
    sg = Axis.SERVO_GRIPPER
    await self.write(sg, "PL", 1, sgc.peak_current)
    await self.write(sg, "CL", 1, sgc.continuous_current)

    await self._home_servo_gripper(sgc)

    await self._set_servo_gripper_force_limit(100)

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
    await self.write(sg, "PL", 1, sgc.peak_current)
    await self.write(sg, "CL", 1, cont_current)
    await self.write(sg, "PL", 1, peak_current)

  async def _get_servo_gripper_force_fraction(self) -> float:
    """Return |IQ| / CL clamped to [0, 1] — the fraction of the configured
    continuous current the gripper is currently drawing."""
    sg = Axis.SERVO_GRIPPER
    cl = await self.query_float(sg, "CL", 1)
    iq = await self.query_float(sg, "IQ", 0)

    if cl == 0:
      return 0.0

    return max(0.0, min(abs(iq / cl), 1.0))

  async def check_plate_gripped(self, num_attempts: int = 5) -> None:
    for _ in range(num_attempts):
      motor_status = await self.query_int(
        Axis.SERVO_GRIPPER, "MS", 1
      )
      logger.debug("Servo gripper motor status: %s", motor_status)

      if motor_status in {0, 1}:
        max_force_percentage = await self._get_servo_gripper_force_fraction()
        if max_force_percentage > 90:
          return
        await asyncio.sleep(0.5)
        max_force_percentage = await self._get_servo_gripper_force_fraction()
        if max_force_percentage > 90:
          return

        current_position = await self.motor_get_current_position(Axis.SERVO_GRIPPER)
        closed_position = 1
        if abs(current_position - closed_position) < 2.0 / 625:
          raise RuntimeError(
            "Servo Gripper was able to move all the way to the closed position, which indicates the absence of an object in the gripper.  The closed position value may need to be decreased."
          )

        return

      elif motor_status == 2:
        motor_fault = await self.motor_get_fault(Axis.SERVO_GRIPPER)
        if motor_fault is None:
          raise RuntimeError("Error querying whether plate is gripped. Error querying motor fault.")
        raise RuntimeError(
          f"Servo Gripper may not have gripped the plate correctly. Motor fault: '{motor_fault}'"
        )

      await asyncio.sleep(0.05)

    raise RuntimeError(
      f"Servo Gripper was unable to confirm that the plate is gripped after {num_attempts} attempts."
    )

  async def servo_gripper_close(self, closed_position: int = 0, check_plate_gripped=True) -> None:
    async with self._motion_guard():
      await self._motors_move_joint_locked({Axis.SERVO_GRIPPER: closed_position})
      if check_plate_gripped:
        await self.check_plate_gripped()

  async def servo_gripper_open(self, open_position: float) -> None:
    async with self._motion_guard():
      await self._motors_move_joint_locked({Axis.SERVO_GRIPPER: open_position})

  async def drive_set_move_count_parameters(
    self,
    move_count: int,
    travel: List[float],
    last_maintenance_performed: float,
    maintenance_required: bool,
    last_maintenance_performed_date: int,
    last_maintenance_performed_rail: float,
    maintenance_required_rail: bool,
    last_maintenance_performed_date_rail: int,
  ) -> None:
    z = Axis.Z

    # MoveCount -> Z axis, UI index 22
    await self.write(z, "UI", 22, int(move_count))

    # Travel[] -> each node, UF index 5
    # The source looked 1-based for Travel and 0-based for NodeIDList; handle both cleanly.
    if len(travel) == len(self._cfg.axes) + 1:
      pairs = zip(self._cfg.axes, travel[1:])
    else:
      pairs = zip(self._cfg.axes, travel)

    for axis_key, dist in pairs:
      await self.write(int(axis_key), "UF", 5, float(dist))

    # LastMaintenancePerformed -> Z axis, UF index 6
    await self.write(z, "UF", 6, float(last_maintenance_performed))

    # MaintenanceRequired -> Z axis, UI index 23
    await self.write(z, "UI", 23, 1 if maintenance_required else 0)

    # LastMaintenancePerformedDate -> Z axis, UI index 21
    await self.write(z, "UI", 21, int(last_maintenance_performed_date))

    # Rail (if present)
    if self._cfg.robot_on_rail:
      rail = Axis.RAIL
      await self.write(rail, "UF", 6, float(last_maintenance_performed_rail))
      await self.write(rail, "UI", 23, 1 if maintenance_required_rail else 0)
      await self.write(rail, "UI", 21, int(last_maintenance_performed_date_rail))

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
      wrist_offset=await self.query_float(sh, "UF", 8),
      elbow_offset=await self.query_float(sh, "UF", 9),
      elbow_zero_offset=await self.query_float(sh, "UF", 10),
      axes=axes,
      base_to_gripper_clearance_z=await self.query_float(sh, "UF", 6),
      base_to_gripper_clearance_arm=await self.query_float(sh, "UF", 7),
      robot_on_rail=has_rail,
      servo_gripper=await self._read_servo_gripper_config() if has_servo_gripper else None,
    )

  async def _read_axis_config(self, nid: int) -> AxisConfig:
    logger.debug("Reading parameters for axis %s", nid)

    digital_inputs = await self._read_io_names(nid, 5, 11, _DIGITAL_INPUT_NAMES)
    analog_inputs = await self._read_io_names(nid, 11, 13, {})
    outputs = await self._read_io_names(nid, 13, 17, _OUTPUT_NAMES)

    await self.query_int(nid, "UI", 24)  # serial — read for parity, unused

    uf1 = await self.query_float(nid, "UF", 1)
    uf2 = await self.query_float(nid, "UF", 2)
    if uf1 == 0.0 or uf2 == 0.0:
      raise CanError(f"Invalid motor conversion factor for axis {nid}: UF[1]={uf1}, UF[2]={uf2}")
    motor_conversion_factor = uf1 / uf2

    xm1 = await self.query_int(nid, "XM", 1)
    xm2 = await self.query_int(nid, "XM", 2)
    max_travel = await self.query_float(nid, "UF", 3)
    min_travel = await self.query_float(nid, "UF", 4)
    vh3 = await self.query_int(nid, "VH", 3)
    vl3 = await self.query_int(nid, "VL", 3)

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

    ca45 = await self.query_int(nid, "CA", 45)
    if not (0 < ca45 <= 4):
      raise CanError(f"Invalid encoder socket for axis {nid}: CA[45]={ca45}")
    enc_type = await self.query_int(nid, "CA", 40 + ca45)
    if enc_type in (1, 2):
      absolute_encoder = False
    elif enc_type == 24:
      absolute_encoder = True
    else:
      raise CanError(f"Unsupported encoder type for axis {nid}: CA[4{ca45}]={enc_type}")

    ca46 = await self.query_int(nid, "CA", 46)
    num3 = 1.0 if ca45 == ca46 else await self.query_float(nid, "FF", 3)
    denom = motor_conversion_factor * num3

    sp2 = await self.query_int(nid, "SP", 2)
    if sp2 == 100000:
      max_vel = await self.query_int(nid, "VH", 2) / 1.01 / denom
    else:
      max_vel = sp2 / denom
    max_accel = await self.query_int(nid, "SD", 0) / 1.01 / denom

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

  async def _read_io_names(
    self, nid: int, start: int, end: int, named: Dict[int, str]
  ) -> Dict[int, str]:
    """Read UI[start..end-1] as a channel -> human name map.

    Channel index is 1-based. Codes in `named` map to fixed labels; positive
    unknowns become "AuxPinN"; non-positive means unassigned.
    """
    out: Dict[int, str] = {}
    for ui_idx in range(start, end):
      code = await self.query_int(nid, "UI", ui_idx)
      ch = ui_idx - start + 1
      if code in named:
        out[ch] = named[code]
      else:
        out[ch] = "" if code <= 0 else f"AuxPin{code}"
    return out

  async def _read_servo_gripper_config(self) -> ServoGripperConfig:
    sg = Axis.SERVO_GRIPPER
    return ServoGripperConfig(
      home_pos=int(await self.query_float(sg, "UF", 6)),
      home_search_vel=int(await self.query_float(sg, "UF", 7)),
      home_search_accel=int(await self.query_float(sg, "UF", 8)),
      home_default_position_error=int(await self.query_float(sg, "UF", 9)),
      home_hard_stop_position_error=int(await self.query_float(sg, "UF", 10)),
      home_hard_stop_offset=int(await self.query_float(sg, "UF", 11)),
      home_index_offset=int(await self.query_float(sg, "UF", 12)),
      home_offset_vel=int(await self.query_float(sg, "UF", 13)),
      home_offset_accel=int(await self.query_float(sg, "UF", 14)),
      home_timeout_msec=int(await self.query_float(sg, "UF", 15)),
      continuous_current=await self.query_float(sg, "UF", 16),
      peak_current=await self.query_float(sg, "UF", 17),
    )

  @property
  def _cfg(self) -> KX2Config:
    if self._config is None:
      raise RuntimeError("KX2 not set up — call setup() first")
    return self._config

  async def motor_get_current_position(self, axis: Axis) -> float:
    raw = await self._motor_read_position_raw(
      node_id=axis, pu=self._cfg.axes[axis].unlimited_travel,
    )
    c = self._cfg.axes[axis].motor_conversion_factor
    if axis == Axis.ELBOW:
      return kinematics.convert_elbow_angle_to_position(self._cfg, raw / c)
    if c == 0:
      logger.warning("Axis %s has conversion factor of 0", axis)
      return 0.0
    return raw / c

  async def read_input(self, axis: Axis, input_num: int) -> bool:
    return await self._read_digital_input(node_id=axis, input_num=0x10 + input_num)

  # IR breakbeam between the gripper fingers, wired to the Z-drive's IO.
  # True = beam interrupted (object present).
  _PROXIMITY_SENSOR_AXIS: Axis = Axis.Z
  _PROXIMITY_SENSOR_INPUT: int = 4

  async def read_proximity_sensor(self) -> bool:
    return await self.read_input(self._PROXIMITY_SENSOR_AXIS, self._PROXIMITY_SENSOR_INPUT)

  async def wait_for_proximity_sensor(
    self, state: bool = True, timeout: float = 5.0, poll: float = 0.01,
  ) -> bool:
    """Poll until the sensor reads `state`. Returns True on trip, False on timeout."""
    deadline = time.monotonic() + timeout
    while True:
      if await self.read_proximity_sensor() == state:
        return True
      if time.monotonic() >= deadline:
        return False
      await asyncio.sleep(poll)

  async def find_z_with_proximity_sensor(
    self,
    z_start: float,
    z_end: float,
    max_gripper_speed: float = 25.0,
    max_gripper_acceleration: float = 100.0,
  ) -> float:
    """Pre-position to ``z_start``, then descend toward ``z_end``; halt when
    the IR breakbeam trips.

    ``z_start > z_end`` (search descends in world frame). The search
    bounds are absolute Z so the caller never has to compute a delta.
    ``max_gripper_speed`` / ``max_gripper_acceleration`` cap Z motion in
    mm/s and mm/s² (Z is linear, so gripper speed equals |v_z|).

    Arms IL[4]=StopForward so the drive halts the motor itself on the
    input edge (sub-ms latency, no software in the loop). IL is restored
    to GeneralPurpose afterwards even if the move raises. Returns the Z
    position where the drive halted; raises RuntimeError if the beam
    never tripped (descent ran the full ``z_start → z_end`` range).
    """
    if z_end >= z_start:
      raise ValueError(
        f"find_z_with_proximity_sensor: z_end ({z_end}) must be below "
        f"z_start ({z_start}) — search descends."
      )
    # Hold the motion guard for the whole find: a parallel caller's move
    # could land between IL=StopForward and the descent, or between the
    # descent and IL restore. The spawned move task uses
    # `_motors_move_joint_locked` (no guard reacquire) since asyncio.Lock
    # is task-aware and our reentrance check uses task identity — the
    # spawned task isn't the owner.
    async with self._motion_guard():
      # Pre-flight: force the drive back to Op Enabled. A prior failed
      # search could have left it in Fault/Quick Stop where new moves
      # silently fail (Z barely moves).
      await self.motors_ensure_enabled([int(Axis.Z)])
      await self._motors_move_joint_locked(
        {Axis.Z: z_start},
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )
      if await self.read_proximity_sensor():
        return await self.motor_get_current_position(Axis.Z)
      await self.configure_input_logic(
        self._PROXIMITY_SENSOR_AXIS, self._PROXIMITY_SENSOR_INPUT, _InputLogic.StopForward,
      )
      move_task = asyncio.create_task(
        self._motors_move_joint_locked(
          {Axis.Z: z_end},
          max_gripper_speed=max_gripper_speed,
          max_gripper_acceleration=max_gripper_acceleration,
        )
      )
      tripped = False
      try:
        # The drive halts itself via IL the moment the beam breaks. We poll
        # the sensor in parallel so we can stop waiting for "move done"
        # (which never arrives — the drive halted short of target).
        while not move_task.done():
          if await self.read_proximity_sensor():
            tripped = True
            break
          await asyncio.sleep(0.01)
        move_task.cancel()
        try:
          await move_task
        except (asyncio.CancelledError, CanError):
          pass
      finally:
        # Match C# search cleanup (KX2RobotControl.cs:8650-8658): halt
        # motor FIRST, then restore IL. Reverse order would let the drive
        # surge toward the unreached target during the gap.
        try:
          await self.motor_stop(Axis.Z)
        except Exception as e:
          logger.warning("find_z: motor_stop failed: %s", e)
        try:
          await self.configure_input_logic(
            self._PROXIMITY_SENSOR_AXIS, self._PROXIMITY_SENSOR_INPUT, _InputLogic.GeneralPurpose,
          )
        except Exception as e:
          logger.warning("find_z: IL restore failed: %s", e)
        # The IL-trip and the motor_stop fire EMCY frames that mark the
        # move as a fault; the trip was *expected*, not a real fault, so
        # clear the sticky state — otherwise the next motion call's
        # `motor_check_if_move_done` raises immediately on a stale flag.
        self.clear_emcy_state(int(Axis.Z))
      if not tripped:
        z_actual_end = await self.motor_get_current_position(Axis.Z)
        raise RuntimeError(
          f"proximity sensor never tripped on Z {z_start:.2f} → {z_end:.2f} "
          f"(stopped at {z_actual_end:.2f})"
        )
      return await self.motor_get_current_position(Axis.Z)

  async def find_with_proximity_sensor(
    self,
    start: Coordinate,
    end: Coordinate,
    *,
    max_gripper_speed: float = 25.0,
    max_gripper_acceleration: float = 100.0,
  ) -> Coordinate:
    """Sweep the gripper from ``start`` to ``end`` along a straight Cartesian
    line; halt when the IR breakbeam trips. Yaw is held at whatever the
    gripper is currently at — proximity sensing doesn't care about
    orientation, and asking the caller to specify a direction would force
    them to think about wrist angle they don't otherwise need to. Returns
    the gripper location at halt; raises ``RuntimeError`` if the beam
    never tripped over the full path.

    Generic Cartesian counterpart to :meth:`find_z_with_proximity_sensor`.
    The Z-only descent has a hardware fast-path (``IL[4]=StopForward`` on
    the Z drive halts the motor sub-ms on the input edge); X/Y motion can't
    use that — the breakbeam is wired only to the Z drive's I/O — so this
    method polls the sensor in software (~10 ms latency at 100 Hz). At the
    default 25 mm/s sweep that's ~0.25 mm of overshoot before cancellation,
    plus ~64 ms of post-cancel buffer-drain (8 PVT frames × 8 ms). For
    Z-only descents where mm-precision into labware matters, prefer
    ``find_z_with_proximity_sensor``.
    """
    async with self._motion_guard():
      await self.motors_ensure_enabled([int(a) for a in MOTION_AXES])
      current_yaw = (await self.request_gripper_pose()).rotation.z
      start_pose = CartesianPose(location=start, rotation=Rotation(z=current_yaw))
      end_pose = CartesianPose(location=end, rotation=Rotation(z=current_yaw))
      # Pre-position to start (joint move — path doesn't matter, just get there).
      pre_pos = await self._cart_to_joints(start_pose)
      await self._motors_move_joint_locked(
        cmd_pos=pre_pos,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )
      if await self.read_proximity_sensor():
        return (await self.request_gripper_pose()).location

      sweep_task = asyncio.create_task(
        self._run_linear_path(
          end_pose,
          max_gripper_speed=max_gripper_speed,
          max_gripper_acceleration=max_gripper_acceleration,
        )
      )
      tripped = False
      try:
        # Drive runs the streamed PVT trajectory; we poll the sensor in
        # parallel and cancel on trip. The sweep task's finally clause
        # then sends ipm_stop + reverts to PPM, leaving the drive ready
        # for the next motion call.
        while not sweep_task.done():
          if await self.read_proximity_sensor():
            tripped = True
            break
          await asyncio.sleep(0.01)
        sweep_task.cancel()
        try:
          await sweep_task
        except (asyncio.CancelledError, CanError):
          pass
      finally:
        # PVT-stop coasts up to 8 frames; halt() is too aggressive (drops
        # all motion-axis MO=0). The sweep task's own cleanup already
        # handled IPM teardown; clear sticky EMCY so next motion call's
        # `motor_check_if_move_done` doesn't raise on the stop-induced frame.
        self.clear_emcy_state()
      if not tripped:
        end_loc = (await self.request_gripper_pose()).location
        raise RuntimeError(
          f"proximity sensor never tripped on "
          f"({start.x:.1f},{start.y:.1f},{start.z:.1f}) → "
          f"({end.x:.1f},{end.y:.1f},{end.z:.1f}) "
          f"(stopped at ({end_loc.x:.1f},{end_loc.y:.1f},{end_loc.z:.1f}))"
        )
      return (await self.request_gripper_pose()).location

  async def motors_move_joint(
    self,
    cmd_pos: Dict[Axis, float],
    *,
    max_gripper_speed: Optional[float] = None,
    max_gripper_acceleration: Optional[float] = None,
  ) -> None:
    async with self._motion_guard():
      await self._motors_move_joint_locked(
        cmd_pos,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )

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
    current = {Axis(k): v for k, v in (await self.request_joint_position()).items()}
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

  async def motors_move_absolute_execute(self, plan: MotorsMovePlan) -> None:
    async with self._motion_guard():
      await self._motors_move_absolute_execute_locked(plan)

  async def _motors_move_absolute_execute_locked(self, plan: MotorsMovePlan) -> None:
    """Caller MUST hold _motion_guard."""
    await self.ipm_select_mode(False)

    if logger.isEnabledFor(logging.DEBUG):
      logger.debug(
        "move plan: move_time=%.3fs, %d axes:", plan.move_time, len(plan.moves)
      )
      for move in plan.moves:
        logger.debug(
          "  node=%d pos=%s vel=%s acc=%s dir=%s",
          move.node_id, move.position, move.velocity,
          move.acceleration, move.direction.name,
        )

    for move in plan.moves:
      nid = move.node_id
      await self.motor_set_move_direction(nid, move.direction)
      # 0x607A = Target Position (24698 decimal)
      await self.can_sdo_download_elmo_object(
        nid, 24698, 0, int(move.position), _ElmoObjectDataType.INTEGER32,
      )
      # 0x6081 = Profile Velocity (24705 decimal)
      await self.can_sdo_download_elmo_object(
        nid, 24705, 0, int(move.velocity), _ElmoObjectDataType.UNSIGNED32,
      )
      acc = max(int(move.acceleration), 100)
      # 0x6083 = Profile Acceleration (24707 decimal)
      await self.can_sdo_download_elmo_object(
        nid, 24707, 0, acc, _ElmoObjectDataType.UNSIGNED32
      )
      # 0x6084 = Profile Deceleration (24708 decimal)
      await self.can_sdo_download_elmo_object(
        nid, 24708, 0, acc, _ElmoObjectDataType.UNSIGNED32
      )

    node_ids = [move.node_id for move in plan.moves]
    await self.ppm_begin_motion(node_ids)
    await self.wait_for_moves_done(node_ids, timeout=plan.move_time + 2)

  async def _cart_to_joints(self, pose: CartesianPose) -> Dict[Axis, float]:
    """Cartesian -> joints, snapping rotary axes to whichever 360° wrap is
    closest to the current joint position."""
    current = {Axis(k): v for k, v in (await self.request_joint_position()).items()}
    ik_joints = kinematics.ik(pose, self._cfg, self._gripper_params)
    return kinematics.snap_to_current(ik_joints, current)

  async def _stream_samples(
    self, samples: "List[kinematics.LinearPathSample]"
  ) -> None:
    """Stream a pre-built list of LinearPathSamples through the drive's IPM
    buffer. Caller MUST hold ``_motion_guard``. The samples' encoder positions
    and velocities are fed to each axis 8 frames ahead of the drive's read
    pointer at the fixed IPM cadence (``_PVT_DT_MS``).

    On cancel/exception the drive is brought back to PPM through a
    ``finally`` block. Coast on a cancel can run up to ``_PVT_PRELOAD
    * dt_ms`` (~64 ms) past the cancel — see ``halt()`` for zero-coast stop.
    """
    if len(samples) < 2:
      await self.ipm_select_mode(False)
      return

    # Skip axes with small total motion (Δ ≤ _SKIP_AXIS_COUNTS). Two reasons:
    # (1) the drive idles on sub-threshold motion and leaves frames stuck in
    # the IP buffer; (2) rotary axes (shoulder, wrist) wrap — IK gives
    # angles in (-180, 180] but the drive's encoder counts up across
    # revolutions, so streaming raw IK values for a rotary axis that's
    # currently many revolutions in causes the drive to interpret each
    # command as a huge multi-rotation move and tracking-error fault.
    all_axes = [int(ax) for ax in (Axis.SHOULDER, Axis.Z, Axis.ELBOW, Axis.WRIST)]
    active_axes: List[int] = []
    for nid in all_axes:
      seq = [s.encoder_position[Axis(nid)] for s in samples]
      if max(seq) - min(seq) > self._SKIP_AXIS_COUNTS:
        active_axes.append(nid)
    if not active_axes:
      return

    # Clear any sticky fault on the involved drives before streaming.
    await self.motors_ensure_enabled(active_axes)

    # Align the sample sequence to the drive's actual encoder positions.
    # Rotary axes (shoulder, wrist) wrap — the drive's encoder counts up
    # forever across full rotations, but IK gives joint angles in (-180,
    # 180]; sample[0] therefore lands many revolutions away from the drive's
    # actual position. Sending those raw triggers an immediate position-
    # tracking fault (drive rejects RPDO3, looks like queue_full to us).
    # Shift every sample's encoder_position by (actual_now - sample[0]) so
    # the trajectory rides on top of the drive's current encoder, preserving
    # all relative motion.
    enc_now = {}
    for nid in active_axes:
      enc_now[nid] = await self._motor_read_position_raw(nid)
    for nid in active_axes:
      offset = enc_now[nid] - samples[0].encoder_position[Axis(nid)]
      if offset == 0:
        continue
      for s in samples:
        s.encoder_position[Axis(nid)] += offset

    dt_s = self._PVT_DT_MS / 1000.0
    preload = min(self._PVT_PRELOAD, len(samples))

    await self.ipm_select_mode(True)
    try:
      await self.ipm_set_time_interval(self._PVT_DT_MS)
      # Preload PRELOAD frames per axis. The drive starts consuming as soon
      # as begin_motion fires; preload lets the producer fall behind by up
      # to (PRELOAD-1) * dt without underflowing the queue.
      for i in range(preload):
        for nid in active_axes:
          ax = Axis(nid)
          self.ipm_send_pvt_point(
            nid,
            samples[i].encoder_position[ax],
            samples[i].encoder_velocity[ax],
          )
      self.ipm_check_queue_fault(active_axes)

      # Capture pacing reference *before* begin_motion (drive starts consuming
      # on SYNC; capturing after underestimates elapsed drive-time and pace
      # too late, eating into the underflow margin).
      start = time.monotonic()
      await self.ipm_begin_motion(active_axes)

      for i in range(preload, len(samples)):
        target_t = (i - (preload - 1)) * dt_s
        while time.monotonic() - start < target_t:
          await asyncio.sleep(0)
        for nid in active_axes:
          ax = Axis(nid)
          self.ipm_send_pvt_point(
            nid,
            samples[i].encoder_position[ax],
            samples[i].encoder_velocity[ax],
          )
        self.ipm_check_queue_fault(active_axes)

      # Drop ip-enable immediately after the last frame. Elmo drives don't
      # latch SW bit-10 (target_reached) while ip-enable is high — polling
      # for it inside IP mode hangs forever — and leaving ip-enable asserted
      # past the buffer drain raises EMCY 0x8A on the next tick. Mirrors C#
      # MotorsMovePathExecute. Trade-off: drive halts ~0.3 mm short of the
      # trajectory end at typical speeds (matches the vendor behaviour).
      await self.ipm_stop(active_axes)
    finally:
      try:
        await self.ipm_stop(active_axes)
      except Exception:
        emcy_snap = {
          ax: vars(self._emcy[ax]) for ax in active_axes
          if ax in self._emcy
        }
        logger.exception(
          "ipm_stop cleanup failed; drive may still be in IPM with ip-enable "
          "high. EMCY state per axis: %s", emcy_snap,
        )
      try:
        await self.ipm_select_mode(False)
      except Exception:
        logger.exception(
          "ipm_select_mode(False) cleanup failed; next motion call will "
          "fresh-arm IPM via the re-arm path"
        )

  async def _run_linear_path(
    self,
    target_pose: CartesianPose,
    *,
    max_gripper_speed: Optional[float],
    max_gripper_acceleration: Optional[float],
  ) -> None:
    """Sample a straight tool-tip path to ``target_pose`` and stream it.
    Caller MUST hold ``_motion_guard``."""
    if max_gripper_speed is None or max_gripper_acceleration is None:
      raise ValueError(
        "move_to_location(path='linear') requires max_gripper_speed and "
        "max_gripper_acceleration: the Cartesian profile is built from them "
        "directly (no firmware fallback for streamed motion)."
      )
    current_joints = {
      Axis(k): v for k, v in (await self.request_joint_position()).items()
    }
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
    )
    await self._stream_samples(samples)

  async def move_parametric(
    self,
    path_fn: "Callable[[float], CartesianPose]",
    duration_s: float,
  ) -> None:
    """Stream a parametric Cartesian trajectory through IPM.

    ``path_fn(t)`` is called at every IPM sample time ``t ∈ [0, duration_s]``
    (seconds, evaluated at the drive's interpolation cadence, currently
    8 ms) and must return the absolute :class:`CartesianPose` the gripper
    should occupy at that instant. Use the start pose if you need offsets:

      start = await arm.request_gripper_pose()
      def fig8(t):
        s = t / duration_s
        theta = 2 * math.pi * s
        return CartesianPose(
          location=Coordinate(start.location.x + 50 * math.sin(theta),
                              start.location.y + 12.5 * math.sin(2*theta),
                              start.location.z + 30 * math.sin(math.pi * s)),
          rotation=start.rotation,
        )
      await arm.move_parametric(fig8, duration_s=8.0)

    No speed/accel cap — the parametrization sets the velocity profile;
    callers are responsible for keeping derivatives within drive limits.
    Joint travel limits are enforced via IK; out-of-range raises IKError
    before any motion. Path continuity is the caller's responsibility —
    discontinuities surface as drive faults.
    """
    if duration_s <= 0:
      raise ValueError(f"duration_s must be > 0, got {duration_s}")
    async with self._motion_guard():
      current_joints = {
        Axis(k): v for k, v in (await self.request_joint_position()).items()
      }
      samples = kinematics.sample_parametric_path(
        cfg=self._cfg,
        gripper_params=self._gripper_params,
        path_fn=path_fn,
        duration_s=duration_s,
        dt_s=self._PVT_DT_MS / 1000.0,
        current_joints=current_joints,
      )
      await self._stream_samples(samples)

  async def move_through_waypoints(
    self,
    waypoints: "List[CartesianPose]",
    *,
    speed: float,
    accel: float,
  ) -> None:
    """Stream a smooth Catmull-Rom spline through ``waypoints``.

    The curve passes through every waypoint with C¹ continuity (no stop at
    intermediate waypoints). Tangents at each interior waypoint are derived
    from neighbours; endpoint tangents are extrapolated from the first/last
    segment. The whole spline is time-reparametrized into a trapezoidal
    arc-length profile with peak Cartesian speed ``speed`` (mm/s) and peak
    acceleration ``accel`` (mm/s²), then sampled at the IPM cadence.

    Args:
      waypoints: at least 2 absolute Cartesian poses. The first should
        match the current pose closely (or the path will start with a
        Cartesian-linear segment to get there).
      speed: peak Cartesian speed along the spline, mm/s.
      accel: peak Cartesian acceleration, mm/s².

    Raises:
      ValueError if waypoints < 2 or speed/accel <= 0.
      IKError if any sample's joints are out of range.
    """
    if len(waypoints) < 2:
      raise ValueError(f"need at least 2 waypoints, got {len(waypoints)}")
    if speed <= 0 or accel <= 0:
      raise ValueError(f"speed and accel must be > 0, got {speed}, {accel}")
    async with self._motion_guard():
      current_joints = {
        Axis(k): v for k, v in (await self.request_joint_position()).items()
      }
      samples = kinematics.sample_waypoint_path(
        cfg=self._cfg,
        gripper_params=self._gripper_params,
        waypoints=waypoints,
        speed_mm_per_s=speed,
        accel_mm_per_s2=accel,
        dt_s=self._PVT_DT_MS / 1000.0,
        current_joints=current_joints,
      )
      await self._stream_samples(samples)

  # -- high-level arm API --

  async def halt(self) -> None:
    # Fire MO=0 on every motion axis concurrently — serial halts let later
    # axes coast for the duration of the earlier SDOs.
    #
    # Deliberately NOT guarded by _motion_guard: emergency-stop must
    # interrupt regardless of who's holding the lock.
    await asyncio.gather(
      *(self.motor_emergency_stop(node_id=axis) for axis in MOTION_AXES)
    )

  # Park pose (centered, well inside workspace) and motion caps.
  _PARK_JOINTS: Dict[Axis, float] = {
    Axis.SHOULDER: 2.0, Axis.Z: 750.0, Axis.ELBOW: 1.0, Axis.WRIST: 356.0,
  }
  _PARK_SPEED: float = 80.0
  _PARK_ACCEL: float = 400.0

  async def park(self) -> None:
    """Move the arm to a centered safe pose via a Cartesian-linear (IPM) move.

    Uses IPM (`path='linear'`) rather than PPM (move_to_joint_position) so the
    drives stay in mode 7 — back-to-back PPM → IPM transitions leave one or
    more drives' IP buffers in a state where the next preload write hits
    EMCY 0x34/0xBA (queue_full on first write). Sticking to IPM throughout
    sidesteps that.
    """
    park_pose = kinematics.fk(self._PARK_JOINTS, self._cfg, self._gripper_params)
    await self.move_to_location(
      location=park_pose.location,
      direction=park_pose.rotation.z,
      max_gripper_speed=self._PARK_SPEED,
      max_gripper_acceleration=self._PARK_ACCEL,
      path="linear",
    )

  async def request_gripper_pose(self) -> CartesianPose:
    joints = {Axis(k): v for k, v in (await self.request_joint_position()).items()}
    return kinematics.fk(joints, self._cfg, self._gripper_params)

  async def open_gripper(self, gripper_width: float) -> None:
    async with self._motion_guard():
      await self._motors_move_joint_locked({Axis.SERVO_GRIPPER: gripper_width})

  async def close_gripper(
    self,
    gripper_width: float,
    *,
    check_plate_gripped: bool = True,
    max_force_percent: Optional[int] = None,
  ) -> None:
    """Close the servo gripper to ``gripper_width``.

    Args:
      check_plate_gripped: after the close, verify the gripper stalled on a
        plate rather than reaching the fully-closed position.
      max_force_percent: 10..100, fraction of the gripper's full current
        rating used as the CL/PL torque cap during this close. ``None`` keeps
        whatever the last close (or setup) left in place.
    """
    async with self._motion_guard():
      if max_force_percent is not None:
        await self._set_servo_gripper_force_limit(max_force_percent)
      await self._motors_move_joint_locked({Axis.SERVO_GRIPPER: gripper_width})
      if check_plate_gripped:
        await self.check_plate_gripped()

  async def is_gripper_closed(self) -> bool:
    pos = await self.motor_get_current_position(Axis.SERVO_GRIPPER)
    return abs(pos) < 1.0

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
  # Below this trajectory delta (in encoder counts) an axis is considered
  # "not moving" and skipped from IPM streaming — the drive idles on
  # sub-threshold motion and leaves frames stuck in its IP buffer otherwise.
  _SKIP_AXIS_COUNTS: int = 500

  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    *,
    max_gripper_speed: Optional[float] = None,
    max_gripper_acceleration: Optional[float] = None,
    path: Literal["joint", "linear"] = "joint",
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
    """
    pose = CartesianPose(location=location, rotation=Rotation(z=direction))
    if path == "linear":
      async with self._motion_guard():
        await self._run_linear_path(
          pose,
          max_gripper_speed=max_gripper_speed,
          max_gripper_acceleration=max_gripper_acceleration,
        )
      return
    async with self._motion_guard():
      joint_pos = await self._cart_to_joints(pose)
      await self._motors_move_joint_locked(
        cmd_pos=joint_pos,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
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
  ) -> None:
    async with self._motion_guard():
      await self.move_to_location(
        location, direction,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )
      await self.close_gripper(resource_width, check_plate_gripped=check_plate_gripped)

  async def drop_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    *,
    max_gripper_speed: Optional[float] = None,
    max_gripper_acceleration: Optional[float] = None,
  ) -> None:
    async with self._motion_guard():
      await self.move_to_location(
        location, direction,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )
      await self.open_gripper(resource_width)

  async def move_to_joint_position(
    self,
    position: Dict[int, float],
    *,
    max_gripper_speed: Optional[float] = None,
    max_gripper_acceleration: Optional[float] = None,
  ) -> None:
    async with self._motion_guard():
      cmd_pos = {Axis(int(k)): float(v) for k, v in position.items()}
      await self._motors_move_joint_locked(
        cmd_pos=cmd_pos,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )

  async def pick_up_at_joint_position(
    self,
    position: Dict[int, float],
    resource_width: float,
    *,
    max_gripper_speed: Optional[float] = None,
    max_gripper_acceleration: Optional[float] = None,
    check_plate_gripped: bool = True,
  ) -> None:
    async with self._motion_guard():
      await self.move_to_joint_position(
        position,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )
      await self.close_gripper(resource_width, check_plate_gripped=check_plate_gripped)

  async def drop_at_joint_position(
    self,
    position: Dict[int, float],
    resource_width: float,
    *,
    max_gripper_speed: Optional[float] = None,
    max_gripper_acceleration: Optional[float] = None,
  ) -> None:
    async with self._motion_guard():
      await self.move_to_joint_position(
        position,
        max_gripper_speed=max_gripper_speed,
        max_gripper_acceleration=max_gripper_acceleration,
      )
      await self.open_gripper(resource_width)

  async def request_joint_position(self) -> Dict[int, float]:
    # Each motor_get_current_position is one BI query (one CAN round-trip,
    # ~5ms). Distinct (node_id, msg_type, msg_index) keys mean concurrent
    # in-flight requests can't collide in _send_bi's pending-future map, so
    # gather pipelines all five into one round-trip's worth of latency.
    axes = (Axis.SHOULDER, Axis.Z, Axis.ELBOW, Axis.WRIST, Axis.SERVO_GRIPPER)
    positions = await asyncio.gather(
      *(self.motor_get_current_position(ax) for ax in axes)
    )
    return dict(zip(axes, positions))

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

  async def start_freedrive_mode(self, free_axes: Optional[List[int]] = None) -> None:
    # Default: free all motion axes (shoulder/Z/elbow/wrist) but never the
    # gripper, so a held plate doesn't drop. Caller can override with an
    # explicit list; [0] means "all motion axes" (freedrive convention).
    if free_axes is None or free_axes == [0]:
      axes: List[int] = list(MOTION_AXES)
    else:
      axes = list(free_axes)
    async with self._motion_guard():
      for axis in axes:
        await self.motor_enable(node_id=axis, state=False, use_ds402=True)
      self._freedrive_axes = axes

  async def stop_freedrive_mode(self) -> None:
    axes: List[int] = self._freedrive_axes or list(MOTION_AXES)
    async with self._motion_guard():
      await self.motors_ensure_enabled([int(a) for a in axes])
      self._freedrive_axes = []

  async def very_dangerously_yeet(
    self,
    min_z: float = 400.0,
    bump: float = 1.25,
    force: bool = False,
  ) -> None:
    """Easter egg — swing the arm at firmware-max and open the gripper at
    peak velocity to throw whatever is being held.

    Call from your pickup pose. Sequence: auto-windup wrist to the inward
    angle, swing shoulder 180° at firmware-max, fire gripper open near end
    of cruise (with wrist flick at peak ω for extra tangential velocity),
    return to pickup pose.

    ``bump`` scales VH[2]/SP[2]/SD[0] on shoulder + wrist for the swing's
    duration (restored in finally). 1.0 = stock; 1.25 confirmed safe;
    higher risks tracking-error faults that need Elmo Composer recovery.

    ``force=True`` skips the interactive 'y' prompt — for scripted use or
    when the prompt path is broken (notebooks ran ``input`` in a thread
    pool where ipykernel's stdin hook doesn't reach, returning '' and
    aborting before the operator could type).
    """
    if not force:
      warning = (
        f"WARNING: very_dangerously_yeet: swing the arm at {bump:.2f}x firmware-max "
        "and open the gripper mid-swing. Anything in the gripper will be "
        "thrown. High bump can fault the drive. Type 'y' to continue: "
      )
      # Synchronous input(): python-can's reader thread keeps draining CAN
      # frames while we block. Don't punt to asyncio.to_thread — ipykernel
      # only services stdin requests on the main kernel thread.
      answer = input(warning)
      if answer.strip().lower() != "y":
        raise RuntimeError("very_dangerously_yeet: aborted by user")

    driver = self
    cfg = self._cfg

    # Hold the motion guard for the whole yeet — bumped VH/SP/SD on
    # SHOULDER+WRIST is a global drive-state mutation that mustn't overlap
    # with another caller's move.
    async with self._motion_guard():
      z_now = await self.motor_get_current_position(Axis.Z)
      if z_now < min_z:
        raise RuntimeError(
          f"yeet refused: Z={z_now:.0f}mm < min_z={min_z:.0f}mm; raise the arm first"
        )

      # Snapshot + bump VH[2]/SP[2]/SD[0] on swing axes; restore in finally.
      saved_limits: Dict[Axis, dict] = {}
      if bump != 1.0:
        for ax in (Axis.SHOULDER, Axis.WRIST):
          nid = ax
          s = {
            "VH2": await driver.query_int(nid, "VH", 2),
            "SP2": await driver.query_int(nid, "SP", 2),
            "SD0": await driver.query_int(nid, "SD", 0),
            "max_vel": cfg.axes[ax].max_vel,
            "max_accel": cfg.axes[ax].max_accel,
          }
          saved_limits[ax] = s
          new_vh2 = int(s["VH2"] * bump)
          new_sp2 = int(s["SP2"] * bump)
          new_sd0 = int(s["SD0"] * bump)
          await driver.write(nid, "VH", 2, new_vh2)
          await driver.write(nid, "SP", 2, new_sp2)
          await driver.write(nid, "SD", 0, new_sd0)
          conv = abs(cfg.axes[ax].motor_conversion_factor)
          cfg.axes[ax].max_vel = (new_sp2 / 1.01) / conv
          cfg.axes[ax].max_accel = (new_sd0 / 1.01) / conv

      try:
        pickup_pose = await self.request_joint_position()

        # Auto-windup: rotate wrist to the inward angle (opposite of outward).
        wrist_inward = 0.0 if self._gripper_params.finger_side == "barcode_reader" else 180.0
        while wrist_inward - pickup_pose[Axis.WRIST] > 180.0:
          wrist_inward -= 360.0
        while wrist_inward - pickup_pose[Axis.WRIST] < -180.0:
          wrist_inward += 360.0
        await self._motors_move_joint_locked(
          cmd_pos={Axis.WRIST: wrist_inward},
          max_gripper_speed=_YEET_WINDUP_GRIPPER_SPEED,
          max_gripper_acceleration=_YEET_WINDUP_GRIPPER_ACC,
        )

        joints0 = await self.request_joint_position()

        # Outward wrist = kinematic target (180° barcode_reader, 0° proximity).
        wrist_outward = 180.0 if self._gripper_params.finger_side == "barcode_reader" else 0.0
        while wrist_outward - joints0[Axis.WRIST] > 180.0:
          wrist_outward -= 360.0
        while wrist_outward - joints0[Axis.WRIST] < -180.0:
          wrist_outward += 360.0

        sh_move, sh_t_acc, sh_t_total, _ = await _yeet_build_axis_move(
          self, Axis.SHOULDER,
          joints0[Axis.SHOULDER], joints0[Axis.SHOULDER] + _YEET_SHOULDER_SWING_DEG,
        )
        wr_move, wr_t_acc, wr_t_total, _ = await _yeet_build_axis_move(
          self, Axis.WRIST, joints0[Axis.WRIST], wrist_outward,
        )
        sh_plan = MotorsMovePlan(moves=[sh_move], move_time=sh_t_total)
        wr_plan = MotorsMovePlan(moves=[wr_move], move_time=wr_t_total)

        # Release fires inside shoulder cruise. Wrist trigger is delayed so its
        # accel ramp finishes at release (peak ω at the gripper offset).
        sh_cruise_dur = max(0.0, sh_t_total - 2 * sh_t_acc)
        release_t = sh_t_acc + sh_cruise_dur * _YEET_RELEASE_FRACTION
        wrist_trigger_t = max(0.0, release_t - wr_t_acc)

        sg = Axis.SERVO_GRIPPER
        sg_cfg = cfg.axes[Axis.SERVO_GRIPPER]
        open_pos = min(_YEET_OPEN_POSITION, sg_cfg.max_travel - _YEET_OPEN_SAFETY_MARGIN)
        gripper_plan = MotorsMovePlan(moves=[MotorMoveParam(
          node_id=sg,
          position=int(round(open_pos * sg_cfg.motor_conversion_factor)),
          velocity=int(round(sg_cfg.max_vel * abs(sg_cfg.motor_conversion_factor))),
          acceleration=int(round(sg_cfg.max_accel * abs(sg_cfg.motor_conversion_factor))),
          direction=sg_cfg.joint_move_direction,
        )])

        # Pre-arm so triggers are pure control-word writes (sub-ms), not SDOs.
        await _yeet_arm_plan(driver, sh_plan)
        await _yeet_arm_plan(driver, wr_plan)

        await driver.ppm_begin_motion([Axis.SHOULDER])
        t0 = time.monotonic()
        await asyncio.sleep(max(0.0, wrist_trigger_t - (time.monotonic() - t0)))
        await driver.ppm_begin_motion([Axis.WRIST])

        await asyncio.sleep(max(0.0, release_t - (time.monotonic() - t0)))
        await self._motors_move_absolute_execute_locked(gripper_plan)

        # Settle slack: at higher bump, drives overshoot + ring before asserting
        # target-reached; tight margin trips a CanError even though throw was OK.
        swing_finish_t = max(sh_t_total, wrist_trigger_t + wr_t_total)
        await driver.wait_for_moves_done(
          [Axis.SHOULDER, Axis.WRIST], timeout=swing_finish_t + 5,
        )

        await self._motors_move_joint_locked(
          cmd_pos={
            Axis.SHOULDER: pickup_pose[Axis.SHOULDER],
            Axis.WRIST: pickup_pose[Axis.WRIST],
          },
          max_gripper_speed=_YEET_RETURN_GRIPPER_SPEED,
          max_gripper_acceleration=_YEET_RETURN_GRIPPER_ACC,
        )
      finally:
        for ax, s in saved_limits.items():
          nid = ax
          await driver.write(nid, "VH", 2, s["VH2"])
          await driver.write(nid, "SP", 2, s["SP2"])
          await driver.write(nid, "SD", 0, s["SD0"])
          cfg.axes[ax].max_vel = s["max_vel"]
          cfg.axes[ax].max_accel = s["max_accel"]



class _MotionLimits(Dict[Axis, tuple]):
  """Pretty-printing dict for `KX2.motion_limits()`. Dict access
  still works (`limits[Axis.Z]` -> `(max_speed, max_accel)`); `__repr__`
  formats it as an aligned ASCII table for both terminals and notebooks.
  """

  def __repr__(self) -> str:
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


# === very_dangerously_yeet helpers (easter egg) ============================
# Constants and helpers for KX2.very_dangerously_yeet. Inlined
# here on purpose; do not split into another module.

_YEET_SHOULDER_SWING_DEG = 180.0
_YEET_RELEASE_FRACTION = 0.85
# Gripper open target (mm). Clamped at runtime to drive's max_travel - margin.
_YEET_OPEN_POSITION = 30.0
_YEET_OPEN_SAFETY_MARGIN = 1.0
# Windup: arm holds the plate, don't whip.
_YEET_WINDUP_GRIPPER_SPEED = 100.0  # mm/s
_YEET_WINDUP_GRIPPER_ACC = 500.0    # mm/s^2
# Return: plate is gone, but still keep it gentle.
_YEET_RETURN_GRIPPER_SPEED = 100.0  # mm/s
_YEET_RETURN_GRIPPER_ACC = 500.0    # mm/s^2


async def _yeet_build_axis_move(
  backend: "KX2", ax: Axis, cur: float, target: float,
) -> tuple:
  """Per-axis MotorMoveParam at firmware velocity limit (VH[2]/1.01).
  Returns (move, t_acc, t_total, v_phys)."""
  cfg = backend._cfg
  ax_cfg = cfg.axes[ax]
  conv = ax_cfg.motor_conversion_factor
  vh2 = await backend.query_int(ax, "VH", 2)
  v_phys = vh2 / 1.01 / abs(conv)
  a_phys = ax_cfg.max_accel
  direction = ax_cfg.joint_move_direction

  d = target - cur
  span = ax_cfg.max_travel - ax_cfg.min_travel
  if span > 0 and ax_cfg.unlimited_travel:
    if direction == JointMoveDirection.Clockwise and d > 0.01:
      d -= span
    elif direction == JointMoveDirection.Counterclockwise and d < -0.01:
      d += span
    elif direction == JointMoveDirection.ShortestWay:
      if d > 180.0:
        d -= span
      elif d < -180.0:
        d += span
  dist = abs(d)

  if ax_cfg.unlimited_travel and direction != JointMoveDirection.Normal:
    target = kinematics._wrap_to_range(target, ax_cfg.min_travel, ax_cfg.max_travel)

  _, _, t_acc, t_total = kinematics._profile(dist, v_phys, a_phys)
  move = MotorMoveParam(
    node_id=ax,
    position=int(round(target * conv)),
    velocity=max(int(round(v_phys * abs(conv))), 1),
    acceleration=max(int(round(a_phys * abs(conv))), 1),
    direction=direction,
  )
  return move, t_acc, t_total, v_phys


async def _yeet_arm_plan(driver: KX2, plan: MotorsMovePlan) -> None:
  """Pre-load a plan onto the drives without triggering it. Splits SDO
  setup latency from the move start so the timer can be accurate."""
  await driver.ipm_select_mode(False)
  for move in plan.moves:
    nid = move.node_id
    await driver.motor_set_move_direction(nid, move.direction)
    await driver.can_sdo_download_elmo_object(
      nid, 24698, 0, int(move.position), _ElmoObjectDataType.INTEGER32,
    )
    await driver.can_sdo_download_elmo_object(
      nid, 24705, 0, int(move.velocity), _ElmoObjectDataType.UNSIGNED32,
    )
    acc = max(int(move.acceleration), 100)
    await driver.can_sdo_download_elmo_object(
      nid, 24707, 0, acc, _ElmoObjectDataType.UNSIGNED32,
    )
    await driver.can_sdo_download_elmo_object(
      nid, 24708, 0, acc, _ElmoObjectDataType.UNSIGNED32,
    )
