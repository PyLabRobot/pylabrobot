import dataclasses
import math
import unittest
from typing import Any, Dict, List, Optional, Tuple, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.hamilton.star.conftest import BARE_X_ARM
from pylabrobot.hamilton.star.device import STAR
from pylabrobot.hamilton.star.driver.features.diagnostics import Diagnostics
from pylabrobot.hamilton.star.driver.features.x_arm_tests import RECORDED_DEVICE, declaring
from pylabrobot.hamilton.star.driver.master import STARDriver
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import STARDeck
from pylabrobot.resources.tip_rack import TipRack, TipSpot
from pylabrobot.testing.concurrency import AnyioTestBase

# Where the tools stand in these tests: each modelled centre plus an offset, in mm.
BACK_OFFSET = Coordinate(x=0.15, y=1.35, z=0)
FRONT_OFFSET = Coordinate(x=-0.24, y=-0.05, z=0)
OPENING_RADIUS = 3.6
TOP = 234.8


class TestWiring(AnyioTestBase):
  async def test_built_with_the_core_grippers(self) -> None:
    star = STAR(simulation=True)
    async with star:
      assert isinstance(star.driver.diagnostics, Diagnostics)
      assert star.diagnostics is star.driver.diagnostics

  async def test_kept_across_a_second_setup(self) -> None:
    star = STAR(simulation=True)
    async with star:
      diagnostics = star.diagnostics
    async with star:
      assert star.diagnostics is diagnostics

  async def test_reaches_pipettes_on_a_two_arm_device(self) -> None:
    both = dataclasses.replace(RECORDED_DEVICE, right_arm=BARE_X_ARM)
    driver = STARSimulationDriver(
      deck=STARDeck(), declared_configuration_json=declaring(device=both)
    )
    async with driver:
      assert driver.diagnostics is not None
      assert driver.left_x_arm is not None
      assert driver.diagnostics._pipettes is driver.left_x_arm.pipettes

  async def test_before_lifespan_entry_refuses_with_lifespan_hint(self) -> None:
    driver = STARDriver(deck=STARDeck())
    diagnostics = Diagnostics(driver)
    with pytest.raises(RuntimeError, match="entered its lifespan"):
      _ = diagnostics._pipettes


class TestCircle(unittest.TestCase):
  def test_center_from_a_vertical_chord_and_a_point_on_its_bisector(self) -> None:
    center_x, center_y, radius = 1337.65, 126.45, 3.6
    x0 = 1337.5
    half_chord = math.sqrt(radius**2 - (x0 - center_x) ** 2)
    found = Diagnostics._compute_circle_center_x(
      x0, center_y + half_chord, center_y - half_chord, center_x - radius
    )
    self.assertAlmostEqual(found, center_x)


