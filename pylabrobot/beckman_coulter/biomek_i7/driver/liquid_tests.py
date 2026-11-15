"""Aspirate and dispense on both heads on the simulator, on the deck of a method Biomek's logs ran
(2026-10-20, a dilution): the logged method's technique values given as arguments, the moves sent
compared with the Master Controller's own log of that run."""

import math
from typing import Dict, List, Tuple

from pylabrobot.beckman_coulter.biomek_i7.device import BiomekI7
from pylabrobot.beckman_coulter.biomek_i7.driver import liquid
from pylabrobot.beckman_coulter.biomek_i7.driver.features.head96 import COLUMNS, ROWS, mandrel_name
from pylabrobot.beckman_coulter.biomek_i7.driver.liquid import (
  ContainerShape,
  PlungerMove,
  Touch,
  ZMove,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.pod_move_tests import PodMoveTest
from pylabrobot.beckman_coulter.biomek_i7.layouts import place_labware
from pylabrobot.beckman_coulter.biomek_i7.resource_model.labware import (
  biomek_labware,
  biomek_tip,
)
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_deck
from pylabrobot.beckman_coulter.biomek_i7.techniques import MixHeights, TipTouch
from pylabrobot.lib.liquid_handling.mix import Mix
from pylabrobot.resources import cor_96_wellplate_360uL_Fb, set_volume_tracking
from pylabrobot.resources.errors import TooLittleLiquidError


class LiquidTests(PodMoveTest):
  async def setup_deck(self):  # type: ignore[no-untyped-def]
    set_volume_tracking(True)
    self.addCleanup(set_volume_tracking, False)
    driver = await self.homed()
    lh = BiomekI7(driver=driver, deck=generic_deck())
    place_labware(
      lh.deck,
      [
        {"position": "P8", "class": "ThermoNUNC_350CB", "name": "Reservoir"},
        {"position": "P6", "class": "BCFlat96", "name": "Plate"},
      ],
    )
    reservoir = lh.deck.get_resource("Reservoir").get_item(0)  # type: ignore[attr-defined]
    reservoir.tracker.set_volume(reservoir.max_volume)
    # As after Biomek's Home All and `setup()`: the right gripper parked (GR 90, GY 48), its
    # fingers out of the probes' way.
    right = self.pod(driver, "right")
    park = right.declared.park  # type: ignore[union-attr]
    await right.move_axes({a: right.declared.to_counts(a, park[a]) for a in ("GR", "GY")})  # type: ignore[union-attr]
    # The 96-head brought forward, in line with P8's row, as after a tip load: the tests put long
    # tips on it here, and from its home corner its way to P8 would cross the washer at W3, which
    # 1025 uL tips cannot clear (the deck check refuses it).
    left = self.pod(driver, "left")
    park = left.declared.park  # type: ignore[union-attr]
    await left.move_axes({a: left.declared.to_counts(a, park[a]) for a in ("GR", "GY")})  # type: ignore[union-attr]
    await left.move_axes({"Y": left.declared.to_counts("Y", 37.45)})  # type: ignore[union-attr]
    return driver, lh

  def record(self, driver, side: str) -> List[Tuple[Dict[str, int], int]]:  # type: ignore[no-untyped-def]
    pod = self.pod(driver, side)
    moves: List[Tuple[Dict[str, int], int]] = []
    original = pod.move_axes

    async def move_axes(targets, speed=1000, **kwargs):  # type: ignore[no-untyped-def]
      moves.append((dict(targets), speed))
      return await original(targets, speed, **kwargs)

    pod.move_axes = move_axes  # type: ignore[method-assign, assignment]
    return moves

  def after_travel(self, moves):  # type: ignore[no-untyped-def]
    """The moves after the X/Y travel (`_move_over` lifts first, then moves X/Y alone)."""
    travel = max(i for i, (t, _) in enumerate(moves) if "X" in t and not any("Z" in k for k in t))
    return [m for m in moves[travel + 1 :] if not set(m[0]) <= {"Z", "GZ"} or m[1] != 1000][:99]

  async def test_a_batchs_touch_off_is_measured_at_its_last_probe(self) -> None:
    # One touch-off moves every probe of a batch; Biomek's logged touch-offs (e.g. 2026-10-20's
    # dilution, where the probes followed different amounts up) are measured at the batch's last
    # probe, and the others reach as far.
    driver, lh = await self.setup_deck()
    pipettes = lh.pipettes
    pipettes._set_tips({n: biomek_tip("T190F", "t") for n in range(1, 9)})
    plate = lh.deck.get_resource("Plate")
    wells = [plate.get_well("A1"), plate.get_well("B1")]
    wells[0].tracker.set_volume(20)
    wells[1].tracker.set_volume(120)
    settings = pipettes._settings(
      liquid_height=0.0, height_from="liquid", follow_liquid=True,
      tip_touch=TipTouch(height=-1.0, height_from="liquid", theta=90),
    )  # fmt: skip
    (plan,) = pipettes.plan_liquid("dispense", wells, [30, 30], [0, 1], settings, 50)
    self.assertEqual([p for p, _ in plan.pairs], [1, 2])
    touch = next(s for s in plan.steps if isinstance(s, Touch))
    at = plan.steps[plan.steps.index(touch) - 1]  # the move to the touch height
    assert isinstance(at, ZMove)
    heights = {c.name: at.z[c.name] - c.bottom - c.tip_length for c in plan.channels}
    first, last = plan.channels
    self.assertAlmostEqual(touch.above_bottom, heights[last.name], places=9)
    self.assertNotAlmostEqual(heights[first.name], heights[last.name], places=3)
    t = settings.tip_touch
    assert t is not None
    self.assertEqual((touch.dx, touch.dy), liquid.touch_offset(last, heights[last.name], t))
    self.assertNotEqual((touch.dx, touch.dy), liquid.touch_offset(first, heights[first.name], t))

  async def test_a_liquid_height_needs_known_contents(self) -> None:
    driver, lh = await self.setup_deck()
    set_volume_tracking(False)
    lh.pipettes._set_tips({1: biomek_tip("T190F", "t")})
    with self.assertRaisesRegex(ValueError, "liquid level"):
      await lh.pipettes.dispense(
        [lh.deck.get_resource("Plate").get_well("A1")], [10], liquid_heights=[0.0],
        height_from="liquid",
      )  # fmt: skip

  async def test_the_96_head_has_no_lld(self) -> None:
    driver, lh = await self.setup_deck()
    with self.assertRaises(NotImplementedError):
      await lh.head96.aspirate(lh.deck.get_resource("Plate"), 10, lld_mode="CAPACITIVE")

  def test_wall_from_the_volume_function(self) -> None:
    # The wall at a height is the volume function's slope (an area) in the top outline's shape.
    # BCFlat96 (all-round): matches the Biomek section, its radius 0.3175 cm at the bottom to
    # 0.343 cm at its 1.0668 cm top. Thomson_24w_Square_RB (round bottom under a square top): a
    # single cross-section type reads the bottom a little inside.
    round_well = biomek_labware("BCFlat96", "p").get_well("A1")
    round_shape = ContainerShape(round_well)
    for height in (0.2, 0.5, 1.0):
      section = 0.3175 + (0.343 - 0.3175) * height / 1.0668
      self.assertAlmostEqual(round_shape.wall_distance(height, 0.0), section, delta=0.001)
    mixed = biomek_labware("Thomson_24w_Square_RB", "p").get_well("A1")
    mixed_shape = ContainerShape(mixed)
    self.assertGreater(mixed_shape.wall_distance(0.5, 0.0), 0.0)
    self.assertLessEqual(mixed_shape.wall_distance(0.4, 0.0), 0.85 + 1e-6)
    self.assertAlmostEqual(mixed_shape.wall_distance(2.0, 0.0), 0.85, delta=0.001)

  async def test_pylabrobots_own_labware(self) -> None:
    # A plate defined by PyLabRobot: heights from its cavity bottom, the level from its own
    # height <-> volume function, the touch-off to its outline less the tip's radius.
    driver, lh = await self.setup_deck()
    head = lh.head96
    tip = biomek_tip("T1025F", "t")
    head._set_tips(
      {
        mandrel_name(r, c): biomek_tip("T1025F", f"t_{r}_{c}")
        for r in range(ROWS)
        for c in range(COLUMNS)
      }
    )
    plate = cor_96_wellplate_360uL_Fb("Native")
    lh.deck.get_position("P6").assign_child_resource(plate)
    reservoir = lh.deck.get_resource("Reservoir")
    await head.aspirate(reservoir, 100)
    settings = head._settings(
      liquid_height=1.0, follow_liquid=True, tip_touch=TipTouch(height=-1.0)
    )
    plan = head.plan_liquid("dispense", plate, 100, settings, 100)
    well = plate.get_well("A1")
    rise = well.compute_height_from_volume(100) / 10
    dispense = next(s for s in plan.steps if isinstance(s, PlungerMove))
    start = plan.steps[1].z["A1"]  # type: ignore[union-attr]
    self.assertAlmostEqual(dispense.z["A1"] - start, rise, places=6)
    touch = next(s for s in plan.steps if isinstance(s, Touch))
    # The wall where the tip touches: the tapered well's area there (its volume function's slope).
    h = touch.above_bottom * 10
    area = (
      well.compute_volume_from_height(h + 0.05) - well.compute_volume_from_height(h - 0.05)
    ) / 0.1
    wall = math.sqrt(area / math.pi) / 10
    self.assertLess(wall, well.get_size_x() / 20)
    self.assertAlmostEqual(-touch.dy, wall - tip.get_size_x() / 20, places=6)
    await head.dispense(plate, 100, liquid_height=1.0, follow_liquid=True)
    self.assertAlmostEqual(well.tracker.get_used_volume(), 100)

  async def test_a_mix_in_the_aspirate_template(self) -> None:
    # The aspirate's mix comes after the blowout air and before the descent, at its own heights;
    # moves measured from the liquid set the sensors first (`liquid`).
    driver, lh = await self.setup_deck()
    pipettes = lh.pipettes
    pipettes._set_tips({1: biomek_tip("T190F", "t")})
    well = lh.deck.get_resource("Plate").get_well("A1")
    well.tracker.set_volume(200)
    settings = pipettes._settings(liquid_height=1.0, blow_out_air_volume=10.0)
    mixed = liquid.with_mix(settings, Mix(25, 2, 50), MixHeights(-2.0, "liquid"))
    plain = pipettes.plan_liquid("aspirate", [well], [20], [0], settings, 50)[0].steps
    steps = pipettes.plan_liquid("aspirate", [well], [20], [0], mixed, 50)[0].steps
    self.assertEqual(steps[:2], plain[:2])  # approach, blowout air
    self.assertEqual(steps[2 + 8 :], plain[2:])  # descent, draw, exit
    cycle = steps[2:6]
    to_draw, draw, to_push, push = cycle
    assert isinstance(to_draw, ZMove) and isinstance(to_push, ZMove)
    assert isinstance(draw, PlungerMove) and isinstance(push, PlungerMove)
    self.assertTrue(to_draw.liquid and to_push.liquid)
    self.assertEqual((draw.volume, push.volume), ({"Z1": 25}, {"Z1": -25}))
    self.assertEqual((draw.z, push.z), ({}, {}))
    # The push is measured from the level the draw left: 25 uL lower.
    shape = ContainerShape(well)
    self.assertAlmostEqual(
      to_draw.z["Z1"] - to_push.z["Z1"], shape.height(200) - shape.height(175), places=9
    )
    self.assertEqual(steps[6:10], cycle)  # the second cycle as the first
    self.assertAlmostEqual(well.tracker.get_used_volume(), 200)  # planning books nothing
    # Following, the tips end each draw and push where the next push and draw are measured: only
    # the first move to the mix's height is sent.
    following = liquid.with_mix(
      settings, Mix(25, 2, 50, auto_surface_following=True), MixHeights(-2.0, "liquid")
    )
    steps = pipettes.plan_liquid("aspirate", [well], [20], [0], following, 50)[0].steps
    self.assertEqual(steps[2], to_draw)
    moves = steps[3:7]
    assert all(isinstance(m, PlungerMove) for m in moves)
    self.assertEqual([m.volume["Z1"] for m in moves], [25, -25, 25, -25])  # type: ignore[union-attr]
    self.assertAlmostEqual(moves[0].z["Z1"], to_push.z["Z1"], places=9)  # type: ignore[union-attr]
    self.assertAlmostEqual(moves[1].z["Z1"], to_draw.z["Z1"], places=9)  # type: ignore[union-attr]
    self.assertEqual(steps[7:], plain[2:])

  async def test_a_mix_at_the_dispense_height_moves_no_z(self) -> None:
    # As logged (09-22, Greiner96RoundDeep): a mix at the height the dispense left the tips at sends
    # only the plunger's moves.
    driver, lh = await self.setup_deck()
    pipettes = lh.pipettes
    pipettes._set_tips({1: biomek_tip("T190F", "t")})
    well = lh.deck.get_resource("Plate").get_well("A1")
    settings = liquid.with_mix(pipettes._settings(liquid_height=2.0), Mix(35, 2, 100), None)
    steps = pipettes.plan_liquid("dispense", [well], [9], [0], settings, 90)[0].steps
    mix = steps[3:7]
    self.assertTrue(all(isinstance(s, PlungerMove) and s.flow_rate == 100 for s in mix))
    self.assertEqual([s.volume["Z1"] for s in mix], [35, -35, 35, -35])  # type: ignore[union-attr]

  async def test_the_span8_refuses_more_than_a_well_holds_before_moving(self) -> None:
    driver, lh = await self.setup_deck()
    pipettes = lh.pipettes
    tips = {n: biomek_tip("T190F", "t") for n in (1, 2)}
    pipettes._set_tips(tips)
    pipettes._tips.update(tips)
    plate = lh.deck.get_resource("Plate")
    enough, short = plate.get_well("A1"), plate.get_well("B1")
    enough.tracker.set_volume(50)
    short.tracker.set_volume(10)
    moves = self.record(driver, "right")
    with self.assertRaises(TooLittleLiquidError):
      await pipettes.aspirate([enough, short], [20, 20])
    self.assertEqual(moves, [])
    # The probe booked before the short one is rolled back too.
    self.assertAlmostEqual(enough.tracker.get_used_volume(), 50)
    self.assertAlmostEqual(tips[1].tracker.get_used_volume(), 0)

  async def test_a_mix_needs_empty_tips_and_one_mix_for_all_probes(self) -> None:
    driver, lh = await self.setup_deck()
    pipettes = lh.pipettes
    tips = {n: biomek_tip("T190F", "t") for n in (1, 2)}
    pipettes._set_tips(tips)
    pipettes._tips.update(tips)  # as a pickup leaves them: the tips whose trackers book liquid
    plate = lh.deck.get_resource("Plate")
    wells = [plate.get_well("A1"), plate.get_well("B1")]
    mix = Mix(10, 1, 50)
    with self.assertRaisesRegex(ValueError, "one mix for all channels"):
      await pipettes.dispense(wells, [5, 5], post_mixes=[mix, None])
    with self.assertRaisesRegex(ValueError, "one mix for all channels"):
      await pipettes.dispense(wells, [5, 5], post_mixes=[mix, Mix(20, 1, 50)])
    for tip in tips.values():
      tip.tracker.add_liquid(30)
    # Biomek: "The mix operation cannot begin until tips are empty".
    with self.assertRaisesRegex(ValueError, "until the tips are empty"):
      await pipettes.dispense(wells, [20, 20], post_mixes=[mix] * 2)
    with self.assertRaisesRegex(ValueError, "until the tips are empty"):
      await pipettes.aspirate(wells, [5, 5], pre_mixes=[mix] * 2)
    with self.assertRaisesRegex(ValueError, "cannot be negative"):
      await pipettes.dispense(wells, [30, 30], post_mixes=[Mix(10, -1, 50)] * 2)
    with self.assertRaises(NotImplementedError):
      await pipettes.dispense(
        wells, [30, 30], post_mixes=[Mix(10, 1, 50, surface_following_distance=1.0)] * 2
      )
    # Drawn for real this time, then a dispense that leaves the tips empty mixes.
    for tip in tips.values():
      tip.tracker.remove_liquid(30)
    left = self.pod(driver, "left")
    await left.move_axes({"GR": left.declared.to_counts("GR", 90)})  # type: ignore[union-attr]
    await left.move_axes({"X": 5586, "Y": 58890})
    reservoir = lh.deck.get_resource("Reservoir").get_item(0)
    await pipettes.aspirate([reservoir] * 2, [30, 30])
    await pipettes.dispense(wells, [30, 30], post_mixes=[mix] * 2)
    self.assertAlmostEqual(wells[0].tracker.get_used_volume(), 30)
    self.assertAlmostEqual(tips[1].tracker.get_used_volume(), 0)

  async def test_the_96_heads_mix(self) -> None:
    # The same template on the 96-head (no logged method mixes on it): after the dispense the
    # plunger draws and pushes the mix volume back, at the dispense height, at the mix's rate.
    driver, lh = await self.setup_deck()
    head = lh.head96
    tips = {
      mandrel_name(r, c): biomek_tip("T1025F", "t") for r in range(ROWS) for c in range(COLUMNS)
    }
    head._set_tips(tips)
    head._tips.update(tips)
    await head.aspirate(lh.deck.get_resource("Reservoir"), 100, liquid_height=2.0)
    plate = lh.deck.get_resource("Plate")
    moves = self.record(driver, "left")
    await head.dispense(plate, 100, liquid_height=1.0, post_mix=Mix(50, 2, 100))
    d = [t["D"] for t, _ in moves if "D" in t]
    ul = [lh.head96._pod.declared.to_value("D", c) for c in d]  # type: ignore[union-attr]
    steps = [b - a for a, b in zip(ul, ul[1:])]
    self.assertEqual(len(steps), 4)  # after the dispense: draw, push, draw, push
    for step, want in zip(steps, [50, -50, 50, -50]):
      self.assertAlmostEqual(step, want, delta=0.01)  # to a count
    self.assertAlmostEqual(plate.get_well("A1").tracker.get_used_volume(), 100)
    self.assertAlmostEqual(tips["A1"].tracker.get_used_volume(), 0)

  async def test_the_96_head_refuses_more_than_the_wells_hold_before_moving(self) -> None:
    driver, lh = await self.setup_deck()
    head = lh.head96
    tips = {
      mandrel_name(r, c): biomek_tip("T1025F", "t") for r in range(ROWS) for c in range(COLUMNS)
    }
    head._set_tips(tips)
    head._tips.update(tips)
    plate = lh.deck.get_resource("Plate")
    for well in plate.get_all_items():
      well.tracker.set_volume(50)
    plate.get_well("H12").tracker.set_volume(5)
    moves = self.record(driver, "left")
    with self.assertRaises(TooLittleLiquidError):
      await head.aspirate(plate, 20, liquid_height=1.0)
    self.assertEqual(moves, [])
    self.assertAlmostEqual(plate.get_well("A1").tracker.get_used_volume(), 50)
    self.assertAlmostEqual(tips["A1"].tracker.get_used_volume(), 0)
