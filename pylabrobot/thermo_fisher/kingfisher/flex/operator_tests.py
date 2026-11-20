"""Interchangeable fulfillment paths with real ManualOperator logic and mocked HID."""

import asyncio
from typing import Literal, cast
from unittest.mock import AsyncMock, create_autospec

from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.manual_operator import (
  ManualOperator,
  OperatorActionCancelledError,
  OperatorActionFailedError,
  OperatorActionProvider,
  OperatorActionRequest,
  OperatorActionResult,
)
from pylabrobot.resources import (
  Resource,
  ResourceHolder,
  thermo_TS_kingfisher_96_wellplate_2000uL_Vb,
)

from .actions import FlexAction
from .connection import FlexError
from .flex import KingFisherFlex
from .operator import FlexManualHandler, FlexStartAcknowledgement
from .protocol import read_flex_bdz
from .test_helpers import HIDTestCase, bdz_bytes


class OperatorTests(HIDTestCase):
  """Physical and software acknowledgment share transfer and failure semantics."""

  def destination_for(self, action: FlexAction) -> Resource:
    """Choose a workcell endpoint outside the driver."""
    return self.destination

  async def physical_handler(self, action: FlexAction) -> bool:
    """Use physical Start as the provider for this specific ManualOperator request."""
    operator = ManualOperator(FlexStartAcknowledgement(self.flex, action), name="physical_operator")
    return await FlexManualHandler(operator, unload_destination=self.destination_for)(action)

  async def asyncSetUp(self) -> None:
    """Prepare one arbitrary plate role, external endpoints and a configurable provider."""
    self.mock_hid()
    self.flex = KingFisherFlex(timeout=0.2, operation_timeout=0.2)
    self.flex.io = self.io
    self.plate = thermo_TS_kingfisher_96_wellplate_2000uL_Vb("physical_001")
    self.source = ResourceHolder("storage", 0, 0, 0)
    self.destination = ResourceHolder("reader_input", 0, 0, 0)
    self.source.assign_child_resource(self.plate)
    self.protocol = read_flex_bdz(
      bdz_bytes(
        'MOR 7\nPAU 1 "X17"\nTXT 11 "add"\nMOR 7\nPAU 0 "add liquid"\n'
        'TXT "add"\nMOR 2\nMOR 7\nPAU 0 "inspect"\nPAU 2 "X17"\nTXT "done"\n',
        plates=(("plate-id", "X17"),),
        steps='<Pause name="add"><Message>add liquid</Message>'
        '<Plates><Plate id="plate-id" wellGroup="Plate" /></Plates>'
        '<Dispense enabled="true"><Reagents><Reagent name="buffer" volume="50" />'
        "</Reagents></Dispense></Pause>",
      )
    )
    self.bound = self.protocol.bind_plates({"X17": self.plate})
    self.provider = create_autospec(OperatorActionProvider, instance=True, spec_set=True)
    self.provider.request.return_value = OperatorActionResult.completed(
      confirmed_by="test_operator"
    )
    self.manual = FlexManualHandler(
      ManualOperator(self.provider), unload_destination=self.destination_for
    )
    await self.flex.setup()
    await self.flex.initialize()
    self.flex.reconcile_inventory()

  async def asyncTearDown(self) -> None:
    """Close only the mocked connection."""
    await self.flex.stop()

  async def test_software_provider_moves_between_application_endpoints_without_pau(self) -> None:
    """ManualOperator supplies source/destination context and applies completed moves."""
    result = await self.flex.run_protocol(self.bound, handle_action=self.manual)
    requests = [call.args[0] for call in self.provider.request.await_args_list]
    self.assertEqual(
      [request.action for request in requests],
      ["resource.move", "kingfisher.intervention", "kingfisher.intervention", "resource.move"],
    )
    self.assertIs(requests[0].source, self.source)
    self.assertIs(requests[0].destination, self.flex.carousel[2])
    self.assertIs(requests[0].resources[0], self.plate)
    self.assertEqual(requests[0].details["bdz_plate_id"], "plate-id")
    self.assertEqual(requests[0].details["source_sha256"], self.protocol.sha256)
    self.assertEqual(requests[1].details["additions"][0]["volume"], 50)
    self.assertIs(requests[1].resources[0], self.plate)
    self.assertEqual(requests[2].resources, ())
    self.assertIs(requests[3].source, self.flex.carousel[2])
    self.assertIs(requests[3].destination, self.destination)
    self.assertIs(self.plate.parent, self.destination)
    self.assertIsNone(self.source.resource)
    self.assertIsNone(self.flex.carousel[2].resource)
    self.assertEqual(result.host_confirmations, 4)
    self.assertFalse(any(command.startswith("PAU") for command in self.commands))

  async def test_physical_provider_waits_for_end_before_reassignment_and_emits_lifecycle(
    self,
  ) -> None:
    """No model update or ManualOperator completion occurs on PAU acknowledgment alone."""
    pauses = [command.text for command in self.protocol.preflight() if command.opcode == "PAU"]
    for command in pauses:
      self.responses[command] = "PAU\r\n"
    bus = EventBus()
    events: list[PLREvent] = []
    bus.subscribe(events.append)
    with use_event_bus(bus):
      task = asyncio.create_task(
        self.flex.run_protocol(
          self.bound, handle_action=self.physical_handler, acknowledgement="instrument"
        )
      )
      try:
        for index, command in enumerate(pauses):
          await self.wait_sent(command)
          manual_events = [event for event in events if event.name.startswith("manual_operator.")]
          self.assertEqual(len(manual_events), index * 2 + 1)
          self.assertTrue(manual_events[-1].name.endswith(".started"))
          self.assertIs(self.plate.parent, self.source if index == 0 else self.flex.carousel[2])
          self.assertFalse(task.done())
          self.emit("PAU END 0\r\n")
        result = await task
      finally:
        if not task.done():
          task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    self.assertEqual(result.host_confirmations, 0)
    self.assertEqual(result.completed_commands, len(self.protocol.preflight()))
    self.assertIs(self.plate.parent, self.destination)
    manual_events = [event for event in events if event.name.startswith("manual_operator.")]
    self.assertEqual(len(manual_events), 8)
    self.assertTrue(all(event.name.endswith(".completed") for event in manual_events[1::2]))
    await self.flex.stop()
    flex_events = [event async for event in self.flex.events()]
    self.assertEqual(sum(event.kind == "prompt_completed" for event in flex_events), 4)
    self.assertFalse(any(event.kind.startswith("host_") for event in flex_events))

  async def test_per_action_selection_can_mix_physical_pauses_and_software_transfers(self) -> None:
    """The application chooses a path before each action's PAU can be sent."""

    def select(action: FlexAction) -> Literal["host", "instrument"]:
      """Use the Flex button for interventions only."""
      return "instrument" if action.action == "pause" else "host"

    async def handle(action: FlexAction) -> bool:
      """Fulfill through the provider corresponding to the selected path."""
      if action.action == "pause":
        return await self.physical_handler(action)
      return await self.manual(action)

    result = await self.flex.run_protocol(self.bound, handle_action=handle, acknowledgement=select)
    self.assertEqual(result.host_confirmations, 2)
    self.assertEqual(
      [c for c in self.commands if c.startswith("PAU")], ['PAU 0 "add liquid"', 'PAU 0 "inspect"']
    )
    self.assertEqual(self.provider.request.await_count, 2)
    self.assertIs(self.plate.parent, self.destination)

  async def test_preloaded_plate_is_confirmed_without_duplicate_manual_move(self) -> None:
    """ManualOperator.perform handles an already-loaded plate while preserving its identity."""
    self.plate.unassign()
    self.flex.carousel[2] = self.plate
    self.flex.reconcile_inventory()
    await self.flex.run_protocol(self.bound, handle_action=self.manual)
    first = self.provider.request.await_args_list[0].args[0]
    self.assertEqual(first.action, "kingfisher.confirm_load")
    self.assertIs(first.source, self.flex.carousel[2])
    self.assertIs(first.destination, self.flex.carousel[2])
    self.assertIs(self.plate.parent, self.destination)

  async def test_provider_rejection_failure_or_exception_blocks_model_update(self) -> None:
    """A manual move is not recorded when its acknowledgment does not succeed."""
    for failure in ("cancelled", "failed", "exception"):
      with self.subTest(failure=failure):
        before = len(self.commands)
        self.provider.request.side_effect = (
          RuntimeError("provider unavailable") if failure == "exception" else None
        )
        self.provider.request.return_value = (
          OperatorActionResult.cancelled()
          if failure == "cancelled"
          else OperatorActionResult.failed()
        )
        with self.assertRaises(
          (OperatorActionCancelledError, OperatorActionFailedError, RuntimeError)
        ):
          await self.flex.run_protocol(self.bound, handle_action=self.manual)
        self.assertIs(self.plate.parent, self.source)
        self.assertIsNone(self.flex.carousel[2].resource)
        self.assertNotIn('TXT 11 "add"', self.commands[before:])
        self.assertFalse(self.flex.inventory_verified)
        await self.flex.stop()
        await self.flex.setup()
        await self.flex.initialize()
        self.flex.reconcile_inventory()

  async def test_software_timeout_and_cancellation_leave_transfer_unconfirmed(self) -> None:
    """A pending provider is cancelled without moving the plate or advancing commands."""
    for cancel in (False, True):
      with self.subTest(cancel=cancel):
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        before = len(self.commands)

        async def pending(request: OperatorActionRequest) -> OperatorActionResult:
          """Wait indefinitely until the enclosing run cancels this operator action."""
          entered.set()
          try:
            await asyncio.Event().wait()
            return OperatorActionResult.completed()
          finally:
            cancelled.set()

        self.provider.request.side_effect = pending
        task = asyncio.create_task(
          self.flex.run_protocol(self.bound, handle_action=self.manual, operator_timeout=0.03)
        )
        await asyncio.wait_for(entered.wait(), 1)
        if cancel:
          task.cancel()
        with self.assertRaises((asyncio.CancelledError, asyncio.TimeoutError)):
          await task
        self.assertTrue(cancelled.is_set())
        self.assertIs(self.plate.parent, self.source)
        self.assertIsNone(self.flex.carousel[2].resource)
        self.assertNotIn('TXT 11 "add"', self.commands[before:])
        await self.flex.stop()
        await self.flex.setup()
        await self.flex.initialize()
        self.flex.reconcile_inventory()

  async def test_invalid_selection_is_rejected_before_pau_or_handler(self) -> None:
    """A selector cannot silently fall back to a different acknowledgment path."""

    def invalid(action: FlexAction) -> Literal["host", "instrument"]:
      """Deliberately violate the selector contract."""
      return cast(Literal["host", "instrument"], None)

    with self.assertRaisesRegex(ValueError, "selector must return"):
      await self.flex.run_protocol(self.bound, handle_action=self.manual, acknowledgement=invalid)
    self.provider.request.assert_not_awaited()
    self.assertFalse(any(command.startswith("PAU") for command in self.commands))

  async def test_physical_error_timeout_cancel_abort_or_disconnect_never_reassigns(self) -> None:
    """The hardware acknowledgment is subject to the same interruption rules as the handler."""
    for failure in ("error", "timeout", "cancel", "abort", "disconnect"):
      with self.subTest(failure=failure):
        before = len(self.commands)
        self.responses['PAU 1 "X17"'] = "PAU\r\nPAU END 13\r\n" if failure == "error" else "PAU\r\n"
        task = asyncio.create_task(
          self.flex.run_protocol(
            self.bound,
            handle_action=self.physical_handler,
            acknowledgement="instrument",
            operator_timeout=0.03,
          )
        )
        await self.wait_sent('PAU 1 "X17"')
        if failure == "cancel":
          task.cancel()
        elif failure == "abort":
          await self.flex.abort()
        elif failure == "disconnect":
          self.incoming.put_nowait(OSError("USB disconnected"))
        with self.assertRaises((FlexError, asyncio.TimeoutError, asyncio.CancelledError)):
          await task
        self.assertIs(self.plate.parent, self.source)
        self.assertIsNone(self.flex.carousel[2].resource)
        self.assertNotIn('TXT 11 "add"', self.commands[before:])
        self.assertFalse(self.flex.inventory_verified)
        await self.flex.stop()
        await self.flex.setup()
        await self.flex.initialize()
        self.flex.reconcile_inventory()

  async def test_source_changes_while_software_request_pending_are_not_overwritten(self) -> None:
    """ManualOperator revalidates its endpoints after acknowledgment before updating the tree."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def confirm(request: OperatorActionRequest) -> OperatorActionResult:
      """Hold the provider acknowledgment while an external model change occurs."""
      entered.set()
      await release.wait()
      return OperatorActionResult.completed()

    self.provider.request.side_effect = confirm
    task = asyncio.create_task(self.flex.run_protocol(self.bound, handle_action=self.manual))
    await asyncio.wait_for(entered.wait(), 1)
    self.assertIs(self.plate.parent, self.source)
    self.plate.unassign()
    self.destination.assign_child_resource(self.plate)
    release.set()
    with self.assertRaisesRegex(RuntimeError, "model changed"):
      await task
    self.assertIs(self.plate.parent, self.destination)
    self.assertIsNone(self.flex.carousel[2].resource)
    self.assertNotIn('TXT 11 "add"', self.commands)

  async def test_instrument_mode_cannot_be_completed_without_instrument_acknowledgment(
    self,
  ) -> None:
    """A software-provider result cannot stand in for physical Start when instrument mode is selected."""
    with self.assertRaisesRegex(FlexError, "must await wait_for_physical_start"):
      await self.flex.run_protocol(
        self.bound, handle_action=self.manual, acknowledgement="instrument"
      )
    self.assertFalse(any(command.startswith("PAU") for command in self.commands))
    self.assertNotIn('TXT 11 "add"', self.commands)
    self.assertFalse(self.flex.inventory_verified)

  async def test_physical_provider_in_host_mode_is_rejected_before_pau(self) -> None:
    """An adapter cannot change a host action into an instrument pause implicitly."""
    with self.assertRaisesRegex(FlexError, "instrument acknowledgment"):
      await self.flex.run_protocol(self.bound, handle_action=self.physical_handler)
    self.assertFalse(any(command.startswith("PAU") for command in self.commands))
    self.assertIs(self.plate.parent, self.source)

  async def test_duplicate_and_stale_physical_acknowledgments_cannot_send_another_pau(self) -> None:
    """The exact active action can be acknowledged once; an old adapter cannot affect another run."""
    saved: list[FlexAction] = []

    async def duplicate(action: FlexAction) -> bool:
      """Attempt to acknowledge one action twice."""
      saved.append(action)
      await self.flex.wait_for_physical_start(action)
      await self.flex.wait_for_physical_start(action)
      return True

    with self.assertRaisesRegex(FlexError, "already been requested"):
      await self.flex.run_protocol(
        self.bound, handle_action=duplicate, acknowledgement="instrument"
      )
    self.assertEqual(self.commands.count('PAU 1 "X17"'), 1)
    await self.flex.stop()
    await self.flex.setup()
    await self.flex.initialize()
    self.flex.reconcile_inventory()

    async def stale(action: FlexAction) -> bool:
      """Reuse an adapter captured from a different action instance."""
      operator = ManualOperator(FlexStartAcknowledgement(self.flex, saved[0]))
      await operator.perform(action="stale", title="Stale", instructions="Stale request")
      return True

    with self.assertRaisesRegex(FlexError, "active action"):
      await self.flex.run_protocol(self.bound, handle_action=stale, acknowledgement="instrument")
    self.assertEqual(self.commands.count('PAU 1 "X17"'), 1)

  async def test_manual_endpoints_are_required_and_unload_must_leave_carousel(self) -> None:
    """Sources/destinations belong to the application and are checked before provider use."""
    self.plate.unassign()
    with self.assertRaisesRegex(FlexError, "modeled source"):
      await self.flex.run_protocol(
        self.bound, handle_action=self.physical_handler, acknowledgement="instrument"
      )
    self.assertFalse(any(command.startswith("PAU") for command in self.commands))
    self.source.assign_child_resource(self.plate)
    await self.flex.stop()
    await self.flex.setup()
    await self.flex.initialize()
    self.flex.reconcile_inventory()
    invalid = FlexManualHandler(
      ManualOperator(self.provider), unload_destination=lambda action: self.flex.carousel[3]
    )
    with self.assertRaisesRegex(FlexError, "outside this Flex carousel"):
      await self.flex.run_protocol(self.bound, handle_action=invalid)
    self.assertIs(self.plate.parent, self.flex.carousel[2])
    self.assertNotIn('TXT "done"', self.commands)

  async def test_application_robotic_handler_needs_no_manual_operator_or_pau(self) -> None:
    """An awaited external transfer implementation can fulfill the same requests."""
    transfer = AsyncMock(return_value=None)

    async def robot(action: FlexAction) -> bool:
      """Wait for an application operation, then model its completed transfer."""
      if action.action in ("load", "unload"):
        assert action.plate is not None and action.plate.parent is not None
        target = action.holder if action.action == "load" else self.destination_for(action)
        await transfer(resource=action.plate, source=action.plate.parent, destination=target)
        action.plate.unassign()
        target.assign_child_resource(action.plate, location=None)
      return True

    await self.flex.run_protocol(self.bound, handle_action=robot)
    self.assertEqual(transfer.await_count, 2)
    self.provider.request.assert_not_awaited()
    self.assertFalse(any(command.startswith("PAU") for command in self.commands))
    self.assertIs(self.plate.parent, self.destination)
