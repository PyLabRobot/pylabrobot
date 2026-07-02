"""Tests for the sequential Stacker capability."""

import unittest

from pylabrobot.resources import Plate, PlateHolder, ResourceNotFoundError
from pylabrobot.resources.resource_stack import ResourceStack
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import Well

from .chatterbox import StackerChatterboxBackend
from .stacker import EmptyStackError, LoadingTrayOccupiedError, Stacker


def _plate(name: str) -> Plate:
  return Plate(
    name,
    size_x=127.76,
    size_y=85.48,
    size_z=14.0,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=1,
      num_items_y=1,
      dx=0,
      dy=0,
      dz=0,
      item_dx=9,
      item_dy=9,
      size_x=9,
      size_y=9,
      size_z=10,
    ),
  )


def _make_stacker(num_stacks: int = 2) -> Stacker:
  stacks = [ResourceStack(f"stack_{i}", "z") for i in range(num_stacks)]
  loading_tray = PlateHolder(name="tray", size_x=127.76, size_y=85.48, size_z=0, pedestal_size_z=0)
  return Stacker(
    backend=StackerChatterboxBackend(),
    stacks=stacks,
    loading_tray=loading_tray,
  )


class StackerTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.stacker = _make_stacker()
    await self.stacker._on_setup()
    assert self.stacker.loading_tray is not None
    self.tray: PlateHolder = self.stacker.loading_tray

  async def test_requires_setup(self):
    stacker = _make_stacker()  # not set up
    with self.assertRaises(RuntimeError):
      await stacker.downstack(0)

  async def test_upstack_moves_plate_from_tray_to_stack(self):
    plate = _plate("p1")
    self.tray.assign_child_resource(plate)
    await self.stacker.upstack(0)
    self.assertIsNone(self.tray.resource)
    self.assertIs(self.stacker.stacks[0].get_top_item(), plate)
    self.assertIs(self.stacker.get_accessible_plate(0), plate)

  async def test_downstack_moves_accessible_plate_to_tray(self):
    plate = _plate("p1")
    self.tray.assign_child_resource(plate)
    await self.stacker.upstack(0)
    returned = await self.stacker.downstack(0)
    self.assertIs(returned, plate)
    self.assertIs(self.tray.resource, plate)
    self.assertEqual(len(self.stacker.stacks[0].children), 0)

  async def test_lifo_order(self):
    for name in ("A", "B"):
      self.tray.assign_child_resource(_plate(name))
      await self.stacker.upstack(0)
    accessible = self.stacker.get_accessible_plate(0)
    assert accessible is not None
    self.assertEqual(accessible.name, "B")
    first_out = await self.stacker.downstack(0)
    self.assertEqual(first_out.name, "B")

  async def test_downstack_empty_raises(self):
    with self.assertRaises(EmptyStackError):
      await self.stacker.downstack(0)

  async def test_downstack_with_occupied_tray_raises(self):
    self.tray.assign_child_resource(_plate("in_stack"))
    await self.stacker.upstack(0)
    self.tray.assign_child_resource(_plate("on_tray"))
    with self.assertRaises(LoadingTrayOccupiedError):
      await self.stacker.downstack(0)

  async def test_upstack_without_plate_raises(self):
    with self.assertRaises(ResourceNotFoundError):
      await self.stacker.upstack(0)

  async def test_get_stack_by_plate_name(self):
    self.tray.assign_child_resource(_plate("findme"))
    await self.stacker.upstack(1)
    self.assertIs(self.stacker.get_stack_by_plate_name("findme"), self.stacker.stacks[1])
    with self.assertRaises(ResourceNotFoundError):
      self.stacker.get_stack_by_plate_name("nope")

  async def test_resolve_stack_rejects_foreign_stack(self):
    foreign = ResourceStack("foreign", "z")
    with self.assertRaises(ValueError):
      await self.stacker.upstack(foreign)

  async def test_no_loading_tray_raises(self):
    stacker = Stacker(backend=StackerChatterboxBackend(), stacks=[ResourceStack("s", "z")])
    await stacker._on_setup()
    with self.assertRaises(RuntimeError):
      await stacker.downstack(0)
