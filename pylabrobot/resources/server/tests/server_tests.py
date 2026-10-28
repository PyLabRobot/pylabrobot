"""The resource store's command API, as any store must answer it.

Every test speaks to the store through `ResourceServer`, over Connect, and only through the
command API, `ResourceService`: what a store does is what these calls do.
`ServerTestCase.make_server` is the one place a store is chosen, so another store runs the same tests
by overriding it.
"""

from __future__ import annotations

import asyncio
import unittest
import warnings
from typing import List, Optional

from connectrpc.request import RequestContext
from pyqwest import Client

from pylabrobot.resources import Coordinate, ResourceStack
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb
from pylabrobot.resources.hamilton import (
  STARLetDeck,
  hamilton_96_tiprack_300uL_filter,
  hamilton_plate_carrier_L5_ac,
  hamilton_tip_carrier_L5,
)
from pylabrobot.resources.hamilton.trough_carriers import Trough_CAR_4R200_A00
from pylabrobot.resources.hamilton.troughs import hamilton_1_trough_200mL_Vb
from pylabrobot.resources.n_channel_pipettes import TipMountingShaft
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.server import build, handles, library
from pylabrobot.resources.server.asgi_transport import ASGITransport
from pylabrobot.resources.server.client import ResourceServer
from pylabrobot.resources.server.definitions import definition_from_resource
from pylabrobot.resources.server.errors import ServerError
from pylabrobot.resources.server.memory import MemoryStore, asgi_app
from pylabrobot.resources.server.tests.scenes import stacking_plate
from pylabrobot.resources.server.tree import to_coordinate
from pylabrobot.resources.server.v1 import primitives_pb2 as ppb
from pylabrobot.resources.server.v1 import server_pb2 as spb
from pylabrobot.resources.server.v1 import types_pb2 as pb
from pylabrobot.resources.server.v1.server_connect import ResourceServiceClient

ROOT = "deck"


def ref(name: str, root: str = ROOT) -> pb.ResourceRef:
  """A resource in the test deck, by name; a root by its own name when `root` is empty."""
  return pb.ResourceRef(name=pb.NameRef(root=root, name=name))


def item(parent: str, identifier: str) -> pb.ResourceRef:
  return pb.ResourceRef(item=pb.ItemRef(parent=ref(parent), identifier=identifier))


def build_deck() -> Resource:
  """A STARlet deck with a plate carrier, a tip carrier, a trough, and an arm with four channels."""
  with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    deck = STARLetDeck(name=ROOT)
    plates = hamilton_plate_carrier_L5_ac(name="plate_carrier")
    plates[0] = cor_96_wellplate_360uL_Fb(name="plate")
    plates[1] = cor_96_wellplate_360uL_Fb(name="other_plate")
    deck.assign_child_resource(plates, track=1)
    tips = hamilton_tip_carrier_L5(name="tip_carrier")
    tips[0] = hamilton_96_tiprack_300uL_filter(name="tips")
    deck.assign_child_resource(tips, track=8)
    troughs = Trough_CAR_4R200_A00(name="trough_carrier")
    troughs[0] = hamilton_1_trough_200mL_Vb(name="trough")
    deck.assign_child_resource(troughs, track=15)
    arm = Resource("x_arm", size_x=30, size_y=650, size_z=40, category="x_arm")
    deck.assign_child_resource(arm, location=Coordinate(400, 0, 250), ignore_collision=True)
    for channel in range(4):
      body = Resource(f"channel_{channel}", size_x=9, size_y=9, size_z=100, category="channel")
      arm.assign_child_resource(body, location=Coordinate(10, 400 - 9 * channel, 20))
      shaft = TipMountingShaft(name=f"shaft_{channel}", tip_pickup_mode="core")
      body.assign_child_resource(shaft, location=Coordinate(0, 0, -shaft.get_size_z()))
  return deck


class ServerTestCase(unittest.IsolatedAsyncioTestCase):
  """A store with the test deck in it, reached over Connect."""

  server: ResourceServer
  commands: ResourceServiceClient

  def make_server(self) -> ResourceServer:
    """The store under test. Another store's tests override this."""
    return ResourceServer.local()

  async def asyncSetUp(self) -> None:
    self.server = self.make_server()
    self.commands = self.server.commands
    await self.server.commands.create_resources(
      spb.CreateResourcesRequest(
        resources=[spb.CreateResource(definition=definition_from_resource(build_deck()))]
      )
    )

  async def asyncTearDown(self) -> None:
    await self.server.close()

  async def node(
    self, resource: pb.ResourceRef, view: pb.View = pb.VIEW_PENDING
  ) -> pb.ResourceNode:
    response = await self.server.commands.get_resources(
      spb.GetResourcesRequest(resources=[resource], depth=0, view=view)
    )
    return response.nodes[0]

  async def children(self, resource: pb.ResourceRef) -> List[pb.ResourceNode]:
    response = await self.server.commands.get_resources(
      spb.GetResourcesRequest(resources=[resource], depth=1)
    )
    return list(response.nodes[1:])

  async def tool_on(self, shaft: str) -> Optional[pb.ResourceNode]:
    tools = [
      n
      for n in await self.children(ref(shaft))
      if n.attributes.kind in (pb.RESOURCE_KIND_TIP, pb.RESOURCE_KIND_HEAD_TOOL)
    ]
    return tools[0] if tools else None

  async def volume(self, resource: pb.ResourceRef, view: pb.View = pb.VIEW_PENDING) -> float:
    return (await self.node(resource, view)).state.volume.pending_volume

  async def set_volume(self, resource: pb.ResourceRef, volume: float) -> None:
    await self.server.commands.update_resources(
      spb.UpdateResourcesRequest(
        updates=[spb.ResourceUpdate(volume=pb.SetVolume(resource=resource, volume=volume))]
      )
    )

  async def pick_up(self, spots: List[str], channels: List[int]) -> spb.BeginToolPickupResponse:
    return await self.server.commands.begin_tool_pickup(
      spb.BeginToolPickupRequest(
        frame=ref(ROOT, ""),
        items=[
          spb.ToolPickupItem(source=item("tips", spot), shaft=ref(f"shaft_{channel}"))
          for spot, channel in zip(spots, channels)
        ],
      )
    )

  async def resolve(self, operation: pb.Operation, *outcomes: spb.ItemOutcome) -> None:
    await self.server.commands.resolve_operation(
      spb.ResolveOperationRequest(operation_id=operation.id, outcomes=outcomes)
    )

  async def succeed(self, operation: pb.Operation) -> None:
    await self.resolve(
      operation, *[spb.ItemOutcome(item=i, succeeded=True) for i in range(len(operation.items))]
    )


