"""SiLA acceptance, callback, fault, timeout, and lifespan behavior at the HTTP boundary."""

import inspect
import logging
import threading
import xml.etree.ElementTree as ET
from typing import Any, Callable, Mapping, Optional
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.inheco.soap import soap_body_payload, soap_encode
from pylabrobot.inheco.transport import sila as sila_module
from pylabrobot.inheco.transport.sila import (
  InhecoSiLAInterface,
  SiLAError,
  get_params,
  required_text,
)
from pylabrobot.io.http import HTTPResponse
from pylabrobot.io.http_server import HTTPServer
from pylabrobot.testing.concurrency import AnyioTestBase


def _response(
  command: str,
  code: int = 2,
  message: str = "Success",
  duration: Optional[str] = "PT10S",
) -> HTTPResponse:
  result: dict[str, Any] = {"returnCode": code, "message": message}
  if duration is not None:
    result["duration"] = duration
  return HTTPResponse(
    200,
    soap_encode(
      f"{command}Response",
      {f"{command}Result": result},
      method_ns="http://sila.coop",
    ).encode(),
    {},
  )


def _event(
  request_id: int,
  code: int = 3,
  name: str = "ResponseEvent",
  data: str = "<ResponseData/>",
) -> bytes:
  return soap_encode(
    name,
    {
      "requestId": request_id,
      "returnValue": {"returnCode": code, "message": "device message"},
      "responseData": data,
    },
    method_ns="http://sila.coop",
  ).encode()


class _FakeHTTPServer(HasLifespan):
  """Software double for HTTPServer within InhecoSiLAInterface unit tests."""

  def __init__(
    self,
    handler: Callable[[bytes], Any],
    host: str,
    port: int = 0,
    *,
    entry_error: Optional[BaseException] = None,
  ) -> None:
    super().__init__()
    self.handler = handler
    self.host = host
    self.port = port
    self._entry_error = entry_error
    self._bound_port: Optional[int] = None

  @property
  def bound_port(self) -> int:
    if self._bound_port is None:
      raise RuntimeError("HTTP callback server is not active")
    return self._bound_port

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    if self._entry_error is not None:
      raise self._entry_error
    self._bound_port = 8000
    stack.callback(self._clear)

  def _clear(self) -> None:
    self._bound_port = None


