"""The motion channel, checked without a browser.

What a command is read as (`motion.py`), and how the server holds a command until the pages have
played it, against a simulated STAR and pages that are only websocket clients. The player itself is
checked in Node (`motion_player_tests.mjs`), run from here where Node is installed.
"""

import asyncio
import inspect
import json
import pathlib
import shutil
import subprocess
import time
import unittest
from unittest import mock
from typing import Any, Dict, List, Optional

import websockets

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.tip_rack import TipRack
from pylabrobot.resources.tip_tracking import does_tip_tracking, set_tip_tracking
from pylabrobot.visualizer3D.demo import build_facility, fill, star_of
from pylabrobot.hamilton.star import motion
from pylabrobot.hamilton.star.motion import (
  HEAD96_ASPIRATE_FIXED,
  HEAD96_DISPENSE_FIXED,
  HEAD96_TIP_DROP_FIXED,
  HEAD96_TIP_PICKUP_FIXED,
  SIMPLE_MOVE_FIXED,
  attach_viewer_motion,
  star_motion,
)
from pylabrobot.visualizer3D.server import Viewer3D
from pylabrobot.visualizer3D.server_tests import free_ports, track_volumes

HERE = pathlib.Path(__file__).parent
NODE = shutil.which("node")


async def simulated_star(test: unittest.TestCase) -> Any:
  """The demo facility's STAR, set up, tracking volumes and tips."""
  track_volumes(test)
  was_tracking = does_tip_tracking()
  set_tip_tracking(True)
  test.addCleanup(set_tip_tracking, was_tracking)
  facility = build_facility()
  star = star_of(facility)
  await star.setup()
  return facility, star


