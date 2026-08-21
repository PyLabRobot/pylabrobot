"""Golden-frame tests: byte-for-byte wire output against a checked-in fixture.

``testdata/darwin_golden_frames.json`` holds ordered
``[node_id, dev_id, cmd_type, sub_command, cmd_val]`` packet sequences: the
expected byte-level output for each scenario, captured from a reference
implementation driving ``darwin.axis``/``darwin.motion``/``darwin.sequences``/
``darwin.controller`` through a recording fake Gemini device. Every test here
drives the equivalent call through an equivalent recording fake and asserts
the captured sequence matches the fixture exactly. The fixture is checked in
so a change in packet content, field order, or phase sequencing fails
immediately.

This is what actually exercises the commutation and homing state machines
(including retry-on-regression), coordinated multi-axis moves, jog, and the
W-axis parameter table end to end -- unit tests on individual helper
functions do not catch a wrong byte inside a multi-step homing or parameter-
apply sequence the way a full recorded comparison does.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from ..errors import BravoError
from ..protocol.gemini.engine import GeminiEngine
from ..protocol.gemini.enums import (
  FRAME_HEADER_SIZE,
  MSG_SYNC,
  NODE_BROADCAST,
  PROTOCOL_VERSION,
  CommandTypes,
  CommonSubCommands,
  GeminiSubCommands,
  MotorState,
  TCPMessageType,
)
from ..protocol.gemini.framing import (
  FrameHeader,
  MultipacketResponse,
  pack_packet_frame,
  unpack_multipacket_batch,
)
from ..protocol.gemini.instruction import pack_float32
from ..protocol.gemini.packet import BROADCAST_ADDRESS, InstructionAddress, Packet
from ..transport.base import Transport
from . import axis as axis_module
from . import motion, sequences
from .controller import DarwinController
from .params import ParameterAccess
from .topology import axis_address
from .waxis_params import apply_waxis_parameters

_GOLDEN_PATH = Path(__file__).parent / "testdata" / "darwin_golden_frames.json"
with open(_GOLDEN_PATH) as _f:
  GOLDEN: dict = json.load(_f)

PacketHandler = Callable[[Packet], Optional[Packet]]
BroadcastListener = Callable[[Packet], None]


class FakeGeminiTransport(Transport):
  """An in-memory fake Darwin controller, speaking framed Gemini over no socket.

  Mirrors the shape of a real device closely enough to drive
  :class:`~..protocol.gemini.engine.GeminiEngine` end to end: per-``(node,
  dev, sub_command)`` GET/SET handlers, broadcast listeners for trigger
  events, and a decoded log of every packet sent (including each sub-packet
  of a multipacket batch), in send order.
  """

  def __init__(self) -> None:
    """Create an empty fake device with no registered handlers."""
    self._cond = threading.Condition()
    self._buffer = bytearray()
    self._connected = True
    self.sent_packets: List[Packet] = []
    self._get_handlers: Dict[Tuple[int, int, int], PacketHandler] = {}
    self._set_handlers: Dict[Tuple[int, int, int], PacketHandler] = {}
    self._broadcast_listeners: List[BroadcastListener] = []

  # --- Handler registration ------------------------------------------------

  def on_get(self, addr: InstructionAddress, sub_command: int, handler: PacketHandler) -> None:
    """Register a handler for GETs to ``(addr, sub_command)``."""
    self._get_handlers[(addr.node_id, addr.dev_id, sub_command)] = handler

  def on_set(self, addr: InstructionAddress, sub_command: int, handler: PacketHandler) -> None:
    """Register a handler for SETs to ``(addr, sub_command)``."""
    self._set_handlers[(addr.node_id, addr.dev_id, sub_command)] = handler

  def on_broadcast(self, listener: BroadcastListener) -> None:
    """Register a callback fired for every broadcast SET packet."""
    self._broadcast_listeners.append(listener)

  # --- Transport interface ---------------------------------------------------

  def push_frame(self, data: bytes) -> None:
    """Append a fully-framed response directly to the receive buffer.

    Used by broadcast listeners to push an asynchronous echo (e.g. a
    move-complete SEND_EVT) outside the normal send/response cycle.

    Args:
      data: The complete framed bytes to make available to the next read.
    """
    with self._cond:
      self._buffer.extend(data)
      self._cond.notify_all()

  def send(self, data: bytes) -> None:
    """Decode a sent frame, record its packets, and enqueue any reply.

    Args:
      data: The complete framed bytes sent by the engine.
    """
    header = FrameHeader.from_bytes(data[:FRAME_HEADER_SIZE])
    payload = data[FRAME_HEADER_SIZE : FRAME_HEADER_SIZE + header.payload_size]
    if header.payload_type == TCPMessageType.PACKET:
      pkt = Packet.from_bytes(payload)
      self.sent_packets.append(pkt)
      resp = self._handle_packet(pkt)
      if resp is not None:
        self.push_frame(pack_packet_frame(resp))
    elif header.payload_type == TCPMessageType.MULTIPACKET:
      packets = unpack_multipacket_batch(payload)
      self.sent_packets.extend(packets)
      for p in packets:
        self._handle_packet(p)
      mp_resp = MultipacketResponse(
        num_exchanges=len(packets), error_code=0, error_device_addr=0, device_error_nak=0
      )
      resp_bytes = mp_resp.to_bytes()
      resp_header = FrameHeader(
        msg_sync=MSG_SYNC,
        protocol_version=PROTOCOL_VERSION,
        payload_type=TCPMessageType.MULTIPACKET,
        payload_size=len(resp_bytes),
      )
      self.push_frame(resp_header.to_bytes() + resp_bytes)

  def _handle_packet(self, pkt: Packet) -> Optional[Packet]:
    """Dispatch one decoded packet to its registered handler.

    Args:
      pkt: The received packet.

    Returns:
      The response packet, or ``None`` for a broadcast (which gets no
      reply) or an unhandled command type.
    """
    if pkt.dest.node_id == NODE_BROADCAST:
      for listener in self._broadcast_listeners:
        listener(pkt)
      return None
    key = (pkt.dest.node_id, pkt.dest.dev_id, pkt.sub_command)
    if pkt.cmd_type == CommandTypes.GETCMD:
      handler = self._get_handlers.get(key)
      if handler is not None:
        custom = handler(pkt)
        if custom is not None:
          return custom
      return Packet(
        src=pkt.dest,
        dest=pkt.src,
        cmd_type=CommandTypes.GETCMD_RESP,
        sub_command=pkt.sub_command,
        cmd_val=0,
      )
    if pkt.cmd_type == CommandTypes.SETCMD:
      handler = self._set_handlers.get(key)
      if handler is not None:
        custom = handler(pkt)
        if custom is not None:
          return custom
      return Packet(
        src=pkt.dest,
        dest=pkt.src,
        cmd_type=CommandTypes.SETCMD_RESP,
        sub_command=pkt.sub_command,
        cmd_val=0,
      )
    return None

  def receive(self, timeout: float = 2.0) -> bytes:
    """Return whatever is currently buffered, waiting up to ``timeout`` for data."""
    with self._cond:
      if not self._buffer:
        self._cond.wait(timeout)
      data = bytes(self._buffer)
      self._buffer.clear()
      return data

  def receive_exact(self, num_bytes: int, timeout: float = 2.0) -> bytes:
    """Block until exactly ``num_bytes`` are available, or raise on timeout."""
    deadline = time.monotonic() + timeout
    with self._cond:
      while len(self._buffer) < num_bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          raise TimeoutError(f"FakeGeminiTransport timed out waiting for {num_bytes} bytes")
        self._cond.wait(remaining)
      chunk = bytes(self._buffer[:num_bytes])
      del self._buffer[:num_bytes]
      return chunk

  @property
  def is_connected(self) -> bool:
    """Always True: this fake has no real connection to lose."""
    return self._connected


def _capture(fake: FakeGeminiTransport) -> List[list]:
  """Encode every packet the fake recorded as a JSON-comparable list.

  Args:
    fake: The fake transport to read from.

  Returns:
    A list of ``[node_id, dev_id, cmd_type, sub_command, cmd_val]`` entries,
    one per recorded packet, in send order.
  """
  return [
    [p.dest.node_id, p.dest.dev_id, int(p.cmd_type), int(p.sub_command), int(p.cmd_val)]
    for p in fake.sent_packets
  ]


class _StateSim:
  """Motor-state simulator: reports "still pending" for a fixed number of
  reads, then the target state -- deterministic by call count, not by
  wall-clock time, so the captured packet sequence never depends on
  scheduling jitter.
  """

  def __init__(self, settle_after: int = 3):
    self._lock = threading.Lock()
    self._state = MotorState.INITIAL
    self._settle_after = settle_after
    self._pending: Optional[MotorState] = None
    self._reads_since_pending = 0

  def current_state(self) -> MotorState:
    with self._lock:
      if self._pending is not None:
        self._reads_since_pending += 1
        if self._reads_since_pending >= self._settle_after:
          self._state = self._pending
          self._pending = None
      return self._state

  def set_state(self, requested: MotorState) -> None:
    with self._lock:
      if requested == MotorState.COMMUTATE:
        self._state = MotorState.COMMUTATE
        self._pending = MotorState.COMMUTATED
        self._reads_since_pending = 0
      elif requested == MotorState.HOME:
        self._state = MotorState.HOME
        self._pending = MotorState.READY
        self._reads_since_pending = 0
      elif requested == MotorState.DISABLE:
        self._state = MotorState.DISABLED
        self._pending = None
      elif requested == MotorState.ENABLE:
        self._state = MotorState.READY
        self._pending = None
      else:
        self._state = requested

  def force(self, state: MotorState) -> None:
    with self._lock:
      self._state = state
      self._pending = None


def _install_state_sim(fake: FakeGeminiTransport, addr: InstructionAddress, sim: _StateSim) -> None:
  """Wire a :class:`_StateSim` to a fake device's MOTOR_STATE GET/SET."""

  def get_handler(pkt: Packet) -> Packet:
    return Packet(
      src=pkt.dest,
      dest=pkt.src,
      cmd_type=CommandTypes.GETCMD_RESP,
      sub_command=pkt.sub_command,
      cmd_val=int(sim.current_state()),
    )

  def set_handler(pkt: Packet) -> Packet:
    try:
      requested = MotorState(pkt.cmd_val)
    except ValueError:
      requested = MotorState.INITIAL
    sim.set_state(requested)
    return Packet(
      src=pkt.dest,
      dest=pkt.src,
      cmd_type=CommandTypes.SETCMD_RESP,
      sub_command=pkt.sub_command,
      cmd_val=0,
    )

  fake.on_get(addr, GeminiSubCommands.MOTOR_STATE, get_handler)
  fake.on_set(addr, GeminiSubCommands.MOTOR_STATE, set_handler)