class _Probing(AnyioTestBase):
  """A STAR whose probes answer as tools standing at the offsets above would."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    self.star = STAR(simulation=True)
    await stack.enter_async_context(self.star.lifespan())
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

    async def recording(module: str, command: str, **kwargs: Any) -> Any:
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
  async def test_finds_both_tools(self) -> None:
    await self._pick_up([3, 4])
    found: Dict[int, Coordinate] = await self.diagnostics.probe_core_gripper_tool_centers(
      front_channel=4, top_probe_front_channel=1
    )
    for channel, tool in ((3, "back"), (4, "front")):
      assert found[channel].x == pytest.approx(self.centers[tool].x, abs=0.05)
      assert found[channel].y == pytest.approx(self.centers[tool].y, abs=0.05)
      assert found[channel].z == pytest.approx(TOP - 2.0)

  async def test_searches_each_top_only_where_the_model_allows_it(self) -> None:
    await self._pick_up([3, 4])
    await self.diagnostics.probe_core_gripper_tool_centers(
      front_channel=4, top_probe_front_channel=1, max_top_deviation=1.0
    )
    deck = self.star.deck
    model_top = self.diagnostics._get_core_gripper_tools()[0].get_location_wrt(deck, z="t").z
    clearance = self.pipettes.search_start_clearance
    for search in self.z_searches:
      assert search["search_start_position"] == pytest.approx(model_top + 1.0 + clearance)
      assert search["search_end_position"] == pytest.approx(model_top - 1.0)

  async def test_takes_a_given_top_without_probing_one(self) -> None:
    await self._pick_up([0, 3, 4])  # no bare pair needed
    found = await self.diagnostics.probe_core_gripper_tool_centers(
      front_channel=4, modelled_z_top=TOP
    )
    assert self.z_searches == []
    assert found[3].z == pytest.approx(TOP - 2.0)
    assert found[4].z == pytest.approx(TOP - 2.0)

  async def test_refuses_a_given_top_off_the_model_before_moving(self) -> None:
    for given in (TOP + 20.0, TOP - 20.0):
      with pytest.raises(ValueError, match="more than max_top_deviation"):
        await self.diagnostics.probe_core_gripper_tool_centers(
          front_channel=4, modelled_z_top=given
        )
    assert self.sent == []

  async def test_refuses_a_given_top_outside_floor_and_safety_height(self) -> None:
    for given in (99.0, 250.0):
      with pytest.raises(ValueError) as raised:
        await self.diagnostics.probe_core_gripper_tool_centers(
          front_channel=4, modelled_z_top=given
        )
      assert "between the Z drive's floor" in str(raised.value)
      assert "more than max_top_deviation" in str(raised.value)
    assert self.sent == []

  async def test_refuses_both_or_neither_top_source(self) -> None:
    with pytest.raises(ValueError, match="not both"):
      await self.diagnostics.probe_core_gripper_tool_centers(
        front_channel=4, top_probe_front_channel=1, modelled_z_top=TOP
      )
    with pytest.raises(ValueError, match="not neither"):
      await self.diagnostics.probe_core_gripper_tool_centers(front_channel=4)
    assert self.sent == []

  async def test_places_the_pair_and_its_neighbours_in_one_move(self) -> None:
    await self._pick_up([3, 4])
    await self.diagnostics.probe_core_gripper_tool_centers(
      front_channel=4, top_probe_front_channel=1
    )
    # Neighbours one search reach (half the tool's width plus max_center_deviation) and one channel
    # spacing past the centres, rounded away to the master's 0.1 mm step, with the pair.
    y_moves = [kwargs["yp"] for _, command, kwargs in self.wire if command == "JY"]
    assert "1403 1251 1069 0917" in " ".join(str(yp) for yp in y_moves)

  async def test_probes_each_top_on_the_rim(self) -> None:
    await self._pick_up([3, 4])
    await self.diagnostics.probe_core_gripper_tool_centers(
      front_channel=4, top_probe_front_channel=1, top_probe_x_offset=2.0
    )
    x_moves = [kwargs.get("la") for _, command, kwargs in self.wire if command == "XP"]
    assert x_moves[0] == "13395"  # 1337.5 + 2.0, in 0.1 mm

  async def test_refuses_overlapping_pairs_before_moving(self) -> None:
    await self._pick_up([0, 1])
    with pytest.raises(ValueError, match="overlap"):
      await self.diagnostics.probe_core_gripper_tool_centers(
        front_channel=1, top_probe_front_channel=2
      )
    assert self.sent == []

  async def test_reports_every_argument_problem_at_once(self) -> None:
    with pytest.raises(ValueError) as raised:
      await self.diagnostics.probe_core_gripper_tool_centers(
        front_channel=0, top_probe_front_channel=8, n_replicates=0
      )
    for problem in ("front_channel must be", "top_probe_front_channel must be", "n_replicates"):
      assert problem in str(raised.value)
    assert self.sent == []

  async def test_reports_both_tip_problems_at_once(self) -> None:
    await self._pick_up([0, 3])
    with pytest.raises(RuntimeError) as raised:
      await self.diagnostics.probe_core_gripper_tool_centers(
        front_channel=4, top_probe_front_channel=1
      )
    assert "channels [4] sense no needle or tip" in str(raised.value)
    assert "channels [0] sense a needle or tip" in str(raised.value)
    assert not self._moved()

  async def test_stops_when_a_top_is_off_the_model(self) -> None:
    await self._pick_up([3, 4])
    self.top = TOP - 2.0
    with pytest.raises(RuntimeError, match="back tool's top is -2.2"):
      await self.diagnostics.probe_core_gripper_tool_centers(
        front_channel=4, top_probe_front_channel=1
      )
    assert [cmd for mod, cmd in self.sent if mod == "C0" and cmd != "RT"][-1] == "ZA"

  async def test_reports_both_channels_missing_a_wall(self) -> None:
    await self._pick_up([3, 4])

    async def finds_nothing(channel: int, direction: str, **kwargs: Any) -> Optional[float]:
      return None

    self.pipettes.probe_y_using_clld = finds_nothing  # type: ignore[method-assign]
    with pytest.raises(RuntimeError) as raised:
      await self.diagnostics.probe_core_gripper_tool_centers(
        front_channel=4, top_probe_front_channel=1
      )
    assert "channel 3 found no back wall within 6.17 mm" in str(raised.value)
    assert "channel 4 found no back wall within 6.17 mm" in str(raised.value)
    assert [cmd for mod, cmd in self.sent if mod == "C0" and cmd != "RT"][-1] == "ZA"

  async def test_reports_both_tops_at_once(self) -> None:
    await self._pick_up([3, 4])
    self.top = None
    with pytest.raises(RuntimeError) as raised:
      await self.diagnostics.probe_core_gripper_tool_centers(
        front_channel=4, top_probe_front_channel=1
      )
    assert "no top found on the back tool" in str(raised.value)
    assert "no top found on the front tool" in str(raised.value)
    assert [cmd for mod, cmd in self.sent if mod == "C0" and cmd != "RT"][-1] == "ZA"

  async def test_cancellation_during_top_probe_raises_channels_to_safe_z(self) -> None:
    await self._pick_up([3, 4])
    retracted_to_safe_z = False
    original_move_to_safe_z = self.pipettes.move_to_safe_z
    in_top_probe = False

    async def tracked_move_to_safe_z(*args: Any, **kwargs: Any) -> None:
      nonlocal retracted_to_safe_z
      await original_move_to_safe_z(*args, **kwargs)
      await anyio.lowlevel.checkpoint()
      if in_top_probe:
        retracted_to_safe_z = True

    self.pipettes.move_to_safe_z = tracked_move_to_safe_z  # type: ignore[method-assign]

    with anyio.fail_after(30):
      with anyio.CancelScope() as scope:

        async def cancelling_probe_z(channel: int, **kwargs: Any) -> Optional[float]:
          nonlocal in_top_probe
          in_top_probe = True
          scope.cancel()
          await anyio.sleep_forever()
          return None

        self.pipettes.probe_z_using_clld = cancelling_probe_z  # type: ignore[method-assign]
        await self.diagnostics.probe_core_gripper_tool_centers(
          front_channel=4, top_probe_front_channel=1
        )

    assert scope.cancelled_caught
    assert retracted_to_safe_z

  async def test_cancellation_during_y_wall_probe_raises_channels_to_safe_z(self) -> None:
    await self._pick_up([3, 4])
    retracted_to_safe_z = False
    original_move_to_safe_z = self.pipettes.move_to_safe_z
    in_y_probe = False

    async def tracked_move_to_safe_z(*args: Any, **kwargs: Any) -> None:
      nonlocal retracted_to_safe_z
      await original_move_to_safe_z(*args, **kwargs)
      await anyio.lowlevel.checkpoint()
      if in_y_probe:
        retracted_to_safe_z = True

    self.pipettes.move_to_safe_z = tracked_move_to_safe_z  # type: ignore[method-assign]

    with anyio.fail_after(30):
      with anyio.CancelScope() as scope:

        async def cancelling_probe_y(channel: int, direction: str, **kwargs: Any) -> float:
          nonlocal in_y_probe
          in_y_probe = True
          scope.cancel()
          await anyio.sleep_forever()
          return 0.0

        self.pipettes.probe_y_using_clld = cancelling_probe_y  # type: ignore[method-assign]
        await self.diagnostics.probe_core_gripper_tool_centers(front_channel=4, modelled_z_top=TOP)

    assert scope.cancelled_caught
    assert retracted_to_safe_z

  async def test_cancellation_during_x_wall_probe_raises_channels_to_safe_z(self) -> None:
    await self._pick_up([3, 4])
    retracted_to_safe_z = False
    original_move_to_safe_z = self.pipettes.move_to_safe_z
    in_x_probe = False

    async def tracked_move_to_safe_z(*args: Any, **kwargs: Any) -> None:
      nonlocal retracted_to_safe_z
      await original_move_to_safe_z(*args, **kwargs)
      await anyio.lowlevel.checkpoint()
      if in_x_probe:
        retracted_to_safe_z = True

    self.pipettes.move_to_safe_z = tracked_move_to_safe_z  # type: ignore[method-assign]

    with anyio.fail_after(30):
      with anyio.CancelScope() as scope:

        async def cancelling_probe_x(channel: int, direction: str, **kwargs: Any) -> float:
          nonlocal in_x_probe
          in_x_probe = True
          scope.cancel()
          await anyio.sleep_forever()
          return 0.0

        self.pipettes.probe_x_using_clld = cancelling_probe_x  # type: ignore[method-assign]
        await self.diagnostics.probe_core_gripper_tool_centers(front_channel=4, modelled_z_top=TOP)

    assert scope.cancelled_caught
    assert retracted_to_safe_z
