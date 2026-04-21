import contextlib
import unittest.mock

import anyio
import pytest

from pylabrobot.plate_reading.byonoy.byonoy_backend import ByonoyAbsorbance96AutomateBackend
from pylabrobot.testing.concurrency import AnyioTestBase


class TestByonoyBackend(AnyioTestBase):
  async def _enter_lifespan(self, stack: contextlib.AsyncExitStack):
    await super()._enter_lifespan(stack)
    self.backend = ByonoyAbsorbance96AutomateBackend()
    self.backend.io = unittest.mock.AsyncMock()

    self.backend.get_available_absorbance_wavelengths = unittest.mock.AsyncMock(  # type: ignore[method-assign]
      return_value=[450, 660]
    )
    self.backend.initialize_measurements = unittest.mock.AsyncMock()  # type: ignore[method-assign]

  @pytest.mark.parametrize("backend", ["asyncio", "trio"])
  async def test_setup(self):

    async with self.backend:
      assert self.backend.io.__aenter__.called  # type: ignore[attr-defined]
      assert self.backend.initialize_measurements.called  # type: ignore[attr-defined]

      assert self.backend.available_wavelengths == [450, 660]

      # Verify ping loop is running by checking if write was called (if sending_pings is True)
      # Wait, sending_pings defaults to False!
      assert not self.backend._sending_pings

      # Enable pings
      self.backend._start_background_pings()
      assert self.backend._sending_pings

      # Wait for a bit to let ping loop run
      await anyio.sleep(1.5)
      assert self.backend.io.write.called
