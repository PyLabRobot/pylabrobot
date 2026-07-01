import math
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

from pylabrobot.plate_reading.molecular_devices.backend import (
  Calibrate,
  CarriageSpeed,
  KineticSettings,
  MolecularDevicesBackend,
  MolecularDevicesError,
  MolecularDevicesSettings,
  MolecularDevicesUnrecognizedCommandError,
  PmtGain,
  ReadMode,
  ReadOrder,
  ReadType,
  ShakeSettings,
  SpectrumSettings,
)
from pylabrobot.plate_reading.molecular_devices.spectramax_gemini_em_backend import (
  MolecularDevicesSpectraMaxGeminiEMBackend,
)
from pylabrobot.resources.agenbio.plates import agenbio_96_wellplate_Ub_2200uL


class TestMolecularDevicesBackend(unittest.IsolatedAsyncioTestCase):
  backend: MolecularDevicesBackend
  mock_serial: MagicMock
  send_command_mock: AsyncMock

  def setUp(self):
    self.mock_serial = MagicMock()
    self.mock_serial.setup = AsyncMock()
    self.mock_serial.stop = AsyncMock()
    self.mock_serial.write = AsyncMock()
    self.mock_serial.readline = AsyncMock(return_value=b"OK>\r\n")

    with patch("pylabrobot.io.serial.Serial", return_value=self.mock_serial):
      self.backend = MolecularDevicesBackend(port="COM1")
      self.backend.io = self.mock_serial
      self.send_command_mock = patch.object(
        self.backend, "send_command", new_callable=AsyncMock
      ).start()
    self.addCleanup(patch.stopall)

  async def test_setup_stop(self):
    # un-mock send_command for this test
    with patch.object(
      self.backend, "send_command", wraps=self.backend.send_command
    ) as wrapped_send_command:
      await self.backend.setup()
      self.mock_serial.setup.assert_called_once()
      wrapped_send_command.assert_called_with("!")
      await self.backend.stop()
      self.mock_serial.stop.assert_called_once()

  async def test_set_clear(self):
    await self.backend._set_clear()
    self.send_command_mock.assert_called_once_with("!CLEAR DATA")

  async def test_set_mode(self):
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
    await self.backend._set_mode(settings)
    self.send_command_mock.assert_called_once_with("!MODE ENDPOINT")

    self.send_command_mock.reset_mock()
    settings.read_type = ReadType.KINETIC
    settings.kinetic_settings = KineticSettings(interval=10, num_readings=5)
    await self.backend._set_mode(settings)
    self.send_command_mock.assert_called_once_with("!MODE KINETIC 10 5")

    self.send_command_mock.reset_mock()
    settings.read_type = ReadType.SPECTRUM
    settings.spectrum_settings = SpectrumSettings(start_wavelength=200, step=10, num_steps=50)
    await self.backend._set_mode(settings)
    self.send_command_mock.assert_called_once_with("!MODE SPECTRUM 200 10 50")

    self.send_command_mock.reset_mock()
    settings.spectrum_settings.excitation_emission_type = "EXSPECTRUM"
    await self.backend._set_mode(settings)
    self.send_command_mock.assert_called_once_with("!MODE EXSPECTRUM 200 10 50")

  async def test_set_wavelengths(self):
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
    await self.backend._set_wavelengths(settings)
    self.send_command_mock.assert_called_once_with("!WAVELENGTH 500 F600")

    self.send_command_mock.reset_mock()
    settings.path_check = True
    await self.backend._set_wavelengths(settings)
    self.send_command_mock.assert_called_once_with("!WAVELENGTH 500 F600 900 998")

    self.send_command_mock.reset_mock()
    settings.read_mode = ReadMode.FLU
    settings.excitation_wavelengths = [485]
    settings.emission_wavelengths = [520]
    await self.backend._set_wavelengths(settings)
    self.send_command_mock.assert_has_calls([call("!EXWAVELENGTH 485"), call("!EMWAVELENGTH 520")])

    self.send_command_mock.reset_mock()
    settings.read_mode = ReadMode.LUM
    settings.emission_wavelengths = [590]
    await self.backend._set_wavelengths(settings)
    self.send_command_mock.assert_called_once_with("!EMWAVELENGTH 590")

  async def test_set_plate_position(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
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
    await self.backend._set_plate_position(settings)
    self.send_command_mock.assert_has_calls(
      [call("!XPOS 13.380 9.000 12"), call("!YPOS 12.240 9.000 8")]
    )

  async def test_set_strip(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
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
    await self.backend._set_strip(settings)
    self.send_command_mock.assert_called_once_with("!STRIP 1 12")

  async def test_set_shake(self):
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
    await self.backend._set_shake(settings)
    self.send_command_mock.assert_called_once_with("!SHAKE OFF")

    self.send_command_mock.reset_mock()
    settings.shake_settings = ShakeSettings(before_read=True, before_read_duration=5)
    await self.backend._set_shake(settings)
    self.send_command_mock.assert_has_calls([call("!SHAKE ON"), call("!SHAKE 5 0 0 0 0")])

    self.send_command_mock.reset_mock()
    settings.shake_settings = ShakeSettings(between_reads=True, between_reads_duration=3)
    settings.kinetic_settings = KineticSettings(interval=10, num_readings=5)
    await self.backend._set_shake(settings)
    self.send_command_mock.assert_has_calls([call("!SHAKE ON"), call("!SHAKE 0 10 7 3 0")])

  async def test_set_carriage_speed(self):
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
    await self.backend._set_carriage_speed(settings)
    self.send_command_mock.assert_called_once_with("!CSPEED 8")
    self.send_command_mock.reset_mock()
    settings.carriage_speed = CarriageSpeed.SLOW
    await self.backend._set_carriage_speed(settings)
    self.send_command_mock.assert_called_once_with("!CSPEED 1")

  async def test_set_read_stage(self):
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
    await self.backend._set_read_stage(settings)
    self.send_command_mock.assert_called_once_with("!READSTAGE TOP")
    self.send_command_mock.reset_mock()
    settings.read_from_bottom = True
    await self.backend._set_read_stage(settings)
    self.send_command_mock.assert_called_once_with("!READSTAGE BOT")
    self.send_command_mock.reset_mock()
    settings.read_mode = ReadMode.ABS
    await self.backend._set_read_stage(settings)
    self.send_command_mock.assert_not_called()

  async def test_set_flashes_per_well(self):
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
    await self.backend._set_flashes_per_well(settings)
    self.send_command_mock.assert_called_once_with("!FPW 10")
    self.send_command_mock.reset_mock()
    settings.read_mode = ReadMode.ABS
    await self.backend._set_flashes_per_well(settings)
    self.send_command_mock.assert_not_called()

  async def test_set_pmt(self):
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
    await self.backend._set_pmt(settings)
    self.send_command_mock.assert_called_once_with("!AUTOPMT ON")
    self.send_command_mock.reset_mock()
    settings.pmt_gain = PmtGain.HIGH
    await self.backend._set_pmt(settings)
    self.send_command_mock.assert_has_calls([call("!AUTOPMT OFF"), call("!PMT HIGH")])
    self.send_command_mock.reset_mock()
    settings.pmt_gain = 9
    await self.backend._set_pmt(settings)
    self.send_command_mock.assert_has_calls([call("!AUTOPMT OFF"), call("!PMT 9")])
    self.send_command_mock.reset_mock()
    settings.read_mode = ReadMode.ABS
    await self.backend._set_pmt(settings)
    self.send_command_mock.assert_not_called()

  async def test_set_filter(self):
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
    await self.backend._set_filter(settings)
    self.send_command_mock.assert_has_calls([call("!AUTOFILTER OFF"), call("!EMFILTER 8 9")])
    self.send_command_mock.reset_mock()
    settings.cutoff_filters = []
    await self.backend._set_filter(settings)
    self.send_command_mock.assert_called_once_with("!AUTOFILTER ON")
    self.send_command_mock.reset_mock()
    settings.read_mode = ReadMode.ABS
    settings.cutoff_filters = [515, 530]
    await self.backend._set_filter(settings)
    self.send_command_mock.assert_called_once_with("!AUTOFILTER ON")

  async def test_set_calibrate(self):
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
    await self.backend._set_calibrate(settings)
    self.send_command_mock.assert_called_once_with("!CALIBRATE ON")
    self.send_command_mock.reset_mock()
    settings.read_mode = ReadMode.FLU
    await self.backend._set_calibrate(settings)
    self.send_command_mock.assert_called_once_with("!PMTCAL ON")

  async def test_set_order(self):
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
    await self.backend._set_order(settings)
    self.send_command_mock.assert_called_once_with("!ORDER COLUMN")
    self.send_command_mock.reset_mock()
    settings.read_order = ReadOrder.WAVELENGTH
    await self.backend._set_order(settings)
    self.send_command_mock.assert_called_once_with("!ORDER WAVELENGTH")

  async def test_set_speed(self):
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
    await self.backend._set_speed(settings)
    self.send_command_mock.assert_called_once_with("!SPEED ON")
    self.send_command_mock.reset_mock()
    settings.speed_read = False
    await self.backend._set_speed(settings)
    self.send_command_mock.assert_called_once_with("!SPEED OFF")
    self.send_command_mock.reset_mock()
    settings.read_mode = ReadMode.FLU
    await self.backend._set_speed(settings)
    self.send_command_mock.assert_not_called()

  async def test_set_integration_time(self):
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
    await self.backend._set_integration_time(settings, 10, 100)
    self.send_command_mock.assert_has_calls([call("!COUNTTIMEDELAY 10"), call("!COUNTTIME 0.1")])
    self.send_command_mock.reset_mock()
    settings.read_mode = ReadMode.ABS
    await self.backend._set_integration_time(settings, 10, 100)
    self.send_command_mock.assert_not_called()

  async def test_set_nvram_polar(self):
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
    )
    await self.backend._set_nvram(settings)
    self.send_command_mock.assert_called_once_with("!NVRAM FPSETTLETIME 5")

  async def test_set_nvram_other(self):
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
    )
    await self.backend._set_nvram(settings)
    self.send_command_mock.assert_called_once_with("!NVRAM CARCOL 100")
    self.send_command_mock.reset_mock()
    settings.settling_time = 110
    await self.backend._set_nvram(settings)
    self.send_command_mock.assert_called_once_with("!NVRAM CARCOL 110")

  async def test_set_tag(self):
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
    await self.backend._set_tag(settings)
    self.send_command_mock.assert_called_once_with("!TAG ON")
    self.send_command_mock.reset_mock()
    settings.read_type = ReadType.ENDPOINT
    await self.backend._set_tag(settings)
    self.send_command_mock.assert_called_once_with("!TAG OFF")
    self.send_command_mock.reset_mock()
    settings.read_mode = ReadMode.ABS
    settings.read_type = ReadType.KINETIC
    await self.backend._set_tag(settings)
    self.send_command_mock.assert_called_once_with("!TAG OFF")

  @patch(
    "pylabrobot.plate_reading.molecular_devices.backend.MolecularDevicesBackend._wait_for_idle",
    new_callable=AsyncMock,
  )
  @patch(
    "pylabrobot.plate_reading.molecular_devices.backend.MolecularDevicesBackend._transfer_data",
    new_callable=AsyncMock,
    return_value="",
  )
  @patch(
    "pylabrobot.plate_reading.molecular_devices.backend.MolecularDevicesBackend._read_now",
    new_callable=AsyncMock,
  )
  async def test_read_absorbance(self, mock_read_now, mock_transfer_data, mock_wait_for_idle):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    await self.backend.read_absorbance(plate, [500])

    commands = [c.args[0] for c in self.send_command_mock.call_args_list]
    self.assertIn("!CLEAR DATA", commands)
    self.assertIn("!STRIP 1 12", commands)
    self.assertIn("!CSPEED 8", commands)
    self.assertIn("!SHAKE OFF", commands)
    self.assertIn("!WAVELENGTH 500", commands)
    self.assertIn("!CALIBRATE ONCE", commands)
    self.assertIn("!MODE ENDPOINT", commands)
    self.assertIn("!ORDER COLUMN", commands)
    self.assertIn("!SPEED OFF", commands)

    readtype_call = next(
      c for c in self.send_command_mock.call_args_list if c.args[0] == "!READTYPE ABSPLA"
    )
    self.assertEqual(readtype_call.kwargs, {"num_res_fields": 2})

    mock_read_now.assert_called_once()
    mock_wait_for_idle.assert_called_once()
    mock_transfer_data.assert_called_once()

  @patch(
    "pylabrobot.plate_reading.molecular_devices.backend.MolecularDevicesBackend._wait_for_idle",
    new_callable=AsyncMock,
  )
  @patch(
    "pylabrobot.plate_reading.molecular_devices.backend.MolecularDevicesBackend._transfer_data",
    new_callable=AsyncMock,
    return_value="",
  )
  @patch(
    "pylabrobot.plate_reading.molecular_devices.backend.MolecularDevicesBackend._read_now",
    new_callable=AsyncMock,
  )
  async def test_read_fluorescence(self, mock_read_now, mock_transfer_data, mock_wait_for_idle):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    await self.backend.read_fluorescence(plate, [485], [520], [515])

    commands = [c.args[0] for c in self.send_command_mock.call_args_list]
    self.assertIn("!CLEAR DATA", commands)
    self.assertTrue(any(cmd.startswith("!XPOS") for cmd in commands))
    self.assertTrue(any(cmd.startswith("!YPOS") for cmd in commands))
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
    self.assertIn("!READSTAGE TOP", commands)

    readtype_call = next(
      c for c in self.send_command_mock.call_args_list if c.args[0] == "!READTYPE FLU"
    )
    self.assertEqual(readtype_call.kwargs, {"num_res_fields": 1})

    mock_read_now.assert_called_once()
    mock_wait_for_idle.assert_called_once()
    mock_transfer_data.assert_called_once()

  @patch(
    "pylabrobot.plate_reading.molecular_devices.backend.MolecularDevicesBackend._wait_for_idle",
    new_callable=AsyncMock,
  )
  @patch(
    "pylabrobot.plate_reading.molecular_devices.backend.MolecularDevicesBackend._transfer_data",
    new_callable=AsyncMock,
    return_value="",
  )
  @patch(
    "pylabrobot.plate_reading.molecular_devices.backend.MolecularDevicesBackend._read_now",
    new_callable=AsyncMock,
  )
  async def test_read_luminescence(self, mock_read_now, mock_transfer_data, mock_wait_for_idle):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    await self.backend.read_luminescence(plate, [590])

    commands = [c.args[0] for c in self.send_command_mock.call_args_list]
    self.assertIn("!CLEAR DATA", commands)
    self.assertTrue(any(cmd.startswith("!XPOS") for cmd in commands))
    self.assertTrue(any(cmd.startswith("!YPOS") for cmd in commands))
    self.assertIn("!STRIP 1 12", commands)
    self.assertIn("!CSPEED 8", commands)
    self.assertIn("!SHAKE OFF", commands)
    self.assertIn("!EMWAVELENGTH 590", commands)
    self.assertIn("!PMTCAL ONCE", commands)
    self.assertIn("!MODE ENDPOINT", commands)
    self.assertIn("!ORDER COLUMN", commands)
    self.assertIn("!READSTAGE TOP", commands)

    readtype_call = next(
      c for c in self.send_command_mock.call_args_list if c.args[0] == "!READTYPE LUM"
    )
    self.assertEqual(readtype_call.kwargs, {"num_res_fields": 1})

    mock_read_now.assert_called_once()
    mock_wait_for_idle.assert_called_once()
    mock_transfer_data.assert_called_once()

  @patch(
    "pylabrobot.plate_reading.molecular_devices.backend.MolecularDevicesBackend._wait_for_idle",
    new_callable=AsyncMock,
  )
  @patch(
    "pylabrobot.plate_reading.molecular_devices.backend.MolecularDevicesBackend._transfer_data",
    new_callable=AsyncMock,
    return_value="",
  )
  @patch(
    "pylabrobot.plate_reading.molecular_devices.backend.MolecularDevicesBackend._read_now",
    new_callable=AsyncMock,
  )
  async def test_read_fluorescence_polarization(
    self,
    mock_read_now,
    mock_transfer_data,
    mock_wait_for_idle,
  ):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    await self.backend.read_fluorescence_polarization(plate, [485], [520], [515])

    commands = [c.args[0] for c in self.send_command_mock.call_args_list]
    self.assertIn("!CLEAR DATA", commands)
    self.assertTrue(any(cmd.startswith("!XPOS") for cmd in commands))
    self.assertTrue(any(cmd.startswith("!YPOS") for cmd in commands))
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
    self.assertIn("!READSTAGE TOP", commands)

    readtype_call = next(
      c for c in self.send_command_mock.call_args_list if c.args[0] == "!READTYPE POLAR"
    )
    self.assertEqual(readtype_call.kwargs, {"num_res_fields": 1})

    mock_read_now.assert_called_once()
    mock_wait_for_idle.assert_called_once()
    mock_transfer_data.assert_called_once()

  @patch(
    "pylabrobot.plate_reading.molecular_devices.backend.MolecularDevicesBackend._wait_for_idle",
    new_callable=AsyncMock,
  )
  @patch(
    "pylabrobot.plate_reading.molecular_devices.backend.MolecularDevicesBackend._transfer_data",
    new_callable=AsyncMock,
    return_value="",
  )
  @patch(
    "pylabrobot.plate_reading.molecular_devices.backend.MolecularDevicesBackend._read_now",
    new_callable=AsyncMock,
  )
  async def test_read_time_resolved_fluorescence(
    self,
    mock_read_now,
    mock_transfer_data,
    mock_wait_for_idle,
  ):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    await self.backend.read_time_resolved_fluorescence(
      plate, [485], [520], [515], delay_time=10, integration_time=100
    )

    commands = [c.args[0] for c in self.send_command_mock.call_args_list]
    self.assertIn("!CLEAR DATA", commands)
    self.assertTrue(any(cmd.startswith("!XPOS") for cmd in commands))
    self.assertTrue(any(cmd.startswith("!YPOS") for cmd in commands))
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
    self.assertIn("!READSTAGE TOP", commands)

    readtype_call = next(
      c for c in self.send_command_mock.call_args_list if c.args[0] == "!READTYPE TIME 0 250"
    )
    self.assertEqual(readtype_call.kwargs, {"num_res_fields": 1})

    mock_read_now.assert_called_once()
    mock_wait_for_idle.assert_called_once()
    mock_transfer_data.assert_called_once()