class ServerInfoTests(ServerTestCase):
  async def test_info(self):
    info = await self.commands.get_server_info(spb.GetServerInfoRequest())
    self.assertEqual(info.api_version, "v1")
    self.assertTrue(info.instance_id)
    self.assertGreater(info.revision, 0)


class TreeTests(ServerTestCase):
  async def test_names_ids_and_items_name_the_same_resource(self):
    by_name = await self.node(ref("plate_well_A1"))
    by_item = await self.node(item("plate", "A1"))
    by_id = await self.node(pb.ResourceRef(id=by_name.id))
    self.assertEqual(by_name.id, by_item.id)
    self.assertEqual(by_name.id, by_id.id)
    self.assertEqual(by_name.attributes.identifier, "A1")

  async def test_unknown_names_are_refused(self):
    with self.assertRaises(ServerError) as refused:
      await self.node(ref("nowhere"))
    self.assertEqual(refused.exception.problem, pb.PROBLEM_NOT_FOUND)

  async def test_children_keep_their_order(self):
    wells = [n.attributes.identifier for n in await self.children(ref("plate"))]
    self.assertEqual(wells[:3], ["A1", "B1", "C1"])
    self.assertEqual(len(wells), 96)

  async def test_assign_to_a_holder_seats_it_there(self):
    await self.commands.unassign_resources(spb.UnassignResourcesRequest(resources=[ref("plate")]))
    root = await self.node(ref("plate", ""))
    self.assertFalse(root.attributes.HasField("location"))
    self.assertEqual(root.parent_id, b"")
    await self.commands.assign_resources(
      spb.AssignResourcesRequest(
        assignments=[pb.AssignChild(child=ref("plate", ""), parent=ref("plate_carrier-2"))]
      )
    )
    plate = await self.node(ref("plate"))
    holder = await self.node(ref("plate_carrier-2"))
    self.assertEqual(plate.parent_id, holder.id)
    self.assertAlmostEqual(plate.attributes.location.z, -3.03)

  async def test_assign_with_no_location_leaves_it_unlocated(self):
    await self.commands.unassign_resources(spb.UnassignResourcesRequest(resources=[ref("plate")]))
    deck = await self.node(ref("deck", ""))
    await self.commands.assign_resources(
      spb.AssignResourcesRequest(
        assignments=[
          pb.AssignChild(child=ref("plate", ""), parent=ref("deck", ""), no_location=True)
        ]
      )
    )
    plate = await self.node(ref("plate"))
    self.assertEqual(plate.parent_id, deck.id)
    self.assertFalse(plate.attributes.HasField("location"))

  async def test_a_holder_holding_something_refuses_more(self):
    with self.assertRaises(ServerError) as refused:
      await self.commands.assign_resources(
        spb.AssignResourcesRequest(
          assignments=[pb.AssignChild(child=ref("other_plate"), parent=ref("plate_carrier-0"))]
        )
      )
    self.assertEqual(refused.exception.problem, pb.PROBLEM_CANNOT_DROP_HERE)

  async def test_a_request_is_all_or_none(self):
    before = await self.node(ref("plate"))
    with self.assertRaises(ServerError):
      await self.commands.assign_resources(
        spb.AssignResourcesRequest(
          assignments=[
            pb.AssignChild(child=ref("plate"), parent=ref("plate_carrier-3")),
            pb.AssignChild(child=ref("other_plate"), parent=ref("plate_carrier-3")),
          ]
        )
      )
    after = await self.node(ref("plate"))
    self.assertEqual(before.parent_id, after.parent_id)
    self.assertEqual(before.attributes.location, after.attributes.location)

  async def test_tracks_place_on_a_hamilton_deck(self):
    await self.commands.unassign_resources(
      spb.UnassignResourcesRequest(resources=[ref("trough_carrier")])
    )
    await self.commands.assign_resources(
      spb.AssignResourcesRequest(
        assignments=[
          pb.AssignChild(child=ref("trough_carrier", ""), parent=ref(ROOT, ""), track=20)
        ]
      )
    )
    carrier = await self.node(ref("trough_carrier"))
    self.assertAlmostEqual(carrier.attributes.location.x, 100 + 19 * 22.5)

  async def test_a_deck_refuses_what_overlaps(self):
    await self.commands.unassign_resources(
      spb.UnassignResourcesRequest(resources=[ref("trough_carrier")])
    )
    with self.assertRaises(ServerError) as refused:
      await self.commands.assign_resources(
        spb.AssignResourcesRequest(
          assignments=[
            pb.AssignChild(child=ref("trough_carrier", ""), parent=ref(ROOT, ""), track=2)
          ]
        )
      )
    self.assertEqual(refused.exception.problem, pb.PROBLEM_CANNOT_DROP_HERE)

  async def test_names_are_unique_in_a_tree_and_among_roots(self):
    plate = definition_from_resource(cor_96_wellplate_360uL_Fb(name="plate"))
    await self.commands.create_resources(
      spb.CreateResourcesRequest(resources=[spb.CreateResource(definition=plate)])
    )
    with self.assertRaises(ServerError) as refused:
      await self.commands.create_resources(
        spb.CreateResourcesRequest(resources=[spb.CreateResource(definition=plate)])
      )
    self.assertEqual(refused.exception.problem, pb.PROBLEM_NAME_CONFLICT)
    with self.assertRaises(ServerError) as refused:
      await self.commands.assign_resources(
        spb.AssignResourcesRequest(
          assignments=[pb.AssignChild(child=ref("plate", ""), parent=ref("plate_carrier-3"))]
        )
      )
    self.assertEqual(refused.exception.problem, pb.PROBLEM_NAME_CONFLICT)

  async def test_models_name_their_children_after_the_instance(self):
    model = library.get_model("cor_96_wellplate_360uL_Fb")
    self.assertTrue(model.definition.attributes.itemized.HasField("grid"))
    model.name = "cor_96"
    await self.commands.put_model(spb.PutModelRequest(model=model))
    self.assertEqual(
      (await self.commands.get_model(spb.GetModelRequest(name="cor_96"))).name, "cor_96"
    )
    await self.commands.create_resources(
      spb.CreateResourcesRequest(
        resources=[
          spb.CreateResource(
            model="cor_96",
            name="fresh",
            assign=pb.AssignChild(parent=ref("plate_carrier-3")),
          )
        ]
      )
    )
    well = await self.node(ref("fresh_well_H12"))
    self.assertEqual(well.attributes.identifier, "H12")
    built = cor_96_wellplate_360uL_Fb(name="built").get_well("H12").location
    assert built is not None
    self.assertEqual(to_coordinate(well.attributes.location), built)
    fresh = await self.node(ref("fresh"))
    self.assertEqual(len(fresh.child_ids), 96)
    self.assertFalse(fresh.attributes.itemized.HasField("grid"))

  async def test_a_grid_and_listed_items_together_are_refused(self):
    definition = pb.ResourceDefinition()
    definition.CopyFrom(library.get_model("cor_96_wellplate_360uL_Fb").definition)
    item = definition.children.add()
    item.CopyFrom(definition.attributes.itemized.grid.item)
    item.attributes.identifier = "A1"
    with self.assertRaises(ServerError) as caught:
      await self.commands.create_resources(
        spb.CreateResourcesRequest(
          resources=[spb.CreateResource(definition=definition, name="both")]
        )
      )
    self.assertEqual(caught.exception.problem, pb.PROBLEM_WRONG_KIND)

  async def test_delete_takes_everything_under_it(self):
    await self.commands.delete_resources(spb.DeleteResourcesRequest(resources=[ref("plate")]))
    for name in ("plate", "plate_well_A1"):
      with self.assertRaises(ServerError):
        await self.node(ref(name))

  async def test_find_list_and_pages(self):
    found = await self.commands.find_resources(
      spb.FindResourcesRequest(
        scope=ref("plate"),
        filter=spb.ResourceFilter(kinds=[pb.RESOURCE_KIND_WELL]),
        page_size=50,
      )
    )
    self.assertEqual(len(found.nodes), 50)
    rest = await self.commands.find_resources(
      spb.FindResourcesRequest(
        scope=ref("plate"),
        filter=spb.ResourceFilter(kinds=[pb.RESOURCE_KIND_WELL]),
        page_size=50,
        page_token=found.next_page_token,
      )
    )
    self.assertEqual(len(rest.nodes), 46)
    self.assertEqual(rest.next_page_token, "")
    roots = await self.commands.list_roots(spb.ListRootsRequest())
    self.assertEqual([r.attributes.name for r in roots.roots], [ROOT])

  async def test_turning_about_a_pivot_keeps_the_pivot_still(self):
    before = await self.commands.resolve_geometry(
      spb.ResolveGeometryRequest(
        queries=[spb.GeometryQuery(resource=ref("plate_carrier"), anchors=[pb.Anchor()])]
      )
    )
    await self.commands.update_resources(
      spb.UpdateResourcesRequest(
        updates=[
          spb.ResourceUpdate(
            rotation=pb.RotationChange(
              resource=ref("plate_carrier"),
              by=pb.Rotation(z=90),
              pivot=pb.Vec3(),
            )
          )
        ]
      )
    )
    after = await self.commands.resolve_geometry(
      spb.ResolveGeometryRequest(
        queries=[spb.GeometryQuery(resource=ref("plate_carrier"), anchors=[pb.Anchor()])]
      )
    )
    self.assertEqual(before.results[0].locations[0], after.results[0].locations[0])
    self.assertAlmostEqual(after.results[0].absolute_rotation.z, 90)


