"""Presto public operations exercised through scripted HID reports, without hardware."""

import base64
import unittest
import xml.etree.ElementTree as ET
import zlib
from collections import deque
from typing import Any, Optional

import anyio
import anyio.lowlevel
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.hid import HID
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.thermo_fisher import KingFisherPresto
from pylabrobot.thermo_fisher.kingfisher.bdz import BdzProtocol, MixStep, ProtocolTip, write_bdz
from pylabrobot.thermo_fisher.kingfisher.presto import PrestoError
from pylabrobot.thermo_fisher.kingfisher.presto.connection import XMLMessages


class ScriptedHID(HID):
  """Deliver XML as framed input reports and inspect the full transmitted command."""

  def __init__(self) -> None:
    super().__init__("test Presto", 0x0AB6, 0x02C9)
    self.incoming: deque[bytes | Exception] = deque()
    self.incoming_ready = anyio.Event()
    self.commands: list[ET.Element] = []
    self.reports: list[bytes] = []
    self.features: list[bytes] = []
    self.pending = bytearray()
    self.opened = 0
    self.closed = 0
    self.sent = anyio.Event()
    self.responses: dict[str, Optional[bytes]] = {
      "Connect": (
        b'<Res name="Connect" ok="true"><Instrument>KingFisher Presto</Instrument>'
        b"<Version>1.0</Version><Serial>123</Serial></Res>"
      ),
      "GetStatus": b'<Res name="GetStatus" ok="true"><Status>Idle</Status></Res>',
      "Rotate": b'<Res name="Rotate" ok="true"/><Evt name="Temperature"/><Evt name="Ready"/>',
    }

  def enqueue(self, item: bytes | Exception) -> None:
    """Queue one raw report or transport failure and wake any blocked reader."""
    self.incoming.append(item)
    self.incoming_ready.set()

  def reset_sent(self) -> None:
    """Create a fresh event signaled when the next newline-terminated command is written."""
    self.sent = anyio.Event()

  def emit(self, data: bytes) -> None:
    """Split data at report boundaries, including XML and UTF-8 boundaries."""
    for offset in range(0, len(data), 63):
      chunk = data[offset : offset + 63]
      self.enqueue(bytes([len(chunk)]) + chunk.ljust(63, b"\x00"))

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Record opening and closing without invoking hidapi."""
    await HasLifespan._enter_lifespan(self, stack)
    self.opened += 1
    self.pending.clear()
    self.incoming = deque()
    self.incoming_ready = anyio.Event()
    self.sent = anyio.Event()
    stack.callback(self._record_close)

  def _record_close(self) -> None:
    """Record transport teardown."""
    self.closed += 1

  async def write(self, data: bytes, report_id: bytes = b"\x00") -> int:
    """Assemble output reports and respond once the newline terminator arrives."""
    await anyio.lowlevel.checkpoint()
    self.reports.append(report_id + data)
    self.pending.extend(data[1 : 1 + data[0]])
    if self.pending.endswith(b"\n"):
      payload = bytes(self.pending)
      if payload != b"\x1b\n":
        try:
          command = ET.fromstring(payload)
        except ET.ParseError:
          return len(data) + len(report_id)
        self.commands.append(command)
        name = command.get("name", "")
        response = self.responses.get(name, f'<Res name="{name}" ok="true"/>'.encode())
        if response is not None:
          self.emit(response)
      self.pending.clear()
      self.sent.set()
    return len(data) + len(report_id)

  async def read(self, size: int = 64, timeout: Optional[int | Any] = None) -> bytes:
    """Wait for the next script entry; exceptions model transport failure."""
    if timeout is not None:
      raise AssertionError(
        "PrestoConnection._read_loop must not pass deprecated timeout= to HID.read"
      )
    while not self.incoming:
      ready = self.incoming_ready
      await ready.wait()
      if self.incoming_ready is ready and not self.incoming:
        self.incoming_ready = anyio.Event()
    result = self.incoming.popleft()
    if not self.incoming:
      self.incoming_ready = anyio.Event()
    if isinstance(result, Exception):
      raise result
    return result

  async def send_feature_report(self, data: bytes, report_id: bytes = b"\x00") -> int:
    """Record the report ID and abort bytes."""
    await anyio.lowlevel.checkpoint()
    self.features.append(report_id + data)
    return len(data) + len(report_id)


class TestXMLMessages(unittest.TestCase):
  def test_fragmented_nested_events_cdata_and_utf8(self) -> None:
    """Parse every byte boundary while preserving nested events and CDATA text."""
    parser = XMLMessages()
    data = (
      '<Res name="DownloadProtocol" ok="true"><![CDATA[<Evt>abc</Evt>]]></Res>'
      '<Evt name="ChangePlate"><Evt name="RemovePlate" plate="é &amp; x"/></Evt>'
    ).encode()
    messages = []
    for byte in data:
      messages.extend(parser.feed(bytes([byte])))
    self.assertEqual([item.tag for item in messages], ["Res", "Evt"])
    self.assertEqual(messages[0].text, "<Evt>abc</Evt>")
    self.assertEqual(messages[1][0].get("plate"), "é & x")


class TestPresto(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    self.wire = ScriptedHID()
    self.presto = KingFisherPresto(timeout=0.1)
    self.presto.io = self.wire
    self._tg = await stack.enter_task_group()
    self._presto_stack = AsyncExitStackWithShielding()
    await stack.enter_async_context(self._presto_stack)
    await self._open_presto()

  async def _open_presto(self) -> None:
    await self._presto_stack.enter_async_context(self.presto.lifespan())

  async def _close_presto(self) -> None:
    await self._presto_stack.aclose()

  async def test_lifespan_entry_reads_identity_without_moving_and_rejects_overlap(self) -> None:
    with pytest.raises(RuntimeError, match="already has an active lifespan"):
      async with self.presto:
        pass
    assert self.wire.opened == 1
    assert self.presto.serial == "123"
    assert [item.get("name") for item in self.wire.commands] == ["Connect", "GetStatus"]
    assert self.presto.get_turntable_state() == {1: None, 2: None}

  async def test_rotate_is_targeted_and_preserves_events(self) -> None:
    self.wire.emit(b'<Evt name="StepStarted"/>')
    await self.presto.rotate(1, "loading")
    assert self.presto.get_turntable_state() == {1: "loading", 2: "processing"}
    names = [(await self.presto.next_event()).name for _ in range(3)]
    assert names == ["StepStarted", "Temperature", "Ready"]
    await self.presto.rotate(1, "loading")
    moves = [item for item in self.wire.commands if item.get("name") == "Rotate"]
    assert len(moves) == 1
    assert moves[0].attrib == {"name": "Rotate", "nest": "1", "position": "2"}
    await self.presto.load_plate(1)
    assert self.presto.get_turntable_state()[1] == "processing"
    await self.presto.load_plate(1)
    assert sum(item.get("name") == "Rotate" for item in self.wire.commands) == 2

  async def test_stale_ready_is_not_used_for_rotation(self) -> None:
    self.wire.emit(b'<Evt name="Ready"/>')
    assert (await self.presto.next_event()).name == "Ready"
    self.wire.responses["Rotate"] = None
    with pytest.raises(PrestoError, match="Timeout"):
      await self.presto.rotate(1, "processing")
    assert self.presto.get_turntable_state() == {1: None, 2: None}

  async def test_rotate_error_does_not_acknowledge_or_keep_position(self) -> None:
    await self.presto.rotate(1, "processing")
    self.wire.responses["Rotate"] = b'<Evt name="Error"><Error code="6">Jam</Error></Evt>'
    with pytest.raises(PrestoError) as caught:
      await self.presto.rotate(2, "processing")
    assert caught.value.code == 6
    assert self.presto.get_turntable_state() == {1: None, 2: None}
    assert "ErrorAcknowledge" not in [item.get("name") for item in self.wire.commands]

  async def test_rotate_response_rejection_fails_without_waiting_for_event(self) -> None:
    self.wire.responses["Rotate"] = (
      b'<Res name="Rotate" ok="false"><Error code="28">Busy</Error></Res>'
    )
    with pytest.raises(PrestoError) as caught:
      await self.presto.rotate(1, "processing")
    assert caught.value.code == 28

  async def test_command_xml_escapes_protocol_names(self) -> None:
    await self.presto.start_protocol('A & "B" <C>', tip="Tip1", step="Mix")
    assert self.wire.commands[-1].get("protocol") == 'A & "B" <C>'
    assert all(len(report) == 65 for report in self.wire.reports)

  async def test_interleaved_event_and_wrong_response_do_not_complete_command(self) -> None:
    self.wire.responses["ListProtocols"] = (
      b'<Res name="Other" ok="true"/><Evt name="Pause"/>'
      b'<Res name="ListProtocols" ok="true"><Protocols><Protocol>Wash</Protocol>'
      b'</Protocols><MemoryUsed value="12"/></Res>'
    )
    assert await self.presto.list_protocols() == (["Wash"], 12)
    assert (await self.presto.next_event()).requires_acknowledgement

  async def test_attach_in_error_returns_error(self) -> None:
    self.wire.responses["GetStatus"] = (
      b'<Res name="GetStatus" ok="false"><Status>In error</Status>'
      b'<Error code="9999">Fault</Error></Res>'
    )
    event = await self.presto.next_event(attach=True)
    assert event.name == "Error"
    assert not event.requires_acknowledgement
    error = event.details.find("Error")
    assert error is not None
    assert error.get("code") == "9999"

  async def test_duration_results_are_seconds(self) -> None:
    self.wire.responses["GetProtocolTimeLeft"] = (
      b'<Res name="GetProtocolTimeLeft" ok="true"><TimeLeft value="PT1M2.5S"/></Res>'
    )
    remaining = await self.presto.get_protocol_time_left()
    assert remaining.time_left == 62.5
    assert remaining.time_to_pause is None
    self.wire.responses["GetProtocolDuration"] = (
      b'<Res name="GetProtocolDuration" ok="true"><Tip name="T">'
      b'<TimeStamp type="Start" step="Mix"/>'
      b'<TimeStamp type="Stop" step="Mix" duration="PT30S"/></Tip>'
      b'<Total duration="PT1M"/></Res>'
    )
    duration = await self.presto.get_protocol_duration("P")
    assert duration.total_duration == 60
    assert duration.steps[0].duration == 30

  async def test_upload_download_preserve_bytes_and_crc(self) -> None:
    data = write_bdz(
      BdzProtocol(name='A & "B"', tips=[ProtocolTip(name="T", steps=[MixStep("Mix")])])
    )
    await self.presto.upload_protocol('A & "B"', data)
    upload = self.wire.commands[-1]
    assert upload.get("protocol") == 'A & "B"'
    assert int(upload.attrib["crc"]) == zlib.crc32(data) & 0xFFFFFFFF
    assert base64.b64decode(upload.text or "") == data
    self.wire.responses["DownloadProtocol"] = (
      b'<Res name="DownloadProtocol" ok="true"><![CDATA[' + base64.b64encode(data) + b"]]></Res>"
    )
    assert await self.presto.download_protocol('A & "B"') == data

  async def test_reject_duo_upload_before_writing(self) -> None:
    count = len(self.wire.commands)
    protocol = BdzProtocol(
      name="Duo", variant="duo", tips=[ProtocolTip(name="T", steps=[MixStep("Mix")])]
    )
    with pytest.raises(ValueError, match="Only Presto"):
      await self.presto.upload_protocol("Duo", protocol)
    assert len(self.wire.commands) == count

  async def test_timeout_invalidates_session_and_reconnect_resets_state(self) -> None:
    self.wire.responses["ListProtocols"] = None
    with pytest.raises(PrestoError, match="Timeout"):
      await self.presto.list_protocols()
    self.wire.emit(b'<Res name="ListProtocols" ok="true"/>')
    with pytest.raises(PrestoError, match="reconnect"):
      await self.presto.list_protocols()
    await self._close_presto()
    await self._open_presto()
    assert (await self.presto.get_status()).status == "Idle"
    assert self.presto.get_turntable_state() == {1: None, 2: None}

  async def test_cancelled_command_requires_reconnect(self) -> None:
    self.wire.responses["ListProtocols"] = None
    self.wire.reset_sent()
    with anyio.CancelScope() as scope:
      async with anyio.create_task_group() as tg:

        async def cancel_once_sent() -> None:
          await self.wire.sent.wait()
          scope.cancel()

        tg.start_soon(cancel_once_sent)
        await self.presto.list_protocols()
    assert scope.cancelled_caught
    with pytest.raises(PrestoError, match="cancelled"):
      await self.presto.get_status()

  async def test_close_wakes_pending_command_and_event_waiter(self) -> None:
    self.wire.responses["ListProtocols"] = None
    self.wire.reset_sent()
    command_done = anyio.Event()
    event_done = anyio.Event()

    async def wait_command() -> None:
      with pytest.raises(PrestoError):
        with anyio.fail_after(0.2):
          await self.presto.list_protocols()
      command_done.set()

    async def wait_event() -> None:
      with pytest.raises(PrestoError):
        with anyio.fail_after(0.2):
          await self.presto.next_event()
      event_done.set()

    self._tg.start_soon(wait_command)
    self._tg.start_soon(wait_event)
    await self.wire.sent.wait()
    await self._close_presto()
    with anyio.fail_after(0.2):
      await command_done.wait()
      await event_done.wait()

  async def test_reader_failure_wakes_event_waiter(self) -> None:
    async with anyio.create_task_group() as tg:

      async def wait_event() -> None:
        with pytest.raises(PrestoError, match="unplugged"):
          with anyio.fail_after(0.2):
            await self.presto.next_event()

      tg.start_soon(wait_event)
      await anyio.lowlevel.checkpoint()
      self.wire.enqueue(OSError("unplugged"))

  async def test_invalid_hid_length_fails_connection(self) -> None:
    self.wire.enqueue(b"\x40bad")
    with pytest.raises(PrestoError, match="payload length"):
      with anyio.fail_after(0.2):
        await self.presto.next_event()

  async def test_abort_interrupts_a_pending_command(self) -> None:
    self.wire.responses["ListProtocols"] = None
    self.wire.reset_sent()
    async with anyio.create_task_group() as tg:

      async def abort_once_sent() -> None:
        await self.wire.sent.wait()
        await self.presto.abort()

      tg.start_soon(abort_once_sent)
      with pytest.raises(PrestoError, match="aborted"):
        await self.presto.list_protocols()
    assert self.wire.features == [b"\x00\x01\x00"]
    assert self.wire.reports[-1][1:4] == b"\x02\x1b\n"

  async def test_setup_failure_closes_transport(self) -> None:
    await self._close_presto()
    self.wire.responses["Connect"] = (
      b'<Res name="Connect" ok="false"><Error code="3">In use</Error></Res>'
    )
    closed = self.wire.closed
    with pytest.raises(PrestoError):
      await self._open_presto()
    assert self.wire.closed > closed
    assert self.presto.serial is None

  async def test_invalid_move_and_incomplete_step_do_not_write(self) -> None:
    count = len(self.wire.commands)
    for position in (3, True, 1.0):
      with pytest.raises(ValueError):
        await self.presto.rotate(position, "loading")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
      await self.presto.start_protocol("P", step="Mix")
    assert len(self.wire.commands) == count

  async def test_fault_after_ready_does_not_restore_cached_position(self) -> None:
    self.wire.responses["Rotate"] = (
      b'<Evt name="Ready"/><Evt name="Error"><Error code="6">Jam</Error></Evt>'
    )
    await self.presto.rotate(1, "loading")
    assert self.presto.get_turntable_state() == {1: None, 2: None}

  async def test_concurrent_moves_are_serialized_through_state_update(self) -> None:
    first_entered = anyio.Event()
    second_entered = anyio.Event()

    async def first_move() -> None:
      first_entered.set()
      await self.presto.rotate(1, "loading")

    async def second_move() -> None:
      await first_entered.wait()
      second_entered.set()
      await self.presto.rotate(1, "processing")

    async def third_move() -> None:
      await second_entered.wait()
      await self.presto.rotate(1, "loading")

    async with anyio.create_task_group() as tg:
      tg.start_soon(first_move)
      tg.start_soon(second_move)
      tg.start_soon(third_move)
    moves = [item.get("position") for item in self.wire.commands if item.get("name") == "Rotate"]
    assert moves == ["2", "1", "2"]
    assert self.presto.get_turntable_state()[1] == "loading"

  async def test_reconnect_after_closing_pending_command_has_clean_routing(self) -> None:
    self.wire.responses["ListProtocols"] = None
    self.wire.reset_sent()
    pending_done = anyio.Event()

    async def run_pending() -> None:
      with pytest.raises(PrestoError):
        await self.presto.list_protocols()
      pending_done.set()

    self._tg.start_soon(run_pending)
    await self.wire.sent.wait()
    await self._close_presto()
    await self._open_presto()
    await pending_done.wait()
    assert (await self.presto.get_status()).status == "Idle"

  async def test_aborted_rotation_leaves_position_unknown(self) -> None:
    self.wire.responses["Rotate"] = b'<Evt name="Aborted"/>'
    with pytest.raises(PrestoError, match="aborted"):
      await self.presto.rotate(1, "loading")
    assert self.presto.get_turntable_state() == {1: None, 2: None}

  async def test_abort_is_available_after_response_timeout(self) -> None:
    self.wire.responses["ListProtocols"] = None
    with pytest.raises(PrestoError, match="Timeout"):
      await self.presto.list_protocols()
    await self.presto.abort()
    assert self.wire.features == [b"\x00\x01\x00"]

  async def test_abort_completes_both_phases_when_caller_scope_is_cancelled(self) -> None:
    cancelled = False
    with anyio.CancelScope() as scope:
      scope.cancel()
      try:
        await self.presto.abort()
      except anyio.get_cancelled_exc_class():
        cancelled = True
        raise
    assert cancelled
    assert self.wire.features == [b"\x00\x01\x00"]
    assert self.wire.reports[-1] == b"\x00\x02\x1b\n" + b"\x00" * 61
