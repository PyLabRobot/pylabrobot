import itertools
import tempfile
import unittest
import unittest.mock
from typing import Any, List, Union, cast
from unittest.mock import PropertyMock

import pytest

from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.liquid_handling.backends.chatterbox import LiquidHandlerChatterboxBackend
from pylabrobot.liquid_handling.errors import ChannelizedError
from pylabrobot.liquid_handling.strictness import (
  Strictness,
  set_strictness,
)
from pylabrobot.liquid_handling.utils import get_tight_single_resource_liquid_op_offsets
from pylabrobot.resources import (
  PLT_CAR_L5AC_A00,
  TIP_CAR_480_A00,
  Container,
  Coordinate,
  Cor_96_wellplate_360ul_Fb,
  Deck,
  Lid,
  Plate,
  ResourceNotFoundError,
  ResourceStack,
  TipRack,
  nest_1_troughplate_195000uL_Vb,
  no_tip_tracking,
  set_tip_tracking,
)
from pylabrobot.resources.carrier import PlateHolder
from pylabrobot.resources.errors import (
  HasTipError,
  NoTipError,
)
from pylabrobot.resources.hamilton import (
  STARLetDeck,
  hamilton_96_tiprack_300uL_filter,
  hamilton_96_tiprack_1000uL_filter,
)
from pylabrobot.resources.revvity.plates import Revvity_384_wellplate_28ul_Ub
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.volume_tracker import (
  set_volume_tracking,
)
from pylabrobot.resources.well import Well
from pylabrobot.serializer import serialize

from .liquid_handler import LiquidHandler
from .standard import (
  Drop,
  DropTipRack,
  GripDirection,
  MultiHeadAspirationPlate,
  MultiHeadDispensePlate,
  Pickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)


def _create_mock_backend(num_channels: int = 8):
  """Create a mock LiquidHandlerBackend with the specified number of channels."""
  mock = unittest.mock.create_autospec(LiquidHandlerBackend, instance=True)
  type(mock).num_channels = PropertyMock(return_value=num_channels)
  mock.can_pick_up_tip.return_value = True
  return mock


def _make_asp(
  r: Container,
  vol: float,
  tip: Any,
  offset: Coordinate = Coordinate.zero(),
) -> SingleChannelAspiration:
  return SingleChannelAspiration(
    resource=r,
    volume=vol,
    tip=tip,
    offset=offset,
    flow_rate=None,
    liquid_height=None,
    blow_out_air_volume=None,
    mix=None,
  )


def _make_disp(
  r: Container,
  vol: float,
  tip: Any,
  offset: Coordinate = Coordinate.zero(),
) -> SingleChannelDispense:
  return SingleChannelDispense(
    resource=r,
    volume=vol,
    tip=tip,
    offset=offset,
    flow_rate=None,
    liquid_height=None,
    blow_out_air_volume=None,
    mix=None,
  )


