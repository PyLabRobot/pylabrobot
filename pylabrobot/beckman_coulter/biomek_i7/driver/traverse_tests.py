"""Travel over the deck at a traverse height (design.md B9): `Pod._move_over`, the obstacles Biomek
keeps per pod, and the deck model's clearance."""

import unittest
from typing import List

from pylabrobot.beckman_coulter.biomek_i7.driver.planning import traverse
from pylabrobot.beckman_coulter.biomek_i7.driver.pod_move_tests import PodMoveTest


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
    await pod._move_over({"X": 100000}, minimum_traverse_height=25.0, z_final=20.0)
    self.assertEqual(pod.cpositions["Z"][1], d.to_counts("Z", 20.0))
    with self.assertRaisesRegex(ValueError, "z_final"):
      await pod._move_over({"Z": 0})

  async def test_a_tools_default_is_safe_max_or_a_height(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "left")
    d = pod.declared
    assert d is not None and pod.head96 is not None and pod.gripper is not None
    # From the instrument's `AlwaysMaxZ` (unset here): Biomek's safe height for both tools.
    self.assertEqual(pod.head96.default_minimum_traverse_height, "safe")
    self.assertEqual(pod.gripper.default_minimum_traverse_height, "safe")
    # With no deck to reckon from (no device), "safe" travels as high as it goes.
    self.assertEqual(pod._traverse_heights()["Z"], pod._max_traverse_heights()["Z"])
    pod.head96.default_minimum_traverse_height = 350.0  # mm of the head's lowest point
    await pod.move_axes({"Z": -20000})
    await pod._move_over({"X": 100000})
    self.assertEqual(pod.cpositions["Z"][1], d.to_counts("Z", 35.0))
    pod.head96.default_minimum_traverse_height = 990.0  # above the top: kept to the top of travel
    self.assertEqual(pod._traverse_heights()["Z"], pod._max_traverse_heights()["Z"])
    pod.head96.default_minimum_traverse_height = "max"
    self.assertEqual(pod._traverse_heights()["Z"], pod._max_traverse_heights()["Z"])
    with self.assertRaises(ValueError):
      pod.head96.default_minimum_traverse_height = "low"  # type: ignore[assignment]


def record(pod) -> List[dict]:  # type: ignore[no-untyped-def]
  """The targets of each move `pod` makes from now on."""
  moves: List[dict] = []
  original = pod.move_axes

  async def move_axes(targets, speed=1000, **kwargs):  # type: ignore[no-untyped-def]
    moves.append(dict(targets))
    return await original(targets, speed, **kwargs)

  pod.move_axes = move_axes
  return moves


class TurnTests(PodMoveTest):
  """The gripper's turn: to the angle given, as Biomek's GR is a line (no whole-turn twin)."""

  async def test_a_turn_goes_to_the_angle_given(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "left")
    d = pod.declared
    assert d is not None
    await pod.move_axes({"GR": d.to_counts("GR", 90.0)})
    moves = record(pod)
    # To -180: three quarters the long way round, not 180 a quarter turn away.
    await pod._travel_gripper({"GR": d.to_counts("GR", -180.0)})
    self.assertEqual([m["GR"] for m in moves if "GR" in m], [d.to_counts("GR", -180.0)])

  async def test_a_whole_turn_apart_is_another_angle(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "left")
    d = pod.declared
    assert d is not None
    await pod.move_axes({"GR": d.to_counts("GR", -270.0)})
    self.assertTrue(pod._at_angle(d.to_counts("GR", -270.0)))
    self.assertFalse(pod._at_angle(d.to_counts("GR", 90.0)))


class SpanTests(PodMoveTest):
  """Before a gripper approach a Span-8 narrows its probes' span, as Biomek's: to its minimum,
  with the probes raised first only when that is not clear."""

  async def test_the_span_is_narrowed_before_the_gripper_travels(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "right")
    d = pod.declared
    assert d is not None
    await pod.move_axes({"S": d.to_counts("S", 20.0)})
    moves = record(pod)
    await pod._travel_gripper({"GY": d.to_counts("GY", 50.0)})
    self.assertEqual(moves[0], {"S": d.to_counts("S", d.limits["Span"][0])})
    self.assertEqual(set(moves[-1]), {"GY"})
    moves.clear()
    await pod._travel_gripper({"GY": d.to_counts("GY", 52.0)})  # at its minimum already
    self.assertFalse(any("S" in m for m in moves))

  async def test_the_probes_rise_first_when_narrowing_is_not_clear(self) -> None:
    driver = await self.homed()
    pod = self.pod(driver, "right")
    d = pod.declared
    assert d is not None
    await pod.move_axes({"S": d.to_counts("S", 20.0), "Z1": d.to_counts("Z1", 20.0)})
    tops = pod._max_traverse_heights()
    clear = pod._clear

    def not_clear_until_probes_up(moves, start=None):  # type: ignore[no-untyped-def]
      return (
        "S" not in moves[0] or pod.cpositions["Z1"][1] >= d.to_counts("Z1", tops["Z1"])
      ) and clear(moves, start)

    # Narrowing is not clear until the probes are at the top.
    pod._clear = not_clear_until_probes_up  # type: ignore[method-assign]
    moves = record(pod)
    await pod._travel_gripper({"GY": d.to_counts("GY", 50.0)})
    self.assertEqual(moves[0], {"Z1": d.to_counts("Z1", tops["Z1"])})  # the one below the top
    self.assertEqual(moves[1], {"S": d.to_counts("S", d.limits["Span"][0])})


class SafeHeightTests(unittest.TestCase):
  """Biomek's travel height: the tallest deck item under the swept footprint (`travel`)."""

  def test_a_move_in_y_covers_its_bounding_box(self) -> None:
    item = ((10.0, 11.0, 30.0, 31.0), 7.0)
    self.assertEqual(traverse.covered((0, 2, 0, 2), (0, 2, 40, 42), [item]), [])
    self.assertEqual(traverse.covered((9, 11, 0, 2), (9, 11, 40, 42), [item]), [7.0])

  def test_a_diagonal_covers_its_band_not_its_bounding_box(self) -> None:
    # From (0..2, 0..2) to (40..42, 40..42): a box in the bounding box's far corner is off the band.
    off, on = ((35.0, 36.0, 0.0, 1.0), 5.0), ((20.0, 21.0, 20.0, 21.0), 6.0)
    self.assertEqual(traverse.covered((0, 2, 0, 2), (40, 42, 40, 42), [off, on]), [6.0])

  def test_touching_counts(self) -> None:
    # The footprint grows by 1-3 mm (its spans + 2 mm, rounded): an item 1 mm off still counts.
    self.assertEqual(traverse.covered((0, 2, 0, 2), (0, 2, 0, 2), [((2.1, 3, 0, 2), 4.0)]), [4.0])
    self.assertEqual(traverse.covered((0, 2, 0, 2), (0, 2, 0, 2), [((2.5, 3, 0, 2), 4.0)]), [])
    self.assertEqual(traverse.deck_height((0, 2, 0, 2), (0, 2, 0, 2), []), float("-inf"))


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
