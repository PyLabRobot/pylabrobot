import asyncio
import threading
import time
import unittest
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest

pytest.importorskip("ot_api")

from pylabrobot.legacy.liquid_handling import LiquidHandler
from pylabrobot.legacy.liquid_handling.backends.opentrons_backend import (
  _OT_DECK_IS_ADDRESSABLE_AREA_VERSION,
  OpentronsOT2Backend,
)
from pylabrobot.legacy.liquid_handling.errors import NoChannelError
from pylabrobot.legacy.liquid_handling.standard import (
  Drop,
  Pickup,
  SingleChannelAspiration,
)
from pylabrobot.resources import Coordinate, Tip, no_volume_tracking
from pylabrobot.resources.celltreat import celltreat_96_wellplate_350uL_Fb
from pylabrobot.resources.opentrons import OTDeck, opentrons_96_filtertiprack_20ul
from pylabrobot.resources.well import Well

_PIPETTES = (
  {"pipetteId": "left-pipette-id", "name": "p20_single_gen2"},
  {"pipetteId": "right-pipette-id", "name": "p20_single_gen2"},
)


def _mock_define(lw):
  return {"data": {"definitionUri": f'lw["namespace"]/{lw["metadata"]["displayName"]}/1'}}


def _mock_health_get():
  return {
    "api_version": "7.0.1",
  }


class _FakeRobot:
  """Stands in for the robot-server's command queue.

  Records what the backend enqueues and answers every poll "succeeded", so a test
  can read the exact wire params the robot would have been handed.
  """

  def __init__(self, position: Tuple[float, float, float] = (0.0, 0.0, 0.0)) -> None:
    self.commands: List[Tuple[str, Dict[str, Any]]] = []
    self.position = position
    self.error: Optional[Dict[str, str]] = None

  def enqueue_command(self, command_type, params, intent="setup", **kwargs) -> str:
    self.commands.append((command_type, dict(params)))
    return f"cmd-{len(self.commands)}"

  def get_command(self, command_id, **kwargs) -> Dict[str, Any]:
    if self.error is not None:
      return {"data": {"status": "failed", "error": self.error}}
    x, y, z = self.position
    return {"data": {"status": "succeeded", "result": {"position": {"x": x, "y": y, "z": z}}}}

  def command_types(self) -> List[str]:
    return [command_type for command_type, _params in self.commands]

  def params(self, command_type: str) -> Dict[str, Any]:
    return dict(self.commands)[command_type]


class OpentronsBackendSetupTests(unittest.IsolatedAsyncioTestCase):
  """Tests for setup and stop"""

  @patch("ot_api.runs.create")
  @patch("ot_api.health.home")
  @patch("ot_api.lh.add_mounted_pipettes")
  @patch("ot_api.health.get")
  async def test_setup(
    self,
    mock_health_get,
    mock_add_mounted_pipettes,
    mock_home,
    mock_create,
  ):
    mock_create.return_value = "run-id"
    mock_add_mounted_pipettes.return_value = _PIPETTES
    mock_health_get.side_effect = _mock_health_get

    self.backend = OpentronsOT2Backend(host="localhost", port=1338)
    self.lh = LiquidHandler(backend=self.backend, deck=OTDeck())
    await self.lh.setup()

  def test_serialize(self):
    serialized = OpentronsOT2Backend(host="localhost", port=1337).serialize()
    self.assertEqual(
      serialized,
      {"type": "OpentronsOT2Backend", "host": "localhost", "port": 1337},
    )
    self.assertEqual(
      OpentronsOT2Backend.deserialize(serialized).__class__.__name__,
      "OpentronsOT2Backend",
    )

  def test_a_budget_of_zero_or_less_is_refused(self):
    """A zero poll interval spins the robot-server; a zero command budget never polls."""
    for kwargs in (
      {"request_timeout": 0.0},
      {"command_timeout": -1.0},
      {"status_poll_interval": 0.0},
    ):
      with self.subTest(**kwargs):
        with self.assertRaises(ValueError):
          OpentronsOT2Backend(host="localhost", port=1338, **kwargs)


