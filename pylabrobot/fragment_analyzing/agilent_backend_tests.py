import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.fragment_analyzing.agilent_backend import (
  AgilentFABackend,
  FragmentAnalyzerCommandFailedError,
  FragmentAnalyzerError,
  FragmentAnalyzerHardwareError,
  FragmentAnalyzerInvalidCommandError,
  FragmentAnalyzerLowSolutionError,
  FragmentAnalyzerMethodError,
  FragmentAnalyzerOtherError,
)


class TestAgilentFABackend(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.mock_socket = MagicMock()
    self.mock_socket.setup = AsyncMock()
    self.mock_socket.stop = AsyncMock()
    self.mock_socket.write = AsyncMock()
    self.mock_socket.read = AsyncMock(return_value="*OK")

    with patch("pylabrobot.io.Socket", return_value=self.mock_socket):
      self.backend = AgilentFABackend(host="localhost", port=3000)
      self.backend.io = self.mock_socket

  async def test_setup_stop(self):
    await self.backend.setup()
    self.mock_socket.setup.assert_called_once()
    await self.backend.stop()
    self.mock_socket.stop.assert_called_once()

  async def test_send_command(self):
    self.mock_socket.read.return_value = "*DONE"
    response = await self.backend.send_command("TEST")
    self.mock_socket.write.assert_called_once_with("TEST")
    self.mock_socket.read.assert_called_once()
    self.assertEqual(response, "*DONE")

  async def test_send_command_and_await_completion(self):
    self.mock_socket.read.side_effect = ["*RUN", "*COMPLETE"]
    responses = await self.backend.send_command_and_await_completion("RUN METHOD", "*RUN")
    self.mock_socket.write.assert_called_once_with("RUN METHOD")
    self.assertEqual(self.mock_socket.read.call_count, 2)
    self.assertEqual(responses, ["*RUN", "*COMPLETE"])

  async def test_send_command_and_await_completion_unexpected_response(self):
    self.mock_socket.read.side_effect = ["*UNEXPECTED", "*COMPLETE"]
    with self.assertRaises(FragmentAnalyzerError):
      await self.backend.send_command_and_await_completion("RUN METHOD", "*RUN")

  async def test_send_command_and_await_completion_no_complete(self):
    self.mock_socket.read.side_effect = ["*RUN", "*NOT_COMPLETE"]
    with self.assertRaises(FragmentAnalyzerError):
      await self.backend.send_command_and_await_completion("RUN METHOD", "*RUN")

  async def test_get_status(self):
    self.mock_socket.read.return_value = "*STATUS: IDLE"
    status = await self.backend.get_status()
    self.mock_socket.write.assert_called_once_with("STATUS")
    self.assertEqual(status, "IDLE")

  async def test_get_status_complete(self):
    self.mock_socket.read.return_value = "*Complete"
    status = await self.backend.get_status()
    self.assertEqual(status, "Method complete. Waiting for conditioning to finish.")

  async def test_get_status_unexpected(self):
    self.mock_socket.read.return_value = "UNEXPECTED"
    with self.assertRaises(FragmentAnalyzerError):
      await self.backend.get_status()

  async def test_tray_out(self):
    self.mock_socket.read.side_effect = ["*OUT", "*COMPLETE"]
    await self.backend.tray_out(5)
    self.mock_socket.write.assert_called_once_with("OUT")

  async def test_tray_out_other_tray(self):
    self.mock_socket.read.side_effect = ["*OUT", "*COMPLETE"]
    await self.backend.tray_out(3)
    self.mock_socket.write.assert_called_once_with("OUT3")

  async def test_tray_out_invalid_tray(self):
    with self.assertRaises(ValueError):
      await self.backend.tray_out(0)
    with self.assertRaises(ValueError):
      await self.backend.tray_out(6)

  async def test_store_capillary(self):
    self.mock_socket.read.side_effect = ["*STORE", "*COMPLETE"]
    await self.backend.store_capillary()
    self.mock_socket.write.assert_called_once_with("STORE")

  async def test_set_plate_name(self):
    self.mock_socket.read.return_value = "*TRAY"
    await self.backend.set_plate_name("test_plate")
    self.mock_socket.write.assert_called_once_with("TRAY test_plate")

  async def test_set_plate_name_fail(self):
    self.mock_socket.read.return_value = "FAIL"
    with self.assertRaises(FragmentAnalyzerError):
      await self.backend.set_plate_name("test_plate")

  async def test_run_method_blocking(self):
    self.mock_socket.read.side_effect = ["*RUN", "*COMPLETE"]
    await self.backend.run_method("my_method", nonblocking=False)
    self.mock_socket.write.assert_called_once_with("RUN my_method")
    self.assertEqual(self.mock_socket.read.call_count, 2)

  async def test_run_method_nonblocking(self):
    self.mock_socket.read.return_value = "*RUN"
    await self.backend.run_method("my_method", nonblocking=True)
    self.mock_socket.write.assert_called_once_with("RUN my_method")
    self.mock_socket.read.assert_called_once()

  async def test_get_ladder_file(self):
    self.mock_socket.read.side_effect = ["*CAL", "*COMPLETE"]
    await self.backend.get_ladder_file("my_method", "ladder.txt")
    self.mock_socket.write.assert_called_once_with("CAL my_method, ladder.txt")

  async def test_set_ladder_file(self):
    self.mock_socket.read.return_value = "*LAD-FILE"
    await self.backend.set_ladder_file("ladder.txt")
    self.mock_socket.write.assert_called_once_with("LAD-FILE ladder.txt")

  async def test_set_ladder_file_fail(self):
    self.mock_socket.read.return_value = "FAIL"
    with self.assertRaises(FragmentAnalyzerError):
      await self.backend.set_ladder_file("ladder.txt")

  async def test_abort(self):
    self.mock_socket.read.side_effect = ["*ABORT", "*COMPLETE"]
    await self.backend.abort()
    self.mock_socket.write.assert_called_once_with("ABORT")

  async def test_get_solution_levels(self):
    self.mock_socket.read.return_value = "*SOLUTIONS: 1.1,2.2,3.3,4.4"
    levels = await self.backend.get_solution_levels()
    self.mock_socket.write.assert_called_once_with("SOLUTIONS")
    self.assertEqual(levels.gel1, 1.1)
    self.assertEqual(levels.gel2, 2.2)
    self.assertEqual(levels.conditioningSolution, 3.3)
    self.assertEqual(levels.waste, 4.4)

  async def test_get_solution_levels_fail(self):
    self.mock_socket.read.return_value = "FAIL"
    with self.assertRaises(FragmentAnalyzerError):
      await self.backend.get_solution_levels()

  async def test_get_sensor_data(self):
    self.mock_socket.read.return_value = "*VCP: 5.5,6.6,7.7"
    data = await self.backend.get_sensor_data()
    self.mock_socket.write.assert_called_once_with("VCP")
    self.assertEqual(data.voltage, 5.5)
    self.assertEqual(data.current, 6.6)
    self.assertEqual(data.pressure, 7.7)

  async def test_get_sensor_data_fail(self):
    self.mock_socket.read.return_value = "FAIL"
    with self.assertRaises(FragmentAnalyzerError):
      await self.backend.get_sensor_data()


class TestAgilentFAErrorParsing(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    with patch("pylabrobot.io.Socket", return_value=MagicMock()):
      self.backend = AgilentFABackend(host="localhost", port=3000)

  def test_parse_error(self):
    with self.assertRaises(FragmentAnalyzerInvalidCommandError):
      self.backend._parse_error("!1, some info")
    with self.assertRaises(FragmentAnalyzerMethodError):
      self.backend._parse_error("!2")
    with self.assertRaises(FragmentAnalyzerMethodError):
      self.backend._parse_error("!3")
    with self.assertRaises(FragmentAnalyzerCommandFailedError):
      self.backend._parse_error("!4")
    with self.assertRaises(FragmentAnalyzerLowSolutionError):
      self.backend._parse_error("!5")
    with self.assertRaises(FragmentAnalyzerHardwareError):
      self.backend._parse_error("!6")
    with self.assertRaises(FragmentAnalyzerHardwareError):
      self.backend._parse_error("!7")
    with self.assertRaises(FragmentAnalyzerHardwareError):
      self.backend._parse_error("!8")
    with self.assertRaises(FragmentAnalyzerHardwareError):
      self.backend._parse_error("!9")
    with self.assertRaises(FragmentAnalyzerOtherError):
      self.backend._parse_error("!10")
    with self.assertRaises(FragmentAnalyzerError):
      self.backend._parse_error("!999") # Unknown error

  def test_no_error(self):
    try:
      self.backend._parse_error("*OK")
    except FragmentAnalyzerError:
      self.fail("FragmentAnalyzerError raised for a valid OK response")

if __name__ == "__main__":
  unittest.main()
