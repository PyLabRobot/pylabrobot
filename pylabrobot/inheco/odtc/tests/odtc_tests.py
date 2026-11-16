"""Drive the ODTC through SOAP exchanges and asynchronous device callbacks."""

import asyncio
import unittest
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pylabrobot.inheco.odtc import (
  ODTC,
  ODTCMethodSet,
  ODTCProtocol,
  ODTCProtocolStopped,
  ODTCStage,
  ODTCStep,
  compile_protocol,
  method_set_to_xml,
  parse_method_set,
)
from pylabrobot.inheco.soap import soap_body_payload, soap_encode
from pylabrobot.inheco.transport.sila import SiLAError, required_text
from pylabrobot.io.http import HTTP, HTTPResponse
from pylabrobot.io.http_server import HTTPServer

FIXTURES = Path(__file__).with_name("fixtures")


def profile(name=None):
  return ODTCProtocol(
    [ODTCStage([ODTCStep(95, 30), ODTCStep(55, 20), ODTCStep(72, 60)], 3)], name=name
  )


class MockODTC:
  """A test-only device speaking the manual's SOAP and MethodSet formats."""

  def __init__(self):
    self.callback: Callable[[bytes], Awaitable[bytes]] | None = None
    self.receiver_url = None
    self.commands = []
    self.executed = []
    self.methods = {}
    self.premethods = {}
    self.parameters = {}
    self.state = "Idle"
    self.hold_name = None
    self.running_id = None
    self.started = asyncio.Event()
    self.reject = {}
    self.emit_progress = False

  async def complete(self, request_id=None, code=3, data="<ResponseData/>"):
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

  def parameter_data(self, name, value):
    root = ET.Element("ResponseData")
    parameters = ET.SubElement(root, "ParameterSet")
    parameter = ET.SubElement(parameters, "Parameter", name=name)
    ET.SubElement(parameter, "String").text = value
    return ET.tostring(root, encoding="unicode")

  async def handle(self, body):
    request = soap_body_payload(body.decode())
    name = request.tag.rsplit("}", 1)[-1]
    request_id = int(required_text(request, "requestId"))
    self.commands.append(name)
    code = self.reject.get(name, 1)
    extra = {}
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
      methods = parse_method_set(self.parameters["MethodsXML"])
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

  async def post(self, method, path, body, **kwargs):
    return HTTPResponse(200, await self.handle(body), {})


class ODTCTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.server_patch = patch("pylabrobot.inheco.transport.sila.HTTPServer")
    server = self.server_patch.start().return_value
    server.setup = AsyncMock()
    server.stop = AsyncMock()
    server.bound_port = 8000
    self.odtc = ODTC("odtc", "127.0.0.1", client_ip="127.0.0.1")
    self.fake = MockODTC()
    self.fake.callback = self.odtc._sila._handle_event
    self.post_patch = patch.object(self.odtc._sila.io, "request_raw", side_effect=self.fake.post)
    self.post_patch.start()
    await self.odtc.setup()

  async def asyncTearDown(self):
    self.fake.reject.clear()
    await self.odtc.stop()
    self.post_patch.stop()
    self.server_patch.stop()

  async def test_setup_lifecycle_and_repeated_setup(self):
    self.assertEqual(
      self.fake.commands, ["GetStatus", "Reset", "GetStatus", "Initialize", "GetStatus"]
    )
    await self.odtc.setup()
    self.assertEqual(len(self.fake.commands), 5)
    self.assertIsNone(self.odtc.door_open)

  async def test_run_prepares_matching_lid_and_retains_progress(self):
    self.fake.emit_progress = True
    await self.odtc.run_protocol(profile(), volume=25, lid_temperature=105)
    self.assertEqual(self.fake.executed, ["plr_currentPreMethod", "plr_currentProtocol"])
    pre = self.fake.premethods["plr_currentPreMethod"]
    self.assertEqual((pre.temperature, pre.lid_temperature), (95, 105))
    self.assertEqual(self.fake.parameters["MatchLidTemperatures"], "true")
    progress = await self.odtc.request_progress()
    assert progress is not None
    self.assertEqual(progress.elapsed, 4.355)
    self.assertEqual(self.odtc._prepared, (72, 105))

  async def test_read_while_running_then_complete(self):
    self.fake.hold_name = "plr_currentProtocol"
    task = asyncio.create_task(self.odtc.run_protocol(profile(), volume=25, lid_temperature=105))
    await asyncio.wait_for(self.fake.started.wait(), 1)
    self.assertEqual((await self.odtc.request_temperatures()).mount, 95)
    self.assertFalse(task.done())
    await self.fake.complete()
    await task

  async def test_stop_during_premethod_never_starts_main_method(self):
    self.fake.hold_name = "plr_currentPreMethod"
    task = asyncio.create_task(self.odtc.run_protocol(profile(), volume=25, lid_temperature=105))
    await asyncio.wait_for(self.fake.started.wait(), 1)
    await self.odtc.stop_protocol()
    with self.assertRaises(ODTCProtocolStopped):
      await task
    self.assertEqual(self.fake.executed, ["plr_currentPreMethod"])
    self.assertIsNone(self.odtc._prepared)

  async def test_late_waiter_sees_completed_device_error(self):
    self.fake.hold_name = "plr_currentProtocol"
    task = asyncio.create_task(self.odtc.run_protocol(profile(), volume=25, lid_temperature=105))
    await asyncio.wait_for(self.fake.started.wait(), 1)
    await self.fake.complete(code=2002)
    with self.assertRaises(SiLAError):
      await task
    with self.assertRaises(SiLAError):
      await self.odtc.wait_for_completion()

  async def test_cancel_wait_leaves_device_command_running(self):
    self.fake.hold_name = "plr_currentProtocol"
    task = asyncio.create_task(self.odtc.run_protocol(profile(), volume=25, lid_temperature=105))
    await asyncio.wait_for(self.fake.started.wait(), 1)
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    assert self.odtc._execution is not None
    self.assertFalse(self.odtc._execution.future.done())
    await self.fake.complete()
    await self.odtc.wait_for_completion()
    self.assertIsNone(self.odtc._prepared)

  async def test_timeout_does_not_report_completion(self):
    self.fake.hold_name = "plr_currentProtocol"
    with self.assertRaises(asyncio.TimeoutError):
      await self.odtc.run_protocol(profile(), volume=25, lid_temperature=105, timeout=0.001)
    assert self.odtc._execution is not None
    self.assertFalse(self.odtc._execution.future.done())
    await self.fake.complete()
    await self.odtc.wait_for_completion()

  async def test_stored_protocol_preserves_settings_and_requires_overwrite(self):
    method = compile_protocol(profile("stored"), variant=96, volume=75, lid_temperature=100)
    await self.odtc.upload_protocol(method)
    self.assertEqual(await self.odtc.request_protocol("stored"), method)
    with self.assertRaisesRegex(ValueError, "overwrite"):
      await self.odtc.upload_protocol(method)
    await self.odtc.run_stored_protocol("stored")
    self.assertEqual(self.fake.premethods["plr_currentPreMethod"].lid_temperature, 100)
    self.assertEqual(self.fake.methods["stored"].fluid_quantity, 2)

  async def test_repeated_door_commands_and_failed_move(self):
    await self.odtc.open_door()
    await self.odtc.open_door()
    self.assertEqual(self.fake.commands.count("OpenDoor"), 1)
    self.fake.reject["CloseDoor"] = 2002
    with self.assertRaises(SiLAError):
      await self.odtc.close_door()
    self.assertIsNone(self.odtc.door_open)

  async def test_reconnect_invalidates_tracked_state(self):
    await self.odtc.open_door()
    await self.odtc.stop()
    await self.odtc.setup()
    self.assertIsNone(self.odtc.door_open)
    self.assertIsNone(self.odtc._prepared)

  async def test_setup_does_not_reset_busy_instrument(self):
    await self.odtc.stop()
    self.fake.state = "Busy"
    count = self.fake.commands.count("Reset")
    with self.assertRaisesRegex(RuntimeError, "will not reset"):
      await self.odtc.setup()
    self.assertEqual(self.fake.commands.count("Reset"), count)
    self.assertIsNone(self.odtc._sila._server)

  async def test_stop_closes_transport_even_if_device_refuses(self):
    self.fake.reject["StopMethod"] = 2000
    with self.assertRaises(SiLAError):
      await self.odtc.stop()
    self.assertIsNone(self.odtc._sila._server)
    self.assertFalse(self.odtc._connected)

  async def test_device_status_fault_reaches_pending_execution(self):
    self.fake.hold_name = "plr_currentProtocol"
    task = asyncio.create_task(self.odtc.run_protocol(profile(), volume=25, lid_temperature=105))
    await asyncio.wait_for(self.fake.started.wait(), 1)
    await self.odtc._sila._handle_event(
      soap_encode(
        "StatusEvent",
        {
          "eventDescription": "<DeviceState>InError</DeviceState>",
        },
        method_ns="http://sila.coop",
      ).encode()
    )
    with self.assertRaises(SiLAError):
      await task
    self.assertIsNone(self.odtc.door_open)

  async def test_invalid_timeout_is_rejected_before_upload_or_execution(self):
    commands = list(self.fake.commands)
    for timeout in (0, -1, float("nan"), float("inf")):
      with self.subTest(timeout=timeout), self.assertRaises(ValueError):
        await self.odtc.run_protocol(profile(), volume=25, lid_temperature=105, timeout=timeout)
    self.assertEqual(self.fake.commands, commands)

  async def test_failed_initialization_cleans_up_and_can_retry(self):
    await self.odtc.stop()
    self.fake.reject["Initialize"] = 2000
    with self.assertRaises(SiLAError):
      await self.odtc.setup()
    self.assertIsNone(self.odtc._sila._server)
    self.fake.reject.clear()
    await self.odtc.setup()
    self.assertTrue(self.odtc._connected)


class ODTCLoopbackTests(unittest.IsolatedAsyncioTestCase):
  async def test_full_stack_with_real_http_and_callback_sockets(self):
    fake = MockODTC()
    server = HTTPServer(fake.handle, "127.0.0.1")
    await server.setup()
    odtc = ODTC(
      "loopback", "127.0.0.1", variant=384, client_ip="127.0.0.1", odtc_port=server.bound_port
    )
    callback_client = None

    async def callback(body):
      nonlocal callback_client
      if callback_client is None:
        assert fake.receiver_url is not None
        callback_client = HTTP("simulated ODTC callback", fake.receiver_url)
        await callback_client.setup()
      result = await callback_client.request_raw("POST", "/", body)
      self.assertEqual(result.status, 200)
      return result.body

    fake.callback = callback
    fake.emit_progress = True
    try:
      await odtc.setup()
      await odtc.open_door()
      await odtc.close_door()
      await odtc.run_protocol(profile(), volume=25, lid_temperature=115, plate_type=2)
      self.assertEqual((await odtc.request_temperatures()).lid, 105)
      self.assertEqual(fake.methods["plr_currentProtocol"].variant, 384)
      progress = await odtc.request_progress()
      assert progress is not None
      self.assertEqual(progress.current_temperature, 16.56)
    finally:
      await odtc.stop()
      if callback_client is not None:
        await callback_client.stop()
      await server.stop()