class TestLiquidHandlerLayout(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend = _create_mock_backend(num_channels=8)
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(self.backend, deck=self.deck)

  def test_resource_assignment(self):
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = hamilton_96_tiprack_300uL_filter(name="tip_rack_01")
    tip_car[1] = hamilton_96_tiprack_300uL_filter(name="tip_rack_02")
    tip_car[3] = hamilton_96_tiprack_1000uL_filter("tip_rack_04")

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
    tip_car[0] = hamilton_96_tiprack_300uL_filter(name="tip_rack_01")
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cor_96_wellplate_360ul_Fb(name="aspiration plate")
    self.deck.assign_child_resource(tip_car, rails=1)
    self.deck.assign_child_resource(plt_car, rails=10)

    # Get resource.
    self.assertEqual(self.lh.deck.get_resource("tip_carrier").name, "tip_carrier")
    self.assertEqual(self.lh.deck.get_resource("plate carrier").name, "plate carrier")

    # Get subresource.
    self.assertEqual(self.lh.deck.get_resource("tip_rack_01").name, "tip_rack_01")
    self.assertEqual(
      self.lh.deck.get_resource("aspiration plate").name,
      "aspiration plate",
    )

    # Get unknown resource.
    with self.assertRaises(ResourceNotFoundError):
      self.lh.deck.get_resource("unknown resource")

  def test_name_parameter(self):
    # Default name is derived from deck name
    deck = STARLetDeck()
    lh = LiquidHandler(_create_mock_backend(), deck=deck)
    self.assertEqual(lh.name, f"lh_{deck.name}")

    # Custom name
    deck2 = STARLetDeck()
    lh2 = LiquidHandler(_create_mock_backend(), deck=deck2, name="my_liquid_handler")
    self.assertEqual(lh2.name, "my_liquid_handler")

  def test_subcoordinates(self):
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = hamilton_96_tiprack_300uL_filter(name="tip_rack_01")
    tip_car[3] = hamilton_96_tiprack_1000uL_filter(name="tip_rack_04")
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cor_96_wellplate_360ul_Fb(name="aspiration plate")
    plt_car[2] = Cor_96_wellplate_360ul_Fb(name="dispense plate")
    self.deck.assign_child_resource(tip_car, rails=1)
    self.deck.assign_child_resource(plt_car, rails=10)

    # Rails 10 should be left of rails 1.
    self.assertGreater(
      self.lh.deck.get_resource("plate carrier").get_absolute_location().x,
      self.lh.deck.get_resource("tip_carrier").get_absolute_location().x,
    )

    # Verified with Hamilton Method Editor.
    # Carriers.
    self.assertEqual(
      self.lh.deck.get_resource("tip_carrier").get_absolute_location(),
      Coordinate(100.0, 63.0, 100.0),
    )
    self.assertEqual(
      self.lh.deck.get_resource("plate carrier").get_absolute_location(),
      Coordinate(302.5, 63.0, 100.0),
    )

    # Subresources.
    self.assertEqual(
      cast(TipRack, self.lh.deck.get_resource("tip_rack_01")).get_item("A1").get_absolute_location()
      + cast(TipRack, self.lh.deck.get_resource("tip_rack_01")).get_item("A1").center(),
      Coordinate(117.900, 145.800, 164.450),
    )
    self.assertEqual(
      cast(TipRack, self.lh.deck.get_resource("tip_rack_04")).get_item("A1").get_absolute_location()
      + cast(TipRack, self.lh.deck.get_resource("tip_rack_04")).get_item("A1").center(),
      Coordinate(117.900, 433.800, 131.450),
    )

    self.assertEqual(
      cast(Plate, self.lh.deck.get_resource("aspiration plate"))
      .get_item("A1")
      .get_absolute_location()
      + cast(Plate, self.lh.deck.get_resource("aspiration plate")).get_item("A1").center(),
      Coordinate(x=320.8, y=145.7, z=186.15),
    )

  def test_illegal_subresource_assignment_before(self):
    # Test assigning subresource with the same name as another resource in another carrier. This
    # should raise an ValueError when the carrier is assigned to the liquid handler.
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = hamilton_96_tiprack_300uL_filter(name="sub")
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cor_96_wellplate_360ul_Fb(name="sub")
    self.deck.assign_child_resource(tip_car, rails=1)
    with self.assertRaises(ValueError):
      self.deck.assign_child_resource(plt_car, rails=10)

  def test_illegal_subresource_assignment_after(self):
    # Test assigning subresource with the same name as another resource in another carrier, after
    # the carrier has been assigned. This should raise an error.
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = hamilton_96_tiprack_300uL_filter(name="sub")
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
    self.assertEqual(
      plate.get_item("A1").get_absolute_location() + plate.get_item("A1").center(),
      Coordinate(x=568.3, y=337.7, z=186.15),
    )

  async def test_move_plate_free(self):
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = plate = Cor_96_wellplate_360ul_Fb(name="plate")
    self.deck.assign_child_resource(plt_car, rails=1)

    await self.lh.move_plate(plate, Coordinate(1000, 1000, 1000))
    self.assertIsNotNone(self.lh.deck.get_resource("plate"))
    self.assertIsNone(plt_car[0].resource)
    self.assertEqual(plate.get_absolute_location(), Coordinate(1000, 1000, 1000))

  async def test_move_lid(self):
    plate = Plate("plate", size_x=100, size_y=100, size_z=15, ordered_items={})
    self.deck.assign_child_resource(plate, location=Coordinate(0, 0, 100))
    lid_height = 10
    lid = Lid(
      name="lid",
      size_x=plate.get_absolute_size_x(),
      size_y=plate.get_absolute_size_y(),
      size_z=lid_height,
      nesting_z_height=lid_height,
    )
    self.deck.assign_child_resource(lid, location=Coordinate(100, 100, 200))

    assert plate.get_absolute_location().x != lid.get_absolute_location().x
    assert plate.get_absolute_location().y != lid.get_absolute_location().y
    assert (
      plate.get_absolute_location().z + plate.get_absolute_size_z() - lid_height
      != lid.get_absolute_location().z
    )

    await self.lh.move_lid(lid, plate)

    assert plate.get_absolute_location().x == lid.get_absolute_location().x
    assert plate.get_absolute_location().y == lid.get_absolute_location().y
    assert (
      plate.get_absolute_location().z + plate.get_absolute_size_z() - lid_height
      == lid.get_absolute_location().z
    )

  async def test_move_plate_onto_resource_stack_with_lid(self):
    plate = Plate("plate", size_x=100, size_y=100, size_z=15, ordered_items={})
    lid = Lid(
      name="lid",
      size_x=plate.get_absolute_size_x(),
      size_y=plate.get_absolute_size_y(),
      size_z=10,
      nesting_z_height=4,
    )

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
    rotations = [0, 180, 360]  # rotation wrt site before AND after move
    grip_directions = [
      (GripDirection.LEFT, GripDirection.RIGHT),
      (GripDirection.FRONT, GripDirection.BACK),
    ]
    sites: List[Union[ResourceStack, PlateHolder]] = [
      ResourceStack(name="stack", direction="z"),
      PlateHolder(
        name="site",
        size_x=100,
        size_y=100,
        size_z=15,
        pedestal_size_z=1,
      ),
    ]

    test_cases = itertools.product(sites, rotations, grip_directions)

    for site, rotation, (pickup_direction, drop_direction) in test_cases:
      with self.subTest(
        stack_type=site.__class__.__name__,
        rotation=rotation,
        pickup_direction=pickup_direction,
        drop_direction=drop_direction,
      ):
        self.deck.assign_child_resource(site, location=Coordinate(100, 100, 0))

        plate = Plate(
          "plate",
          size_x=200,
          size_y=100,
          size_z=15,
          ordered_items=create_ordered_items_2d(
            Well,
            num_items_x=1,
            num_items_y=1,
            dx=0,
            dy=0,
            dz=0,
            item_dx=10,
            item_dy=10,
            size_x=10,
            size_y=10,
            size_z=10,
          ),
        )
        plate.rotate(z=rotation)
        site.assign_child_resource(plate)
        original_center = plate.get_absolute_location(x="c", y="c", z="c")
        await self.lh.move_plate(
          plate,
          site,
          pickup_direction=pickup_direction,
          drop_direction=drop_direction,
        )
        new_center = plate.get_absolute_location(x="c", y="c", z="c")
        assert plate.rotation.z == (rotation + 180) % 360

        self.assertEqual(
          new_center,
          original_center,
          f"Center mismatch for {site.__class__.__name__}, rotation {rotation}, "
          f"pickup_direction {pickup_direction}, "
          f"drop_direction {drop_direction}",
        )
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
    lid = Lid(
      name="lid",
      size_x=plate.get_absolute_size_x(),
      size_y=plate.get_absolute_size_y(),
      size_z=10,
      nesting_z_height=4,
    )
    self.deck.assign_child_resource(plate, location=Coordinate(100, 100, 0))
    for rot, (pickup_direction, drop_direction) in test_cases:
      with self.subTest(
        rotation=rot,
        pickup_direction=pickup_direction,
        drop_direction=drop_direction,
      ):
        plate.rotate(z=rot)
        plate.assign_child_resource(lid)
        original_center = lid.get_absolute_location(x="c", y="c", z="c")
        await self.lh.move_lid(
          lid,
          plate,
          pickup_direction=pickup_direction,
          drop_direction=drop_direction,
        )
        new_center = lid.get_absolute_location(x="c", y="c", z="c")
        self.assertEqual(
          new_center,
          original_center,
          f"Center mismatch for rotation {rot}, pickup_direction {pickup_direction}, "
          f"drop_direction {drop_direction}",
        )
        lid.unassign()
        # reset rotations
        plate.rotation.z = 0
        lid.rotation.z = 0

  def test_serialize(self):
    # Use a real backend for serialization test since mocks can't be deserialized
    backend = LiquidHandlerChatterboxBackend(num_channels=8)
    lh = LiquidHandler(backend, deck=STARLetDeck())

    serialized = lh.serialize()
    deserialized = LiquidHandler.deserialize(serialized)

    self.assertEqual(deserialized.deck, lh.deck)
    self.assertEqual(
      deserialized.backend.__class__.__name__,
      lh.backend.__class__.__name__,
    )


class TestLiquidHandlerCommands(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.maxDiff = None

    self.backend = _create_mock_backend(num_channels=8)
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)

    self.tip_rack = hamilton_96_tiprack_300uL_filter(name="tip_rack")
    self.plate = Cor_96_wellplate_360ul_Fb(name="plate")
    self.deck.assign_child_resource(self.tip_rack, location=Coordinate(0, 0, 0))
    self.deck.assign_child_resource(self.plate, location=Coordinate(100, 100, 0))
    await self.lh.setup()

  async def test_offsets_tips(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.lh.pick_up_tips([tip_spot], offsets=[Coordinate(x=1, y=1, z=1)])
    await self.lh.drop_tips([tip_spot], offsets=[Coordinate(x=1, y=1, z=1)])

    self.backend.pick_up_tips.assert_called_once_with(
      use_channels=[0],
      ops=[Pickup(tip_spot, tip=tip, offset=Coordinate(x=1, y=1, z=1))],
    )
    self.backend.drop_tips.assert_called_once_with(
      use_channels=[0],
      ops=[Drop(tip_spot, tip=tip, offset=Coordinate(x=1, y=1, z=1))],
    )

  async def test_default_offset_head96(self):
    self.lh.default_offset_head96 = Coordinate(1, 2, 3)

    await self.lh.pick_up_tips96(self.tip_rack)
    self.backend.pick_up_tips96.assert_called_once()
    call_kwargs = self.backend.pick_up_tips96.call_args.kwargs
    self.assertEqual(call_kwargs["pickup"].offset, Coordinate(1, 2, 3))
    self.backend.pick_up_tips96.reset_mock()

    # aspirate with extra offset; effective offset should be default + provided
    await self.lh.aspirate96(self.plate, volume=10, offset=Coordinate(1, 0, 0))
    self.backend.aspirate96.assert_called_once()
    call_kwargs = self.backend.aspirate96.call_args.kwargs
    self.assertEqual(call_kwargs["aspiration"].offset, Coordinate(2, 2, 3))
    self.backend.aspirate96.reset_mock()

    # dispense without providing offset uses default
    await self.lh.dispense96(self.plate, volume=10)
    self.backend.dispense96.assert_called_once()
    call_kwargs = self.backend.dispense96.call_args.kwargs
    self.assertEqual(call_kwargs["dispense"].offset, Coordinate(1, 2, 3))
    self.backend.dispense96.reset_mock()

    await self.lh.drop_tips96(self.tip_rack, offset=Coordinate(0, 1, 0))
    self.backend.drop_tips96.assert_called_once()
    call_kwargs = self.backend.drop_tips96.call_args.kwargs
    self.assertEqual(call_kwargs["drop"].offset, Coordinate(1, 3, 3))

  async def test_default_offset_head96_initializer(self):
    backend = _create_mock_backend(num_channels=8)
    deck = STARLetDeck()
    lh = LiquidHandler(
      backend=backend,
      deck=deck,
      default_offset_head96=Coordinate(1, 2, 3),
    )
    self.assertEqual(lh.default_offset_head96, Coordinate(1, 2, 3))

  async def test_default_offset_head96_serialization(self):
    # Use a real backend for serialization test since mocks can't be deserialized
    backend = LiquidHandlerChatterboxBackend(num_channels=8)
    lh = LiquidHandler(backend=backend, deck=STARLetDeck())
    lh.default_offset_head96 = Coordinate(1, 2, 3)
    data = lh.serialize()
    self.assertEqual(data["default_offset_head96"], serialize(Coordinate(1, 2, 3)))
    new_lh = LiquidHandler.deserialize(data)
    self.assertEqual(new_lh.default_offset_head96, Coordinate(1, 2, 3))

  async def test_with_use_channels(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    with self.lh.use_channels([2]):
      await self.lh.pick_up_tips([tip_spot])
      await self.lh.drop_tips([tip_spot])

    self.backend.pick_up_tips.assert_called_once_with(
      use_channels=[2],
      ops=[Pickup(tip_spot, tip=tip, offset=Coordinate.zero())],
    )
    self.backend.drop_tips.assert_called_once_with(
      use_channels=[2],
      ops=[Drop(tip_spot, tip=tip, offset=Coordinate.zero())],
    )

  async def test_offsets_asp_disp(self):
    well = self.plate.get_item("A1")
    self.plate.get_item("A1").tracker.set_volume(10)
    t = self.tip_rack.get_item("A1").get_tip()
    self.lh.update_head_state({0: t})
    await self.lh.aspirate([well], vols=[10], offsets=[Coordinate(x=1, y=1, z=1)])
    await self.lh.dispense([well], vols=[10], offsets=[Coordinate(x=1, y=1, z=1)])

    self.backend.aspirate.assert_called_once_with(
      use_channels=[0],
      ops=[_make_asp(well, vol=10, offset=Coordinate(x=1, y=1, z=1), tip=t)],
    )
    self.backend.dispense.assert_called_once_with(
      use_channels=[0],
      ops=[_make_disp(well, vol=10, offset=Coordinate(x=1, y=1, z=1), tip=t)],
    )

  async def test_return_tips(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.lh.pick_up_tips([tip_spot])
    await self.lh.return_tips()

    self.backend.drop_tips.assert_called_once_with(
      use_channels=[0],
      ops=[Drop(tip_spot, tip=tip, offset=Coordinate.zero())],
    )

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

    self.backend.drop_tips96.assert_called_once_with(
      drop=DropTipRack(resource=self.tip_rack, offset=Coordinate.zero())
    )

    with self.assertRaises(RuntimeError):
      await self.lh.return_tips()

  async def test_aspirate_dispense96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.aspirate96(self.plate, volume=10)
    await self.lh.dispense96(self.plate, 10)
    self.backend.dispense96.assert_called_with(
      dispense=MultiHeadDispensePlate(
        wells=self.plate.get_all_items(),
        offset=Coordinate.zero(),
        tips=[self.lh.head96[i].get_tip() for i in range(96)],
        volume=10,
        flow_rate=None,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      )
    )

  async def test_dispense96_with_quadrant_well_list(self):
    plate_384 = Revvity_384_wellplate_28ul_Ub(name="plate_384")
    self.deck.assign_child_resource(plate_384, location=Coordinate(400, 100, 0))
    quadrant_wells = plate_384.get_quadrant("tl")

    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.aspirate96(self.plate, volume=10)

    self.lh.backend.dispense96 = unittest.mock.AsyncMock()  # type: ignore
    await self.lh.dispense96(quadrant_wells, 10)
    self.lh.backend.dispense96.assert_called_with(  # type: ignore
      dispense=MultiHeadDispensePlate(
        wells=quadrant_wells,
        offset=Coordinate.zero(),
        tips=[self.lh.head96[i].get_tip() for i in range(96)],
        volume=10,
        flow_rate=None,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      )
    )

  async def test_dispense96_well_list_mixed_parents(self):
    plate2 = Cor_96_wellplate_360ul_Fb(name="plate2")
    self.deck.assign_child_resource(plate2, location=Coordinate(400, 100, 0))
    mixed = self.plate.get_all_items()[:48] + plate2.get_all_items()[:48]
    await self.lh.pick_up_tips96(self.tip_rack)
    with self.assertRaises(ValueError):
      await self.lh.dispense96(mixed, 10)

  async def test_transfer(self):
    t = self.tip_rack.get_item("A1").get_tip()
    self.lh.update_head_state({0: t})

    # Simple transfer
    self.plate.get_item("A1").tracker.set_volume(10)
    await self.lh.transfer(self.plate.get_well("A1"), self.plate["A2"], source_vol=10)

    self.backend.aspirate.assert_called_once_with(
      use_channels=[0],
      ops=[_make_asp(self.plate.get_item("A1"), vol=10.0, tip=t)],
    )
    self.backend.dispense.assert_called_once_with(
      use_channels=[0],
      ops=[_make_disp(self.plate.get_item("A2"), vol=10.0, tip=t)],
    )
    self.backend.aspirate.reset_mock()
    self.backend.dispense.reset_mock()

    # Transfer to multiple wells
    self.plate.get_item("A1").tracker.set_volume(80)
    await self.lh.transfer(self.plate.get_well("A1"), self.plate["A1:H1"], source_vol=80)
    self.backend.aspirate.assert_called_once_with(
      use_channels=[0],
      ops=[_make_asp(self.plate.get_item("A1"), vol=80.0, tip=t)],
    )

    dispense_calls = self.backend.dispense.call_args_list
    expected_dispenses = [
      unittest.mock.call(use_channels=[0], ops=[_make_disp(well, vol=10.0, tip=t)])
      for well in self.plate["A1:H1"]
    ]
    self.assertEqual(dispense_calls, expected_dispenses)
    self.backend.aspirate.reset_mock()
    self.backend.dispense.reset_mock()

    # Transfer with ratios
    self.plate.get_item("A1").tracker.set_volume(60)
    await self.lh.transfer(
      self.plate.get_well("A1"),
      self.plate["B1:C1"],
      source_vol=60,
      ratios=[2, 1],
    )
    self.backend.aspirate.assert_called_once_with(
      use_channels=[0],
      ops=[_make_asp(self.plate.get_item("A1"), vol=60.0, tip=t)],
    )
    dispense_calls = self.backend.dispense.call_args_list
    expected_dispenses = [
      unittest.mock.call(use_channels=[0], ops=[_make_disp(well, vol=vol, tip=t)])
      for well, vol in zip(self.plate["B1:C1"], [40, 20])
    ]
    self.assertEqual(dispense_calls, expected_dispenses)
    self.backend.aspirate.reset_mock()
    self.backend.dispense.reset_mock()

    # Transfer with target_vols
    vols: List[float] = [3, 1, 4, 1, 5, 9, 6, 2]
    self.plate.get_item("A1").tracker.set_volume(sum(vols))
    await self.lh.transfer(self.plate.get_well("A1"), self.plate["A1:H1"], target_vols=vols)
    self.backend.aspirate.assert_called_once_with(
      use_channels=[0],
      ops=[_make_asp(self.plate.get_well("A1"), vol=sum(vols), tip=t)],
    )
    dispense_calls = self.backend.dispense.call_args_list
    expected_dispenses = [
      unittest.mock.call(use_channels=[0], ops=[_make_disp(well, vol=vol, tip=t)])
      for well, vol in zip(self.plate["A1:H1"], vols)
    ]
    self.assertEqual(dispense_calls, expected_dispenses)
    self.backend.aspirate.reset_mock()
    self.backend.dispense.reset_mock()

    # target_vols and source_vol specified
    with self.assertRaises(TypeError):
      await self.lh.transfer(
        self.plate.get_well("A1"),
        self.plate["A1:H1"],
        source_vol=100,
        target_vols=vols,
      )

    # target_vols and ratios specified
    with self.assertRaises(TypeError):
      await self.lh.transfer(
        self.plate.get_well("A1"),
        self.plate["A1:H1"],
        ratios=[1] * 8,
        target_vols=vols,
      )

  async def test_stamp(self):
    # Simple transfer
    await self.lh.pick_up_tips96(self.tip_rack)  # pick up tips first.
    await self.lh.stamp(self.plate, self.plate, volume=10)
    ts = self.tip_rack.get_all_tips()

    self.backend.aspirate96.assert_called_once_with(
      aspiration=MultiHeadAspirationPlate(
        wells=self.plate.get_all_items(),
        volume=10.0,
        tips=ts,
        offset=Coordinate.zero(),
        flow_rate=None,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      )
    )
    self.backend.dispense96.assert_called_once_with(
      dispense=MultiHeadDispensePlate(
        wells=self.plate.get_all_items(),
        volume=10.0,
        tips=ts,
        offset=Coordinate.zero(),
        flow_rate=None,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      )
    )

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

  async def test_get_mounted_tips(self):
    self.assertEqual(self.lh.get_mounted_tips(), [None] * 8)
    await self.lh.pick_up_tips(self.tip_rack["A1", "B1", "C1"])
    mounted = self.lh.get_mounted_tips()
    self.assertIsNotNone(self.tip_rack.get_item("A1").get_tip())
    self.assertIsNotNone(self.tip_rack.get_item("B1").get_tip())
    self.assertIsNotNone(self.tip_rack.get_item("C1").get_tip())
    self.assertIsNone(mounted[3])
    self.assertIsNone(mounted[4])
    self.assertIsNone(mounted[5])
    self.assertIsNone(mounted[6])
    self.assertIsNone(mounted[7])

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
    offsets = get_tight_single_resource_liquid_op_offsets(trash, num_channels=4)

    # drop_tips is called twice: once for pick_up, so check the second call
    drop_tips_calls = self.backend.drop_tips.call_args_list
    self.assertEqual(len(drop_tips_calls), 1)
    self.assertEqual(
      drop_tips_calls[0],
      unittest.mock.call(
        use_channels=[0, 1, 3, 4],
        ops=[
          Drop(
            self.deck.get_trash_area(),
            tip=tips[3],
            offset=offsets[0],
          ),
          Drop(
            self.deck.get_trash_area(),
            tip=tips[2],
            offset=offsets[1],
          ),
          Drop(
            self.deck.get_trash_area(),
            tip=tips[1],
            offset=offsets[2],
          ),
          Drop(
            self.deck.get_trash_area(),
            tip=tips[0],
            offset=offsets[3],
          ),
        ],
      ),
    )

    # test tip tracking
    with self.assertRaises(RuntimeError):
      await self.lh.discard_tips()

  async def test_aspirate_with_lid(self):
    lid = Lid(
      "lid",
      size_x=self.plate.get_size_x(),
      size_y=self.plate.get_size_y(),
      size_z=10,
      nesting_z_height=self.plate.get_size_z(),
    )
    self.plate.assign_child_resource(lid)
    well = self.plate.get_item("A1")
    t = self.tip_rack.get_item("A1").get_tip()
    self.lh.update_head_state({0: t})
    with self.assertRaises(ValueError):
      await self.lh.aspirate([well], vols=[10])

  @pytest.mark.filterwarnings("ignore:Extra arguments to backend")
  async def test_strictness(self):
    # Create a mock backend with a custom pick_up_tips that checks arguments
    async def custom_pick_up_tips(ops, use_channels, non_default, default=True):
      assert non_default == default

    self.backend = _create_mock_backend(num_channels=16)
    self.backend.pick_up_tips = custom_pick_up_tips
    self.lh = LiquidHandler(self.backend, deck=self.deck)
    await self.lh.setup()

    with no_tip_tracking():
      set_strictness(Strictness.IGNORE)
      await self.lh.pick_up_tips(self.tip_rack["A1"], non_default=True)
      await self.lh.pick_up_tips(
        self.tip_rack["A1"],
        use_channels=[1],
        non_default=True,
        does_not_exist=True,
      )
      with self.assertRaises(TypeError):  # missing non_default
        await self.lh.pick_up_tips(self.tip_rack["A1"], use_channels=[2])

      set_strictness(Strictness.WARN)
      await self.lh.pick_up_tips(self.tip_rack["A1"], non_default=True, use_channels=[3])
      with self.assertWarns(UserWarning):  # extra kwargs should warn
        await self.lh.pick_up_tips(
          self.tip_rack["A1"],
          use_channels=[4],
          non_default=True,
          does_not_exist=True,
        )
      self.lh.clear_head_state()
      # We override default to False, so this should raise an assertion error. To test whether
      # overriding default to True works.
      with self.assertRaises(AssertionError):
        await self.lh.pick_up_tips(
          self.tip_rack["A1"],
          use_channels=[4],
          non_default=True,
          does_not_exist=True,
          default=False,
        )
      with self.assertRaises(TypeError):  # missing non_default
        await self.lh.pick_up_tips(self.tip_rack["A1"], use_channels=[5])

      set_strictness(Strictness.STRICT)
      self.lh.clear_head_state()
      await self.lh.pick_up_tips(self.tip_rack["A1"], non_default=True, use_channels=[6])
      with self.assertRaises(TypeError):  # cannot have extra kwargs
        await self.lh.pick_up_tips(
          self.tip_rack["A1"],
          use_channels=[7],
          non_default=True,
          does_not_exist=True,
        )
      with self.assertRaises(TypeError):  # missing non_default
        await self.lh.pick_up_tips(self.tip_rack["A1"], use_channels=[8])

      set_strictness(Strictness.WARN)

  async def test_save_state(self):
    set_volume_tracking(enabled=True)

    # set and save the state
    self.plate.get_item("A2").tracker.set_volume(10)
    state_filename = tempfile.mktemp()
    self.lh.deck.save_state_to_file(fn=state_filename)

    # save the deck
    deck_filename = tempfile.mktemp()
    self.lh.deck.save(fn=deck_filename)

    # create a new liquid handler, load the state and the deck
    backend2 = _create_mock_backend(num_channels=8)
    lh2 = LiquidHandler(backend2, deck=STARLetDeck())
    lh2.deck = Deck.load_from_json_file(json_file=deck_filename)
    lh2.deck.load_state_from_file(fn=state_filename)

    # assert that the state is the same
    well_a1 = lh2.deck.get_resource("plate").get_item("A1")  # type: ignore
    self.assertEqual(well_a1.tracker.volume, 0)
    well_a2 = lh2.deck.get_resource("plate").get_item("A2")  # type: ignore
    self.assertEqual(well_a2.tracker.volume, 10)

    set_volume_tracking(enabled=False)

  async def test_pick_up_tips96_incomplete_rack(self):
    set_tip_tracking(enabled=True)

    # Test that picking up tips from an incomplete rack works
    self.tip_rack.fill()
    self.tip_rack.get_item("A1").tracker.remove_tip()

    await self.lh.pick_up_tips96(self.tip_rack)

    # Check that the tips were picked up correctly
    self.assertFalse(self.lh.head96[0].has_tip)
    for i in range(1, 96):
      self.assertTrue(self.lh.head96[i].has_tip)

    set_tip_tracking(enabled=False)


class TestLiquidHandlerVolumeTracking(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = _create_mock_backend(num_channels=8)
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    self.tip_rack = hamilton_96_tiprack_300uL_filter(name="tip_rack")
    self.deck.assign_child_resource(self.tip_rack, location=Coordinate(0, 0, 0))
    self.plate = Cor_96_wellplate_360ul_Fb(name="plate")
    self.deck.assign_child_resource(self.plate, location=Coordinate(100, 100, 0))
    self.single_well_plate = nest_1_troughplate_195000uL_Vb(name="single_well_plate")
    self.deck.assign_child_resource(self.single_well_plate, location=Coordinate(300, 100, 0))
    await self.lh.setup()
    set_volume_tracking(enabled=True)

  async def asyncTearDown(self):
    set_volume_tracking(enabled=False)

  async def test_dispense_with_volume_tracking(self):
    well = self.plate.get_item("A1")
    await self.lh.pick_up_tips(self.tip_rack["A1"])
    well.tracker.set_volume(10)
    await self.lh.aspirate([well], vols=[10])
    await self.lh.dispense([well], vols=[10])
    self.assertEqual(well.tracker.volume, 10)

  async def test_mix_volume_tracking(self):
    for i in range(8):
      self.plate.get_item(i).set_volume(55)

    await self.lh.pick_up_tips(self.tip_rack[0:8])
    for _ in range(10):
      await self.lh.aspirate(self.plate[0:8], vols=[45] * 8)
      await self.lh.dispense(self.plate[0:8], vols=[45] * 8)

  async def test_channel_1_liquid_tracking(self):
    self.plate.get_item("A1").tracker.set_volume(10)
    with self.lh.use_channels([1]):
      await self.lh.pick_up_tips(self.tip_rack["A1"])
      await self.lh.aspirate([self.plate.get_item("A1")], vols=[10])
      await self.lh.dispense([self.plate.get_item("A2")], vols=[10])

  async def test_dispense_fails(self):
    well = self.plate.get_item("A1")
    await self.lh.pick_up_tips(self.tip_rack["A1"])

    async def error_func(*args, **kwargs):
      raise ChannelizedError(errors={0: Exception("This is an error")})

    self.backend.dispense = error_func  # type: ignore
    well.tracker.set_volume(200)

    await self.lh.aspirate([well], vols=[200])
    assert self.lh.head[0].get_tip().tracker.get_used_volume() == 200
    with self.assertRaises(ChannelizedError):
      await self.lh.dispense([well], vols=[60])
    # test volume doesn't change on failed dispense
    assert self.lh.head[0].get_tip().tracker.get_used_volume() == 200

  async def test_96_head_volume_tracking_multi_container(self):
    for item in self.plate.get_all_items():
      item.tracker.set_volume(10)
    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.aspirate96(self.plate, volume=10)
    for i in range(96):
      self.assertEqual(self.lh.head96[i].get_tip().tracker.get_used_volume(), 10)
      self.plate.get_item(i).tracker.get_used_volume() == 0
    await self.lh.dispense96(self.plate, volume=10)
    for i in range(96):
      self.assertEqual(self.lh.head96[i].get_tip().tracker.get_used_volume(), 0)
      self.plate.get_item(i).tracker.get_used_volume() == 10
    await self.lh.return_tips96()

  async def test_96_head_volume_tracking_single_container(self):
    well = self.single_well_plate.get_item(0)
    well.tracker.set_volume(10 * 96)
    await self.lh.pick_up_tips96(self.tip_rack)

    await self.lh.aspirate96(self.single_well_plate, volume=10)
    assert all(self.lh.head96[i].get_tip().tracker.get_used_volume() == 10 for i in range(96))
    assert all(self.lh.head96[i].get_tip().tracker.volume == 10 for i in range(96))
    assert well.tracker.get_used_volume() == 0

    await self.lh.dispense96(self.single_well_plate, volume=10)
    assert all(self.lh.head96[i].get_tip().tracker.get_used_volume() == 0 for i in range(96))
    assert all(self.lh.head96[i].get_tip().tracker.volume == 0 for i in range(96))
    assert well.tracker.get_used_volume() == 10 * 96

    await self.lh.return_tips96()

  async def test_96_head_volume_tracking_well_list(self):
    plate_384 = Revvity_384_wellplate_28ul_Ub(name="plate_384")
    self.deck.assign_child_resource(plate_384, location=Coordinate(600, 100, 0))
    quadrant_wells = plate_384.get_quadrant("tl")
    for well in quadrant_wells:
      well.tracker.set_volume(10)

    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.aspirate96(quadrant_wells, volume=10)
    assert all(self.lh.head96[i].get_tip().tracker.get_used_volume() == 10 for i in range(96))
    assert all(w.tracker.get_used_volume() == 0 for w in quadrant_wells)

    await self.lh.dispense96(quadrant_wells, volume=10)
    assert all(self.lh.head96[i].get_tip().tracker.get_used_volume() == 0 for i in range(96))
    assert all(w.tracker.get_used_volume() == 10 for w in quadrant_wells)
    await self.lh.return_tips96()
