"""What pipette and head commands sweep through, on a simulated STARlet's demo deck."""

import unittest

from pylabrobot.hamilton.star.driver.features.star_collisions import (
  check_head_move,
  check_pipette_move,
)
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.lid import Lid
from pylabrobot.resources.tip_rack import TipRack
from pylabrobot.resources.tip_tracking import does_tip_tracking, set_tip_tracking
from pylabrobot.visualizer3D.demo import build_facility, star_of


class StarCollisionTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    was = does_tip_tracking()
    set_tip_tracking(True)
    self.addCleanup(set_tip_tracking, was)
    self.facility = build_facility()
    self.star = star_of(self.facility)
    self.deck = self.star.deck
    self.deck.get_resource("destination_1").unassign()
    await self.star.setup()
    assert self.star.pipettes is not None and self.star.head96 is not None
    self.pipettes = self.star.pipettes
    self.head = self.star.head96

  async def test_a_row_of_channels_moved_across_the_deck_is_caught_when_lowered(self):
    # The channels ride as one row when they are commanded the same way: let off each other, judged
    # against the world. At Z safety the row moved towards the front is clear; lowered over the
    # deck, the same move sweeps through the plate carrier and the racks; raised again, it is clear.
    refs = []
    for channel in range(8):
      here = self.pipettes.get_reference_point_location(channel)
      assert here is not None
      refs.append(here)
    targets = {i: here.y - 150.0 for i, here in enumerate(refs)}
    self.assertEqual(check_pipette_move(self.pipettes, y=targets), [])
    await self.pipettes.move_stop_disc_to_z_positions({i: 150.0 for i in range(8)})
    hits = check_pipette_move(self.pipettes, y=targets)
    self.assertTrue(hits)
    self.assertIn("source_carrier", {c.obstacle.name for c in hits})
    await self.pipettes.move_stop_disc_to_z_positions({i: 334.7 for i in range(8)})
    self.assertEqual(check_pipette_move(self.pipettes, y=targets), [])

  async def test_a_channel_moved_across_its_neighbours_meets_them(self):
    # The channels stand in a row along Y, each on its own drive: no channel may cross where another
    # stands, whatever the height. Channel 0, moved towards the front, crosses two of its neighbours
    # on the way, and nothing else stands in its way there.
    hits = check_pipette_move(self.pipettes, y={0: 340.0})
    self.assertTrue(hits)
    self.assertTrue(
      all("pipette_channel" in (c.other_group or "") for c in hits),
      {(c.obstacle.name, c.other_group) for c in hits},
    )

  async def test_a_head_move_across_the_deck_at_working_height_is_caught_at_the_tip_racks(self):
    # The head swept across the deck at a working height runs into the tip racks and the tips
    # standing in them; at the height it rides above them, the same move is clear.
    assert self.star.iswap is not None
    await self.star.iswap.make_space()
    hits = check_head_move(self.head, y=100.0, z=200.0)
    self.assertTrue(hits)
    self.assertIn("tips_2", {c.obstacle.name for c in hits})
    self.assertEqual(check_head_move(self.head, y=100.0, z=260.0), [])

  async def test_tips_lowered_onto_a_lidded_plate_meet_the_lid_and_not_the_plate(self):
    # The command means to touch the plate, which is let off as the box it is. The lid seated on it
    # is its own solid: tips carried on the channel meet it where they go into it, and it is
    # reported.
    source_0 = self.deck.get_resource("source_0")
    assert isinstance(source_0, Plate)
    lid = Lid(
      name="source_0_lid",
      size_x=source_0.get_size_x(),
      size_y=source_0.get_size_y(),
      size_z=10.0,
      nesting_z_height=2.0,
    )
    source_0.assign_child_resource(lid)
    rack = self.deck.get_resource("tips_0")
    assert isinstance(rack, TipRack)
    await self.pipettes.pick_up_tips([rack.get_item(f"{row}1") for row in "ABCDEFGH"])
    # Over the plate: the arm carries the channels across in X, the channels spread along it in Y.
    arm = self.star.driver.arms[0]
    deck_at = self.deck.get_absolute_location()
    plate_center_x = source_0.get_absolute_location().x + source_0.get_size_x() / 2 - deck_at.x
    plate_center_y = source_0.get_absolute_location().y + source_0.get_size_y() / 2 - deck_at.y
    ref_0 = self.pipettes.get_reference_point_location(0)
    assert ref_0 is not None
    await arm.move_to_x_position(await arm.request_position() + plate_center_x - ref_0.x)
    await self.pipettes.move_to_y_positions({i: plate_center_y + (3.5 - i) * 9.5 for i in range(8)})
    # Where the stop disc stands when the tip's bottom just meets the lid's top.
    tip = self.pipettes.get_mounted_tip(0)
    assert tip is not None
    to_the_lid = lid.get_absolute_location().z + lid.get_size_z() - tip.get_absolute_location().z
    touching = ref_0.z + to_the_lid
    self.assertEqual(check_pipette_move(self.pipettes, z={0: touching + 1.0}, touch=[source_0]), [])
    hits = check_pipette_move(self.pipettes, z={0: touching - 2.0}, touch=[source_0])
    self.assertIn("source_0_lid", {c.obstacle.name for c in hits})


if __name__ == "__main__":
  unittest.main()
