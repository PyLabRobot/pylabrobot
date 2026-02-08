import json
import time
import unittest
import unittest.mock

import pytest
import requests
import websockets

from pylabrobot.__version__ import STANDARD_FORM_JSON_VERSION
from pylabrobot.resources import (
  Coordinate,
  Cor_96_wellplate_360ul_Fb,
  Resource,
)
from pylabrobot.visualizer import Visualizer
from pylabrobot.visualizer.visualizer import _sanitize_floats, _serialize_with_methods


class SanitizeFloatsTests(unittest.TestCase):
  def test_inf_replaced(self):
    result = _sanitize_floats({"v": float("inf")})
    self.assertEqual(result, {"v": "Infinity"})
    self.assertEqual(json.dumps(result), '{"v": "Infinity"}')

  def test_neg_inf_replaced(self):
    result = _sanitize_floats({"v": float("-inf")})
    self.assertEqual(result, {"v": "-Infinity"})

  def test_nan_replaced(self):
    result = _sanitize_floats({"v": float("nan")})
    self.assertEqual(result, {"v": "NaN"})

  def test_finite_float_unchanged(self):
    self.assertEqual(_sanitize_floats({"v": 3.14}), {"v": 3.14})

  def test_non_floats_unchanged(self):
    data = {"s": "hello", "i": 42, "b": True, "n": None}
    self.assertEqual(_sanitize_floats(data), data)

  def test_nested_dict(self):
    data = {"a": {"b": {"c": float("inf")}}}
    self.assertEqual(_sanitize_floats(data), {"a": {"b": {"c": "Infinity"}}})

  def test_values_in_list(self):
    data = {"vals": [1.0, float("inf"), float("-inf"), float("nan")]}
    result = _sanitize_floats(data)
    self.assertEqual(result, {"vals": [1.0, "Infinity", "-Infinity", "NaN"]})

  def test_string_containing_infinity_not_touched(self):
    data = {"msg": "status: Infinity reached"}
    self.assertEqual(_sanitize_floats(data), data)

  def test_result_is_valid_json(self):
    data = {"a": float("inf"), "b": [float("-inf")], "c": {"d": float("nan")}}
    serialized = json.dumps(_sanitize_floats(data))
    roundtripped = json.loads(serialized)
    self.assertEqual(roundtripped["a"], "Infinity")
    self.assertEqual(roundtripped["b"], ["-Infinity"])
    self.assertEqual(roundtripped["c"]["d"], "NaN")


class VisualizerSetupStopTests(unittest.IsolatedAsyncioTestCase):
  """Tests for the setup and stop methods of the visualizer backend."""

  @pytest.mark.timeout(20)
  async def test_setup_stop(self):
    """Test that the thread is started and stopped correctly."""

    r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(r, open_browser=False)

    async def setup_stop_single():
      await vis.setup()
      self.assertIsNotNone(vis.loop)
      # wait for the server to start
      time.sleep(1)
      await vis.stop()
      self.assertFalse(vis.has_connection())

    # setup and stop twice to ensure that everything is recycled correctly
    await setup_stop_single()
    await setup_stop_single()


