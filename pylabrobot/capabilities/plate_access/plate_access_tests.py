import unittest
from unittest.mock import AsyncMock, Mock

from pylabrobot.capabilities.plate_access import PlateAccess, PlateAccessBackend, PlateAccessState


class TestPlateAccess(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.mock_backend = Mock(spec=PlateAccessBackend)
    self.mock_backend.lock = AsyncMock()
    self.mock_backend.unlock = AsyncMock()
    self.mock_backend.get_access_state = AsyncMock(
      return_value=PlateAccessState(
        source_access_open=False,
        source_access_closed=True,
        destination_access_open=False,
        destination_access_closed=True,
        door_open=False,
        door_closed=True,
      )
    )
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
    opened_state = PlateAccessState(
      source_access_open=True,
      source_access_closed=False,
      door_open=True,
      door_closed=False,
      source_plate_position=-1,
    )
    closed_state = PlateAccessState(
      source_access_open=False,
      source_access_closed=True,
      door_open=True,
      door_closed=False,
      source_plate_position=0,
    )
    self.mock_backend.get_access_state = AsyncMock(side_effect=[opened_state, closed_state])
    cap = await self._make_cap()
    opened = await cap.open_source_plate(timeout=5.0, poll_interval=0.001)
    closed = await cap.close_source_plate(timeout=5.0, poll_interval=0.001)
    self.mock_backend.open_source_plate.assert_awaited_once()
    self.assertGreaterEqual(self.mock_backend.open_source_plate.await_args.kwargs["timeout"], 0.0)
    self.mock_backend.close_source_plate.assert_awaited_once_with(
      plate_type=None,
      barcode_location=None,
      barcode="",
      timeout=self.mock_backend.close_source_plate.await_args.kwargs["timeout"],
    )
    self.assertTrue(opened.source_access_open)
    self.assertTrue(closed.source_access_closed)

  async def test_destination_plate_methods(self):
    opened_state = PlateAccessState(
      destination_access_open=True,
      destination_access_closed=False,
      door_open=True,
      door_closed=False,
      destination_plate_position=-1,
    )
    closed_state = PlateAccessState(
      destination_access_open=False,
      destination_access_closed=True,
      door_open=True,
      door_closed=False,
      destination_plate_position=0,
    )
    self.mock_backend.get_access_state = AsyncMock(side_effect=[opened_state, closed_state])
    cap = await self._make_cap()
    opened = await cap.open_destination_plate(timeout=5.0, poll_interval=0.001)
    closed = await cap.close_destination_plate(
      plate_type="Foo",
      barcode_location="Rear",
      barcode="123",
      timeout=5.0,
      poll_interval=0.001,
    )
    self.mock_backend.open_destination_plate.assert_awaited_once()
    self.mock_backend.close_destination_plate.assert_awaited_once_with(
      plate_type="Foo",
      barcode_location="Rear",
      barcode="123",
      timeout=self.mock_backend.close_destination_plate.await_args.kwargs["timeout"],
    )
    self.assertTrue(opened.destination_access_open)
    self.assertTrue(closed.destination_access_closed)

  async def test_source_plate_timeout_passthrough(self):
    self.mock_backend.get_access_state = AsyncMock(
      return_value=PlateAccessState(source_access_closed=True)
    )
    cap = await self._make_cap()
    await cap.close_source_plate(plate_type="384PP_DMSO2", timeout=30.0, poll_interval=0.001)
    self.mock_backend.close_source_plate.assert_awaited_once_with(
      plate_type="384PP_DMSO2",
      barcode_location=None,
      barcode="",
      timeout=self.mock_backend.close_source_plate.await_args.kwargs["timeout"],
    )
    self.assertGreaterEqual(self.mock_backend.close_source_plate.await_args.kwargs["timeout"], 0.0)

  async def test_close_door(self):
    closed_state = PlateAccessState(
      source_access_open=False,
      source_access_closed=True,
      destination_access_open=False,
      destination_access_closed=True,
      door_open=False,
      door_closed=True,
    )
    self.mock_backend.get_access_state = AsyncMock(return_value=closed_state)
    cap = await self._make_cap()
    state = await cap.close_door(timeout=5.0, poll_interval=0.001)
    self.mock_backend.close_door.assert_awaited_once()
    self.assertTrue(state.door_closed)

  async def test_action_methods_wait_for_expected_state(self):
    self.mock_backend.get_access_state = AsyncMock(
      side_effect=[
        PlateAccessState(
          source_access_open=False, destination_access_closed=True, door_closed=False
        ),
        PlateAccessState(
          source_access_open=True, destination_access_closed=False, door_closed=False
        ),
        PlateAccessState(
          source_access_open=False, destination_access_closed=True, door_closed=True
        ),
      ]
    )
    cap = await self._make_cap()

    opened_state = await cap.open_source_plate(timeout=0.1, poll_interval=0.001)
    final_state = await cap.close_door(timeout=0.1, poll_interval=0.001)

    self.assertTrue(opened_state.source_access_open)
    self.assertTrue(final_state.door_closed)

  async def test_action_timeout_raises(self):
    self.mock_backend.get_access_state = AsyncMock(
      return_value=PlateAccessState(source_access_open=False)
    )
    cap = await self._make_cap()

    with self.assertRaises(TimeoutError):
      await cap.open_source_plate(timeout=0.01, poll_interval=0.001)

  async def test_not_setup_raises(self):
    cap = PlateAccess(backend=self.mock_backend)
    with self.assertRaises(RuntimeError):
      await cap.open_source_plate()


if __name__ == "__main__":
  unittest.main()
