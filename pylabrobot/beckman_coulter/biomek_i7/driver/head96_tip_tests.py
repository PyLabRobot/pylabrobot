"""The 96-head picking up, dropping and discarding tips on the simulator, on the deck of a method
Biomek's logs ran (a dilution)."""

from pylabrobot.beckman_coulter.biomek_i7.device import BiomekI7
from pylabrobot.beckman_coulter.biomek_i7.driver.pod_move_tests import PodMoveTest
from pylabrobot.beckman_coulter.biomek_i7.driver.recipes.tip_load import TipLoad
from pylabrobot.beckman_coulter.biomek_i7.layouts import place_labware
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_deck
from pylabrobot.resources import set_tip_tracking


class Head96TipTests(PodMoveTest):
  async def setup_deck(self):  # type: ignore[no-untyped-def]
    driver = await self.homed()
    lh = BiomekI7(driver=driver, deck=generic_deck())
    place_labware(
      lh.deck,
      [
        {"position": "TL1", "class": "BC1025F", "name": "BufferTips"},
        {"position": "TL4", "class": "BC230", "name": "SmallTips"},
      ],
    )
    head96 = self.pod(driver, "left").head96
    assert head96 is not None
    return driver, lh, head96

  async def test_pick_up_records_the_tips(self) -> None:
    driver, lh, head96 = await self.setup_deck()
    rack = lh.deck.get_resource("BufferTips")
    await head96.pick_up_tips(rack)
    self.assertEqual(sum(t is not None for t in head96.tips), 96)
    self.assertEqual(head96.tips[0], "beckman_coulter_biomek_tip_1025uL_filter")
    # As the STAR shows them: each tip on its mandrel's shaft in the head's model.
    assert head96.resource is not None
    shafts = head96.resource.get_all_items()
    self.assertTrue(all(shaft.has_tip() for shaft in shafts))
    self.assertEqual(len({shaft.tip.name for shaft in shafts}), 96)  # type: ignore[union-attr]
    with self.assertRaises(RuntimeError):
      await head96.pick_up_tips(rack)

  async def test_tips_load_only_on_a_tip_loading_position(self) -> None:
    # Biomek's "Multichannel Can Load Tips": the TipLoad1x1 ALP's position, not a static one.
    driver, lh, head96 = await self.setup_deck()
    place_labware(lh.deck, [{"position": "P5", "class": "BC230", "name": "StaticTips"}])
    with self.assertRaises(ValueError):
      head96.plan_pick_up(lh.deck.get_resource("StaticTips"))

  async def test_a_partial_load_counts_only_the_tips_under_the_head(self) -> None:
    driver, lh, head96 = await self.setup_deck()
    rack = lh.deck.get_resource("SmallTips")
    # Biomek's 12-tip loads: mandrel A1 over row H, so only row A meets tips.
    plan, load = head96.plan_pick_up(rack, tip_spot="H1")
    self.assertEqual(len(plan.pairs), 12)
    self.assertEqual(load.load_force, 518.75)
    self.assertEqual(
      load, TipLoad.parse("LT 43176 -50081 518.750 100 2.000 -48673 50 31216 -47926 50 43176")
    )

  async def test_tracked_racks_give_up_their_tips(self) -> None:
    set_tip_tracking(True)
    self.addCleanup(set_tip_tracking, False)
    driver, lh, head96 = await self.setup_deck()
    rack = lh.deck.get_resource("SmallTips")
    await head96.pick_up_tips(rack, tip_spot="H1")  # row H gone
    plan, load = head96.plan_pick_up(rack, tip_spot="G1")  # rows A, B over G, H: only G left
    self.assertEqual(len(plan.pairs), 12)
    self.assertIsNone(rack.get_item("H1").tip)
    # Back into the rack where they came from, then the rack holds them again.
    assert head96.resource is not None
    tip = head96.resource.get_item("A1").tip
    self.assertIsNotNone(tip)
    await head96.drop_tips(rack, tip_spot="H1")
    self.assertIs(rack.get_item("H1").tip, tip)  # the same tip, off the shaft and back in its spot
    self.assertTrue(all(t is None for t in head96.tips))
    self.assertFalse(any(shaft.has_tip() for shaft in head96.resource.get_all_items()))

  async def test_discard_and_eject(self) -> None:
    driver, lh, head96 = await self.setup_deck()
    rack = lh.deck.get_resource("SmallTips")
    await head96.pick_up_tips(rack)
    self.mark(driver)
    await head96.discard_tips()
    pod = self.pod(driver, "left")
    d = pod.declared
    assert d is not None
    # The eject ends with the plunger at 0 and the tips gone from the head.
    self.assertEqual(pod.cpositions["D"][1], d.to_counts("D", 0.0))
    self.assertTrue(all(t is None for t in head96.tips))
    assert head96.resource is not None
    self.assertFalse(any(shaft.has_tip() for shaft in head96.resource.get_all_items()))
    with self.assertRaises(RuntimeError):
      await head96.discard_tips()