class OpentronsBackendCommandTests(unittest.IsolatedAsyncioTestCase):
  """Tests Opentrons commands.

  The robot is faked at the command queue (``runs.enqueue_command`` /
  ``runs.get_command``), which is where the backend actually reaches it, so every
  assertion here is about the params a real robot would receive.
  """

  async def asyncSetUp(self):
    self.robot = _FakeRobot()
    for target, kwargs in (
      ("ot_api.runs.create", {"return_value": "run-id"}),
      ("ot_api.runs.enqueue_command", {"side_effect": self.robot.enqueue_command}),
      ("ot_api.runs.get_command", {"side_effect": self.robot.get_command}),
      ("ot_api.lh.add_mounted_pipettes", {"return_value": _PIPETTES}),
      ("ot_api.health.get", {"side_effect": _mock_health_get}),
      ("ot_api.health.home", {}),
      ("ot_api.labware.define", {"side_effect": _mock_define}),
    ):
      patcher = patch(target, **kwargs)
      patcher.start()
      self.addCleanup(patcher.stop)

    self.backend = OpentronsOT2Backend(host="localhost", port=1338)
    self.deck = OTDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    await self.lh.setup()

    self.tip_rack = opentrons_96_filtertiprack_20ul(name="tip_rack")
    self.deck.assign_child_at_slot(self.tip_rack, slot=1)
    self.plate = celltreat_96_wellplate_350uL_Fb(name="plate")
    self.deck.assign_child_at_slot(self.plate, slot=11)
    self.robot.commands.clear()

  async def test_tip_pick_up(self):
    await self.lh.pick_up_tips(self.tip_rack["A1"])

    params = self.robot.params("pickUpTip")
    self.assertEqual(params["labwareId"], self.backend.get_ot_name("tip_rack"))
    self.assertEqual(
      params["wellName"], self.backend.get_ot_name(self.tip_rack.get_item("A1").name)
    )
    self.assertEqual(params["pipetteId"], "left-pipette-id")
    self.assertEqual(params["wellLocation"]["origin"], "bottom")

  async def test_a_tip_rack_is_loaded_into_the_run_before_its_first_pickup(self):
    await self.lh.pick_up_tips(self.tip_rack["A1"])

    params = self.robot.params("loadLabware")
    self.assertEqual(params["location"], {"slotName": "1"})
    self.assertEqual(params["labwareId"], self.backend.get_ot_name("tip_rack"))
    self.assertLess(
      self.robot.command_types().index("loadLabware"),
      self.robot.command_types().index("pickUpTip"),
    )

  async def test_tip_drop(self):
    await self.lh.pick_up_tips(self.tip_rack["A1"])
    await self.lh.drop_tips(self.tip_rack["A1"])

    params = self.robot.params("dropTip")
    self.assertEqual(
      params["wellName"], self.backend.get_ot_name(self.tip_rack.get_item("A1").name)
    )
    self.assertEqual(params["pipetteId"], "left-pipette-id")

  async def test_get_channel_position_asks_the_robot_to_save_its_position(self):
    self.robot.position = (11.0, 22.0, 33.0)

    position = await self.backend.get_channel_position(0)

    self.assertEqual(position, Coordinate(11.0, 22.0, 33.0))
    self.assertEqual(self.robot.params("savePosition"), {"pipetteId": "left-pipette-id"})

  async def test_a_failed_save_position_names_the_robot_error(self):
    self.robot.error = {"errorType": "MustHomeError", "detail": "Must home first"}

    with self.assertRaises(RuntimeError) as caught:
      await self.backend.get_channel_position(0)

    self.assertIn("MustHomeError", str(caught.exception))

  async def test_move_channel_to_travels_once_holding_the_axes_left_out(self):
    self.robot.position = (11.0, 22.0, 33.0)

    await self.backend.move_channel_to(0, x=50.0, z=5.0)

    self.assertEqual(self.robot.command_types().count("moveToCoordinates"), 1)
    params = self.robot.params("moveToCoordinates")
    self.assertEqual(params["coordinates"]["x"], 50.0)
    self.assertEqual(params["coordinates"]["y"], 22.0)  # not named, so held
    self.assertEqual(params["coordinates"]["z"], 5.0)
    self.assertEqual(params["minimumZHeight"], self.backend.traversal_height)

  async def test_aspirate(self):
    await self.lh.pick_up_tips(self.tip_rack["A1"])
    self.plate.get_well("A1").tracker.set_volume(10)

    await self.lh.aspirate(self.plate["A1"], vols=[10])

    self.assertEqual(
      self.robot.params("aspirateInPlace"),
      {"flowRate": 3.78, "volume": 10, "pipetteId": "left-pipette-id"},
    )

  async def test_dispense(self):
    await self.test_aspirate()
    with no_volume_tracking():
      await self.lh.dispense(self.plate["A1"], vols=[10])

    self.assertEqual(
      self.robot.params("dispenseInPlace"),
      {"flowRate": 7.56, "volume": 10, "pipetteId": "left-pipette-id", "pushOut": False},
    )

  async def test_a_motion_command_never_goes_through_ot_apis_capped_wrapper(self):
    """``ot_api``'s ``@command`` decorator hard-codes a 30s ceiling and takes no
    ``timeout`` kwarg, so anything routed through it ignores ``command_timeout``."""
    with patch("ot_api.lh.aspirate_in_place") as capped_wrapper:
      await self.lh.pick_up_tips(self.tip_rack["A1"])
      self.plate.get_well("A1").tracker.set_volume(10)
      await self.lh.aspirate(self.plate["A1"], vols=[10])

    capped_wrapper.assert_not_called()
    self.assertIn("aspirateInPlace", self.robot.command_types())

  async def test_a_command_may_outlast_a_single_requests_budget(self):
    """The request budget bounds one exchange; the command budget bounds the motion.
    A move that inherited the request budget could never wait out a real motion."""
    answers = ["running", "running", "running", "succeeded"]

    def still_moving(command_id, **kwargs):
      return {"data": {"status": answers.pop(0), "result": {}}}

    self.backend.request_timeout = 0.05
    self.backend.command_timeout = 5.0
    self.backend.status_poll_interval = 0.05
    with patch("ot_api.runs.get_command", side_effect=still_moving):
      started = time.monotonic()
      await self.backend.move_pipette_head(Coordinate(1.0, 2.0, 3.0), pipette_id="left")

    self.assertGreater(time.monotonic() - started, self.backend.request_timeout)
    self.assertEqual(answers, [])

  async def test_home_calls_health_home(self):
    """home() issues exactly one ot_api.health.home() call."""
    with patch("ot_api.health.home") as mock_home:
      await self.backend.home()
    mock_home.assert_called_once_with()

  async def test_list_connected_modules_passthrough(self):
    """list_connected_modules() returns ot_api.modules.list_connected_modules() verbatim."""
    with patch("ot_api.modules.list_connected_modules") as mock_modules:
      mock_modules.return_value = [{"id": "tempdeck"}]
      result = await self.backend.list_connected_modules()
    mock_modules.assert_called_once_with()
    self.assertEqual(result, [{"id": "tempdeck"}])

  @patch("ot_api.run_id", "run-id", create=True)
  async def test_stop_halts_the_run_then_releases_it(self):
    """stop() halts the run before deleting it: deleting a run the robot is still
    working through leaves it working."""
    with patch("ot_api.requestor.post") as mock_post, patch("ot_api.requestor.delete") as mock_del:
      await self.backend.stop()

    mock_post.assert_called_once_with("/runs/run-id/actions", {"data": {"actionType": "stop"}})
    mock_del.assert_called_once_with("/runs/run-id")
    self.assertIsNone(self.backend.left_pipette)
    self.assertIsNone(self.backend.right_pipette)

  async def test_tip_drop_to_trash_uses_addressable_area(self):
    """At api_version >= 7.1.0 a discard to the deck trash routes via the addressable
    area (moveToAddressableAreaForDropTip + dropTipInPlace), not dropTip."""
    self.backend.ot_api_version = _OT_DECK_IS_ADDRESSABLE_AREA_VERSION

    await self.lh.pick_up_tips(self.tip_rack["A1"])
    await self.lh.discard_tips()

    types = self.robot.command_types()
    self.assertEqual(types.count("moveToAddressableAreaForDropTip"), 1)
    self.assertEqual(types.count("dropTipInPlace"), 1)
    self.assertNotIn("dropTip", types)


