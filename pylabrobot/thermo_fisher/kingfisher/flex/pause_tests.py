"""Operator pauses, reagent additions and timed waits with simulated HID."""

import asyncio
from dataclasses import replace

from .actions import FlexAction
from .connection import FlexError
from .flex import KingFisherFlex
from .protocol import FlexPrompt, FlexProtocolError, read_flex_bdz
from .test_helpers import HIDTestCase, bdz_bytes


class PauseTests(HIDTestCase):
  """Keep manual confirmations distinct from firmware timers."""

  async def asyncSetUp(self) -> None:
    """Prepare one standalone addition pause and a simulated session."""
    self.mock_hid()
    self.flex = KingFisherFlex(timeout=5, operation_timeout=5)
    self.flex.io = self.io
    self.physical_flex = self.flex
    self.protocol = read_flex_bdz(
      bdz_bytes(
        'MOR 6\nPAU 1 "plate"\nTXT 11 "addition"\nMOR 6\nPAU 0 "add liquid"\n'
        'TXT "addition"\nMOR 1\nMOR 6\nPAU 2 "plate"\n',
        steps='<Pause name="addition"><Message>add liquid</Message>'
        '<Plates><Plate id="plate" wellGroup="Plate" /></Plates>'
        '<Dispense enabled="true"><Reagents><Reagent name="water" volume="50" />'
        "</Reagents></Dispense></Pause>",
      )
    )
    await self.flex.setup()
    await self.flex.initialize()
    self.flex.reconcile_inventory()

  async def asyncTearDown(self) -> None:
    """Close the simulated reader."""
    await self.flex.stop()

  async def confirm_plate(self, prompt: FlexAction) -> bool:
    """Accept only loading/unloading actions."""
    self.assertIn(prompt.action, ("load", "unload"))
    self.complete_transfer(prompt)
    return True

  async def test_physical_intervention_with_host_transfers(self) -> None:
    """Explicit mode selection preserves PAU for interventions only."""

    async def handle(action: FlexAction) -> bool:
      """Confirm transfers in software and wait for physical Start on an intervention."""
      if action.action == "pause":
        await self.flex.wait_for_physical_start(action)
        return True
      return await self.confirm_plate(action)

    self.responses['PAU 0 "add liquid"'] = "PAU\r\nTEM 37 22\r\n"
    task = asyncio.create_task(
      self.flex.run_protocol(
        self.protocol.bind_plates(self.plate_bindings),
        handle_action=handle,
        acknowledgement=lambda action: "instrument" if action.action == "pause" else "host",
      )
    )
    try:
      await self.wait_sent('PAU 0 "add liquid"')
      self.assertFalse(task.done())
      self.assertNotIn('TXT "addition"', self.commands)
      self.emit("PAU END 0\r\n")
      result = await asyncio.wait_for(task, 1)
      self.assertEqual(result.host_confirmations, 2)
    finally:
      task.cancel()
      await asyncio.gather(task, return_exceptions=True)

  async def test_addition_metadata_and_host_gate(self) -> None:
    """Expose reagent details and hold all following motion until confirmed."""
    entered: asyncio.Queue[FlexPrompt] = asyncio.Queue()
    release = asyncio.Event()

    async def confirm(prompt: FlexAction) -> bool:
      """Fulfill transfers and hold a single addition confirmation."""
      if prompt.action != "pause":
        return await self.confirm_plate(prompt)
      entered.put_nowait(prompt)
      await release.wait()
      return True

    task = asyncio.create_task(
      self.flex.run_protocol(
        self.protocol.bind_plates(self.plate_bindings),
        handle_action=confirm,
      )
    )
    try:
      prompt = await asyncio.wait_for(entered.get(), 1)
      self.assertEqual(prompt.loading_slot, 1)
      self.assertEqual(prompt.additions[0].volume, 50)
      self.assertEqual(prompt.additions[0].reagent_name, "water")
      self.assertIn("50 uL", prompt.label)
      self.assertNotIn('TXT "addition"', self.commands)
      self.assertFalse(any(c.startswith("PAU") for c in self.commands))
      release.set()
      result = await asyncio.wait_for(task, 1)
      self.assertEqual(result.host_confirmations, 3)
    finally:
      task.cancel()
      await asyncio.gather(task, return_exceptions=True)

  def test_inconsistent_dispense_metadata_rejected(self) -> None:
    """Reject missing reagents and incorrect plate presentation before transmission."""
    for xml in (
      self.protocol.exported_xml.replace(b'volume="50"', b'volume="nan"'),
      self.protocol.exported_xml.replace(b'<Reagent name="water" volume="50" />', b""),
      self.protocol.exported_xml.replace(b'wellGroup="Plate"', b'wellGroup="missing"'),
    ):
      with self.subTest(xml=xml), self.assertRaises(FlexProtocolError):
        replace(self.protocol, exported_xml=xml).preflight()
    with self.assertRaises(FlexProtocolError):
      replace(
        self.protocol,
        script=self.protocol.script.replace('TXT 11 "addition"\nMOR 6', 'TXT 11 "addition"\nMOR 7'),
      ).preflight()

  async def test_two_timed_cycles_wait_for_each_completion(self) -> None:
    """Already-expanded loops run once; every WAI completion gates subsequent commands."""
    cycle = "MOH 630 200 0 3000 0\nWAI 1 3000\nWAI 1\n"
    protocol = read_flex_bdz(bdz_bytes(cycle * 2 + 'TXT "finished"\n'))
    self.responses["WAI 1"] = "WAI\r\n"
    task = asyncio.create_task(self.flex.run_protocol(protocol.bind_plates(self.plate_bindings)))
    try:
      for count in (1, 2):
        await self.wait_sent("WAI 1")
        self.assertEqual(self.commands.count("MOH 630 200 0 3000 0"), count)
        self.assertNotIn('TXT "finished"', self.commands)
        self.emit("WAI END 0\r\n")
      result = await asyncio.wait_for(task, 1)
      self.assertEqual(result.completed_commands, 7)
      self.assertEqual(result.host_confirmations, 0)
    finally:
      task.cancel()
      await asyncio.gather(task, return_exceptions=True)

  async def test_heater_failure_blocks_following_motion(self) -> None:
    """A heater error retains its raw code and prevents the rest of the method."""
    protocol = read_flex_bdz(bdz_bytes("TEM 37 100\nMOI 310\nMOH 630 200 0 3000 0\n"))
    self.responses["MOI 310"] = "MOI\r\nMOI END 9\r\n"
    with self.assertRaises(FlexError) as error:
      await self.flex.run_protocol(protocol.bind_plates(self.plate_bindings))
    self.assertEqual(error.exception.code, 9)
    self.assertNotIn("MOH 630 200 0 3000 0", self.commands)
    self.assertEqual(self.flex.state, "uncertain")

  async def test_temperature_telemetry_does_not_wait_for_target(self) -> None:
    """TEM END permits the next command even if telemetry is below the target."""
    protocol = read_flex_bdz(bdz_bytes("TEM 37 100\nMOI 310\n"))
    self.responses["TEM 37 100"] = "TEM\r\nTEM 29 22\r\nTEM END 0\r\n"
    await self.flex.run_protocol(protocol.bind_plates(self.plate_bindings))
    self.assertIn("MOI 310", self.commands)
