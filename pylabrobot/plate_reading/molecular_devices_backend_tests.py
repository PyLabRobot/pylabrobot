import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import math

from pylabrobot.resources.agenbio.plates import AGenBio_96_wellplate_Ub_2200ul

from pylabrobot.plate_reading.molecular_devices_backend import (
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
    MolecularDevicesDataCollectionAbsorbance,
    MolecularDevicesDataAbsorbance,
    MolecularDevicesDataCollectionFluorescence,
    MolecularDevicesDataFluorescence,
    MolecularDevicesDataCollectionLuminescence,
    MolecularDevicesDataLuminescence,
    MolecularDevicesError,
    MolecularDevicesUnrecognizedCommandError,
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
    self.assertEqual(self.backend._get_mode_command(settings), "!MODE SPECTRUM 200 10 50")

    settings.spectrum_settings.excitation_emission_type = "EXSPECTRUM"
    self.assertEqual(self.backend._get_mode_command(settings), "!MODE EXSPECTRUM 200 10 50")

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
        cutoff_filters=[self.backend._get_cutoff_filter_index_from_wavelength(535), 9],
        kinetic_settings=None,
        spectrum_settings=None,
    )
    self.assertEqual(self.backend._get_filter_commands(settings), ["!AUTOFILTER OFF", "!EMFILTER 8 9"])
    settings.cutoff_filters = []
    self.assertEqual(self.backend._get_filter_commands(settings), ["!AUTOFILTER ON"])
    settings.read_mode = ReadMode.ABS
    settings.cutoff_filters = [515, 530]
    self.assertEqual(self.backend._get_filter_commands(settings), ['!AUTOFILTER ON'])

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