class _MotionSim:
  """Simulated axis that echoes SEND_EVT after a delay, mirroring real hardware."""

  def __init__(self, address: InstructionAddress, complete_s: float = 0.005):
    self.address = address
    self.state = MotorState.READY
    self.complete_s = complete_s
    self.start_event: Optional[int] = None
    self.send_event: Optional[int] = None
    self.position = 0.0
    self._fake: Optional[FakeGeminiTransport] = None
    self._lock = threading.Lock()

  def install(self, fake: FakeGeminiTransport) -> None:
    self._fake = fake
    fake.on_get(self.address, GeminiSubCommands.MOTOR_STATE, self._get_state)
    fake.on_set(self.address, GeminiSubCommands.MOTOR_STATE, self._set_motor_state)
    fake.on_set(self.address, GeminiSubCommands.START_EVT, self._set_start)
    fake.on_set(self.address, GeminiSubCommands.SEND_EVT, self._set_send)
    fake.on_get(self.address, GeminiSubCommands.POSITION, self._get_position)
    fake.on_broadcast(self._broadcast)

  def _set_send(self, pkt: Packet) -> Packet:
    with self._lock:
      self.send_event = pkt.cmd_val
    return Packet(
      src=pkt.dest,
      dest=pkt.src,
      cmd_type=CommandTypes.SETCMD_RESP,
      sub_command=pkt.sub_command,
      cmd_val=0,
    )

  def _set_motor_state(self, pkt: Packet) -> Packet:
    with self._lock:
      try:
        self.state = MotorState(pkt.cmd_val)
      except ValueError:
        pass
    return Packet(
      src=pkt.dest,
      dest=pkt.src,
      cmd_type=CommandTypes.SETCMD_RESP,
      sub_command=pkt.sub_command,
      cmd_val=0,
    )

  def _get_state(self, pkt: Packet) -> Packet:
    with self._lock:
      return Packet(
        src=pkt.dest,
        dest=pkt.src,
        cmd_type=CommandTypes.GETCMD_RESP,
        sub_command=pkt.sub_command,
        cmd_val=int(self.state),
      )

  def _get_position(self, pkt: Packet) -> Packet:
    with self._lock:
      return Packet(
        src=pkt.dest,
        dest=pkt.src,
        cmd_type=CommandTypes.GETCMD_RESP,
        sub_command=pkt.sub_command,
        cmd_val=pack_float32(self.position),
      )

  def _set_start(self, pkt: Packet) -> Packet:
    with self._lock:
      self.start_event = pkt.cmd_val
      self.state = MotorState.BUSY
    return Packet(
      src=pkt.dest,
      dest=pkt.src,
      cmd_type=CommandTypes.SETCMD_RESP,
      sub_command=pkt.sub_command,
      cmd_val=0,
    )

  def _broadcast(self, pkt: Packet) -> None:
    if pkt.sub_command != CommonSubCommands.TRIGGER:
      return
    with self._lock:
      if self.start_event is None or pkt.cmd_val != self.start_event:
        return
      complete_at = time.monotonic() + self.complete_s
      send_event = self.send_event
      fake = self._fake

    def completer() -> None:
      while time.monotonic() < complete_at:
        time.sleep(0.001)
      with self._lock:
        self.state = MotorState.READY
        self.position = 0.6  # Settle position read back by jog's validation.
      if send_event is not None and fake is not None:
        echo = Packet(
          src=self.address,
          dest=BROADCAST_ADDRESS,
          cmd_type=CommandTypes.SETCMD,
          sub_command=CommonSubCommands.TRIGGER,
          cmd_val=send_event,
        )
        fake.push_frame(pack_packet_frame(echo))

    threading.Thread(target=completer, daemon=True).start()


