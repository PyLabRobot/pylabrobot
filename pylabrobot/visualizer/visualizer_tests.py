import asyncio
import errno
import json
import logging
import socket
import threading
import time
import unittest
import unittest.mock
import urllib.request
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional

import anyio
import pytest
import websockets

import pylabrobot.visualizer.visualizer as visualizer_module
from pylabrobot.__version__ import STANDARD_FORM_JSON_VERSION
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.resources import (
  Coordinate,
  Resource,
  cor_96_wellplate_360uL_Fb,
)
from pylabrobot.resources.hamilton import hamilton_96_tiprack_300uL
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.visualizer import Visualizer
from pylabrobot.visualizer.visualizer import (
  _build_method_registry,
  _sanitize_floats,
  _serialize_resource_tree,
  _state_of,
  _VisualizerHTTPRequestHandler,
)

pytestmark = pytest.mark.timeout(60)

_TRANSIENT_ACCEPT_ERROR_NAMES = frozenset(
  {
    "EAGAIN",
    "EWOULDBLOCK",
    "EINTR",
    "ECONNABORTED",
    "ENETDOWN",
    "EPROTO",
    "ENOPROTOOPT",
    "EHOSTDOWN",
    "ENONET",
    "EHOSTUNREACH",
    "EOPNOTSUPP",
    "ENOTSUP",
    "ENETUNREACH",
  }
)
_AVAILABLE_TRANSIENT_ACCEPT_ERRORS = tuple(
  pytest.param(error_number, id=name)
  for error_number, name in errno.errorcode.items()
  if name in _TRANSIENT_ACCEPT_ERROR_NAMES
)


@pytest.mark.parametrize("error_number", _AVAILABLE_TRANSIENT_ACCEPT_ERRORS)
def test_available_transient_accept_errors_are_retried(error_number: int) -> None:
  """Every transient accept errno available on this platform is retryable."""
  assert visualizer_module._is_transient_accept_error(OSError(error_number, "transient accept"))


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


class TestVisualizerLifecycleContract(unittest.TestCase):
  """Tests for the Visualizer's managed lifecycle contract."""

  def test_managed_setup_stop_and_context_methods_are_inherited(self) -> None:
    assert Visualizer.setup is HasLifespan.setup
    assert Visualizer.stop is HasLifespan.stop
    assert Visualizer.__aenter__ is HasLifespan.__aenter__
    assert Visualizer.__aexit__ is HasLifespan.__aexit__


