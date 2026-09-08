import unittest
from dataclasses import FrozenInstanceError
from typing import Any, Dict, List
from unittest.mock import AsyncMock, call

from pylabrobot.io.http import HTTP, HTTPError
from pylabrobot.opentrons.api import OpentronsAPI
from pylabrobot.opentrons.errors import OpentronsError, OpentronsProtocolError
from pylabrobot.opentrons.types import LabwareIdentity, ModuleInfo, MountedPipette, RunInfo


class OpentronsAPITests(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    self.io = AsyncMock(spec=HTTP)
    self.api = OpentronsAPI(self.io)

  async def test_health_is_a_fresh_value_without_changing_client_state(self) -> None:
    response = {
      "name": "OT2sterile",
      "robot_model": "OT-2 Standard",
      "api_version": "8.7.0",
      "fw_version": "v1.1.0",
      "robot_serial": None,
    }
    self.io.request.return_value = response
    state = vars(self.api).copy()
    first = await self.api.get_health()
    response["api_version"] = "8.8.1"
    second = await self.api.get_health()
    self.assertEqual(first.software_version, "8.7.0")
    self.assertEqual(second.software_version, "8.8.1")
    self.assertEqual(first.model, "OT-2 Standard")
    self.assertEqual(vars(self.api), state)
    with self.assertRaises(FrozenInstanceError):
      setattr(first, "name", "replacement")
    self.io.setup.assert_not_called()

  async def test_mount_discovery_handles_an_empty_mount_without_a_name_key(self) -> None:
    self.io.request.return_value = {
      "left": {"id": "physical-serial", "model": "p20_single_v2.2", "name": "p20_single_gen2"},
      "right": {"mount_axis": "a", "plunger_axis": "c"},
    }
    pipettes = await self.api.get_mounted_pipettes()
    self.assertEqual(
      pipettes, (MountedPipette("left", "p20_single_gen2", "p20_single_v2.2", "physical-serial"),)
    )
    self.io.request.assert_awaited_once_with("GET", "/pipettes")

  async def test_run_creation_does_not_select_or_replace_a_run(self) -> None:
    self.io.request.side_effect = [
      {"data": {"id": "first", "status": "idle"}},
      {"data": {"id": "second", "status": "idle"}},
    ]
    state = vars(self.api).copy()
    first, second = await self.api.create_run(), await self.api.create_run()
    self.assertEqual((first.id, second.id), ("first", "second"))
    self.assertEqual(vars(self.api), state)
    self.assertEqual(self.io.request.await_args_list, [call("POST", "/runs")] * 2)

  async def test_queries_unpack_run_and_module_results(self) -> None:
    self.io.request.side_effect = [
      {"data": [{"id": "run", "status": "idle"}]},
      {"data": {"id": "run", "status": "running"}},
      {"data": [{"id": "module", "moduleType": "temperatureModuleType"}]},
    ]
    self.assertEqual(await self.api.get_runs(), (RunInfo("run", "idle"),))
    self.assertEqual(await self.api.get_run("run"), RunInfo("run", "running"))
    self.assertEqual(
      await self.api.get_connected_modules(), (ModuleInfo("module", "temperatureModuleType"),)
    )

  async def test_labware_definition_receipt_is_parsed(self) -> None:
    self.io.request.return_value = {"data": {"definitionUri": "pylabrobot/rack/1"}}
    definition = {"schemaVersion": 2}
    self.assertEqual(
      await self.api.define_labware("run", definition), LabwareIdentity("pylabrobot", "rack", 1)
    )
    self.io.request.assert_awaited_once_with(
      "POST", "/runs/run/labware_definitions", {"data": definition}
    )

  async def test_invalid_receipts_are_rejected_before_an_id_can_be_used(self) -> None:
    responses: List[Dict[str, Any]] = [{}, {"data": {}}, {"data": {"id": None}}]
    for response in responses:
      with self.subTest(response=response):
        self.io.request.return_value = response
        with self.assertRaises(OpentronsProtocolError):
          await self.api.create_run()

  async def test_command_status_accepts_absent_or_null_results(self) -> None:
    for data in ({"status": "queued"}, {"status": "running", "result": None, "error": None}):
      with self.subTest(data=data):
        self.io.request.return_value = {"data": data}
        command = await self.api.get_command("run", "command")
        self.assertEqual(command.result, {})
        self.assertEqual(command.error, {})

  async def test_stop_uses_legacy_route_only_for_unsupported_endpoint(self) -> None:
    self.io.request.side_effect = [HTTPError("POST", "/actions", 404, "unsupported"), {}]
    await self.api.stop_run("run")
    self.assertEqual(
      self.io.request.await_args_list,
      [
        call("POST", "/runs/run/actions", {"data": {"actionType": "stop"}}),
        call("POST", "/runs/run/cancel", None),
      ],
    )

  async def test_stop_does_not_mask_a_server_or_connection_failure_with_a_fallback(self) -> None:
    for failure in (HTTPError("POST", "/actions", 500, "server error"), TimeoutError("timeout")):
      for fallback_count in range(4):
        with self.subTest(failure=failure, fallback_count=fallback_count):
          self.io.request.reset_mock()
          self.io.request.side_effect = [
            HTTPError("POST", "/actions", 404, "unsupported")
          ] * fallback_count + [failure]
          with self.assertRaises(OpentronsError) as raised:
            await self.api.stop_run("run")
          self.assertIs(raised.exception.__cause__, failure)
          self.assertEqual(self.io.request.await_count, fallback_count + 1)

  async def test_stop_preserves_fallback_order_and_stops_at_first_success(self) -> None:
    """Every supported route ends the fallback sequence immediately."""
    expected_calls = [
      call("POST", "/runs/run/actions", {"data": {"actionType": "stop"}}),
      call("POST", "/runs/run/cancel", None),
      call("POST", "/runs/run/actions/cancel", None),
      call("DELETE", "/runs/run", None),
    ]
    for status in (404, 405):
      for fallback_count in range(4):
        with self.subTest(status=status, fallback_count=fallback_count):
          self.io.request.reset_mock()
          self.io.request.side_effect = [
            HTTPError("POST", "/actions", status, "unsupported")
          ] * fallback_count + [{}]
          await self.api.stop_run("run")
          self.assertEqual(self.io.request.await_args_list, expected_calls[: fallback_count + 1])

  async def test_stop_reports_last_error_when_all_routes_are_unsupported(self) -> None:
    """Exhausting the fallback routes preserves the final failure as the cause."""
    failure = HTTPError("DELETE", "/runs/run", 405, "unsupported")
    self.io.request.side_effect = [HTTPError("POST", "/actions", 404, "unsupported")] * 3 + [
      failure
    ]
    with self.assertRaisesRegex(OpentronsError, "state is retained for retry") as raised:
      await self.api.stop_run("run")
    self.assertIs(raised.exception.__cause__, failure)
    self.assertEqual(self.io.request.await_count, 4)
