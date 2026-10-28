"""The store's rules on what goes where: trashes keep what they are given until cleared, stacks
growing in z change only at the top, single-child parents hold one child, and names."""

from __future__ import annotations

from typing import List

from pylabrobot.resources import Coordinate
from pylabrobot.resources.petri_dish import PetriDish, PetriDishHolder
from pylabrobot.resources.plate_adapter import PlateAdapter
from pylabrobot.resources.resource_stack import ResourceStack
from pylabrobot.resources.server import build, handles
from pylabrobot.resources.server.definitions import definition_from_resource
from pylabrobot.resources.server.errors import ServerError
from pylabrobot.resources.server.tests.scenes import stacking_plate
from pylabrobot.resources.server.tests.server_tests import ROOT, ServerTestCase, item, ref
from pylabrobot.resources.server.v1 import server_pb2 as spb
from pylabrobot.resources.server.v1 import types_pb2 as pb


class TrashTests(ServerTestCase):
  async def drop_into_trash(self, channel: int) -> spb.BeginToolDropResponse:
    drop = await self.commands.begin_tool_drop(
      spb.BeginToolDropRequest(
        frame=ref(ROOT, ""),
        items=[spb.ToolDropItem(shaft=ref(f"shaft_{channel}"), target=ref("trash"))],
      )
    )
    await self.succeed(drop.operation)
    return drop

  async def test_tip_spots_track_tips_by_default(self):
    spot = await self.node(item("tips", "A1"))
    self.assertTrue(spot.state.tip_tracking_enabled)
    built = await self.server.create("hamilton_96_tiprack_300uL", "rack")
    assert isinstance(built, handles.TipRack)
    made = await built.get_tip_spot("A1").node()
    self.assertTrue(made.state.tip_tracking_enabled)
    picked = await self.pick_up(["A1"], [0])
    await self.succeed(picked.operation)
    self.assertEqual(list((await self.node(item("tips", "A1"))).child_ids), [])

  async def test_a_dropped_tip_stays_in_the_trash_until_it_is_cleared(self):
    picked = await self.pick_up(["A1", "B1"], [0, 1])
    await self.succeed(picked.operation)
    tips = [picked.items[0].tool.id, picked.items[1].tool.id]
    await self.drop_into_trash(0)
    await self.drop_into_trash(1)
    trash = await self.node(ref("trash"))
    self.assertEqual(list(trash.child_ids), tips)
    tip = await self.node(pb.ResourceRef(id=tips[0]))
    self.assertEqual(tip.parent_id, trash.id)
    # Centred on the trash's floor.
    self.assertAlmostEqual(
      tip.attributes.location.x, (trash.attributes.size.x - tip.attributes.size.x) / 2
    )
    self.assertEqual(tip.attributes.location.z, 0)

    start = (await self.commands.get_server_info(spb.GetServerInfoRequest())).revision
    held = await self.server.get("trash", root=ROOT)
    assert isinstance(held, handles.Trash)
    await held.clear()
    self.assertEqual(list((await self.node(ref("trash"))).child_ids), [])
    for tip_id in tips:
      with self.assertRaises(ServerError):
        await self.node(pb.ResourceRef(id=tip_id))
    deleted: List[bytes] = []
    async for event in self.commands.watch(
      spb.WatchRequest(roots=[ref(ROOT, "")], after_revision=start)
    ):
      if event.WhichOneof("event") == "deleted":
        deleted.append(event.deleted)
      if len(deleted) == len(tips):
        break
    self.assertEqual(sorted(deleted), sorted(tips))

  async def test_only_a_trash_is_cleared(self):
    with self.assertRaises(ServerError) as refused:
      await self.commands.update_resources(
        spb.UpdateResourcesRequest(
          updates=[spb.ResourceUpdate(clear_trash=pb.ClearTrash(trash=ref("plate")))]
        )
      )
    self.assertEqual(refused.exception.problem, pb.PROBLEM_WRONG_KIND)

  async def test_a_plate_placed_into_the_trash_stays_there(self):
    plate = await self.node(ref("plate"))
    placed = await self.commands.begin_resource_place(
      spb.BeginResourcePlaceRequest(
        frame=ref(ROOT, ""),
        resource=ref("plate"),
        destination_resource=ref("trash"),
        grip=spb.Grip(distance_from_top=5),
      )
    )
    await self.succeed(placed.operation)
    kept = await self.node(ref("plate"))
    self.assertEqual(kept.id, plate.id)
    self.assertEqual(kept.parent_id, (await self.node(ref("trash"))).id)

  async def test_a_name_held_in_the_trash_says_so_when_it_clashes(self):
    picked = await self.pick_up(["A1"], [0])
    await self.succeed(picked.operation)
    await self.drop_into_trash(0)
    tip = build.resource("tips_tipspot_A1#0", Coordinate(5, 5, 50))
    with self.assertRaises(ServerError) as refused:
      await self.commands.create_resources(
        spb.CreateResourcesRequest(
          resources=[
            spb.CreateResource(
              definition=tip,
              assign=pb.AssignChild(parent=ref(ROOT, ""), location=pb.Vec3(x=0, y=0, z=300)),
            )
          ]
        )
      )
    self.assertEqual(refused.exception.problem, pb.PROBLEM_NAME_CONFLICT)
    self.assertIn("in trash 'trash'", str(refused.exception))

  async def test_a_tip_dropped_into_an_untracked_spot_leaves_the_store(self):
    picked = await self.pick_up(["A1"], [0])
    await self.succeed(picked.operation)
    await self.commands.update_resources(
      spb.UpdateResourcesRequest(
        updates=[
          spb.ResourceUpdate(tracking=pb.SetTracking(scope=item("tips", "B1"), tip_tracking=False))
        ]
      )
    )
    drop = await self.commands.begin_tool_drop(
      spb.BeginToolDropRequest(
        frame=ref(ROOT, ""),
        items=[spb.ToolDropItem(shaft=ref("shaft_0"), target=item("tips", "B1"))],
      )
    )
    await self.succeed(drop.operation)
    with self.assertRaises(ServerError):
      await self.node(pb.ResourceRef(id=picked.items[0].tool.id))


