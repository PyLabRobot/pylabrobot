"""Handles over the test deck: each answers what PLR's own resource classes answer for the same
deck, and each change is made in the store, where every handle sees it."""

from __future__ import annotations

import inspect
import unittest
from typing import Any, Callable, Dict, List, Tuple

from pylabrobot.events import (
  EventBus,
  PLREvent,
  handle_reference,
  resource_reference,
  use_event_bus,
)
from pylabrobot.resources import Coordinate
from pylabrobot.resources.carrier import Carrier, PlateCarrier
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb
from pylabrobot.resources.hamilton.hamilton_decks import HamiltonDeck, track_for_x_coordinate
from pylabrobot.resources.hamilton.plate_carriers import hamilton_plate_carrier_L5_ac
from pylabrobot.resources.plate import Plate as PLRPlate
from pylabrobot.resources.resource import Resource as PLRResource
from pylabrobot.resources.server import build
from pylabrobot.resources.server import handles as h
from pylabrobot.resources.server.definitions import kind_of
from pylabrobot.resources.server.errors import ServerError
from pylabrobot.resources.server.tests.server_tests import ROOT, ServerTestCase, build_deck
from pylabrobot.resources.server.v1 import server_pb2 as spb
from pylabrobot.resources.server.v1 import types_pb2 as pb
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.well import Well as PLRWell


def by_kind(data: Any, plr: PLRResource) -> Any:
  """PLR's event data with each resource's type the handle class its kind is, as a store names it:
  the store keeps a resource's kind, not the PLR class it was built as."""
  classes: Dict[str, str] = {}
  for resource in [plr, *plr.get_all_children()]:
    classes[type(resource).__name__] = h.HANDLE_TYPES[kind_of(resource)].__name__

  def visit(value: Any) -> Any:
    if isinstance(value, dict):
      made = {key: visit(each) for key, each in value.items()}
      if isinstance(made.get("type"), str) and "name" in made:
        made["type"] = classes.get(made["type"], made["type"])
      return made
    if isinstance(value, (list, tuple)):
      return type(value)(visit(each) for each in value)
    return value

  return visit(data)


class HandleTestCase(ServerTestCase):
  """The test deck in a store, and the same deck built with PLR's classes beside it."""

  async def asyncSetUp(self) -> None:
    await super().asyncSetUp()
    self.plr = build_deck()
    self.deck = await self.server.get(ROOT, kind=h.Deck)