class TestVisualizerSetupStop(AnyioTestBase):
  """Tests for the asyncio Visualizer lifecycle."""

  _anyio_backends = ["asyncio"]

  async def test_lifespan_can_be_entered_repeatedly(self) -> None:
    """The same Visualizer can own fresh server state in consecutive scopes."""

    r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(r, ws_port=0, fs_port=0, open_browser=False)

    async def run_once() -> None:
      async with vis:
        assert vis.loop is asyncio.get_running_loop()
        assert vis._task_group is not None
        assert vis.is_active
      assert not vis.has_connection()
      assert not vis.is_active
      assert vis._loop is None
      assert vis._task_group is None

    await run_once()
    await run_once()

  async def test_setup_stop_use_managed_bridge(self) -> None:
    """Inherited setup/stop drive the same lifespan for interactive callers."""

    resource = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(resource, ws_port=0, fs_port=0, open_browser=False)

    with pytest.warns(DeprecationWarning):
      await vis.setup()
    assert vis.is_active
    await vis.stop()
    assert not vis.is_active

  async def test_resource_mutation_before_entry_does_not_schedule_work(self) -> None:
    """Callbacks do not observe resource mutations before the Visualizer is active."""
    resource = Resource(size_x=100, size_y=100, size_z=100, name="root")
    descendant = Resource(size_x=10, size_y=10, size_z=10, name="descendant")
    resource.assign_child_resource(descendant, location=Coordinate(0, 0, 0))
    vis = Visualizer(resource, ws_port=0, fs_port=0, open_browser=False)
    send_command = unittest.mock.AsyncMock()
    vis.send_command = send_command  # type: ignore[method-assign]

    descendant.rotate(z=1)
    resource.unassign_child_resource(descendant)
    resource.assign_child_resource(
      Resource(size_x=10, size_y=10, size_z=10, name="before"),
      location=Coordinate(0, 0, 0),
    )
    await anyio.sleep(0)

    send_command.assert_not_awaited()

  async def test_resource_mutation_after_exit_does_not_schedule_work(self) -> None:
    """Callbacks do not observe resource mutations after the Visualizer has stopped."""
    resource = Resource(size_x=100, size_y=100, size_z=100, name="root")
    descendant = Resource(size_x=10, size_y=10, size_z=10, name="descendant")
    resource.assign_child_resource(descendant, location=Coordinate(0, 0, 0))
    vis = Visualizer(resource, ws_port=0, fs_port=0, open_browser=False)
    send_command = unittest.mock.AsyncMock()
    vis.send_command = send_command  # type: ignore[method-assign]

    async with vis:
      pass
    send_command.reset_mock()

    descendant.rotate(z=1)
    resource.unassign_child_resource(descendant)
    resource.assign_child_resource(
      Resource(size_x=10, size_y=10, size_z=10, name="after"),
      location=Coordinate(0, 0, 0),
    )
    await anyio.sleep(0)

    send_command.assert_not_awaited()

  async def test_reentry_does_not_duplicate_callbacks(self) -> None:
    """Consecutive runs emit one assignment, unassignment, and descendant state event each."""

    class CountingResource(Resource):
      """Resource that records serialization work caused by state callbacks."""

      def __init__(self, name: str) -> None:
        super().__init__(name=name, size_x=10, size_y=10, size_z=10)
        self.serialize_calls = 0

      def serialize_state(self) -> dict:
        self.serialize_calls += 1
        return super().serialize_state()

    resource = Resource(size_x=100, size_y=100, size_z=100, name="root")
    descendant = CountingResource(name="descendant")
    resource.assign_child_resource(descendant, location=Coordinate(0, 0, 0))
    vis = Visualizer(resource, ws_port=0, fs_port=0, open_browser=False)
    send_command = unittest.mock.AsyncMock()
    vis.send_command = send_command  # type: ignore[method-assign]

    for index in range(2):
      async with vis:
        send_command.reset_mock()
        descendant.serialize_calls = 0
        child = Resource(size_x=10, size_y=10, size_z=10, name=f"child-{index}")
        resource.assign_child_resource(child, location=Coordinate(0, 0, 0))
        resource.unassign_child_resource(child)
        descendant.rotate(z=1)
        with anyio.fail_after(5):
          while send_command.await_count < 3:
            await anyio.sleep(0)
        for _ in range(4):
          await anyio.sleep(0)

        assert [call.kwargs["event"] for call in send_command.await_args_list] == [
          "resource_assigned",
          "resource_unassigned",
          "set_state",
        ]
        assert descendant.serialize_calls == 2

  async def test_moving_hashable_resource_does_not_duplicate_state_callback(self) -> None:
    """Reassigning a moved resource retains one state observer and removes it on exit."""
    root = Resource(size_x=100, size_y=100, size_z=100, name="root")
    child = Resource(size_x=10, size_y=10, size_z=10, name="child")
    root.assign_child_resource(child, location=Coordinate(0, 0, 0))
    vis = Visualizer(root, ws_port=0, fs_port=0, open_browser=False)

    async with vis:
      assert len(child._resource_state_updated_callbacks) == 1
      original_hash = hash(child)
      root.unassign_child_resource(child)
      root.assign_child_resource(child, location=Coordinate(10, 0, 0))
      assert hash(child) != original_hash
      assert len(child._resource_state_updated_callbacks) == 1

    assert child._resource_state_updated_callbacks == []

  async def test_detached_resource_stops_sending_state_updates(self) -> None:
    """Unassigned children no longer retain a live Visualizer state observer."""
    root = Resource(size_x=100, size_y=100, size_z=100, name="root")
    child = Resource(size_x=10, size_y=10, size_z=10, name="child")
    grandchild = Resource(size_x=1, size_y=1, size_z=1, name="grandchild")
    child.assign_child_resource(grandchild, location=Coordinate(0, 0, 0))
    root.assign_child_resource(child, location=Coordinate(0, 0, 0))
    vis = Visualizer(root, ws_port=0, fs_port=0, open_browser=False)
    sent: list[str] = []

    async def record(event: str, data: Optional[dict] = None, wait_for_response: bool = True):
      del data, wait_for_response
      sent.append(event)

    vis.send_command = record  # type: ignore[method-assign]
    async with vis:
      root.unassign_child_resource(child)
      with anyio.fail_after(5):
        while "resource_unassigned" not in sent:
          await anyio.sleep(0)
      child.rotate(z=1)
      grandchild.rotate(z=1)
      await anyio.wait_all_tasks_blocked()
      assert "set_state" not in sent
      assert child._resource_state_updated_callbacks == []
      assert grandchild._resource_state_updated_callbacks == []

  async def test_stale_descendant_callback_cannot_target_a_later_run(self) -> None:
    """A callback fetched before teardown cannot enqueue after the Visualizer reenters."""
    resource = Resource(size_x=100, size_y=100, size_z=100, name="root")
    descendant = Resource(size_x=10, size_y=10, size_z=10, name="descendant")
    resource.assign_child_resource(descendant, location=Coordinate(0, 0, 0))
    vis = Visualizer(resource, ws_port=0, fs_port=0, open_browser=False)
    send_command = unittest.mock.AsyncMock()
    vis.send_command = send_command  # type: ignore[method-assign]

    async with vis:
      stale_callback = vis._state_update_callbacks[id(descendant)][1]

    async with vis:
      send_command.reset_mock()
      stale_callback(descendant.serialize_state())
      for _ in range(4):
        await anyio.sleep(0)
      send_command.assert_not_awaited()

  async def test_assignment_registration_racing_exit_is_fully_removed(self) -> None:
    """Exit waits for an in-progress assignment callback and removes its installed state callback."""
    registration_started = threading.Event()
    release_registration = threading.Event()

    class BlockingRegistrationResource(Resource):
      """Pause Visualizer state callback registration to expose teardown overlap."""

      def register_state_update_callback(self, callback) -> None:
        registration_started.set()
        if not release_registration.wait(2):
          raise TimeoutError("registration was not released")
        super().register_state_update_callback(callback)

    resource = Resource(size_x=100, size_y=100, size_z=100, name="root")
    child = BlockingRegistrationResource(name="child", size_x=10, size_y=10, size_z=10)
    vis = Visualizer(resource, ws_port=0, fs_port=0, open_browser=False)
    lifespan = vis.lifespan()
    await lifespan.__aenter__()
    mutation_errors: list[BaseException] = []

    def assign_child() -> None:
      try:
        resource.assign_child_resource(child, location=Coordinate(0, 0, 0))
      except BaseException as error:
        mutation_errors.append(error)

    assert vis._task_group is not None
    vis._task_group.start_soon(anyio.to_thread.run_sync, assign_child)
    assert await anyio.to_thread.run_sync(registration_started.wait, 1)
    release_timer = threading.Timer(0.05, release_registration.set)
    release_timer.start()
    try:
      await lifespan.__aexit__(None, None, None)
    finally:
      release_registration.set()
      release_timer.join(2)

    assert mutation_errors == []
    assert vis._state_update_callbacks == {}
    assert child._resource_state_updated_callbacks == []

  async def test_partial_root_callback_registration_unwinds_successes(self) -> None:
    """A failed second root registration removes the first callback before reentry."""

    class FailingRoot(Resource):
      """Fail the first Visualizer unassignment callback registration."""

      fail_registration = True

      def register_did_unassign_resource_callback(self, callback) -> None:
        if self.fail_registration:
          raise RuntimeError("unassignment registration failed")
        super().register_did_unassign_resource_callback(callback)

    resource = FailingRoot(name="root", size_x=100, size_y=100, size_z=100)
    vis = Visualizer(resource, ws_port=0, fs_port=0, open_browser=False)
    send_command = unittest.mock.AsyncMock()
    vis.send_command = send_command  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="unassignment registration failed"):
      async with vis:
        pass

    resource.fail_registration = False
    async with vis:
      resource.assign_child_resource(
        Resource(name="child", size_x=10, size_y=10, size_z=10),
        location=Coordinate(0, 0, 0),
      )
      with anyio.fail_after(5):
        while send_command.await_count < 1:
          await anyio.sleep(0)
      for _ in range(4):
        await anyio.sleep(0)

    assert send_command.await_count == 1

  async def test_failed_state_callback_registration_is_not_deregistered(self) -> None:
    """Cleanup tracks only state callbacks whose registration returned successfully."""

    class FailingStateRegistrationResource(Resource):
      """Reject state callback registration without installing the callback."""

      def register_state_update_callback(self, callback) -> None:
        raise RuntimeError("state registration failed")

    resource = FailingStateRegistrationResource(name="root", size_x=100, size_y=100, size_z=100)
    vis = Visualizer(resource, ws_port=0, fs_port=0, open_browser=False)

    with pytest.raises(RuntimeError, match="state registration failed"):
      async with vis:
        pass


