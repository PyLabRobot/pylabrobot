"""Presto public operations exercised through scripted HID reports, without hardware."""

import asyncio
import base64
import unittest
import xml.etree.ElementTree as ET
import zlib
from typing import Optional

from pylabrobot.io.hid import HID
from pylabrobot.thermo_fisher import KingFisherPresto
from pylabrobot.thermo_fisher.kingfisher.bdz import BdzProtocol, MixStep, ProtocolTip, write_bdz
from pylabrobot.thermo_fisher.kingfisher.presto import PrestoError
from pylabrobot.thermo_fisher.kingfisher.presto.connection import XMLMessages


class ScriptedHID(HID):
  """Deliver XML as framed input reports and inspect the full transmitted command."""

  def __init__(self) -> None:
    super().__init__("test Presto", 0x0AB6, 0x02C9)
    self.incoming: asyncio.Queue[bytes | Exception] = asyncio.Queue()
    self.commands: list[ET.Element] = []
    self.reports: list[bytes] = []
    self.features: list[bytes] = []
    self.pending = bytearray()
    self.opened = 0
    self.closed = 0
    self.sent = asyncio.Event()
    self.responses: dict[str, Optional[bytes]] = {
      "Connect": (
        b'<Res name="Connect" ok="true"><Instrument>KingFisher Presto</Instrument>'
        b"<Version>1.0</Version><Serial>123</Serial></Res>"
      ),
      "GetStatus": b'<Res name="GetStatus" ok="true"><Status>Idle</Status></Res>',
      "Rotate": b'<Res name="Rotate" ok="true"/><Evt name="Temperature"/><Evt name="Ready"/>',
    }

  def emit(self, data: bytes) -> None:
    """Split data at report boundaries, including XML and UTF-8 boundaries."""
    for offset in range(0, len(data), 63):
      chunk = data[offset : offset + 63]
      self.incoming.put_nowait(bytes([len(chunk)]) + chunk.ljust(63, b"\x00"))

  async def setup(self) -> None:
    """Record opening without invoking hidapi."""
    self.opened += 1
    self.pending.clear()
    self.incoming = asyncio.Queue()

  async def stop(self) -> None:
    """Record closing without invoking hidapi."""
    self.closed += 1

  async def write(self, data: bytes, report_id: bytes = b"\x00") -> int:
    """Assemble output reports and respond once the newline terminator arrives."""
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

  async def read(self, size: int, timeout: int) -> bytes:
    """Wait for the next script entry; exceptions model transport failure."""
    result = await self.incoming.get()
    if isinstance(result, Exception):
      raise result
    return result

  async def send_feature_report(self, data: bytes, report_id: bytes = b"\x00") -> int:
    """Record the report ID and abort bytes."""
    self.features.append(report_id + data)
    return len(data) + len(report_id)


class XMLMessagesTests(unittest.TestCase):
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


class PrestoTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.wire = ScriptedHID()
    self.presto = KingFisherPresto(timeout=0.1)
    self.presto.io = self.wire
    await self.presto.setup()

  async def asyncTearDown(self) -> None:
    await self.presto.stop()

  async def test_setup_is_idempotent_and_does_not_move(self) -> None:
    await self.presto.setup()
    self.assertEqual(self.wire.opened, 1)
    self.assertEqual(self.presto.serial, "123")
    self.assertEqual([item.get("name") for item in self.wire.commands], ["Connect", "GetStatus"])
    self.assertEqual(self.presto.get_turntable_state(), {1: None, 2: None})

  async def test_rotate_is_targeted_and_preserves_events(self) -> None:
    self.wire.emit(b'<Evt name="StepStarted"/>')
    await self.presto.rotate(1, "loading")
    self.assertEqual(self.presto.get_turntable_state(), {1: "loading", 2: "processing"})
    names = [(await self.presto.next_event()).name for _ in range(3)]
    self.assertEqual(names, ["StepStarted", "Temperature", "Ready"])
    await self.presto.rotate(1, "loading")
    moves = [item for item in self.wire.commands if item.get("name") == "Rotate"]
    self.assertEqual(len(moves), 1)
    self.assertEqual(moves[0].attrib, {"name": "Rotate", "nest": "1", "position": "2"})
    await self.presto.load_plate(1)
    self.assertEqual(self.presto.get_turntable_state()[1], "processing")
    await self.presto.load_plate(1)
    self.assertEqual(sum(item.get("name") == "Rotate" for item in self.wire.commands), 2)

  async def test_stale_ready_is_not_used_for_rotation(self) -> None:
    self.wire.emit(b'<Evt name="Ready"/>')
    self.assertEqual((await self.presto.next_event()).name, "Ready")
    self.wire.responses["Rotate"] = None
    with self.assertRaisesRegex(PrestoError, "Timeout"):
      await self.presto.rotate(1, "processing")
    self.assertEqual(self.presto.get_turntable_state(), {1: None, 2: None})

  async def test_rotate_error_does_not_acknowledge_or_keep_position(self) -> None:
    await self.presto.rotate(1, "processing")
    self.wire.responses["Rotate"] = b'<Evt name="Error"><Error code="6">Jam</Error></Evt>'
    with self.assertRaises(PrestoError) as caught:
      await self.presto.rotate(2, "processing")
    self.assertEqual(caught.exception.code, 6)
    self.assertEqual(self.presto.get_turntable_state(), {1: None, 2: None})
    self.assertNotIn("ErrorAcknowledge", [item.get("name") for item in self.wire.commands])

  async def test_rotate_response_rejection_fails_without_waiting_for_event(self) -> None:
    self.wire.responses["Rotate"] = (
      b'<Res name="Rotate" ok="false"><Error code="28">Busy</Error></Res>'
    )
    with self.assertRaises(PrestoError) as caught:
      await self.presto.rotate(1, "processing")
    self.assertEqual(caught.exception.code, 28)

  async def test_command_xml_escapes_protocol_names(self) -> None:
    await self.presto.start_protocol('A & "B" <C>', tip="Tip1", step="Mix")
    self.assertEqual(self.wire.commands[-1].get("protocol"), 'A & "B" <C>')
    self.assertTrue(all(len(report) == 65 for report in self.wire.reports))

  async def test_interleaved_event_and_wrong_response_do_not_complete_command(self) -> None:
    self.wire.responses["ListProtocols"] = (
      b'<Res name="Other" ok="true"/><Evt name="Pause"/>'
      b'<Res name="ListProtocols" ok="true"><Protocols><Protocol>Wash</Protocol>'
      b'</Protocols><MemoryUsed value="12"/></Res>'
    )
    self.assertEqual(await self.presto.list_protocols(), (["Wash"], 12))
    self.assertTrue((await self.presto.next_event()).requires_acknowledgement)

  async def test_attach_in_error_returns_error(self) -> None:
    self.wire.responses["GetStatus"] = (
      b'<Res name="GetStatus" ok="false"><Status>In error</Status>'
      b'<Error code="9999">Fault</Error></Res>'
    )
    event = await self.presto.next_event(attach=True)
    self.assertEqual(event.name, "Error")
    self.assertFalse(event.requires_acknowledgement)
    error = event.details.find("Error")
    assert error is not None
    self.assertEqual(error.get("code"), "9999")

  async def test_duration_results_are_seconds(self) -> None:
    self.wire.responses["GetProtocolTimeLeft"] = (
      b'<Res name="GetProtocolTimeLeft" ok="true"><TimeLeft value="PT1M2.5S"/></Res>'
    )
    remaining = await self.presto.get_protocol_time_left()
    self.assertEqual(remaining.time_left, 62.5)
    self.assertIsNone(remaining.time_to_pause)
    self.wire.responses["GetProtocolDuration"] = (
      b'<Res name="GetProtocolDuration" ok="true"><Tip name="T">'
      b'<TimeStamp type="Start" step="Mix"/>'
      b'<TimeStamp type="Stop" step="Mix" duration="PT30S"/></Tip>'
      b'<Total duration="PT1M"/></Res>'
    )
    duration = await self.presto.get_protocol_duration("P")
    self.assertEqual(duration.total_duration, 60)
    self.assertEqual(duration.steps[0].duration, 30)

  async def test_upload_download_preserve_bytes_and_crc(self) -> None:
    data = write_bdz(
      BdzProtocol(name='A & "B"', tips=[ProtocolTip(name="T", steps=[MixStep("Mix")])])
    )
    await self.presto.upload_protocol('A & "B"', data)
    upload = self.wire.commands[-1]
    self.assertEqual(upload.get("protocol"), 'A & "B"')
    self.assertEqual(int(upload.attrib["crc"]), zlib.crc32(data) & 0xFFFFFFFF)
    self.assertEqual(base64.b64decode(upload.text or ""), data)
    self.wire.responses["DownloadProtocol"] = (
      b'<Res name="DownloadProtocol" ok="true"><![CDATA[' + base64.b64encode(data) + b"]]></Res>"
    )
    self.assertEqual(await self.presto.download_protocol('A & "B"'), data)

  async def test_reject_duo_upload_before_writing(self) -> None:
    count = len(self.wire.commands)
    protocol = BdzProtocol(
      name="Duo", variant="duo", tips=[ProtocolTip(name="T", steps=[MixStep("Mix")])]
    )
    with self.assertRaisesRegex(ValueError, "Only Presto"):
      await self.presto.upload_protocol("Duo", protocol)
    self.assertEqual(len(self.wire.commands), count)

  async def test_timeout_invalidates_session_and_reconnect_resets_state(self) -> None:
    self.wire.responses["ListProtocols"] = None
    with self.assertRaisesRegex(PrestoError, "Timeout"):
      await self.presto.list_protocols()
    self.wire.emit(b'<Res name="ListProtocols" ok="true"/>')
    with self.assertRaisesRegex(PrestoError, "reconnect"):
      await self.presto.list_protocols()
    await self.presto.stop()
    await self.presto.setup()
    self.assertEqual((await self.presto.get_status()).status, "Idle")
    self.assertEqual(self.presto.get_turntable_state(), {1: None, 2: None})

  async def test_cancelled_command_requires_reconnect(self) -> None:
    self.wire.responses["ListProtocols"] = None
    self.wire.sent.clear()
    task = asyncio.create_task(self.presto.list_protocols())
    await self.wire.sent.wait()
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    with self.assertRaisesRegex(PrestoError, "cancelled"):
      await self.presto.get_status()

  async def test_close_wakes_pending_command_and_event_waiter(self) -> None:
    self.wire.responses["ListProtocols"] = None
    self.wire.sent.clear()
    command = asyncio.create_task(self.presto.list_protocols())
    event = asyncio.create_task(self.presto.next_event())
    await self.wire.sent.wait()
    await self.presto.stop()
    for task in (command, event):
      with self.assertRaises(PrestoError):
        await asyncio.wait_for(task, 0.2)

  async def test_reader_failure_wakes_event_waiter(self) -> None:
    task = asyncio.create_task(self.presto.next_event())
    self.wire.incoming.put_nowait(OSError("unplugged"))
    with self.assertRaisesRegex(PrestoError, "unplugged"):
      await asyncio.wait_for(task, 0.2)

  async def test_invalid_hid_length_fails_connection(self) -> None:
    self.wire.incoming.put_nowait(b"\x40bad")
    with self.assertRaisesRegex(PrestoError, "payload length"):
      await asyncio.wait_for(self.presto.next_event(), 0.2)

  async def test_abort_interrupts_a_pending_command(self) -> None:
    self.wire.responses["ListProtocols"] = None
    self.wire.sent.clear()
    task = asyncio.create_task(self.presto.list_protocols())
    await self.wire.sent.wait()
    await self.presto.abort()
    with self.assertRaisesRegex(PrestoError, "aborted"):
      await task
    self.assertEqual(self.wire.features, [b"\x00\x01\x00"])
    self.assertEqual(self.wire.reports[-1][1:4], b"\x02\x1b\n")

  async def test_setup_failure_closes_transport(self) -> None:
    await self.presto.stop()
    self.wire.responses["Connect"] = (
      b'<Res name="Connect" ok="false"><Error code="3">In use</Error></Res>'
    )
    closed = self.wire.closed
    with self.assertRaises(PrestoError):
      await self.presto.setup()
    self.assertGreater(self.wire.closed, closed)
    self.assertIsNone(self.presto.serial)

  async def test_invalid_move_and_incomplete_step_do_not_write(self) -> None:
    count = len(self.wire.commands)
    for position in (3, True, 1.0):
      with self.subTest(position=position), self.assertRaises(ValueError):
        await self.presto.rotate(position, "loading")  # type: ignore[arg-type]
    with self.assertRaises(ValueError):
      await self.presto.start_protocol("P", step="Mix")
    self.assertEqual(len(self.wire.commands), count)

  async def test_fault_after_ready_does_not_restore_cached_position(self) -> None:
    self.wire.responses["Rotate"] = (
      b'<Evt name="Ready"/><Evt name="Error"><Error code="6">Jam</Error></Evt>'
    )
    await self.presto.rotate(1, "loading")
    self.assertEqual(self.presto.get_turntable_state(), {1: None, 2: None})

  async def test_concurrent_moves_are_serialized_through_state_update(self) -> None:
    await asyncio.gather(
      self.presto.rotate(1, "loading"),
      self.presto.rotate(1, "processing"),
      self.presto.rotate(1, "loading"),
    )
    moves = [item.get("position") for item in self.wire.commands if item.get("name") == "Rotate"]
    self.assertEqual(moves, ["2", "1", "2"])
    self.assertEqual(self.presto.get_turntable_state()[1], "loading")

  async def test_reconnect_after_closing_pending_command_has_clean_routing(self) -> None:
    self.wire.responses["ListProtocols"] = None
    self.wire.sent.clear()
    pending = asyncio.create_task(self.presto.list_protocols())
    await self.wire.sent.wait()
    await self.presto.stop()
    await self.presto.setup()
    with self.assertRaises(PrestoError):
      await pending
    self.assertEqual((await self.presto.get_status()).status, "Idle")

  async def test_aborted_rotation_leaves_position_unknown(self) -> None:
    self.wire.responses["Rotate"] = b'<Evt name="Aborted"/>'
    with self.assertRaisesRegex(PrestoError, "aborted"):
      await self.presto.rotate(1, "loading")
    self.assertEqual(self.presto.get_turntable_state(), {1: None, 2: None})

  async def test_abort_is_available_after_response_timeout(self) -> None:
    self.wire.responses["ListProtocols"] = None
    with self.assertRaisesRegex(PrestoError, "Timeout"):
      await self.presto.list_protocols()
    await self.presto.abort()
    self.assertEqual(self.wire.features, [b"\x00\x01\x00"])