class VisualizerServerTests(unittest.IsolatedAsyncioTestCase):
  """Tests for servers (ws/fs)."""

  async def asyncSetUp(self):
    await super().asyncSetUp()
    self.r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    self.vis = Visualizer(self.r, open_browser=False)
    await self.vis.setup()

    ws_port = self.vis.ws_port  # port may change if port is already in use
    self.uri = f"ws://localhost:{ws_port}"
    self.client = await websockets.connect(self.uri)

  async def asyncTearDown(self):
    await super().asyncTearDown()
    await self.vis.stop()
    await self.client.close()

  def test_get_index_html(self):
    """Test that the index.html file is returned."""
    r = requests.get("http://localhost:1337/", timeout=10)
    self.assertEqual(r.status_code, 200)
    self.assertIn(
      r.headers["Content-Type"],
      ["text/html", "text/html; charset=utf-8"],
    )

  async def test_connect(self):
    await self.client.send('{"event": "ready"}')
    response = await self.client.recv()
    response = json.loads(response)
    self.assertEqual(
      response,
      {
        "event": "set_root_resource",
        "data": {
          "resource": _serialize_with_methods(self.r),
        },
        "id": "0001",
        "version": STANDARD_FORM_JSON_VERSION,
      },
    )

  async def test_event_sent(self):
    await self.client.send('{"event": "ready"}')
    _ = await self.client.recv()  # set_root_resource
    _ = await self.client.recv()  # set_state
    _ = await self.client.recv()  # show_machine_tools

    await self.vis.send_command("test", wait_for_response=False)
    recv = await self.client.recv()
    data = json.loads(recv)
    self.assertEqual(data["event"], "test")


class VisualizerShowMachineToolsTests(unittest.IsolatedAsyncioTestCase):
  """Tests for the show_machine_tools_at_start parameter."""

  async def test_show_machine_tools_at_start_false(self):
    """When show_machine_tools_at_start=False, the show_machine_tools event should not be sent."""
    r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(r, open_browser=False, show_machine_tools_at_start=False)
    vis.send_command = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    await vis.setup()

    # Simulate browser ready
    await vis._send_resources_and_state()

    # Check that show_machine_tools was never sent
    for call in vis.send_command.call_args_list:  # type: ignore[attr-defined]
      self.assertNotEqual(
        call[1].get("event") if call[1] else call[0][0],
        "show_machine_tools",
        "show_machine_tools event should not be sent when show_machine_tools_at_start=False",
      )

    await vis.stop()


class VisualizerCommandTests(unittest.IsolatedAsyncioTestCase):
  """Tests for command sending using the visualizer backend."""

  async def asyncSetUp(self):
    await super().asyncSetUp()
    self.maxDiff = None
    self.r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    self.vis = Visualizer(self.r, open_browser=False)

    # mock the send_command method to catch the events
    self.vis.send_command = unittest.mock.AsyncMock()  # type: ignore[method-assign]

    await self.vis.setup()

  async def test_assign_child_resource(self):
    """Test that the assign_child_resource method sends the correct event."""
    child = Resource(size_x=100, size_y=100, size_z=100, name="child")
    self.r.assign_child_resource(child, location=Coordinate(0, 0, 0))
    time.sleep(0.1)  # wait for the event to be sent
    self.vis.send_command.assert_called_once_with(  # type: ignore[attr-defined]
      event="resource_assigned",
      data={
        "resource": _serialize_with_methods(child),
        "state": child.serialize_all_state(),
        "parent_name": "root",
      },
      wait_for_response=False,
    )

  async def test_resource_unassigned(self):
    """Test that the unassign_child_resource method sends the correct event."""
    child = Resource(size_x=100, size_y=100, size_z=100, name="child")
    self.r.assign_child_resource(child, location=Coordinate(0, 0, 0))
    self.r.unassign_child_resource(child)
    time.sleep(0.1)

    self.vis.send_command.assert_called_with(  # type: ignore[attr-defined]
      event="resource_unassigned",
      data={"resource_name": "child"},
      wait_for_response=False,
    )

  async def test_state_updated(self):
    """Test that the state_updated method sends the correct event."""
    plate = Cor_96_wellplate_360ul_Fb(name="plate_01")
    self.r.assign_child_resource(plate, location=Coordinate(0, 0, 0))
    plate.set_well_volumes([500] * 96)
    time.sleep(0.1)
    self.vis.send_command.assert_called()  # type: ignore[attr-defined]
    call_args = self.vis.send_command.call_args[1]  # type: ignore[attr-defined]
    self.assertEqual(call_args["event"], "set_state")
    self.assertEqual(
      call_args["data"]["plate_01_well_H12"]["volume"],
      500,
    )