class StackTests(ServerTestCase):
  async def test_a_stack_on_a_plate_holder_sinks_by_its_bottom_plate(self):
    stack = definition_from_resource(ResourceStack("stack", "z"))
    await self.commands.delete_resources(spb.DeleteResourcesRequest(resources=[ref("other_plate")]))
    await self.commands.create_resources(
      spb.CreateResourcesRequest(
        resources=[
          spb.CreateResource(definition=stack, assign=pb.AssignChild(parent=ref("plate_carrier-1")))
        ]
      )
    )
    for name in ("s1", "s2"):
      await self.commands.create_resources(
        spb.CreateResourcesRequest(
          resources=[
            spb.CreateResource(
              definition=definition_from_resource(stacking_plate(name)),
              assign=pb.AssignChild(parent=ref("stack")),
            )
          ]
        )
      )
    stacked = await self.node(ref("stack"))
    self.assertAlmostEqual(stacked.attributes.location.z, -1.5)
    second = await self.node(ref("s2"))
    self.assertAlmostEqual(second.attributes.location.z, 4.0)
    with self.assertRaises(ServerError):
      await self.commands.unassign_resources(spb.UnassignResourcesRequest(resources=[ref("s1")]))


class ToolTests(ServerTestCase):
  async def test_a_tip_moves_from_its_spot_to_the_shaft_and_back(self):
    response = await self.pick_up(["A1", "B1"], [0, 1])
    self.assertEqual(len(response.items), 2)
    self.assertEqual(response.items[0].tool.attributes.name, "tips_tipspot_A1#0")
    self.assertGreater(response.items[0].source.bottom.z, 0)
    tip = await self.tool_on("shaft_0")
    assert tip is not None
    self.assertIn(response.operation.id, list(tip.pending_operation_ids))
    committed = await self.node(item("tips", "A1"), pb.VIEW_COMMITTED)
    self.assertEqual(len(committed.child_ids), 1)
    await self.succeed(response.operation)
    tip = await self.tool_on("shaft_0")
    assert tip is not None
    self.assertEqual(list(tip.pending_operation_ids), [])

    drop = await self.commands.begin_tool_drop(
      spb.BeginToolDropRequest(
        frame=ref(ROOT, ""),
        items=[spb.ToolDropItem(shaft=ref("shaft_0"), origin=True)],
      )
    )
    await self.succeed(drop.operation)
    self.assertIsNone(await self.tool_on("shaft_0"))
    spot = await self.node(item("tips", "A1"))
    self.assertEqual(len(spot.child_ids), 1)

  async def test_a_failed_channel_leaves_its_tip_in_the_spot(self):
    response = await self.pick_up(["A1", "B1"], [0, 1])
    await self.resolve(
      response.operation,
      spb.ItemOutcome(item=0, succeeded=True),
      spb.ItemOutcome(item=1, succeeded=False),
    )
    self.assertIsNotNone(await self.tool_on("shaft_0"))
    self.assertIsNone(await self.tool_on("shaft_1"))
    self.assertEqual(len((await self.node(item("tips", "B1"))).child_ids), 1)

  async def test_an_empty_spot_is_an_issue_or_a_refusal(self):
    first = await self.pick_up(["A1"], [0])
    await self.succeed(first.operation)
    recorded = await self.pick_up(["A1"], [1])
    self.assertEqual([i.problem for i in recorded.issues], [pb.PROBLEM_NO_TIP])
    self.assertIsNotNone(await self.tool_on("shaft_1"))
    with self.assertRaises(ServerError) as refused:
      await self.commands.begin_tool_pickup(
        spb.BeginToolPickupRequest(
          frame=ref(ROOT, ""),
          items=[spb.ToolPickupItem(source=item("tips", "A1"), shaft=ref("shaft_2"))],
          on_tracking_problem=pb.ON_TRACKING_PROBLEM_REJECT,
        )
      )
    self.assertEqual(refused.exception.problem, pb.PROBLEM_NO_TIP)
    self.assertIsNone(await self.tool_on("shaft_2"))

  async def test_an_untracked_spot_makes_a_fresh_tip_each_time(self):
    await self.commands.update_resources(
      spb.UpdateResourcesRequest(
        updates=[spb.ResourceUpdate(tracking=pb.SetTracking(scope=ref("tips"), tip_tracking=False))]
      )
    )
    for channel in (0, 1):
      response = await self.pick_up(["A1"], [channel])
      self.assertEqual(list(response.issues), [])
      await self.succeed(response.operation)
    names = [(await self.tool_on(f"shaft_{c}")).attributes.name for c in (0, 1)]  # type: ignore[union-attr]
    # The rack's own tip in A1 was its first: "#0".
    self.assertEqual(names, ["tips_tipspot_A1#1", "tips_tipspot_A1#2"])
    self.assertEqual(len((await self.node(item("tips", "A1"))).child_ids), 1)

  async def test_a_tip_with_liquid_is_an_issue_to_drop(self):
    response = await self.pick_up(["A1"], [0])
    await self.succeed(response.operation)
    tip = await self.tool_on("shaft_0")
    assert tip is not None
    await self.set_volume(pb.ResourceRef(id=tip.id), 10)
    drop = await self.commands.begin_tool_drop(
      spb.BeginToolDropRequest(
        frame=ref(ROOT, ""),
        items=[spb.ToolDropItem(shaft=ref("shaft_0"), target=ref("trash"))],
      )
    )
    self.assertEqual([i.problem for i in drop.issues], [pb.PROBLEM_TIP_NOT_EMPTY])
    await self.succeed(drop.operation)
    trashed = await self.node(pb.ResourceRef(id=tip.id))
    self.assertEqual(trashed.parent_id, (await self.node(ref("trash"))).id)

  async def test_a_tool_named_itself_goes_back_where_it_stood(self):
    tool = build.resource(
      "{name}",
      Coordinate(36, 8, 32),
      kind=pb.RESOURCE_KIND_HEAD_TOOL,
      head_tool=pb.HeadToolSpec(fitting_depth=8.0),
    )
    front, back = pb.ResourceDefinition(), pb.ResourceDefinition()
    front.CopyFrom(tool)
    front.attributes.name = "tools_front"
    front.attributes.location.CopyFrom(build.vec3(Coordinate(-6.25, 8.0, 1.0)))
    back.CopyFrom(tool)
    back.attributes.name = "tools_back"
    back.attributes.location.CopyFrom(build.vec3(Coordinate(29.75, 34.5, 1.0)))
    holder = build.resource("tools", Coordinate(23.5, 42.5, 18), children=[front, back])
    await self.commands.create_resources(
      spb.CreateResourcesRequest(
        resources=[
          spb.CreateResource(
            definition=holder,
            assign=pb.AssignChild(parent=ref(ROOT, ""), location=build.vec3(Coordinate(300, 0, 0))),
          )
        ]
      )
    )
    picked = await self.commands.begin_tool_pickup(
      spb.BeginToolPickupRequest(
        frame=ref(ROOT, ""),
        items=[spb.ToolPickupItem(source=ref("tools_back"), shaft=ref("shaft_0"))],
        on_tracking_problem=pb.ON_TRACKING_PROBLEM_REJECT,
      )
    )
    self.assertEqual(picked.items[0].tool.attributes.name, "tools_back")
    await self.succeed(picked.operation)
    on_shaft = await self.tool_on("shaft_0")
    assert on_shaft is not None
    self.assertEqual(on_shaft.attributes.name, "tools_back")
    dropped = await self.commands.begin_tool_drop(
      spb.BeginToolDropRequest(
        frame=ref(ROOT, ""), items=[spb.ToolDropItem(shaft=ref("shaft_0"), origin=True)]
      )
    )
    await self.succeed(dropped.operation)
    parked = [n.attributes.name for n in await self.children(ref("tools"))]
    self.assertEqual(parked, ["tools_front", "tools_back"])
    self.assertEqual(
      to_coordinate((await self.node(ref("tools_back"))).attributes.location),
      Coordinate(29.75, 34.5, 1.0),
    )

  async def test_context_shafts_say_what_they_carry(self):
    first = await self.pick_up(["A1"], [0])
    await self.succeed(first.operation)
    response = await self.commands.begin_tool_pickup(
      spb.BeginToolPickupRequest(
        frame=ref(ROOT, ""),
        items=[spb.ToolPickupItem(source=item("tips", "B1"), shaft=ref("shaft_1"))],
        context_shafts=[ref("shaft_0"), ref("shaft_2")],
      )
    )
    self.assertTrue(response.context_shafts[0].HasField("tool"))
    self.assertFalse(response.context_shafts[1].HasField("tool"))