class DecoderTests(unittest.IsolatedAsyncioTestCase):
  """A command is read for where it takes the drives, which is where the model then has them."""

  async def asyncSetUp(self) -> None:
    self.facility, self.star = await simulated_star(self)
    self.requests: List[Dict[str, Any]] = []

    async def listen(module: str, command: str, params: Dict[str, Any]) -> None:
      request = star_motion(self.star.driver, module, command, params)
      if request is not None:
        self.requests.append(request)

    self.star.driver.motion_listener = listen
    self.rack = self.star.deck.get_resource("tips_0")
    self.source = self.star.deck.get_resource("source_0")
    assert isinstance(self.rack, TipRack) and isinstance(self.source, Plate)
    fill(self.source, 300.0)

  def assert_ends_where_the_model_is(self, request: Dict[str, Any]) -> None:
    arm = self.star.x_arm.resource
    if request["arm"] is not None:
      self.assertAlmostEqual(request["arm"]["x"], arm.location.x, delta=0.1, msg="arm")
    channels = {c.name: c for c in self.star.pipettes.resources}
    for target in request["channels"]:
      at = channels[target["name"]].location
      if target.get("y") is not None:
        self.assertAlmostEqual(target["y"], at.y, delta=0.1, msg=f"{target['name']} y")
      if target.get("end") is not None:
        self.assertAlmostEqual(target["end"], at.z, delta=0.1, msg=f"{target['name']} z")

  async def test_each_command_ends_where_the_model_records_it_ending(self):
    spots = [self.rack.get_item(f"{row}1") for row in "ABCDEFGH"]
    wells = [self.source.get_item(f"{row}1") for row in "ABCDEFGH"]
    for operation, kind in (
      (lambda: self.star.pipettes.pick_up_tips(spots), "tip_pickup"),
      (lambda: self.star.pipettes.aspirate(wells, [50.0] * 8), "aspirate"),
      (lambda: self.star.pipettes.drop_tips(spots), "tip_drop"),
    ):
      self.requests.clear()
      await operation()
      stroke = [r for r in self.requests if r["kind"] == kind]
      self.assertEqual(len(stroke), 1, f"{kind}: {[r['kind'] for r in self.requests]}")
      self.assert_ends_where_the_model_is(stroke[0])

  async def test_heights_with_a_tip_come_from_the_model_not_the_simulator(self):
    """On a real STAR there is no simulator to ask how far a tip hangs below the stop disc.
    Read from it, every height with a tip on was off by the tip's length."""
    pipettes = self.star.pipettes
    simulators_own = type(pipettes)._below_stop_disc

    def not_from_the_decoder(this: Any, channel: int) -> float:
      # The simulator keeps its own model with it; only the decoder must not lean on it.
      if inspect.stack()[1].filename == motion.__file__:
        raise AssertionError("the decoder asked the simulator how long the tip is")
      return simulators_own(this, channel)

    with mock.patch.object(type(pipettes), "_below_stop_disc", not_from_the_decoder):
      with mock.patch.object(self.star.driver, "defined_tip_lengths", {}, create=True):
        await self.test_each_command_ends_where_the_model_records_it_ending()

  async def test_the_tips_change_hands_where_the_model_then_has_them(self):
    """Placed anywhere else, the tip jumps when the model's own move reaches the page."""
    spots = [self.rack.get_item(f"{row}2") for row in "AB"]
    tips = [spot.tip for spot in spots]
    await self.star.pipettes.pick_up_tips(spots)
    pick_up = next(r for r in self.requests if r["kind"] == "tip_pickup")
    shafts = [c.children[0].name for c in self.star.pipettes.resources[:2]]
    self.assertEqual(
      [(a["name"], a["parent"]) for a in pick_up["attach"]],
      [(t.name, s) for t, s in zip(tips, shafts)],
    )
    for handover, tip in zip(pick_up["attach"], tips):
      self.assertEqual(tip.parent.name, handover["parent"])
      for axis in "xyz":
        self.assertAlmostEqual(handover["location"][axis], getattr(tip.location, axis), 6)

    await self.star.pipettes.drop_tips(spots)
    drop = next(r for r in self.requests if r["kind"] == "tip_drop")
    self.assertEqual(
      [(a["name"], a["parent"]) for a in drop["attach"]],
      [(t.name, s.name) for t, s in zip(tips, spots)],
    )
    for handover, tip in zip(drop["attach"], tips):
      for axis in "xyz":
        self.assertAlmostEqual(handover["location"][axis], getattr(tip.location, axis), 6)

  async def test_a_dropped_tip_is_let_go_of_down_in_its_spot(self):
    """`DROP` heights are the stop disc's: taken as the tip's bottom, the channel stops a tip's
    length short and lets go of it in the air above the rack."""
    spot = self.rack.get_item("A4")
    tip = spot.tip
    await self.star.pipettes.pick_up_tips([spot])
    channel, shaft = self.star.pipettes.resources[0], self.star.pipettes.resources[0].children[0]
    carried = tip.get_absolute_location() - shaft.get_absolute_location()
    await self.star.pipettes.drop_tips([spot])
    stroke = [r for r in self.requests if r["kind"] == "tip_drop"][-1]["channels"][0]
    # Where the tip is at the bottom of the stroke, and where the model then rests it.
    bottom = shaft.get_absolute_location().z - (stroke["end"] - stroke["down"])
    self.assertAlmostEqual(bottom + carried.z, tip.get_absolute_location().z, delta=0.5)
    self.assertEqual(channel.location.z, stroke["end"])

  async def test_an_aspiration_dwells_as_long_as_its_volume_takes(self):
    await self.star.pipettes.pick_up_tips([self.rack.get_item("A3")])
    await self.star.pipettes.aspirate([self.source.get_item("A3")], [100.0])
    aspirate = next(r for r in self.requests if r["kind"] == "aspirate")
    self.assertGreater(aspirate["dwell"], 0.0)
    # The drives move as a STAR's own timings say, where PLR's defaults are slower.
    self.assertEqual(aspirate["drives"]["z"]["speed"], motion.CHANNEL_Z_SPEED)
    self.assertEqual(aspirate["drives"]["y"]["speed"], motion.CHANNEL_Y_SPEED)
    self.assertEqual(aspirate["drives"]["y"]["stagger"], motion.CHANNEL_Y_STAGGER)

  async def test_every_command_takes_its_fixed_time_as_well(self):
    spots = [self.rack.get_item(f"{row}5") for row in "ABCD"]
    await self.star.pipettes.pick_up_tips(spots)
    await self.star.pipettes.aspirate(
      [self.source.get_item(f"{row}5") for row in "ABCD"], [50.0] * 4
    )
    await self.star.pipettes.drop_tips(spots)
    by_kind = {r["kind"]: r for r in self.requests}
    pickup = by_kind["tip_pickup"]
    self.assertAlmostEqual(pickup["fixed"] + pickup["dwell"], motion.TIP_PICKUP_FIXED, places=3)
    self.assertGreater(pickup["dwell"], 1.0)
    self.assertGreaterEqual(by_kind["aspirate"]["fixed"] + 0.001, motion.ASPIRATE_FIXED)
    # A drop spends its fixed time at the bottom, holding while the tips are pushed off.
    drop = by_kind["tip_drop"]
    self.assertAlmostEqual(drop["fixed"] + drop["dwell"], motion.TIP_DROP_FIXED, places=3)
    self.assertGreater(drop["dwell"], 2.5)
    for request in self.requests:
      self.assertGreater(request["fixed"], 0.0, request["kind"])

  async def test_a_tip_pick_up_presses_the_last_stretch_slowly(self):
    await self.star.pipettes.pick_up_tips([self.rack.get_item("A6")])
    channel = next(r for r in self.requests if r["kind"] == "tip_pickup")["channels"][0]
    self.assertLess(channel["press"], channel["down"])
    self.assertEqual(channel["press_speed"], motion.TIP_PRESS_SPEED)

  async def test_an_aspiration_follows_the_surface_and_pulls_out(self):
    """Immersion, its direction, surface following and the pull-out, read off the command."""
    sent: List[Dict[str, Any]] = []
    listen = self.star.driver.motion_listener

    async def keep(module: str, command: str, params: Dict[str, Any]) -> None:
      if module + command == "C0AS":
        sent.append(dict(params))
      await listen(module, command, params)

    self.star.driver.motion_listener = keep
    await self.star.pipettes.pick_up_tips([self.rack.get_item("A7")])
    await self.star.pipettes.aspirate([self.source.get_item("A7")], [100.0])
    params = sent[-1]

    # PLR sends the lowest allowed height as the surface itself; room below it, so immersion shows.
    floor = [f"{int(z) - 200:04}" for z in params["zl"]]

    def decoded(**changed: Any) -> Dict[str, Any]:
      request = star_motion(self.star.driver, "C0", "AS", {**params, "zx": floor, **changed})
      assert request is not None
      return request["channels"][0]

    plain = decoded(ip=["0020"], it=["0"], fp=["0000"], po=["0000"])
    deeper = decoded(ip=["0050"], it=["0"], fp=["0000"], po=["0000"])
    above = decoded(ip=["0050"], it=["1"], fp=["0000"], po=["0000"])
    self.assertAlmostEqual(plain["down"] - deeper["down"], 3.0, places=2)
    self.assertAlmostEqual(above["down"] - deeper["down"], 10.0, places=2)
    self.assertNotIn("follow", plain)
    self.assertNotIn("pull_out", plain)

    following = decoded(ip=["0020"], it=["0"], fp=["0040"], po=["0100"])
    self.assertAlmostEqual(following["down"] - following["follow"], 4.0, places=2)
    self.assertGreater(following["follow_speed"], 0.0)
    self.assertAlmostEqual(following["pull_out"] - following["leave"], 10.0, places=2)

  async def test_a_read_moves_nothing(self):
    self.assertIsNone(star_motion(self.star.driver, "C0", "RY", {}))
    self.assertIsNone(star_motion(self.star.driver, "C0", "XX", {}))