class TestVisualizerBackend(AnyioTestBase):
  """Tests for Visualizer backend restrictions."""

  _anyio_backends = ["trio"]

  async def test_trio_backend_is_rejected_explicitly(self) -> None:
    resource = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(resource, ws_port=0, fs_port=0, open_browser=False)

    with pytest.raises(RuntimeError, match="only supports the asyncio backend"):
      async with vis:
        pass


class TestVisualizerServerStartupFailure(AnyioTestBase):
  """Tests for failures while starting the visualizer servers."""

  _anyio_backends = ["asyncio"]

  async def test_file_server_waits_for_claimed_request_on_exit(self) -> None:
    """A claimed request finishes before its owning listener closes."""
    vis = Visualizer(
      Resource(size_x=100, size_y=100, size_z=100, name="root"),
      ws_port=0,
      fs_port=0,
      open_browser=False,
    )
    ready, stop, exited = anyio.Event(), anyio.Event(), anyio.Event()
    worker_started, release_worker = threading.Event(), threading.Event()
    servers: list[visualizer_module._VisualizerFileServer] = []
    request, peer = socket.socketpair()

    def hold_request(*args: object, **kwargs: object) -> None:
      del args, kwargs
      worker_started.set()
      assert release_worker.wait(4), "worker was never released"

    async def owner() -> None:
      try:
        async with vis:
          httpd = vis._httpd
          task_group = vis._task_group
          assert httpd is not None and task_group is not None
          servers.append(httpd)
          with unittest.mock.patch.object(httpd, "finish_request", side_effect=hold_request):
            task_group.start_soon(vis._dispatch_file_request, httpd, request, ("local", 0))
            ready.set()
            await stop.wait()
      finally:
        exited.set()

    try:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(owner)
        try:
          with anyio.fail_after(5):
            await ready.wait()
            assert await anyio.to_thread.run_sync(worker_started.wait, 1)
            stop.set()
            await anyio.wait_all_tasks_blocked()
            assert not exited.is_set()
            assert vis._httpd is servers[0]
            release_worker.set()
            await exited.wait()
        finally:
          release_worker.set()
      assert request.fileno() == -1
      assert servers[0].socket.fileno() == -1
      assert vis._httpd is None
    finally:
      release_worker.set()
      request.close()
      peer.close()

  def _make_visualizer(self) -> Visualizer:
    """Create a Visualizer with an OS-selected websocket port."""
    resource = Resource(size_x=100, size_y=100, size_z=100, name="root")
    return Visualizer(resource, ws_port=0, fs_port=43000, open_browser=False)

  async def test_websocket_startup_permission_error_is_propagated(self) -> None:
    vis = self._make_visualizer()
    error = PermissionError(errno.EACCES, "permission denied")
    with unittest.mock.patch(
      "pylabrobot.visualizer.visualizer.websockets.asyncio.server.serve",
      side_effect=error,
    ) as serve:
      with pytest.raises(PermissionError):
        async with vis:
          pass
    assert serve.call_count == 1
    assert vis._loop is None
    assert vis._task_group is None

  async def test_websocket_startup_address_retries_are_bounded(self) -> None:
    vis = self._make_visualizer()
    error = OSError(errno.EADDRINUSE, "address already in use")
    with (
      unittest.mock.patch(
        "pylabrobot.visualizer.visualizer.websockets.asyncio.server.serve",
        side_effect=error,
      ) as serve,
      unittest.mock.patch("pylabrobot.visualizer.visualizer._SERVER_PORT_ATTEMPTS", 3),
    ):
      with pytest.raises(OSError):
        async with vis:
          pass
    assert serve.call_count == 3

  async def test_file_server_startup_permission_error_rolls_back_websocket(self) -> None:
    vis = self._make_visualizer()
    error = PermissionError(errno.EACCES, "permission denied")
    ws_servers: list[Any] = []
    orig_start_ws = vis._start_ws_server

    async def capture_ws(stack: AsyncExitStackWithShielding) -> None:
      await orig_start_ws(stack)
      ws_servers.append(vis._ws_server)

    with (
      unittest.mock.patch.object(vis, "_start_ws_server", side_effect=capture_ws),
      unittest.mock.patch(
        "http.server.HTTPServer.server_bind",
        side_effect=error,
      ),
    ):
      with pytest.raises(PermissionError):
        async with vis:
          pass

    assert len(ws_servers) == 1
    assert not ws_servers[0].is_serving()
    assert vis._ws_server is None
    assert vis._loop is None
    assert vis._task_group is None

  async def test_file_server_startup_address_retries_are_bounded(self) -> None:
    vis = self._make_visualizer()
    error = OSError(errno.EADDRINUSE, "address already in use")
    with (
      unittest.mock.patch("http.server.HTTPServer.server_bind", side_effect=error) as bind,
      unittest.mock.patch("pylabrobot.visualizer.visualizer._SERVER_PORT_ATTEMPTS", 3),
    ):
      with pytest.raises(OSError):
        async with vis:
          pass
    assert bind.call_count == 3

  async def test_file_server_startup_unrelated_error_is_not_retried(self) -> None:
    vis = self._make_visualizer()
    error = OSError(errno.EINVAL, "invalid argument")
    with unittest.mock.patch("http.server.HTTPServer.server_bind", side_effect=error) as bind:
      with pytest.raises(OSError):
        async with vis:
          pass
    assert bind.call_count == 1

  async def test_file_server_startup_registers_cancellation_before_child(self) -> None:
    """Task-group cleanup is purpose-named and registered before child work starts."""
    vis = self._make_visualizer()
    callbacks: list[Callable[..., Any]] = []

    class ImmediateEvent:
      """Test event whose waiter returns after child startup records readiness."""

      def __init__(self) -> None:
        self.is_set = False

      def set(self) -> None:
        self.is_set = True

      async def wait(self) -> None:
        assert self.is_set

    event = ImmediateEvent()

    class RecordingTaskGroup:
      """Task group double that checks cleanup before accepting child work."""

      def start_soon(self, function: Callable[..., Any], *args: Any) -> None:
        assert callbacks[-2:] == [vis._cancel_run_tasks, vis._interrupt_file_requests]
        event.set()

    task_group = RecordingTaskGroup()

    class RecordingStack:
      """Exit-stack double recording callbacks in registration order."""

      async def enter_task_group(self) -> RecordingTaskGroup:
        return task_group

      def callback(self, callback: Callable[..., Any]) -> None:
        callbacks.append(callback)

    try:
      with (
        unittest.mock.patch("pylabrobot.visualizer.visualizer.anyio.Event", return_value=event),
      ):
        await vis._start_file_server(RecordingStack())  # type: ignore[arg-type]
    finally:
      vis._close_file_server()


