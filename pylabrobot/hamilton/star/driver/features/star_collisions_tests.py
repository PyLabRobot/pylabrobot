"""What pipette and head commands sweep through, on a simulated STARlet's demo deck."""

import unittest
from typing import List

from pylabrobot.hamilton.star.driver.features.iswap_transport import (
  iSWAPCollisionError,
  iSWAPTransport,
)
from pylabrobot.hamilton.star.driver.features.star_collisions import (
  CollisionError,
  check_head_move,
  check_pipette_move,
)
from pylabrobot.hamilton.star.motion import attach_viewer_collisions
from pylabrobot.resources.lid import Lid
from pylabrobot.resources.n_channel_pipettes import TipMountingShaft
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.tip_rack import TipRack
from pylabrobot.resources.tip_tracking import does_tip_tracking, set_tip_tracking
from pylabrobot.visualizer3D.demo import build_facility, star_of


class _RecordingViewer:
  """What the glue needs of a viewer, and a list of everything it was told to draw."""

  def __init__(self, raise_on_collision: bool):
    self.raise_on_collision = raise_on_collision
    self.shown: list = []

  async def show_collisions(self, collisions) -> None:
    self.shown.append(collisions)


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


class CollisionGateTests(unittest.IsolatedAsyncioTestCase):
  """The viewer's flag turns the arm's checks into gates at the driver's dispatch."""

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
    assert self.star.iswap is not None
    self.iswap = self.star.iswap
    self.viewer = _RecordingViewer(raise_on_collision=True)
    self.transport = iSWAPTransport(self.iswap, check_collisions=True)
    attach_viewer_collisions(self.star.driver, self.viewer, self.transport)

  async def test_a_channel_move_that_would_hit_something_is_refused_saying_which_command(self):
    # Lowered with the gate off: the lowering itself would be refused from the row's spread, where
    # two of the channels stand over plates.
    self.viewer.raise_on_collision = False
    await self.pipettes.move_stop_disc_to_z_positions({i: 150.0 for i in range(8)})
    self.viewer.raise_on_collision = True
    before = self.pipettes.get_reference_point_location(0)
    yp = " ".join(f"{round(300.0 * 10):04}" for _ in range(8))
    with self.assertRaises(CollisionError) as refused:
      await self.star.driver.send_command(module="C0", command="JY", yp=yp)
    self.assertEqual(refused.exception.origin, "the channels' Y move (C0 JY)")
    self.assertTrue(self.viewer.shown)
    # Refused before the device heard it: the channels are where they were.
    self.assertEqual(self.pipettes.get_reference_point_location(0), before)

  async def test_one_channels_move_is_refused_naming_the_channel(self):
    await self.iswap.make_space()
    za = f"{self.pipettes.configuration.z_drive_mm_to_increments(150.0):05}"
    with self.assertRaises(CollisionError) as refused:
      await self.star.driver.send_command(module=self.pipettes.channel_id(0), command="ZA", za=za)
    self.assertEqual(
      refused.exception.origin, f"channel 0's Z move ({self.pipettes.channel_id(0)} ZA)"
    )
    self.assertTrue(self.viewer.shown)

  async def test_a_tipped_channels_jz_is_judged_at_its_stop_disc(self):
    # The command takes Z to be each channel's lowest point - the tip's bottom where one is
    # mounted - and the check speaks of the stop disc: the gate lifts each target by the overhang
    # of the tip the model has on the channel. A tip lowered toward a lid is refused where it
    # meets it; a stop a millimetre above runs.
    self.viewer.raise_on_collision = False
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
    arm = self.star.driver.arms[0]
    deck_at = self.deck.get_absolute_location()
    plate_center_x = source_0.get_absolute_location().x + source_0.get_size_x() / 2 - deck_at.x
    plate_center_y = source_0.get_absolute_location().y + source_0.get_size_y() / 2 - deck_at.y
    ref_0 = self.pipettes.get_reference_point_location(0)
    assert ref_0 is not None
    await arm.move_to_x_position(await arm.request_position() + plate_center_x - ref_0.x)
    await self.pipettes.move_to_y_positions({i: plate_center_y + (3.5 - i) * 9.5 for i in range(8)})
    self.viewer.raise_on_collision = True
    assert self.pipettes.get_mounted_tip(0) is not None
    shaft = next(
      child for child in self.pipettes.resources[0].children if isinstance(child, TipMountingShaft)
    )
    bottom = shaft.tip_bottom()
    assert bottom is not None
    tip = self.pipettes.get_mounted_tip(0)
    assert tip is not None
    # The command's Z is the tip's bottom: at the lid's top it touches, above it the way is clear.
    to_the_lid = (lid.get_absolute_location().z + lid.get_size_z()) - tip.get_absolute_location().z
    jz_touching = ref_0.z + to_the_lid + bottom.z

    def jz(offset: float) -> List[str]:
      target = jz_touching + offset
      return [f"{round(target * 10):04}" for _ in range(8)]

    await self.star.driver.send_command(module="C0", command="JZ", zp=jz(1.0))
    self.assertEqual(self.viewer.shown, [])
    with self.assertRaises(CollisionError) as refused:
      await self.star.driver.send_command(module="C0", command="JZ", zp=jz(-2.0))
    self.assertEqual(refused.exception.origin, "the channels' Z move (C0 JZ)")
    self.assertTrue(self.viewer.shown)

  async def test_a_head_move_that_would_hit_something_is_refused_saying_which_command(self):
    self.viewer.raise_on_collision = False
    await self.head.move_stop_disc_to_z_position(200.0)
    self.viewer.raise_on_collision = True
    ya = f"{self.head.configuration.y_drive_mm_to_increments(100.0):05}"
    with self.assertRaises(CollisionError) as refused:
      await self.star.driver.send_command(
        module=self.head.configuration.module, command="YA", ya=ya
      )
    self.assertEqual(
      refused.exception.origin,
      f"the head's Y move ({self.head.configuration.module} YA)",
    )
    self.assertTrue(self.viewer.shown)

  async def test_with_the_flag_off_the_same_command_runs(self):
    self.viewer.raise_on_collision = False
    await self.pipettes.move_stop_disc_to_z_positions({i: 150.0 for i in range(8)})
    yp = " ".join(f"{round(300.0 * 10):04}" for _ in range(8))
    await self.star.driver.send_command(module="C0", command="JY", yp=yp)
    self.assertEqual(self.viewer.shown, [])

  async def test_reads_are_never_handed_to_the_gate(self):
    handed: list = []

    async def recorder(module: str, command: str, params) -> None:
      handed.append((module, command))

    self.star.driver.collision_listener = recorder
    await self.pipettes.request_y_positions()
    self.assertEqual(handed, [])

  async def test_a_refused_plan_is_drawn_before_it_is_refused(self):
    plan = self.transport.plan_pick_up(
      self.deck.get_resource("source_3"), direction="front", elbow="right"
    )
    with self.assertRaises(iSWAPCollisionError):
      await self.transport.execute(plan)
    self.assertTrue(self.viewer.shown)
    for shown in self.viewer.shown:
      self.assertIn("tips_2", {c.obstacle.name for c in shown})


if __name__ == "__main__":
  unittest.main()