class Head96DecoderTests(unittest.IsolatedAsyncioTestCase):
  """A 96-head tip command is read for where it takes the arm and the head, and which tips move."""

  async def test_a_rack_is_picked_up_and_put_back_where_the_model_then_has_it(self):
    facility, star = await simulated_star(self)
    head = star.driver.arms[0].head96
    requests: List[Dict[str, Any]] = []

    async def listen(module: str, command: str, params: Dict[str, Any]) -> None:
      request = star_motion(star.driver, module, command, params)
      if request is not None:
        requests.append(request)

    star.driver.motion_listener = listen
    rack = star.deck.get_resource("tips_0")
    tips = [spot.tip for spot in rack.get_all_items()]
    for operation in (head.pick_up_tips, head.drop_tips):
      requests.clear()
      await operation(rack)
      stroke = [r for r in requests if r["kind"].startswith("head96_tip")][-1]
      self.assertAlmostEqual(stroke["arm"]["x"], star.x_arm.resource.location.x, delta=0.05)
      target = stroke["channels"][0]
      self.assertAlmostEqual(target["y"], head.resource.location.y, delta=0.05)
      self.assertAlmostEqual(target["end"], head.resource.location.z, delta=0.05)
      self.assertEqual(len(stroke["attach"]), 96)
      for handover, tip in zip(stroke["attach"], tips):
        self.assertEqual(handover["name"], tip.name)
        self.assertEqual(handover["parent"], tip.parent.name)
        for axis in "xyz":
          self.assertAlmostEqual(handover["location"][axis], getattr(tip.location, axis), 6)

  async def test_the_tips_are_clamped_at_the_bottom_not_waited_for_before_the_head_moves(self):
    facility, star = await simulated_star(self)
    head = star.driver.arms[0].head96
    requests: List[Dict[str, Any]] = []

    async def listen(module: str, command: str, params: Dict[str, Any]) -> None:
      request = star_motion(star.driver, module, command, params)
      if request is not None:
        requests.append(request)

    star.driver.motion_listener = listen
    rack = star.deck.get_resource("tips_0")
    for operation, fixed in (
      (head.pick_up_tips, HEAD96_TIP_PICKUP_FIXED),
      (head.drop_tips, HEAD96_TIP_DROP_FIXED),
    ):
      requests.clear()
      await operation(rack)
      stroke = [r for r in requests if r["kind"].startswith("head96_tip")][-1]
      self.assertEqual(stroke["fixed"], SIMPLE_MOVE_FIXED)
      self.assertAlmostEqual(stroke["dwell"] + stroke["fixed"], fixed, 3)

  async def test_an_aspirate_and_a_dispense_take_their_pumping_and_measured_time_at_the_bottom(
    self,
  ):
    facility, star = await simulated_star(self)
    head = star.driver.arms[0].head96
    requests: List[Dict[str, Any]] = []

    async def listen(module: str, command: str, params: Dict[str, Any]) -> None:
      request = star_motion(star.driver, module, command, params)
      if request is not None:
        requests.append((command, params, request))

    await head.pick_up_tips(star.deck.get_resource("tips_0"))
    plate = star.deck.get_resource("source_1")
    for well in plate.get_all_items():
      well.set_volume(100.0)
    star.driver.motion_listener = listen
    await head.aspirate(plate, 50.0)
    await head.dispense(plate, 50.0)
    self.assertEqual(sorted(c for c, _, _ in requests if c in ("EA", "ED")), ["EA", "ED"])
    for command, params, request in requests:
      if command not in ("EA", "ED"):
        continue
      volume, flow = (
        (params["af"], params["ag"]) if command == "EA" else (params["df"], params["dg"])
      )
      pumping = int(volume) / int(flow)
      measured = (
        HEAD96_ASPIRATE_FIXED if command == "EA" else HEAD96_DISPENSE_FIXED[int(params["da"])]
      )
      settling = int(params["wh"]) / 10
      self.assertEqual(request["fixed"], SIMPLE_MOVE_FIXED)
      self.assertAlmostEqual(request["dwell"] + request["fixed"], pumping + settling + measured, 2)


