""" Tests for LiquidHandler """
# pylint: disable=missing-class-docstring

import itertools
import pytest
import tempfile
from typing import Any, Dict, List, Optional, Union, cast
import unittest
import unittest.mock

from pylabrobot.liquid_handling.strictness import Strictness, set_strictness
from pylabrobot.resources import no_tip_tracking, set_tip_tracking, Liquid
from pylabrobot.resources.carrier import PlateCarrierSite
from pylabrobot.resources.errors import HasTipError, NoTipError, CrossContaminationError
from pylabrobot.resources.volume_tracker import set_volume_tracking, set_cross_contamination_tracking
from pylabrobot.resources.well import Well
from pylabrobot.resources.utils import create_ordered_items_2d

from . import backends
from .liquid_handler import LiquidHandler, OperationCallback
from pylabrobot.resources import (
  Container,
  Coordinate,
  Deck,
  Lid,
  Plate,
  ResourceStack,
  TipRack,
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cor_96_wellplate_360ul_Fb,
  ResourceNotFoundError,
)
from pylabrobot.resources.hamilton import STARLetDeck
from pylabrobot.resources.ml_star import STF_L, HTF_L
from .standard import (
  GripDirection,
  Pickup,
  Drop,
  DropTipRack,
  Aspiration,
  Dispense,
  AspirationPlate,
  DispensePlate
)

def _make_asp(
  r: Container, vol: float, tip: Any, offset: Coordinate=Coordinate.zero()) -> Aspiration:
  return Aspiration(resource=r, volume=vol, tip=tip, offset=offset,
                   flow_rate=None, liquid_height=None, blow_out_air_volume=None,
                   liquids=[(None, vol)])
def _make_disp(
  r: Container, vol: float, tip: Any, offset: Coordinate=Coordinate.zero()) -> Dispense:
  return Dispense(resource=r, volume=vol, tip=tip, offset=offset,
                  flow_rate=None, liquid_height=None, blow_out_air_volume=None,
                  liquids=[(None, vol)])


