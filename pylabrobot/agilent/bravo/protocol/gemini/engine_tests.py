import threading
import time
import unittest
from typing import Callable, Optional

from pylabrobot.agilent.bravo.protocol.gemini.engine import _RX_POLL_S, GeminiEngine
from pylabrobot.agilent.bravo.protocol.gemini.enums import CommandTypes, CommonSubCommands
from pylabrobot.agilent.bravo.protocol.gemini.errors import GeminiTimeoutError, NAKError
from pylabrobot.agilent.bravo.protocol.gemini.framing import FrameHeader, pack_packet_frame
from pylabrobot.agilent.bravo.protocol.gemini.packet import InstructionAddress, Packet
from pylabrobot.agilent.bravo.transport import Transport


class LoopbackTransport(Transport):
  """An in-memory stand-in for a Darwin controller's TCP connection.

  Every ``send()`` is handed to an optional ``responder`` callback, whose
  return value (if any) is queued for the next ``receive``/``receive_exact``
  to read back -- the minimum needed to drive :class:`GeminiEngine`'s
  request/response and background receive-thread logic without real sockets.
  """

  def __init__(self, responder: Optional[Callable[[bytes], Optional[bytes]]] = None):
    self._cond = threading.Condition()
    self._buffer = bytearray()
    self.sent_frames: list = []
    self._connected = True
    self.responder = responder

  def send(self, data: bytes) -> None:
    self.sent_frames.append(data)
    if self.responder is not None:
      reply = self.responder(data)
      if reply:
        with self._cond:
          self._buffer.extend(reply)
          self._cond.notify_all()

  def receive(self, timeout: float = 2.0) -> bytes:
    with self._cond:
      if not self._buffer:
        self._cond.wait(timeout)
      data = bytes(self._buffer)
      self._buffer.clear()
      return data

  def receive_exact(self, num_bytes: int, timeout: float = 2.0) -> bytes:
    deadline = time.monotonic() + timeout
    with self._cond:
      while len(self._buffer) < num_bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          raise TimeoutError(f"LoopbackTransport timed out waiting for {num_bytes} bytes")
        self._cond.wait(remaining)
      chunk = bytes(self._buffer[:num_bytes])
      del self._buffer[:num_bytes]
      return chunk

  @property
  def is_connected(self) -> bool:
    return self._connected


def _get_resp_responder(value: int):
  def _respond(frame: bytes) -> Optional[bytes]:
    header = FrameHeader.from_bytes(frame[:8])
    packet = Packet.from_bytes(frame[8 : 8 + header.payload_size])
    if packet.cmd_type != CommandTypes.GETCMD:
      return None
    resp = Packet(
      src=packet.dest,
      dest=packet.src,
      cmd_type=CommandTypes.GETCMD_RESP,
      sub_command=packet.sub_command,
      cmd_val=value,
    )
    return pack_packet_frame(resp)

  return _respond


def _nak_responder(nak_code: int):
  def _respond(frame: bytes) -> Optional[bytes]:
    header = FrameHeader.from_bytes(frame[:8])
    packet = Packet.from_bytes(frame[8 : 8 + header.payload_size])
    resp = Packet(
      src=packet.dest,
      dest=packet.src,
      cmd_type=CommandTypes.GETCMD_ERR_RESP,
      sub_command=packet.sub_command,
      cmd_val=nak_code,
    )
    return pack_packet_frame(resp)

  return _respond


class GeminiEngineLifecycleTests(unittest.TestCase):
  def test_start_receiving_starts_rx_thread_and_stop_receiving_stops_it(self):
    engine = GeminiEngine(LoopbackTransport())
    self.assertFalse(engine.is_connected)
    engine.start_receiving()
    self.assertTrue(engine.is_connected)
    engine.stop_receiving()
    self.assertFalse(engine.is_connected)

  def test_start_receiving_requires_a_connected_transport(self):
    transport = LoopbackTransport()
    transport._connected = False
    engine = GeminiEngine(transport)
    with self.assertRaises(RuntimeError):
      engine.start_receiving()

  def test_context_manager(self):
    with GeminiEngine(LoopbackTransport()) as engine:
      self.assertTrue(engine.is_connected)
    self.assertFalse(engine.is_connected)

  def test_rx_poll_interval_is_in_seconds(self):
    # The receive-thread poll interval is expressed in seconds, not milliseconds.
    self.assertEqual(_RX_POLL_S, 0.1)