class FakePage:
  """A page that is only a websocket: it plays each motion by waiting `delay` seconds."""

  def __init__(self, viewer: Viewer3D, delay: Optional[float]):
    self.viewer = viewer
    self.delay = delay  # None: never says it is done
    self.events: List[str] = []
    self._socket: Any = None
    self._task: Optional["asyncio.Task[None]"] = None

  async def open(self) -> "FakePage":
    self._socket = await websockets.connect(self.viewer.ws_url, max_size=None)
    await self._socket.send(json.dumps({"event": "hello", "data": {"backend": "none"}}))
    self._task = asyncio.ensure_future(self._listen())
    while "scene" not in self.events:
      await asyncio.sleep(0.01)
    return self

  async def _listen(self) -> None:
    try:
      async for message in self._socket:
        parsed = json.loads(message)
        kind, data = parsed["event"], parsed["data"]
        self.events.append(kind)
        if kind == "state" and data.get("locations"):
          self.events.append("locations")
          self.events.extend(f"at:{name}" for name in data["locations"])
        if kind == "motion" and self.delay is not None:
          asyncio.ensure_future(self._play(data["id"]))
    except websockets.ConnectionClosed:
      pass

  async def _play(self, motion_id: int) -> None:
    await asyncio.sleep(self.delay or 0.0)
    await self._socket.send(json.dumps({"event": "motion_done", "data": {"id": motion_id}}))

  async def close(self) -> None:
    await self._socket.close()
    if self._task is not None:
      await self._task