class Test_get_nvram_and_tag_commands(unittest.TestCase):
  def setUp(self):
    self.mock_serial = MagicMock()
    self.mock_serial.setup = AsyncMock()
    self.mock_serial.stop = AsyncMock()
    self.mock_serial.write = AsyncMock()
    self.mock_serial.readline = AsyncMock(return_value=b"OK>\r\n")

    with patch("pylabrobot.io.serial.Serial", return_value=self.mock_serial):
      self.backend = MolecularDevicesBackend(port="COM1")
      self.backend.io = self.mock_serial

  def test_get_nvram_commands_polar(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode=ReadMode.POLAR,
      read_type=ReadType.ENDPOINT,
      read_order=ReadOrder.COLUMN,
      calibrate=Calibrate.ON,
      shake_settings=None,
      carriage_speed=CarriageSpeed.NORMAL,
      speed_read=False,
      kinetic_settings=None,
      spectrum_settings=None,
      settling_time=5,
      is_settling_time_on=True
    )
    self.assertEqual(self.backend._get_nvram_commands(settings), ["!NVRAM FPSETTLETIME 5"])
    settings.is_settling_time_on = False
    self.assertEqual(self.backend._get_nvram_commands(settings), ["!NVRAM FPSETTLETIME 0"])

  def test_get_nvram_commands_other(self):
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
      settling_time=10,
      is_settling_time_on=True
    )
    self.assertEqual(self.backend._get_nvram_commands(settings), ["!NVRAM CARCOL 10"])
    settings.is_settling_time_on = False
    self.assertEqual(self.backend._get_nvram_commands(settings), ["!NVRAM CARCOL 100"])

  def test_get_tag_command(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode=ReadMode.POLAR,
      read_type=ReadType.KINETIC,
      read_order=ReadOrder.COLUMN,
      calibrate=Calibrate.ON,
      shake_settings=None,
      carriage_speed=CarriageSpeed.NORMAL,
      speed_read=False,
      kinetic_settings=KineticSettings(interval=10, num_readings=5),
      spectrum_settings=None,
    )
    self.assertEqual(self.backend._get_tag_command(settings), "!TAG ON")
    settings.read_type = ReadType.ENDPOINT
    self.assertEqual(self.backend._get_tag_command(settings), "!TAG OFF")
    settings.read_mode = ReadMode.ABS
    settings.read_type = ReadType.KINETIC
    self.assertEqual(self.backend._get_tag_command(settings), "!TAG OFF")

  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._wait_for_idle", new_callable=AsyncMock)
  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._transfer_data", new_callable=AsyncMock, return_value="")
  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._read_now", new_callable=AsyncMock)
  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._send_commands", new_callable=AsyncMock)
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

  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._wait_for_idle", new_callable=AsyncMock)
  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._transfer_data", new_callable=AsyncMock, return_value="")
  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._read_now", new_callable=AsyncMock)
  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._send_commands", new_callable=AsyncMock)
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
    self.assertIn('!READSTAGE TOP', commands)
    mock_read_now.assert_called_once()
    mock_wait_for_idle.assert_called_once()
    mock_transfer_data.assert_called_once()

  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._wait_for_idle", new_callable=AsyncMock)
  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._transfer_data", new_callable=AsyncMock, return_value="")
  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._read_now", new_callable=AsyncMock)
  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._send_commands", new_callable=AsyncMock)
  def test_read_luminescence(self, mock_send_commands, mock_read_now, mock_transfer_data, mock_wait_for_idle):
    plate = AGenBio_96_wellplate_Ub_2200ul("test_plate")
    asyncio.run(self.backend.read_luminescence(plate, [590]))
    mock_send_commands.assert_called_once()
    commands = mock_send_commands.call_args[0][0]
    self.assertIn("!CLEAR DATA", commands)
    self.assertTrue(any(isinstance(cmd, str) and cmd.startswith("!XPOS") for cmd in commands))
    self.assertTrue(any(isinstance(cmd, str) and cmd.startswith("!YPOS") for cmd in commands))
    self.assertIn("!STRIP 1 12", commands)
    self.assertIn("!CSPEED 8", commands)
    self.assertIn("!SHAKE OFF", commands)
    self.assertIn("!EMWAVELENGTH 590", commands)
    self.assertIn("!PMTCAL ONCE", commands)
    self.assertIn("!MODE ENDPOINT", commands)
    self.assertIn("!ORDER COLUMN", commands)
    self.assertIn(("!READTYPE LUM", 1), commands)
    self.assertIn('!READSTAGE TOP', commands)
    mock_read_now.assert_called_once()
    mock_wait_for_idle.assert_called_once()
    mock_transfer_data.assert_called_once()

  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._wait_for_idle", new_callable=AsyncMock)
  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._transfer_data", new_callable=AsyncMock, return_value="")
  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._read_now", new_callable=AsyncMock)
  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._send_commands", new_callable=AsyncMock)
  def test_read_fluorescence_polarization(self, mock_send_commands, mock_read_now, mock_transfer_data, mock_wait_for_idle):
    plate = AGenBio_96_wellplate_Ub_2200ul("test_plate")
    asyncio.run(self.backend.read_fluorescence_polarization(plate, [485], [520], [515]))
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
    self.assertIn(("!READTYPE POLAR", 1), commands)
    self.assertIn('!READSTAGE TOP', commands)
    mock_read_now.assert_called_once()
    mock_wait_for_idle.assert_called_once()
    mock_transfer_data.assert_called_once()

  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._wait_for_idle", new_callable=AsyncMock)
  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._transfer_data", new_callable=AsyncMock, return_value="")
  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._read_now", new_callable=AsyncMock)
  @patch("pylabrobot.plate_reading.molecular_devices_backend.MolecularDevicesBackend._send_commands", new_callable=AsyncMock)
  def test_read_time_resolved_fluorescence(self, mock_send_commands, mock_read_now, mock_transfer_data, mock_wait_for_idle):
    plate = AGenBio_96_wellplate_Ub_2200ul("test_plate")
    asyncio.run(self.backend.read_time_resolved_fluorescence(plate, [485], [520], [515], delay_time=10, integration_time=100))
    mock_send_commands.assert_called_once()
    commands = mock_send_commands.call_args[0][0]
    self.assertIn("!CLEAR DATA", commands)
    self.assertTrue(any(isinstance(cmd, str) and cmd.startswith("!XPOS") for cmd in commands))
    self.assertTrue(any(isinstance(cmd, str) and cmd.startswith("!YPOS") for cmd in commands))
    self.assertIn("!STRIP 1 12", commands)
    self.assertIn("!CSPEED 8", commands)
    self.assertIn("!SHAKE OFF", commands)
    self.assertIn("!FPW 50", commands)
    self.assertIn("!AUTOPMT ON", commands)
    self.assertIn("!EXWAVELENGTH 485", commands)
    self.assertIn("!EMWAVELENGTH 520", commands)
    self.assertIn("!AUTOFILTER OFF", commands)
    self.assertIn("!EMFILTER 515", commands)
    self.assertIn("!PMTCAL ONCE", commands)
    self.assertIn("!MODE ENDPOINT", commands)
    self.assertIn("!ORDER COLUMN", commands)
    self.assertIn("!COUNTTIMEDELAY 10", commands)
    self.assertIn("!COUNTTIME 0.1", commands)
    self.assertIn(("!READTYPE TIME 0 250", 1), commands)
    self.assertIn('!READSTAGE TOP', commands)
    mock_read_now.assert_called_once()
    mock_wait_for_idle.assert_called_once()
    mock_transfer_data.assert_called_once()