class LiquidTests(ServerTestCase):
  async def asyncSetUp(self) -> None:
    await super().asyncSetUp()
    response = await self.pick_up(["A1", "B1"], [0, 1])
    await self.succeed(response.operation)
    await self.set_volume(item("plate", "A1"), 200)
    await self.set_volume(item("plate", "B1"), 50)

  async def transfer(
    self,
    direction: int,
    wells: List[str],
    volumes: List[float],
    policy: int = pb.ON_TRACKING_PROBLEM_RECORD,
  ) -> spb.BeginLiquidTransferResponse:
    return await self.commands.begin_liquid_transfer(
      spb.BeginLiquidTransferRequest(
        frame=ref(ROOT, ""),
        direction=direction,  # type: ignore[arg-type]
        items=[
          spb.TransferItem(container=item("plate", well), shaft=ref(f"shaft_{c}"), volume=v)
          for c, (well, v) in enumerate(zip(wells, volumes))
        ],
        on_tracking_problem=policy,  # type: ignore[arg-type]
      )
    )

  async def test_staged_liquid_is_pending_until_resolved(self):
    response = await self.transfer(spb.TRANSFER_DIRECTION_ASPIRATE, ["A1", "B1"], [100, 20])
    self.assertEqual(list(response.issues), [])
    # As it stood before this command: what the driver plans the aspiration from.
    self.assertAlmostEqual(response.items[0].container.volume.pending_volume, 200)
    self.assertGreater(
      response.items[0].container.top.z, response.items[0].container.cavity_bottom.z
    )
    self.assertAlmostEqual(await self.volume(item("plate", "A1")), 100)
    self.assertAlmostEqual(await self.volume(item("plate", "A1"), pb.VIEW_COMMITTED), 200)
    await self.resolve(
      response.operation,
      spb.ItemOutcome(item=0, succeeded=True),
      spb.ItemOutcome(item=1, succeeded=False, volume=5),
    )
    a1 = (await self.node(item("plate", "A1"))).state.volume
    b1 = (await self.node(item("plate", "B1"))).state.volume
    self.assertAlmostEqual(a1.volume, 100)
    self.assertAlmostEqual(a1.pending_volume, 100)
    self.assertAlmostEqual(b1.volume, 45)
    self.assertAlmostEqual(b1.pending_volume, 45)
    tip = await self.tool_on("shaft_1")
    assert tip is not None
    self.assertAlmostEqual(tip.state.volume.volume, 5)

  async def test_too_little_liquid_is_an_issue_or_a_refusal(self):
    recorded = await self.transfer(spb.TRANSFER_DIRECTION_ASPIRATE, ["B1"], [80])
    self.assertEqual([i.problem for i in recorded.issues], [pb.PROBLEM_TOO_LITTLE_LIQUID])
    with self.assertRaises(ServerError) as refused:
      await self.transfer(
        spb.TRANSFER_DIRECTION_ASPIRATE, ["B1"], [80], pb.ON_TRACKING_PROBLEM_REJECT
      )
    self.assertEqual(refused.exception.problem, pb.PROBLEM_TOO_LITTLE_LIQUID)

  async def test_dispense_fills_the_container(self):
    aspirated = await self.transfer(spb.TRANSFER_DIRECTION_ASPIRATE, ["A1"], [100])
    await self.succeed(aspirated.operation)
    dispensed = await self.transfer(spb.TRANSFER_DIRECTION_DISPENSE, ["B1"], [60])
    await self.succeed(dispensed.operation)
    self.assertAlmostEqual(await self.volume(item("plate", "B1")), 110)
    tip = await self.tool_on("shaft_0")
    assert tip is not None
    self.assertAlmostEqual(tip.state.volume.volume, 40)

  async def test_measurements_set_what_is_there_under_what_is_pending(self):
    response = await self.transfer(spb.TRANSFER_DIRECTION_ASPIRATE, ["A1"], [100])
    measured = await self.commands.record_measurements(
      spb.RecordMeasurementsRequest(
        measurements=[spb.Measurement(resource=item("plate", "A1"), liquid_height=5.0)]
      )
    )
    result = measured.results[0]
    self.assertAlmostEqual(result.previous_volume, 200)
    self.assertGreater(result.volume, 150)
    await self.succeed(response.operation)
    self.assertAlmostEqual(await self.volume(item("plate", "A1")), result.volume - 100)

  async def test_heights_and_volumes(self):
    converted = await self.commands.convert_height_volume(
      spb.ConvertHeightVolumeRequest(
        queries=[
          spb.HeightVolumeQuery(container=item("plate", "A1"), height=0.45),
          spb.HeightVolumeQuery(container=item("plate", "A1"), volume=100),
          spb.HeightVolumeQuery(container=item("plate", "A1"), height=500),
        ]
      )
    )
    self.assertAlmostEqual(converted.results[0].volume, 20)
    self.assertAlmostEqual(converted.results[1].height, 3.22)
    self.assertEqual(converted.results[2].issue.problem, pb.PROBLEM_OUTSIDE_HEIGHT_VOLUME_DATA)

  async def test_a_volume_it_cannot_hold_is_an_issue_or_a_refusal(self):
    def update(volume: float, policy: int) -> spb.UpdateResourcesRequest:
      return spb.UpdateResourcesRequest(
        updates=[
          spb.ResourceUpdate(volume=pb.SetVolume(resource=item("plate", "B1"), volume=volume))
        ],
        on_tracking_problem=policy,  # type: ignore[arg-type]
      )

    recorded = await self.commands.update_resources(update(400, pb.ON_TRACKING_PROBLEM_RECORD))
    self.assertEqual([i.problem for i in recorded.issues], [pb.PROBLEM_TOO_LITTLE_VOLUME])
    self.assertAlmostEqual(await self.volume(item("plate", "B1")), 400)
    for volume, problem in (
      (500, pb.PROBLEM_TOO_LITTLE_VOLUME),
      (-1, pb.PROBLEM_TOO_LITTLE_LIQUID),
    ):
      with self.assertRaises(ServerError) as refused:
        await self.commands.update_resources(update(volume, pb.ON_TRACKING_PROBLEM_REJECT))
      self.assertEqual(refused.exception.problem, problem)
    self.assertAlmostEqual(await self.volume(item("plate", "B1")), 400)
    fits = await self.commands.update_resources(update(360, pb.ON_TRACKING_PROBLEM_REJECT))
    self.assertEqual(list(fits.issues), [])

  async def test_liquid_added_past_what_it_holds_is_an_issue_or_a_refusal(self):
    def add(volume: float, policy: int) -> ppb.ApplyChangesRequest:
      change = ppb.Change(add_liquid=ppb.AddLiquid(resource=item("plate", "B1"), volume=volume))
      return ppb.ApplyChangesRequest(
        groups=[ppb.ChangeGroup(changes=[change])],
        on_tracking_problem=policy,  # type: ignore[arg-type]
      )

    with self.assertRaises(ServerError) as refused:
      await self.server.primitives.apply_changes(add(400, pb.ON_TRACKING_PROBLEM_REJECT))
    self.assertEqual(refused.exception.problem, pb.PROBLEM_TOO_LITTLE_VOLUME)
    recorded = await self.server.primitives.apply_changes(add(400, pb.ON_TRACKING_PROBLEM_RECORD))
    self.assertEqual([i.problem for i in recorded.issues], [pb.PROBLEM_TOO_LITTLE_VOLUME])


