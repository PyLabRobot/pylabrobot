from unittest.mock import patch

import pytest

from pylabrobot.machines.backend import MachineBackend
from pylabrobot.testing.concurrency import AnyioTestBase

pytest.importorskip("ot_api")
from pylabrobot.temperature_controlling.opentrons_backend import (
  OpentronsTemperatureModuleBackend,
)


class MockOpentronsTemperatureModuleBackend(OpentronsTemperatureModuleBackend):
  # Skip the shielded deactivate-on-exit callback so teardown does not hit ot_api.
  async def _enter_lifespan(self, stack):
    await MachineBackend._enter_lifespan(self, stack)


class TestOpentronsTemperatureModuleBackend(AnyioTestBase):
  """The backend's ot_api calls are offloaded via anyio.to_thread.run_sync.

  These tests confirm the offloaded calls still forward the right arguments and
  parse responses correctly (run on both the asyncio and trio backends).
  """

  async def _enter_lifespan(self, stack):
    await super()._enter_lifespan(stack)
    self.backend = MockOpentronsTemperatureModuleBackend(opentrons_id="test_id")
    await stack.enter_async_context(self.backend)

  @patch("ot_api.modules.temperature_module_set_temperature")
  async def test_set_temperature(self, mock_set):
    await self.backend.set_temperature(37.0)
    mock_set.assert_called_once_with(celsius=37.0, module_id="test_id")

  @patch("ot_api.modules.temperature_module_deactivate")
  async def test_deactivate(self, mock_deactivate):
    await self.backend.deactivate()
    mock_deactivate.assert_called_once_with(module_id="test_id")

  @patch("ot_api.modules.list_connected_modules")
  async def test_get_current_temperature(self, mock_list):
    mock_list.return_value = [{"id": "test_id", "data": {"currentTemperature": 25.5}}]
    self.assertEqual(await self.backend.get_current_temperature(), 25.5)
    mock_list.assert_called_once()

  @patch("ot_api.modules.list_connected_modules")
  async def test_get_current_temperature_raises_when_not_found(self, mock_list):
    mock_list.return_value = [{"id": "other_id", "data": {}}]
    with self.assertRaises(RuntimeError):
      await self.backend.get_current_temperature()
