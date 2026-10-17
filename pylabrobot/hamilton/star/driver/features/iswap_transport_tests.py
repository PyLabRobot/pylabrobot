"""Plates and lids moved by the iSWAP from its primitive moves, on a simulated STARlet."""

import unittest

from pylabrobot.hamilton.star.driver.features.iswap_transport import (
  GRIPPER_FACING,
  Grip,
  Open,
  Release,
  Rise,
  Travel,
  iSWAPTransport,
)
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb_lid
from pylabrobot.resources.plate import Plate
from pylabrobot.visualizer3D.demo import build_facility, star_of


class iSWAPTransportTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    facility = build_facility()
    self.star = star_of(facility)
    self.deck = self.star.deck
    self.deck.get_resource("destination_1").unassign()
    self.plate = self.deck.get_resource("source_1")
    assert isinstance(self.plate, Plate)
    assert self.plate.parent is not None
    self.start = self.plate.parent
    self.site = self.deck.get_resource("destination_carrier").children[1]
    await self.star.setup()
    iswap = self.star.iswap
    assert iswap is not None
    self.iswap = iswap
    await iswap.make_space()
    # Where things land, not what the arm passes on the way: that is `iswap_collisions_tests`.
    self.transport = iSWAPTransport(iswap, check_collisions=False)

  def where_the_site_puts_it(self, site):
    return site.get_absolute_location() + site.get_default_child_location(self.plate)

  async def test_a_plate_lands_where_its_site_places_it_whichever_side_it_is_gripped_from(self):
    for direction in ("front", "back", "left", "right"):
      with self.subTest(direction=direction):
        before = self.plate.get_absolute_location()
        await self.transport.move_resource(self.plate, self.site, pickup_direction=direction)
        self.assertIs(self.plate.parent, self.site)
        at, placed = self.plate.get_absolute_location(), self.where_the_site_puts_it(self.site)
        self.assertLess(max(abs(at.x - placed.x), abs(at.y - placed.y), abs(at.z - placed.z)), 1e-6)
        await self.transport.move_resource(self.plate, self.start, pickup_direction=direction)
        back = self.plate.get_absolute_location()
        self.assertLess(max(abs(back.x - before.x), abs(back.y - before.y)), 1e-6)

  async def test_a_held_plate_hangs_from_the_gripper_where_it_was_gripped(self):
    before = self.plate.get_absolute_location()
    await self.transport.pick_up_resource(self.plate, direction="front", end_height=None)
    self.assertIs(self.plate.parent, self.iswap.gripper)
    self.assertIs(self.transport.holding, self.plate)
    # Lifted straight up: the same X and Y, higher.
    now = self.plate.get_absolute_location()
    self.assertAlmostEqual(now.x, before.x, places=6)
    self.assertAlmostEqual(now.y, before.y, places=6)
    self.assertGreater(now.z, before.z)

  async def test_the_plan_is_the_firmware_s_phases(self):
    plan = self.transport.plan_pick_up(self.plate, direction="front")
    kinds = [type(step) for step in plan.steps]
    self.assertEqual(kinds[1:], [Rise, Travel, Rise, Grip, Rise])
    grip = plan.steps[4]
    assert isinstance(grip, Grip)
    # Legacy's widths: a front grip closes across the plate's X; the jaws open 3 mm wider.
    self.assertAlmostEqual(grip.width, self.plate.get_absolute_size_x())
    opening = plan.steps[0]
    assert isinstance(opening, Open)
    self.assertAlmostEqual(opening.width, grip.width + 3.0)
    self.assertEqual(plan.facing, GRIPPER_FACING["front"])
    self.assertIn("jaws close on source_1", plan.describe())

  async def test_turning_the_grip_turns_the_plate(self):
    await self.transport.pick_up_resource(self.plate, direction="back")
    plan = self.transport.plan_drop(self.site, direction="front")
    release = plan.steps[3]
    assert isinstance(release, Release)
    self.assertAlmostEqual(release.rotation_wrt_destination % 360, 180.0)
    await self.transport.execute(plan)
    self.assertAlmostEqual(self.plate.get_absolute_rotation().z % 360, 180.0)

  async def test_a_lidded_plate_is_gripped_below_its_lid(self):
    lid = cor_96_wellplate_360uL_Fb_lid(name="source_1_lid")
    self.plate.assign_child_resource(lid, location=None)
    plan = self.transport.plan_pick_up(self.plate, direction="front")
    rise = plan.steps[3]
    assert isinstance(rise, Rise)
    # The jaws close below where the lid's skirt comes down to.
    self.assertLess(rise.z, lid.get_location_wrt(self.deck).z)
    with self.assertRaises(ValueError):
      self.transport.plan_pick_up(self.plate, direction="front", pickup_distance_from_top=5.0)

  async def test_a_lid_goes_on_a_plate_as_its_lid(self):
    lid = cor_96_wellplate_360uL_Fb_lid(name="source_1_lid")
    self.plate.assign_child_resource(lid, location=None)
    await self.transport.pick_up_resource(lid, direction="front")
    await self.transport.drop_resource(self.site)
    self.assertIs(lid.parent, self.site)
    await self.transport.pick_up_resource(lid, direction="front")
    await self.transport.drop_resource(self.plate)
    assert isinstance(self.plate, Plate)
    self.assertIs(self.plate.lid, lid)

  async def test_a_grip_out_of_reach_is_refused_before_anything_moves(self):
    front_most = self.deck.get_resource("source_0")
    joints = (self.iswap.elbow_drive_get_angle(), self.iswap.wrist_drive_get_angle())
    with self.assertRaises(ValueError):
      await self.transport.pick_up_resource(front_most, direction="front")
    self.assertEqual(
      (self.iswap.elbow_drive_get_angle(), self.iswap.wrist_drive_get_angle()), joints
    )

  async def test_every_plan_clears_the_channels_out_of_the_iswap_s_way_first(self):
    pipettes = self.star.pipettes
    assert pipettes is not None
    cleared = await pipettes.request_y_positions()  # where make_space left them, in setUp
    # Something moves the channels back over the deck between two iSWAP moves.
    await pipettes.move_to_y_positions({0: cleared[0] + 150.0}, make_space=True)
    moved = await pipettes.request_y_positions()
    self.assertGreater(moved[0], cleared[0] + 100.0)
    commands = []
    listening = self.star.driver.motion_listener  # type: ignore[attr-defined]

    async def listen(module, command, params):
      commands.append(module + command)
      if listening is not None:
        await listening(module, command, params)

    self.star.driver.motion_listener = listen  # type: ignore[attr-defined]
    await self.transport.pick_up_resource(self.plate, direction="front")
    # The Y range is freed before the iSWAP's first motion, and the channels stand aside again.
    self.assertIn("C0FY", commands)
    first_iswap = next(i for i, c in enumerate(commands) if c[:2] == "R0" and c[2] != "R")
    self.assertLess(commands.index("C0FY"), first_iswap)
    for now, before in zip(await pipettes.request_y_positions(), cleared):
      self.assertAlmostEqual(now, before, delta=1.0)

  async def test_no_turn_sweeps_the_arm_behind_the_rail(self):
    elbow, wrist = self.iswap.elbow_drive_get_angle(), self.iswap.wrist_drive_get_angle()
    drive = self.iswap.elbow_get_reference_point_location()
    assert elbow is not None and wrist is not None and drive is not None
    for name in ("source_2", "source_4", "destination_3"):
      for direction in ("front", "back", "left", "right"):
        try:
          plan = self.transport.plan_pick_up(self.deck.get_resource(name), direction=direction)
        except ValueError:
          continue
        travel = plan.steps[2]
        assert isinstance(travel, Travel)
        # Where the turn is made: at the target, where the drive is, or part way.
        y = {"translate_first": travel.elbow_y, "turn_at": travel.turn_y}.get(travel.order)
        y = drive.y if y is None else y
        self.assertTrue(
          self.transport._sweep_clear(elbow, wrist, plan.reach, y), f"{name} {direction}"
        )


if __name__ == "__main__":
  unittest.main()