class PlaceTests(ServerTestCase):
  async def test_a_plate_moves_between_sites(self):
    picked = await self.commands.begin_resource_pickup(
      spb.BeginResourcePickupRequest(
        frame=ref(ROOT, ""),
        resource=ref("plate"),
        carrier=ref("x_arm"),
        grip=spb.Grip(distance_from_top=5),
      )
    )
    self.assertAlmostEqual(picked.grip_height, 14.2 - 5)
    await self.succeed(picked.operation)
    self.assertEqual((await self.node(ref("plate"))).parent_id, (await self.node(ref("x_arm"))).id)
    placed = await self.commands.begin_resource_place(
      spb.BeginResourcePlaceRequest(
        frame=ref(ROOT, ""),
        resource=ref("plate"),
        destination_resource=ref("plate_carrier-4"),
        grip=spb.Grip(distance_from_top=5),
      )
    )
    await self.succeed(placed.operation)
    plate, site = await self.node(ref("plate")), await self.node(ref("plate_carrier-4"))
    self.assertEqual(plate.parent_id, site.id)
    self.assertAlmostEqual(plate.attributes.location.z, -3.03)
    self.assertEqual(
      placed.place_location.y,
      (
        await self.commands.resolve_geometry(
          spb.ResolveGeometryRequest(
            queries=[spb.GeometryQuery(resource=ref("plate"), frame=ref(ROOT, ""))]
          )
        )
      )
      .results[0]
      .locations[0]
      .y,
    )

  async def test_a_rolled_back_place_leaves_it_where_it_was(self):
    before = await self.node(ref("plate"))
    placed = await self.commands.begin_resource_place(
      spb.BeginResourcePlaceRequest(
        frame=ref(ROOT, ""),
        resource=ref("plate"),
        destination_resource=ref("plate_carrier-4"),
        rotation_applied_by_move=180,
        grip=spb.Grip(distance_from_top=5),
      )
    )
    self.assertAlmostEqual(placed.rotation_in_destination.z, 180)
    await self.resolve(placed.operation, spb.ItemOutcome(item=0, succeeded=False))
    after = await self.node(ref("plate"))
    self.assertEqual(before.parent_id, after.parent_id)
    self.assertEqual(before.attributes, after.attributes)

  async def test_an_occupied_destination_refuses(self):
    with self.assertRaises(ServerError) as refused:
      await self.commands.begin_resource_place(
        spb.BeginResourcePlaceRequest(
          frame=ref(ROOT, ""),
          resource=ref("plate"),
          destination_resource=ref("plate_carrier-1"),
          grip=spb.Grip(distance_from_top=5),
        )
      )
    self.assertEqual(refused.exception.problem, pb.PROBLEM_CANNOT_DROP_HERE)

  async def test_a_location_inside_something_refuses(self):
    inside = await self.commands.resolve_geometry(
      spb.ResolveGeometryRequest(
        queries=[
          spb.GeometryQuery(
            resource=ref("other_plate"),
            frame=ref(ROOT, ""),
            anchors=[pb.Anchor(x=pb.ANCHOR_X_CENTER, y=pb.ANCHOR_Y_CENTER, z=pb.ANCHOR_Z_CENTER)],
          )
        ]
      )
    )
    with self.assertRaises(ServerError):
      await self.commands.begin_resource_place(
        spb.BeginResourcePlaceRequest(
          frame=ref(ROOT, ""),
          resource=ref("plate"),
          destination_location=inside.results[0].locations[0],
          grip=spb.Grip(distance_from_top=5),
        )
      )
    found = await self.commands.find_resources_at(
      spb.FindResourcesAtRequest(frame=ref(ROOT, ""), location=inside.results[0].locations[0])
    )
    self.assertEqual(found.nodes[0].attributes.name, "other_plate")