class TestVisualizerServer(AnyioTestBase):
  """Tests for servers (ws/fs)."""

  _anyio_backends = ["asyncio"]

  @asynccontextmanager
  async def lifespan(self, **kwargs):
    self.r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    self.vis = Visualizer(self.r, ws_port=0, fs_port=0, open_browser=False)
    async with self.vis:
      self.uri = f"ws://localhost:{self.vis.ws_port}"
      async with websockets.connect(self.uri) as client:
        self.client = client
        yield

  async def test_get_index_html(self):
    """Test that the index.html file is returned."""

    def fetch():
      response = urllib.request.urlopen(f"http://localhost:{self.vis.fs_port}/", timeout=10)
      return response.status, response.headers["Content-Type"]

    status, content_type = await anyio.to_thread.run_sync(fetch)
    assert status == 200
    assert content_type in ["text/html", "text/html; charset=utf-8"]

  async def test_liquid_color_default_substituted(self):
    """Test that the default liquid_color is substituted into the served HTML."""
    url = f"http://localhost:{self.vis.fs_port}/"
    html = await anyio.to_thread.run_sync(
      lambda: urllib.request.urlopen(url, timeout=10).read().decode()
    )
    assert "{{ liquid_color }}" not in html
    assert 'value="F39C12"' in html

  async def test_file_server_accepts_on_event_loop_before_worker_dispatch(self) -> None:
    """Only processing an already-accepted socket is dispatched to a worker."""
    httpd = self.vis._httpd
    assert httpd is not None
    loop_thread = threading.get_ident()
    accept_threads: list[int] = []
    get_request = httpd.get_request

    def tracked_get_request():
      accept_threads.append(threading.get_ident())
      return get_request()

    with unittest.mock.patch.object(httpd, "get_request", side_effect=tracked_get_request):
      status = await anyio.to_thread.run_sync(
        lambda: urllib.request.urlopen(f"http://localhost:{self.vis.fs_port}/", timeout=10).status
      )

    assert status == 200
    assert accept_threads == [loop_thread]

  async def test_file_server_closes_socket_when_cancelled_during_worker_handoff(self) -> None:
    """Cancellation before worker queueing leaves no unowned accepted socket."""
    httpd = self.vis._httpd
    assert httpd is not None
    request, peer = socket.socketpair()

    async def cancel_before_queue(*args: Any, **kwargs: Any) -> None:
      raise asyncio.CancelledError

    try:
      with unittest.mock.patch(
        "pylabrobot.visualizer.visualizer.anyio.to_thread.run_sync",
        side_effect=cancel_before_queue,
      ):
        with pytest.raises(asyncio.CancelledError):
          await self.vis._dispatch_file_request(httpd, request, ("local", 0))
      assert request.fileno() == -1
    finally:
      request.close()
      peer.close()

  async def test_file_server_retries_transient_accept_error(self) -> None:
    """An aborted pending connection does not stop later static-file requests."""
    httpd = self.vis._httpd
    assert httpd is not None
    get_request = httpd.get_request
    attempts = 0

    def transient_then_accept():
      nonlocal attempts
      attempts += 1
      if attempts == 1:
        raise OSError(errno.ECONNABORTED, "connection aborted")
      return get_request()

    with unittest.mock.patch.object(httpd, "get_request", side_effect=transient_then_accept):
      status = await anyio.to_thread.run_sync(
        lambda: urllib.request.urlopen(f"http://localhost:{self.vis.fs_port}/", timeout=10).status
      )

    assert status == 200
    assert attempts == 2
    assert self.vis.is_active

  async def test_file_server_does_not_mask_fatal_accept_error(self) -> None:
    """Listener closure and unrelated accept errors aren't classified as transient."""
    assert visualizer_module._is_transient_accept_error(
      OSError(errno.ECONNABORTED, "connection aborted")
    )
    assert not visualizer_module._is_transient_accept_error(OSError(errno.EBADF, "bad descriptor"))
    assert not visualizer_module._is_transient_accept_error(
      OSError(errno.EINVAL, "invalid argument")
    )

  async def test_connect(self):
    await self.client.send('{"event": "ready"}')
    response = await self.client.recv()
    response = json.loads(response)
    assert response == {
      "event": "set_root_resource",
      "data": {
        "resource": _serialize_resource_tree(self.r),
        "method_registry": _build_method_registry(self.r),
      },
      "id": "0001",
      "version": STANDARD_FORM_JSON_VERSION,
    }

  async def test_event_sent(self):
    await self.client.send('{"event": "ready"}')
    _ = await self.client.recv()  # set_root_resource
    _ = await self.client.recv()  # set_state
    _ = await self.client.recv()  # show_machine_tools

    await self.vis.send_command("test", wait_for_response=False)
    recv = await self.client.recv()
    data = json.loads(recv)
    assert data["event"] == "test"