def _make_backend_with_pipettes(left_name="p300_single_gen2", right_name="p20_single_gen2"):
  """Create a backend with pipette state set directly (no ot_api needed)."""
  backend = OpentronsOT2Backend.__new__(OpentronsOT2Backend)
  backend.left_pipette = {"name": left_name, "pipetteId": "left-id"} if left_name else None
  backend.right_pipette = {"name": right_name, "pipetteId": "right-id"} if right_name else None
  backend.left_pipette_has_tip = False
  backend.right_pipette_has_tip = False
  return backend


class OpentronsSharedHelperTests(unittest.TestCase):
  """Tests for _get_pickup_pipette, _get_drop_pipette, _get_liquid_pipette, _set_tip_state."""

  def setUp(self):
    self.backend = _make_backend_with_pipettes()
    self.deck = OTDeck()
    self.tip_rack = opentrons_96_filtertiprack_20ul(name="tip_rack")
    self.deck.assign_child_at_slot(self.tip_rack, slot=1)
    self.tip_spot = self.tip_rack.get_item("A1")
    self.tip_20 = Tip(
      has_filter=True,
      total_tip_length=39.2,
      maximal_volume=20,
      fitting_depth=8.25,
      name="test_tip_20",
    )
    self.tip_300 = Tip(
      has_filter=False,
      total_tip_length=51.0,
      maximal_volume=300,
      fitting_depth=8.0,
      name="test_tip_300",
    )

  # -- _get_pickup_pipette --

  def test_get_pickup_pipette_selects_right_for_20ul(self):
    ops = [Pickup(resource=self.tip_spot, offset=Coordinate.zero(), tip=self.tip_20)]
    self.assertEqual(self.backend._get_pickup_pipette(ops), "right-id")

  def test_get_pickup_pipette_selects_left_for_300ul(self):
    ops = [Pickup(resource=self.tip_spot, offset=Coordinate.zero(), tip=self.tip_300)]
    self.assertEqual(self.backend._get_pickup_pipette(ops), "left-id")

  def test_get_pickup_pipette_raises_when_tip_already_mounted(self):
    self.backend.right_pipette_has_tip = True
    ops = [Pickup(resource=self.tip_spot, offset=Coordinate.zero(), tip=self.tip_20)]
    with self.assertRaises(NoChannelError):
      self.backend._get_pickup_pipette(ops)

  # -- _deck_to_robot_frame --

  def test_deck_to_robot_frame_maps_slot1_corner_to_robot_origin(self):
    """The deck->robot transform subtracts slot 1's corner, so a deck-frame point at slot 1's
    corner becomes the robot origin and a point offset from it keeps that offset."""
    self.backend.set_deck(self.deck)
    corner = self.deck.slot_locations[0]
    self.assertEqual(self.backend._deck_to_robot_frame(corner), Coordinate(0, 0, 0))
    self.assertEqual(
      self.backend._deck_to_robot_frame(corner + Coordinate(10, 20, 3)),
      Coordinate(10, 20, 3),
    )

  # -- _get_drop_pipette --

  def test_get_drop_pipette_selects_right_for_20ul(self):
    self.backend.right_pipette_has_tip = True
    ops = [Drop(resource=self.tip_spot, offset=Coordinate.zero(), tip=self.tip_20)]
    self.assertEqual(self.backend._get_drop_pipette(ops), "right-id")

  def test_get_drop_pipette_raises_when_no_tip(self):
    ops = [Drop(resource=self.tip_spot, offset=Coordinate.zero(), tip=self.tip_20)]
    with self.assertRaises(NoChannelError):
      self.backend._get_drop_pipette(ops)

  # -- _get_liquid_pipette --

  def test_get_liquid_pipette_selects_left_for_large_volume(self):
    self.backend.left_pipette_has_tip = True
    well = Well(name="w", size_x=5, size_y=5, size_z=10, max_volume=350)
    ops = [
      SingleChannelAspiration(
        resource=well,
        offset=Coordinate.zero(),
        tip=self.tip_300,
        volume=100,
        flow_rate=None,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      )
    ]
    self.assertEqual(self.backend._get_liquid_pipette(ops), "left-id")

  def test_get_liquid_pipette_selects_right_for_small_volume(self):
    self.backend.right_pipette_has_tip = True
    well = Well(name="w", size_x=5, size_y=5, size_z=10, max_volume=350)
    ops = [
      SingleChannelAspiration(
        resource=well,
        offset=Coordinate.zero(),
        tip=self.tip_20,
        volume=5,
        flow_rate=None,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      )
    ]
    self.assertEqual(self.backend._get_liquid_pipette(ops), "right-id")

  def test_get_liquid_pipette_raises_without_tip(self):
    well = Well(name="w", size_x=5, size_y=5, size_z=10, max_volume=350)
    ops = [
      SingleChannelAspiration(
        resource=well,
        offset=Coordinate.zero(),
        tip=self.tip_20,
        volume=5,
        flow_rate=None,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      )
    ]
    with self.assertRaises(NoChannelError):
      self.backend._get_liquid_pipette(ops)

  # -- _set_tip_state --

  def test_set_tip_state_left(self):
    self.backend._set_tip_state("left-id", True)
    self.assertTrue(self.backend.left_pipette_has_tip)
    self.assertFalse(self.backend.right_pipette_has_tip)

  def test_set_tip_state_right(self):
    self.backend._set_tip_state("right-id", True)
    self.assertFalse(self.backend.left_pipette_has_tip)
    self.assertTrue(self.backend.right_pipette_has_tip)