class DeviceTests(ServerTestCase):
  async def test_a_channel_reports_where_its_reference_point_is(self):
    reference = pb.Vec3(x=4.5, y=4.5, z=-10)
    await self.commands.record_device_positions(
      spb.RecordDevicePositionsRequest(
        positions=[
          spb.DevicePosition(
            resource=ref("channel_0"),
            frame=ref(ROOT, ""),
            location=pb.PartialVec3(y=300.0, z=200.0),
            reference_point=reference,
          )
        ]
      )
    )
    where = await self.commands.resolve_geometry(
      spb.ResolveGeometryRequest(
        queries=[spb.GeometryQuery(resource=ref("channel_0"), frame=ref(ROOT, ""))]
      )
    )
    self.assertAlmostEqual(where.results[0].locations[0].y + reference.y, 300.0)
    self.assertAlmostEqual(where.results[0].locations[0].z + reference.z, 200.0)

  async def test_sensed_tools_the_model_lacks_are_issues(self):
    measured = await self.commands.record_measurements(
      spb.RecordMeasurementsRequest(
        measurements=[spb.Measurement(resource=ref("shaft_0"), tool_present=True)]
      )
    )
    self.assertEqual(measured.results[0].issue.problem, pb.PROBLEM_NO_TIP)


class OperationTests(ServerTestCase):
  async def test_unresolved_operations_are_listed_until_resolved(self):
    response = await self.pick_up(["A1", "B1"], [0, 1])
    listed = await self.commands.list_operations(
      spb.ListOperationsRequest(root=ref(ROOT, ""), unresolved_only=True)
    )
    self.assertEqual([op.id for op in listed.operations], [response.operation.id])
    await self.resolve(response.operation, spb.ItemOutcome(item=0, succeeded=True))
    still = await self.commands.get_operation(
      spb.GetOperationRequest(operation_id=response.operation.id)
    )
    self.assertEqual(
      [i.state for i in still.items], [pb.ITEM_STATE_COMMITTED, pb.ITEM_STATE_PENDING]
    )
    await self.resolve(response.operation, spb.ItemOutcome(item=1, succeeded=False))
    listed = await self.commands.list_operations(
      spb.ListOperationsRequest(root=ref(ROOT, ""), unresolved_only=True)
    )
    self.assertEqual(list(listed.operations), [])
    with self.assertRaises(ServerError):
      await self.resolve(response.operation, spb.ItemOutcome(item=1, succeeded=True))


