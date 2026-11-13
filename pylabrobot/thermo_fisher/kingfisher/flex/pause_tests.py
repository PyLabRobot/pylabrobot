"""Operator pauses, reagent additions and timed waits with simulated HID."""

import asyncio
import unittest
from dataclasses import replace

from .connection import FlexError
from .flex import KingFisherFlex
from .flex_tests import ScriptedHID
from .protocol import FlexPrompt, FlexProtocolError, read_flex_bdz
from .test_helpers import bdz_bytes


class PauseTests(unittest.IsolatedAsyncioTestCase):
  """Keep manual confirmations distinct from firmware timers."""

  async def asyncSetUp(self) -> None:
    """Prepare one standalone addition pause and a simulated session."""
    self.io = ScriptedHID()
    self.flex = KingFisherFlex(timeout=0.2, operation_timeout=0.2)
    self.flex.io = self.io
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

  async def asyncTearDown(self) -> None:
    """Close the simulated reader."""
    await self.flex.stop()

  async def confirm_plate(self, prompt: FlexPrompt) -> bool:
    """Accept only loading/unloading actions."""
    self.assertIn(prompt.action, ("load", "unload"))
    return True

  async def test_plate_handler_does_not_release_manual_pause(self) -> None:
    """PAU 0 needs its own handler or an instrument completion."""
    self.io.responses['PAU 0 "add liquid"'] = "PAU\r\nTEM 37 22\r\n"
    task = asyncio.create_task(
      self.flex.run_protocol(self.protocol, confirm_prompt=self.confirm_plate)
    )
    try:
      await self.io.wait_sent('PAU 0 "add liquid"')
      self.assertFalse(task.done())
      self.assertNotIn('TXT "addition"', self.io.commands)
      self.io.emit("PAU END 0\r\n")
      result = await asyncio.wait_for(task, 1)
      self.assertEqual(result.host_confirmations, 2)
    finally:
      task.cancel()
      await asyncio.gather(task, return_exceptions=True)

  async def test_addition_metadata_and_host_gate(self) -> None:
    """Expose reagent details and hold all following motion until confirmed."""
    entered: asyncio.Queue[FlexPrompt] = asyncio.Queue()
    release = asyncio.Event()

    async def confirm(prompt: FlexPrompt) -> bool:
      """Hold a single addition confirmation."""
      entered.put_nowait(prompt)
      await release.wait()
      return True

    task = asyncio.create_task(
      self.flex.run_protocol(
        self.protocol,
        confirm_prompt=self.confirm_plate,
        confirm_pause=confirm,
      )
    )
    try:
      prompt = await asyncio.wait_for(entered.get(), 1)
      self.assertEqual(prompt.loading_slot, 1)
      self.assertEqual(prompt.additions[0].volume, 50)
      self.assertEqual(prompt.additions[0].reagent_name, "water")
      self.assertIn("50 uL", prompt.label)
      self.assertNotIn('TXT "addition"', self.io.commands)
      self.assertFalse(any(c.startswith("PAU") for c in self.io.commands))
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
    self.io.responses["WAI 1"] = "WAI\r\n"
    task = asyncio.create_task(self.flex.run_protocol(protocol))
    try:
      for count in (1, 2):
        await self.io.wait_sent("WAI 1")
        self.assertEqual(self.io.commands.count("MOH 630 200 0 3000 0"), count)
        self.assertNotIn('TXT "finished"', self.io.commands)
        self.io.emit("WAI END 0\r\n")
      result = await asyncio.wait_for(task, 1)
      self.assertEqual(result.completed_commands, 7)
      self.assertEqual(result.host_confirmations, 0)
    finally:
      task.cancel()
      await asyncio.gather(task, return_exceptions=True)

  async def test_heater_failure_blocks_following_motion(self) -> None:
    """A heater error retains its raw code and prevents the rest of the method."""
    protocol = read_flex_bdz(bdz_bytes("TEM 37 100\nMOI 310\nMOH 630 200 0 3000 0\n"))
    self.io.responses["MOI 310"] = "MOI\r\nMOI END 9\r\n"
    with self.assertRaises(FlexError) as error:
      await self.flex.run_protocol(protocol)
    self.assertEqual(error.exception.code, 9)
    self.assertNotIn("MOH 630 200 0 3000 0", self.io.commands)
    self.assertEqual(self.flex.state, "uncertain")

  async def test_temperature_telemetry_does_not_wait_for_target(self) -> None:
    """TEM END permits the next command even if telemetry is below the target."""
    protocol = read_flex_bdz(bdz_bytes("TEM 37 100\nMOI 310\n"))
    self.io.responses["TEM 37 100"] = "TEM\r\nTEM 29 22\r\nTEM END 0\r\n"
    await self.flex.run_protocol(protocol)
    self.assertIn("MOI 310", self.io.commands)
