"""Software-only lifecycle tests for the Inheco SiLA transport."""

import datetime
import socket
import threading
import urllib.request
import xml.etree.ElementTree as ET
from types import TracebackType
from typing import Any, Optional, Type, cast
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import HasLifespan
from pylabrobot.inheco.scila.soap import soap_encode
from pylabrobot.inheco.transport import sila as sila_module
from pylabrobot.inheco.transport.sila import (
  InhecoSiLAInterface,
  SOAP_RESPONSE_ResponseEventResponse,
)
from pylabrobot.testing.concurrency import AnyioTestBase


def _response_event(request_id: int, *, return_code: int = 3, message: str = "Success") -> bytes:
  """Build one asynchronous SiLA callback body."""
  return cast(
    bytes,
    soap_encode(
      "ResponseEvent",
      {
        "requestId": request_id,
        "returnValue": {"returnCode": return_code, "message": message},
        "responseData": '<Parameters><Parameter name="value"><Int32>42</Int32></Parameter></Parameters>',
      },
      method_ns="http://sila.coop",
    ).encode("utf-8"),
  )


def _immediate_response(command: str) -> bytes:
  """Build one successful immediate SiLA command response."""
  return cast(
    bytes,
    soap_encode(
      f"{command}Response",
      {f"{command}Result": {"returnCode": 1, "message": "Success"}},
      method_ns="http://sila.coop",
    ).encode("utf-8"),
  )


def _accepted_response(command: str, duration: object = datetime.timedelta(seconds=1)) -> bytes:
  """Build one immediate response accepting an asynchronous command."""
  result = {"returnCode": 2, "message": "Accepted"}
  if duration is not None:
    result["duration"] = duration
  return cast(
    bytes,
    soap_encode(
      f"{command}Response",
      {f"{command}Result": result},
      method_ns="http://sila.coop",
    ).encode("utf-8"),
  )


async def _post_callback(port: int, body: bytes) -> bytes:
  """POST a callback without blocking the asynchronous test runner."""

  def post() -> bytes:
    request = urllib.request.Request(
      f"http://127.0.0.1:{port}/callback?source=test", data=body, method="POST"
    )
    with urllib.request.urlopen(request, timeout=2) as response:
      return cast(bytes, response.read())

  return cast(bytes, await anyio.to_thread.run_sync(post))


class _BlockingResponse:
  """Context-managed response returned by the blocking machine fake."""

  def __init__(self, body: bytes) -> None:
    self._body = body

  def __enter__(self) -> "_BlockingResponse":
    return self

  def __exit__(
    self,
    exception_type: Optional[Type[BaseException]],
    exception: Optional[BaseException],
    traceback: Optional[TracebackType],
  ) -> None:
    return None

  def read(self) -> bytes:
    return self._body


class _RecordingThreadingHTTPServer(sila_module._CallbackHTTPServer):
  """Signal when the real callback handler begins processing a request."""

  def __init__(self, *args: Any, **kwargs: Any) -> None:
    self.request_started = threading.Event()
    super().__init__(*args, **kwargs)

  def finish_request(self, request: Any, client_address: Any) -> None:
    self.request_started.set()
    super().finish_request(request, client_address)


class _RecordingInhecoSiLAInterface(InhecoSiLAInterface):
  """Record callback cleanup ordering around the real transport cleanup."""

  def __init__(self) -> None:
    super().__init__(machine_ip="127.0.0.1", client_ip="127.0.0.1")
    self.cleanup_trace: list[str] = []

  async def _close_callback_server(self) -> None:
    self.cleanup_trace.append("cleanup-started")
    await super()._close_callback_server()


class _PausedStartupInhecoSiLAInterface(InhecoSiLAInterface):
  """Pause callback serving before its worker can report readiness."""

  def __init__(self) -> None:
    super().__init__(machine_ip="127.0.0.1", client_ip="127.0.0.1")
    self.child_started = anyio.Event()

  async def _serve_callbacks(self) -> None:
    self.child_started.set()
    await anyio.sleep_forever()