class GoldenFrameTestCase(unittest.TestCase):
  """Base class for Darwin golden-frame comparisons."""

  def assert_matches_golden(self, scenario: str, calls: List[list]) -> None:
    expected = GOLDEN[scenario]
    self.assertEqual(calls, expected, f"{scenario}: captured frames diverge from golden")


class AxisGoldenTests(GoldenFrameTestCase):
  """Commutation, homing (with retry-on-regression), and initialize."""

  def test_commutate_normal(self):
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("x")
      sim = _StateSim(settle_after=3)
      _install_state_sim(fake, addr, sim)
      axis_module.commutate(engine, addr, "X", poll=0.002, timeout=2.0)
    finally:
      engine.stop_receiving()
    self.assert_matches_golden("axis_commutate_normal", _capture(fake))

  def test_commutate_retry_on_regression(self):
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("x")
      sim = _StateSim(settle_after=3)
      counts = {"n": 0}

      def set_handler(pkt: Packet) -> Packet:
        try:
          requested = MotorState(pkt.cmd_val)
        except ValueError:
          requested = MotorState.INITIAL
        if requested == MotorState.COMMUTATE:
          counts["n"] += 1
          if counts["n"] == 1:
            sim.set_state(MotorState.COMMUTATE)
            sim.force(MotorState.INITIAL)
          else:
            sim.set_state(MotorState.COMMUTATE)
        else:
          sim.set_state(requested)
        return Packet(
          src=pkt.dest,
          dest=pkt.src,
          cmd_type=CommandTypes.SETCMD_RESP,
          sub_command=pkt.sub_command,
          cmd_val=0,
        )

      def get_handler(pkt: Packet) -> Packet:
        return Packet(
          src=pkt.dest,
          dest=pkt.src,
          cmd_type=CommandTypes.GETCMD_RESP,
          sub_command=pkt.sub_command,
          cmd_val=int(sim.current_state()),
        )

      fake.on_set(addr, GeminiSubCommands.MOTOR_STATE, set_handler)
      fake.on_get(addr, GeminiSubCommands.MOTOR_STATE, get_handler)
      axis_module.commutate(engine, addr, "X", poll=0.005, timeout=2.0)
    finally:
      engine.stop_receiving()
    self.assert_matches_golden("axis_commutate_retry_on_regression", _capture(fake))

  def test_home_normal(self):
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("y")
      sim = _StateSim(settle_after=3)
      sim.force(MotorState.COMMUTATED)
      _install_state_sim(fake, addr, sim)
      axis_module.home(engine, addr, "Y", poll=0.002, timeout=2.0)
    finally:
      engine.stop_receiving()
    self.assert_matches_golden("axis_home_normal", _capture(fake))

  def test_home_retry_on_regression(self):
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("y")
      sim = _StateSim(settle_after=3)
      sim.force(MotorState.COMMUTATED)
      home_sets = {"n": 0}

      def set_handler(pkt: Packet) -> Packet:
        try:
          requested = MotorState(pkt.cmd_val)
        except ValueError:
          requested = MotorState.INITIAL
        if requested == MotorState.HOME:
          home_sets["n"] += 1
          if home_sets["n"] == 1:
            sim.force(MotorState.HOME)
            sim.force(MotorState.COMMUTATED)
          else:
            sim.set_state(MotorState.HOME)
        elif requested == MotorState.COMMUTATE:
          sim.set_state(MotorState.COMMUTATE)
        else:
          sim.set_state(requested)
        return Packet(
          src=pkt.dest,
          dest=pkt.src,
          cmd_type=CommandTypes.SETCMD_RESP,
          sub_command=pkt.sub_command,
          cmd_val=0,
        )

      def get_handler(pkt: Packet) -> Packet:
        return Packet(
          src=pkt.dest,
          dest=pkt.src,
          cmd_type=CommandTypes.GETCMD_RESP,
          sub_command=pkt.sub_command,
          cmd_val=int(sim.current_state()),
        )

      fake.on_set(addr, GeminiSubCommands.MOTOR_STATE, set_handler)
      fake.on_get(addr, GeminiSubCommands.MOTOR_STATE, get_handler)
      axis_module.home(engine, addr, "Y", poll=0.002, timeout=2.0)
    finally:
      engine.stop_receiving()
    self.assert_matches_golden("axis_home_retry_on_regression", _capture(fake))

  def test_initialize(self):
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("x")
      sim = _StateSim(settle_after=3)
      _install_state_sim(fake, addr, sim)
      axis_module.initialize(engine, addr, "X", commutate_timeout=2.0, home_timeout=2.0)
    finally:
      engine.stop_receiving()
    self.assert_matches_golden("axis_initialize", _capture(fake))


