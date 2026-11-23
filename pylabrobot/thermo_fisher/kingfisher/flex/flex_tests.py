"""Driver lifecycle and fault handling against controlled HID responses."""

import asyncio
import unittest
from dataclasses import replace

from pylabrobot.thermo_fisher import KingFisherFlex
from pylabrobot.thermo_fisher.kingfisher.flex import FlexError, FlexProtocolError, read_flex_bdz

from .test_helpers import HIDTestCase, bdz_bytes


class FlexTests(HIDTestCase):
  """Exercise public APIs with no USB access."""

  async def asyncSetUp(self) -> None:
    """Prepare a Flex and immutable protocol fixture without opening either."""
    self.mock_hid()
    self.flex = KingFisherFlex(timeout=5, operation_timeout=5)
    self.flex.io = self.io
    self.physical_flex = self.flex
    self.protocol = read_flex_bdz(bdz_bytes())

  async def asyncTearDown(self) -> None:
    """Always drain connection tasks."""
    await self.flex.stop()

  async def ready(self) -> None:
    """Explicitly initialize the fixture for motion tests."""
    await self.flex.setup()
    await self.flex.initialize()
    self.flex.reconcile_inventory()

  async def test_identification_is_idempotent_and_motion_free(self) -> None:
    """Setup sends VER only, and initialization must be requested separately."""
    await self.flex.setup()
    await self.flex.setup()
    self.assertEqual(self.commands, ["VER"])
    self.io.setup.assert_awaited_once_with()
    self.assertEqual(self.flex.serial, "fixture")
    self.assertIsNone(self.flex.processing_slot)
    with self.assertRaisesRegex(FlexError, "initialize"):
      await self.flex.run_protocol(self.protocol.bind_plates(self.plate_bindings))
    self.assertEqual(self.commands, ["VER"])

  async def test_wrong_device_closes_connection(self) -> None:
    """A matching VID/PID alone cannot establish instrument identity."""
    self.responses["VER"] = "VER\r\nVER KingFisherPresto 1.01.0 fixture\r\nVER END 0\r\n"
    with self.assertRaises(FlexError):
      await self.flex.setup()
    self.io.stop.assert_awaited_once_with()
    self.assertEqual(self.flex.state, "disconnected")

  async def test_complete_protocol_preserves_command_order(self) -> None:
    """Preserve script commands between initialization and successful finalization."""
    await self.ready()
    await self.flex.initialize()
    self.flex.reconcile_inventory()
    before = len(self.commands)
    result = await self.flex.run_protocol(self.protocol.bind_plates(self.plate_bindings))
    self.assertEqual(result.completed_commands, 3)
    self.assertEqual(result.source_sha256, self.protocol.sha256)
    self.assertEqual(
      self.commands[before:],
      [
        "EAK",
        "MOI 0",
        "MOS 0",
        "MOM 0",
        "MOH 0 100",
        "TEM 0 300",
        "MOR 6",
        'PAU 1 "plate"',
        'PAU 2 "plate"',
        'TXT "END_OF_RUN"',
        "TEM 0 300",
        "TEM 0 300",
      ],
    )
    self.assertEqual(self.flex.state, "ready")
    await self.flex.stop()
    events = [e async for e in self.flex.events()]
    self.assertEqual(sum(e.kind == "prompt_completed" for e in events), 2)
    self.assertEqual(sum(e.kind == "run_completed" for e in events), 1)
    self.assertFalse(any(e.kind == "failure" for e in events))

  async def test_entire_script_preflight_precedes_transmission(self) -> None:
    """An unsupported final line prevents even the execution preamble."""
    await self.ready()
    before = list(self.commands)
    with self.assertRaises(FlexProtocolError):
      await self.flex.run_protocol(
        replace(self.protocol, script=self.protocol.script + "REC 0\n").bind_plates(
          self.plate_bindings
        )
      )
    self.assertEqual(self.commands, before)

  async def test_pause_waits_for_instrument_completion(self) -> None:
    """Temperature reports cannot complete PAU."""
    await self.ready()
    self.responses['PAU 1 "plate"'] = "PAU\r\nTEM 24 22\r\n"
    protocol = replace(self.protocol, script='MOR 7\nPAU 1 "plate"\nTXT "done"\n')
    task = asyncio.create_task(self.flex.run_protocol(protocol.bind_plates(self.plate_bindings)))
    await self.wait_sent('PAU 1 "plate"')
    async for event in self.flex.events():
      if event.kind == "load":
        self.assertIsNotNone(event.prompt)
        if event.prompt is not None:
          self.assertEqual(event.prompt.loading_slot, 2)
        break
    self.assertFalse(task.done())
    self.assertEqual(self.flex.state, "waiting")
    self.assertNotIn('TXT "done"', self.commands)
    self.emit("PAU END 0\r\n")
    await task
    self.assertIn('TXT "done"', self.commands)

  async def test_long_operation_uses_duration_not_ack_timeout(self) -> None:
    """A timed mix survives past both the base motion and acceptance deadlines."""
    self.flex = KingFisherFlex(timeout=0.01, operation_timeout=0.01)
    self.flex.io = self.io
    self.physical_flex = self.flex
    await self.ready()
    self.responses["MOH 547 669 0 60000 0"] = "MOH\r\nTEM 24 22\r\n"
    task = asyncio.create_task(
      self.flex.run_protocol(
        replace(self.protocol, script="MOH 547 669 0 60000 0\n").bind_plates(self.plate_bindings)
      )
    )
    await self.wait_sent("MOH 547 669 0 60000 0")
    with self.assertRaises(asyncio.TimeoutError):
      await asyncio.wait_for(asyncio.shield(task), 0.04)
    self.emit("MOH END 0\r\n")
    await task

  async def test_abort_interrupts_loading_or_motion_without_replay(self) -> None:
    """Abort bypasses the blocked command lock and preserves interrupted state."""
    for command in ('PAU 1 "plate"', "MOH 650 80"):
      with self.subTest(command=command):
        await self.ready()
        self.responses[command] = command[:3] + "\r\n"
        task = asyncio.create_task(
          self.flex.run_protocol(
            replace(self.protocol, script=f"MOR 1\n{command}\n").bind_plates(self.plate_bindings)
          )
        )
        await self.wait_sent(command)
        await self.flex.abort()
        with self.assertRaises(FlexError):
          await task
        self.io.send_feature_report.assert_awaited_once_with(b"\x01\x00", report_id=b"\x00")
        self.io.write.assert_awaited_with(b"\x01\x1b" + b"\x00" * 62, report_id=b"\x00")
        self.assertIsNone(self.flex.processing_slot)
        before = len(self.commands)
        with self.assertRaises(FlexError):
          await self.flex.run_protocol(self.protocol.bind_plates(self.plate_bindings))
        self.assertEqual(len(self.commands), before)
        await self.flex.stop()
        self.responses.clear()
        self.io.send_feature_report.reset_mock()

  async def test_cancellation_requires_fresh_session(self) -> None:
    """Late completion cannot satisfy a later command with the same opcode."""
    await self.ready()
    self.responses['PAU 1 "plate"'] = "PAU\r\n"
    task = asyncio.create_task(
      self.flex.run_protocol(
        replace(self.protocol, script='MOR 1\nPAU 1 "plate"\n').bind_plates(self.plate_bindings)
      )
    )
    await self.wait_sent('PAU 1 "plate"')
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.emit("PAU END 0\r\n")
    with self.assertRaises(FlexError):
      await self.flex.setup()
    self.io.send_feature_report.assert_not_awaited()
    await self.flex.stop()
    await self.flex.setup()
    self.assertEqual(self.flex.state, "idle")
    self.assertEqual(self.commands[-1], "VER")

  async def test_timeout_does_not_resend(self) -> None:
    """A missing acknowledgment invalidates the session without retry."""
    self.responses["VER"] = None
    with self.assertRaises(asyncio.TimeoutError):
      await self.flex.setup()
    self.assertEqual(self.commands, ["VER"])

  async def test_instrument_error_preserves_unknown_code(self) -> None:
    """Nonzero END prevents subsequent commands and remains distinguishable."""
    await self.ready()
    self.responses["MOR 7"] = "MOR\r\nMOR END 9876\r\n"
    with self.assertRaises(FlexError) as raised:
      await self.flex.run_protocol(
        replace(self.protocol, script='MOR 7\nPAU 1 "plate"\n').bind_plates(self.plate_bindings)
      )
    self.assertEqual(raised.exception.code, 9876)
    self.assertNotIn('PAU 1 "plate"', self.commands)

  async def test_disconnect_wakes_operator_wait_and_event_consumer(self) -> None:
    """Indefinite human waits still terminate on transport failure."""
    await self.ready()
    self.responses['PAU 1 "plate"'] = "PAU\r\n"
    task = asyncio.create_task(
      self.flex.run_protocol(
        replace(self.protocol, script='MOR 1\nPAU 1 "plate"\n').bind_plates(self.plate_bindings)
      )
    )
    await self.wait_sent('PAU 1 "plate"')
    self.incoming.put_nowait(OSError("unplugged"))
    with self.assertRaisesRegex(FlexError, "unplugged"):
      await asyncio.wait_for(task, 1)
    self.assertIn("failure", [e.kind async for e in self.flex.events()])

  async def test_stop_during_pause_does_not_claim_abort(self) -> None:
    """Connection teardown completes even while a run owns the operation lock."""
    await self.ready()
    self.responses['PAU 1 "plate"'] = "PAU\r\n"
    task = asyncio.create_task(
      self.flex.run_protocol(
        replace(self.protocol, script='MOR 1\nPAU 1 "plate"\n').bind_plates(self.plate_bindings)
      )
    )
    await self.wait_sent('PAU 1 "plate"')
    await asyncio.wait_for(self.flex.stop(), 1)
    with self.assertRaises(FlexError):
      await task
    self.assertEqual(self.flex.state, "disconnected")
    self.io.send_feature_report.assert_not_awaited()

  async def test_unsolicited_quit_prevents_success(self) -> None:
    """Unsolicited QIT cannot be interpreted as successful run completion."""
    await self.ready()
    self.responses["MOR 7"] = "MOR\r\nQIT\r\n"
    with self.assertRaises(FlexError):
      await self.flex.run_protocol(
        replace(self.protocol, script="MOR 7\n").bind_plates(self.plate_bindings)
      )
    self.assertEqual(self.flex.state, "uncertain")

  async def test_short_write_and_wrong_completion_fail(self) -> None:
    """A partial transfer or mismatched opcode never counts as success."""
    self.io.write.side_effect = None
    self.io.write.return_value = 1
    with self.assertRaises(FlexError):
      await self.flex.setup()
    self.io.write.side_effect = self.write_report
    self.responses["VER"] = "VER\r\nMOR END 0\r\n"
    with self.assertRaises(FlexError):
      await self.flex.setup()

  async def test_concurrent_run_is_rejected_without_queueing(self) -> None:
    """A second run cannot start later merely because the first was waiting."""
    await self.ready()
    self.responses['PAU 1 "plate"'] = "PAU\r\n"
    task = asyncio.create_task(
      self.flex.run_protocol(
        replace(self.protocol, script='MOR 1\nPAU 1 "plate"\n').bind_plates(self.plate_bindings)
      )
    )
    await self.wait_sent('PAU 1 "plate"')
    before = list(self.commands)
    with self.assertRaisesRegex(FlexError, "active"):
      await self.flex.run_protocol(self.protocol.bind_plates(self.plate_bindings))
    with self.assertRaisesRegex(FlexError, "active"):
      await self.flex.initialize()
      self.flex.reconcile_inventory()
    self.assertEqual(self.commands, before)
    self.emit("PAU END 0\r\n")
    await task

  async def test_abort_requires_terminal_response(self) -> None:
    """Sending abort bytes is not enough to claim the instrument acknowledged abort."""
    await self.ready()
    self.auto_abort = False
    task = asyncio.create_task(self.flex.abort())
    with self.assertRaises(asyncio.TimeoutError):
      await task
    self.assertEqual(self.flex.state, "uncertain")
    self.assertIn("<ESC>", [entry.text for entry in self.flex.transcript])

  async def test_finite_operator_deadline_never_advances(self) -> None:
    """A timed-out human wait invalidates the run rather than skipping its prompt."""
    await self.ready()
    self.responses['PAU 1 "plate"'] = "PAU\r\n"
    with self.assertRaises(asyncio.TimeoutError):
      await self.flex.run_protocol(
        replace(self.protocol, script='MOR 1\nPAU 1 "plate"\nTXT "next"\n').bind_plates(
          self.plate_bindings
        ),
        operator_timeout=0.01,
      )
    self.assertNotIn('TXT "next"', self.commands)
    self.io.send_feature_report.assert_not_awaited()

  async def test_duplicate_ack_invalidates_session(self) -> None:
    """Ambiguous same-opcode replies cannot be accepted as a new transaction."""
    await self.ready()
    self.responses["MOR 7"] = "MOR\r\nMOR\r\nMOR END 0\r\n"
    with self.assertRaisesRegex(FlexError, "Duplicate"):
      await self.flex.run_protocol(
        replace(self.protocol, script="MOR 7\n").bind_plates(self.plate_bindings)
      )


if __name__ == "__main__":
  unittest.main()
