"""Resource inventories and confirmed transfers, without hardware connections."""

import asyncio
import json
import unittest

from pylabrobot.high_res.micro_serve import (
  HighResMicroServe,
  MicroServePlateDimensions,
  MicroServeStacker,
)
from pylabrobot.high_res.micro_serve.tests.micro_serve_tests import (
  DIMENSIONS,
  GEOMETRY,
  FakeSocket,
  status,
)
from pylabrobot.resources import Coordinate, Lid, Plate, Resource, ResourceStack


def plate(name: str) -> Plate:
  """Create a plate with known height and nesting pitch for inventory tests."""
  return Plate(name, size_x=127.76, size_y=85.48, size_z=11, stacking_z_height=10, ordered_items={})


class MicroServeResourceTests(unittest.TestCase):
  """Check resource ordering, geometry, serialization, and controller binding."""

  def test_stack_is_a_resource_stack_with_top_access_and_nesting(self) -> None:
    """Only the upper plate can leave; children and locations follow PLR conventions."""
    top, bottom = plate("top"), plate("bottom")
    stack = MicroServeStacker(index=5, resources=[top, bottom])
    self.assertIsInstance(stack, ResourceStack)
    self.assertEqual(stack.direction, "z")
    self.assertEqual(stack.children, [bottom, top])
    self.assertIs(stack.get_top_item(), top)
    self.assertEqual(top.location, Coordinate(0, 0, 10))
    self.assertEqual(stack.get_size_z(), 21)
    with self.assertRaises(ValueError):
      stack.unassign_child_resource(bottom)
    stack.assign_child_resource(top)
    self.assertEqual(len(stack.children), 2)
    stack.unassign_child_resource(top)
    self.assertIsNone(top.parent)
    self.assertIs(stack.get_top_item(), bottom)
    stack.unassign_child_resource(bottom)
    with self.assertRaises(ValueError):
      stack.get_top_item()
    with self.assertRaises(ValueError):
      stack.unassign_child_resource(bottom)

  def test_nonplates_are_rejected(self) -> None:
    """A resource tree cannot silently contain arbitrary labware in the stack."""
    stack = MicroServeStacker()
    resource = Resource("box", 1, 1, 1)
    with self.assertRaises(TypeError):
      stack.assign_child_resource(resource)
    with self.assertRaises(TypeError):
      stack.check_can_drop_resource_here(resource)
    self.assertIsNone(resource.parent)

  def test_serialized_inventory_restores_detached_and_can_bind(self) -> None:
    """JSON round trips plate identity, ordering, index, metadata, and placement, not IO."""
    device = HighResMicroServe("192.0.2.1", name="carousel")
    stack = device.stackers[5]
    stack.metadata["inventory_source"] = "operator"
    stack.assign_child_resource(plate("bottom"))
    stack.assign_child_resource(plate("top"))
    self.assertEqual(
      {key: stack.serialize()[key] for key in ("size_x", "size_y", "size_z")},
      {"size_x": 0, "size_y": 0, "size_z": 0},
    )
    root = Resource("deck", 1000, 1000, 0)
    root.assign_child_resource(stack, Coordinate(100, 200, 0))
    restored_root = Resource.deserialize(json.loads(json.dumps(root.serialize())))
    restored_stack = restored_root.children[0]
    self.assertIsInstance(restored_stack, MicroServeStacker)
    self.assertEqual(restored_stack.serialize(), stack.serialize())
    restored = [
      MicroServeStacker.deserialize(json.loads(json.dumps(s.serialize()))) for s in device.stackers
    ]
    self.assertTrue(all(isinstance(s, MicroServeStacker) for s in restored))
    rebound = HighResMicroServe("192.0.2.2", stackers=restored)
    self.assertEqual(rebound.stackers[5].index, 5)
    self.assertEqual(rebound.stackers[5].get_top_item().name, "top")
    self.assertEqual(rebound.stackers[5].get_size_z(), 21)
    self.assertEqual(rebound.stackers[5].metadata, {"inventory_source": "operator"})
    with self.assertRaises(ValueError):
      HighResMicroServe("192.0.2.3", stackers=rebound.stackers)

  def test_invalid_bindings_do_not_partially_attach(self) -> None:
    """Incorrect indices or duplicated names fail before any controller is attached."""
    stacks = [MicroServeStacker(index=i) for i in range(14)]
    with self.assertRaises(ValueError):
      HighResMicroServe("192.0.2.1", stackers=stacks[:-1])
    with self.assertRaises(ValueError):
      HighResMicroServe("192.0.2.1", stackers=list(reversed(stacks)))
    stacks[13] = MicroServeStacker(index=13, name=stacks[12].name)
    with self.assertRaises(ValueError):
      HighResMicroServe("192.0.2.1", stackers=stacks)
    stacks[13] = MicroServeStacker(index=13, name="last")
    HighResMicroServe("192.0.2.1", stackers=stacks)

  def test_plate_geometry_requires_known_pitch_and_support_thickness(self) -> None:
    """Plate height and pitch map to device units without guessing unknown geometry."""
    item = plate("plate")
    self.assertEqual(MicroServePlateDimensions.from_plate(item, thickness=10), GEOMETRY)
    item.stacking_z_height = None
    with self.assertRaises(ValueError):
      MicroServePlateDimensions.from_plate(item, thickness=10)
    self.assertEqual(
      MicroServePlateDimensions.from_plate(item, thickness=10, stack_height=10), GEOMETRY
    )
    item.assign_child_resource(Lid("lid", 127.76, 85.48, 4, nesting_z_height=2))
    with self.assertRaises(ValueError):
      MicroServePlateDimensions.from_plate(item, thickness=10)
    self.assertEqual(
      MicroServePlateDimensions.from_plate(item, thickness=10, stack_height=13),
      MicroServePlateDimensions(height=13, stack_height=13, thickness=10),
    )