class MotionGoldenTests(GoldenFrameTestCase):
  """Single- and multi-axis coordinated moves."""

  def test_move_single_axis(self):
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("x")
      sim = _MotionSim(addr, complete_s=0.005)
      sim.install(fake)
      motion.move_absolute(
        engine, addr, "X", 0.6, velocity_percent=80.0, acceleration_percent=90.0, timeout=2.0
      )
    finally:
      engine.stop_receiving()
    self.assert_matches_golden("move_single_axis", _capture(fake))

  def test_move_multi_axis(self):
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      x_addr = axis_address("x")
      y_addr = axis_address("y")
      x_sim = _MotionSim(x_addr, complete_s=0.005)
      y_sim = _MotionSim(y_addr, complete_s=0.008)
      x_sim.install(fake)
      y_sim.install(fake)
      reqs = [
        motion.MoveRequest(
          address=x_addr,
          axis_name="X",
          target_normalized=0.5,
          velocity_percent=100.0,
          acceleration_percent=100.0,
        ),
        motion.MoveRequest(
          address=y_addr,
          axis_name="Y",
          target_normalized=0.4,
          velocity_percent=100.0,
          acceleration_percent=100.0,
        ),
      ]
      motion.move_multi(engine, reqs, timeout=2.0)
    finally:
      engine.stop_receiving()
    self.assert_matches_golden("move_multi_axis", _capture(fake))


