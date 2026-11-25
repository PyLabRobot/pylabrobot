"""Plate-aware actions, explicit inventory and transfer completion with mocked HID."""

import asyncio
from dataclasses import replace

from pylabrobot.resources import thermo_TS_kingfisher_96_wellplate_2000uL_Vb

from .actions import FlexAction
from .connection import FlexError
from .flex import KingFisherFlex
from .protocol import FlexProtocolError, read_flex_bdz
from .test_helpers import HIDTestCase, bdz_bytes


class ActionTests(HIDTestCase):
  """No real instrument, arm or operator adapter is needed to test the action contract."""

  async def asyncSetUp(self) -> None:
    """Prepare a mid-run exchange followed by targeted and untargeted interventions."""
    self.mock_hid()
    self.flex = KingFisherFlex(timeout=5, operation_timeout=5)
    self.flex.io = self.io
    self.a = thermo_TS_kingfisher_96_wellplate_2000uL_Vb("physical_a")
    self.b = thermo_TS_kingfisher_96_wellplate_2000uL_Vb("physical_b")
    self.protocol = read_flex_bdz(
      bdz_bytes(
        'DTL 0 "test"\nMOR 7\nPAU 1 "A17"\nTXT "phase A"\nPAU 2 "A17"\n'
        'PAU 1 "B42"\nTXT 11 "add"\nMOR 7\nPAU 0 "add liquid"\nTXT "add"\nMOR 2\n'
        'TXT 10 "inspect"\nMOR 7\nPAU 0 "inspect plate"\nTXT "inspect"\nMOR 2\n'
        'MOR 7\nPAU 0 "general check"\nPAU 2 "B42"\nTXT "done"\n',
        plates=(("a-id", "A17"), ("b-id", "B42")),
        steps='<Pause name="add"><Message>add liquid</Message>'
        '<Plates><Plate id="b-id" wellGroup="Plate" /></Plates>'
        '<Dispense enabled="true"><Reagents><Reagent name="water" volume="50" />'
        '</Reagents></Dispense></Pause><Pause name="inspect"><Message>inspect plate</Message>'
        '<Plates><Plate id="b-id" /></Plates></Pause>',
      )
    )
    self.bound = self.protocol.bind_plates({"A17": self.a, "B42": self.b})

  async def asyncTearDown(self) -> None:
    """Drain mocked session tasks."""
    await self.flex.stop()

  async def ready(self) -> None:
    """Establish orientation and explicitly reconcile the current simulated contents."""
    await self.flex.setup()
    await self.flex.initialize()
    self.flex.reconcile_inventory()

  async def fulfill(self, action: FlexAction) -> bool:
    """Complete a modeled transfer or intervention synchronously within the test handler."""
    self.complete_transfer(action)
    return True

  async def test_structured_actions_cover_exchange_additions_and_final_unload(self) -> None:
    """All actions use the original source identity, bound object and persistent holder."""
    await self.ready()
    seen: list[FlexAction] = []
    holder = self.flex.carousel[2]

    async def fulfill(action: FlexAction) -> bool:
      """Inspect action context before updating the modeled transfer."""
      self.assertIs(action.holder, holder)
      self.assertIs(self.flex.loading_holder, holder)
      self.assertEqual(action.source_sha256, self.protocol.sha256)
      self.assertEqual(action.command.line_number, action.line_number)
      self.assertEqual(
        action.command.text, self.protocol.script.splitlines()[action.line_number - 1]
      )
      if action.plate_id is not None:
        self.assertIs(action.plate, self.bound.plates_by_id[action.plate_id])
      seen.append(action)
      return await self.fulfill(action)

    result = await self.flex.run_protocol(self.bound, handle_action=fulfill)
    self.assertEqual(
      [a.action for a in seen], ["load", "unload", "load", "pause", "pause", "pause", "unload"]
    )
    self.assertEqual(
      [a.plate_id for a in seen], ["a-id", "a-id", "b-id", "b-id", "b-id", None, "b-id"]
    )
    self.assertEqual(seen[0].plate_name, "A17")
    self.assertEqual(seen[3].additions[0].volume, 50)
    self.assertEqual(seen[3].additions[0].reagent_name, "water")
    self.assertEqual(seen[4].label, "inspect plate")
    self.assertIsNone(seen[5].plate)
    self.assertEqual(result.host_confirmations, len(seen))
    self.assertFalse(any(command.startswith("PAU") for command in self.commands))
    self.assertTrue(self.flex.inventory_verified)
    self.assertIs(self.flex.carousel[2], holder)
    self.assertIsNone(holder.resource)
    self.assertIsNone(self.a.parent)
    self.assertIsNone(self.b.parent)

  async def test_assignment_does_not_complete_handler_or_unfinished_rotation(self) -> None:
    """ACK cannot present an action, and assignment cannot release a pending handler."""
    await self.ready()
    self.responses["MOR 7"] = "MOR\r\n"
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fulfill(action: FlexAction) -> bool:
      """Model loading, then wait separately for transfer completion/clearance."""
      self.complete_transfer(action)
      if action.line_number == 3:
        entered.set()
        await release.wait()
      return True

    task = asyncio.create_task(self.flex.run_protocol(self.bound, handle_action=fulfill))
    try:
      await self.wait_sent("MOR 7")
      self.assertFalse(entered.is_set())
      self.assertIsNone(self.flex.loading_holder)
      self.emit("MOR END 0\r\n")
      await asyncio.wait_for(entered.wait(), 1)
      self.assertIs(self.flex.carousel[2].resource, self.a)
      self.assertNotIn('TXT "phase A"', self.commands)
      self.assertFalse(self.flex.inventory_verified)
      with self.assertRaisesRegex(FlexError, "active"):
        self.flex.reconcile_inventory()
      self.responses.pop("MOR 7")
      release.set()
      await task
    finally:
      if not task.done():
        task.cancel()
      await asyncio.gather(task, return_exceptions=True)

  async def test_empty_inventory_requires_confirmation_after_connection(self) -> None:
    """Homing and an empty tree cannot replace explicit inventory reconciliation."""
    self.flex.reconcile_inventory()
    await self.flex.setup()
    await self.flex.initialize()
    before = list(self.commands)
    with self.assertRaisesRegex(FlexError, "reconcile_inventory"):
      await self.flex.run_protocol(self.bound, handle_action=self.fulfill)
    self.assertEqual(self.commands, before)
    self.flex.reconcile_inventory()
    await self.flex.run_protocol(self.bound, handle_action=self.fulfill)

  async def test_preloaded_plate_is_checked_and_still_presented_to_handler(self) -> None:
    """Matching preload avoids a duplicate transfer, without skipping acknowledgment."""
    self.flex.carousel[2] = self.a
    await self.ready()
    seen: list[FlexAction] = []

    async def fulfill(action: FlexAction) -> bool:
      """Recognize the already-loaded plate before fulfilling subsequent actions."""
      if not seen:
        self.assertIs(action.holder.resource, self.a)
      seen.append(action)
      return await self.fulfill(action)

    await self.flex.run_protocol(self.bound, handle_action=fulfill)
    self.assertEqual(seen[0].action, "load")

  async def test_inconsistent_initial_inventory_fails_before_preamble(self) -> None:
    """Reject wrong preloaded trays and conflicting occupants before any run motion."""
    await self.ready()
    before = list(self.commands)
    for tray, plate, message in ((1, self.a, "different Flex tray"), (2, self.b, "occupied")):
      with self.subTest(tray=tray):
        self.flex.carousel[tray] = plate
        self.flex.reconcile_inventory()
        with self.assertRaisesRegex(FlexError, message):
          await self.flex.run_protocol(self.bound, handle_action=self.fulfill)
        self.assertEqual(self.commands, before)
        self.assertEqual(self.flex.state, "ready")
        self.assertIs(self.flex.carousel[tray].resource, plate)
        plate.unassign()

  async def test_successful_return_requires_expected_occupancy(self) -> None:
    """No update, wrong plate/tray or unrelated changes block the next script command."""
    for mistake in ("nothing", "wrong_plate", "wrong_tray", "unrelated"):
      with self.subTest(mistake=mistake):
        await self.ready()
        before = len(self.commands)

        async def incorrect(action: FlexAction) -> bool:
          """Return success with a deliberately incorrect transfer record."""
          if mistake == "wrong_plate":
            action.holder.assign_child_resource(self.b)
          elif mistake == "wrong_tray":
            self.flex.carousel[3] = self.a
          elif mistake == "unrelated":
            self.complete_transfer(action)
            self.flex.carousel[5] = self.b
          return True

        with self.assertRaisesRegex(FlexError, "incorrect occupancy"):
          await self.flex.run_protocol(self.bound, handle_action=incorrect)
        self.assertNotIn('TXT "phase A"', self.commands[before:])
        self.assertFalse(self.flex.inventory_verified)
        self.assertEqual(self.flex.state, "uncertain")
        await self.flex.stop()
        for plate in (self.a, self.b):
          if plate.parent is not None:
            plate.unassign()

  async def test_unload_confirmation_requires_actual_removal(self) -> None:
    """Returning True while the bound plate remains loaded cannot advance an exchange."""
    await self.ready()

    async def incomplete(action: FlexAction) -> bool:
      """Load correctly but leave the plate in place when asked to unload."""
      if action.action == "load":
        self.complete_transfer(action)
      return True

    with self.assertRaisesRegex(FlexError, "unload.*incorrect occupancy"):
      await self.flex.run_protocol(self.bound, handle_action=incomplete)
    self.assertNotIn('TXT 11 "add"', self.commands)
    self.assertIs(self.a.parent, self.flex.carousel[2])

  async def test_interrupted_handler_preserves_assignment_and_requires_reconciliation(self) -> None:
    """Failure after a transfer preserves its record without trusting run completion."""
    for failure in ("reject", "error", "timeout", "cancel", "abort", "disconnect"):
      with self.subTest(failure=failure):
        await self.ready()
        entered = asyncio.Event()
        before = len(self.commands)

        async def interrupted(action: FlexAction) -> bool:
          """Move the modeled plate before an incomplete or failed fulfillment."""
          self.complete_transfer(action)
          entered.set()
          if failure == "reject":
            return False
          if failure == "error":
            raise RuntimeError("transfer failed after placement")
          await asyncio.Event().wait()
          return True

        task = asyncio.create_task(
          self.flex.run_protocol(self.bound, handle_action=interrupted, operator_timeout=0.03)
        )
        await asyncio.wait_for(entered.wait(), 1)
        if failure == "cancel":
          task.cancel()
        elif failure == "abort":
          await self.flex.abort()
        elif failure == "disconnect":
          self.incoming.put_nowait(OSError("disconnected"))
        with self.assertRaises(
          (FlexError, RuntimeError, asyncio.TimeoutError, asyncio.CancelledError)
        ):
          await task
        self.assertIs(self.flex.carousel[2].resource, self.a)
        self.assertFalse(self.flex.inventory_verified)
        self.assertIsNone(self.flex.loading_holder)
        self.assertNotIn('TXT "phase A"', self.commands[before:])
        await self.flex.stop()
        await self.flex.setup()
        await self.flex.initialize()
        with self.assertRaisesRegex(FlexError, "reconcile_inventory"):
          await self.flex.run_protocol(self.bound, handle_action=self.fulfill)
        await self.flex.stop()

  async def test_physical_start_does_not_infer_a_transfer(self) -> None:
    """Firmware PAU END 0 without a modeled load fails the same occupancy check."""
    await self.ready()
    with self.assertRaisesRegex(FlexError, "incorrect occupancy"):
      await self.flex.run_protocol(self.bound)
    self.assertIn('PAU 1 "A17"', self.commands)
    self.assertNotIn('TXT "phase A"', self.commands)
    self.assertIsNone(self.a.parent)

  async def test_physical_pause_events_expose_action_and_wait_for_start(self) -> None:
    """A modeled transfer alone cannot complete an active physical PAU exchange."""
    protocol = read_flex_bdz(
      bdz_bytes('MOR 7\nPAU 1 "A17"\nPAU 2 "A17"\n', plates=(("a-id", "A17"),))
    )
    await self.ready()
    self.responses['PAU 1 "A17"'] = "PAU\r\n"
    self.responses['PAU 2 "A17"'] = "PAU\r\n"
    task = asyncio.create_task(self.flex.run_protocol(protocol.bind_plates({"A17": self.a})))
    try:
      for kind in ("load", "unload"):
        async for event in self.flex.events():
          if event.kind == kind:
            self.assertIsNotNone(event.prompt)
            assert event.prompt is not None
            self.assertIs(event.prompt.plate, self.a)
            self.assertEqual(event.prompt.plate_id, "a-id")
            self.assertIs(event.prompt.holder, self.flex.carousel[2])
            self.complete_transfer(event.prompt)
            break
        self.assertFalse(task.done())
        if kind == "load":
          self.assertNotIn('PAU 2 "A17"', self.commands)
        self.emit("PAU END 0\r\n")
      await task
      self.assertTrue(self.flex.inventory_verified)
      self.assertIsNone(self.a.parent)
    finally:
      if not task.done():
        task.cancel()
      await asyncio.gather(task, return_exceptions=True)

  async def test_intervention_must_leave_its_bound_plate_in_place(self) -> None:
    """Reagent addition confirmation cannot authorize an unintended plate removal."""
    await self.ready()

    async def incorrect(action: FlexAction) -> bool:
      """Perform transfers correctly, but remove the addition target."""
      if action.additions:
        self.b.unassign()
      else:
        self.complete_transfer(action)
      return True

    with self.assertRaisesRegex(FlexError, "pause.*B42.*incorrect occupancy"):
      await self.flex.run_protocol(self.bound, handle_action=incorrect)
    self.assertNotIn('TXT "add"', self.commands)
    self.assertFalse(self.flex.inventory_verified)

  async def test_unexpected_inventory_change_during_motion_blocks_next_command(self) -> None:
    """An external assignment during rotation invalidates certainty without auto-resume."""
    await self.ready()
    self.responses["MOR 7"] = "MOR\r\n"
    task = asyncio.create_task(self.flex.run_protocol(self.bound, handle_action=self.fulfill))
    await self.wait_sent("MOR 7")
    self.flex.carousel[8] = self.b
    self.emit("MOR END 0\r\n")
    with self.assertRaisesRegex(FlexError, "Inventory is uncertain"):
      await task
    self.assertIs(self.flex.carousel[8].resource, self.b)
    self.assertIsNone(self.flex.carousel[2].resource)
    self.assertNotIn('TXT "phase A"', self.commands)

  async def test_reloaded_plate_can_receive_a_later_addition(self) -> None:
    """A reagent intervention resolves the most recent load after an intermediate unload."""
    protocol = replace(
      self.protocol,
      script=self.protocol.script.replace(
        'PAU 1 "B42"\n', 'PAU 1 "B42"\nPAU 2 "B42"\nPAU 1 "B42"\n'
      ),
    )
    await self.ready()
    result = await self.flex.run_protocol(
      protocol.bind_plates({"A17": self.a, "B42": self.b}), handle_action=self.fulfill
    )
    self.assertEqual(result.host_confirmations, 9)

  async def test_unknown_action_plate_or_invalid_handler_fail_offline(self) -> None:
    """No hardware access is needed to reject actions that cannot be resolved safely."""
    invalid = replace(
      self.protocol, script=self.protocol.script.replace('PAU 1 "A17"', 'PAU 1 "other"')
    )
    with self.assertRaisesRegex(FlexProtocolError, "unknown BDZ plate"):
      await self.flex.run_protocol(invalid.bind_plates({"A17": self.a, "B42": self.b}))
    with self.assertRaisesRegex(TypeError, "async callable"):
      await self.flex.run_protocol(self.bound, handle_action=True)  # type: ignore[arg-type]
    with self.assertRaisesRegex(ValueError, "requires an action handler"):
      await self.flex.run_protocol(self.bound, acknowledgement="host")
    self.io.setup.assert_not_awaited()
    self.io.write.assert_not_awaited()
