import math
import unittest
from typing import Any, Dict, List, Optional, Tuple, cast

from pylabrobot.hamilton.star.device import STAR, STARDevice
from pylabrobot.hamilton.star.driver.features.diagnostics import Diagnostics
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.tip_rack import TipRack, TipSpot

# Where the tools stand in these tests: each modelled centre plus an offset, in mm.
BACK_OFFSET = Coordinate(x=0.15, y=1.35, z=0)
FRONT_OFFSET = Coordinate(x=-0.24, y=-0.05, z=0)
OPENING_RADIUS = 3.6
TOP = 234.8


async def _set_up() -> STARDevice:
  star = STAR(simulation=True)
  await star.setup()
  return star


class TestWiring(unittest.IsolatedAsyncioTestCase):
  async def test_built_with_the_core_grippers(self):
    star = await _set_up()
    self.assertIsInstance(star.driver.diagnostics, Diagnostics)
    self.assertIs(star.diagnostics, star.driver.diagnostics)

  async def test_kept_across_a_second_setup(self):
    star = await _set_up()
    diagnostics = star.diagnostics
    await star.setup()
    self.assertIs(star.diagnostics, diagnostics)


class TestCircle(unittest.TestCase):
  def test_center_from_a_vertical_chord_and_a_point_on_its_bisector(self):
    center_x, center_y, radius = 1337.65, 126.45, 3.6
    x0 = 1337.5
    half_chord = math.sqrt(radius**2 - (x0 - center_x) ** 2)
    found = Diagnostics._compute_circle_center_x(
      x0, center_y + half_chord, center_y - half_chord, center_x - radius
    )
    self.assertAlmostEqual(found, center_x)


class _Probing(unittest.IsolatedAsyncioTestCase):
  """A STAR whose probes answer as tools standing at the offsets above would."""

  async def asyncSetUp(self):
    self.star = await _set_up()
    pipettes, diagnostics = self.star.pipettes, self.star.diagnostics
    assert pipettes is not None and diagnostics is not None
    self.pipettes = pipettes
    self.diagnostics = diagnostics
    deck = self.star.deck
    back, front = self.diagnostics._get_core_gripper_tools()
    self.centers = {
      "back": back.get_location_wrt(other=deck, x="c", y="c", z="t") + BACK_OFFSET,
      "front": front.get_location_wrt(other=deck, x="c", y="c", z="t") + FRONT_OFFSET,
    }
    self.top: Optional[float] = TOP
    self.sent: List[Tuple[str, str]] = []
    self.wire: List[Tuple[str, str, Dict[str, Any]]] = []
    answer = self.star.driver.send_command

    async def recording(module: str, command: str, **kwargs: Any):
      self.sent.append((module, command))
      self.wire.append((module, command, {k: v for k, v in kwargs.items() if len(k) == 2}))
      return await answer(module=module, command=command, **kwargs)

    self.star.driver.send_command = recording  # type: ignore[assignment]

    # The tool a channel probes: whichever centre lies nearer in Y.
    async def nearest(channel: int) -> Coordinate:
      y = await self.pipettes.request_y_position(channel=channel)
      return min(self.centers.values(), key=lambda center: abs(center.y - y))

    self.z_searches: List[Dict[str, Any]] = []

    async def probe_z(channel: int, **kwargs: Any) -> Optional[float]:
      self.z_searches.append(kwargs)
      return self.top

    async def probe_y(channel: int, direction: str, **kwargs: Any) -> float:
      center = await nearest(channel)
      x = await self.pipettes.request_x_position()
      half_chord = math.sqrt(OPENING_RADIUS**2 - (x - center.x) ** 2)
      return center.y + half_chord if direction == "backward" else center.y - half_chord

    async def probe_x(channel: int, direction: str, **kwargs: Any) -> float:
      center = await nearest(channel)
      y = await self.pipettes.request_y_position(channel=channel)
      return center.x - math.sqrt(OPENING_RADIUS**2 - (y - center.y) ** 2)

    self.pipettes.probe_z_using_clld = probe_z  # type: ignore[method-assign]
    self.pipettes.probe_y_using_clld = probe_y  # type: ignore[method-assign]
    self.pipettes.probe_x_using_clld = probe_x  # type: ignore[method-assign]

  async def _pick_up(self, channels: List[int]) -> None:
    deck = self.star.deck
    rack = cast(TipRack, deck.get_resource(deck.get_component_name("teaching_tip_rack")))
    spots: List[TipSpot] = [rack.get_item(index) for index in range(len(channels))]
    await self.pipettes.pick_up_tips(spots, use_channels=channels)
    self.sent.clear()

  def _moved(self) -> bool:
    # Anything but the tip-presence read
    return any((module, command) != ("C0", "RT") for module, command in self.sent)


