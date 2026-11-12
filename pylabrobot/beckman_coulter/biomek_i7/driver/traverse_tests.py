"""Travel over the deck at a traverse height (design.md B9): `Pod._move_over`, the obstacles Biomek
keeps per pod, and the deck model's clearance."""

import unittest

from pylabrobot.beckman_coulter.biomek_i7.device import BiomekI7
from pylabrobot.beckman_coulter.biomek_i7.driver import pod_geometry as g
from pylabrobot.beckman_coulter.biomek_i7.driver.pod_move_tests import PodMoveTest
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  generic_configuration,
  generic_deck,
)


class ObstacleTests(unittest.TestCase):
  def setUp(self) -> None:
    self.obstacles = generic_configuration().pod("left").obstacles

  def test_the_pump_bank_is_cleared_from_above(self) -> None:
    # The pump bank: X from 121.044, Y to 13.447 cm, up to Z 37.069, buffer 0.2.
    self.assertAlmostEqual(g.obstacle_clearance(self.obstacles, (125, 130), (5, 12)).height, 37.269)
    # Elsewhere only the floor (Z to 0, buffer 0.2).
    self.assertAlmostEqual(g.obstacle_clearance(self.obstacles, (50, 60), (30, 40)).height, 0.2)

  def test_walls_and_ceilings_are_not_cleared(self) -> None:
    self.assertEqual(
      g.obstacle_clearance(self.obstacles, (157, 160), (30, 40)).walls, ("RightShield",)
    )
    # The I-beam (from Z 37.069 behind Y 11.092) bounds the pod's body: under it is refused.
    self.assertEqual(g.obstacle_clearance(self.obstacles, (50, 60), (5, 10)).walls, ("IBeam",))
    # Just clear of it, with its 0.2 cm buffer.
    self.assertEqual(g.obstacle_clearance(self.obstacles, (50, 60), (11.3, 20)).walls, ())


class MoveOverTests(PodMoveTest):
  async def test_lifts_then_travels_at_the_park_height(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "left")
    await pod.move_axes({"Z": -20000, "GZ": -3000})
    self.mark(driver)
    await pod._move_over({"X": 100000})
    d = pod.declared
    assert d is not None
    # Z to its rest height, 39.763 cm (-256 counts), and GZ to its own, then X alone.
    self.assertEqual(d.to_counts("Z", pod._traverse_heights()["Z"]), -256)
    self.assertEqual(pod.cpositions["Z"][1], -256)
    self.assertEqual(pod.cpositions["GZ"][1], d.to_counts("GZ", d.limits["GZ"][1] - 0.1))
    self.assertEqual(pod.cpositions["X"][1], 100000)
    moves = [n for n, _ in self.sent(driver, "multichannel") if n == "Move All"]
    self.assertEqual(len(moves), 2)

  async def test_an_axis_above_the_traverse_height_is_not_lowered(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "left")
    await pod.move_axes({"Z": 0, "GZ": 0})  # the top of travel
    await pod._move_over({"X": 100000})
    self.assertEqual((pod.cpositions["Z"][1], pod.cpositions["GZ"][1]), (0, 0))

  async def test_an_override_is_checked_and_z_final_set(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "left")
    d = pod.declared
    assert d is not None
    self.mark(driver)
    with self.assertRaisesRegex(ValueError, "below"):
      await pod._move_over({"X": 100000}, minimum_traverse_height=25.0, clearance=30.0)
    self.assertNotIn("Move All", [n for n, _ in self.sent(driver, "multichannel")])
    await pod._move_over(
      {"X": 100000},
      minimum_traverse_height=25.0,
      clearance=30.0,
      allow_below_clearance=True,
      z_final=20.0,
    )
    self.assertEqual(pod.cpositions["Z"][1], d.to_counts("Z", 20.0))
    with self.assertRaisesRegex(ValueError, "z_final"):
      await pod._move_over({"Z": 0})

  async def test_the_default_is_biomeks_safe_height_when_set(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "left")
    d = pod.declared
    assert d is not None
    self.assertIsNone(pod.default_minimum_traverse_height)  # this instrument: -100000, unset
    pod.default_minimum_traverse_height = 350.0  # mm
    await pod.move_axes({"Z": -20000})
    await pod._move_over({"X": 100000})
    self.assertEqual(pod.cpositions["Z"][1], d.to_counts("Z", 35.0))
    pod.default_minimum_traverse_height = 990.0  # mm, above the top: kept to the top of travel
    self.assertEqual(pod._traverse_heights()["Z"], d.limits["Z"][1] - 0.1)


class DeckClearanceTests(PodMoveTest):
  async def test_the_tallest_labware_under_the_path(self) -> None:
    driver = await self.homed()
    lh = BiomekI7(driver=driver, deck=generic_deck())
    site = lh.deck.get_position("P6")
    plate = cor_96_wellplate_360uL_Fb("plate")
    site.assign_child_resource(plate)
    left = self.pod(driver, "left")
    assert left.declared is not None
    loc = site.get_location_wrt(lh.deck)
    # Biomek X/Y of the position's middle, cm.
    x = (loc.x + site.get_size_x() / 2 - 100.0) / 10  # deck x = Biomek X + FRAME_X (10 cm)
    y = (700.0 - (loc.y + site.get_size_y() / 2)) / 10  # deck y = FRAME_Y (70 cm) - Biomek Y
    top = (loc.z + plate.get_size_z()) / 10
    safe = left.declared.geometry.additional_safe_height
    self.assertAlmostEqual(
      lh._traverse_clearance("left", (x - 1, x + 1), (y - 1, y + 1)), top + safe, places=4
    )
    self.assertAlmostEqual(
      lh._traverse_clearance("left", (x - 1, x + 1), (y - 1, y + 1), tip_length=9.0),
      top + safe + 9.0,
      places=4,
    )


class PublicTravelInMillimetresTests(PodMoveTest):
  """The public travel and traverse API is mm (and deck frame); its Biomek-unit twins are `_`."""

  async def test_move_over_in_mm_sends_what_its_native_twin_sends(self) -> None:
    from pylabrobot.beckman_coulter.biomek_i7 import units

    native, public = await self.homed(), await self.homed()
    for driver in (native, public):
      await self.pod(driver, "right").move_aside()
    pn, pp = self.pod(native, "left"), self.pod(public, "left")
    d = pn.declared
    assert d is not None
    x_counts, y_counts = 100000, 50000
    self.mark(native)
    await pn._move_over({"X": x_counts, "Y": y_counts}, 500, minimum_traverse_height=30.0)
    x = units.counts_to_axis(pp, "X", x_counts)
    y = units.counts_to_axis(pp, "Y", y_counts)
    self.mark(public)
    await pp.move_over({"X": x, "Y": y}, speed_fraction=0.5, minimum_traverse_height=300.0)
    self.assertEqual(self.sent(public, "multichannel"), self.sent(native, "multichannel"))
    self.assertEqual(pp.cpositions["X"][1], x_counts)

  async def test_traverse_figures_are_mm(self) -> None:
    pod = self.pod(await self.homed(), "left")
    for axis, cm_value in pod._traverse_heights().items():
      self.assertAlmostEqual(pod.traverse_heights()[axis], cm_value * 10)
    for axis, cm_value in pod._traverse_ceilings().items():
      self.assertAlmostEqual(pod.traverse_ceilings()[axis], cm_value * 10)
    with self.assertRaises(ValueError):
      await pod.move_over({"X": 500.0}, speed_fraction=1.5)
