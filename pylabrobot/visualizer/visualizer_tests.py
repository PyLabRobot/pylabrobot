import json
import unittest
import unittest.mock
import urllib.request
from contextlib import asynccontextmanager
from typing import Optional, Tuple

import anyio
import pytest
import websockets

from pylabrobot.__version__ import STANDARD_FORM_JSON_VERSION
from pylabrobot.resources import (
  Coordinate,
  Resource,
  cor_96_wellplate_360uL_Fb,
)
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.visualizer import Visualizer
from pylabrobot.visualizer.visualizer import (
  _build_method_registry,
  _sanitize_floats,
  _serialize_resource_tree,
)

# Guard the server-based tests against hangs. The visualizer only supports the asyncio backend
# because `websockets` ships an asyncio-only server implementation.
pytestmark = pytest.mark.timeout(60)


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


class VisualizerLiquidColorValidationTests(unittest.TestCase):
  """Tests for liquid_color parameter validation."""

  def test_valid_hex(self):
    r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(r, open_browser=False, liquid_color="5DADE2")
    self.assertEqual(vis._liquid_color, "5DADE2")

  def test_valid_hex_with_hash(self):
    r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(r, open_browser=False, liquid_color="#aabbcc")
    self.assertEqual(vis._liquid_color, "AABBCC")

  def test_invalid_hex_raises(self):
    r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    with self.assertRaises(ValueError):
      Visualizer(r, open_browser=False, liquid_color="nope")

  def test_short_hex_raises(self):
    r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    with self.assertRaises(ValueError):
      Visualizer(r, open_browser=False, liquid_color="FFF")


class TestVisualizerSetupStop(AnyioTestBase):
  """Tests for the structured-concurrency lifecycle of the visualizer backend."""

  # The visualizer relies on `websockets`' asyncio-only server, so it cannot run on Trio.
  _anyio_backends = ["asyncio"]

  async def test_async_with_recycles_lifecycle(self):
    """The servers start and stop correctly under ``async with``, and can be recycled."""

    r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(r, open_browser=False)

    async def setup_stop_single():
      async with vis:
        self.assertIsNotNone(vis.loop)
        self.assertTrue(vis.setup_finished)
        # wait for the servers to start
        await anyio.sleep(1)
      self.assertFalse(vis.has_connection())
      self.assertFalse(vis.setup_finished)

    # setup and stop twice to ensure that everything is recycled correctly
    await setup_stop_single()
    await setup_stop_single()

  async def test_setup_stop(self):
    """The legacy ``setup()``/``stop()`` API drives the lifespan via the global manager."""
    r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(r, open_browser=False)

    with pytest.warns(DeprecationWarning):
      await vis.setup()
    self.assertTrue(vis.setup_finished)

    await vis.stop()
    self.assertFalse(vis.setup_finished)

  async def test_non_asyncio_backend_rejected(self):
    """The visualizer only supports asyncio and rejects other backends with a clear error."""
    r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(r, open_browser=False)
    # Simulate a non-asyncio backend. Match the guard's own message so the test fails (not passes on
    # a coincidental downstream RuntimeError) if the guard is ever removed.
    with unittest.mock.patch(
      "pylabrobot.visualizer.visualizer.sniffio.current_async_library",
      return_value="trio",
    ):
      with self.assertRaisesRegex(RuntimeError, "only supports the asyncio backend"):
        async with vis:
          pass