class ReadingTests(HandleTestCase):
  async def test_a_handle_has_its_kind_and_name(self):
    plate = await self.server.get("plate", root=self.deck)
    self.assertIsInstance(plate, h.Plate)
    self.assertEqual(plate.name, "plate")
    self.assertEqual(plate.kind, pb.RESOURCE_KIND_PLATE)
    self.assertIsInstance(self.deck, h.Deck)
    self.assertEqual(await self.server.get("plate", root=ROOT), plate)

  async def test_items_are_known_without_asking(self):
    plate = await self.server.get("plate", root=self.deck, kind=h.Plate)
    plr = self.plr.get_resource("plate")
    assert isinstance(plr, PLRPlate)
    self.assertEqual(plate.num_items, 96)
    self.assertEqual((plate.num_items_x, plate.num_items_y), (12, 8))
    for key in ("A1", 5, (2, 3), "A1:H1", slice(0, 8), slice("A1", "A2"), [0, 95], range(3)):
      with self.subTest(key=key):
        self.assertEqual([w.name for w in plate[key]], [w.name for w in plr[key]])  # type: ignore
    self.assertEqual(plate.get_well("H12").name, plr.get_well("H12").name)
    self.assertEqual(
      [w.name for w in plate.get_wells("A1:C1")],
      ["plate_well_A1", "plate_well_B1", "plate_well_C1"],
    )
    with self.assertRaises(IndexError):
      plate.get_item("Z99")

  async def test_locations_and_sizes_are_plr_s(self):
    for name in ("plate", "plate_well_C4", "tips_tipspot_H12", "trough", "shaft_2"):
      handle = await self.server.get(name, root=self.deck)
      plr = self.plr.get_resource(name)
      with self.subTest(resource=name):
        for x, y, z in (("l", "f", "b"), ("c", "c", "t"), ("r", "b", "c")):
          ours = await handle.get_absolute_location(x, y, z)
          self.assertEqual(ours, plr.get_absolute_location(x, y, z))
        ours = await handle.get_location_wrt(self.deck, "c", "c", "b")
        self.assertEqual(ours, plr.get_location_wrt(self.plr, "c", "c", "b"))
        self.assertEqual(
          await handle.get_size(), Coordinate(plr.get_size_x(), plr.get_size_y(), plr.get_size_z())
        )
        self.assertEqual(await handle.get_location(), plr.location)

  async def test_the_tree_is_walked_through_the_store(self):
    well = await self.server.get("plate_well_A1", root=self.deck)
    plate = await well.get_parent()
    self.assertIsInstance(plate, h.Plate)
    self.assertEqual(await well.get_root(), self.deck)
    carrier = await self.deck.get_resource("plate_carrier", kind=h.Carrier)
    self.assertIsInstance(carrier, h.Carrier)
    self.assertEqual(carrier.capacity, 5)
    site = carrier[0]
    self.assertIsInstance(site, h.PlateHolder)
    self.assertEqual(await site.get_resource_held(), plate)
    self.assertIsNone(await carrier[4].get_resource_held())  # type: ignore
    self.assertIn(plate, await site.get_children())
    self.assertTrue(await well.is_in_subtree_of(carrier))
    wells = await carrier.find_resources(kinds=[pb.RESOURCE_KIND_WELL])
    self.assertEqual(len(wells), 192)
    with self.assertRaises(ServerError):
      await carrier.get_resource("tips")

  async def test_volumes_and_heights(self):
    plate = await self.server.get("plate", root=self.deck, kind=h.Plate)
    await plate.set_well_volumes([10.0 * i for i in range(96)])
    well = plate.get_well("B1")
    self.assertEqual(await well.get_volume(), 10.0)
    await well.set_volume(100)
    self.assertEqual(await well.get_volume(), 100)
    self.assertEqual(await well.get_free_volume(), 260)
    plr = self.plr.get_resource("plate_well_B1")
    assert isinstance(plr, PLRWell)
    height = await well.compute_height_from_volume(100)
    self.assertAlmostEqual(height, plr.compute_height_from_volume(100), places=6)
    self.assertAlmostEqual(await well.compute_volume_from_height(height), 100, places=6)

  async def test_tips_are_there_until_emptied(self):
    tips = await self.server.get("tips", root=self.deck, kind=h.TipRack)
    spot = tips.get_tip_spot("A1")
    self.assertTrue(await spot.has_tip())
    tip = await spot.get_tip()
    self.assertIsInstance(tip, h.Tip)
    await tips.empty()
    self.assertFalse(await spot.has_tip())
    await tips.set_tip_state([i % 2 == 0 for i in range(96)])
    self.assertTrue(await tips.get_tip_spot(0).has_tip())
    self.assertFalse(await tips.get_tip_spot(1).has_tip())