class TestLiquidHandlerLayout(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend = backends.SaverBackend(num_channels=8)
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(self.backend, deck=self.deck)

  def test_resource_assignment(self):
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = STF_L(name="tip_rack_01")
    tip_car[1] = STF_L(name="tip_rack_02")
    tip_car[3] = HTF_L("tip_rack_04")

    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cor_96_wellplate_360ul_Fb(name="aspiration plate")
    plt_car[2] = Cor_96_wellplate_360ul_Fb(name="dispense plate")

    self.deck.assign_child_resource(tip_car, rails=1)
    self.deck.assign_child_resource(plt_car, rails=21)

    # Test placing a carrier at a location where another carrier is located.
    with self.assertRaises(ValueError):
      dbl_plt_car_1 = PLT_CAR_L5AC_A00(name="double placed carrier 1")
      self.deck.assign_child_resource(dbl_plt_car_1, rails=1)

    with self.assertRaises(ValueError):
      dbl_plt_car_2 = PLT_CAR_L5AC_A00(name="double placed carrier 2")
      self.deck.assign_child_resource(dbl_plt_car_2, rails=2)

    with self.assertRaises(ValueError):
      dbl_plt_car_3 = PLT_CAR_L5AC_A00(name="double placed carrier 3")
      self.deck.assign_child_resource(dbl_plt_car_3, rails=20)

    # Test invalid rails.
    with self.assertRaises(ValueError):
      self.deck.assign_child_resource(plt_car, rails=-1)
    with self.assertRaises(ValueError):
      self.deck.assign_child_resource(plt_car, rails=42)
    with self.assertRaises(ValueError):
      self.deck.assign_child_resource(plt_car, rails=27)

  def test_get_resource(self):
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = STF_L(name="tip_rack_01")
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cor_96_wellplate_360ul_Fb(name="aspiration plate")
    self.deck.assign_child_resource(tip_car, rails=1)
    self.deck.assign_child_resource(plt_car, rails=10)

    # Get resource.
    self.assertEqual(self.lh.deck.get_resource("tip_carrier").name, "tip_carrier")
    self.assertEqual(self.lh.deck.get_resource("plate carrier").name, "plate carrier")

    # Get subresource.
    self.assertEqual(self.lh.deck.get_resource("tip_rack_01").name, "tip_rack_01")
    self.assertEqual(self.lh.deck.get_resource("aspiration plate").name, "aspiration plate")

    # Get unknown resource.
    with self.assertRaises(ResourceNotFoundError):
      self.lh.deck.get_resource("unknown resource")

  def test_subcoordinates(self):
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = STF_L(name="tip_rack_01")
    tip_car[3] = HTF_L(name="tip_rack_04")
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cor_96_wellplate_360ul_Fb(name="aspiration plate")
    plt_car[2] = Cor_96_wellplate_360ul_Fb(name="dispense plate")
    self.deck.assign_child_resource(tip_car, rails=1)
    self.deck.assign_child_resource(plt_car, rails=10)

    # Rails 10 should be left of rails 1.
    self.assertGreater(self.lh.deck.get_resource("plate carrier").get_absolute_location().x,
                       self.lh.deck.get_resource("tip_carrier").get_absolute_location().x)

    # Verified with Hamilton Method Editor.
    # Carriers.
    self.assertEqual(self.lh.deck.get_resource("tip_carrier").get_absolute_location(),
                     Coordinate(100.0, 63.0, 100.0))
    self.assertEqual(self.lh.deck.get_resource("plate carrier").get_absolute_location(),
                     Coordinate(302.5, 63.0, 100.0))

    # Subresources.
    self.assertEqual(
      cast(TipRack, self.lh.deck.get_resource("tip_rack_01")).get_item("A1") \
        .get_absolute_location() +
      cast(TipRack, self.lh.deck.get_resource("tip_rack_01")).get_item("A1").center(),
      Coordinate(117.900, 145.800, 164.450))
    self.assertEqual(
      cast(TipRack, self.lh.deck.get_resource("tip_rack_04")).get_item("A1") \
        .get_absolute_location() +
      cast(TipRack, self.lh.deck.get_resource("tip_rack_04")).get_item("A1").center(),
      Coordinate(117.900, 433.800, 131.450))

    self.assertEqual(
      cast(Plate, self.lh.deck.get_resource("aspiration plate")).get_item("A1")
        .get_absolute_location() +
      cast(Plate, self.lh.deck.get_resource("aspiration plate")).get_item("A1").center(),
        Coordinate(x=320.8, y=145.7, z=186.15) )

  def test_illegal_subresource_assignment_before(self):
    # Test assigning subresource with the same name as another resource in another carrier. This
    # should raise an ValueError when the carrier is assigned to the liquid handler.
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = STF_L(name="sub")
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cor_96_wellplate_360ul_Fb(name="sub")
    self.deck.assign_child_resource(tip_car, rails=1)
    with self.assertRaises(ValueError):
      self.deck.assign_child_resource(plt_car, rails=10)

  def test_illegal_subresource_assignment_after(self):
    # Test assigning subresource with the same name as another resource in another carrier, after
    # the carrier has been assigned. This should raise an error.
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = STF_L(name="sub")
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cor_96_wellplate_360ul_Fb(name="ok")
    self.deck.assign_child_resource(tip_car, rails=1)
    self.deck.assign_child_resource(plt_car, rails=10)
    with self.assertRaises(ValueError):
      plt_car[1] = Cor_96_wellplate_360ul_Fb(name="sub")

  async def test_move_plate_to_site(self):
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = plate = Cor_96_wellplate_360ul_Fb(name="plate")
    self.deck.assign_child_resource(plt_car, rails=21)

    await self.lh.move_plate(plate, plt_car[2])
    self.assertIsNotNone(plt_car[2].resource)
    self.assertIsNone(plt_car[0].resource)
    self.assertEqual(plt_car[2].resource, self.lh.deck.get_resource("plate"))
    self.assertEqual(plate.get_item("A1").get_absolute_location() + plate.get_item("A1").center(),
                     Coordinate(x=568.3, y=337.7, z=186.15))

  async def test_move_plate_free(self):
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = plate = Cor_96_wellplate_360ul_Fb(name="plate")
    self.deck.assign_child_resource(plt_car, rails=1)

    await self.lh.move_plate(plate, Coordinate(1000, 1000, 1000))
    self.assertIsNotNone(self.lh.deck.get_resource("plate"))
    self.assertIsNone(plt_car[0].resource)
    self.assertEqual(plate.get_absolute_location(),
      Coordinate(1000, 1000, 1000))

  async def test_move_lid(self):
    plate = Plate("plate", size_x=100, size_y=100, size_z=15, ordered_items={})
    plate.location = Coordinate(0, 0, 100)
    lid_height = 10
    lid = Lid(name="lid", size_x=plate.get_absolute_size_x(), size_y=plate.get_absolute_size_y(),
      size_z=lid_height, nesting_z_height=lid_height)
    lid.location = Coordinate(100, 100, 200)

    assert plate.get_absolute_location().x != lid.get_absolute_location().x
    assert plate.get_absolute_location().y != lid.get_absolute_location().y
    assert plate.get_absolute_location().z + plate.get_absolute_size_z() - lid_height \
      != lid.get_absolute_location().z

    await self.lh.move_lid(lid, plate)

    assert plate.get_absolute_location().x == lid.get_absolute_location().x
    assert plate.get_absolute_location().y == lid.get_absolute_location().y
    assert plate.get_absolute_location().z + plate.get_absolute_size_z() - lid_height \
      == lid.get_absolute_location().z

  async def test_move_plate_onto_resource_stack_with_lid(self):
    plate = Plate("plate", size_x=100, size_y=100, size_z=15, ordered_items={})
    lid = Lid(name="lid", size_x=plate.get_absolute_size_x(), size_y=plate.get_absolute_size_y(),
      size_z=10, nesting_z_height=4)

    stack = ResourceStack("stack", direction="z")
    self.deck.assign_child_resource(stack, location=Coordinate(100, 100, 0))

    await self.lh.move_plate(plate, stack)
    await self.lh.move_lid(lid, plate)

    assert plate.location is not None
    self.assertEqual(plate.location.z, 0)
    assert lid.location is not None
    self.assertEqual(lid.location.z, 11)
    self.assertEqual(plate.lid, lid)
    self.assertEqual(stack.get_absolute_size_z(), 21)

  async def test_move_plate_onto_resource_stack_with_plate(self):
    plate1 = Plate("plate1", size_x=100, size_y=100, size_z=15, ordered_items={})
    plate2 = Plate("plate2", size_x=100, size_y=100, size_z=15, ordered_items={})

    stack = ResourceStack("stack", direction="z")

    self.deck.assign_child_resource(stack, location=Coordinate(100, 100, 0))
    await self.lh.move_plate(plate1, stack)
    await self.lh.move_plate(plate2, stack)

    assert plate1.location is not None and plate2.location is not None
    self.assertEqual(plate1.location.z, 0)
    self.assertEqual(plate2.location.z, 15)
    self.assertEqual(stack.get_absolute_size_z(), 30)

  async def test_move_plate_rotation(self):
    rotations = [0, 90, 270, 360]
    grip_directions = [
      (GripDirection.LEFT, GripDirection.RIGHT),
      (GripDirection.FRONT, GripDirection.BACK),
    ]
    sites: List[Union[ResourceStack, PlateCarrierSite]] = [
      ResourceStack(name="stack", direction="z"),
      PlateCarrierSite(name="site", size_x=100, size_y=100, size_z=15, pedestal_size_z=1)
    ]

    test_cases = itertools.product(sites, rotations, grip_directions)

    for site, rotation, (get_direction, put_direction) in test_cases:
      with self.subTest(stack_type=site.__class__.__name__, rotation=rotation,
                        get_direction=get_direction, put_direction=put_direction):
        self.deck.assign_child_resource(site, location=Coordinate(100, 100, 0))

        plate = Plate("plate", size_x=200, size_y=100, size_z=15,
                      ordered_items=create_ordered_items_2d(
                        Well, num_items_x=1, num_items_y=1, dx=0, dy=0, dz=0,
                        item_dx=10, item_dy=10, size_x=10, size_y=10, size_z=10))
        plate.rotate(z=rotation)
        site.assign_child_resource(plate)
        original_center = plate.get_absolute_location(x="c", y="c", z="c")
        await self.lh.move_plate(plate, site, get_direction=get_direction,
                                 put_direction=put_direction)
        new_center = plate.get_absolute_location(x="c", y="c", z="c")

        self.assertEqual(new_center, original_center,
                         f"Center mismatch for {site.__class__.__name__}, rotation {rotation}, "
                         f"get_direction {get_direction}, "
                         f"put_direction {put_direction}")
        plate.unassign()
        self.deck.unassign_child_resource(site)

  async def test_move_lid_rotation(self):
    rotations = [0, 90, 270, 360]
    grip_directions = [
      (GripDirection.LEFT, GripDirection.RIGHT),
      (GripDirection.FRONT, GripDirection.BACK),
    ]

    test_cases = itertools.product(rotations, grip_directions)

    plate = Plate("plate", size_x=200, size_y=100, size_z=15, ordered_items={})
    lid = Lid(name="lid", size_x=plate.get_absolute_size_x(), size_y=plate.get_absolute_size_y(),
      size_z=10, nesting_z_height=4)
    self.deck.assign_child_resource(plate, location=Coordinate(100, 100, 0))
    for rot, (get_direction, put_direction) in test_cases:
      with self.subTest(rotation=rot, get_direction=get_direction, put_direction=put_direction):
        plate.rotate(z=rot)
        plate.assign_child_resource(lid)
        original_center = lid.get_absolute_location(x="c", y="c", z="c")
        await self.lh.move_lid(lid, plate, get_direction=get_direction, put_direction=put_direction)
        new_center = lid.get_absolute_location(x="c", y="c", z="c")
        self.assertEqual(new_center, original_center,
                         f"Center mismatch for rotation {rot}, get_direction {get_direction}, "
                         f"put_direction {put_direction}")
        lid.unassign()
        # reset rotations
        plate.rotation.z = 0
        lid.rotation.z = 0

  def test_serialize(self):
    serialized = self.lh.serialize()
    deserialized = LiquidHandler.deserialize(serialized)

    self.assertEqual(deserialized.deck, self.lh.deck)
    self.assertEqual(deserialized.backend.__class__.__name__,
      self.lh.backend.__class__.__name__)


class TestLiquidHandlerCommands(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.maxDiff = None

    self.backend = backends.SaverBackend(num_channels=8)
    self.deck =STARLetDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)

    self.tip_rack = STF_L(name="tip_rack")
    self.plate = Cor_96_wellplate_360ul_Fb(name="plate")
    self.deck.assign_child_resource(self.tip_rack, location=Coordinate(0, 0, 0))
    self.deck.assign_child_resource(self.plate, location=Coordinate(100, 100, 0))
    await self.lh.setup()

  def get_first_command(self, command) -> Optional[Dict[str, Any]]:
    for sent_command in self.backend.commands_received:
      if sent_command["command"] == command:
        return sent_command
    return None

  async def test_offsets_tips(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.lh.pick_up_tips([tip_spot], offsets=[Coordinate(x=1, y=1, z=1)])
    await self.lh.drop_tips([tip_spot], offsets=[Coordinate(x=1, y=1, z=1)])

    self.assertEqual(self.get_first_command("pick_up_tips"), {
      "command": "pick_up_tips",
      "args": (),
      "kwargs": {
        "use_channels": [0],
        "ops": [
          Pickup(tip_spot, tip=tip, offset=Coordinate(x=1, y=1, z=1))]}})
    self.assertEqual(self.get_first_command("drop_tips"), {
      "command": "drop_tips",
      "args": (),
      "kwargs": {
        "use_channels": [0], "ops": [
          Drop(tip_spot, tip=tip, offset=Coordinate(x=1, y=1, z=1))]}})

  async def test_with_use_channels(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    with self.lh.use_channels([2]):
      await self.lh.pick_up_tips([tip_spot])
      await self.lh.drop_tips([tip_spot])

    self.assertEqual(self.get_first_command("pick_up_tips"), {
      "command": "pick_up_tips",
      "args": (),
      "kwargs": {
        "use_channels": [2],
        "ops": [
          Pickup(tip_spot, tip=tip, offset=Coordinate.zero())]}})
    self.assertEqual(self.get_first_command("drop_tips"), {
      "command": "drop_tips",
      "args": (),
      "kwargs": {
        "use_channels": [2], "ops": [
          Drop(tip_spot, tip=tip, offset=Coordinate.zero())]}})

  async def test_offsets_asp_disp(self):
    well = self.plate.get_item("A1")
    well.tracker.set_liquids([(None, 10)])
    t = self.tip_rack.get_item("A1").get_tip()
    self.lh.update_head_state({0: t})
    await self.lh.aspirate([well], vols=[10], offsets=[Coordinate(x=1, y=1, z=1)])
    await self.lh.dispense([well], vols=[10], offsets=[Coordinate(x=1, y=1, z=1)])

    self.assertEqual(self.get_first_command("aspirate"), {
      "command": "aspirate",
      "args": (),
      "kwargs": {
        "use_channels": [0],
        "ops": [_make_asp(well, vol=10, offset=Coordinate(x=1, y=1, z=1), tip=t)]}})
    self.assertEqual(self.get_first_command("dispense"), {
      "command": "dispense",
      "args": (),
      "kwargs": {
        "use_channels": [0],
        "ops": [_make_disp(well, vol=10, offset=Coordinate(x=1, y=1, z=1), tip=t)]}})

  async def test_return_tips(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.lh.pick_up_tips([tip_spot])
    await self.lh.return_tips()

    self.assertEqual(self.get_first_command("drop_tips"), {
      "command": "drop_tips",
      "args": (),
      "kwargs": {
        "use_channels": [0],
        "ops": [Drop(tip_spot, tip=tip, offset=Coordinate.zero())]}})

    with self.assertRaises(RuntimeError):
      await self.lh.return_tips()

  async def test_return_tips96(self):
    for i in range(96):
      assert not self.lh.head96[i].has_tip, f"Channel head {i} is not empty."
    await self.lh.pick_up_tips96(self.tip_rack)
    for i in range(96):
      assert self.lh.head96[i].has_tip, f"Channel head {i} is empty."
    await self.lh.return_tips96()
    for i in range(96):
      assert not self.lh.head96[i].has_tip, f"Channel head {i} is not empty."

    self.assertEqual(self.get_first_command("drop_tips96"), {
      "command": "drop_tips96",
      "args": (),
      "kwargs": {
        "drop": DropTipRack(resource=self.tip_rack, offset=Coordinate.zero())
      }})

    with self.assertRaises(RuntimeError):
      await self.lh.return_tips()

  async def test_aspirate_dispense96(self):
    self.plate.get_item("A1").tracker.set_liquids([(None, 10)])
    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.aspirate96(self.plate, volume=10)
    for i in range(96):
      self.assertTrue(self.lh.head96[i].has_tip)
      self.assertEqual(self.lh.head96[i].get_tip().tracker.get_used_volume(), 10)
    await self.lh.dispense96(self.plate, volume=10)
    for i in range(96):
      self.assertEqual(self.lh.head96[i].get_tip().tracker.get_used_volume(), 0)

  async def test_transfer(self):
    t = self.tip_rack.get_item("A1").get_tip()
    self.lh.update_head_state({0: t})

    # Simple transfer
    self.plate.get_item("A1").tracker.set_liquids([(None, 10)])
    await self.lh.transfer(self.plate.get_well("A1"), self.plate["A2"], source_vol=10)

    self.assertEqual(self.get_first_command("aspirate"), {
      "command": "aspirate",
      "args": (),
      "kwargs": {
        "use_channels": [0],
        "ops": [_make_asp(self.plate.get_item("A1"), vol=10.0, tip=t)]}})
    self.assertEqual(self.get_first_command("dispense"), {
      "command": "dispense",
      "args": (),
      "kwargs": {
        "use_channels": [0],
        "ops": [_make_disp(self.plate.get_item("A2"), vol=10.0, tip=t)]}})
    self.backend.clear()

    # Transfer to multiple wells
    self.plate.get_item("A1").tracker.set_liquids([(None, 80)])
    await self.lh.transfer(self.plate.get_well("A1"), self.plate["A1:H1"], source_vol=80)
    self.assertEqual(self.get_first_command("aspirate"), {
      "command": "aspirate",
      "args": (),
      "kwargs": {
        "use_channels": [0],
        "ops": [_make_asp(self.plate.get_item("A1"), vol=80.0, tip=t)]}})

    dispenses = list(filter(lambda x: x["command"] == "dispense", self.backend.commands_received))
    self.assertEqual(dispenses, [{
      "command": "dispense",
      "args": (),
      "kwargs": {
        "use_channels": [0],
        "ops": [_make_disp(well, vol=10.0, tip=t)]}}
      for well in self.plate["A1:H1"]])
    self.backend.clear()

    # Transfer with ratios
    self.plate.get_item("A1").tracker.set_liquids([(None, 60)])
    await self.lh.transfer(self.plate.get_well("A1"), self.plate["B1:C1"], source_vol=60,
      ratios=[2, 1])
    self.assertEqual(self.get_first_command("aspirate"), {
      "command": "aspirate",
      "args": (),
      "kwargs": {
        "use_channels": [0],
        "ops": [_make_asp(self.plate.get_item("A1"), vol=60.0, tip=t)]}})
    dispenses = list(filter(lambda x: x["command"] == "dispense", self.backend.commands_received))
    self.assertEqual(dispenses, [{
      "command": "dispense",
      "args": (),
      "kwargs": {
        "use_channels": [0],
        "ops": [_make_disp(well, vol=vol, tip=t)]}}
      for well, vol in zip(self.plate["B1:C1"], [40, 20])])
    self.backend.clear()

    # Transfer with target_vols
    vols: List[float] = [3, 1, 4, 1, 5, 9, 6, 2]
    self.plate.get_item("A1").tracker.set_liquids([(None, sum(vols))])
    await self.lh.transfer(self.plate.get_well("A1"), self.plate["A1:H1"], target_vols=vols)
    self.assertEqual(self.get_first_command("aspirate"), {
      "command": "aspirate",
      "args": (),
      "kwargs": {
        "use_channels": [0],
        "ops": [_make_asp(self.plate.get_well("A1"), vol=sum(vols), tip=t)]}})
    dispenses = list(filter(lambda x: x["command"] == "dispense", self.backend.commands_received))
    self.assertEqual(dispenses, [{
      "command": "dispense",
      "args": (),
      "kwargs": {
        "use_channels": [0],
        "ops": [_make_disp(well, vol=vol, tip=t)]}}
      for well, vol in zip(self.plate["A1:H1"], vols)])
    self.backend.clear()

    # target_vols and source_vol specified
    with self.assertRaises(TypeError):
      await self.lh.transfer(self.plate.get_well("A1"), self.plate["A1:H1"],
        source_vol=100, target_vols=vols)

    # target_vols and ratios specified
    with self.assertRaises(TypeError):
      await self.lh.transfer(self.plate.get_well("A1"), self.plate["A1:H1"],
        ratios=[1]*8, target_vols=vols)

  async def test_stamp(self):
    # Simple transfer
    await self.lh.pick_up_tips96(self.tip_rack) # pick up tips first.
    await self.lh.stamp(self.plate, self.plate, volume=10)
    ts = self.tip_rack.get_all_tips()

    self.assertEqual(self.get_first_command("aspirate96"), {
      "command": "aspirate96",
      "args": (),
      "kwargs": {"aspiration":
        AspirationPlate(wells=self.plate.get_all_items(), volume=10.0, tips=ts,
                        offset=Coordinate.zero(), flow_rate=None, liquid_height=None,
                        blow_out_air_volume=None, liquids=[[(None, 10)]]*96)}})
    self.assertEqual(self.get_first_command("dispense96"), {
      "command": "dispense96",
      "args": (),
      "kwargs": {"dispense":
        DispensePlate(wells=self.plate.get_all_items(), volume=10.0, tips=ts,
                      offset=Coordinate.zero(), flow_rate=None, liquid_height=None,
                      blow_out_air_volume=None, liquids=[[(None, 10)]]*96)}})
    self.backend.clear()

  async def test_tip_tracking_double_pickup(self):
    set_tip_tracking(enabled=True)
    await self.lh.pick_up_tips(self.tip_rack["A1"])
    with self.assertRaises(HasTipError):
      await self.lh.pick_up_tips(self.tip_rack["A2"])
    await self.lh.drop_tips(self.tip_rack["A1"])
    # pick_up_tips should work even after causing a HasTipError
    await self.lh.pick_up_tips(self.tip_rack["A2"])
    await self.lh.drop_tips(self.tip_rack["A2"])
    set_tip_tracking(enabled=False)

    self.lh.clear_head_state()
    with no_tip_tracking():
      await self.lh.pick_up_tips(self.tip_rack["A2"])

  async def test_tip_tracking_empty_drop(self):
    with self.assertRaises(NoTipError):
      await self.lh.drop_tips(self.tip_rack["A1"])

    await self.lh.pick_up_tips(self.tip_rack["A2"])
    set_tip_tracking(enabled=True)
    with self.assertRaises(HasTipError):
      await self.lh.drop_tips(self.tip_rack["A3"])
    set_tip_tracking(enabled=False)

  async def test_tip_tracking_empty_pickup(self):
    self.tip_rack.get_item("A1").empty()

    set_tip_tracking(enabled=True)
    with self.assertRaises(NoTipError):
      await self.lh.pick_up_tips(self.tip_rack["A1"])
    set_tip_tracking(enabled=False)

  async def test_tip_tracking_full_spot(self):
    await self.lh.pick_up_tips(self.tip_rack["A1"])
    with self.assertRaises(HasTipError):
      set_tip_tracking(enabled=True)
      await self.lh.drop_tips(self.tip_rack["A2"])
      set_tip_tracking(enabled=False)

  async def test_tip_tracking_double_pickup_single_command(self):
    set_tip_tracking(enabled=True)
    with self.assertRaises(NoTipError):
      await self.lh.pick_up_tips(self.tip_rack["A1", "A1"])
    set_tip_tracking(enabled=False)

  async def test_discard_tips(self):
    tips = self.tip_rack.get_tips("A1:D1")
    await self.lh.pick_up_tips(self.tip_rack["A1", "B1", "C1", "D1"], use_channels=[0, 1, 3, 4])
    await self.lh.discard_tips()
    trash = self.deck.get_trash_area()
    offsets = list(reversed(trash.centers(yn=4)))
    offsets = [o - trash.center() for o in offsets] # offset is wrt trash center

    self.assertEqual(self.get_first_command("drop_tips"), {
      "command": "drop_tips",
      "args": (),
      "kwargs": {
        "use_channels": [0, 1, 3, 4],
        "ops": [
          Drop(self.deck.get_trash_area(), tip=tips[3], offset=offsets[0]),
          Drop(self.deck.get_trash_area(), tip=tips[2], offset=offsets[1]),
          Drop(self.deck.get_trash_area(), tip=tips[1], offset=offsets[2]),
          Drop(self.deck.get_trash_area(), tip=tips[0], offset=offsets[3]),
        ]}})

    # test tip tracking
    with self.assertRaises(RuntimeError):
      await self.lh.discard_tips()

  async def test_aspirate_with_lid(self):
    lid = Lid("lid", size_x=self.plate.get_size_x(), size_y=self.plate.get_size_y(),
              size_z=10, nesting_z_height=self.plate.get_size_z())
    self.plate.assign_child_resource(lid)
    well = self.plate.get_item("A1")
    well.tracker.set_liquids([(None, 10)])
    t = self.tip_rack.get_item("A1").get_tip()
    self.lh.update_head_state({0: t})
    with self.assertRaises(ValueError):
      await self.lh.aspirate([well], vols=[10])

  @pytest.mark.filterwarnings("ignore:Extra arguments to backend.pick_up_tips")
  async def test_strictness(self):
    class TestBackend(backends.SaverBackend):
      """ Override pick_up_tips for testing. """
      async def pick_up_tips(self, ops, use_channels, non_default, default=True): # type: ignore
        # pylint: disable=unused-argument
        assert non_default == default

    self.backend = TestBackend(num_channels=16)
    self.lh = LiquidHandler(self.backend, deck=self.deck)
    await self.lh.setup()

    with no_tip_tracking():
      set_strictness(Strictness.IGNORE)
      await self.lh.pick_up_tips(self.tip_rack["A1"], non_default=True)
      await self.lh.pick_up_tips(self.tip_rack["A1"], use_channels=[1],
        non_default=True, does_not_exist=True)
      with self.assertRaises(TypeError): # missing non_default
        await self.lh.pick_up_tips(self.tip_rack["A1"], use_channels=[2])

      set_strictness(Strictness.WARN)
      await self.lh.pick_up_tips(self.tip_rack["A1"], non_default=True, use_channels=[3])
      with self.assertWarns(UserWarning): # extra kwargs should warn
        await self.lh.pick_up_tips(self.tip_rack["A1"], use_channels=[4],
          non_default=True, does_not_exist=True)
      self.lh.clear_head_state()
      # We override default to False, so this should raise an assertion error. To test whether
      # overriding default to True works.
      with self.assertRaises(AssertionError):
        await self.lh.pick_up_tips(self.tip_rack["A1"], use_channels=[4],
          non_default=True, does_not_exist=True, default=False)
      with self.assertRaises(TypeError): # missing non_default
        await self.lh.pick_up_tips(self.tip_rack["A1"], use_channels=[5])

      set_strictness(Strictness.STRICT)
      self.lh.clear_head_state()
      await self.lh.pick_up_tips(self.tip_rack["A1"], non_default=True, use_channels=[6])
      with self.assertRaises(TypeError): # cannot have extra kwargs
        await self.lh.pick_up_tips(self.tip_rack["A1"], use_channels=[7],
          non_default=True, does_not_exist=True)
      with self.assertRaises(TypeError): # missing non_default
        await self.lh.pick_up_tips(self.tip_rack["A1"], use_channels=[8])

      set_strictness(Strictness.WARN)

  async def test_save_state(self):
    set_volume_tracking(enabled=True)

    # set and save the state
    self.plate.get_item("A2").tracker.set_liquids([(None, 10)])
    state_filename = tempfile.mktemp()
    self.lh.deck.save_state_to_file(fn=state_filename)

    # save the deck
    deck_filename = tempfile.mktemp()
    self.lh.deck.save(fn=deck_filename)

    # create a new liquid handler, load the state and the deck
    lh2 = LiquidHandler(self.backend, deck=STARLetDeck())
    lh2.deck = Deck.load_from_json_file(json_file=deck_filename)
    lh2.deck.load_state_from_file(fn=state_filename)

    # assert that the state is the same
    well_a1 = lh2.deck.get_resource("plate").get_item("A1") # type: ignore
    self.assertEqual(well_a1.tracker.liquids, [])
    well_a2 = lh2.deck.get_resource("plate").get_item("A2") # type: ignore
    self.assertEqual(well_a2.tracker.liquids, [(None, 10)])

    set_volume_tracking(enabled=False)


class TestLiquidHandlerVolumeTracking(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = backends.SaverBackend(num_channels=8)
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    self.tip_rack = STF_L(name="tip_rack")
    self.plate = Cor_96_wellplate_360ul_Fb(name="plate")
    self.deck.assign_child_resource(self.tip_rack, location=Coordinate(0, 0, 0))
    self.deck.assign_child_resource(self.plate, location=Coordinate(100, 100, 0))
    await self.lh.setup()
    set_volume_tracking(enabled=True)

  async def asyncTearDown(self):
    set_volume_tracking(enabled=False)

  async def test_dispense_with_volume_tracking(self):
    well = self.plate.get_item("A1")
    await self.lh.pick_up_tips(self.tip_rack["A1"])
    well.tracker.set_liquids([(None, 10)])
    await self.lh.aspirate([well], vols=[10])
    await self.lh.dispense([well], vols=[10])
    self.assertEqual(well.tracker.liquids, [(None, 10)])

  async def test_mix_volume_tracking(self):
    for i in range(8):
      self.plate.get_item(i).set_liquids([(Liquid.SERUM, 55)])

    await self.lh.pick_up_tips(self.tip_rack[0:8])
    initial_liquids = [self.plate.get_item(i).tracker.liquids for i in range(8)]
    for _ in range(10):
      await self.lh.aspirate(self.plate[0:8], vols=[45]*8)
      await self.lh.dispense(self.plate[0:8], vols=[45]*8)
    liquids_now = [self.plate.get_item(i).tracker.liquids for i in range(8)]
    self.assertEqual(liquids_now, initial_liquids)

  async def test_channel_1_liquid_tracking(self):
    self.plate.get_item("A1").tracker.set_liquids([(Liquid.WATER, 10)])
    with self.lh.use_channels([1]):
      await self.lh.pick_up_tips(self.tip_rack["A1"])
      await self.lh.aspirate([self.plate.get_item("A1")], vols=[10])
      await self.lh.dispense([self.plate.get_item("A2")], vols=[10])

class TestLiquidHandlerCrossContaminationTracking(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = backends.SaverBackend(num_channels=8)
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    self.tip_rack = STF_L(name="tip_rack")
    self.plate = Cor_96_wellplate_360ul_Fb(name="plate")
    self.deck.assign_child_resource(self.tip_rack, location=Coordinate(0, 0, 0))
    self.deck.assign_child_resource(self.plate, location=Coordinate(100, 100, 0))
    await self.lh.setup()
    set_volume_tracking(enabled=True)
    set_cross_contamination_tracking(enabled=True)

  async def asyncTearDown(self):
    set_volume_tracking(enabled=False)
    set_cross_contamination_tracking(enabled=False)

  async def test_aspirate_with_contaminated_tip(self):
    blood_well = self.plate.get_item("A1")
    etoh_well = self.plate.get_item("A2")
    dest_well = self.plate.get_item("A3")
    await self.lh.pick_up_tips(self.tip_rack["A1"])
    blood_well.tracker.set_liquids([(Liquid.BLOOD, 10)])
    etoh_well.tracker.set_liquids([(Liquid.ETHANOL, 10)])
    await self.lh.aspirate([blood_well], vols=[10])
    await self.lh.dispense([dest_well], vols=[10])
    with self.assertRaises(CrossContaminationError):
      await self.lh.aspirate([etoh_well], vols=[10])

  async def test_aspirate_from_same_well_twice(self):
    src_well = self.plate.get_item("A1")
    dst_well = self.plate.get_item("A2")
    await self.lh.pick_up_tips(self.tip_rack["A1"])
    src_well.tracker.set_liquids([(Liquid.BLOOD, 20)])
    await self.lh.aspirate([src_well], vols=[10])
    await self.lh.dispense([dst_well], vols=[10])
    self.assertEqual(dst_well.tracker.liquids, [(Liquid.BLOOD, 10)])
    await self.lh.aspirate([src_well], vols=[10])
    await self.lh.dispense([dst_well], vols=[10])
    self.assertEqual(dst_well.tracker.liquids, [(Liquid.BLOOD, 20)])

  async def test_aspirate_from_well_with_partial_overlap(self):
    pure_blood_well = self.plate.get_item("A1")
    mix_well = self.plate.get_item("A2")
    await self.lh.pick_up_tips(self.tip_rack["A1"])
    pure_blood_well.tracker.set_liquids([(Liquid.BLOOD, 20)])
    mix_well.tracker.set_liquids([(Liquid.ETHANOL, 20)])
    await self.lh.aspirate([pure_blood_well], vols=[10])
    await self.lh.dispense([mix_well], vols=[10])
    self.assertEqual(mix_well.tracker.liquids, [(Liquid.ETHANOL, 20),
                                                    (Liquid.BLOOD, 10)]) # order matters
    with self.assertRaises(CrossContaminationError):
      await self.lh.aspirate([pure_blood_well], vols=[10])


class LiquidHandlerForTesting(LiquidHandler):
  ALLOWED_CALLBACKS = {
    "test_operation",
    "test_duplicate",
    "test_operation_without_error",
    "test_callback_not_registered_with_error",
  }

  def trigger_callback(self, method_name: str, *args, **kwargs):
    self._trigger_callback(method_name, *args, **kwargs)


class TestLiquidHandlerCallbacks(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend = backends.SaverBackend(num_channels=8)
    self.deck = STARLetDeck()
    self.lh = LiquidHandlerForTesting(self.backend, deck=self.deck)
    self.callback = unittest.mock.Mock(spec=OperationCallback)

  def test_register_callback(self):
    self.lh.register_callback("test_operation", self.callback)
    assert "test_operation" in self.lh.callbacks

  def test_duplicate_register_callback(self):
    self.lh.register_callback("test_duplicate", self.callback)
    with pytest.raises(RuntimeError):
      self.lh.register_callback("test_duplicate", self.callback)

  def test_register_disallowed_callback(self):
    with pytest.raises(RuntimeError):
      self.lh.register_callback("not_allowed", self.callback)

  def test_trigger_callback_without_error(self):
    self.lh.register_callback("test_operation_without_error", self.callback)
    self.lh.trigger_callback("test_operation_without_error")
    self.callback.assert_called_once()

  def test_trigger_callback_with_error_raised(self):
    callback = unittest.mock.Mock(spec = OperationCallback, side_effect=RuntimeError)
    self.lh.register_callback("test_operation", callback)
    with pytest.raises(RuntimeError):
      self.lh.trigger_callback(
        "test_operation",
        error=RuntimeError("test")
      )
    error_passed = callback.call_args[1].get("error")
    assert isinstance(error_passed, Exception)

  def test_trigger_callback_with_error_not_raised(self):
    error = RuntimeError("test")
    self.lh.register_callback("test_operation", self.callback)
    try:
      self.lh.trigger_callback("test_operation", error=error)
    except RuntimeError as e:
      pytest.fail(f"Unexpected exception raised: {e}")
    self.callback.assert_called_with(self.lh, error=error)

  def test_trigger_callback_not_found_with_error(self):
    with pytest.raises(RuntimeError):
      self.lh.trigger_callback(
        "test_callback_not_registered_with_error",
        error=RuntimeError("test"),
      )