class GeminiEngineGetSetTests(unittest.TestCase):
  def setUp(self):
    self.address = InstructionAddress(4)

  def test_get_value_returns_response(self):
    transport = LoopbackTransport(responder=_get_resp_responder(0x2A))
    with GeminiEngine(transport) as engine:
      value = engine.get_value(self.address, CommonSubCommands.FW_VERSION, timeout=1.0)
    self.assertEqual(value, 0x2A)

  def test_get_float_decodes_ieee754(self):
    from pylabrobot.agilent.bravo.protocol.gemini.instruction import pack_float32

    raw = pack_float32(3.5)
    transport = LoopbackTransport(responder=_get_resp_responder(raw))
    with GeminiEngine(transport) as engine:
      value = engine.get_float(self.address, CommonSubCommands.FW_VERSION, timeout=1.0)
    self.assertAlmostEqual(value, 3.5, places=3)

  def test_get_value_raises_nak_error(self):
    transport = LoopbackTransport(responder=_nak_responder(3))  # OUT_OF_RANGE
    with GeminiEngine(transport) as engine:
      with self.assertRaises(NAKError):
        engine.get_value(self.address, CommonSubCommands.FW_VERSION, timeout=1.0)

  def test_get_value_timeout_carries_seconds_not_milliseconds(self):
    transport = LoopbackTransport(responder=None)  # never answers
    with GeminiEngine(transport) as engine:
      start = time.monotonic()
      with self.assertRaises(GeminiTimeoutError) as ctx:
        engine.get_value(self.address, CommonSubCommands.FW_VERSION, timeout=0.05)
      elapsed = time.monotonic() - start
    # The wait genuinely spans close to the 0.05s given: an upper bound alone
    # does not rule out the wait finishing early (e.g. a timeout silently
    # divided by 1000), so both bounds are asserted.
    self.assertGreaterEqual(elapsed, 0.04)
    self.assertLess(elapsed, 2.0)
    self.assertEqual(ctx.exception.timeout, 0.05)

  def test_broadcast_set_does_not_wait_for_response(self):
    transport = LoopbackTransport(responder=None)
    broadcast = InstructionAddress(63)
    with GeminiEngine(transport) as engine:
      start = time.monotonic()
      engine.set_uint(broadcast, CommonSubCommands.TRIGGER, value=1, timeout=5.0)
      elapsed = time.monotonic() - start
    # Broadcasts sleep BROADCAST_WAIT_MS (6ms), not the 5s request timeout.
    self.assertLess(elapsed, 1.0)

  def test_set_uint_timeout_is_in_seconds(self):
    transport = LoopbackTransport(responder=None)  # never answers
    with GeminiEngine(transport) as engine:
      start = time.monotonic()
      with self.assertRaises(GeminiTimeoutError) as ctx:
        engine.set_uint(self.address, CommonSubCommands.TRIGGER, value=1, timeout=0.05)
      elapsed = time.monotonic() - start
    self.assertGreaterEqual(elapsed, 0.04)
    self.assertLess(elapsed, 2.0)
    self.assertEqual(ctx.exception.timeout, 0.05)

  def test_send_multipacket_timeout_is_in_seconds(self):
    transport = LoopbackTransport(responder=None)  # never answers
    packets = [Packet.set_request(self.address, CommonSubCommands.TRIGGER, 1)]
    with GeminiEngine(transport) as engine:
      start = time.monotonic()
      with self.assertRaises(GeminiTimeoutError) as ctx:
        engine.send_multipacket(packets, timeout=0.05)
      elapsed = time.monotonic() - start
    self.assertGreaterEqual(elapsed, 0.04)
    self.assertLess(elapsed, 2.0)
    self.assertEqual(ctx.exception.timeout, 0.05)

  def test_send_serial_timeout_is_in_seconds(self):
    transport = LoopbackTransport(responder=None)  # never answers
    with GeminiEngine(transport) as engine:
      start = time.monotonic()
      with self.assertRaises(GeminiTimeoutError) as ctx:
        engine.send_serial(bytes(range(9)), timeout=0.05)
      elapsed = time.monotonic() - start
    self.assertGreaterEqual(elapsed, 0.04)
    self.assertLess(elapsed, 2.0)
    self.assertEqual(ctx.exception.timeout, 0.05)


class GeminiEngineTriggerCallbackTests(unittest.TestCase):
  def test_broadcast_trigger_is_self_routed_to_callbacks(self):
    transport = LoopbackTransport(responder=None)
    received = []
    with GeminiEngine(transport) as engine:
      engine.on_trigger(lambda pkt: received.append(pkt.cmd_val))
      engine.set_uint(InstructionAddress(63), CommonSubCommands.TRIGGER, value=0x99, timeout=1.0)
      deadline = time.monotonic() + 2.0
      while not received and time.monotonic() < deadline:
        time.sleep(0.01)
    self.assertEqual(received, [0x99])

  def test_wait_for_trigger_event_matches_value(self):
    transport = LoopbackTransport(responder=None)
    with GeminiEngine(transport) as engine:

      def _fire():
        time.sleep(0.05)
        engine.set_uint(InstructionAddress(63), CommonSubCommands.TRIGGER, value=42, timeout=1.0)

      threading.Thread(target=_fire, daemon=True).start()
      matched = engine.wait_for_trigger_event(42, timeout=2.0)
    self.assertTrue(matched)

  def test_wait_for_trigger_event_times_out_in_seconds(self):
    transport = LoopbackTransport(responder=None)
    with GeminiEngine(transport) as engine:
      start = time.monotonic()
      matched = engine.wait_for_trigger_event(0xDEAD, timeout=0.1)
      elapsed = time.monotonic() - start
    self.assertFalse(matched)
    self.assertLess(elapsed, 1.0)


if __name__ == "__main__":
  unittest.main()