class TestProbeCoreGripperToolCenters(_Probing):
  async def test_finds_both_tools(self):
    await self._pick_up([3, 4])
    found: Dict[int, Coordinate] = await self.diagnostics.probe_core_gripper_tool_centers(
      front_channel=4, top_probe_front_channel=1
    )
    for channel, tool in ((3, "back"), (4, "front")):
      self.assertAlmostEqual(found[channel].x, self.centers[tool].x, places=1)
      self.assertAlmostEqual(found[channel].y, self.centers[tool].y, places=1)
      self.assertAlmostEqual(found[channel].z, TOP - 2.0)

  async def test_searches_each_top_only_where_the_model_allows_it(self):
    await self._pick_up([3, 4])
    await self.diagnostics.probe_core_gripper_tool_centers(
      front_channel=4, top_probe_front_channel=1, max_top_deviation=1.0
    )
    deck = self.star.deck
    model_top = self.diagnostics._get_core_gripper_tools()[0].get_location_wrt(deck, z="t").z
    clearance = self.pipettes.search_start_clearance
    for search in self.z_searches:
      self.assertAlmostEqual(search["search_start_position"], model_top + 1.0 + clearance)
      self.assertAlmostEqual(search["search_end_position"], model_top - 1.0)

  async def test_takes_a_given_top_without_probing_one(self):
    await self._pick_up([0, 3, 4])  # no bare pair needed
    found = await self.diagnostics.probe_core_gripper_tool_centers(
      front_channel=4, modelled_z_top=TOP
    )
    self.assertEqual(self.z_searches, [])
    self.assertAlmostEqual(found[3].z, TOP - 2.0)
    self.assertAlmostEqual(found[4].z, TOP - 2.0)

  async def test_refuses_a_given_top_off_the_model_before_moving(self):
    for given in (TOP + 20.0, TOP - 20.0):
      with self.assertRaisesRegex(ValueError, "more than max_top_deviation"):
        await self.diagnostics.probe_core_gripper_tool_centers(
          front_channel=4, modelled_z_top=given
        )
    self.assertEqual(self.sent, [])

  async def test_refuses_a_given_top_outside_floor_and_safety_height(self):
    for given in (99.0, 250.0):
      with self.assertRaises(ValueError) as raised:
        await self.diagnostics.probe_core_gripper_tool_centers(
          front_channel=4, modelled_z_top=given
        )
      self.assertIn("between the Z drive's floor", str(raised.exception))
      self.assertIn("more than max_top_deviation", str(raised.exception))
    self.assertEqual(self.sent, [])

  async def test_refuses_both_or_neither_top_source(self):
    with self.assertRaisesRegex(ValueError, "not both"):
      await self.diagnostics.probe_core_gripper_tool_centers(
        front_channel=4, top_probe_front_channel=1, modelled_z_top=TOP
      )
    with self.assertRaisesRegex(ValueError, "not neither"):
      await self.diagnostics.probe_core_gripper_tool_centers(front_channel=4)
    self.assertEqual(self.sent, [])

  async def test_places_the_pair_and_its_neighbours_in_one_move(self):
    await self._pick_up([3, 4])
    await self.diagnostics.probe_core_gripper_tool_centers(
      front_channel=4, top_probe_front_channel=1
    )
    # Neighbours one search reach (half the tool's width plus max_center_deviation) and one channel
    # spacing past the centres, rounded away to the master's 0.1 mm step, with the pair.
    y_moves = [kwargs["yp"] for module, command, kwargs in self.wire if command == "JY"]
    self.assertIn("1403 1251 1069 0917", " ".join(str(yp) for yp in y_moves))

  async def test_probes_each_top_on_the_rim(self):
    await self._pick_up([3, 4])
    await self.diagnostics.probe_core_gripper_tool_centers(
      front_channel=4, top_probe_front_channel=1, top_probe_x_offset=2.0
    )
    x_moves = [kwargs.get("la") for module, command, kwargs in self.wire if command == "XP"]
    self.assertEqual(x_moves[0], "13395")  # 1337.5 + 2.0, in 0.1 mm

  async def test_refuses_overlapping_pairs_before_moving(self):
    await self._pick_up([0, 1])
    with self.assertRaises(ValueError):
      await self.diagnostics.probe_core_gripper_tool_centers(
        front_channel=1, top_probe_front_channel=2
      )
    self.assertEqual(self.sent, [])

  async def test_reports_every_argument_problem_at_once(self):
    with self.assertRaises(ValueError) as raised:
      await self.diagnostics.probe_core_gripper_tool_centers(
        front_channel=0, top_probe_front_channel=8, n_replicates=0
      )
    for problem in ("front_channel must be", "top_probe_front_channel must be", "n_replicates"):
      self.assertIn(problem, str(raised.exception))
    self.assertEqual(self.sent, [])

  async def test_reports_both_tip_problems_at_once(self):
    await self._pick_up([0, 3])
    with self.assertRaises(RuntimeError) as raised:
      await self.diagnostics.probe_core_gripper_tool_centers(
        front_channel=4, top_probe_front_channel=1
      )
    self.assertIn("channels [4] sense no needle or tip", str(raised.exception))
    self.assertIn("channels [0] sense a needle or tip", str(raised.exception))
    self.assertFalse(self._moved())

  async def test_stops_when_a_top_is_off_the_model(self):
    await self._pick_up([3, 4])
    self.top = TOP - 2.0
    with self.assertRaisesRegex(RuntimeError, "back tool's top is -2.2"):
      await self.diagnostics.probe_core_gripper_tool_centers(
        front_channel=4, top_probe_front_channel=1
      )

  async def test_reports_both_channels_missing_a_wall(self):
    await self._pick_up([3, 4])

    async def finds_nothing(channel: int, direction: str, **kwargs: Any) -> Optional[float]:
      return None

    self.pipettes.probe_y_using_clld = finds_nothing  # type: ignore[method-assign]
    with self.assertRaises(RuntimeError) as raised:
      await self.diagnostics.probe_core_gripper_tool_centers(
        front_channel=4, top_probe_front_channel=1
      )
    self.assertIn("channel 3 found no back wall within 6.17 mm", str(raised.exception))
    self.assertIn("channel 4 found no back wall within 6.17 mm", str(raised.exception))

  async def test_reports_both_tops_at_once(self):
    await self._pick_up([3, 4])
    self.top = None
    with self.assertRaises(RuntimeError) as raised:
      await self.diagnostics.probe_core_gripper_tool_centers(
        front_channel=4, top_probe_front_channel=1
      )
    self.assertIn("no top found on the back tool", str(raised.exception))
    self.assertIn("no top found on the front tool", str(raised.exception))


if __name__ == "__main__":
  unittest.main()