class StackRuleTests(ServerTestCase):
  async def asyncSetUp(self) -> None:
    await super().asyncSetUp()
    await self.commands.delete_resources(spb.DeleteResourcesRequest(resources=[ref("other_plate")]))
    await self.commands.create_resources(
      spb.CreateResourcesRequest(
        resources=[
          spb.CreateResource(
            definition=definition_from_resource(ResourceStack("stack", "z")),
            assign=pb.AssignChild(parent=ref("plate_carrier-1")),
          )
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

  async def assert_not_top(self, refused: ServerError) -> None:
    self.assertEqual(refused.problem, pb.PROBLEM_CANNOT_DROP_HERE)
    self.assertIn("not the top of stack 'stack'", str(refused))

  async def test_only_the_top_item_is_assigned_elsewhere(self):
    with self.assertRaises(ServerError) as refused:
      await self.commands.assign_resources(
        spb.AssignResourcesRequest(
          assignments=[pb.AssignChild(child=ref("s1"), parent=ref("plate_carrier-2"))]
        )
      )
    await self.assert_not_top(refused.exception)
    await self.commands.assign_resources(
      spb.AssignResourcesRequest(
        assignments=[pb.AssignChild(child=ref("s2"), parent=ref("plate_carrier-2"))]
      )
    )

  async def test_only_the_top_item_is_deleted(self):
    with self.assertRaises(ServerError) as refused:
      await self.commands.delete_resources(spb.DeleteResourcesRequest(resources=[ref("s1")]))
    await self.assert_not_top(refused.exception)
    await self.commands.delete_resources(spb.DeleteResourcesRequest(resources=[ref("s2")]))
    await self.commands.delete_resources(spb.DeleteResourcesRequest(resources=[ref("s1")]))

  async def test_only_the_top_item_is_picked_up(self):
    with self.assertRaises(ServerError) as refused:
      await self.commands.begin_resource_pickup(
        spb.BeginResourcePickupRequest(
          frame=ref(ROOT, ""),
          resource=ref("s1"),
          carrier=ref("x_arm"),
          grip=spb.Grip(distance_from_top=5),
        )
      )
    await self.assert_not_top(refused.exception)
    picked = await self.commands.begin_resource_pickup(
      spb.BeginResourcePickupRequest(
        frame=ref(ROOT, ""),
        resource=ref("s2"),
        carrier=ref("x_arm"),
        grip=spb.Grip(distance_from_top=5),
      )
    )
    await self.succeed(picked.operation)

  async def test_only_the_top_item_is_assigned_onto(self):
    marker = build.resource("marker", Coordinate(5, 5, 5))
    for parent, refused_here in (("s1", True), ("s2", False)):
      request = spb.CreateResourcesRequest(
        resources=[
          spb.CreateResource(
            definition=marker,
            assign=pb.AssignChild(parent=ref(parent), location=pb.Vec3(x=1, y=1, z=1)),
          )
        ]
      )
      if refused_here:
        with self.assertRaises(ServerError) as refused:
          await self.commands.create_resources(request)
        await self.assert_not_top(refused.exception)
      else:
        await self.commands.create_resources(request)


class SingleChildRuleTests(ServerTestCase):
  async def create(self, resource: pb.ResourceDefinition, parent: pb.ResourceRef) -> None:
    await self.commands.create_resources(
      spb.CreateResourcesRequest(
        resources=[
          spb.CreateResource(
            definition=resource,
            assign=pb.AssignChild(parent=parent, location=pb.Vec3(x=0, y=0, z=0)),
          )
        ]
      )
    )

  async def test_a_carrier_takes_only_holders_and_carriers(self):
    with self.assertRaises(ServerError) as refused:
      await self.create(definition_from_resource(stacking_plate("loose")), ref("plate_carrier"))
    self.assertEqual(refused.exception.problem, pb.PROBLEM_WRONG_KIND)
    self.assertIn("takes only resource holders and carriers", str(refused.exception))
    holder = build.resource("site", Coordinate(10, 10, 1), kind=pb.RESOURCE_KIND_RESOURCE_HOLDER)
    await self.create(holder, ref("plate_carrier"))

  async def test_a_plate_adapter_holds_one_plate(self):
    adapter = PlateAdapter(
      "adapter",
      size_x=127.76,
      size_y=85.48,
      size_z=10,
      dx=10,
      dy=8,
      dz=2,
      adapter_hole_size_x=8,
      adapter_hole_size_y=8,
    )
    await self.create(definition_from_resource(adapter), ref(ROOT, ""))
    await self.create(definition_from_resource(stacking_plate("first")), ref("adapter"))
    with self.assertRaises(ServerError) as refused:
      await self.create(definition_from_resource(stacking_plate("second")), ref("adapter"))
    self.assertEqual(refused.exception.problem, pb.PROBLEM_CANNOT_DROP_HERE)
    self.assertIn("already holds 'first'", str(refused.exception))

  async def test_a_petri_dish_holder_holds_one_dish(self):
    await self.create(definition_from_resource(PetriDishHolder("dishes")), ref(ROOT, ""))
    with self.assertRaises(ServerError) as refused:
      await self.create(definition_from_resource(stacking_plate("plate_2")), ref("dishes"))
    self.assertEqual(refused.exception.problem, pb.PROBLEM_WRONG_KIND)
    await self.create(definition_from_resource(PetriDish("dish_1", 90, 15)), ref("dishes"))
    with self.assertRaises(ServerError) as refused:
      await self.create(definition_from_resource(PetriDish("dish_2", 90, 15)), ref("dishes"))
    self.assertEqual(refused.exception.problem, pb.PROBLEM_CANNOT_DROP_HERE)


class RootNameTests(ServerTestCase):
  async def test_a_resource_is_created_into_a_parent_beside_a_root_of_its_name(self):
    await self.server.create(build.resource("loose", Coordinate(5, 5, 5)), "loose")
    deck = await self.server.get(ROOT)
    made = await self.server.create(
      build.resource("loose", Coordinate(5, 5, 5)),
      "loose",
      parent=deck,
      location=Coordinate(0, 0, 300),
    )
    self.assertEqual((await made.get_root()).name, ROOT)

  async def test_unassigning_onto_a_root_of_its_name_says_why(self):
    await self.server.create(build.resource("plate", Coordinate(5, 5, 5)), "plate")
    with self.assertRaises(ServerError) as refused:
      await self.commands.unassign_resources(spb.UnassignResourcesRequest(resources=[ref("plate")]))
    self.assertEqual(refused.exception.problem, pb.PROBLEM_NAME_CONFLICT)
    self.assertIn("root names are unique in the store", str(refused.exception))


class DisplayModelTests(ServerTestCase):
  async def test_a_resource_is_drawn_as_another_model_and_back(self):
    deck = await self.server.get(ROOT)
    model = (await deck.attributes()).model
    start = (await self.commands.get_server_info(spb.GetServerInfoRequest())).revision
    await deck.set_display_model("starlet_autoload_tray_belt_frame")
    attributes = await deck.attributes()
    self.assertEqual(attributes.display_model, "starlet_autoload_tray_belt_frame")
    self.assertEqual(attributes.model, model)
    async for event in self.commands.watch(
      spb.WatchRequest(roots=[ref(ROOT, "")], after_revision=start)
    ):
      self.assertEqual(list(event.changed), [spb.CHANGE_ASPECT_ATTRIBUTES])
      break
    await deck.set_display_model(None)
    self.assertEqual((await deck.attributes()).display_model, "")
