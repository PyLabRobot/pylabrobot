"""Aspirate and dispense on the Span-8 on the simulator, on the deck of a method Biomek's logs ran
(2026-10-20, a dilution): the logged method's technique values given as arguments, the moves sent
compared with the Master Controller's own log of that run."""

from typing import Dict, List, Tuple

from pylabrobot.beckman_coulter.biomek_i7.device import BiomekI7
from pylabrobot.beckman_coulter.biomek_i7 import units
from pylabrobot.beckman_coulter.biomek_i7.driver import liquid
from pylabrobot.beckman_coulter.biomek_i7.driver.liquid import (
  ContainerShape,
  PlungerMove,
  Touch,
  ZMove,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.pod_move_tests import PodMoveTest
from pylabrobot.beckman_coulter.biomek_i7.layouts import place_labware
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_deck
from pylabrobot.beckman_coulter.biomek_i7.resource_model.labware import (
  biomek_labware,
  biomek_tip,
  biomek_touch_distance,
)
from pylabrobot.beckman_coulter.biomek_i7.techniques import MixHeights, TipTouch
from pylabrobot.lib.liquid_handling.mix import Mix
from pylabrobot.resources import Coordinate, cor_96_wellplate_360uL_Fb, set_volume_tracking
from pylabrobot.resources.beckman_coulter import BiomekWell

# The Span-8's eight probes from the reservoir (the "S8 Pipetting Reservoir Offset Aspirate"
# template: 10 mm approach), then probe 1 into A1 of the plate the 96-head filled.
LOGGED_SPAN8_ASPIRATE = [
  "AM 2047 1000 0 -180729 105210 0 -58375 -58221 -58381 -58710 -58545 -58312 -58272 -58330",
  "AM 2040 300 0 -80273 -80120 -80280 -80609 -80444 -80211 -80171 -80229",
  "AM 522240 52 0 3516 3509 3401 3312 3118 3528 2472 3566",
  "AM 2040 300 0 -62716 -62562 -62722 -63051 -62886 -62653 -62613 -62671",
]
LOGGED_SPAN8_DISPENSE = [
  "AM 8 1000 0 -74227",
  "AM 8 300 0 -77040",  # at the liquid's surface (SS before)
  "AM 2056 52 0 -75826 480",  # following up
  "AM 8 300 0 -74862",  # 2 mm above the liquid (SS before)
  "AM 8 1000 0 -76309",  # touch: liquid - 1 mm (SS before)
  "AM 7 1000 0 -230264 152817 0",
  "AM 7 1000 0 -230264 154304 0",
  "AM 8 300 0 -74227",
]
# 2026-09-22 13.45 (a transfection method): probes 5-8 dispense 190 uL into A2-D2 of the
# Thomson 24-well plate at P6, 3 mm from the bottom, then mix 3 x 150 uL at 200 uL/s, drawing 5 mm
# and pushing 15 mm from the bottom, at the step's 25 % (Biomek did not know the plate's amounts: no
# move 2 mm above the liquid; the blowout has nothing left above the system air gap to push).
LOGGED_SPAN8_MIX = [
  "AM 1920 1000 0 -59975 -59742 -59702 -59760",  # 1 mm above the top
  "AM 1920 250 0 -78304 -78071 -78031 -78089",  # 3 mm above the bottom
  "AM 491520 103 0 480 480 480 480",  # 190 uL at 100 uL/s
  *[
    "AM 1920 250 0 -77339 -77106 -77066 -77124",  # the draw's 5 mm
    "AM 491520 207 0 4080 4080 4080 4080",  # 150 uL at 200 uL/s
    "AM 1920 250 0 -72516 -72283 -72243 -72301",  # the push's 15 mm
    "AM 491520 207 0 480 480 480 480",
  ]
  * 3,
  "AM 1920 250 0 -59975 -59742 -59702 -59760",  # 1 mm above the top
]
SPAN8_VOLUMES = [146.5, 146.2, 141.7, 138, 129.9, 147, 103, 148.6]  # D after, uL; 20 before


SPAN8_SLOTS = ["X", "Y", "S"] + [f"Z{n}" for n in range(1, 9)] + [f"D{n}" for n in range(1, 9)]


def parse_span8(command: str) -> Tuple[Dict[str, int], int]:
  f = [int(x) for x in command.split()[1:]]
  mask, speed, values = f[0], f[1], iter(f[3:])
  return {n: next(values) for i, n in enumerate(SPAN8_SLOTS) if mask & (1 << i)}, speed


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
        {"position": "P6", "class": "Thermo96VB_PP", "name": "Plate"},
      ],
    )
    reservoir = lh.deck.get_resource("Reservoir").get_item(0)  # type: ignore[attr-defined]
    reservoir.tracker.set_volume(reservoir.max_volume)
    # As after Biomek's Home All and `setup()`: the right gripper parked (GR 90, GY 48), its
    # fingers out of the probes' way.
    right = self.pod(driver, "right")
    park = right.declared.park  # type: ignore[union-attr]
    await right.move_axes({a: right.declared.to_counts(a, park[a]) for a in ("GR", "GY")})  # type: ignore[union-attr]
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

  async def test_the_span8s_logged_transfer(self) -> None:
    driver, lh = await self.setup_deck()
    pipettes = lh.pipettes
    pipettes._set_tips({n: biomek_tip("T190F", "t") for n in range(1, 9)})
    # As logged: the left pod's gripper turned to 90 degrees and the pod at X 12.701, clear of P6.
    left = self.pod(driver, "left")
    await left.move_axes({"GR": left.declared.to_counts("GR", 90)})  # type: ignore[union-attr]
    await left.move_axes({"X": 5586, "Y": 58890})
    plate = lh.deck.get_resource("Plate")
    plate.get_well("A1").tracker.set_volume(200)  # the 96-head's dispense before
    moves = self.record(driver, "right")
    reservoir = lh.deck.get_resource("Reservoir").get_item(0)
    await pipettes.aspirate(
      [reservoir] * 8, [v - 20 for v in SPAN8_VOLUMES], resource_offsets=[Coordinate(-2, 0, 0)] * 8,
      liquid_heights=[2.0] * 8, flow_rates=[50] * 8, z_speed=109.3, settling_times=[0.2] * 8,
      approach_height=10,
    )  # fmt: skip
    start = next(i for i, (t, _) in enumerate(moves) if "S" in t and "Z1" in t)
    self.assertEqual(moves[start : start + 4], [parse_span8(c) for c in LOGGED_SPAN8_ASPIRATE])
    moves.clear()
    sensed = self.usb(driver, "span8").requests.copy()
    await pipettes.dispense(
      [plate.get_well("A1")], [SPAN8_VOLUMES[0] - 20], use_channels=[0], liquid_heights=[0.0],
      height_from="liquid", follow_liquid=True, flow_rates=[50], z_speed=109.3,
      tip_touch=TipTouch(height=-1.0, height_from="liquid", theta=90, speed_fraction=1.0, distance=3.1563),
    )  # fmt: skip
    first = next(
      i for i, (t, _) in enumerate(moves) if t == parse_span8(LOGGED_SPAN8_DISPENSE[0])[0]
    )
    self.assertEqual(moves[first : first + 8], [parse_span8(c) for c in LOGGED_SPAN8_DISPENSE])
    self.assertGreater(len(self.usb(driver, "span8").requests), len(sensed))
    self.assertAlmostEqual(plate.get_well("A1").tracker.get_used_volume(), 200 + 126.5)

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

  def test_biomeks_touch_off_distance(self) -> None:
    # Biomek's distance: the wall at the touch height less the tip's cone where it crosses the rim.
    well = biomek_labware("Thermo96VB_PP", "p").get_well("A1")
    t1025f, t190f = biomek_tip("T1025F", "t"), biomek_tip("T190F", "t")
    self.assertAlmostEqual(biomek_touch_distance(well, t1025f, 3.67) or 0.0, 2.7532, places=3)
    self.assertAlmostEqual(biomek_touch_distance(well, t190f, 7.184) or 0.0, 3.1563, places=3)
    self.assertEqual(biomek_touch_distance(well, t190f, 0.2), 0.0)  # in the cone: no room
    native = cor_96_wellplate_360uL_Fb("n").get_well("A1")
    self.assertIsNone(biomek_touch_distance(native, t190f, 3.0))  # not Biomek's labware

  def test_wall_from_the_volume_function(self) -> None:
    # The wall at a height is the volume function's slope (an area) in the top outline's shape: for
    # a cone-bottomed round well it is Biomek's segment wall; for a round bottom under a square top
    # PyLabRobot's one cross-section shape reads it as a square, a little inside the wall.
    for name, low in (("Thermo96VB_PP", False), ("Thomson_24w_Square_RB", True)):
      well = biomek_labware(name, "p").get_well("A1")
      shape = ContainerShape(well)
      assert isinstance(well, BiomekWell)
      for z in (0.1, 0.3, 0.6, 0.9 * shape.depth):
        ours, biomeks = shape.wall_distance(z, 0.0), well.wall_distance(z * 10, 0.0) / 10
        if low:
          self.assertLessEqual(ours, biomeks + 1e-6)
        else:
          self.assertAlmostEqual(ours, biomeks, delta=0.001)

  async def test_the_span8s_logged_mix(self) -> None:
    driver, lh = await self.setup_deck()
    set_volume_tracking(False)  # as Biomek, which did not know the plate's amounts
    lh.deck.get_resource("Plate").unassign()
    place_labware(
      lh.deck, [{"position": "P6", "class": "Thomson_24w_Square_RB", "name": "Culture"}]
    )
    left = self.pod(driver, "left")
    await left.move_axes({"GR": left.declared.to_counts("GR", 90)})  # type: ignore[union-attr]
    await left.move_axes({"X": 5586, "Y": 58890})
    pipettes, right = lh.pipettes, self.pod(driver, "right")
    pipettes._set_tips({n: biomek_tip("T190F", "t") for n in range(1, 9)})
    await pipettes.set_valves({f"D{n}": "output" for n in range(1, 9)})
    held = {f"D{n}": 20.0 if n < 5 else 210.0 for n in range(1, 9)}
    await right.move_axes({a: right.declared.to_counts(a, v) for a, v in held.items()})  # type: ignore[union-attr]
    plate = lh.deck.get_resource("Culture")
    moves = self.record(driver, "right")
    mix = Mix(volume=150, repetitions=3, flow_rate=200)
    await pipettes.dispense(
      [plate.get_well(w) for w in ("A2", "B2", "C2", "D2")], [190] * 4, use_channels=[4, 5, 6, 7],
      liquid_heights=[3.0] * 4, flow_rates=[100] * 4, z_speed=units.field_to_rate(right, "Z1", 250),
      post_mixes=[mix] * 4, mix_heights=MixHeights(5.0, "bottom", 15.0, "bottom"),
    )  # fmt: skip
    first = next(i for i, (t, _) in enumerate(moves) if t == parse_span8(LOGGED_SPAN8_MIX[0])[0])
    self.assertEqual(
      moves[first : first + len(LOGGED_SPAN8_MIX)], [parse_span8(c) for c in LOGGED_SPAN8_MIX]
    )

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