class ServerTests(unittest.IsolatedAsyncioTestCase):
  """A command waits until every page has played it, and for nothing when there is nobody."""

  async def asyncSetUp(self) -> None:
    self.facility, self.star = await simulated_star(self)
    fs_port, ws_port = free_ports(2)
    self.viewer = Viewer3D(self.facility, open_browser=False, fs_port=fs_port, ws_port=ws_port)
    await self.viewer.start()
    attach_viewer_motion(self.star.driver, self.viewer)
    self.addAsyncCleanup(self.viewer.stop)
    self.rack = self.star.deck.get_resource("tips_0")
    assert isinstance(self.rack, TipRack)

  async def page(self, delay: Optional[float]) -> FakePage:
    page = await FakePage(self.viewer, delay).open()
    self.addAsyncCleanup(page.close)
    return page

  async def timed_pick_up(self, well: str = "A1") -> float:
    began = time.monotonic()
    await self.star.pipettes.pick_up_tips([self.rack.get_item(well)])
    return time.monotonic() - began

  async def test_a_command_waits_for_the_page(self):
    page = await self.page(0.5)
    self.assertGreaterEqual(await self.timed_pick_up(), 0.5)
    self.assertIn("motion", page.events)

  async def test_the_slowest_page_sets_the_pace(self):
    await self.page(0.1)
    await self.page(0.6)
    self.assertGreaterEqual(await self.timed_pick_up(), 0.6)

  async def test_with_no_page_nothing_waits(self):
    self.assertLess(await self.timed_pick_up(), 0.5)

  async def test_a_page_that_leaves_lets_the_command_go(self):
    page = await self.page(None)  # never answers
    asyncio.get_running_loop().call_later(0.3, lambda: asyncio.ensure_future(page.close()))
    self.assertLess(await self.timed_pick_up(), 5.0)

  async def test_a_page_that_leaves_while_a_motion_is_sent_lets_it_go_once(self):
    """A reload closes the page while the motion is still being sent to it: letting the page go
    releases the motion, and the send finding nobody left must not release it again."""
    await self.page(None)  # never answers
    send = self.viewer._broadcast

    async def send_then_leave(event: str, data: Any) -> None:
      await send(event, data)
      if event == "motion":
        for websocket in list(self.viewer._clients):
          self.viewer._clients.discard(websocket)
          self.viewer._release_motions(websocket)

    self.viewer._broadcast = send_then_leave  # type: ignore[method-assign]
    self.assertLess(await self.timed_pick_up(), 5.0)

  async def test_a_motion_starts_from_where_the_last_command_left_things(self):
    """The model moves when a command ends; the next motion is played from there, so what that
    move changed has to reach the page first."""
    page = await self.page(0.05)
    await self.timed_pick_up("A1")
    await self.star.pipettes.drop_tips([self.rack.get_item("A1")])
    motions = [i for i, kind in enumerate(page.events) if kind == "motion"]
    self.assertGreaterEqual(len(motions), 2)
    between = page.events[motions[0] + 1 : motions[-1]]
    # The pick-up took a tip onto a shaft, a change of shape, which carries the positions with it.
    self.assertIn("moves", between, f"the pick-up reached the page too late: {page.events}")


class ISWAPDecoderTests(unittest.IsolatedAsyncioTestCase):
  """An iSWAP command is read for the drives it moves, which is where the model then has them."""

  async def asyncSetUp(self) -> None:
    self.facility, self.star = await simulated_star(self)
    self.iswap = self.star.iswap
    self.requests: List[Dict[str, Any]] = []

    async def listen(module: str, command: str, params: Dict[str, Any]) -> None:
      request = star_motion(self.star.driver, module, command, params)
      if request is not None:
        self.requests.append(request)

    self.star.driver.motion_listener = listen
    await self.iswap.make_space()
    self.parked = await self.iswap.elbow_request_y_position()

  def last(self, kind: str) -> Dict[str, Any]:
    return [r for r in self.requests if r["kind"] == kind][-1]

  async def test_the_head_moves_where_the_model_puts_it(self):
    await self.iswap.elbow_move_to_y_position(self.parked - 150.0)
    await self.iswap.elbow_move_to_z_position(250.0)
    head = self.iswap.resource
    moves = [r["moves"][0] for r in self.requests if r["kind"] == "iswap_move"]
    along_y, along_z = moves[-2], moves[-1]
    self.assertEqual((along_y["axis"], along_z["axis"]), (1, 2))
    self.assertAlmostEqual(along_y["to"], head.location.y, delta=0.05)
    self.assertAlmostEqual(along_z["to"], head.location.z, delta=0.05)
    self.assertGreater(along_y["speed"], 0)

  async def test_a_joint_turns_to_the_rotation_the_model_gives_it(self):
    await self.iswap.elbow_move_to_y_position(self.parked - 200.0)
    await self.iswap.rotate_to_angles(elbow_absolute_angle="front", gripper_absolute_angle="left")
    turns = {t["name"]: t for t in self.last("iswap_turn")["turns"]}
    for resource in (self.iswap.link_1, self.iswap.gripper):
      turn = turns[resource.name]
      self.assertAlmostEqual((turn["drive"] - turn["base"]) % 360, resource.rotation.z % 360, 3)
      self.assertEqual(turn["pivot"]["x"], resource.proximal_joint.x)
      self.assertGreater(turn["speed"], 0)

  async def test_the_fingers_stand_where_the_model_stands_them(self):
    await self.iswap.gripper_move_to_jaw_position(90.0)
    jaws = self.last("iswap_jaws")["jaws"]
    for finger, target in zip(self.iswap.gripper.fingers, jaws["fingers"]):
      self.assertEqual(target["name"], finger.name)
      self.assertAlmostEqual(target["y"], finger.location.y, delta=0.01)
    self.assertEqual(jaws["gripper"], self.iswap.gripper.name)