class TestDataParsing(unittest.TestCase):
  def setUp(self):
    with patch("pylabrobot.io.serial.Serial", return_value=MagicMock()):
      self.backend = MolecularDevicesBackend(port="COM1")

  def test_parse_absorbance_single_wavelength(self):
    data_str = """
    12345.6\t25.1\t96-well
    L:\t260
    L:\t260
    1:\t0.1\t0.2
    2:\t0.3\t0.4
    """
    result = self.backend._parse_data(data_str)
    self.assertIsInstance(result, MolecularDevicesDataCollectionAbsorbance)
    self.assertEqual(result.container_type, "96-well")
    self.assertEqual(result.all_absorbance_wavelengths, [260])
    self.assertEqual(len(result.reads), 1)
    read = result.reads[0]
    self.assertIsInstance(read, MolecularDevicesDataAbsorbance)
    self.assertEqual(read.measurement_time, 12345.6)
    self.assertEqual(read.temperature, 25.1)
    self.assertEqual(read.absorbance_wavelength, 260)
    self.assertEqual(read.data, [[0.1, 0.3], [0.2, 0.4]])

  def test_parse_absorbance_multiple_wavelengths(self):
    data_str = """
    12345.6\t25.1\t96-well
    L:\t260\t280
    L:\t260
    1:\t0.1\t0.2
    2:\t0.3\t0.4
    L:\t280
    1:\t0.5\t0.6
    2:\t0.7\t0.8
    """
    result = self.backend._parse_data(data_str)
    self.assertIsInstance(result, MolecularDevicesDataCollectionAbsorbance)
    self.assertEqual(result.all_absorbance_wavelengths, [260, 280])
    self.assertEqual(len(result.reads), 2)
    read1 = result.reads[0]
    self.assertEqual(read1.absorbance_wavelength, 260)
    self.assertEqual(read1.data, [[0.1, 0.3], [0.2, 0.4]])
    read2 = result.reads[1]
    self.assertEqual(read2.absorbance_wavelength, 280)
    self.assertEqual(read2.data, [[0.5, 0.7], [0.6, 0.8]])

  def test_parse_fluorescence(self):
    data_str = """
    12345.6\t25.1\t96-well
    exL:\t485
    emL:\t520
    L:\t485\t520
    1:\t100\t200
    2:\t300\t400
    """
    result = self.backend._parse_data(data_str)
    self.assertIsInstance(result, MolecularDevicesDataCollectionFluorescence)
    self.assertEqual(result.all_excitation_wavelengths, [485])
    self.assertEqual(result.all_emission_wavelengths, [520])
    self.assertEqual(len(result.reads), 1)
    read = result.reads[0]
    self.assertIsInstance(read, MolecularDevicesDataFluorescence)
    self.assertEqual(read.excitation_wavelength, 485)
    self.assertEqual(read.emission_wavelength, 520)
    self.assertEqual(read.data, [[100.0, 300.0], [200.0, 400.0]])

  def test_parse_luminescence(self):
    data_str = """
    12345.6\t25.1\t96-well
    emL:\t590
    L:\t\t590
    1:\t1000\t2000
    2:\t3000\t4000
    """
    result = self.backend._parse_data(data_str)
    self.assertIsInstance(result, MolecularDevicesDataCollectionLuminescence)
    self.assertEqual(result.all_emission_wavelengths, [590])
    self.assertEqual(len(result.reads), 1)
    read = result.reads[0]
    self.assertIsInstance(read, MolecularDevicesDataLuminescence)
    self.assertEqual(read.emission_wavelength, 590)
    self.assertEqual(read.data, [[1000.0, 3000.0], [2000.0, 4000.0]])

  def test_parse_data_with_sat_and_nan(self):
    data_str = """
    12345.6\t25.1\t96-well
    L:\t260
    L:\t260
    1:\t0.1\t#SAT
    2:\t0.3\t-
    """
    result = self.backend._parse_data(data_str)
    read = result.reads[0]
    self.assertEqual(read.data[1][0], float('inf'))
    self.assertTrue(math.isnan(read.data[1][1]))

  def test_parse_kinetic_absorbance(self):
    # Mock the send_command to return two different data blocks
    def data_generator():
      yield ["OK", """
    12345.6\t25.1\t96-well
    L:\t260
    L:\t260
    1:\t0.1\t0.2
    2:\t0.3\t0.4
    """]
      yield ["OK", """
    12355.6\t25.2\t96-well
    L:\t260
    L:\t260
    1:\t0.15\t0.25
    2:\t0.35\t0.45
    """]

    self.backend.send_command = AsyncMock(side_effect=data_generator())

    settings = MolecularDevicesSettings(
        plate=MagicMock(),
        read_mode=ReadMode.ABS,
        read_type=ReadType.KINETIC,
        kinetic_settings=KineticSettings(interval=10, num_readings=2),
        read_order=ReadOrder.COLUMN,
        calibrate=Calibrate.ON,
        shake_settings=None,
        carriage_speed=CarriageSpeed.NORMAL,
        speed_read=False,
        spectrum_settings=None,
    )

    result = asyncio.run(self.backend._transfer_data(settings))
    self.assertEqual(len(result.reads), 2)
    self.assertEqual(result.reads[0].data, [[0.1, 0.3], [0.2, 0.4]])
    self.assertEqual(result.reads[1].data, [[0.15, 0.35], [0.25, 0.45]])
    self.assertEqual(result.reads[0].measurement_time, 12345.6)
    self.assertEqual(result.reads[1].measurement_time, 12355.6)


