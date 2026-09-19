import textwrap
import unittest
from typing import cast

from pylabrobot.resources import Coordinate, Deck, Resource, TipRack
from pylabrobot.resources.corning import (
  cor_96_wellplate_360uL_Fb,
)
from pylabrobot.resources.hamilton import (
  PLT_CAR_L5AC_A00,
  TIP_CAR_480_A00,
  HamiltonDeck,
  HamiltonSTARDeck,
  STARDeck,
  STARLetDeck,
  hamilton_96_tiprack_300uL_filter,
  hamilton_96_tiprack_1000uL_filter,
)
from pylabrobot.resources.stanley.cups import (
  StanleyCup_QUENCHER_FLOWSTATE_TUMBLER,
)


class HamiltonDeckTests(unittest.TestCase):
  def test_rails_is_deprecated(self):
    """`rails` still places a resource, and says it is deprecated."""
    deck = STARLetDeck()
    with self.assertWarns(DeprecationWarning):
      deck.assign_child_resource(TIP_CAR_480_A00(name="tip_carrier"), rails=1)
    self.assertEqual(
      deck.get_resource("tip_carrier").get_location_wrt(deck).x,
      deck.track_to_location(1).x,
    )

  def test_track_and_rails_together_is_refused(self):
    """Passing both is a mistake rather than a preference."""
    deck = STARLetDeck()
    with self.assertRaises(ValueError):
      deck.assign_child_resource(TIP_CAR_480_A00(name="tip_carrier"), track=1, rails=1)

  def test_num_tracks_and_num_rails_together_is_refused(self):
    class OwnDeck(HamiltonDeck):
      def track_to_location(self, track: int) -> Coordinate:
        return Coordinate(100.0 + (track - 1) * 22.5, 63, 100)

    with self.assertRaises(ValueError):
      OwnDeck(num_tracks=30, num_rails=30, size_x=1000, size_y=600, size_z=300)

  def test_a_deck_saved_with_num_rails_loads_with_the_tracks_it_has(self):
    """A saved STAR deck counted two more rails than it has tracks."""
    for factory, tracks in ((STARLetDeck, 30), (STARDeck, 54)):
      data = factory().serialize()
      data["num_rails"] = data.pop("num_tracks") + 2
      with self.assertWarns(DeprecationWarning):
        deck = Deck.deserialize(data)
      self.assertEqual(cast(HamiltonDeck, deck).num_tracks, tracks)

  def test_num_rails_counts_as_it_did(self):
    with self.assertWarns(DeprecationWarning):
      self.assertEqual((STARLetDeck().num_rails, STARDeck().num_rails), (32, 56))

  def test_rails_is_bounded_as_it_was_and_track_by_the_tracks(self):
    deck = STARLetDeck()
    with self.assertWarns(DeprecationWarning):
      deck.assign_child_resource(Resource("front", size_x=20, size_y=20, size_z=20), rails=32)
    with self.assertRaises(ValueError):
      deck.assign_child_resource(Resource("front_2", size_x=20, size_y=20, size_z=20), track=32)

  def test_names_the_module_had_still_import(self):
    from pylabrobot.resources.hamilton import core_grippers, hamilton_decks, star_decks

    for name in ("HamiltonSTARDeck", "STARDeck", "STARLetDeck"):
      self.assertIs(getattr(hamilton_decks, name), getattr(star_decks, name))
    self.assertIs(
      hamilton_decks.hamilton_core_gripper_1000ul_at_waste,
      core_grippers.hamilton_core_gripper_1000ul_at_waste,
    )
    self.assertEqual((hamilton_decks.STARLET_NUM_RAILS, hamilton_decks.STAR_NUM_RAILS), (32, 56))

  def test_hamilton_deck_takes_its_track_count_first_as_it_took_rails(self):
    class OwnDeck(HamiltonDeck):
      def track_to_location(self, track: int) -> Coordinate:
        return Coordinate(100.0 + (track - 1) * 22.5, 63, 100)

    deck = OwnDeck(30, 1000, 600, 300)
    self.assertEqual((deck.num_tracks, deck.get_size_x()), (30, 1000))

  def test_a_deck_implementing_rails_to_location_still_places_by_track(self):
    class RailsDeck(HamiltonDeck):
      def rails_to_location(self, rails: int) -> Coordinate:
        return Coordinate(100.0 + (rails - 1) * 22.5, 63, 100)

    deck = RailsDeck(30, 1000, 600, 300)
    self.assertEqual(deck.track_to_location(3), Coordinate(145.0, 63, 100))
    deck.assign_child_resource(TIP_CAR_480_A00(name="tip_carrier"), track=3)
    self.assertEqual(deck.get_resource("tip_carrier").location, Coordinate(145.0, 63, 100))

  def test_a_deck_implementing_neither_location_method_is_refused(self):
    class NoTracksDeck(HamiltonDeck):
      pass

    with self.assertRaises(TypeError):
      NoTracksDeck(30, 1000, 600, 300)

  """Tests for the HamiltonDeck class."""

  def build_layout(self):
    """Build a deck layout for testing"""
    deck = STARLetDeck()

    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = hamilton_96_tiprack_300uL_filter(name="tip_rack_01")
    tip_car[1] = hamilton_96_tiprack_300uL_filter(name="tip_rack_02")
    tip_car[3] = hamilton_96_tiprack_1000uL_filter(name="tip_rack_04")

    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = cor_96_wellplate_360uL_Fb(name="aspiration plate")
    plt_car[2] = cor_96_wellplate_360uL_Fb(name="dispense plate")

    deck.assign_child_resource(tip_car, track=1)
    deck.assign_child_resource(plt_car, track=21)

    return deck

  def test_summary(self):
    self.maxDiff = None
    deck = self.build_layout()
    self.assertEqual(
      deck.summary(),
      textwrap.dedent(
        """
    Rail  Resource                                Type                  Coordinates (mm)
    ==================================================================================================
    (-6)  ├── STARlet_trash_core96                Trash                 (-58.200, 106.000, 216.400)
          │
    (1)   ├── tip_carrier                         TipCarrier            (100.000, 063.000, 100.000)
          │   ├── tip_rack_01                     EmbeddedTipRack       (106.200, 073.000, 208.950)
          │   ├── tip_rack_02                     EmbeddedTipRack       (106.200, 169.000, 208.950)
          │   ├── <empty>
          │   ├── tip_rack_04                     EmbeddedTipRack       (106.200, 361.000, 208.950)
          │   ├── <empty>
          │
    (21)  ├── plate carrier                       PlateCarrier          (550.000, 063.000, 100.000)
          │   ├── aspiration plate                Plate                 (554.000, 071.500, 183.120)
          │   ├── <empty>
          │   ├── dispense plate                  Plate                 (554.000, 263.500, 183.120)
          │   ├── <empty>
          │   ├── <empty>
          │
    (31)  ├── STARlet_waste_block                 Resource              (775.000, 115.000, 100.000)
          │   ├── STARlet_teaching_needle_rack    TipRack               (780.900, 461.100, 100.000)
          │   ├── STARlet_core_gripper_holder     HamiltonCoreGrippers  (778.000, 085.500, 200.500)
          │
    (32)  ├── STARlet_trash                       Trash                 (800.000, 190.600, 137.100)
    """[1:]
      ),
    )

  def test_teaching_rack_excluded_by_nominal_volume_search(self):
    """The built-in teaching rack holds teaching needles (nominal_volume 0), so searching
    the deck's tipracks by tip nominal_volume excludes it: the five added 300 uL racks
    match, the teaching rack does not."""
    deck = STARLetDeck()
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    for i in range(5):
      tip_car[i] = hamilton_96_tiprack_300uL_filter(name=f"tip_rack_0{i}")
    deck.assign_child_resource(tip_car, track=1)

    tip_racks = [r for r in deck.get_all_children() if isinstance(r, TipRack)]
    matches = [
      tr
      for tr in tip_racks
      if (tip := next((ts.get_tip() for ts in tr.get_all_items() if ts.has_tip()), None))
      and tip.nominal_volume == 300
    ]

    self.assertEqual(len(tip_racks), 6)  # 5 added 300 uL racks + the built-in teaching rack
    self.assertEqual(len(matches), 5)
    self.assertNotIn(deck.teaching_needle_rack, matches)

  def test_get_trash_area96_survives_serialization_round_trip(self):
    """`serialize()` encodes the 96 trash as a child and sets `with_trash96=False`, so the
    rebuilt deck never runs the `with_trash96` branch in `__init__`. `get_trash_area96()` must
    still resolve it from the child tree rather than raising."""
    deck = self.build_layout()
    original_location = deck.get_trash_area96().get_location_wrt(deck)

    deck2 = cast(HamiltonSTARDeck, Deck.deserialize(deck.serialize()))

    self.assertIsNotNone(deck2.trash96)
    self.assertEqual(deck2.get_trash_area96().get_location_wrt(deck2), original_location)

  def test_get_trash_area96_raises_after_clear_include_trash(self):
    """`clear(include_trash=True)` unassigns the 96 trash, so `get_trash_area96()` must raise
    rather than hand back the now-orphaned resource."""
    deck = self.build_layout()
    deck.get_trash_area96()  # resolves while still assigned

    deck.clear(include_trash=True)

    self.assertIsNone(deck.trash96)
    with self.assertRaises(RuntimeError):
      deck.get_trash_area96()

  def test_assign_gigantic_resource(self):
    stanley_cup = StanleyCup_QUENCHER_FLOWSTATE_TUMBLER(name="HUGE")
    deck = STARLetDeck()
    with self.assertLogs("pylabrobot") as log:
      deck.assign_child_resource(stanley_cup, track=1)
    self.assertEqual(
      log.output,
      [
        "WARNING:pylabrobot.resources.hamilton.hamilton_decks:Resource 'HUGE' is very high on the deck: 412.42 mm. Be "
        "careful when traversing the deck.",
        "WARNING:pylabrobot.resources.hamilton.hamilton_decks:Resource 'HUGE' is very high on the deck: 412.42 mm. Be "
        "careful when grabbing this resource.",
      ],
    )

  def test_core_grippers_on_waste_as_probed(self):
    # Probed on a STAR: holder top 220.0, 19.5 mm tall, tool tops (collar tops) 235.0. The collars
    # stand where the legacy pick-up sends the channels: x on the holder's centre, y 107.0 and 125.0.
    for deck, x in ((STARDeck(), 1337.5), (STARLetDeck(), 797.5)):
      with self.subTest(deck=type(deck).__name__):
        holder = deck.core_gripper_holder
        assert holder is not None
        self.assertAlmostEqual(holder.get_location_wrt(deck, x="c").x, x)
        self.assertAlmostEqual(holder.get_location_wrt(deck).z, 200.5)
        self.assertAlmostEqual(holder.get_location_wrt(deck, z="t").z, 220.0)
        front, back = holder.children[0], holder.children[1]
        for tool in (front, back):
          self.assertAlmostEqual(tool.get_location_wrt(deck, x="c", z="t").x, x)
          self.assertAlmostEqual(tool.get_location_wrt(deck, z="t").z, 235.0)
        # the collar is 4.25 mm from the side of the tool its pins face
        self.assertAlmostEqual(front.get_location_wrt(deck).y, 107.0 - 4.25)
        # the back tool is turned 180 degrees, so its own front faces the back of the deck
        self.assertAlmostEqual(back.get_location_wrt(deck, y="f").y, 125.0 + 4.25)

  def test_two_decks_stand_in_one_tree(self):
    """A deck names what it owns after itself, so two of them stand in one tree."""
    lab = Resource(name="lab", size_x=4000, size_y=2000, size_z=1000)
    first = STARLetDeck(name="left_Deck")
    second = STARLetDeck(name="right_Deck")
    lab.assign_child_resource(first, location=Coordinate.zero())
    lab.assign_child_resource(second, location=Coordinate(2000, 0, 0))

    names = [r.name for r in lab.get_all_children()]
    self.assertEqual(len(names), len(set(names)))
    for deck, prefix in ((first, "left"), (second, "right")):
      for resource in (
        deck.waste_block,
        deck.trash,
        deck.trash96,
        deck.teaching_needle_rack,
        deck.core_gripper_holder,
      ):
        assert resource is not None
        self.assertTrue(resource.name.startswith(f"{prefix}_"), resource.name)