class SequencesGoldenTests(GoldenFrameTestCase):
  """Force-controlled jog."""

  def test_jog_z_axis(self):
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("z")
      sim = _MotionSim(addr, complete_s=0.005)
      sim.install(fake)

      def read_pos(engine: GeminiEngine, a: InstructionAddress) -> float:
        return engine.get_float(a, GeminiSubCommands.POSITION)

      sequences.jog(
        engine,
        addr,
        None,  # type: ignore[arg-type]
        sequences.JogParams(
          axis_name="Z",
          target_position=0.5,
          tolerance=0.2,
          peak_current_amps=0.3,
          velocity_mm=50.0,
          acceleration_mm=500.0,
          velocity_limit=150.0,
          acceleration_limit=1500.0,
          exceed_epsilon=0.05,
        ),
        read_position=read_pos,
        timeout=2.0,
        settle=0.001,
      )
    finally:
      engine.stop_receiving()
    self.assert_matches_golden("jog_z_axis", _capture(fake))


class WaxisParamsGoldenTests(GoldenFrameTestCase):
  """The 57-entry W-axis parameter apply."""

  def test_apply_waxis_parameters_96_d_70(self):
    fake = FakeGeminiTransport()
    engine = GeminiEngine(fake)
    engine.start_receiving()
    try:
      addr = axis_address("w")
      params = ParameterAccess(engine, addr)
      apply_waxis_parameters(params, "96_d_70")
    finally:
      engine.stop_receiving()
    self.assert_matches_golden("waxis_param_apply_96_d_70", _capture(fake))


class ControllerGoldenTests(GoldenFrameTestCase):
  """DarwinController.initialize()."""

  def test_controller_initialize(self):
    fake = FakeGeminiTransport()
    ctrl = DarwinController(fake)
    try:
      ctrl.initialize()
    except BravoError:
      pass
    finally:
      ctrl.deinitialize()
    self.assert_matches_golden("controller_initialize", _capture(fake))


if __name__ == "__main__":
  unittest.main()
