"""The Span-8 picking up and discarding tips on the simulator, on the deck of a method Biomek's logs
ran (BC190F tips at P24)."""

from typing import Dict, List, Tuple

from pylabrobot.beckman_coulter.biomek_i7.device import BiomekI7
from pylabrobot.beckman_coulter.biomek_i7.driver.errors import TipCheckError
from pylabrobot.beckman_coulter.biomek_i7.driver.features.pipettes import press_groups
from pylabrobot.beckman_coulter.biomek_i7.driver.pod_move_tests import PodMoveTest
from pylabrobot.beckman_coulter.biomek_i7.layouts import place_labware
from pylabrobot.beckman_coulter.biomek_i7.resource_model.heads import shaft_of
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_deck
from pylabrobot.resources import set_tip_tracking
from pylabrobot.resources.errors import HasTipError

LOADED, UNLOADED = 0x00, 0x02


class PipettesTipTests(PodMoveTest):
  async def setup_deck(self):  # type: ignore[no-untyped-def]
    driver = await self.homed()
    lh = BiomekI7(driver=driver, deck=generic_deck())
    place_labware(lh.deck, [{"position": "P24", "class": "BC190F", "name": "Tips"}])
    # As after Biomek's Home All and `setup()`: the right gripper parked (GR 90, GY 48), its
    # fingers out of the probes' way.
    right = self.pod(driver, "right")
    park = right.declared.park  # type: ignore[union-attr]
    await right.move_axes({a: right.declared.to_counts(a, park[a]) for a in ("GR", "GY")})  # type: ignore[union-attr]
    pipettes = self.pod(driver, "right").pipettes
    assert pipettes is not None
    return driver, lh, pipettes

  def record(self, driver) -> List[Tuple[Dict[str, int], int]]:  # type: ignore[no-untyped-def]
    """Every `move_axes` the right pod is asked for from now on: (targets, speed)."""
    pod = self.pod(driver, "right")
    moves: List[Tuple[Dict[str, int], int]] = []
    original = pod.move_axes

    async def move_axes(targets, speed=1000, **kwargs):  # type: ignore[no-untyped-def]
      moves.append((dict(targets), speed))
      return await original(targets, speed, **kwargs)

    pod.move_axes = move_axes  # type: ignore[method-assign, assignment]
    return moves

  def sensor(self, driver, state: int) -> None:  # type: ignore[no-untyped-def]
    self.usb(driver, "span8").tip_states = bytes([state] * 8)

  async def test_a_full_load_records_the_tips(self) -> None:
    driver, lh, pipettes = await self.setup_deck()
    rack = lh.deck.get_resource("Tips")
    self.sensor(driver, LOADED)
    await pipettes.pick_up_tips([rack.get_item(f"{r}1") for r in "ABCDEFGH"])
    self.assertEqual(pipettes.tips, ["beckman_coulter_biomek_tip_190uL_filter"] * 8)
    # As the STAR shows them: each tip on its probe's shaft, the probe's channel where its axes are.
    self.assertEqual(len(pipettes.resources), 8)
    self.assertTrue(all(shaft_of(c).has_tip() for c in pipettes.resources))
    with self.assertRaises(HasTipError):
      await pipettes.pick_up_tips([rack.get_item("A2")])

  async def test_discard_shucks_in_fours_at_the_top_of_travel(self) -> None:
    driver, lh, pipettes = await self.setup_deck()
    rack = lh.deck.get_resource("Tips")
    self.sensor(driver, LOADED)
    await pipettes.pick_up_tips([rack.get_item(f"{r}1") for r in "ABCDEFGH"])
    moves = self.record(driver)
    self.sensor(driver, UNLOADED)
    await pipettes.discard_tips()
    # The stripper takes the tips at the top of Z's travel, four probes at a time.
    d = self.pod(driver, "right").declared
    assert d is not None
    top = d.to_counts("Z1", d.limits["Z1"][1])
    shucks = [sorted(t) for t, _ in moves if len(t) == 4 and set(t.values()) == {top}]
    self.assertEqual(shucks, [["Z1", "Z2", "Z3", "Z4"], ["Z5", "Z6", "Z7", "Z8"]])
    self.assertEqual(pipettes.tips, [None] * 8)
    self.assertFalse(any(shaft_of(c).has_tip() for c in pipettes.resources))
    with self.assertRaises(RuntimeError):
      await pipettes.discard_tips()

  async def test_the_tip_sensor_checks_the_probes_worked(self) -> None:
    driver, lh, pipettes = await self.setup_deck()
    rack = lh.deck.get_resource("Tips")
    self.sensor(driver, UNLOADED)  # the sensor sees no tip where one was pressed on
    with self.assertRaises(TipCheckError):
      await pipettes.pick_up_tips([rack.get_item("A1")])

  async def test_tips_cannot_go_back_into_a_rack(self) -> None:
    driver, lh, pipettes = await self.setup_deck()
    rack = lh.deck.get_resource("Tips")
    with self.assertRaisesRegex(NotImplementedError, "discard_tips"):
      await pipettes.drop_tips([rack.get_item("A1")])

  async def test_a_partial_load_presses_by_biomeks_groups(self) -> None:
    set_tip_tracking(True)
    self.addCleanup(set_tip_tracking, False)
    driver, lh, pipettes = await self.setup_deck()
    rack = lh.deck.get_resource("Tips")
    # Probes 6-8 over F1-H1: the span stays at its narrowest, probe 1 five rows behind F1.
    spots = [rack.get_item(n) for n in ("F1", "G1", "H1")]
    (plan,) = pipettes.plan_pick_up(spots, [5, 6, 7])
    self.assertEqual(plan.groups, ((6,), (7,), (8,)))  # none 4 apart
    self.assertEqual(plan.lowered, (6, 7, 8))  # A1-E1 still hold tips: probes 1-5 stay up
    self.assertAlmostEqual(plan.span, 6.3, places=6)
    self.sensor(driver, LOADED)
    await pipettes.pick_up_tips(spots, [5, 6, 7])
    self.assertIsNone(rack.get_item("H1").tip)
    self.assertEqual(pipettes.tips[5:], ["beckman_coulter_biomek_tip_190uL_filter"] * 3)
    # The next load from that column lowers the tipless probes over its emptied spots as well.
    (plan,) = pipettes.plan_pick_up([rack.get_item("A1")], [0])
    self.assertEqual(plan.lowered, (1,))  # probes 6-8 carry tips; 2-5 stand over full spots

  def test_biomeks_groups(self) -> None:
    self.assertEqual(press_groups(range(1, 9), 2, 4), [(1, 5), (2, 6), (3, 7), (4, 8)])
    self.assertEqual(press_groups(range(1, 9), 4, 1), [(1, 2, 3, 4), (5, 6, 7, 8)])
    self.assertEqual(press_groups([2, 3, 4, 5, 6, 7, 8], 2, 4), [(2, 6), (3, 7), (4, 8), (5,)])

  async def test_carrying_probes_travel_below_the_stripper(self) -> None:
    driver, lh, pipettes = await self.setup_deck()
    pod = self.pod(driver, "right")
    d = pod.declared
    assert d is not None
    ceilings = pod._traverse_ceilings()
    self.assertAlmostEqual(ceilings["Z6"], d.limits["Z6"][1] - d.settings["DiscardOffset"])
    rack = lh.deck.get_resource("Tips")
    self.sensor(driver, LOADED)
    await pipettes.pick_up_tips([rack.get_item("A1")])
    # Heights at the API are the tips' ends, mm: one the tip on probe 1 cannot reach is refused.
    reach_mm = (ceilings["Z1"] - pipettes._carried_tip_lengths()["Z1"]) * 10
    with self.assertRaisesRegex(ValueError, "reach with their tips"):
      await pipettes.pick_up_tips(
        [rack.get_item("A2")], [1], minimum_traverse_height_start=reach_mm + 1
      )