class EventTests(HandleTestCase):
  async def test_a_handle_is_referred_to_as_plr_s_classes_are(self):
    for name in ("plate_well_B3", "tips", "shaft_1"):
      handle = await self.server.get(name, root=self.deck)
      self.assertEqual(
        await handle_reference(handle),
        by_kind(resource_reference(self.plr.get_resource(name)), self.plr),
        name,
      )
    self.assertIsNone(await handle_reference(None))

  async def test_moves_emit_what_plr_s_classes_emit(self):
    def recorded(bus: EventBus) -> List[PLREvent]:
      events: List[PLREvent] = []
      bus.subscribe(events.append)
      return events

    plr_events = recorded(plr_bus := EventBus())
    plr_carrier = self.plr.get_resource("plate_carrier")
    assert isinstance(plr_carrier, PlateCarrier)
    plr_plate = self.plr.get_resource("plate")
    with use_event_bus(plr_bus):
      plr_plate.unassign()
      plr_carrier[3].assign_child_resource(plr_plate)
      plr_plate.unassign()
      plr_carrier[4].assign_child_resource(plr_plate)

    # A handle moves a resource in one assignment, and emits both events of the move.
    events = recorded(bus := EventBus())
    carrier = await self.deck.get_resource("plate_carrier", kind=h.Carrier)
    plate = await self.server.get("plate", root=self.deck)
    with use_event_bus(bus):
      await plate.unassign()
      await carrier[3].assign_child_resource(plate)
      await carrier[4].assign_child_resource(plate)

    self.assertEqual(
      [(e.name, e.data) for e in events],
      [(e.name, by_kind(e.data, self.plr)) for e in plr_events],
    )
    self.assertEqual(
      [e.name for e in events],
      ["resource.unassigned", "resource.assigned"] * 2,
    )

  async def events_of(self, run: Callable[[], Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """The events `run` emits, by name and data; awaited when it returns an awaitable."""
    events: List[PLREvent] = []
    bus = EventBus()
    bus.subscribe(events.append)
    with use_event_bus(bus):
      result = run()
      if inspect.isawaitable(result):
        await result
    return [(event.name, event.data) for event in events]

  async def test_taking_and_making_tips_emits_what_plr_s_classes_do(self):
    tips = await self.server.get("tips", root=self.deck, kind=h.TipRack)
    spot = self.plr.get_resource("tips_tipspot_A1")
    assert isinstance(spot, TipSpot)
    ours = await self.events_of(lambda: tips.set_tip_state([i != 0 for i in range(tips.num_items)]))
    self.assertEqual(ours, by_kind(await self.events_of(spot.unassign_tip), self.plr))
    ours = await self.events_of(tips.fill)
    made = await self.events_of(lambda: spot.assign_tip(spot.make_tip()))
    self.assertEqual(ours, by_kind(made, self.plr))
    ours = await self.events_of(tips.empty)
    self.assertEqual(len(ours), tips.num_items)
    self.assertEqual({name for name, _ in ours}, {"resource.unassigned"})

  async def test_creating_and_deleting_in_a_parent_emits_assignments(self):
    carrier = await self.server.get("plate_carrier", root=self.deck, kind=h.Carrier)
    events = await self.events_of(
      lambda: self.server.create("cor_96_wellplate_360uL_Fb", name="new_plate", parent=carrier[4])
    )
    self.assertEqual([name for name, _ in events], ["resource.assigned"])
    self.assertEqual(events[0][1]["parent"]["name"], "plate_carrier-4")
    plate = await self.server.get("new_plate", root=self.deck)
    events = await self.events_of(plate.delete)
    self.assertEqual([name for name, _ in events], ["resource.unassigned"])
    self.assertEqual(events[0][1]["previous_parent"]["name"], "plate_carrier-4")


class DeckTests(HandleTestCase):
  async def test_tracks_are_plr_s(self):
    plr = self.plr
    assert isinstance(plr, HamiltonDeck)
    for track in (1, 8, 30):
      self.assertEqual(await self.deck.track_location(track), plr.track_to_location(track))
    for x in (100.0, 122.4, 122.6, 700.0):
      self.assertEqual(await self.deck.track_at(x), track_for_x_coordinate(x))
    for name in ("plate_carrier", "tip_carrier", "trough_carrier"):
      carrier = await self.deck.get_resource(name)
      plr_carrier = plr.get_resource(name)
      assert isinstance(plr_carrier, Carrier)
      first, last = await self.deck.tracks_of(carrier)
      self.assertEqual(last, plr.compute_right_track_of_carrier(plr_carrier), name)
      self.assertEqual(first, track_for_x_coordinate(plr_carrier.get_location_wrt(plr).x), name)


class ChangingTests(HandleTestCase):
  async def test_a_plr_resource_is_made_where_it_is_assigned(self):
    # PLR puts the same plate in the same site, for where it and its wells end up.
    plr_carrier = self.plr.get_resource("plate_carrier")
    assert isinstance(plr_carrier, PlateCarrier)
    plr_plate = cor_96_wellplate_360uL_Fb(name="new_plate")
    plr_carrier[3].assign_child_resource(plr_plate)

    loose = cor_96_wellplate_360uL_Fb(name="new_plate")
    loose.get_well("A1").tracker.set_volume(50)
    carrier = await self.deck.get_resource("plate_carrier", kind=h.Carrier)
    plate = await carrier[3].assign_child_resource(loose)

    self.assertEqual(plate.name, "new_plate")
    self.assertEqual(await plate.get_parent(), carrier[3])
    self.assertEqual(await self.server.get("new_plate", root=self.deck), plate)
    self.assertEqual(plate.num_items, 96)
    self.assertEqual(await plate.get_absolute_location(), plr_plate.get_absolute_location())
    for well in ("A1", "H12"):
      self.assertEqual(
        await plate.get_well(well).get_absolute_location(),
        plr_plate.get_well(well).get_absolute_location(),
        well,
      )
    self.assertEqual(await plate.get_well("A1").get_volume(), 50)
    # The object assigned is left as it was, in no tree.
    self.assertIsNone(loose.parent)

  async def test_a_handle_assigned_comes_back(self):
    carrier = await self.deck.get_resource("plate_carrier", kind=h.Carrier)
    plate = await self.server.get("plate", root=self.deck)
    self.assertIs(await carrier[3].assign_child_resource(plate), plate)

  async def test_a_site_of_a_carrier_on_a_server_is_not_set_by_index(self):
    carrier = await self.deck.get_resource("plate_carrier", kind=h.Carrier)
    with self.assertRaisesRegex(TypeError, r"await carrier\[3\]\.assign_child_resource"):
      carrier[3] = cor_96_wellplate_360uL_Fb(name="new_plate")

  async def test_labware_is_made_from_plr_s_models(self):
    carrier = await self.deck.get_resource("plate_carrier", kind=h.Carrier)
    plate = await self.server.create(
      "cor_96_wellplate_360uL_Fb", name="fresh", parent=carrier[2], kind=h.Plate
    )
    self.assertIsInstance(plate, h.Plate)
    self.assertEqual(plate.get_well("H12").name, "fresh_well_H12")
    plr_carrier = self.plr.get_resource("plate_carrier")
    assert isinstance(plr_carrier, PlateCarrier)
    plr_carrier[2] = cor_96_wellplate_360uL_Fb(name="fresh")
    self.assertEqual(
      await plate.get_well("H12").get_absolute_location("c", "c", "cavity_bottom"),
      self.plr.get_resource("fresh_well_H12").get_absolute_location("c", "c", "cavity_bottom"),
    )

  async def test_a_plate_moves_by_hand(self):
    plate = await self.server.get("plate", root=self.deck)
    carrier = await self.deck.get_resource("plate_carrier", kind=h.Carrier)
    await carrier[3].assign_child_resource(plate)
    self.assertEqual(await plate.get_parent(), carrier[3])
    await plate.unassign()
    self.assertIsNone(await plate.get_parent())
    self.assertIsNone(await plate.get_location())
    self.assertIn(plate, await self.server.roots())
    await self.deck.assign_child_resource(plate, location=Coordinate(600, 100, 0))
    self.assertEqual(await plate.get_location(), Coordinate(600, 100, 0))

  async def test_a_root_with_no_location_still_has_a_size(self):
    plate = await self.server.create("cor_96_wellplate_360uL_Fb", name="loose", kind=h.Plate)
    self.assertEqual(await plate.get_size(), Coordinate(127.76, 85.48, 14.2))
    self.assertEqual((await plate.get_absolute_rotation()).z, 0)
    with self.assertRaises(ServerError) as caught:
      await plate.get_absolute_location()
    self.assertEqual(caught.exception.problem, pb.PROBLEM_NO_LOCATION)
    self.assertEqual(
      await plate.get_well("A1").get_location_wrt(plate), Coordinate(10.87, 70.77, 3.03)
    )

  async def test_turning_and_metadata(self):
    plate = await self.server.get("plate", root=self.deck)
    await plate.unassign()
    await plate.rotate(z=90)
    self.assertEqual((await plate.get_rotation()).z, 90)
    await plate.rotate_to(z=0)
    self.assertEqual((await plate.get_rotation()).z, 0)
    await plate.set_metadata({"owner": "lab"})
    await plate.set_metadata({"run": 3})
    self.assertEqual(await plate.get_metadata(), {"owner": "lab", "run": 3})
    await plate.set_barcode(pb.Barcode(data="123", symbology="code128"))
    barcode = await plate.get_barcode()
    assert barcode is not None
    self.assertEqual(barcode.data, "123")

  async def test_a_preferred_pickup_location_is_set_and_cleared(self):
    plate = await self.server.get("plate", root=self.deck)
    self.assertIsNone(await plate.get_preferred_pickup_location())
    await plate.set_preferred_pickup_location(Coordinate(63.88, 42.74, 5))
    self.assertEqual(await plate.get_preferred_pickup_location(), Coordinate(63.88, 42.74, 5))
    await plate.set_preferred_pickup_location(None)
    self.assertIsNone(await plate.get_preferred_pickup_location())

  async def test_a_device_builds_its_parts_from_definitions(self):
    body = build.resource(
      "reader",
      Coordinate(150, 100, 80),
      category="plate_reader",
      children=[
        build.resource(
          "reader_tray",
          Coordinate(127.76, 85.48, 5),
          kind=pb.RESOURCE_KIND_PLATE_HOLDER,
          location=Coordinate(10, 7, 3),
          holder=pb.HolderSpec(child_location=build.vec3(Coordinate(0, 0, 2))),
        )
      ],
    )
    reader = await self.server.create(
      body, name="reader", parent=self.deck, location=Coordinate(700, 50, 0)
    )
    tray = await reader.get_resource("reader_tray")
    self.assertIsInstance(tray, h.PlateHolder)
    plate = await self.server.get("plate", root=self.deck)
    await tray.assign_child_resource(plate)
    self.assertEqual(await plate.get_location_wrt(self.deck), Coordinate(710, 57, 5))

  async def test_deleting_takes_everything_under_it(self):
    plate = await self.server.get("plate", root=self.deck)
    await plate.delete()
    with self.assertRaises(ServerError):
      await self.server.get("plate_well_A1", root=self.deck)


if __name__ == "__main__":
  unittest.main()


class KindTests(HandleTestCase):
  """What a handle is typed as is what it is: lookups take the kind expected, and check it."""

  async def test_a_lookup_given_a_kind_gives_it_or_refuses(self):
    plate = await self.server.get("plate", root=self.deck, kind=h.Plate)
    self.assertIsInstance(plate, h.Plate)
    self.assertEqual(await self.server.get_by_id(plate.id, h.Plate), plate)
    with self.assertRaisesRegex(TypeError, "'plate' is a Plate, not a TipRack"):
      await self.server.get("plate", root=self.deck, kind=h.TipRack)
    with self.assertRaises(TypeError):
      await self.deck.get_resource("plate", kind=h.Carrier)
    holder = await plate.get_parent(h.PlateHolder)
    self.assertIsInstance(holder, h.PlateHolder)
    assert holder is not None
    self.assertEqual(await holder.get_resource_held(h.Plate), plate)
    self.assertEqual(await plate.get_root(h.Deck), self.deck)

  async def test_a_list_given_a_kind_is_only_those(self):
    carriers = await self.deck.get_children(h.Carrier)
    self.assertEqual(
      sorted(c.name for c in carriers), ["plate_carrier", "tip_carrier", "trough_carrier"]
    )
    plates = await self.deck.find_resources(kind=h.Plate)
    self.assertEqual(sorted(p.name for p in plates), ["other_plate", "plate"])
    containers = await self.server.find_resources(
      spb.ResourceFilter(), scope=self.deck, kind=h.Container
    )
    self.assertTrue(containers)
    self.assertTrue(all(isinstance(c, h.Container) for c in containers))
    self.assertIn("trough", [c.name for c in containers])

  async def test_a_plr_resource_comes_back_as_its_class(self):
    carrier = await self.deck.assign_child_resource(
      hamilton_plate_carrier_L5_ac(name="typed_carrier"), track=22
    )
    self.assertIsInstance(carrier, h.Carrier)
    self.assertTrue(all(isinstance(site, h.PlateHolder) for site in carrier.sites))
    plate = await carrier[0].assign_child_resource(cor_96_wellplate_360uL_Fb(name="typed"))
    self.assertIsInstance(plate, h.Plate)
    self.assertTrue(all(isinstance(well, h.Well) for well in plate.items))
    self.assertEqual(
      await self.deck.assign_child_resource(plate, location=Coordinate(0, 0, 0)), plate
    )

  def test_handles_nest_as_plr_s_classes_do(self):
    self.assertTrue(issubclass(h.Tip, h.HeadTool))
    self.assertTrue(issubclass(h.MechanicalGripper, h.LinkBody))
    self.assertTrue(issubclass(h.EmbeddedTipRackHolder, h.ResourceHolder))
    self.assertTrue(issubclass(h.TubeRack, h.ContainerRack))
    self.assertTrue(issubclass(h.ContainerRack, h.ItemizedResource))
    self.assertIn(pb.RESOURCE_KIND_TRASH, h.kinds_of(h.Container))
    self.assertNotIn(pb.RESOURCE_KIND_PLATE, h.kinds_of(h.Container))