class TestInhecoSiLAInterface(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    stack.enter_context(mock.patch.object(sila_module, "HTTPServer", _FakeHTTPServer))
    self.session = InhecoSiLAInterface("127.0.0.1", client_ip="127.0.0.1")
    self.requests: list[tuple[str, str, Optional[bytes], Optional[Mapping[str, str]]]] = []
    self.next_response: Callable[..., Any] = lambda *args, **kw: _response("GetStatus", 1)

    async def fake_request_raw(
      method: str,
      path: str,
      body: Optional[bytes] = None,
      *,
      headers: Optional[Mapping[str, str]] = None,
      allow_redirects: bool = True,
    ) -> HTTPResponse:
      self.requests.append((method, path, body, headers))
      res = self.next_response(method, path, body, headers=headers, allow_redirects=allow_redirects)
      if inspect.isawaitable(res):
        awaited: HTTPResponse = await res
        return awaited
      direct: HTTPResponse = res
      return direct

    stack.enter_context(
      mock.patch.object(self.session.io, "request_raw", side_effect=fake_request_raw)
    )
    await stack.enter_async_context(self.session.lifespan())

  def test_uses_has_lifespan_managed_methods(self) -> None:
    assert isinstance(self.session, HasLifespan)
    assert InhecoSiLAInterface.__aenter__ is HasLifespan.__aenter__
    assert InhecoSiLAInterface.__aexit__ is HasLifespan.__aexit__
    assert InhecoSiLAInterface.setup is HasLifespan.setup
    assert InhecoSiLAInterface.stop is HasLifespan.stop

  async def test_callback_before_acceptance_is_retained(self) -> None:
    async def post(method: str, path: str, body: Optional[bytes], **kwargs: Any) -> HTTPResponse:
      assert body is not None
      request = soap_body_payload(body.decode())
      request_id = int(required_text(request, "requestId"))
      await self.session._handle_event(_event(request_id))
      return _response("OpenDoor")

    self.next_response = post
    pending = await self.session.start_command("OpenDoor")
    result = await pending.wait()
    assert isinstance(result, ET.Element)
    assert result.tag == "ResponseData"
    assert pending.duration == 10
    assert self.session._pending == {}

  async def test_error_is_still_raised_by_later_waiter(self) -> None:
    self.next_response = lambda *args, **kw: _response("ExecuteMethod")
    pending = await self.session.start_command("ExecuteMethod", methodName="PCR")
    await self.session._handle_event(_event(pending.request_id, 2002))
    await anyio.sleep(0)
    for _ in range(2):
      with pytest.raises(SiLAError) as error_info:
        await pending.wait()
      assert error_info.value.code == 2002

  async def test_timeout_and_cancellation_do_not_cancel_command(self) -> None:
    self.next_response = lambda *args, **kw: _response("ExecuteMethod")
    pending = await self.session.start_command("ExecuteMethod")
    with pytest.raises(TimeoutError):
      await pending.wait(0.001)
    assert not pending.done

    entered = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def wait_and_cancel() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        entered.set()
        await pending.wait()

    async with anyio.create_task_group() as tg:
      tg.start_soon(wait_and_cancel)
      await entered.wait()
      await anyio.sleep(0)
      scopes[0].cancel()

    assert scopes[0].cancelled_caught
    assert not pending.done
    await self.session._handle_event(_event(pending.request_id))
    assert isinstance(await pending.wait(), ET.Element)

  async def test_concurrent_reads_do_not_wait_for_method_completion_and_exit_fails_pending(
    self,
  ) -> None:
    with mock.patch.object(sila_module, "HTTPServer", _FakeHTTPServer):
      session = InhecoSiLAInterface("127.0.0.1", client_ip="127.0.0.1")
      responses = [_response("ExecuteMethod"), _response("GetStatus", 1)]

      async def fake_post(*args: Any, **kw: Any) -> HTTPResponse:
        return responses.pop(0)

      with mock.patch.object(session.io, "request_raw", side_effect=fake_post):
        async with session:
          pending = await session.start_command("ExecuteMethod")
          await session.send_command("GetStatus")
          assert not pending.done
      with pytest.raises(ConnectionError, match="SiLA connection closed"):
        await pending.wait()

  async def test_busy_rejection_retains_firmware_error(self) -> None:
    self.next_response = lambda *args, **kw: _response("OpenDoor", 4, "Busy with ExecuteMethod")
    with pytest.raises(SiLAError, match="Busy with ExecuteMethod"):
      await self.session.send_command("OpenDoor")
    assert self.session._pending == {}

  async def test_finished_with_warning_logs_and_succeeds(
    self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
  ) -> None:
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    self.next_response = lambda *args, **kw: _response("OpenDoor")
    pending = await self.session.start_command("OpenDoor")
    with caplog.at_level(logging.WARNING, logger="pylabrobot.inheco.transport.sila"):
      await self.session._handle_event(_event(pending.request_id, 12))
    await pending.wait()
    assert any("completed with warning" in record.message for record in caplog.records)

  async def test_response_code_one_and_empty_payload(self) -> None:
    self.next_response = lambda *args, **kw: _response("OpenDoor")
    pending = await self.session.start_command("OpenDoor")
    await self.session._handle_event(_event(pending.request_id, 1, data=""))
    assert await pending.wait() is None

  async def test_error_event_fails_corresponding_command(self) -> None:
    self.next_response = lambda *args, **kw: _response("ExecuteMethod")
    pending = await self.session.start_command("ExecuteMethod")
    await self.session._handle_event(_event(pending.request_id, 2001, name="ErrorEvent"))
    with pytest.raises(SiLAError) as error_info:
      await pending.wait()
    assert error_info.value.code == 2001

  async def test_malformed_callback_fails_waiter(self) -> None:
    self.next_response = lambda *args, **kw: _response("ReadActualTemperature")
    pending = await self.session.start_command("ReadActualTemperature")
    with pytest.raises(ET.ParseError):
      await self.session._handle_event(_event(pending.request_id, data="<broken"))
    with pytest.raises(ET.ParseError):
      await pending.wait()

  async def test_sequential_reentry_and_bind_failure_cleanup(self) -> None:
    bind_error = OSError("bind failed")
    with mock.patch.object(
      sila_module,
      "HTTPServer",
      lambda handler, host, port=0: _FakeHTTPServer(handler, host, port, entry_error=bind_error),
    ):
      failing = InhecoSiLAInterface("127.0.0.1", client_ip="127.0.0.1")
      with pytest.raises(OSError, match="bind failed"):
        async with failing:
          pass
      assert not failing.is_active
      assert not failing.io.is_active
      with pytest.raises(RuntimeError, match="not active"):
        await failing.send_command("GetStatus")

    with mock.patch.object(sila_module, "HTTPServer", _FakeHTTPServer):
      reusable = InhecoSiLAInterface("127.0.0.1", client_ip="127.0.0.1")
      with mock.patch.object(
        reusable.io,
        "request_raw",
        side_effect=lambda *args, **kw: _response("GetStatus", 1),
      ):
        async with reusable:
          await reusable.send_command("GetStatus")
        async with reusable:
          await reusable.send_command("GetStatus")

  async def test_empty_success_message_is_valid_xml_string(self) -> None:
    self.next_response = lambda *args, **kw: _response("GetStatus", 1, message="")
    await self.session.send_command("GetStatus")

  async def test_invalid_duration_raises_and_mismatched_callback_leaves_pending_intact(
    self,
  ) -> None:
    self.next_response = lambda *args, **kw: _response("OpenDoor", 2, duration="NOT_ISO8601")
    with pytest.raises(ValueError, match="Invalid SiLA duration"):
      await self.session.start_command("OpenDoor")
    assert self.session._pending == {}

    self.next_response = lambda *args, **kw: _response("OpenDoor", 2, duration="PT80S")
    pending = await self.session.start_command("OpenDoor")
    assert pending.duration == 80.0
    await self.session._handle_event(_event(pending.request_id + 999))
    assert not pending.done
    await self.session._handle_event(_event(pending.request_id))
    assert pending.done

  async def test_send_command_reconciles_accepted_callback_under_cancellation(self) -> None:
    accepted = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    request_ids: list[int] = []

    async def post(method: str, path: str, body: Optional[bytes], **kwargs: Any) -> HTTPResponse:
      assert body is not None
      request = soap_body_payload(body.decode())
      request_ids.append(int(required_text(request, "requestId")))
      accepted.set()
      return _response("OpenDoor", 2, duration="PT5S")

    self.next_response = post
    reconciled = anyio.Event()

    async def run() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await self.session.send_command("OpenDoor")
      reconciled.set()

    async with anyio.create_task_group() as tg:
      tg.start_soon(run)
      await accepted.wait()
      await anyio.sleep(0)
      scopes[0].cancel()
      await anyio.sleep(0.01)
      assert not reconciled.is_set()
      await self.session._handle_event(_event(request_ids[0]))
      await reconciled.wait()
    assert scopes[0].cancelled_caught
    assert self.session._pending == {}

  async def test_send_command_cancelled_without_callback_reports_timeout(self) -> None:
    accepted = anyio.Event()
    scope = anyio.CancelScope()

    async def post(method: str, path: str, body: Optional[bytes], **kwargs: Any) -> HTTPResponse:
      accepted.set()
      return _response("OpenDoor", 2, duration="PT1S")

    self.next_response = post

    async def cancel_after_accept() -> None:
      await accepted.wait()
      await anyio.sleep(0)
      scope.cancel()

    async with anyio.create_task_group() as tg:
      tg.start_soon(cancel_after_accept)
      with pytest.raises(TimeoutError):
        with scope:
          await self.session.send_command("OpenDoor", timeout=0.02)

  async def test_cancel_during_post_retains_accepted_command_and_reconciles_callback(self) -> None:
    with mock.patch.object(sila_module, "HTTPServer", _FakeHTTPServer):
      session = InhecoSiLAInterface("127.0.0.1", client_ip="127.0.0.1")
      post_started = threading.Event()
      allow_return = threading.Event()
      request_ids: list[int] = []

      def blocking_raw_request(
        method: str,
        path: str,
        body: Optional[bytes],
        headers: Mapping[str, str],
        allow_redirects: bool,
      ) -> HTTPResponse:
        del method, path, headers, allow_redirects
        assert body is not None
        request = soap_body_payload(body.decode())
        request_ids.append(int(required_text(request, "requestId")))
        post_started.set()
        assert allow_return.wait(timeout=5.0)
        return _response("ExecuteMethod", 2, duration="PT10S")

      with mock.patch.object(session.io, "_make_raw_request", side_effect=blocking_raw_request):
        async with session:
          start_scope = anyio.CancelScope()
          pending_holder: list[Any] = []

          async def run_start() -> None:
            with start_scope:
              pending_holder.append(await session.start_command("ExecuteMethod", methodName="PCR"))

          with anyio.fail_after(5.0):
            async with anyio.create_task_group() as tg:
              tg.start_soon(run_start)
              while not post_started.is_set():
                await anyio.sleep(0.001)
              start_scope.cancel()
              allow_return.set()

          assert len(pending_holder) == 1
          pending = pending_holder[0]
          assert pending.request_id in session._pending
          await session._handle_event(_event(pending.request_id))
          assert isinstance(await pending.wait(), ET.Element)
          assert session._pending == {}

          post_started.clear()
          allow_return.clear()
          request_ids.clear()
          send_scope = anyio.CancelScope()
          reconciled = anyio.Event()

          async def run_send() -> None:
            with send_scope:
              await session.send_command("ExecuteMethod", methodName="PCR")
            reconciled.set()

          with anyio.fail_after(5.0):
            async with anyio.create_task_group() as tg:
              tg.start_soon(run_send)
              while not post_started.is_set():
                await anyio.sleep(0.001)
              send_scope.cancel()
              allow_return.set()
              while request_ids[0] not in session._pending:
                await anyio.sleep(0.001)
              await anyio.sleep(0.01)
              assert not reconciled.is_set()
              await session._handle_event(_event(request_ids[0]))
              await reconciled.wait()

          assert send_scope.cancelled_caught
          assert session._pending == {}

  def test_get_params_handles_empty_string_and_trimmed_scalars(self) -> None:
    xml = ET.fromstring(
      "<ResponseData>"
      "<Parameter name='EmptyStr'><String/></Parameter>"
      "<Parameter name='Temp'><Float64> 37.5 </Float64></Parameter>"
      "<Parameter name='Count'><Int32> 12 </Int32></Parameter>"
      "<Parameter name='Enabled'><Boolean> true </Boolean></Parameter>"
      "</ResponseData>"
    )
    assert get_params(xml, ["EmptyStr", "Temp", "Count", "Enabled"]) == {
      "EmptyStr": "",
      "Temp": 37.5,
      "Count": 12,
      "Enabled": True,
    }


class TestInhecoSiLAInterfaceLoopback(AnyioTestBase):
  async def test_loopback_http_and_callback_server_work_on_current_backend(self) -> None:
    assert sila_module.HTTPServer is HTTPServer
    interface = InhecoSiLAInterface("127.0.0.1", client_ip="127.0.0.1")

    async def fake_machine_post(
      method: str, path: str, body: Optional[bytes], **kwargs: Any
    ) -> HTTPResponse:
      assert body is not None
      request = soap_body_payload(body.decode())
      request_id = int(required_text(request, "requestId"))
      callback_client = sila_module.HTTP("callback sender", interface.event_receiver_uri)
      async with callback_client:
        ack = await callback_client.request_raw(
          "POST",
          "/",
          _event(
            request_id,
            data='<Parameters><Parameter name="value"><Int32>42</Int32></Parameter></Parameters>',
          ),
        )
        assert ack.status == 200
      return _response("ExecuteMethod")

    with mock.patch.object(interface.io, "request_raw", side_effect=fake_machine_post):
      async with interface:
        result = await interface.send_command("ExecuteMethod", methodName="PCR")
        assert isinstance(result, ET.Element)
        assert result.findtext(".//Int32") == "42"