class MicroServeInventoryTransferTests(unittest.IsolatedAsyncioTestCase):
  """Inventory changes follow explicit successful robot handoffs, not firmware motion."""

  async def asyncSetUp(self) -> None:
    """Create a scripted controller and a two-plate inventory."""
    self.device = HighResMicroServe("192.0.2.1")
    self.io = FakeSocket()
    self.device.io = self.io  # type: ignore[assignment]
    await self.device.setup()
    self.stack = self.device.stackers[0]
    self.bottom, self.top = plate("bottom"), plate("top")
    self.stack.assign_child_resource(self.bottom)
    self.stack.assign_child_resource(self.top)

  def script_prepare(self, direction: str) -> None:
    """Queue geometry confirmation and an acknowledged preparation."""
    self.io.add("status", status())
    self.io.add("setstackerdimensions 0 11000 10000 10000")
    self.io.add("dimstatus", *DIMENSIONS)
    self.io.add(f"{direction} 0")
    self.io.add("status", status(extended=True))

  async def test_detached_hardware_methods_fail_without_io(self) -> None:
    """Deserializing or constructing a resource does not provide hardware access."""
    with self.assertRaisesRegex(RuntimeError, "Attach this stacker"):
      await MicroServeStacker().request_plate_count()

  async def test_preparation_and_retraction_preserve_inventory(self) -> None:
    """Presenting and returning a plate does not imply an external pickup."""
    self.script_prepare("unload")
    await self.stack.prepare_for_unload(GEOMETRY)
    self.assertIs(self.stack.get_top_item(), self.top)
    self.io.add("status", status(extended=True))
    self.io.add("retract")
    self.io.add("status", status())
    await self.device.retract()
    self.assertEqual(self.stack.children, [self.bottom, self.top])
    with self.assertRaises(RuntimeError):
      await self.stack.confirm_plate_unloaded(self.top)

  async def test_confirmed_unload_removes_only_the_named_top_once(self) -> None:
    """Repeating a confirmation cannot accidentally consume the next plate."""
    self.script_prepare("unloadangle")
    await self.stack.prepare_for_unload_with_angle(GEOMETRY)
    writes = list(self.io.writes)
    await asyncio.gather(
      self.stack.confirm_plate_unloaded(self.top), self.stack.confirm_plate_unloaded(self.top)
    )
    self.assertEqual(self.stack.children, [self.bottom])
    self.assertIsNone(self.top.parent)
    with self.assertRaises(RuntimeError):
      await self.stack.confirm_plate_unloaded(self.bottom)
    self.assertEqual(self.io.writes, writes)

  async def test_robot_resource_assignment_is_preserved(self) -> None:
    """Confirmation accepts the robot's successful move without detaching its destination."""
    self.script_prepare("unload")
    await self.stack.prepare_for_unload(GEOMETRY)
    destination = Resource("destination", 200, 100, 20)
    destination.assign_child_resource(self.top, Coordinate.zero())
    await self.stack.confirm_plate_unloaded(self.top)
    self.assertIs(self.top.parent, destination)
    self.assertEqual(self.stack.children, [self.bottom])

  async def test_confirmed_load_adds_plate_once(self) -> None:
    """A successful placement becomes the top plate with the correct nesting height."""
    incoming = plate("incoming")
    self.script_prepare("load")
    await self.stack.prepare_for_load(GEOMETRY)
    self.assertEqual(len(self.stack.children), 2)
    await self.stack.confirm_plate_loaded(incoming)
    await self.stack.confirm_plate_loaded(incoming)
    self.assertIs(self.stack.get_top_item(), incoming)
    self.assertEqual(self.stack.get_size_z(), 31)
    with self.assertRaises(RuntimeError):
      await self.stack.confirm_plate_loaded(plate("another"))

  async def test_robot_load_assignment_is_not_duplicated(self) -> None:
    """PLR robot assignment can precede confirmation without adding a second child."""
    incoming = plate("incoming")
    self.script_prepare("load")
    await self.stack.prepare_for_load(GEOMETRY)
    self.stack.assign_child_resource(incoming)
    await self.stack.confirm_plate_loaded(incoming)
    self.assertEqual(len(self.stack.children), 3)

  async def test_repeated_preparation_keeps_the_original_inventory(self) -> None:
    """A repeated unload after pickup cannot change which plate was handed off."""
    self.script_prepare("unload")
    await self.stack.prepare_for_unload(GEOMETRY)
    await self.stack.confirm_plate_unloaded(self.top)
    self.io.add("status", status(extended=True))
    self.io.add("dimstatus", *DIMENSIONS)
    await self.stack.prepare_for_unload(GEOMETRY)
    await self.stack.confirm_plate_unloaded(self.top)
    self.assertEqual(self.stack.children, [self.bottom])
    self.assertEqual(self.io.writes.count(b"unload 0\n"), 1)

  async def test_next_handoff_can_transfer_another_plate(self) -> None:
    """Retraction and a fresh preparation establish a separate transfer."""
    self.script_prepare("unload")
    await self.stack.prepare_for_unload(GEOMETRY)
    await self.stack.confirm_plate_unloaded(self.top)
    self.io.add("status", status(extended=True))
    self.io.add("retract")
    self.io.add("status", status())
    await self.device.retract()
    self.script_prepare("unload")
    await self.stack.prepare_for_unload(GEOMETRY)
    await self.stack.confirm_plate_unloaded(self.bottom)
    self.assertEqual(self.stack.children, [])

  async def test_changed_inventory_after_confirmation_is_rejected(self) -> None:
    """A repeat does not silently repair a subsequent inventory mutation."""
    self.script_prepare("unload")
    await self.stack.prepare_for_unload(GEOMETRY)
    await self.stack.confirm_plate_unloaded(self.top)
    self.stack.assign_child_resource(self.top)
    with self.assertRaisesRegex(RuntimeError, "Inventory changed after confirmation"):
      await self.stack.confirm_plate_unloaded(self.top)
    self.assertIs(self.stack.get_top_item(), self.top)

  async def test_failed_robot_pickup_and_disconnect_leave_inventory_unchanged(self) -> None:
    """Without successful external pickup, ending the connection never removes a plate."""
    self.script_prepare("unload")
    await self.stack.prepare_for_unload(GEOMETRY)
    await self.device.stop()
    await self.device.setup()
    with self.assertRaises(RuntimeError):
      await self.stack.confirm_plate_unloaded(self.top)
    self.assertEqual(self.stack.children, [self.bottom, self.top])

  async def test_confirmation_rejects_wrong_plate_direction_and_stacker(self) -> None:
    """Only the owned handoff and the original top plate can be confirmed."""
    with self.assertRaises(RuntimeError):
      await self.stack.confirm_plate_unloaded(self.top)
    self.script_prepare("unload")
    await self.stack.prepare_for_unload(GEOMETRY)
    with self.assertRaises(ValueError):
      await self.stack.confirm_plate_unloaded(self.bottom)
    with self.assertRaises(RuntimeError):
      await self.stack.confirm_plate_loaded(plate("incoming"))
    with self.assertRaises(RuntimeError):
      await self.device.stackers[1].confirm_plate_unloaded(self.top)
    self.assertEqual(self.stack.children, [self.bottom, self.top])

  async def test_unexpected_inventory_change_requires_inspection(self) -> None:
    """Another inventory mutation cannot be mistaken for the expected transfer."""
    self.script_prepare("unload")
    await self.stack.prepare_for_unload(GEOMETRY)
    self.stack.assign_child_resource(plate("unexpected"))
    with self.assertRaisesRegex(RuntimeError, "Inventory changed"):
      await self.stack.confirm_plate_unloaded(self.top)
    self.assertEqual(len(self.stack.children), 3)

  async def test_timeout_preserves_inventory_until_reconciled_confirmation(self) -> None:
    """An uncertain preparation retains its original inventory across reconnects."""
    self.io.add("status", status())
    self.io.add("setstackerdimensions 0 11000 10000 10000")
    self.io.add("dimstatus", *DIMENSIONS)
    self.io.scripts.append(("unload 0", [b"ACK! unload 0 4\r\n", TimeoutError()]))
    with self.assertRaises(TimeoutError):
      await self.stack.prepare_for_unload(GEOMETRY)
    self.assertEqual(self.stack.children, [self.bottom, self.top])
    with self.assertRaises(RuntimeError):
      await self.stack.confirm_plate_unloaded(self.top)
    await self.device.setup()
    self.io.add("commandstat 4", "    4 OK!  - unload 0")
    self.io.add("status", status(extended=True))
    await self.device.reconcile_preparation()
    await self.stack.confirm_plate_unloaded(self.top)
    self.assertEqual(self.stack.children, [self.bottom])

  async def test_counts_do_not_create_or_remove_resources(self) -> None:
    """Firmware counts and identity-bearing PLR inventories remain distinct."""
    self.io.add("getplatecounts 0", "0: 17")
    self.assertEqual(await self.stack.request_plate_count(), 17)
    self.assertEqual(self.stack.children, [self.bottom, self.top])
