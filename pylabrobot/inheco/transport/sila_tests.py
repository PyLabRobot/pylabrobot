"""SiLA acceptance, callback, fault, and timeout behavior at the HTTP boundary."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.inheco.soap import soap_body_payload, soap_encode
from pylabrobot.inheco.transport.sila import InhecoSiLAInterface, SiLAError, required_text
from pylabrobot.io.http import HTTPResponse


def response(command, code=2, message="Success", duration="PT10S"):
  return HTTPResponse(
    200,
    soap_encode(
      f"{command}Response",
      {f"{command}Result": {"returnCode": code, "message": message, "duration": duration}},
      method_ns="http://sila.coop",
    ).encode(),
    {},
  )


def event(request_id, code=3, name="ResponseEvent", data="<ResponseData/>"):
  return soap_encode(
    name,
    {
      "requestId": request_id,
      "returnValue": {"returnCode": code, "message": "device message"},
      "responseData": data,
    },
    method_ns="http://sila.coop",
  ).encode()


class SiLATests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.server_patch = patch("pylabrobot.inheco.transport.sila.HTTPServer")
    server = self.server_patch.start().return_value
    server.setup = AsyncMock()
    server.stop = AsyncMock()
    server.bound_port = 8000
    self.session = InhecoSiLAInterface("127.0.0.1", client_ip="127.0.0.1")
    await self.session.setup()
    self.post_patch = patch.object(self.session.io, "request_raw", new_callable=AsyncMock)
    self.post = self.post_patch.start()

  async def asyncTearDown(self):
    await self.session.close()
    self.post_patch.stop()
    self.server_patch.stop()

  async def test_callback_before_acceptance_is_retained(self):
    async def post(method, path, body, **kwargs):
      request = soap_body_payload(body.decode())
      request_id = int(required_text(request, "requestId"))
      await self.session._handle_event(event(request_id))
      return response("OpenDoor")

    self.post.side_effect = post
    pending = await self.session.start_command("OpenDoor")
    self.assertEqual((await pending.wait()).tag, "ResponseData")
    self.assertEqual(pending.duration, 10)

  async def test_error_is_still_raised_by_later_waiter(self):
    self.post.return_value = response("ExecuteMethod")
    pending = await self.session.start_command("ExecuteMethod", methodName="PCR")
    await self.session._handle_event(event(pending.request_id, 2002))
    await asyncio.sleep(0)
    for _ in range(2):
      with self.assertRaises(SiLAError) as error:
        await pending.wait()
      self.assertEqual(error.exception.code, 2002)

  async def test_timeout_and_cancellation_do_not_cancel_command(self):
    self.post.return_value = response("ExecuteMethod")
    pending = await self.session.start_command("ExecuteMethod")
    with self.assertRaises(asyncio.TimeoutError):
      await pending.wait(0.001)
    self.assertFalse(pending.future.done())
    waiter = asyncio.create_task(pending.wait())
    await asyncio.sleep(0)
    waiter.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await waiter
    self.assertFalse(pending.future.done())
    await self.session._handle_event(event(pending.request_id))
    await pending.wait()

  async def test_concurrent_reads_do_not_wait_for_method_completion(self):
    self.post.return_value = response("ExecuteMethod")
    pending = await self.session.start_command("ExecuteMethod")
    self.post.return_value = response("GetStatus", 1)
    await self.session.send_command("GetStatus")
    self.assertFalse(pending.future.done())
    await self.session.close()
    with self.assertRaises(ConnectionError):
      await pending.wait()

  async def test_busy_rejection_retains_firmware_error(self):
    self.post.return_value = response("OpenDoor", 4, "Busy with ExecuteMethod")
    with self.assertRaisesRegex(SiLAError, "Busy with ExecuteMethod"):
      await self.session.send_command("OpenDoor")
    self.assertFalse(self.session._pending)

  async def test_finished_with_warning_logs_and_succeeds(self):
    self.post.return_value = response("OpenDoor")
    pending = await self.session.start_command("OpenDoor")
    with self.assertLogs("pylabrobot.inheco.transport.sila", level="WARNING"):
      await self.session._handle_event(event(pending.request_id, 12))
    await pending.wait()

  async def test_response_code_one_and_empty_payload(self):
    self.post.return_value = response("OpenDoor")
    pending = await self.session.start_command("OpenDoor")
    await self.session._handle_event(event(pending.request_id, 1, data=""))
    self.assertIsNone(await pending.wait())

  async def test_error_event_fails_corresponding_command(self):
    self.post.return_value = response("ExecuteMethod")
    pending = await self.session.start_command("ExecuteMethod")
    await self.session._handle_event(event(pending.request_id, 2001, name="ErrorEvent"))
    with self.assertRaises(SiLAError):
      await pending.wait()

  async def test_malformed_callback_fails_waiter(self):
    self.post.return_value = response("ReadActualTemperature")
    pending = await self.session.start_command("ReadActualTemperature")
    with self.assertRaises(Exception):
      await self.session._handle_event(event(pending.request_id, data="<broken"))
    with self.assertRaises(Exception):
      await pending.wait()

  async def test_reopen_after_close(self):
    await self.session.close()
    await self.session.setup()
    self.post.return_value = response("GetStatus", 1)
    await self.session.send_command("GetStatus")

  async def test_empty_success_message_is_valid_xml_string(self):
    self.post.return_value = response("GetStatus", 1, message="")
    await self.session.send_command("GetStatus")
