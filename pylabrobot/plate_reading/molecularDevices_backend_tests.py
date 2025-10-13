import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.resources.agenbio.plates import AGenBio_96_wellplate_Ub_2200ul

from pylabrobot.plate_reading.molecularDevices_backend import (
    MolecularDevicesBackend,
    ReadMode,
    ReadType,
    ReadOrder,
    Calibrate,
    ShakeSettings,
    CarriageSpeed,
    PmtGain,
    KineticSettings,
    SpectrumSettings,
    MolecularDevicesSettings,
)

class TestMolecularDevicesBackend(unittest.TestCase):
  def setUp(self):
    self.mock_serial = MagicMock()
    self.mock_serial.setup = AsyncMock()
    self.mock_serial.stop = AsyncMock()
    self.mock_serial.write = AsyncMock()
    self.mock_serial.readline = AsyncMock(return_value=b"OK>\r\n")

    with patch("pylabrobot.io.serial.Serial", return_value=self.mock_serial):
      self.backend = MolecularDevicesBackend(port="COM1")
      self.backend.io = self.mock_serial

  def test_setup_stop(self):
    asyncio.run(self.backend.setup())
    self.mock_serial.setup.assert_called_once()
    asyncio.run(self.backend.stop())
    self.mock_serial.stop.assert_called_once()

  def test_get_clear_command(self):
    self.assertEqual(self.backend._get_clear_command(), "!CLEAR DATA")

  def test_get_mode_command(self):
    settings = MolecularDevicesSettings(
        plate=MagicMock(),
        read_mode=ReadMode.ABS,
        read_type=ReadType.ENDPOINT,
        read_order=ReadOrder.COLUMN,
        calibrate=Calibrate.ON,
        shake_settings=None,
        carriage_speed=CarriageSpeed.NORMAL,
        speed_read=False,
        kinetic_settings=None,
        spectrum_settings=None,
    )
    self.assertEqual(self.backend._get_mode_command(settings), "!MODE ENDPOINT")

    settings.read_type = ReadType.KINETIC
    settings.kinetic_settings = KineticSettings(interval=10, num_readings=5)
    self.assertEqual(self.backend._get_mode_command(settings), "!MODE KINETIC 10 5")

    settings.read_type = ReadType.SPECTRUM
    settings.spectrum_settings = SpectrumSettings(start_wavelength=200, step=10, num_steps=50)
    self.assertEqual(self.backend._get_mode_command(settings), "!MODE SPECTRUM SPECTRUM 200 10 50")

    settings.spectrum_settings.excitation_emission_type = "EXSPECTRUM"
    self.assertEqual(self.backend._get_mode_command(settings), "!MODE SPECTRUM EXSPECTRUM 200 10 50")

  def test_get_wavelength_commands(self):
    settings = MolecularDevicesSettings(
        plate=MagicMock(),
        read_mode=ReadMode.ABS,
        read_type=ReadType.ENDPOINT,
        read_order=ReadOrder.COLUMN,
        calibrate=Calibrate.ON,
        shake_settings=None,
        carriage_speed=CarriageSpeed.NORMAL,
        speed_read=False,
        wavelengths=[500, (600, True)],
        kinetic_settings=None,
        spectrum_settings=None,
    )
    self.assertEqual(self.backend._get_wavelength_commands(settings), ["!WAVELENGTH 500 F600"])

    settings.path_check = True
    self.assertEqual(self.backend._get_wavelength_commands(settings), ["!WAVELENGTH 500 F600 900 998"])

    settings.read_mode = ReadMode.FLU
    settings.excitation_wavelengths = [485]
    settings.emission_wavelengths = [520]
    self.assertEqual(self.backend._get_wavelength_commands(settings), ["!EXWAVELENGTH 485", "!EMWAVELENGTH 520"])

    settings.read_mode = ReadMode.LUM
    settings.emission_wavelengths = [590]
    self.assertEqual(self.backend._get_wavelength_commands(settings), ["!EMWAVELENGTH 590"])

  def test_get_plate_position_commands(self):
    plate = AGenBio_96_wellplate_Ub_2200ul("test_plate")
    settings = MolecularDevicesSettings(
        plate=plate,
        read_mode=ReadMode.ABS,
        read_type=ReadType.ENDPOINT,
        read_order=ReadOrder.COLUMN,
        calibrate=Calibrate.ON,
        shake_settings=None,
        carriage_speed=CarriageSpeed.NORMAL,
        speed_read=False,
        kinetic_settings=None,
        spectrum_settings=None,
    )
    cmds = self.backend._get_plate_position_commands(settings)
    self.assertEqual(len(cmds), 2)
    self.assertEqual(cmds[0], "!XPOS 13.380 9.000 12")
    self.assertEqual(cmds[1], "!YPOS 12.240 9.000 8")

  def test_get_strip_command(self):
    plate = AGenBio_96_wellplate_Ub_2200ul("test_plate")
    settings = MolecularDevicesSettings(
        plate=plate,
        read_mode=ReadMode.ABS,
        read_type=ReadType.ENDPOINT,
        read_order=ReadOrder.COLUMN,
        calibrate=Calibrate.ON,
        shake_settings=None,
        carriage_speed=CarriageSpeed.NORMAL,
        speed_read=False,
        kinetic_settings=None,
        spectrum_settings=None,
    )
    self.assertEqual(self.backend._get_strip_command(settings), "!STRIP 1 12")

  def test_get_shake_commands(self):
    settings = MolecularDevicesSettings(
        plate=MagicMock(),
        read_mode=ReadMode.ABS,
        read_type=ReadType.ENDPOINT,
        read_order=ReadOrder.COLUMN,
        calibrate=Calibrate.ON,
        shake_settings=None,
        carriage_speed=CarriageSpeed.NORMAL,
        speed_read=False,
        kinetic_settings=None,
        spectrum_settings=None,
    )
    self.assertEqual(self.backend._get_shake_commands(settings), ["!SHAKE OFF"])

    settings.shake_settings = ShakeSettings(before_read=True, before_read_duration=5)
    self.assertEqual(self.backend._get_shake_commands(settings), ["!SHAKE ON", "!SHAKE 5 0 0 0 0"])

    settings.shake_settings = ShakeSettings(between_reads=True, between_reads_duration=3)
    settings.kinetic_settings = KineticSettings(interval=10, num_readings=5)
    self.assertEqual(self.backend._get_shake_commands(settings), ["!SHAKE ON", "!SHAKE 0 10 7 3 0"])

  def test_get_carriage_speed_command(self):
    settings = MolecularDevicesSettings(
        plate=MagicMock(),
        read_mode=ReadMode.ABS,
        read_type=ReadType.ENDPOINT,
        read_order=ReadOrder.COLUMN,
        calibrate=Calibrate.ON,
        shake_settings=None,
        carriage_speed=CarriageSpeed.NORMAL,
        speed_read=False,
        kinetic_settings=None,
        spectrum_settings=None,
    )
    self.assertEqual(self.backend._get_carriage_speed_command(settings), "!CSPEED 8")
    settings.carriage_speed = CarriageSpeed.SLOW
    self.assertEqual(self.backend._get_carriage_speed_command(settings), "!CSPEED 1")

  def test_get_read_stage_command(self):
    settings = MolecularDevicesSettings(
        plate=MagicMock(),
        read_mode=ReadMode.FLU,
        read_type=ReadType.ENDPOINT,
        read_order=ReadOrder.COLUMN,
        calibrate=Calibrate.ON,
        shake_settings=None,
        carriage_speed=CarriageSpeed.NORMAL,
        speed_read=False,
        kinetic_settings=None,
        spectrum_settings=None,
    )
    self.assertEqual(self.backend._get_read_stage_command(settings), "!READSTAGE TOP")
    settings.read_from_bottom = True
    self.assertEqual(self.backend._get_read_stage_command(settings), "!READSTAGE BOT")
    settings.read_mode = ReadMode.ABS
    self.assertIsNone(self.backend._get_read_stage_command(settings))

  def test_get_flashes_per_well_command(self):
    settings = MolecularDevicesSettings(
        plate=MagicMock(),
        read_mode=ReadMode.FLU,
        read_type=ReadType.ENDPOINT,
        read_order=ReadOrder.COLUMN,
        calibrate=Calibrate.ON,
        shake_settings=None,
        carriage_speed=CarriageSpeed.NORMAL,
        speed_read=False,
        flashes_per_well=10,
        kinetic_settings=None,
        spectrum_settings=None,
    )
    self.assertEqual(self.backend._get_flashes_per_well_command(settings), "!FPW 10")
    settings.read_mode = ReadMode.ABS
    self.assertIsNone(self.backend._get_flashes_per_well_command(settings))

  def test_get_pmt_commands(self):
    settings = MolecularDevicesSettings(
        plate=MagicMock(),
        read_mode=ReadMode.FLU,
        read_type=ReadType.ENDPOINT,
        read_order=ReadOrder.COLUMN,
        calibrate=Calibrate.ON,
        shake_settings=None,
        carriage_speed=CarriageSpeed.NORMAL,
        speed_read=False,
        pmt_gain=PmtGain.AUTO,
        kinetic_settings=None,
        spectrum_settings=None,
    )
    self.assertEqual(self.backend._get_pmt_commands(settings), ["!AUTOPMT ON"])
    settings.pmt_gain = PmtGain.HIGH
    self.assertEqual(self.backend._get_pmt_commands(settings), ["!AUTOPMT OFF", "!PMT HIGH"])
    settings.pmt_gain = 9
    self.assertEqual(self.backend._get_pmt_commands(settings), ["!AUTOPMT OFF", "!PMT 9"])
    settings.read_mode = ReadMode.ABS
    self.assertEqual(self.backend._get_pmt_commands(settings), [])

  def test_get_filter_commands(self):
    settings = MolecularDevicesSettings(
        plate=MagicMock(),
        read_mode=ReadMode.FLU,
        read_type=ReadType.ENDPOINT,
        read_order=ReadOrder.COLUMN,
        calibrate=Calibrate.ON,
        shake_settings=None,
        carriage_speed=CarriageSpeed.NORMAL,
        speed_read=False,
        cutoff_filters=[515, 530],
        kinetic_settings=None,
        spectrum_settings=None,
    )
    self.assertEqual(self.backend._get_filter_commands(settings), ["!AUTOFILTER OFF", "!EMFILTER 515 530"])
    settings.cutoff_filters = []
    self.assertEqual(self.backend._get_filter_commands(settings), [])
    settings.read_mode = ReadMode.ABS
    settings.cutoff_filters = [515, 530]
    self.assertEqual(self.backend._get_filter_commands(settings), [])

  def test_get_calibrate_command(self):
    settings = MolecularDevicesSettings(
        plate=MagicMock(),
        read_mode=ReadMode.ABS,
        read_type=ReadType.ENDPOINT,
        read_order=ReadOrder.COLUMN,
        calibrate=Calibrate.ON,
        shake_settings=None,
        carriage_speed=CarriageSpeed.NORMAL,
        speed_read=False,
        kinetic_settings=None,
        spectrum_settings=None,
    )
    self.assertEqual(self.backend._get_calibrate_command(settings), "!CALIBRATE ON")
    settings.read_mode = ReadMode.FLU
    self.assertEqual(self.backend._get_calibrate_command(settings), "!PMTCAL ON")

  def test_get_order_command(self):
    settings = MolecularDevicesSettings(
        plate=MagicMock(),
        read_mode=ReadMode.ABS,
        read_type=ReadType.ENDPOINT,
        read_order=ReadOrder.COLUMN,
        calibrate=Calibrate.ON,
        shake_settings=None,
        carriage_speed=CarriageSpeed.NORMAL,
        speed_read=False,
        kinetic_settings=None,
        spectrum_settings=None,
    )
    self.assertEqual(self.backend._get_order_command(settings), "!ORDER COLUMN")
    settings.read_order = ReadOrder.WAVELENGTH
    self.assertEqual(self.backend._get_order_command(settings), "!ORDER WAVELENGTH")

  def test_get_speed_command(self):
    settings = MolecularDevicesSettings(
        plate=MagicMock(),
        read_mode=ReadMode.ABS,
        read_type=ReadType.ENDPOINT,
        read_order=ReadOrder.COLUMN,
        calibrate=Calibrate.ON,
        shake_settings=None,
        carriage_speed=CarriageSpeed.NORMAL,
        speed_read=True,
        kinetic_settings=None,
        spectrum_settings=None,
    )
    self.assertEqual(self.backend._get_speed_command(settings), "!SPEED ON")
    settings.speed_read = False
    self.assertEqual(self.backend._get_speed_command(settings), "!SPEED OFF")
    settings.read_mode = ReadMode.FLU
    self.assertIsNone(self.backend._get_speed_command(settings))

  def test_get_integration_time_commands(self):
    settings = MolecularDevicesSettings(
        plate=MagicMock(),
        read_mode=ReadMode.TIME,
        read_type=ReadType.ENDPOINT,
        read_order=ReadOrder.COLUMN,
        calibrate=Calibrate.ON,
        shake_settings=None,
        carriage_speed=CarriageSpeed.NORMAL,
        speed_read=False,
        kinetic_settings=None,
        spectrum_settings=None,
    )
    self.assertEqual(self.backend._get_integration_time_commands(settings, 10, 100),
                      ["!COUNTTIMEDELAY 10", "!COUNTTIME 0.1"])
    settings.read_mode = ReadMode.ABS
    self.assertEqual(self.backend._get_integration_time_commands(settings, 10, 100), [])

  def test_parse_data(self):
    data_str = "1.0\t2.0\t3.0\r\n4.0\t5.0\t6.0\r\n"
    self.assertEqual(self.backend._parse_data(data_str), [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    data_str_space = "1.0 2.0 3.0\r\n4.0 5.0 6.0\r\n"
    self.assertEqual(self.backend._parse_data(data_str_space), [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])

  @patch("pylabrobot.plate_reading.molecularDevices_backend.MolecularDevicesBackend._wait_for_idle", new_callable=AsyncMock)
  @patch("pylabrobot.plate_reading.molecularDevices_backend.MolecularDevicesBackend._transfer_data", new_callable=AsyncMock, return_value="")
  @patch("pylabrobot.plate_reading.molecularDevices_backend.MolecularDevicesBackend._read_now", new_callable=AsyncMock)
  @patch("pylabrobot.plate_reading.molecularDevices_backend.MolecularDevicesBackend._send_commands", new_callable=AsyncMock)
  def test_read_absorbance(self, mock_send_commands, mock_read_now, mock_transfer_data, mock_wait_for_idle):
    plate = AGenBio_96_wellplate_Ub_2200ul("test_plate")
    asyncio.run(self.backend.read_absorbance(plate, [500]))
    mock_send_commands.assert_called_once()
    commands = mock_send_commands.call_args[0][0]
    self.assertIn("!CLEAR DATA", commands)
    self.assertIn("!STRIP 1 12", commands)
    self.assertIn("!CSPEED 8", commands)
    self.assertIn("!SHAKE OFF", commands)
    self.assertIn("!WAVELENGTH 500", commands)
    self.assertIn("!CALIBRATE ONCE", commands)
    self.assertIn("!MODE ENDPOINT", commands)
    self.assertIn("!ORDER COLUMN", commands)
    self.assertIn("!SPEED OFF", commands)
    self.assertIn(("!READTYPE ABSPLA", 2), commands)
    mock_read_now.assert_called_once()
    mock_wait_for_idle.assert_called_once()
    mock_transfer_data.assert_called_once()

  @patch("pylabrobot.plate_reading.molecularDevices_backend.MolecularDevicesBackend._wait_for_idle", new_callable=AsyncMock)
  @patch("pylabrobot.plate_reading.molecularDevices_backend.MolecularDevicesBackend._transfer_data", new_callable=AsyncMock, return_value="")
  @patch("pylabrobot.plate_reading.molecularDevices_backend.MolecularDevicesBackend._read_now", new_callable=AsyncMock)
  @patch("pylabrobot.plate_reading.molecularDevices_backend.MolecularDevicesBackend._send_commands", new_callable=AsyncMock)
  def test_read_fluorescence(self, mock_send_commands, mock_read_now, mock_transfer_data, mock_wait_for_idle):
    plate = AGenBio_96_wellplate_Ub_2200ul("test_plate")
    asyncio.run(self.backend.read_fluorescence(plate, [485], [520], [515]))
    mock_send_commands.assert_called_once()
    commands = mock_send_commands.call_args[0][0]
    self.assertIn("!CLEAR DATA", commands)
    self.assertTrue(any(isinstance(cmd, str) and cmd.startswith("!XPOS") for cmd in commands))
    self.assertTrue(any(isinstance(cmd, str) and cmd.startswith("!YPOS") for cmd in commands))
    self.assertIn("!STRIP 1 12", commands)
    self.assertIn("!CSPEED 8", commands)
    self.assertIn("!SHAKE OFF", commands)
    self.assertIn("!FPW 10", commands)
    self.assertIn("!AUTOPMT ON", commands)
    self.assertIn("!EXWAVELENGTH 485", commands)
    self.assertIn("!EMWAVELENGTH 520", commands)
    self.assertIn("!AUTOFILTER OFF", commands)
    self.assertIn("!EMFILTER 515", commands)
    self.assertIn("!PMTCAL ONCE", commands)
    self.assertIn("!MODE ENDPOINT", commands)
    self.assertIn("!ORDER COLUMN", commands)
    self.assertIn(("!READTYPE FLU", 1), commands)
    mock_read_now.assert_called_once()
    mock_wait_for_idle.assert_called_once()
    mock_transfer_data.assert_called_once()

if __name__ == "__main__":
  unittest.main()