class TestVisualizerConnectionShutdown(AnyioTestBase):
  """Tests for connection teardown and server release."""

  _anyio_backends = ["asyncio"]

  async def test_connected_client_receives_stop_before_connection_closes(self) -> None:
    resource = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(resource, ws_port=0, fs_port=0, open_browser=False)
    lifespan = vis.lifespan()
    await lifespan.__aenter__()
    client = await websockets.connect(f"ws://localhost:{vis.ws_port}")
    try:
      await client.send('{"event": "ready"}')
      await client.recv()
      await client.recv()
      await client.recv()

      await lifespan.__aexit__(None, None, None)

      message = json.loads(await client.recv())
      assert message["event"] == "stop"
      with pytest.raises(websockets.exceptions.ConnectionClosed):
        await client.recv()
    finally:
      await client.close()
      if vis.is_active:
        await lifespan.__aexit__(None, None, None)

    assert vis._websocket is None
    assert vis._ws_server is None
    assert vis._httpd is None
    assert vis._loop is None
    assert vis._task_group is None

  async def test_lifespan_releases_server_ports(self) -> None:
    resource = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(resource, ws_port=0, fs_port=0, open_browser=False)

    async with vis:
      ws_server = vis._ws_server
      httpd = vis._httpd
      assert ws_server is not None and ws_server.is_serving()
      assert httpd is not None and httpd.socket.fileno() != -1

    assert not ws_server.is_serving()
    assert httpd.socket.fileno() == -1
    assert vis._ws_server is None
    assert vis._httpd is None

  async def test_connection_shutdown_waits_for_bounded_partial_request(self) -> None:
    """A partial request worker finishes before listener shutdown."""
    resource = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(resource, ws_port=0, fs_port=0, open_browser=False)
    handler_started = threading.Event()
    handler_finished = threading.Event()
    handle_one_request = _VisualizerHTTPRequestHandler.handle_one_request

    def tracked_handle_one_request(handler: _VisualizerHTTPRequestHandler) -> None:
      handler_started.set()
      try:
        handle_one_request(handler)
      finally:
        handler_finished.set()

    lifespan = vis.lifespan()
    with (
      unittest.mock.patch.object(
        _VisualizerHTTPRequestHandler, "handle_one_request", tracked_handle_one_request
      ),
      unittest.mock.patch.object(_VisualizerHTTPRequestHandler, "timeout", 0.2),
      unittest.mock.patch("pylabrobot.visualizer.visualizer._VisualizerFileServer.handle_error"),
    ):
      await lifespan.__aenter__()
      client = await anyio.to_thread.run_sync(socket.create_connection, (vis.host, vis.fs_port), 1)
      try:
        await anyio.to_thread.run_sync(client.sendall, b"GET / HTTP/1.1\r\nHost: localhost\r\n")
        assert await anyio.to_thread.run_sync(handler_started.wait, 1)

        started_exit = anyio.current_time()
        await lifespan.__aexit__(None, None, None)
        assert handler_finished.is_set()
        assert anyio.current_time() - started_exit < 2
      finally:
        client.close()
        assert await anyio.to_thread.run_sync(handler_finished.wait, 1)
        if vis.is_active:
          await lifespan.__aexit__(None, None, None)

    assert vis._httpd is None
    assert vis._task_group is None

  async def test_blocked_partial_request_cannot_hold_shutdown(self) -> None:
    """Exit wakes an accepted worker blocked on a partial request before client release."""
    vis = Visualizer(
      Resource(size_x=100, size_y=100, size_z=100, name="root"),
      ws_port=0,
      fs_port=0,
      open_browser=False,
    )
    first_byte_consumed = threading.Event()
    handler_finished = threading.Event()
    release_client = threading.Event()
    second_read: list[bytes] = []

    def read_partial_request(handler: _VisualizerHTTPRequestHandler) -> None:
      try:
        assert handler.rfile.read(1) == b"x"
        first_byte_consumed.set()
        second_read.append(handler.rfile.read(1))
      finally:
        handler_finished.set()

    def release_after_delay(client: socket.socket) -> None:
      if not release_client.wait(0.8):
        try:
          client.shutdown(socket.SHUT_RDWR)
        except OSError:
          pass

    with unittest.mock.patch.object(
      _VisualizerHTTPRequestHandler, "handle_one_request", read_partial_request
    ):
      lifespan = vis.lifespan()
      await lifespan.__aenter__()
      client = await anyio.to_thread.run_sync(socket.create_connection, (vis.host, vis.fs_port), 1)
      client_releaser: Optional[threading.Thread] = None
      try:
        client.sendall(b"x")
        assert await anyio.to_thread.run_sync(first_byte_consumed.wait, 1)
        assert not handler_finished.is_set()
        client_releaser = threading.Thread(target=release_after_delay, args=(client,))
        client_releaser.start()
        started_exit = anyio.current_time()
        await lifespan.__aexit__(None, None, None)
        assert anyio.current_time() - started_exit < 0.4
        assert handler_finished.is_set()
        assert second_read == [b""]
        assert vis._httpd is None
      finally:
        release_client.set()
        client.close()
        if client_releaser is not None:
          client_releaser.join(2)
          assert not client_releaser.is_alive()
        if vis.is_active:
          await lifespan.__aexit__(None, None, None)