class TestInhecoSiLAInterface(AnyioTestBase):
  """Exercise the transport through its managed lifespan on both AnyIO backends."""

  async def test_uses_async_resource_managed_methods(self) -> None:
    interface = InhecoSiLAInterface(machine_ip="127.0.0.1", client_ip="127.0.0.1")

    assert isinstance(interface, HasLifespan)
    assert InhecoSiLAInterface.__aenter__ is HasLifespan.__aenter__
    assert InhecoSiLAInterface.__aexit__ is HasLifespan.__aexit__
    assert InhecoSiLAInterface.setup is HasLifespan.setup
    assert InhecoSiLAInterface.stop is HasLifespan.stop
    assert "start" not in InhecoSiLAInterface.__dict__
    assert "close" not in InhecoSiLAInterface.__dict__

  async def test_lifespan_binds_and_releases_callback_server(self) -> None:
    interface = InhecoSiLAInterface(machine_ip="127.0.0.1", client_ip="127.0.0.1")

    async with interface:
      port = interface.bound_port

      def connect() -> None:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
          pass

      await anyio.to_thread.run_sync(connect)

    with socket.socket() as released_socket:
      released_socket.bind(("127.0.0.1", port))

  async def test_lifespan_supports_sequential_reentry(self) -> None:
    interface = InhecoSiLAInterface(machine_ip="127.0.0.1", client_ip="127.0.0.1")

    async with interface:
      assert interface.is_active
      assert interface.bound_port > 0
    async with interface:
      assert interface.is_active
      assert interface.bound_port > 0

    assert not interface.is_active

  async def test_cancelled_callback_startup_completes_and_releases_port(self) -> None:
    interface = _PausedStartupInhecoSiLAInterface()
    scopes: list[anyio.CancelScope] = []
    finished = anyio.Event()

    async def enter_interface() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with interface:
            pytest.fail("cancelled entry reached the body")
        finally:
          finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_interface)
      await interface.child_started.wait()
      port = interface.bound_port
      scopes[0].cancel()
      with anyio.move_on_after(1):
        await finished.wait()
      completed_without_rescue = finished.is_set()
      if not completed_without_rescue:
        task_group.start_soon(InhecoSiLAInterface._serve_callbacks, interface)
        await finished.wait()

    with socket.socket() as released_socket:
      released_socket.bind(("127.0.0.1", port))
    assert completed_without_rescue

  async def test_bind_failure_clears_run_state_and_prevents_network_send(self) -> None:
    interface = InhecoSiLAInterface(machine_ip="127.0.0.1", client_ip="127.0.0.1")
    bind_error = OSError("bind failed")

    with mock.patch.object(sila_module, "_CallbackHTTPServer", side_effect=bind_error):
      with pytest.raises(OSError, match="bind failed") as error_info:
        async with interface:
          pytest.fail("failed entry reached the body")

    assert error_info.value is bind_error
    assert not interface.is_active
    assert interface._making_request is None
    assert interface._portal is None
    assert interface._httpd is None
    assert interface._callback_server_started is None
    assert interface._callback_server_stopped is None
    assert interface._callback_server_startup_resolved is None
    assert not interface._callback_server_worker_requested
    with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network send")):
      with pytest.raises(RuntimeError, match="is not active"):
        await interface.send_command("Read")

    async with interface:
      assert interface.is_active
      assert interface.bound_port > 0

  async def test_callback_server_routes_matching_response(self) -> None:
    interface = InhecoSiLAInterface(machine_ip="127.0.0.1", client_ip="127.0.0.1")

    async with interface:
      pending = interface._SiLACommand(name="Read", request_id=17, completed=anyio.Event())
      interface._pending = pending

      response = await _post_callback(interface.bound_port, _response_event(17))

      assert response == SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")
      assert pending.completed.is_set()
      result = pending.result
      assert isinstance(result, ET.Element)
      value = result.find(".//Int32")
      assert value is not None
      assert value.text == "42"

  async def test_partial_callback_body_does_not_block_shutdown_or_port_release(self) -> None:
    interface = InhecoSiLAInterface(machine_ip="127.0.0.1", client_ip="127.0.0.1")
    entered = anyio.Event()
    exit_lifespan = anyio.Event()
    finished = anyio.Event()
    ports: list[int] = []

    async def run_lifespan() -> None:
      async with interface:
        ports.append(interface.bound_port)
        entered.set()
        await exit_lifespan.wait()
      finished.set()

    with (
      mock.patch.object(sila_module, "_CALLBACK_REQUEST_TIMEOUT_S", 60.0),
      mock.patch.object(sila_module, "_CallbackHTTPServer", _RecordingThreadingHTTPServer),
    ):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_lifespan)
        await entered.wait()
        client = await anyio.to_thread.run_sync(
          socket.create_connection, ("127.0.0.1", ports[0]), 1
        )
        try:
          await anyio.to_thread.run_sync(
            client.sendall,
            b"POST /callback HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\n<",
          )
          server = interface._httpd
          assert isinstance(server, _RecordingThreadingHTTPServer)
          assert await anyio.to_thread.run_sync(server.request_started.wait, 1)
          exit_lifespan.set()
          with anyio.move_on_after(1):
            await finished.wait()
          completed_before_client_close = finished.is_set()
        finally:
          client.close()
        await finished.wait()

    with socket.socket() as released_socket:
      released_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      released_socket.bind(("127.0.0.1", ports[0]))
    assert completed_before_client_close

  async def test_mismatched_request_id_leaves_command_pending(self) -> None:
    interface = InhecoSiLAInterface(machine_ip="127.0.0.1", client_ip="127.0.0.1")

    async with interface:
      pending = interface._SiLACommand(name="Read", request_id=17, completed=anyio.Event())
      interface._pending = pending

      response = await _post_callback(interface.bound_port, _response_event(18))

      assert response == SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")
      assert not pending.completed.is_set()

    assert pending.completed.is_set()
    assert isinstance(pending.error, RuntimeError)

  async def test_exit_releases_pending_command_and_ignores_late_callback(self) -> None:
    interface = InhecoSiLAInterface(machine_ip="127.0.0.1", client_ip="127.0.0.1")
    pending = interface._SiLACommand(name="Read", request_id=17, completed=anyio.Event())

    async with interface:
      interface._pending = pending

    assert pending.completed.is_set()
    assert isinstance(pending.error, RuntimeError)
    assert interface._pending is None
    request = interface._HTTPRequest(
      method="POST", path="/callback", query="", headers={}, body=_response_event(17)
    )
    assert await interface._on_http(request) == SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")

  async def test_machine_request_uses_finite_socket_operation_timeout(self) -> None:
    interface = InhecoSiLAInterface(machine_ip="127.0.0.1", client_ip="127.0.0.1")
    observed_timeouts: list[float] = []

    def urlopen_with_timeout(
      request: urllib.request.Request, *, timeout: float
    ) -> _BlockingResponse:
      observed_timeouts.append(timeout)
      return _BlockingResponse(_immediate_response("Read"))

    with mock.patch.object(urllib.request, "urlopen", urlopen_with_timeout):
      async with interface:
        await interface.send_command("Read")

    assert observed_timeouts == [sila_module._MACHINE_REQUEST_TIMEOUT_S]

  async def test_cancellation_waits_for_machine_worker_before_callback_cleanup(self) -> None:
    interface = _RecordingInhecoSiLAInterface()
    started = threading.Event()
    release = threading.Event()
    scopes: list[anyio.CancelScope] = []
    finished = anyio.Event()
    worker_finished = threading.Event()

    observed_timeouts: list[float] = []

    def blocking_urlopen(request: urllib.request.Request, *, timeout: float) -> _BlockingResponse:
      observed_timeouts.append(timeout)
      started.set()
      release.wait()
      interface.cleanup_trace.append("worker-finished")
      worker_finished.set()
      return _BlockingResponse(_immediate_response("Read"))

    async def send_in_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with interface:
          await interface.send_command("Read")
      finished.set()

    with anyio.fail_after(3):
      with mock.patch.object(urllib.request, "urlopen", blocking_urlopen):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(send_in_scope)
          assert await anyio.to_thread.run_sync(started.wait, 1)
          scopes[0].cancel()
          await anyio.lowlevel.checkpoint()
          try:
            assert not finished.is_set()
            assert interface._httpd is not None
          finally:
            release.set()
          await finished.wait()

    assert worker_finished.is_set()
    assert observed_timeouts == [sila_module._MACHINE_REQUEST_TIMEOUT_S]
    assert interface.cleanup_trace == ["worker-finished", "cleanup-started"]
    assert interface._httpd is None
    assert not interface.is_active

  async def test_cancel_during_post_waits_for_accepted_command_callback(self) -> None:
    """Reconcile an accepted POST even when cancellation arrives before its worker returns."""
    interface = _RecordingInhecoSiLAInterface()
    post_started = threading.Event()
    release_post = threading.Event()
    finished = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    def accepted_urlopen(request: urllib.request.Request, *, timeout: float) -> _BlockingResponse:
      post_started.set()
      release_post.wait(timeout=2)
      return _BlockingResponse(_accepted_response("ExecuteMethod"))

    async def send_in_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with interface:
          await interface.send_command("ExecuteMethod", methodName="PLR_Test")
      finished.set()

    with mock.patch.object(urllib.request, "urlopen", accepted_urlopen):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(send_in_scope)
        assert await anyio.to_thread.run_sync(post_started.wait, 1)
        scopes[0].cancel()
        try:
          assert not finished.is_set()
          release_post.set()
          with anyio.fail_after(1):
            while interface._pending is None:
              assert not finished.is_set()
              await anyio.lowlevel.checkpoint()
          pending = interface._pending
          assert pending is not None
          assert not finished.is_set()
          request = interface._HTTPRequest(
            method="POST",
            path="/callback",
            query="",
            headers={},
            body=_response_event(pending.request_id),
          )
          await interface._on_http(request)
          with anyio.fail_after(1):
            await finished.wait()
        finally:
          release_post.set()

    assert interface.cleanup_trace == ["cleanup-started"]
    assert scopes[0].cancelled_caught

  @pytest.mark.parametrize("callback_return_code", [3, 5])
  async def test_cancellation_after_acceptance_reconciles_callback_before_cleanup(
    self, callback_return_code: int
  ) -> None:
    """Kills awaiting an accepted command's callback future outside the transaction shield."""
    interface = _RecordingInhecoSiLAInterface()
    scopes: list[anyio.CancelScope] = []
    cancellation_caught: list[bool] = []
    command_errors: list[RuntimeError] = []
    command_returned = False
    finished = anyio.Event()
    release_callback = anyio.Event()
    callback_reconciled = anyio.Event()

    def accepted_urlopen(request: urllib.request.Request, *, timeout: float) -> _BlockingResponse:
      assert timeout == 10.0
      return _BlockingResponse(_accepted_response("ExecuteMethod"))

    async def send_in_scope() -> None:
      nonlocal command_returned
      async with interface:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await interface.send_command("ExecuteMethod", methodName="PLR_Test")
            command_returned = True
          except RuntimeError as error:
            command_errors.append(error)
        cancellation_caught.append(scope.cancelled_caught)
      finished.set()

    async def deliver_callback(request_id: int) -> None:
      await release_callback.wait()
      request = interface._HTTPRequest(
        method="POST",
        path="/callback",
        query="",
        headers={},
        body=_response_event(
          request_id,
          return_code=callback_return_code,
          message="callback failed" if callback_return_code != 3 else "Success",
        ),
      )
      response = await interface._on_http(request)
      assert response == SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")
      interface.cleanup_trace.append("callback-reconciled")
      callback_reconciled.set()

    with anyio.fail_after(3):
      with mock.patch.object(urllib.request, "urlopen", accepted_urlopen):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(send_in_scope)
          while interface._pending is None:
            await anyio.lowlevel.checkpoint()
          request_id = interface._pending.request_id
          scopes[0].cancel()
          await anyio.lowlevel.checkpoint()
          assert interface.cleanup_trace == []
          assert interface._pending is not None
          task_group.start_soon(deliver_callback, request_id)
          release_callback.set()
          await callback_reconciled.wait()
          await finished.wait()

    assert not command_returned
    if callback_return_code == 3:
      assert cancellation_caught == [True]
      assert command_errors == []
    else:
      assert cancellation_caught == [False]
      assert len(command_errors) == 1
      assert "callback failed" in str(command_errors[0])
    assert interface.cleanup_trace == ["callback-reconciled", "cleanup-started"]
    assert interface._pending is None

  async def test_accepted_callback_deadline_uses_protocol_duration(self) -> None:
    """Kill reusing the shorter machine-request timeout for an accepted callback."""
    interface = InhecoSiLAInterface(machine_ip="127.0.0.1", client_ip="127.0.0.1")
    observed_deadlines: list[float] = []
    fail_after = anyio.fail_after

    def accepted_urlopen(request: urllib.request.Request, *, timeout: float) -> _BlockingResponse:
      return _BlockingResponse(
        _accepted_response("ExecuteMethod", datetime.timedelta(seconds=12.5))
      )

    def record_deadline(delay: float):
      observed_deadlines.append(delay)
      return fail_after(1)

    async def deliver_callback() -> None:
      while interface._pending is None:
        await anyio.lowlevel.checkpoint()
      request_id = interface._pending.request_id
      request = interface._HTTPRequest(
        method="POST",
        path="/callback",
        query="",
        headers={},
        body=_response_event(request_id),
      )
      await interface._on_http(request)

    with (
      mock.patch.object(urllib.request, "urlopen", accepted_urlopen),
      mock.patch.object(sila_module.anyio, "fail_after", record_deadline),
    ):
      async with interface:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(deliver_callback)
          result = await interface.send_command("ExecuteMethod", methodName="PLR_Test")

    assert isinstance(result, ET.Element)
    assert observed_deadlines == [22.5]

  async def test_cancellation_without_callback_reports_timeout_not_cancellation(self) -> None:
    """An accepted command cannot promise device idleness when its callback never arrives."""
    interface = InhecoSiLAInterface(machine_ip="127.0.0.1", client_ip="127.0.0.1")
    scopes: list[anyio.CancelScope] = []
    failures: list[TimeoutError] = []
    finished = anyio.Event()

    def accepted_urlopen(request: urllib.request.Request, *, timeout: float) -> _BlockingResponse:
      return _BlockingResponse(_accepted_response("ExecuteMethod", datetime.timedelta(0)))

    async def send_in_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await interface.send_command("ExecuteMethod", methodName="PLR_Test")
        except TimeoutError as error:
          failures.append(error)
      finished.set()

    with (
      mock.patch.object(urllib.request, "urlopen", accepted_urlopen),
      mock.patch.object(sila_module, "_CALLBACK_REQUEST_TIMEOUT_S", 0.02),
    ):
      async with interface:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(send_in_scope)
          with anyio.fail_after(1):
            while interface._pending is None:
              await anyio.lowlevel.checkpoint()
          scopes[0].cancel()
          with anyio.fail_after(1):
            await finished.wait()
        assert len(failures) == 1
        assert not scopes[0].cancelled_caught
        assert interface._pending is not None
        assert not interface._pending.completed.is_set()

  @pytest.mark.parametrize("duration", [None, "later"])
  async def test_accepted_response_requires_valid_protocol_duration(self, duration: object) -> None:
    """Kill silently inventing a callback deadline for malformed accepted responses."""
    interface = InhecoSiLAInterface(machine_ip="127.0.0.1", client_ip="127.0.0.1")

    def accepted_urlopen(request: urllib.request.Request, *, timeout: float) -> _BlockingResponse:
      return _BlockingResponse(_accepted_response("ExecuteMethod", duration))

    with mock.patch.object(urllib.request, "urlopen", accepted_urlopen):
      async with interface:
        with pytest.raises(
          ValueError,
          match="duration not found or invalid in accepted response for ExecuteMethod",
        ):
          await interface.send_command("ExecuteMethod", methodName="PLR_Test")
        assert interface._pending is None

  @pytest.mark.parametrize("callback_return_code", [3, 5])
  async def test_callback_timeout_retains_command_until_late_reconciliation(
    self, callback_return_code: int
  ) -> None:
    """Kill cancelling or releasing an accepted command when its caller times out."""
    interface = InhecoSiLAInterface(machine_ip="127.0.0.1", client_ip="127.0.0.1")
    machine_requests = 0

    def accepted_urlopen(request: urllib.request.Request, *, timeout: float) -> _BlockingResponse:
      nonlocal machine_requests
      machine_requests += 1
      return _BlockingResponse(_accepted_response("ExecuteMethod", datetime.timedelta(0)))

    with (
      mock.patch.object(sila_module, "_CALLBACK_REQUEST_TIMEOUT_S", 0.01),
      mock.patch.object(urllib.request, "urlopen", accepted_urlopen),
    ):
      async with interface:
        with pytest.raises(TimeoutError):
          await interface.send_command("ExecuteMethod", methodName="PLR_Test")

        pending = interface._pending
        assert pending is not None
        assert not pending.completed.is_set()
        with pytest.raises(RuntimeError, match="can't send multiple commands at the same time"):
          await interface.send_command("Read")
        assert machine_requests == 1

        request = interface._HTTPRequest(
          method="POST",
          path="/callback",
          query="",
          headers={},
          body=_response_event(
            pending.request_id,
            return_code=callback_return_code,
            message="callback failed" if callback_return_code != 3 else "Success",
          ),
        )
        assert await interface._on_http(request) == SOAP_RESPONSE_ResponseEventResponse.encode(
          "utf-8"
        )
        assert pending.completed.is_set()
        if callback_return_code == 5:
          assert str(pending.error) == (
            "Command ExecuteMethod failed with code 5: 'callback failed'"
          )
        else:
          assert pending.error is None
          assert isinstance(pending.result, ET.Element)
        assert interface._pending is None

  async def test_lifespan_exit_releases_callback_retained_after_timeout(self) -> None:
    """Kill leaking an accepted command that timed out before lifespan teardown."""
    interface = InhecoSiLAInterface(machine_ip="127.0.0.1", client_ip="127.0.0.1")

    def accepted_urlopen(request: urllib.request.Request, *, timeout: float) -> _BlockingResponse:
      return _BlockingResponse(_accepted_response("ExecuteMethod", datetime.timedelta(0)))

    with (
      mock.patch.object(sila_module, "_CALLBACK_REQUEST_TIMEOUT_S", 0.01),
      mock.patch.object(urllib.request, "urlopen", accepted_urlopen),
    ):
      async with interface:
        with pytest.raises(TimeoutError):
          await interface.send_command("ExecuteMethod", methodName="PLR_Test")
        pending = interface._pending
        assert pending is not None
        assert not pending.completed.is_set()

    assert pending.completed.is_set()
    assert isinstance(pending.error, RuntimeError)
    assert interface._pending is None