class WatchTests(ServerTestCase):
  async def test_watch_sees_changes_after_a_revision(self):
    start = (await self.commands.get_server_info(spb.GetServerInfoRequest())).revision
    seen: List[spb.ResourceEvent] = []

    async def watch() -> None:
      async for event in self.commands.watch(
        spb.WatchRequest(roots=[ref(ROOT, "")], after_revision=start)
      ):
        seen.append(event)
        if event.WhichOneof("event") == "operation":
          return

    watching = asyncio.create_task(watch())
    await asyncio.sleep(0.05)
    await self.pick_up(["A1"], [0])
    await asyncio.wait_for(watching, timeout=10)
    kinds = {event.WhichOneof("event") for event in seen}
    self.assertIn("updated", kinds)
    self.assertIn("operation", kinds)
    self.assertTrue(all(event.revision > start for event in seen))
    tip = next(e for e in seen if e.updated.attributes.kind == pb.RESOURCE_KIND_TIP)
    self.assertIn(spb.CHANGE_ASPECT_PLACEMENT, tip.changed)
    spot = next(e for e in seen if e.updated.attributes.name == "tips_tipspot_A1")
    self.assertEqual(list(spot.changed), [spb.CHANGE_ASPECT_CHILDREN])
    self.assertEqual(len({event.revision for event in seen if event.HasField("updated")}), 1)

  async def test_a_watcher_is_told_what_an_update_changed(self):
    start = (await self.commands.get_server_info(spb.GetServerInfoRequest())).revision
    await self.commands.update_resources(
      spb.UpdateResourcesRequest(
        updates=[spb.ResourceUpdate(volume=pb.SetVolume(resource=ref("trough"), volume=500))]
      )
    )
    events = []
    async for event in self.commands.watch(
      spb.WatchRequest(roots=[ref(ROOT, "")], after_revision=start)
    ):
      events.append(event)
      break
    self.assertEqual(events[0].updated.attributes.name, "trough")
    self.assertEqual(list(events[0].changed), [spb.CHANGE_ASPECT_STATE])