class TestVisualizerServer(AnyioTestBase):
  """Tests for servers (ws/fs)."""

  _anyio_backends = ["asyncio"]

  @asynccontextmanager
  async def lifespan(self, **kwargs):
    self.r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    self.vis = Visualizer(self.r, open_browser=False)
    async with self.vis:
      ws_port = self.vis.ws_port  # port may change if port is already in use
      self.uri = f"ws://localhost:{ws_port}"
      async with websockets.connect(self.uri) as client:
        self.client = client
        yield

  async def test_get_index_html(self):
    """Test that the index.html file is returned."""

    # The file server's accept loop runs on this event loop, so the blocking HTTP request must be
    # offloaded to a thread (otherwise it would block the loop and the request would never be
    # served).
    def fetch():
      r = urllib.request.urlopen(f"http://localhost:{self.vis.fs_port}/", timeout=10)
      return r.status, r.headers["Content-Type"]

    status, content_type = await anyio.to_thread.run_sync(fetch)
    self.assertEqual(status, 200)
    self.assertIn(content_type, ["text/html", "text/html; charset=utf-8"])

  async def test_liquid_color_default_substituted(self):
    """Test that the default liquid_color is substituted into the served HTML."""
    url = f"http://localhost:{self.vis.fs_port}/"
    html = await anyio.to_thread.run_sync(
      lambda: urllib.request.urlopen(url, timeout=10).read().decode()
    )
    self.assertNotIn("{{ liquid_color }}", html)
    self.assertIn('value="F39C12"', html)

  async def test_connect(self):
    await self.client.send('{"event": "ready"}')
    response = await self.client.recv()
    response = json.loads(response)
    self.assertEqual(
      response,
      {
        "event": "set_root_resource",
        "data": {
          "resource": _serialize_resource_tree(self.r),
          "method_registry": _build_method_registry(self.r),
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


class TestVisualizerShowMachineTools(AnyioTestBase):
  """Tests for the show_machine_tools_at_start parameter."""

  _anyio_backends = ["asyncio"]

  async def test_show_machine_tools_at_start_false(self):
    """When show_machine_tools_at_start=False, the show_machine_tools event should not be sent."""
    r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(r, open_browser=False, show_machine_tools_at_start=False)
    vis.send_command = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    async with vis:
      # Simulate browser ready
      await vis._send_resources_and_state()

      # Check that show_machine_tools was never sent
      for call in vis.send_command.call_args_list:  # type: ignore[attr-defined]
        self.assertNotEqual(
          call[1].get("event") if call[1] else call[0][0],
          "show_machine_tools",
          "show_machine_tools event should not be sent when show_machine_tools_at_start=False",
        )


class TestVisualizerCommand(AnyioTestBase):
  """Tests for command sending using the visualizer backend."""

  _anyio_backends = ["asyncio"]

  @asynccontextmanager
  async def lifespan(self, **kwargs):
    self.r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    self.vis = Visualizer(self.r, open_browser=False)

    # mock the send_command method to catch the events
    self.send_command_mock = unittest.mock.AsyncMock()
    self.vis.send_command = self.send_command_mock  # type: ignore[method-assign]

    async with self.vis:
      yield

  async def _wait_for_event(self, event: str, data_key: Optional[str] = None, timeout: float = 5.0):
    """Wait until the most recent send_command call is ``event`` (optionally carrying
    ``data_key`` in its data), yielding to the loop.

    Replaces fixed ``time.sleep()`` waits: those block the event loop and race under CI
    load. Checking the last call - not just any call - matters when one action fans out
    into many events (e.g. set_well_volumes emits a set_state per well); the assert reads
    the last call, so we wait until that call carries the expected payload.
    """
    with anyio.move_on_after(timeout):
      while True:
        last = self.send_command_mock.call_args
        if last is not None and last.kwargs.get("event") == event:
          if data_key is None or data_key in last.kwargs.get("data", {}):
            return
        await anyio.sleep(0.01)

  async def test_assign_child_resource(self):
    """Test that the assign_child_resource method sends the correct event."""
    child = Resource(size_x=100, size_y=100, size_z=100, name="child")
    self.r.assign_child_resource(child, location=Coordinate(0, 0, 0))
    await self._wait_for_event("resource_assigned")
    # Assert on the resource_assigned call specifically, rather than "exactly one call ever": the
    # latter is brittle if assignment ever also emits an initial state update.
    assigned = [
      c
      for c in self.send_command_mock.call_args_list
      if c.kwargs.get("event") == "resource_assigned"
    ]
    self.assertEqual(len(assigned), 1)
    self.assertEqual(
      assigned[0].kwargs,
      {
        "event": "resource_assigned",
        "data": {
          "resource": _serialize_resource_tree(child),
          "method_registry": _build_method_registry(child),
          "state": child.serialize_all_state(),
          "parent_name": "root",
        },
        "wait_for_response": False,
      },
    )

  async def test_outbox_survives_send_failure(self):
    """A failed send in the outbox worker is logged and does not stop later events or teardown."""

    # Fail the first resource_assigned send; a later one must still be delivered, proving the single
    # outbox worker caught the failure and kept going (a detached send-per-event would instead drop
    # the failure as an unhandled task exception).
    state = {"assign_sends": 0}

    async def flaky(*args, **kwargs):
      if kwargs.get("event") == "resource_assigned":
        state["assign_sends"] += 1
        if state["assign_sends"] == 1:
          raise RuntimeError("simulated send failure")

    self.send_command_mock.side_effect = flaky

    first = Resource(size_x=10, size_y=10, size_z=10, name="first")
    self.r.assign_child_resource(first, location=Coordinate(0, 0, 0))
    second = Resource(size_x=10, size_y=10, size_z=10, name="second")
    self.r.assign_child_resource(second, location=Coordinate(0, 0, 0))

    def second_delivered() -> bool:
      return any(
        c.kwargs.get("event") == "resource_assigned"
        and c.kwargs["data"]["resource"]["name"] == "second"
        for c in self.send_command_mock.call_args_list
      )

    with anyio.move_on_after(5.0):
      while not second_delivered():
        await anyio.sleep(0.01)

    # The worker caught the first failure and delivered the later event. If the worker's try/except
    # were removed, the raised error would propagate out of the lifespan task group and make the
    # enclosing `async with vis` teardown raise, failing this test either way.
    self.assertTrue(second_delivered())

  async def test_resource_unassigned(self):
    """Test that the unassign_child_resource method sends the correct event."""
    child = Resource(size_x=100, size_y=100, size_z=100, name="child")
    self.r.assign_child_resource(child, location=Coordinate(0, 0, 0))
    self.r.unassign_child_resource(child)
    await self._wait_for_event("resource_unassigned")

    self.send_command_mock.assert_called_with(
      event="resource_unassigned",
      data={"resource_name": "child"},
      wait_for_response=False,
    )

  async def test_state_updated(self):
    """Test that the state_updated method sends the correct event."""
    plate = cor_96_wellplate_360uL_Fb(name="plate_01")
    self.r.assign_child_resource(plate, location=Coordinate(0, 0, 0))
    plate.set_well_volumes([500] * 96)
    await self._wait_for_event("set_state", data_key="plate_01_well_H12")
    self.send_command_mock.assert_called()
    call_args = self.send_command_mock.call_args[1]
    self.assertEqual(call_args["event"], "set_state")
    self.assertEqual(
      call_args["data"]["plate_01_well_H12"]["volume"],
      500,
    )


class TestVisualizerOutboxBounded(unittest.TestCase):
  """The outbox channel is bounded, so a stalled browser drops events instead of growing memory."""

  def test_enqueue_outbound_drops_newest_when_full(self):
    """When the buffer is full (worker not draining), the newest event is dropped with a warning."""
    r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(r, open_browser=False)

    # A size-1 buffer whose receiver never drains: the first event is buffered, the next overflows.
    # (`_receive` is kept referenced so the stream is not treated as closed, exercising the
    # buffer-full path rather than the closed-stream path.)
    send, _receive = anyio.create_memory_object_stream[Tuple[str, dict]](1)
    vis._outbox_send = send

    vis._enqueue_outbound("set_state", {"a": 1})  # fits in the buffer, no warning

    with unittest.mock.patch("pylabrobot.visualizer.visualizer.logger") as mock_logger:
      vis._enqueue_outbound("resource_assigned", {"b": 2})  # overflow -> dropped + warning

    mock_logger.warning.assert_called_once()