class TestInhecoSiLAInterfaceTrio(AnyioTestBase):
  """Exercise the callback bridge from a Trio task and an HTTP handler thread."""

  _anyio_backends = ["trio"]

  async def test_trio_accepts_http_callback_and_completes_command(self) -> None:
    interface = InhecoSiLAInterface(machine_ip="127.0.0.1", client_ip="127.0.0.1")
    results: list[ET.Element] = []
    urlopen = urllib.request.urlopen

    def accepted_urlopen(request: urllib.request.Request, *, timeout: float) -> Any:
      if request.full_url == "http://127.0.0.1:8080/":
        return _BlockingResponse(_accepted_response("ExecuteMethod"))
      return urlopen(request, timeout=timeout)

    async def run_command() -> None:
      result = await interface.send_command("ExecuteMethod", methodName="PLR_Test")
      assert isinstance(result, ET.Element)
      results.append(result)

    with mock.patch.object(urllib.request, "urlopen", accepted_urlopen):
      async with interface:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(run_command)
          with anyio.fail_after(1):
            while interface._pending is None:
              await anyio.lowlevel.checkpoint()
          callback = _response_event(interface._pending.request_id)
          response = await _post_callback(interface.bound_port, callback)
          assert response == SOAP_RESPONSE_ResponseEventResponse.encode("utf-8"), response

    assert len(results) == 1
    assert results[0].findtext(".//Int32") == "42"
