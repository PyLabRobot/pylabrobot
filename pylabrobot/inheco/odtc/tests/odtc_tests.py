"""Drive the ODTC through SOAP exchanges and asynchronous device callbacks."""

import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Optional
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.inheco.odtc import (
  ODTC,
  ODTCMethodSet,
  ODTCPreMethod,
  ODTCProtocol,
  ODTCProtocolStopped,
  ODTCStage,
  ODTCStep,
  compile_protocol,
  method_set_to_xml,
  parse_method_set,
)
from pylabrobot.inheco.soap import soap_body_payload, soap_encode
from pylabrobot.inheco.transport import sila as sila_module
from pylabrobot.inheco.transport.sila import SiLAError, required_text
from pylabrobot.io.http import HTTP, HTTPResponse
from pylabrobot.io.http_server import HTTPServer
from pylabrobot.testing.concurrency import AnyioTestBase

FIXTURES = Path(__file__).with_name("fixtures")


def profile(name: Optional[str] = None) -> ODTCProtocol:
  return ODTCProtocol(
    [ODTCStage([ODTCStep(95, 30), ODTCStep(55, 20), ODTCStep(72, 60)], 3)], name=name
  )


class _FakeHTTPServer(HasLifespan):
  """Software double for HTTPServer in unit tests."""

  def __init__(
    self, handler: Callable[[bytes], Awaitable[bytes]], host: str, port: int = 0
  ) -> None:
    super().__init__()
    self.handler = handler
    self.host = host
    self.port = port
    self._bound_port: Optional[int] = None

  @property
  def bound_port(self) -> int:
    if self._bound_port is None:
      raise RuntimeError("HTTP callback server is not active")
    return self._bound_port

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    self._bound_port = 8000
    stack.callback(self._clear)

  def _clear(self) -> None:
    self._bound_port = None