class TestMolecularDevicesSpectraMaxGeminiEMBackend(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    with patch("pylabrobot.io.serial.Serial", return_value=MagicMock()):
      self.backend = MolecularDevicesSpectraMaxGeminiEMBackend(port="COM1")

  def test_public_imports(self):
    from pylabrobot.plate_reading import (
      MolecularDevicesSpectraMaxGeminiEMBackend as PlateReadingGeminiBackend,
    )
    from pylabrobot.plate_reading.molecular_devices import (
      MolecularDevicesSpectraMaxGeminiEMBackend as MolecularDevicesGeminiBackend,
    )

    self.assertIs(PlateReadingGeminiBackend, MolecularDevicesSpectraMaxGeminiEMBackend)
    self.assertIs(MolecularDevicesGeminiBackend, MolecularDevicesSpectraMaxGeminiEMBackend)

  def test_serialize(self):
    self.assertEqual(self.backend.serialize()["port"], "COM1")

  async def test_set_temperature_uses_gemini_response_shape(self):
    with patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock:
      await self.backend.set_temperature(26.0)
    send_command_mock.assert_called_once_with("!TEMP 26.0", num_res_fields=1)

  async def test_set_temperature_rejects_out_of_range(self):
    with self.assertRaisesRegex(ValueError, "between 0 and 45"):
      await self.backend.set_temperature(46.0)

  async def test_set_read_stage_top_and_bottom(self):
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
    with patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock:
      await self.backend._set_read_stage(settings)
    send_command_mock.assert_has_calls(
      [call("!TOPREADCLEAR ON"), call("!READSTAGE TOP", num_res_fields=1)]
    )

    settings.read_from_bottom = True
    with patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock:
      await self.backend._set_read_stage(settings)
    send_command_mock.assert_has_calls(
      [call("!TOPREADCLEAR ON"), call("!READSTAGE BOT", num_res_fields=1)]
    )

  async def test_set_read_stage_only_for_fluorescence(self):
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
    with patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock:
      await self.backend._set_read_stage(settings)
    send_command_mock.assert_not_called()

  async def test_set_nvram_noop(self):
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
    with patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock:
      await self.backend._set_nvram(settings)
    send_command_mock.assert_not_called()

  async def test_set_wellscan_mode_uses_gemini_response_shape(self):
    with patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock:
      await self.backend._set_wellscan_mode(True)
      await self.backend._set_wellscan_mode(False)
    send_command_mock.assert_has_calls(
      [
        call("!WELLSCANMODE ON", num_res_fields=1),
        call("!WELLSCANMODE OFF", num_res_fields=1),
      ]
    )

  def test_wellscan_positions(self):
    self.assertEqual(
      self.backend._wellscan_positions("horizontal", 14.380, 20.235),
      [(13.247, 20.235), (14.380, 20.235), (15.513, 20.235)],
    )
    self.assertEqual(
      self.backend._wellscan_positions("vertical", 14.380, 20.235),
      [(14.380, 19.102), (14.380, 20.235), (14.380, 21.368)],
    )
    self.assertEqual(
      self.backend._wellscan_positions("cross", 14.380, 20.235),
      [
        (14.380, 19.102),
        (13.247, 20.235),
        (14.380, 20.235),
        (15.513, 20.235),
        (14.380, 21.368),
      ],
    )
    self.assertEqual(
      self.backend._wellscan_positions("fill", 14.380, 20.235),
      [
        (13.247, 19.102),
        (14.380, 19.102),
        (15.513, 19.102),
        (13.247, 20.235),
        (14.380, 20.235),
        (15.513, 20.235),
        (13.247, 21.368),
        (14.380, 21.368),
        (15.513, 21.368),
      ],
    )

  async def test_experimental_read_fluorescence_wellscan_sequence(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    transfer_results = [[{"data": [[i]]}] for i in range(5)]

    with (
      patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock,
      patch.object(self.backend, "_read_now", new_callable=AsyncMock) as read_now_mock,
      patch.object(self.backend, "_wait_for_idle", new_callable=AsyncMock) as wait_for_idle_mock,
      patch.object(
        self.backend,
        "_transfer_data",
        new_callable=AsyncMock,
        side_effect=transfer_results,
      ) as transfer_data_mock,
    ):
      result = await self.backend.experimental_read_fluorescence_wellscan(
        plate=plate,
        wells=plate.get_all_items(),
        excitation_wavelength=485,
        emission_wavelength=525,
        pattern="cross",
        center_x=14.380,
        center_y=20.235,
      )

    commands = [c.args[0] for c in send_command_mock.call_args_list]
    self.assertIn("!WELLSCANMODE ON", commands)
    self.assertIn("!XPOS 14.380 9 12", commands)
    self.assertIn("!YPOS 19.102 9 6", commands)
    self.assertIn("!XPOS 13.247 9 12", commands)
    self.assertIn("!YPOS 21.368 9 6", commands)
    self.assertEqual(commands.count("!STRIP 2 6"), 5)
    self.assertEqual(commands.count("!PMTCAL OFF"), 4)
    read_now_mock.assert_awaited()
    self.assertEqual(read_now_mock.await_count, 5)
    self.assertEqual(wait_for_idle_mock.await_count, 5)
    self.assertEqual(transfer_data_mock.await_count, 5)
    self.assertEqual(len(result), 5)
    self.assertEqual(result[0]["wellscan_point"], 0)
    self.assertEqual(result[0]["wellscan_x"], 14.380)
    self.assertEqual(result[0]["wellscan_y"], 19.102)
    self.assertEqual(result[-1]["wellscan_point"], 4)
    self.assertEqual(result[-1]["wellscan_x"], 14.380)
    self.assertEqual(result[-1]["wellscan_y"], 21.368)

  async def test_read_luminescence_sequence(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    shake_settings = ShakeSettings(before_read=True, before_read_duration=10)

    with (
      patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock,
      patch.object(self.backend, "_read_now", new_callable=AsyncMock) as read_now_mock,
      patch.object(self.backend, "_wait_for_idle", new_callable=AsyncMock) as wait_for_idle_mock,
      patch.object(
        self.backend,
        "_transfer_data",
        new_callable=AsyncMock,
        return_value=[{"data": [[1.0]]}],
      ) as transfer_data_mock,
    ):
      result = await self.backend.read_luminescence(
        plate=plate,
        wells=plate.get_all_items(),
        shake_settings=shake_settings,
      )

    self.assertEqual(result, [{"data": [[1.0]]}])
    commands = [c.args[0] for c in send_command_mock.call_args_list]
    self.assertIn("!CLEAR DATA", commands)
    self.assertIn("!TAG OFF", commands)
    self.assertIn("!WELLSCANMODE OFF", commands)
    self.assertTrue(any(cmd.startswith("!XPOS") for cmd in commands))
    self.assertTrue(any(cmd.startswith("!YPOS") for cmd in commands))
    self.assertIn("!SHAKE ON", commands)
    self.assertIn("!SHAKE 10 0 0 0 0", commands)
    self.assertIn("!STRIP 1 12", commands)
    self.assertIn("!EMWAVELENGTH 0", commands)
    self.assertIn("!FPW 6", commands)
    self.assertIn("!TOPREADCLEAR OFF", commands)
    self.assertIn("!READSTAGE TOP", commands)
    self.assertIn("!AUTOPMT ON", commands)
    self.assertIn("!CSPEED 8", commands)
    self.assertIn("!PMTCAL ON", commands)
    self.assertIn("!MODE ENDPOINT", commands)
    self.assertIn("!ORDER COLUMN", commands)

    readtype_call = next(
      c for c in send_command_mock.call_args_list if c.args[0] == "!READTYPE LUM"
    )
    self.assertEqual(readtype_call.kwargs, {"num_res_fields": 1})
    send_command_mock.assert_any_call("!WELLSCANMODE OFF", num_res_fields=1)
    send_command_mock.assert_any_call("!READSTAGE TOP", num_res_fields=1)
    read_now_mock.assert_awaited_once()
    wait_for_idle_mock.assert_awaited_once()
    transfer_data_mock.assert_awaited_once()

  async def test_luminescence_rejects_unvalidated_options(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    with self.assertRaisesRegex(NotImplementedError, "focal_height"):
      await self.backend.read_luminescence(
        plate=plate,
        wells=plate.get_all_items(),
        focal_height=0,
      )

    with self.assertRaisesRegex(NotImplementedError, "Bottom luminescence"):
      await self.backend.read_luminescence(
        plate=plate,
        wells=plate.get_all_items(),
        read_from_bottom=True,
      )

    with self.assertRaisesRegex(NotImplementedError, "Only endpoint luminescence"):
      await self.backend.read_luminescence(
        plate=plate,
        wells=plate.get_all_items(),
        read_type=ReadType.KINETIC,
      )

  async def test_experimental_read_time_resolved_fluorescence_top_sequence(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")

    with (
      patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock,
      patch.object(self.backend, "_read_now", new_callable=AsyncMock) as read_now_mock,
      patch.object(self.backend, "_wait_for_idle", new_callable=AsyncMock) as wait_for_idle_mock,
      patch.object(
        self.backend,
        "_transfer_data",
        new_callable=AsyncMock,
        return_value=[{"data": [[2.0]]}],
      ) as transfer_data_mock,
    ):
      result = await self.backend.experimental_read_time_resolved_fluorescence(
        plate=plate,
        excitation_wavelengths=[485],
        emission_wavelengths=[525],
        cutoff_filters=[7],
        delay_time=50,
        integration_time=850,
      )

    self.assertEqual(result, [{"data": [[2.0]]}])
    commands = [c.args[0] for c in send_command_mock.call_args_list]
    self.assertIn("!CLEAR DATA", commands)
    self.assertIn("!TAG OFF", commands)
    self.assertIn("!WELLSCANMODE OFF", commands)
    self.assertTrue(any(cmd.startswith("!XPOS") for cmd in commands))
    self.assertTrue(any(cmd.startswith("!YPOS") for cmd in commands))
    self.assertIn("!SHAKE OFF", commands)
    self.assertIn("!STRIP 1 12", commands)
    self.assertIn("!EXWAVELENGTH 485", commands)
    self.assertIn("!EMWAVELENGTH 525", commands)
    self.assertIn("!AUTOFILTER OFF", commands)
    self.assertIn("!EMFILTER 7", commands)
    self.assertIn("!FPW 6", commands)
    self.assertIn("!TOPREADCLEAR ON", commands)
    self.assertIn("!READSTAGE TOP", commands)
    self.assertIn("!AUTOPMT ON", commands)
    self.assertIn("!CSPEED 8", commands)
    self.assertIn("!PMTCAL ON", commands)
    self.assertIn("!MODE ENDPOINT", commands)
    self.assertIn("!ORDER COLUMN", commands)

    readtype_call = next(
      c for c in send_command_mock.call_args_list if c.args[0] == "!READTYPE TIME 50 850"
    )
    self.assertEqual(readtype_call.kwargs, {"num_res_fields": 1})
    send_command_mock.assert_any_call("!WELLSCANMODE OFF", num_res_fields=1)
    send_command_mock.assert_any_call("!READSTAGE TOP", num_res_fields=1)
    read_now_mock.assert_awaited_once()
    wait_for_idle_mock.assert_awaited_once()
    transfer_data_mock.assert_awaited_once()

  async def test_experimental_read_time_resolved_fluorescence_bottom_sequence(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")

    with (
      patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock,
      patch.object(self.backend, "_read_now", new_callable=AsyncMock),
      patch.object(self.backend, "_wait_for_idle", new_callable=AsyncMock),
      patch.object(self.backend, "_transfer_data", new_callable=AsyncMock, return_value=[]),
    ):
      await self.backend.experimental_read_time_resolved_fluorescence(
        plate=plate,
        excitation_wavelengths=[485],
        emission_wavelengths=[525],
        cutoff_filters=[7],
        delay_time=50,
        integration_time=850,
        read_from_bottom=True,
      )

    send_command_mock.assert_any_call("!TOPREADCLEAR ON")
    send_command_mock.assert_any_call("!READSTAGE BOT", num_res_fields=1)

  async def test_experimental_read_time_resolved_fluorescence_kinetic_sequence(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    shake_settings = ShakeSettings(
      before_read=True,
      before_read_duration=5,
      between_reads=True,
      between_reads_duration=3,
    )

    with (
      patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock,
      patch.object(self.backend, "_read_now", new_callable=AsyncMock) as read_now_mock,
      patch.object(self.backend, "_wait_for_idle", new_callable=AsyncMock) as wait_for_idle_mock,
      patch.object(
        self.backend,
        "_transfer_data",
        new_callable=AsyncMock,
        return_value=[{"data": [[3.0]]}],
      ) as transfer_data_mock,
    ):
      result = await self.backend.experimental_read_time_resolved_fluorescence(
        plate=plate,
        excitation_wavelengths=[485],
        emission_wavelengths=[525],
        cutoff_filters=[7],
        delay_time=50,
        integration_time=850,
        read_type=ReadType.KINETIC,
        shake_settings=shake_settings,
        pmt_gain=PmtGain.MEDIUM,
        kinetic_settings=KineticSettings(interval=30, num_readings=21),
      )

    self.assertEqual(result, [{"data": [[3.0]]}])
    commands = [c.args[0] for c in send_command_mock.call_args_list]
    self.assertIn("!READTYPE TIME 50 850", commands)
    self.assertIn("!SHAKE ON", commands)
    self.assertIn("!SHAKE 5 30 27 3 0", commands)
    self.assertIn("!AUTOPMT OFF", commands)
    self.assertIn("!PMT MED", commands)
    self.assertIn("!MODE KINETIC 30 21", commands)
    self.assertIn("!READSTAGE TOP", commands)
    read_now_mock.assert_awaited_once()
    wait_for_idle_mock.assert_awaited_once()
    transfer_data_mock.assert_awaited_once()

  async def test_time_resolved_fluorescence_kinetic_requires_settings(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    with self.assertRaisesRegex(ValueError, "kinetic_settings is required"):
      await self.backend.experimental_read_time_resolved_fluorescence(
        plate=plate,
        excitation_wavelengths=[485],
        emission_wavelengths=[525],
        cutoff_filters=[7],
        delay_time=50,
        integration_time=850,
        read_type=ReadType.KINETIC,
      )

  async def test_experimental_read_fluorescence_emission_spectrum_sequence(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")

    with (
      patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock,
      patch.object(self.backend, "_read_now", new_callable=AsyncMock) as read_now_mock,
      patch.object(self.backend, "_wait_for_idle", new_callable=AsyncMock) as wait_for_idle_mock,
      patch.object(
        self.backend,
        "_transfer_data",
        new_callable=AsyncMock,
        return_value=[{"data": [[4.0]]}],
      ) as transfer_data_mock,
    ):
      result = await self.backend.experimental_read_fluorescence_emission_spectrum(
        plate=plate,
        wells=plate.get_all_items(),
        excitation_wavelength=350,
        start_emission_wavelength=400,
        step=10,
        num_steps=36,
        read_from_bottom=True,
      )

    self.assertEqual(result, [{"data": [[4.0]]}])
    commands = [c.args[0] for c in send_command_mock.call_args_list]
    self.assertIn("!READTYPE FLU", commands)
    self.assertIn("!EXWAVELENGTH 350", commands)
    self.assertNotIn("!EMWAVELENGTH 400", commands)
    self.assertIn("!AUTOFILTER OFF", commands)
    self.assertIn("!EMFILTER 1", commands)
    self.assertIn("!FPW 7", commands)
    self.assertIn("!TOPREADCLEAR ON", commands)
    self.assertIn("!READSTAGE BOT", commands)
    self.assertIn("!MODE EMSPECTRUM 400 10 36", commands)
    self.assertIn("!ORDER WAVELENGTH", commands)
    send_command_mock.assert_any_call("!WELLSCANMODE OFF", num_res_fields=1)
    send_command_mock.assert_any_call("!READSTAGE BOT", num_res_fields=1)
    read_now_mock.assert_awaited_once()
    wait_for_idle_mock.assert_awaited_once()
    transfer_data_mock.assert_awaited_once()

  async def test_experimental_read_fluorescence_excitation_spectrum_sequence(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")

    with (
      patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock,
      patch.object(self.backend, "_read_now", new_callable=AsyncMock) as read_now_mock,
      patch.object(self.backend, "_wait_for_idle", new_callable=AsyncMock) as wait_for_idle_mock,
      patch.object(
        self.backend,
        "_transfer_data",
        new_callable=AsyncMock,
        return_value=[{"data": [[5.0]]}],
      ) as transfer_data_mock,
    ):
      result = await self.backend.experimental_read_fluorescence_excitation_spectrum(
        plate=plate,
        wells=plate.get_all_items(),
        emission_wavelength=600,
        start_excitation_wavelength=350,
        step=20,
        num_steps=4,
        read_from_bottom=True,
      )

    self.assertEqual(result, [{"data": [[5.0]]}])
    commands = [c.args[0] for c in send_command_mock.call_args_list]
    self.assertIn("!READTYPE FLU", commands)
    self.assertIn("!EMWAVELENGTH 600", commands)
    self.assertNotIn("!EXWAVELENGTH 350", commands)
    self.assertIn("!AUTOFILTER OFF", commands)
    self.assertIn("!EMFILTER 1", commands)
    self.assertIn("!AUTOFILTER EX OFF", commands)
    self.assertIn("!FPW 7", commands)
    self.assertIn("!TOPREADCLEAR ON", commands)
    self.assertIn("!READSTAGE BOT", commands)
    self.assertIn("!MODE EXSPECTRUM 350 20 4", commands)
    self.assertIn("!ORDER WAVELENGTH", commands)
    send_command_mock.assert_any_call("!WELLSCANMODE OFF", num_res_fields=1)
    send_command_mock.assert_any_call("!READSTAGE BOT", num_res_fields=1)
    read_now_mock.assert_awaited_once()
    wait_for_idle_mock.assert_awaited_once()
    transfer_data_mock.assert_awaited_once()

  async def test_frontend_style_fluorescence_call(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    with (
      patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock,
      patch.object(self.backend, "_read_now", new_callable=AsyncMock) as read_now_mock,
      patch.object(self.backend, "_wait_for_idle", new_callable=AsyncMock) as wait_for_idle_mock,
      patch.object(
        self.backend,
        "_transfer_data",
        new_callable=AsyncMock,
        return_value=[{"data": []}],
      ) as transfer_data_mock,
    ):
      result = await self.backend.read_fluorescence(
        plate=plate,
        wells=plate.get_all_items(),
        excitation_wavelength=485,
        emission_wavelength=520,
      )

    self.assertEqual(result, [{"data": []}])
    commands = [c.args[0] for c in send_command_mock.call_args_list]
    self.assertIn("!EXWAVELENGTH 485", commands)
    self.assertIn("!EMWAVELENGTH 520", commands)
    self.assertIn("!EMFILTER 7", commands)
    self.assertIn("!STRIP 1 12", commands)
    self.assertIn("!WELLSCANMODE OFF", commands)
    read_now_mock.assert_awaited_once()
    wait_for_idle_mock.assert_awaited_once()
    transfer_data_mock.assert_awaited_once()

  async def test_frontend_style_fluorescence_accepts_explicit_cutoff_filter(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    with (
      patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock,
      patch.object(self.backend, "_read_now", new_callable=AsyncMock),
      patch.object(self.backend, "_wait_for_idle", new_callable=AsyncMock),
      patch.object(self.backend, "_transfer_data", new_callable=AsyncMock, return_value=[]),
    ):
      await self.backend.read_fluorescence(
        plate=plate,
        wells=plate.get_all_items(),
        excitation_wavelength=485,
        emission_wavelength=520,
        cutoff_filters=[8],
      )

    send_command_mock.assert_any_call("!EMFILTER 8")

  async def test_partial_plate_region_mapping(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    wells = plate.get_items("B2:G7")
    self.assertEqual(self.backend._get_well_region(plate, wells), (1, 6, 1, 6))

    with patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock:
      await self.backend._set_plate_region(plate, wells)

    send_command_mock.assert_has_calls(
      [
        call("!XPOS 13.380 9.000 12"),
        call("!YPOS 21.240 9.000 6"),
        call("!STRIP 2 6"),
      ]
    )

  async def test_partial_plate_region_rejects_non_rectangular_wells(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    with self.assertRaisesRegex(NotImplementedError, "rectangular contiguous"):
      self.backend._get_well_region(
        plate,
        [plate.get_item("A1"), plate.get_item("A2"), plate.get_item("B1")],
      )

  async def test_partial_plate_fluorescence_region_sequence(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    with (
      patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock,
      patch.object(self.backend, "_read_now", new_callable=AsyncMock),
      patch.object(self.backend, "_wait_for_idle", new_callable=AsyncMock),
      patch.object(self.backend, "_transfer_data", new_callable=AsyncMock, return_value=[]),
    ):
      await self.backend.read_fluorescence(
        plate=plate,
        wells=plate.get_items("B2:G7"),
        excitation_wavelength=485,
        emission_wavelength=520,
      )

    commands = [c.args[0] for c in send_command_mock.call_args_list]
    self.assertIn("!XPOS 13.380 9.000 12", commands)
    self.assertIn("!YPOS 21.240 9.000 6", commands)
    self.assertIn("!STRIP 2 6", commands)

  async def test_partial_plate_wellscan_unsupported(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    with self.assertRaisesRegex(NotImplementedError, "Partial-plate reads"):
      await self.backend.experimental_read_fluorescence_wellscan(
        plate=plate,
        wells=[plate.get_item("A1")],
        excitation_wavelength=485,
        emission_wavelength=525,
      )

  async def test_partial_plate_spectra_region_sequence(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    with (
      patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock,
      patch.object(self.backend, "_read_now", new_callable=AsyncMock),
      patch.object(self.backend, "_wait_for_idle", new_callable=AsyncMock),
      patch.object(self.backend, "_transfer_data", new_callable=AsyncMock, return_value=[]),
    ):
      await self.backend.experimental_read_fluorescence_emission_spectrum(
        plate=plate,
        wells=plate.get_items("B2:G7"),
      )

    commands = [c.args[0] for c in send_command_mock.call_args_list]
    self.assertIn("!XPOS 13.380 9.000 12", commands)
    self.assertIn("!YPOS 21.240 9.000 6", commands)
    self.assertIn("!STRIP 2 6", commands)

  async def test_partial_plate_time_resolved_region_sequence(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    with (
      patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock,
      patch.object(self.backend, "_read_now", new_callable=AsyncMock),
      patch.object(self.backend, "_wait_for_idle", new_callable=AsyncMock),
      patch.object(self.backend, "_transfer_data", new_callable=AsyncMock, return_value=[]),
    ):
      await self.backend.experimental_read_time_resolved_fluorescence(
        plate=plate,
        wells=plate.get_items("B2:G7"),
        excitation_wavelengths=[485],
        emission_wavelengths=[525],
        cutoff_filters=[7],
        delay_time=50,
        integration_time=850,
      )

    commands = [c.args[0] for c in send_command_mock.call_args_list]
    self.assertIn("!XPOS 13.380 9.000 12", commands)
    self.assertIn("!YPOS 21.240 9.000 6", commands)
    self.assertIn("!STRIP 2 6", commands)

  async def test_partial_plate_luminescence_region_sequence(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    with (
      patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock,
      patch.object(self.backend, "_read_now", new_callable=AsyncMock),
      patch.object(self.backend, "_wait_for_idle", new_callable=AsyncMock),
      patch.object(self.backend, "_transfer_data", new_callable=AsyncMock, return_value=[]),
    ):
      await self.backend.read_luminescence(
        plate=plate,
        wells=plate.get_items("B2:G7"),
      )

    commands = [c.args[0] for c in send_command_mock.call_args_list]
    self.assertIn("!XPOS 13.380 9.000 12", commands)
    self.assertIn("!YPOS 21.240 9.000 6", commands)
    self.assertIn("!STRIP 2 6", commands)

  async def test_partial_plate_single_well_fluorescence_region_sequence(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    with (
      patch.object(self.backend, "send_command", new_callable=AsyncMock) as send_command_mock,
      patch.object(self.backend, "_read_now", new_callable=AsyncMock),
      patch.object(self.backend, "_wait_for_idle", new_callable=AsyncMock),
      patch.object(self.backend, "_transfer_data", new_callable=AsyncMock, return_value=[]),
    ):
      await self.backend.read_fluorescence(
        plate=plate,
        wells=[plate.get_item("A1")],
        excitation_wavelength=485,
        emission_wavelength=520,
      )

    commands = [c.args[0] for c in send_command_mock.call_args_list]
    self.assertIn("!XPOS 13.380 9.000 12", commands)
    self.assertIn("!YPOS 12.240 9.000 1", commands)
    self.assertIn("!STRIP 1 1", commands)

  async def test_fluorescence_requires_wavelengths(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    with self.assertRaisesRegex(ValueError, "excitation_wavelength is required"):
      await self.backend.read_fluorescence(
        plate=plate,
        wells=plate.get_all_items(),
        emission_wavelength=520,
      )

    with self.assertRaisesRegex(ValueError, "emission_wavelength is required"):
      await self.backend.read_fluorescence(
        plate=plate,
        wells=plate.get_all_items(),
        excitation_wavelength=485,
      )

  async def test_fluorescence_rejects_focal_height(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    with self.assertRaisesRegex(NotImplementedError, "focal_height"):
      await self.backend.read_fluorescence(
        plate=plate,
        wells=plate.get_all_items(),
        excitation_wavelength=485,
        emission_wavelength=520,
        focal_height=0,
      )

  async def test_read_time_resolved_fluorescence_redirects_to_experimental(self):
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
    with self.assertRaisesRegex(
      NotImplementedError, "experimental_read_time_resolved_fluorescence"
    ):
      await self.backend.read_time_resolved_fluorescence(plate, [485], [520], [515], 10, 100)

  async def test_unsupported_absorbance(self):
    with self.assertRaisesRegex(NotImplementedError, "Absorbance reading is not supported"):
      await self.backend.read_absorbance(MagicMock(), [], 500)

  async def test_unsupported_fluorescence_polarization(self):
    with self.assertRaisesRegex(NotImplementedError, "Fluorescence polarization reading"):
      await self.backend.read_fluorescence_polarization(MagicMock(), [485], [520], [515])


class TestDataParsing(unittest.IsolatedAsyncioTestCase):
  send_command_mock: AsyncMock

  def setUp(self):
    with patch("pylabrobot.io.serial.Serial", return_value=MagicMock()):
      self.backend = MolecularDevicesBackend(port="COM1")
      self.send_command_mock = patch.object(
        self.backend, "send_command", new_callable=AsyncMock
      ).start()

  def test_parse_absorbance_single_wavelength(self):
    data_str = """
    12345.6	25.1	96-well
    L:	260
    L:	260
    1:	0.1	0.2
    2:	0.3	0.4
    """
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

    result = self.backend._parse_data(data_str, settings)
    self.assertIsInstance(result, list)
    self.assertEqual(len(result), 1)
    read = result[0]
    self.assertEqual(read["wavelength"], 260)
    self.assertEqual(read["time"], 12345.6)
    self.assertEqual(read["temperature"], 25.1)
    self.assertEqual(read["data"], [[0.1, 0.3], [0.2, 0.4]])

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
    result = self.backend._parse_data(data_str, settings)
    self.assertIsInstance(result, list)
    self.assertEqual(len(result), 2)
    self.assertEqual(result[0]["wavelength"], 260)
    self.assertEqual(result[0]["data"], [[0.1, 0.3], [0.2, 0.4]])
    self.assertEqual(result[1]["wavelength"], 280)
    self.assertEqual(result[1]["data"], [[0.5, 0.7], [0.6, 0.8]])

  def test_parse_fluorescence(self):
    data_str = """
    12345.6\t25.1\t96-well
    exL:\t485
    emL:\t520
    L:\t485\t520
    1:\t100\t200
    2:\t300\t400
    """
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
    result = self.backend._parse_data(data_str, settings)
    self.assertIsInstance(result, list)
    self.assertEqual(len(result), 1)
    read = result[0]
    self.assertEqual(read["ex_wavelength"], 485)
    self.assertEqual(read["em_wavelength"], 520)
    self.assertEqual(read["time"], 12345.6)
    self.assertEqual(read["temperature"], 25.1)
    self.assertEqual(read["data"], [[100.0, 300.0], [200.0, 400.0]])

  def test_parse_luminescence(self):
    data_str = """
    12345.6\t25.1\t96-well
    emL:\t590
    L:\t\t590
    1:\t1000\t2000
    2:\t3000\t4000
    """
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode=ReadMode.LUM,
      read_type=ReadType.ENDPOINT,
      read_order=ReadOrder.COLUMN,
      calibrate=Calibrate.ON,
      shake_settings=None,
      carriage_speed=CarriageSpeed.NORMAL,
      speed_read=False,
      kinetic_settings=None,
      spectrum_settings=None,
    )
    result = self.backend._parse_data(data_str, settings)
    self.assertIsInstance(result, list)
    self.assertEqual(len(result), 1)
    read = result[0]
    self.assertEqual(read["em_wavelength"], 590)
    self.assertEqual(read["time"], 12345.6)
    self.assertEqual(read["temperature"], 25.1)
    self.assertEqual(read["data"], [[1000.0, 3000.0], [2000.0, 4000.0]])

  def test_parse_data_with_sat_and_nan(self):
    data_str = """
    12345.6\t25.1\t96-well
    L:\t260
    L:\t260
    1:\t0.1\t#SAT
    2:\t0.3\t-
    """
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
    result = self.backend._parse_data(data_str, settings)
    self.assertIsInstance(result, list)
    self.assertEqual(len(result), 1)
    read = result[0]
    self.assertEqual(read["data"][1][0], float("inf"))
    self.assertTrue(math.isnan(read["data"][1][1]))

  async def test_parse_kinetic_absorbance(self):
    # Mock the send_command to return two different data blocks
    def data_generator():
      yield [
        "OK",
        """
    12345.6\t25.1\t96-well
    L:\t260
    L:\t260
    1:\t0.1\t0.2
    2:\t0.3\t0.4
    """,
      ]
      yield [
        "OK",
        """
    12355.6\t25.2\t96-well
    L:\t260
    L:\t260
    1:\t0.15\t0.25
    2:\t0.35\t0.45
    """,
      ]

    self.send_command_mock.side_effect = data_generator()

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

    result = await self.backend._transfer_data(settings)
    self.assertEqual(len(result), 2)
    self.assertEqual(result[0]["wavelength"], 260)
    self.assertEqual(result[0]["data"], [[0.1, 0.3], [0.2, 0.4]])
    self.assertEqual(result[0]["time"], 12345.6)
    self.assertEqual(result[1]["wavelength"], 260)
    self.assertEqual(result[1]["data"], [[0.15, 0.35], [0.25, 0.45]])
    self.assertEqual(result[1]["time"], 12355.6)

  async def test_parse_spectrum_absorbance(self):
    # Mock the send_command to return two different data blocks for two wavelengths
    def data_generator():
      yield [
        "OK",
        """
    12345.6\t25.1\t96-well
    L:\t260
    L:\t260
    1:\t0.1\t0.2
    2:\t0.3\t0.4
    """,
      ]
      yield [
        "OK",
        """
    12355.6\t25.2\t96-well
    L:\t270
    L:\t270
    1:\t0.15\t0.25
    2:\t0.35\t0.45
    """,
      ]

    self.send_command_mock.side_effect = data_generator()

    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode=ReadMode.ABS,
      read_type=ReadType.SPECTRUM,
      spectrum_settings=SpectrumSettings(start_wavelength=260, step=10, num_steps=2),
      read_order=ReadOrder.COLUMN,
      calibrate=Calibrate.ON,
      shake_settings=None,
      carriage_speed=CarriageSpeed.NORMAL,
      speed_read=False,
      kinetic_settings=None,
    )

    result = await self.backend._transfer_data(settings)
    self.assertEqual(len(result), 2)

    self.assertEqual(result[0]["wavelength"], 260)
    self.assertEqual(result[0]["data"], [[0.1, 0.3], [0.2, 0.4]])
    self.assertEqual(result[0]["time"], 12345.6)

    self.assertEqual(result[1]["wavelength"], 270)
    self.assertEqual(result[1]["data"], [[0.15, 0.35], [0.25, 0.45]])
    self.assertEqual(result[1]["time"], 12355.6)


class TestErrorHandling(unittest.IsolatedAsyncioTestCase):
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

  async def test_parse_basic_errors_fail_known_error_code(self):
    # Test a known error code (e.g., 107: no data to transfer)
    with self.assertRaisesRegex(
      MolecularDevicesUnrecognizedCommandError,
      "Command '!TEST' failed with error 107: no data to transfer",
    ):
      await self._mock_send_command_response("OK\t\r\n>FAIL\t 107")

  async def test_parse_basic_errors_fail_unknown_error_code(self):
    # Test an unknown error code
    with self.assertRaisesRegex(
      MolecularDevicesError, "Command '!TEST' failed with unknown error code: 999"
    ):
      await self._mock_send_command_response("FAIL\t 999")

  async def test_parse_basic_errors_fail_unparsable_error(self):
    # Test an unparsable error message (e.g., not an integer code)
    with self.assertRaisesRegex(
      MolecularDevicesError, "Command '!TEST' failed with unparsable error: FAIL\t ABC"
    ):
      await self._mock_send_command_response("FAIL\t ABC")

  async def test_parse_basic_errors_empty_response(self):
    # Test an empty response from the device
    self.mock_serial.readline.return_value = b""  # Simulate no response
    with self.assertRaisesRegex(TimeoutError, "Timeout waiting for response to command: !TEST"):
      await self.backend.send_command("!TEST", timeout=1)  # Short timeout for test

  async def test_parse_basic_errors_warning_response(self):
    # Test a response containing a warning
    self.mock_serial.readline.side_effect = [b"OK\tWarning: Something happened>\r\n"]
    # Expect no exception, but a warning logged (not directly testable with assertRaises)
    # We can assert that no error is raised.
    try:
      await self.backend.send_command("!TEST")
    except MolecularDevicesError:
      self.fail("MolecularDevicesError raised for a warning response")

  async def test_parse_basic_errors_ok_response(self):
    # Test a normal OK response
    self.mock_serial.readline.side_effect = [b"OK>\r\n"]
    try:
      response = await self.backend.send_command("!TEST")
      self.assertEqual(response, ["OK"])
    except MolecularDevicesError:
      self.fail("MolecularDevicesError raised for a valid OK response")


if __name__ == "__main__":
  unittest.main()