class OpentronsBackendTimeoutTests(unittest.IsolatedAsyncioTestCase):
  """A robot that stops answering must not hang the process or freeze the event loop.

  ``ot_api`` calls ``urlopen`` with no socket timeout, so an unanswered request blocks
  its thread for good. These pin that the backend stops waiting on its own, that the
  rest of the process keeps running while it waits, and that giving up leaves neither
  the loop nor the robot in a state the next caller can walk into.
  """

  def _backend(self, **kwargs: float) -> OpentronsOT2Backend:
    backend = OpentronsOT2Backend(host="localhost", port=1338, **kwargs)
    backend.left_pipette = {"pipetteId": "left-pipette-id", "name": "p20_single_gen2"}
    backend.right_pipette = None
    return backend

  @patch("ot_api.runs.get_command")
  @patch("ot_api.runs.enqueue_command")
  async def test_a_position_read_that_never_answers_fails_at_the_deadline(
    self, mock_enqueue, mock_get_command
  ):
    mock_enqueue.return_value = "cmd-1"
    released = threading.Event()
    mock_get_command.side_effect = lambda *a, **kw: released.wait()

    backend = self._backend(request_timeout=0.2)
    started = time.monotonic()
    try:
      with self.assertRaises(RuntimeError) as caught:
        await backend.get_channel_position(0)
    finally:
      released.set()  # let the abandoned thread finish, so the suite can exit

    self.assertIsInstance(caught.exception.__cause__, TimeoutError)
    self.assertLess(time.monotonic() - started, 5.0)

  @patch("ot_api.runs.get_command")
  @patch("ot_api.runs.enqueue_command")
  async def test_a_slow_position_read_leaves_the_event_loop_free(
    self, mock_enqueue, mock_get_command
  ):
    mock_enqueue.return_value = "cmd-1"

    def slow_answer(*args, **kwargs):
      time.sleep(0.3)
      return {
        "data": {"status": "succeeded", "result": {"position": {"x": 1.0, "y": 2.0, "z": 3.0}}}
      }

    mock_get_command.side_effect = slow_answer

    ticks = 0

    async def tick():
      nonlocal ticks
      while True:
        await asyncio.sleep(0.01)
        ticks += 1

    ticker = asyncio.ensure_future(tick())
    try:
      position = await self._backend().get_channel_position(0)
    finally:
      ticker.cancel()

    self.assertEqual(position, Coordinate(1.0, 2.0, 3.0))
    self.assertGreater(ticks, 1)  # zero or one means the read blocked the loop

  @patch("ot_api.health.home")
  async def test_a_robot_command_gets_the_longer_budget(self, mock_home):
    mock_home.side_effect = lambda *a, **kw: time.sleep(0.3)

    # A move outlasts the plain request budget on purpose: it waits on the motion.
    await self._backend(request_timeout=0.05, command_timeout=5.0).home()

    mock_home.assert_called_once()

  @patch("ot_api.modules.list_connected_modules")
  async def test_a_plain_read_gets_the_shorter_budget(self, mock_list):
    released = threading.Event()
    mock_list.side_effect = lambda *a, **kw: released.wait()

    backend = self._backend(request_timeout=0.2, command_timeout=60.0)
    started = time.monotonic()
    try:
      with self.assertRaises(TimeoutError):
        await backend.list_connected_modules()
    finally:
      released.set()

    self.assertLess(time.monotonic() - started, 5.0)

  @patch("ot_api.modules.list_connected_modules")
  async def test_a_request_queued_behind_another_still_gives_up_at_its_own_budget(self, mock_list):
    """The budget is what a caller is told a read costs. Starting the clock only after
    the queue lets a 7s read block for as long as whatever is ahead of it takes."""
    released = threading.Event()
    mock_list.side_effect = lambda *a, **kw: released.wait()

    backend = self._backend(request_timeout=0.3)
    holder = asyncio.ensure_future(backend.list_connected_modules())
    await asyncio.sleep(0.05)  # let the holder take the lock

    started = time.monotonic()
    try:
      with self.assertRaises(TimeoutError):
        await asyncio.wait_for(backend.list_connected_modules(), timeout=5.0)
      elapsed = time.monotonic() - started
    finally:
      released.set()
      holder.cancel()

    self.assertLess(elapsed, 2.0)

  @patch("ot_api.modules.list_connected_modules")
  async def test_an_abandoned_request_does_not_hold_the_loops_default_executor(self, mock_list):
    """``asyncio.run()`` joins the default executor's threads on the way out, so a
    request parked there moves the hang from mid-command to shutdown, and burns a
    pool slot every other backend in the process shares."""
    released = threading.Event()
    mock_list.side_effect = lambda *a, **kw: released.wait()

    backend = self._backend(request_timeout=0.2)
    try:
      with self.assertRaises(TimeoutError):
        await backend.list_connected_modules()
      loop = asyncio.get_running_loop()
      await asyncio.wait_for(loop.shutdown_default_executor(), timeout=3.0)
    finally:
      released.set()

  @patch("ot_api.requestor.post")
  @patch("ot_api.runs.get_command")
  @patch("ot_api.runs.enqueue_command")
  @patch("ot_api.run_id", "run-id", create=True)
  async def test_a_timed_out_move_stops_the_run_and_refuses_the_next_command(
    self, mock_enqueue, mock_get_command, mock_post
  ):
    """Giving up on the wait leaves the command in the robot's queue, where it still
    runs. A caller who retries would make the pipette aspirate twice from one well."""
    mock_enqueue.return_value = "cmd-1"
    released = threading.Event()
    mock_get_command.side_effect = lambda *a, **kw: released.wait()

    backend = self._backend(request_timeout=0.2, command_timeout=0.2)
    try:
      with self.assertRaises(TimeoutError):
        await backend.move_pipette_head(Coordinate(1.0, 2.0, 3.0), pipette_id="left")

      mock_post.assert_called_once_with("/runs/run-id/actions", {"data": {"actionType": "stop"}})

      with self.assertRaises(RuntimeError) as refused:
        await backend.move_pipette_head(Coordinate(1.0, 2.0, 3.0), pipette_id="left")
    finally:
      released.set()

    self.assertIn("setup()", str(refused.exception))
    self.assertEqual(mock_enqueue.call_count, 1)  # the second attempt reached no robot

  @patch("ot_api.requestor.post")
  @patch("ot_api.runs.get_command")
  @patch("ot_api.runs.enqueue_command")
  @patch("ot_api.run_id", "run-id", create=True)
  async def test_a_command_that_finished_during_the_last_sleep_is_not_a_timeout(
    self, mock_enqueue, mock_get_command, mock_post
  ):
    """The read after the final sleep is the one that sees a command the robot has
    just finished. Handing it the sliver of deadline that is left cannot answer, and
    then a move that worked halts the robot and latches the refusal."""
    mock_enqueue.return_value = "cmd-1"
    answers = ["running"]

    def status(*args, **kwargs):
      state = answers.pop(0) if answers else "succeeded"
      return {"data": {"status": state, "result": {}}}

    mock_get_command.side_effect = status

    # A poll interval wider than the budget puts the whole remainder into one sleep,
    # so the read that follows it lands exactly on the deadline.
    backend = self._backend(request_timeout=5.0, command_timeout=0.2, status_poll_interval=1.0)

    await backend.move_pipette_head(Coordinate(1.0, 2.0, 3.0), pipette_id="left")

    mock_post.assert_not_called()  # nothing halted a run that completed
    await backend.move_pipette_head(Coordinate(4.0, 5.0, 6.0), pipette_id="left")
    self.assertEqual(mock_enqueue.call_count, 2)  # and nothing latched the refusal

  @patch("ot_api.requestor.post")
  @patch("ot_api.runs.get_command")
  @patch("ot_api.runs.enqueue_command")
  @patch("ot_api.run_id", "run-id", create=True)
  async def test_a_status_read_that_never_answers_does_not_end_the_command(
    self, mock_enqueue, mock_get_command, mock_post
  ):
    """A lost GET says nothing about the motion. Ending the command on one takes the
    abort decision away from whoever asked for the longer budget: with a ten-minute
    mix and a seven-second request budget, the driver would fire at eight seconds."""
    mock_enqueue.return_value = "cmd-1"
    released = threading.Event()
    reads = []

    def status(*args, **kwargs):
      reads.append(1)
      if len(reads) == 1:
        released.wait()  # this one read never comes back
      return {"data": {"status": "succeeded", "result": {}}}

    mock_get_command.side_effect = status

    backend = self._backend(request_timeout=0.2, command_timeout=5.0, status_poll_interval=0.01)
    try:
      await backend.move_pipette_head(Coordinate(1.0, 2.0, 3.0), pipette_id="left")
    finally:
      released.set()

    self.assertEqual(len(reads), 2)  # it polled again rather than giving up
    mock_post.assert_not_called()  # and nothing halted a run that was still running
    await backend.move_pipette_head(Coordinate(4.0, 5.0, 6.0), pipette_id="left")

  @patch("ot_api.requestor.post")
  @patch("ot_api.runs.get_command")
  @patch("ot_api.runs.enqueue_command")
  @patch("ot_api.run_id", "run-id", create=True)
  async def test_a_slow_position_read_does_not_halt_the_robot(
    self, mock_enqueue, mock_get_command, mock_post
  ):
    """savePosition moves nothing, so giving up on it leaves no motion outstanding.
    Halting the run and refusing everything after would cost more than the timeout."""
    mock_enqueue.return_value = "cmd-1"
    released = threading.Event()
    mock_get_command.side_effect = lambda *a, **kw: released.wait()

    backend = self._backend(request_timeout=0.2)
    try:
      with self.assertRaises(RuntimeError):
        await backend.get_channel_position(0)

      mock_post.assert_not_called()

      # still usable: the refusal did not latch
      with self.assertRaises(RuntimeError) as second:
        await backend.get_channel_position(0)
    finally:
      released.set()

    self.assertNotIn("setup()", str(second.exception))
    self.assertEqual(mock_enqueue.call_count, 2)

  @patch("ot_api.health.home")
  @patch("ot_api.health.get")
  @patch("ot_api.lh.add_mounted_pipettes")
  @patch("ot_api.runs.create")
  @patch("ot_api.requestor.post")
  @patch("ot_api.runs.get_command")
  @patch("ot_api.runs.enqueue_command")
  @patch("ot_api.run_id", "run-id", create=True)
  async def test_a_setup_that_cannot_create_a_run_leaves_the_refusal_in_place(
    self,
    mock_enqueue,
    mock_get_command,
    mock_post,
    mock_create,
    mock_pipettes,
    mock_health,
    mock_home,
  ):
    """A fresh run is what the stale command cannot execute in, so until one exists
    there is nothing to lift the refusal. The robot-server answering 409 while it
    still holds the old run is the likeliest reason an operator is here at all."""
    mock_enqueue.return_value = "cmd-1"
    released = threading.Event()
    mock_get_command.side_effect = lambda *a, **kw: released.wait()

    backend = self._backend(request_timeout=0.2, command_timeout=0.2)
    try:
      with self.assertRaises(TimeoutError):
        await backend.move_pipette_head(Coordinate(1.0, 2.0, 3.0), pipette_id="left")

      mock_create.side_effect = RuntimeError("RunConflictError")
      with self.assertRaises(RuntimeError):
        await backend.setup()

      with self.assertRaises(RuntimeError) as refused:
        await backend.move_pipette_head(Coordinate(1.0, 2.0, 3.0), pipette_id="left")
    finally:
      released.set()

    self.assertIn("setup()", str(refused.exception))
    self.assertEqual(mock_enqueue.call_count, 1)  # nothing went into the stale run

  @patch("ot_api.health.home")
  @patch("ot_api.health.get")
  @patch("ot_api.lh.add_mounted_pipettes")
  @patch("ot_api.runs.create")
  @patch("ot_api.requestor.post")
  @patch("ot_api.runs.get_command")
  @patch("ot_api.runs.enqueue_command")
  @patch("ot_api.run_id", "run-id", create=True)
  async def test_setup_is_the_way_back_from_a_latched_refusal(
    self,
    mock_enqueue,
    mock_get_command,
    mock_post,
    mock_create,
    mock_pipettes,
    mock_health,
    mock_home,
  ):
    """The refusal names setup(); this is what makes that a real instruction."""
    mock_enqueue.return_value = "cmd-1"
    mock_create.return_value = "run-2"
    mock_pipettes.return_value = _PIPETTES
    mock_health.side_effect = _mock_health_get
    released = threading.Event()
    mock_get_command.side_effect = lambda *a, **kw: released.wait()

    backend = self._backend(request_timeout=0.2, command_timeout=0.2)
    try:
      with self.assertRaises(TimeoutError):
        await backend.move_pipette_head(Coordinate(1.0, 2.0, 3.0), pipette_id="left")
    finally:
      released.set()

    mock_get_command.side_effect = lambda *a, **kw: {"data": {"status": "succeeded", "result": {}}}
    await backend.setup()

    await backend.move_pipette_head(Coordinate(1.0, 2.0, 3.0), pipette_id="left")

  @patch("ot_api.modules.list_connected_modules")
  async def test_a_timeout_names_the_call_and_a_readable_budget(self, mock_list):
    """An operator debugging a lossy OT-2 reads this line; the proxy the backend logs
    through must not be what it names."""
    released = threading.Event()
    mock_list.side_effect = lambda *a, **kw: released.wait()

    backend = self._backend(request_timeout=0.2)
    try:
      with self.assertRaises(TimeoutError) as caught:
        await backend.list_connected_modules()
    finally:
      released.set()

    self.assertIn("modules.list_connected_modules", str(caught.exception))
    self.assertIn("0.2s", str(caught.exception))