class MockODTC:
  """A test-only device speaking the manual's SOAP and MethodSet formats."""

  def __init__(self) -> None:
    self.callback: Callable[[bytes], Awaitable[bytes]] | None = None
    self.receiver_url: Optional[str] = None
    self.commands: list[str] = []
    self.executed: list[str] = []
    self.methods: dict[str, Any] = {}
    self.premethods: dict[str, ODTCPreMethod] = {}
    self.parameters: dict[str, Optional[str]] = {}
    self.state = "Idle"
    self.hold_name: Optional[str] = None
    self.running_id: Optional[int] = None
    self.started = anyio.Event()
    self.reject: dict[str, int] = {}
    self.emit_progress = False

  async def complete(
    self,
    request_id: Optional[int] = None,
    code: int = 3,
    data: str = "<ResponseData/>",
  ) -> None:
    request_id = self.running_id if request_id is None else request_id
    if request_id is None:
      raise AssertionError("No simulated command to complete")
    assert self.callback is not None
    await self.callback(
      soap_encode(
        "ResponseEvent",
        {
          "requestId": request_id,
          "returnValue": {"returnCode": code, "message": "simulated device response"},
          "responseData": data,
        },
        method_ns="http://sila.coop",
      ).encode()
    )
    self.state = "Idle"

  def parameter_data(self, name: str, value: str) -> str:
    root = ET.Element("ResponseData")
    parameters = ET.SubElement(root, "ParameterSet")
    parameter = ET.SubElement(parameters, "Parameter", name=name)
    ET.SubElement(parameter, "String").text = value
    return ET.tostring(root, encoding="unicode")

  async def handle(self, body: bytes) -> bytes:
    request = soap_body_payload(body.decode())
    name = request.tag.rsplit("}", 1)[-1]
    request_id = int(required_text(request, "requestId"))
    self.commands.append(name)
    code = self.reject.get(name, 1)
    extra: dict[str, Any] = {}
    if name in self.reject:
      pass
    elif name == "GetStatus":
      extra = {"state": self.state}
    elif name == "Reset":
      self.receiver_url = required_text(request, "eventReceiverURI")
      self.state = "Standby"
    elif name == "Initialize":
      self.state = "Idle"
    elif name == "SetParameters":
      parameters = ET.fromstring(required_text(request, "paramsXML"))
      for parameter in parameters:
        self.parameters[parameter.attrib["name"]] = parameter[0].text
      methods_xml = self.parameters["MethodsXML"]
      assert methods_xml is not None
      methods = parse_method_set(methods_xml)
      self.methods.update((m.name, m) for m in methods.methods)
      self.premethods.update((m.name, m) for m in methods.premethods)
    elif name == "GetParameters":
      code = 2
      xml = method_set_to_xml(
        ODTCMethodSet(list(self.methods.values()), list(self.premethods.values()))
      )
      await self.complete(request_id, data=self.parameter_data("MethodsXML", xml))
    elif name == "ReadActualTemperature":
      code = 2
      xml = (
        "<SensorValues><Mount>9500</Mount><Mount_Monitor>9501</Mount_Monitor>"
        "<Lid>10500</Lid><Lid_Monitor>10501</Lid_Monitor><Ambient>2500</Ambient>"
        "<PCB>3300</PCB><Heatsink>2600</Heatsink><Heatsink_TEC>2400</Heatsink_TEC></SensorValues>"
      )
      await self.complete(request_id, data=self.parameter_data("SensorValues", xml))
    elif name in ("OpenDoor", "CloseDoor"):
      code = 2
      await self.complete(request_id)
    elif name == "ExecuteMethod":
      code = 2
      method_name = required_text(request, "methodName")
      self.executed.append(method_name)
      self.running_id = request_id
      self.state = "Busy"
      if self.emit_progress:
        assert self.callback is not None
        await self.callback(
          soap_encode(
            "DataEvent",
            {
              "requestId": request_id,
              "dataValue": (FIXTURES / "firmware_manual_data_event.xml").read_text(),
            },
            method_ns="http://sila.coop",
          ).encode()
        )
      if method_name == self.hold_name:
        self.started.set()
      else:
        await self.complete(request_id)
    elif name == "StopMethod":
      self.state = "Idle"
    else:
      raise AssertionError(f"Unhandled test command {name}")
    return soap_encode(
      f"{name}Response",
      {
        f"{name}Result": {
          "returnCode": code,
          "message": "simulated device response",
          "duration": "PT10S",
        },
        **extra,
      },
      method_ns="http://sila.coop",
    ).encode()

  async def post(
    self, method: str, path: str, body: Optional[bytes], **kwargs: Any
  ) -> HTTPResponse:
    assert body is not None
    return HTTPResponse(200, await self.handle(body), {})