class TestErrorHandling(unittest.TestCase):
  def setUp(self):
    self.mock_serial = MagicMock()
    self.mock_serial.setup = AsyncMock()
    self.mock_serial.stop = AsyncMock()
    self.mock_serial.write = AsyncMock()
    self.mock_serial.readline = AsyncMock()

    with patch("pylabrobot.io.serial.Serial", return_value=self.mock_serial):
      self.backend = MolecularDevicesBackend(port="/dev/tty01")
      self.backend.io = self.mock_serial

  async def _mock_send_command_response(self, response_str: str):
    self.mock_serial.readline.side_effect = [response_str.encode() + b">\r\n"]
    return await self.backend.send_command("!TEST")

  def test_parse_basic_errors_fail_known_error_code(self):
    # Test a known error code (e.g., 107: no data to transfer)
    with self.assertRaisesRegex(MolecularDevicesUnrecognizedCommandError, "Command '!TEST' failed with error 107: no data to transfer"):
      asyncio.run(self._mock_send_command_response("OK\t\r\n>FAIL\t 107"))

  def test_parse_basic_errors_fail_unknown_error_code(self):
    # Test an unknown error code
    with self.assertRaisesRegex(MolecularDevicesError, "Command '!TEST' failed with unknown error code: 999"):
      asyncio.run(self._mock_send_command_response("FAIL\t 999"))

  def test_parse_basic_errors_fail_unparsable_error(self):
    # Test an unparsable error message (e.g., not an integer code)
    with self.assertRaisesRegex(MolecularDevicesError, "Command '!TEST' failed with unparsable error: FAIL\t ABC"):
      asyncio.run(self._mock_send_command_response("FAIL\t ABC"))

  def test_parse_basic_errors_empty_response(self):
    # Test an empty response from the device
    self.mock_serial.readline.return_value = b"" # Simulate no response
    with self.assertRaisesRegex(TimeoutError, "Timeout waiting for response to command: !TEST"):
      asyncio.run(self.backend.send_command("!TEST", timeout=0.01)) # Short timeout for test

  def test_parse_basic_errors_warning_response(self):
    # Test a response containing a warning
    self.mock_serial.readline.side_effect = [b"OK\tWarning: Something happened>\r\n"]
    # Expect no exception, but a warning logged (not directly testable with assertRaises)
    # We can assert that no error is raised.
    try:
      asyncio.run(self.backend.send_command("!TEST"))
    except MolecularDevicesError:
      self.fail("MolecularDevicesError raised for a warning response")

  def test_parse_basic_errors_ok_response(self):
    # Test a normal OK response
    self.mock_serial.readline.side_effect = [b"OK>\r\n"]
    try:
      response = asyncio.run(self.backend.send_command("!TEST"))
      self.assertEqual(response, ["OK"])
    except MolecularDevicesError:
      self.fail("MolecularDevicesError raised for a valid OK response")


if __name__ == "__main__":
  unittest.main()
