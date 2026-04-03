import unittest
from unittest.mock import AsyncMock, Mock

from pylabrobot.capabilities.plate_access import PlateAccess, PlateAccessBackend, PlateAccessState


class TestPlateAccess(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.mock_backend = Mock(spec=PlateAccessBackend)
    self.mock_backend.lock = AsyncMock()
    self.mock_backend.unlock = AsyncMock()
    self.mock_backend.get_access_state = AsyncMock(return_value=PlateAccessState())
    self.mock_backend.open_source_plate = AsyncMock()
    self.mock_backend.close_source_plate = AsyncMock()
    self.mock_backend.open_destination_plate = AsyncMock()
    self.mock_backend.close_destination_plate = AsyncMock()
    self.mock_backend.close_door = AsyncMock()

  async def _make_cap(self):
    cap = PlateAccess(backend=self.mock_backend)
    await cap._on_setup()
    return cap

  async def test_lock(self):
    cap = await self._make_cap()
    await cap.lock(app="PyLabRobot", owner="tester")
    self.mock_backend.lock.assert_awaited_once_with(app="PyLabRobot", owner="tester")

  async def test_get_access_state(self):
    cap = await self._make_cap()
    state = await cap.get_access_state()
    self.assertIsInstance(state, PlateAccessState)
    self.mock_backend.get_access_state.assert_awaited_once()

  async def test_source_plate_methods(self):
    cap = await self._make_cap()
    await cap.open_source_plate()
    await cap.close_source_plate()
    self.mock_backend.open_source_plate.assert_awaited_once()
    self.mock_backend.close_source_plate.assert_awaited_once_with(
      plate_type=None,
      barcode_location=None,
      barcode="",
    )

  async def test_destination_plate_methods(self):
    cap = await self._make_cap()
    await cap.open_destination_plate()
    await cap.close_destination_plate(plate_type="Foo", barcode_location="Rear", barcode="123")
    self.mock_backend.open_destination_plate.assert_awaited_once()
    self.mock_backend.close_destination_plate.assert_awaited_once_with(
      plate_type="Foo",
      barcode_location="Rear",
      barcode="123",
    )

  async def test_close_door(self):
    cap = await self._make_cap()
    await cap.close_door()
    self.mock_backend.close_door.assert_awaited_once()

  async def test_not_setup_raises(self):
    cap = PlateAccess(backend=self.mock_backend)
    with self.assertRaises(RuntimeError):
      await cap.open_source_plate()


if __name__ == "__main__":
  unittest.main()
