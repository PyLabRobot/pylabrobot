"""`EventBridge`: the store's changes, followed through `Watch`, published as the events handles
emit for the same changes, once, and for changes handles never see."""

from __future__ import annotations

import unittest
from typing import Any, Awaitable, Callable, Dict, List, Tuple

from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.resources.server import handles as h
from pylabrobot.resources.server.client import ResourceServer
from pylabrobot.resources.server.definitions import definition_from_resource
from pylabrobot.resources.server.event_bridge import EventBridge
from pylabrobot.resources.server.tests.server_tests import ROOT, build_deck, item, ref
from pylabrobot.resources.server.v1 import server_pb2 as spb
from pylabrobot.resources.server.v1 import types_pb2 as pb

Scenario = Callable[[ResourceServer, h.Resource], Awaitable[Any]]
Recorded = List[Tuple[str, Dict[str, Any]]]


async def _with_deck() -> Tuple[ResourceServer, h.Resource]:
  server = ResourceServer.local()
  await server.commands.create_resources(
    spb.CreateResourcesRequest(
      resources=[spb.CreateResource(definition=definition_from_resource(build_deck()))]
    )
  )
  return server, await server.get(ROOT)


async def _moves(server: ResourceServer, deck: h.Resource) -> None:
  carrier = await deck.get_resource("plate_carrier", kind=h.Carrier)
  plate = await server.get("plate", root=deck)
  await plate.unassign()
  await carrier[3].assign_child_resource(plate)
  await carrier[4].assign_child_resource(plate)


async def _tips(server: ResourceServer, deck: h.Resource) -> None:
  tips = await server.get("tips", root=deck, kind=h.TipRack)
  await tips.set_tip_state([i != 0 for i in range(tips.num_items)])
  await tips.fill()
  await tips.empty()


async def _made_and_deleted(server: ResourceServer, deck: h.Resource) -> None:
  carrier = await deck.get_resource("plate_carrier", kind=h.Carrier)
  plate = await server.create("cor_96_wellplate_360uL_Fb", name="new_plate", parent=carrier[4])
  await plate.delete()


class EventBridgeTests(unittest.IsolatedAsyncioTestCase):
  async def client_events(self, scenario: Scenario) -> Recorded:
    """What handles emit for `scenario`, on a store with no bridge."""
    server, deck = await _with_deck()
    events: List[PLREvent] = []
    bus = EventBus()
    bus.subscribe(events.append)
    with use_event_bus(bus):
      await scenario(server, deck)
    await server.close()
    return [(event.name, event.data) for event in events]

  async def bridged_events(self, scenario: Scenario) -> List[PLREvent]:
    """What a bridge publishes for `scenario`, everything it emitted on the bus."""
    server, deck = await _with_deck()
    events: List[PLREvent] = []
    bus = EventBus()
    bus.subscribe(events.append)
    with use_event_bus(bus):
      async with EventBridge(server) as bridge:
        await scenario(server, deck)
        await bridge.caught_up()
    await server.close()
    return events

  async def assert_as_handles_emit(self, scenario: Scenario) -> None:
    bridged = await self.bridged_events(scenario)
    self.assertEqual([(e.name, e.data) for e in bridged], await self.client_events(scenario))
    self.assertTrue(all(event.context["source"] == "store" for event in bridged))

  async def test_moves_are_published_once_as_handles_emit_them(self):
    await self.assert_as_handles_emit(_moves)

  async def test_tips_made_and_taken(self):
    await self.assert_as_handles_emit(_tips)

  async def test_a_tree_made_and_deleted_in_a_parent_is_assigned_and_unassigned_at_its_top(self):
    await self.assert_as_handles_emit(_made_and_deleted)

  async def test_raw_commands_are_published(self):
    async def raw(server: ResourceServer, deck: h.Resource) -> None:
      await server.commands.assign_resources(
        spb.AssignResourcesRequest(
          assignments=[pb.AssignChild(child=ref("plate"), parent=ref("plate_carrier-3"))]
        )
      )

    self.assertEqual(await self.client_events(raw), [])
    bridged = await self.bridged_events(raw)
    self.assertEqual([e.name for e in bridged], ["resource.unassigned", "resource.assigned"])
    self.assertEqual(bridged[1].data["parent"]["name"], "plate_carrier-3")

  async def test_a_resolved_operation_is_published(self):
    async def pick_up(server: ResourceServer, deck: h.Resource) -> None:
      begun = await server.commands.begin_tool_pickup(
        spb.BeginToolPickupRequest(
          frame=ref(ROOT, ""),
          items=[spb.ToolPickupItem(source=item("tips", "A1"), shaft=ref("shaft_0"))],
        )
      )
      await server.commands.resolve_operation(
        spb.ResolveOperationRequest(
          operation_id=begun.operation.id, outcomes=[spb.ItemOutcome(item=0, succeeded=True)]
        )
      )

    bridged = await self.bridged_events(pick_up)
    self.assertEqual([e.name for e in bridged], ["resource.unassigned", "resource.assigned"])
    self.assertEqual(bridged[0].data["previous_parent"]["name"], "tips_tipspot_A1")
    self.assertEqual(bridged[1].data["parent"]["name"], "shaft_0")

  async def test_stopping_turns_the_client_s_own_events_back_on(self):
    server, _ = await _with_deck()
    bridge = EventBridge(server)
    await bridge.start()
    self.assertFalse(server.client_events)
    await bridge.stop()
    self.assertTrue(server.client_events)
    with self.assertRaises(RuntimeError):
      await bridge.caught_up()
    await server.close()

  async def test_only_the_trees_asked_for(self):
    server, deck = await _with_deck()
    loose = await server.create("cor_96_wellplate_360uL_Fb", name="loose")
    other = await server.create("cor_96_wellplate_360uL_Fb", name="other")
    events: List[PLREvent] = []
    bus = EventBus()
    bus.subscribe(events.append)
    async with EventBridge(server, bus=bus, roots=[deck]) as bridge:
      carrier = await deck.get_resource("plate_carrier", kind=h.Carrier)
      await carrier[3].assign_child_resource(loose)
      await other.delete()
      await bridge.caught_up()
    self.assertEqual([e.name for e in events], ["resource.assigned"])
    self.assertEqual(events[0].data["resource"]["name"], "loose")
    await server.close()


if __name__ == "__main__":
  unittest.main()