class ModelTests(ServerTestCase):
  """Resources made from a model refer to it, and follow it when it changes."""

  async def put(self, model: pb.ResourceModel) -> None:
    await self.commands.put_model(spb.PutModelRequest(model=model))

  async def create(self, model: str, name: str) -> None:
    await self.commands.create_resources(
      spb.CreateResourcesRequest(resources=[spb.CreateResource(model=model, name=name)])
    )

  async def test_resources_of_a_model_follow_it_when_it_changes(self):
    model = library.get_model("cor_96_wellplate_360uL_Fb")
    model.name = "plate_96"
    await self.put(model)
    await self.create("plate_96", "a")
    await self.create("plate_96", "b")
    start = (await self.commands.get_server_info(spb.GetServerInfoRequest())).revision
    grid = model.definition.attributes.itemized.grid
    grid.item.attributes.container.max_volume = 500
    grid.offset.z += 1
    await self.put(model)
    for plate in ("a", "b"):
      well = await self.node(ref(f"{plate}_well_C3", plate))
      self.assertEqual(well.attributes.container.max_volume, 500)
      self.assertAlmostEqual(well.attributes.location.z, grid.offset.z)
    events = []
    async for event in self.commands.watch(spb.WatchRequest(after_revision=start)):
      events.append(event)
      if len(events) == 2 * 96:
        break
    self.assertEqual({e.updated.attributes.kind for e in events}, {pb.RESOURCE_KIND_WELL})
    self.assertEqual(
      {tuple(e.changed) for e in events},
      {(spb.CHANGE_ASPECT_PLACEMENT, spb.CHANGE_ASPECT_ATTRIBUTES)},
    )

  async def test_a_resource_moved_off_its_model_place_stays_there(self):
    model = library.get_model("cor_96_wellplate_360uL_Fb")
    model.name = "plate_96"
    await self.put(model)
    await self.create("plate_96", "a")
    await self.commands.update_resources(
      spb.UpdateResourcesRequest(
        updates=[
          spb.ResourceUpdate(
            rotation=pb.RotationChange(resource=ref("a_well_A1", "a"), by=pb.Rotation(z=90))
          )
        ]
      )
    )
    before = (await self.node(ref("a_well_A1", "a"))).attributes.location
    model.definition.attributes.itemized.grid.offset.z += 1
    await self.put(model)
    self.assertEqual((await self.node(ref("a_well_A1", "a"))).attributes.location, before)
    moved = await self.node(ref("a_well_B1", "a"))
    self.assertAlmostEqual(moved.attributes.location.z, before.z + 1)

  async def test_a_model_change_of_kind_is_refused(self):
    model = library.get_model("cor_96_wellplate_360uL_Fb")
    model.name = "plate_96"
    await self.put(model)
    await self.create("plate_96", "a")
    model.definition.attributes.itemized.grid.item.attributes.kind = pb.RESOURCE_KIND_TUBE
    with self.assertRaises(ServerError) as refused:
      await self.put(model)
    self.assertEqual(refused.exception.problem, pb.PROBLEM_WRONG_KIND)
    well = await self.node(ref("a_well_A1", "a"))
    self.assertEqual(well.attributes.kind, pb.RESOURCE_KIND_WELL)

  async def test_a_spec_of_another_kind_is_refused(self):
    definition = build.resource(
      "odd", Coordinate(10, 10, 10), kind=pb.RESOURCE_KIND_LID, plate=pb.PlateSpec()
    )
    with self.assertRaises(ServerError) as refused:
      await self.commands.create_resources(
        spb.CreateResourcesRequest(resources=[spb.CreateResource(definition=definition)])
      )
    self.assertEqual(refused.exception.problem, pb.PROBLEM_WRONG_KIND)

  async def test_get_or_create_finds_what_is_there(self):
    made = await self.server.get_or_create("cor_96_wellplate_360uL_Fb", "a")
    again = await self.server.get_or_create("cor_96_wellplate_360uL_Fb", "a")
    self.assertEqual(again, made)
    with self.assertRaises(ServerError) as refused:
      await self.server.get_or_create("hamilton_96_tiprack_300uL_filter", "a")
    self.assertEqual(refused.exception.problem, pb.PROBLEM_NAME_CONFLICT)
    carrier = await self.server.get("plate_carrier", ROOT)
    assert isinstance(carrier, handles.Carrier)
    held = await self.server.get_or_create("cor_96_wellplate_360uL_Fb", "plate", parent=carrier[0])
    self.assertEqual(held, await self.server.get("plate", ROOT))

  async def test_a_definition_read_back_gives_its_grid_once(self):
    made = await self.server.create("cor_96_wellplate_360uL_Fb", "a")
    definition = await made.definition()
    grid = definition.attributes.itemized.grid
    self.assertEqual(grid.item.attributes.name, "a_well_{identifier}")
    self.assertEqual(len(definition.children), 0)
    await self.server.create(definition, "b")
    self.assertEqual(len((await self.node(ref("b", ""))).child_ids), 96)


class LibraryTests(ServerTestCase):
  """PLR's stores start with PLR's labware models put in."""

  async def test_plr_models_are_listed_and_made_by_name(self):
    first = await self.commands.list_models(spb.ListModelsRequest(page_size=3))
    self.assertEqual([m.name for m in first.models], library.model_names()[:3])
    self.assertTrue(first.next_page_token)
    await self.server.create("hamilton_96_tiprack_300uL_filter", "more_tips")
    node = await self.node(ref("more_tips", ""))
    made_as = library.get_model("hamilton_96_tiprack_300uL_filter").definition.attributes
    self.assertEqual(node.attributes.model, made_as.model)

  async def test_a_deprecated_name_makes_its_model(self):
    _, factory = library._factories(deprecated=True)[0]
    await self.server.create(factory.__name__, "deprecated")
    made_as = library.get_model(factory.__name__).definition.attributes
    self.assertEqual((await self.node(ref("deprecated", ""))).attributes.model, made_as.model)


if __name__ == "__main__":
  unittest.main()


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
  async def test_a_connection_sends_its_headers_with_every_call(self):
    seen: List[Optional[str]] = []

    class Recording(MemoryStore):
      async def get_server_info(
        self, request: spb.GetServerInfoRequest, ctx: RequestContext
      ) -> spb.ServerInfo:
        seen.append(ctx.request_headers.get("authorization"))
        return await super().get_server_info(request, ctx)

    store = Recording()
    http = Client(transport=ASGITransport(asgi_app(store)))
    server = ResourceServer("http://in-process", http, store, {"authorization": "Bearer t"})
    await server.commands.get_server_info(spb.GetServerInfoRequest())
    await server.commands.get_server_info(spb.GetServerInfoRequest())
    self.assertEqual(seen, ["Bearer t", "Bearer t"])
    await server.close()
