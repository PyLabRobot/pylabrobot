from unittest.mock import AsyncMock

from pylabrobot.centrifuge.vspin_backend import Access2Backend
from pylabrobot.testing.concurrency import AnyioTestBase


class TestAccess2Backend(AnyioTestBase):
  async def test_load_grip_steps_validation(self):
    backend = Access2Backend(device_id="dummy")
    backend.send_command = AsyncMock(return_value=b"\x00")  # type: ignore[method-assign]

    # Valid values 1..4 should pass
    for grip_steps in (1, 2, 3, 4):
      backend.send_command.reset_mock()
      await backend.load(grip_steps=grip_steps)
      self.assertGreater(backend.send_command.call_count, 0)

    # Invalid values should raise ValueError
    for invalid_grip_steps in (0, 5, -1):
      with self.assertRaises(ValueError):
        await backend.load(grip_steps=invalid_grip_steps)  # type: ignore[arg-type]