class Head96OffsetAndLiquidDecoderTests(unittest.IsolatedAsyncioTestCase):
  """The 96-head's commands, read for what they move and which tips change hands."""

  async def asyncSetUp(self) -> None:
    self.facility, self.star = await simulated_star(self)
    self.head = self.star.head96
    self.rack = self.star.deck.get_resource("tips_1")
    self.requests: List[Dict[str, Any]] = []

    async def listen(module: str, command: str, params: Dict[str, Any]) -> None:
      request = star_motion(self.star.driver, module, command, params)
      if request is not None:
        self.requests.append(request)

    self.star.driver.motion_listener = listen

  async def test_an_aspiration_goes_down_to_the_liquid_surface(self):
    # The 96-head has no liquid class for the demo rack's filtered 1000 uL tips, and the
    # aspirate refuses without one; the decoder only needs the corrected volume, so the class
    # the 300 uL head uses stands in.
    from pylabrobot.hamilton.star.liquid_classes.mapping import star_mapping
    from pylabrobot.resources.liquid import Liquid

    liquid_class = star_mapping[(300, True, True, False, Liquid.WATER, False, False)]
    await self.head.pick_up_tips(self.rack)
    plate = self.star.deck.get_resource("source_1")
    fill(plate, 200.0)
    await self.head.aspirate(plate, 50.0, liquid_height=2.0, hamilton_liquid_class=liquid_class)
    request = [r for r in self.requests if r["kind"] == "head96_aspirate"][-1]
    self.assertEqual(request["command"], "C0EA")
    channel = request["channels"][0]
    self.assertLess(channel["down"], channel["end"])


class ISWAPServerTests(unittest.IsolatedAsyncioTestCase):
  async def test_a_move_the_model_records_first_is_played_before_it_is_told(self):
    """The iSWAP writes a move's target before sending it. Told first, the page would put the head
    at the end of the move and have nothing left to play."""
    facility, star = await simulated_star(self)
    fs_port, ws_port = free_ports(2)
    viewer = Viewer3D(facility, open_browser=False, fs_port=fs_port, ws_port=ws_port)
    await viewer.start()
    self.addAsyncCleanup(viewer.stop)
    attach_viewer_motion(star.driver, viewer)
    await star.iswap.make_space()
    y = await star.iswap.elbow_request_y_position()
    page = await FakePage(viewer, 0.05).open()
    self.addAsyncCleanup(page.close)
    head = f"at:{star.iswap.resource.name}"
    before = len(page.events)
    await star.iswap.elbow_move_to_y_position(y - 100.0)
    for _ in range(100):
      if head in page.events[before:]:
        break
      await asyncio.sleep(0.02)
    after = page.events[before:]
    self.assertIn("motion", after)
    self.assertIn(head, after)
    self.assertLess(after.index("motion"), after.index(head), after)


@unittest.skipUnless(NODE, "no Node to run the player's tests")
class PlayerTests(unittest.TestCase):
  def test_the_player(self):
    result = subprocess.run(
      [str(NODE), "--test", str(HERE.parent.parent / "visualizer3D" / "motion_player_tests.mjs")],
      capture_output=True,
      text=True,
      timeout=120,
    )
    self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-2000:])


if __name__ == "__main__":
  unittest.main()