class TestODTC(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    stack.enter_context(mock.patch.object(sila_module, "HTTPServer", _FakeHTTPServer))
    self.odtc = ODTC("odtc", "127.0.0.1", client_ip="127.0.0.1")
    self.fake = MockODTC()
    self.fake.callback = self.odtc._sila._handle_event
    stack.enter_context(
      mock.patch.object(self.odtc._sila.io, "request_raw", side_effect=self.fake.post)
    )
    await stack.enter_async_context(self.odtc.lifespan())

  def test_inherits_managed_lifespan_methods(self) -> None:
    assert isinstance(self.odtc, HasLifespan)
    assert ODTC.setup is HasLifespan.setup
    assert ODTC.stop is HasLifespan.stop
    assert ODTC.__aenter__ is HasLifespan.__aenter__
    assert ODTC.__aexit__ is HasLifespan.__aexit__

  async def test_lifespan_entry_and_overlapping_entry_rejection(self) -> None:
    assert self.fake.commands == ["GetStatus", "Reset", "GetStatus", "Initialize", "GetStatus"]
    with pytest.raises(RuntimeError, match="already has an active lifespan"):
      async with self.odtc:
        pass
    assert len(self.fake.commands) == 5
    assert self.odtc.door_open is None

  async def test_run_prepares_matching_lid_and_retains_progress(self) -> None:
    self.fake.emit_progress = True
    await self.odtc.run_protocol(profile(), volume=25, lid_temperature=105)
    assert self.fake.executed == ["plr_currentPreMethod", "plr_currentProtocol"]
    pre = self.fake.premethods["plr_currentPreMethod"]
    assert (pre.temperature, pre.lid_temperature) == (95, 105)
    assert self.fake.parameters["MatchLidTemperatures"] == "true"
    progress = await self.odtc.request_progress()
    assert progress is not None
    assert progress.elapsed == 4.355
    assert self.odtc._prepared == (72, 105)

  async def test_read_while_running_then_complete(self) -> None:
    self.fake.hold_name = "plr_currentProtocol"
    done = anyio.Event()

    async def run() -> None:
      await self.odtc.run_protocol(profile(), volume=25, lid_temperature=105)
      done.set()

    async with anyio.create_task_group() as tg:
      tg.start_soon(run)
      with anyio.fail_after(1):
        await self.fake.started.wait()
      assert (await self.odtc.request_temperatures()).mount == 95
      assert not done.is_set()
      await self.fake.complete()
    assert done.is_set()

  async def test_stop_during_premethod_never_starts_main_method(self) -> None:
    self.fake.hold_name = "plr_currentPreMethod"
    errors: list[ODTCProtocolStopped] = []

    async def run() -> None:
      try:
        await self.odtc.run_protocol(profile(), volume=25, lid_temperature=105)
      except ODTCProtocolStopped as err:
        errors.append(err)

    async with anyio.create_task_group() as tg:
      tg.start_soon(run)
      with anyio.fail_after(1):
        await self.fake.started.wait()
      await self.odtc.stop_protocol()
    assert len(errors) == 1
    assert self.fake.executed == ["plr_currentPreMethod"]
    assert self.odtc._prepared is None

  async def test_late_waiter_sees_completed_device_error(self) -> None:
    self.fake.hold_name = "plr_currentProtocol"
    errors: list[SiLAError] = []

    async def run() -> None:
      try:
        await self.odtc.run_protocol(profile(), volume=25, lid_temperature=105)
      except SiLAError as err:
        errors.append(err)

    async with anyio.create_task_group() as tg:
      tg.start_soon(run)
      with anyio.fail_after(1):
        await self.fake.started.wait()
      await self.fake.complete(code=2002)
    assert len(errors) == 1
    with pytest.raises(SiLAError):
      await self.odtc.wait_for_completion()

  async def test_cancel_wait_leaves_device_command_running(self) -> None:
    self.fake.hold_name = "plr_currentProtocol"
    scopes: list[anyio.CancelScope] = []
    exited = anyio.Event()

    async def run() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await self.odtc.run_protocol(profile(), volume=25, lid_temperature=105)
        finally:
          exited.set()

    async with anyio.create_task_group() as tg:
      tg.start_soon(run)
      with anyio.fail_after(1):
        await self.fake.started.wait()
      scopes[0].cancel()
      await exited.wait()
    assert scopes[0].cancelled_caught
    assert self.odtc._execution is not None
    assert not self.odtc._execution.done
    await self.fake.complete()
    await self.odtc.wait_for_completion()
    assert self.odtc._prepared is None

  async def test_timeout_does_not_report_completion(self) -> None:
    self.fake.hold_name = "plr_currentProtocol"
    with pytest.raises(TimeoutError):
      await self.odtc.run_protocol(profile(), volume=25, lid_temperature=105, timeout=0.001)
    assert self.odtc._execution is not None
    assert not self.odtc._execution.done
    await self.fake.complete()
    await self.odtc.wait_for_completion()

  async def test_stored_protocol_preserves_settings_and_requires_overwrite(self) -> None:
    method = compile_protocol(profile("stored"), variant=96, volume=75, lid_temperature=100)
    await self.odtc.upload_protocol(method)
    assert await self.odtc.request_protocol("stored") == method
    with pytest.raises(ValueError, match="overwrite"):
      await self.odtc.upload_protocol(method)
    await self.odtc.run_stored_protocol("stored")
    assert self.fake.premethods["plr_currentPreMethod"].lid_temperature == 100
    assert self.fake.methods["stored"].fluid_quantity == 2

  async def test_repeated_door_commands_and_failed_move(self) -> None:
    await self.odtc.open_door()
    await self.odtc.open_door()
    assert self.fake.commands.count("OpenDoor") == 1
    self.fake.reject["CloseDoor"] = 2002
    with pytest.raises(SiLAError):
      await self.odtc.close_door()
    assert self.odtc.door_open is None

  async def test_device_status_fault_reaches_pending_execution(self) -> None:
    self.fake.hold_name = "plr_currentProtocol"
    errors: list[SiLAError] = []

    async def run() -> None:
      try:
        await self.odtc.run_protocol(profile(), volume=25, lid_temperature=105)
      except SiLAError as err:
        errors.append(err)

    async with anyio.create_task_group() as tg:
      tg.start_soon(run)
      with anyio.fail_after(1):
        await self.fake.started.wait()
      await self.odtc._sila._handle_event(
        soap_encode(
          "StatusEvent",
          {
            "eventDescription": "<DeviceState>InError</DeviceState>",
          },
          method_ns="http://sila.coop",
        ).encode()
      )
    assert len(errors) == 1
    assert self.odtc.door_open is None

  async def test_invalid_timeout_is_rejected_before_upload_or_execution(self) -> None:
    commands = list(self.fake.commands)
    for timeout in (0, -1, float("nan"), float("inf")):
      with pytest.raises(ValueError):
        await self.odtc.run_protocol(profile(), volume=25, lid_temperature=105, timeout=timeout)
    assert self.fake.commands == commands


class TestODTCLifecycleTransitions(AnyioTestBase):
  def _build_odtc(self) -> tuple[ODTC, MockODTC]:
    odtc = ODTC("odtc", "127.0.0.1", client_ip="127.0.0.1")
    fake = MockODTC()
    fake.callback = odtc._sila._handle_event
    return odtc, fake

  async def test_reentry_invalidates_tracked_state(self) -> None:
    odtc, fake = self._build_odtc()
    with (
      mock.patch.object(sila_module, "HTTPServer", _FakeHTTPServer),
      mock.patch.object(odtc._sila.io, "request_raw", side_effect=fake.post),
    ):
      async with odtc:
        await odtc.open_door()
        assert odtc.door_open is True
      async with odtc:
        assert odtc.door_open is None
        assert odtc._prepared is None
      with pytest.raises(RuntimeError, match="not active"):
        await odtc.request_progress()
      with pytest.raises(RuntimeError, match="not active"):
        await odtc.wait_for_completion()

  async def test_lifespan_entry_does_not_reset_busy_instrument(self) -> None:
    odtc, fake = self._build_odtc()
    fake.state = "Busy"
    with (
      mock.patch.object(sila_module, "HTTPServer", _FakeHTTPServer),
      mock.patch.object(odtc._sila.io, "request_raw", side_effect=fake.post),
    ):
      with pytest.raises(RuntimeError, match="will not reset"):
        async with odtc:
          pass
      assert fake.commands.count("Reset") == 0
      assert odtc._sila._server is None
      assert not odtc.is_active

  async def test_lifespan_exit_closes_transport_even_if_device_refuses_stop(self) -> None:
    odtc, fake = self._build_odtc()
    with (
      mock.patch.object(sila_module, "HTTPServer", _FakeHTTPServer),
      mock.patch.object(odtc._sila.io, "request_raw", side_effect=fake.post),
    ):
      with pytest.raises(SiLAError):
        async with odtc:
          fake.reject["StopMethod"] = 2000
      assert odtc._sila._server is None
      assert not odtc._connected
      assert not odtc.is_active

  async def test_failed_initialization_cleans_up_and_can_retry(self) -> None:
    odtc, fake = self._build_odtc()
    fake.reject["Initialize"] = 2000
    with (
      mock.patch.object(sila_module, "HTTPServer", _FakeHTTPServer),
      mock.patch.object(odtc._sila.io, "request_raw", side_effect=fake.post),
    ):
      with pytest.raises(SiLAError):
        async with odtc:
          pass
      assert odtc._sila._server is None
      assert not odtc._connected
      fake.reject.clear()
      async with odtc:
        assert odtc._connected


class TestODTCLoopback(AnyioTestBase):
  async def test_full_stack_with_real_http_and_callback_sockets(self) -> None:
    fake = MockODTC()
    server = HTTPServer(fake.handle, "127.0.0.1")
    async with AsyncExitStackWithShielding() as stack:
      await stack.enter_async_context(server.lifespan())
      odtc = ODTC(
        "loopback", "127.0.0.1", variant=384, client_ip="127.0.0.1", odtc_port=server.bound_port
      )
      callback_client: Optional[HTTP] = None

      async def callback(body: bytes) -> bytes:
        nonlocal callback_client
        if callback_client is None:
          assert fake.receiver_url is not None
          callback_client = HTTP("simulated ODTC callback", fake.receiver_url)
          await stack.enter_async_context(callback_client.lifespan())
        result = await callback_client.request_raw("POST", "/", body)
        assert result.status == 200
        return result.body

      fake.callback = callback
      fake.emit_progress = True
      async with odtc:
        await odtc.open_door()
        await odtc.close_door()
        await odtc.run_protocol(profile(), volume=25, lid_temperature=115, plate_type=2)
        assert (await odtc.request_temperatures()).lid == 105
        assert fake.methods["plr_currentProtocol"].variant == 384
        progress = await odtc.request_progress()
        assert progress is not None
        assert progress.current_temperature == 16.56

  async def test_outer_cancellation_completes_async_stop_method_callback_cleanly(self) -> None:
    stop_called = False

    async def handle_machine(body: bytes) -> bytes:
      nonlocal stop_called
      req = soap_body_payload(body.decode())
      name = req.tag.rsplit("}", 1)[-1]
      req_id = int(required_text(req, "requestId"))
      if name == "GetStatus":
        return soap_encode(
          "GetStatusResponse",
          {
            "GetStatusResult": {"returnCode": 1, "message": "ok", "duration": "PT1S"},
            "state": "Idle",
          },
          method_ns="http://sila.coop",
        ).encode()
      if name == "Reset":
        handle_machine.receiver_uri = required_text(req, "eventReceiverURI")  # type: ignore[attr-defined]
        return soap_encode(
          "ResetResponse",
          {"ResetResult": {"returnCode": 1, "message": "ok", "duration": "PT1S"}},
          method_ns="http://sila.coop",
        ).encode()
      if name == "StopMethod":
        stop_called = True
        client = HTTP("async stop callback", handle_machine.receiver_uri)  # type: ignore[attr-defined]
        async with client:
          ack = await client.request_raw(
            "POST",
            "/",
            soap_encode(
              "ResponseEvent",
              {
                "requestId": req_id,
                "returnValue": {"returnCode": 3, "message": "stopped"},
                "responseData": "<ResponseData/>",
              },
              method_ns="http://sila.coop",
            ).encode(),
          )
          assert ack.status == 200
        return soap_encode(
          "StopMethodResponse",
          {"StopMethodResult": {"returnCode": 2, "message": "accepted", "duration": "PT5S"}},
          method_ns="http://sila.coop",
        ).encode()
      raise AssertionError(f"Unexpected command: {name}")

    server = HTTPServer(handle_machine, "127.0.0.1")
    with anyio.fail_after(5):
      async with server:
        odtc = ODTC(
          "loopback-cancel",
          "127.0.0.1",
          client_ip="127.0.0.1",
          odtc_port=server.bound_port,
        )
        with anyio.CancelScope() as outer:
          async with odtc:
            outer.cancel()
            await anyio.sleep_forever()
        assert outer.cancelled_caught
    assert stop_called