class TestVisualizerShowMachineTools(AnyioTestBase):
  """Tests for the show_machine_tools_at_start parameter."""

  _anyio_backends = ["asyncio"]

  async def test_show_machine_tools_at_start_false(self):
    """When show_machine_tools_at_start=False, the show_machine_tools event should not be sent."""
    r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(r, ws_port=0, fs_port=0, open_browser=False, show_machine_tools_at_start=False)
    vis.send_command = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    async with vis:
      await vis._send_resources_and_state()

      for call in vis.send_command.call_args_list:  # type: ignore[attr-defined]
        assert (call[1].get("event") if call[1] else call[0][0]) != "show_machine_tools"


class TestVisualizerCommand(AnyioTestBase):
  """Tests for command sending using the visualizer backend."""

  _anyio_backends = ["asyncio"]

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    self.maxDiff = None
    self.r = Resource(size_x=100, size_y=100, size_z=100, name="root")
    self.vis = Visualizer(self.r, ws_port=0, fs_port=0, open_browser=False)

    # mock the send_command method to catch the events
    self.send_command_mock = unittest.mock.AsyncMock()
    self.vis.send_command = self.send_command_mock  # type: ignore[method-assign]

    await stack.enter_async_context(self.vis)

  async def _wait_for_event(self, event: str, data_key: Optional[str] = None, timeout: float = 5.0):
    """Wait until the most recent send_command call is ``event`` (optionally carrying
    ``data_key`` in its data), yielding to the loop.

    Replaces fixed ``time.sleep()`` waits: those block the event loop and race under CI
    load. Checking the last call - not just any call - matters when one action fans out
    into many events (e.g. set_well_volumes emits a set_state per well); the assert reads
    the last call, so we wait until that call carries the expected payload.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
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
    self.send_command_mock.assert_called_once_with(
      event="resource_assigned",
      data={
        "resource": _serialize_resource_tree(child),
        "method_registry": _build_method_registry(child),
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
    assert call_args["event"] == "set_state"
    assert call_args["data"]["plate_01_well_H12"]["volume"] == 500

  async def test_callback_events_preserve_payloads_batching_and_order(self) -> None:
    """Assignment, unassignment, and latest state are emitted in callback order."""

    class StatefulResource(Resource):
      """Resource with a small explicit state used to verify latest-value batching."""

      def __init__(self, name: str) -> None:
        super().__init__(name=name, size_x=10, size_y=10, size_z=10)
        self.value = 0

      def serialize_state(self) -> dict:
        return {"value": self.value}

      def update(self, value: int) -> None:
        self.value = value
        self._state_updated()

    child = StatefulResource(name="child")
    self.r.assign_child_resource(child, location=Coordinate(1, 2, 3))
    assigned_data = {
      "resource": _serialize_resource_tree(child),
      "method_registry": _build_method_registry(child),
      "state": {"child": {"value": 0}},
      "parent_name": "root",
    }
    child.update(1)
    child.update(2)
    self.r.unassign_child_resource(child)

    with anyio.fail_after(5):
      while self.send_command_mock.await_count != 3:
        await anyio.sleep(0)

    calls = self.send_command_mock.await_args_list
    assert [call.kwargs["event"] for call in calls] == [
      "resource_assigned",
      "resource_unassigned",
      "set_state",
    ]
    assert calls[0].kwargs == {
      "event": "resource_assigned",
      "data": assigned_data,
      "wait_for_response": False,
    }
    assert calls[1].kwargs == {
      "event": "resource_unassigned",
      "data": {"resource_name": "child"},
      "wait_for_response": False,
    }
    assert calls[2].kwargs == {
      "event": "set_state",
      "data": {"child": {"value": 2}},
      "wait_for_response": False,
    }


class TestVisualizerOutbox(AnyioTestBase):
  """Tests for bounded, failure-isolated callback delivery."""

  _anyio_backends = ["asyncio"]

  def test_size_one_outbox_drops_newest_event_with_one_warning(self, caplog, monkeypatch) -> None:
    """A full outbox retains its old event and warns once for the dropped new event."""
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    resource = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(resource, open_browser=False)
    send_stream, receive_stream = anyio.create_memory_object_stream[tuple[str, dict]](1)
    vis._outbox_send = send_stream

    vis._enqueue_outbound("first", {"sequence": 1})
    with caplog.at_level("WARNING", logger=visualizer_module.logger.name):
      vis._enqueue_outbound("second", {"sequence": 2})

    assert receive_stream.receive_nowait() == ("first", {"sequence": 1})
    assert [record.getMessage() for record in caplog.records] == [
      "visualizer: dropping 'second' event (outbox closed or full)"
    ]

  async def test_worker_logs_failed_send_and_continues(self, caplog, monkeypatch) -> None:
    """One browser-send failure does not prevent a later outbox event from sending."""
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    resource = Resource(size_x=100, size_y=100, size_z=100, name="root")
    vis = Visualizer(resource, open_browser=False)
    sent: list[str] = []

    async def send_command(
      event: str,
      data: Optional[dict[str, Any]] = None,
      wait_for_response: bool = True,
    ) -> Optional[dict]:
      sent.append(event)
      if event == "first":
        raise RuntimeError("browser send failed")
      return None

    vis.send_command = send_command  # type: ignore[method-assign]
    send_stream, receive_stream = anyio.create_memory_object_stream[tuple[str, dict]](2)
    send_stream.send_nowait(("first", {}))
    send_stream.send_nowait(("second", {}))
    send_stream.close()

    with caplog.at_level("ERROR", logger=visualizer_module.logger.name):
      await vis._outbox_worker(receive_stream)

    assert sent == ["first", "second"]
    assert [record.getMessage() for record in caplog.records] == [
      "visualizer: failed to send 'first' event to browser"
    ]


class VisualizerHeldToolTests(unittest.TestCase):
  def test_a_racked_tip_is_its_spots_state_not_a_resource_in_the_tree(self):
    """A tip serializes without a location, which the page cannot draw; its spot shows it."""
    rack = hamilton_96_tiprack_300uL(name="rack", with_tips=True)
    spot = rack.get_item("A1")
    self.assertTrue(spot.children)

    tree = _serialize_resource_tree(rack)
    spot_data = next(child for child in tree["children"] if child["name"] == spot.name)
    self.assertEqual(spot_data["children"], [])
    self.assertNotIn(spot.get_tip().name, _state_of(rack))
    self.assertIn(spot.name, _state_of(rack))


class TestVisualizerMetadataHandling(unittest.TestCase):
  """Tests for visualizer metadata handling."""

  def test_serialize_resource_tree_preserves_and_stringifies_metadata(self):
    deck = Resource(
      size_x=100,
      size_y=100,
      size_z=100,
      name="deck",
      metadata={"level": 0, "fn": lambda x: x},
    )
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    plate.metadata = {"level": 1, "unserializable": object()}
    plate.get_well("A1").metadata = {"level": 2, "sample_id": 123}
    deck.assign_child_resource(plate, location=Coordinate(0, 0, 0))

    serialized = _serialize_resource_tree(deck)
    self.assertIn("metadata", serialized)
    self.assertEqual(serialized["metadata"]["level"], 0)

    plate_serialized = serialized["children"][0]
    self.assertIn("metadata", plate_serialized)
    self.assertEqual(plate_serialized["metadata"]["level"], 1)

    well_serialized = plate_serialized["children"][0]
    self.assertIn("metadata", well_serialized)
    self.assertEqual(well_serialized["metadata"]["level"], 2)
    self.assertEqual(well_serialized["metadata"]["sample_id"], 123)

    # Ensure _assemble_command succeeds and round-trips via json.loads
    vis = Visualizer(deck, open_browser=False)
    payload, _ = vis._assemble_command(
      "set_root_resource", {"resource": _serialize_resource_tree(deck)}
    )
    decoded = json.loads(payload)["data"]["resource"]
    self.assertEqual(decoded["name"], "deck")
    self.assertEqual(decoded["metadata"]["level"], 0)
    self.assertIsInstance(decoded["metadata"]["fn"], str)
    self.assertEqual(decoded["children"][0]["metadata"]["level"], 1)
    self.assertEqual(decoded["children"][0]["children"][0]["metadata"]["sample_id"], 123)
