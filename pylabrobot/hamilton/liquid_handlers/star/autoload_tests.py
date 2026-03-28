import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.hamilton.liquid_handlers.star.autoload import STARAutoload


class TestAutoloadCommands(unittest.IsolatedAsyncioTestCase):
  """Test that STARAutoload methods produce the correct firmware commands."""

  async def asyncSetUp(self):
    self.mock_driver = MagicMock()
    self.mock_driver.send_command = AsyncMock()
    self.autoload = STARAutoload(driver=self.mock_driver, instrument_size_slots=54)

  # -- initialization --------------------------------------------------------

  async def test_initialize(self):
    await self.autoload.initialize()
    self.mock_driver.send_command.assert_called_once_with(module="C0", command="II")

  async def test_request_initialization_status_true(self):
    self.mock_driver.send_command.return_value = {"qw": 1}
    result = await self.autoload.request_initialization_status()
    self.assertTrue(result)
    self.mock_driver.send_command.assert_called_once_with(
      module="I0", command="QW", fmt="qw#"
    )

  async def test_request_initialization_status_false(self):
    self.mock_driver.send_command.return_value = {"qw": 0}
    result = await self.autoload.request_initialization_status()
    self.assertFalse(result)

  # -- z-position safety -----------------------------------------------------

  async def test_move_to_safe_z_position(self):
    await self.autoload.move_to_safe_z_position()
    self.mock_driver.send_command.assert_called_once_with(module="C0", command="IV")

  # -- position queries ------------------------------------------------------

  async def test_request_track(self):
    self.mock_driver.send_command.return_value = {"qa": 12}
    result = await self.autoload.request_track()
    self.assertEqual(result, 12)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="QA", fmt="qa##"
    )

  async def test_request_type_1d(self):
    self.mock_driver.send_command.return_value = {"cq": 0}
    result = await self.autoload.request_type()
    self.assertEqual(result, "ML-STAR with 1D Barcode Scanner")
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="CQ", fmt="cq#"
    )

  async def test_request_type_2d(self):
    self.mock_driver.send_command.return_value = {"cq": 2}
    result = await self.autoload.request_type()
    self.assertEqual(result, "ML-STAR with 2D Barcode Scanner")

  async def test_request_type_unknown(self):
    self.mock_driver.send_command.return_value = {"cq": 9}
    result = await self.autoload.request_type()
    self.assertEqual(result, "9")

  # -- carrier sensing -------------------------------------------------------

  async def test_decode_hex_bitmask_empty(self):
    self.assertEqual(STARAutoload._decode_hex_bitmask_to_track_list("0000"), [])

  async def test_decode_hex_bitmask_single(self):
    # 0x01 = bit 0 set = slot 1
    self.assertEqual(STARAutoload._decode_hex_bitmask_to_track_list("01"), [1])

  async def test_decode_hex_bitmask_multiple(self):
    # 0x05 = bits 0 and 2 = slots 1 and 3
    self.assertEqual(STARAutoload._decode_hex_bitmask_to_track_list("05"), [1, 3])

  async def test_decode_hex_bitmask_invalid(self):
    with self.assertRaises(ValueError):
      STARAutoload._decode_hex_bitmask_to_track_list("ZZ")

  async def test_request_presence_of_carriers_on_deck(self):
    self.mock_driver.send_command.return_value = "C0RCid0001ce0005"
    result = await self.autoload.request_presence_of_carriers_on_deck()
    self.assertEqual(result, [1, 3])
    self.mock_driver.send_command.assert_called_once_with(module="C0", command="RC")

  async def test_request_presence_of_carriers_on_loading_tray(self):
    self.mock_driver.send_command.return_value = "C0CSid0001cd03"
    result = await self.autoload.request_presence_of_carriers_on_loading_tray()
    self.assertEqual(result, [1, 2])
    self.mock_driver.send_command.assert_called_once_with(module="C0", command="CS")

  async def test_request_presence_of_carriers_on_loading_tray_missing_cd(self):
    self.mock_driver.send_command.return_value = "C0CSid0001xx00"
    with self.assertRaises(ValueError):
      await self.autoload.request_presence_of_carriers_on_loading_tray()

  async def test_request_presence_of_single_carrier_present(self):
    self.mock_driver.send_command.return_value = {"ct": 1}
    result = await self.autoload.request_presence_of_single_carrier_on_loading_tray(track=10)
    self.assertTrue(result)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="CT", fmt="ct#", cp="10"
    )

  async def test_request_presence_of_single_carrier_absent(self):
    self.mock_driver.send_command.return_value = {"ct": 0}
    result = await self.autoload.request_presence_of_single_carrier_on_loading_tray(track=5)
    self.assertFalse(result)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="CT", fmt="ct#", cp="05"
    )

  async def test_request_presence_of_single_carrier_invalid_track(self):
    with self.assertRaises(AssertionError):
      await self.autoload.request_presence_of_single_carrier_on_loading_tray(track=0)
    with self.assertRaises(AssertionError):
      await self.autoload.request_presence_of_single_carrier_on_loading_tray(track=55)

  # -- movement commands -----------------------------------------------------

  async def test_move_to_track(self):
    await self.autoload.move_to_track(track=12)
    calls = self.mock_driver.send_command.call_args_list
    # First call: move_to_safe_z_position (C0:IV)
    self.assertEqual(calls[0].kwargs, {"module": "C0", "command": "IV"})
    # Second call: I0:XP
    self.assertEqual(calls[1].kwargs, {"module": "I0", "command": "XP", "xp": "12"})

  async def test_move_to_track_invalid(self):
    with self.assertRaises(AssertionError):
      await self.autoload.move_to_track(track=0)
    with self.assertRaises(AssertionError):
      await self.autoload.move_to_track(track=55)

  async def test_park(self):
    await self.autoload.park()
    calls = self.mock_driver.send_command.call_args_list
    self.assertEqual(calls[0].kwargs, {"module": "C0", "command": "IV"})
    self.assertEqual(calls[1].kwargs, {"module": "I0", "command": "XP", "xp": "54"})

  async def test_park_custom_slots(self):
    self.autoload._instrument_size_slots = 30
    await self.autoload.park()
    calls = self.mock_driver.send_command.call_args_list
    self.assertEqual(calls[1].kwargs, {"module": "I0", "command": "XP", "xp": "30"})

  # -- belt operations -------------------------------------------------------

  async def test_take_carrier_out_to_belt(self):
    # Carrier not on tray -> should proceed with CN command
    self.mock_driver.send_command.side_effect = [
      {"ct": 0},  # presence check returns absent
      None,        # CN command
    ]
    await self.autoload.take_carrier_out_to_belt(carrier_end_rail=10)
    calls = self.mock_driver.send_command.call_args_list
    self.assertEqual(calls[0].kwargs, {"module": "C0", "command": "CT", "fmt": "ct#", "cp": "10"})
    self.assertEqual(calls[1].kwargs, {"module": "C0", "command": "CN", "cp": "10"})

  async def test_take_carrier_out_to_belt_already_on_tray(self):
    self.mock_driver.send_command.return_value = {"ct": 1}
    with self.assertRaises(ValueError, msg="already on the loading tray"):
      await self.autoload.take_carrier_out_to_belt(carrier_end_rail=10)

  async def test_unload_carrier_after_barcode_scanning(self):
    await self.autoload.unload_carrier_after_barcode_scanning()
    self.mock_driver.send_command.assert_called_once_with(module="C0", command="CA")

  # -- barcode commands ------------------------------------------------------

  async def test_set_1d_barcode_type(self):
    await self.autoload.set_1d_barcode_type("Code 39")
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="CB", bt="04"
    )
    self.assertEqual(self.autoload._default_1d_symbology, "Code 39")

  async def test_set_1d_barcode_type_default(self):
    await self.autoload.set_1d_barcode_type(None)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="CB", bt="02"  # Code 128 default
    )

  async def test_load_carrier_from_tray_and_scan_carrier_barcode(self):
    self.mock_driver.send_command.return_value = "C0CIid0001bb/ABC123"
    result = await self.autoload.load_carrier_from_tray_and_scan_carrier_barcode(
      carrier_end_rail=10,
      carrier_barcode_reading=True,
    )
    self.assertIsNotNone(result)
    self.assertEqual(result.data, "ABC123")
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="CI",
      cp="10",
      bi="0043",
      bw="380",
      co="0960",
      cv="1281",
    )

  async def test_load_carrier_from_tray_no_barcode_reading(self):
    self.mock_driver.send_command.return_value = "C0CIid0001"
    result = await self.autoload.load_carrier_from_tray_and_scan_carrier_barcode(
      carrier_end_rail=10,
      carrier_barcode_reading=False,
    )
    self.assertIsNone(result)

  # -- high-level load / unload ----------------------------------------------

  async def test_unload_carrier(self):
    self.mock_driver.send_command.side_effect = [
      "C0CRid0001",  # CR command
      None,           # safe z
      None,           # park XP
    ]
    await self.autoload.unload_carrier(carrier_end_rail=10, park_autoload_after=True)
    calls = self.mock_driver.send_command.call_args_list
    self.assertEqual(calls[0].kwargs, {"module": "C0", "command": "CR", "cp": "10"})

  async def test_unload_carrier_no_park(self):
    self.mock_driver.send_command.return_value = "C0CRid0001"
    await self.autoload.unload_carrier(carrier_end_rail=10, park_autoload_after=False)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="CR", cp="10"
    )

  async def test_unload_carrier_invalid_rail(self):
    with self.assertRaises(AssertionError):
      await self.autoload.unload_carrier(carrier_end_rail=0)
    with self.assertRaises(AssertionError):
      await self.autoload.unload_carrier(carrier_end_rail=55)

  # -- LED / monitoring ------------------------------------------------------

  async def test_set_loading_indicators(self):
    bit_pattern = [True] + [False] * 53
    blink_pattern = [False] * 53 + [True]
    await self.autoload.set_loading_indicators(bit_pattern, blink_pattern)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="CP",
      cl="20000000000000",
      cb="00000000000001",
    )

  async def test_set_loading_indicators_invalid_length(self):
    with self.assertRaises(AssertionError):
      await self.autoload.set_loading_indicators([True] * 10, [False] * 10)

  async def test_set_carrier_monitoring(self):
    await self.autoload.set_carrier_monitoring(should_monitor=True)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="CU", cu=True
    )

  async def test_load_carrier_from_belt_no_barcode(self):
    self.mock_driver.send_command.side_effect = [
      "C0CLid0001",  # CL command
      None,           # safe z (park)
      None,           # park XP
    ]
    result = await self.autoload.load_carrier_from_belt(
      barcode_reading=False,
      park_autoload_after=True,
    )
    self.assertEqual(result, {})
    calls = self.mock_driver.send_command.call_args_list
    self.assertEqual(calls[0].kwargs["module"], "C0")
    self.assertEqual(calls[0].kwargs["command"], "CL")
    self.assertEqual(calls[0].kwargs["bd"], "0")  # vertical when no barcode
    self.assertEqual(calls[0].kwargs["cn"], "00")  # no scanning
