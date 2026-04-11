import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

from pylabrobot.capabilities.plate_reading.absorbance.standard import AbsorbanceResult
from pylabrobot.capabilities.plate_reading.fluorescence.standard import FluorescenceResult
from pylabrobot.capabilities.plate_reading.luminescence.standard import LuminescenceResult
from pylabrobot.molecular_devices.spectramax.backend import (
  Calibrate,
  CarriageSpeed,
  KineticSettings,
  MolecularDevicesAbsorbanceBackend,
  MolecularDevicesDriver,
  MolecularDevicesSettings,
  PmtGain,
  ReadMode,
  ReadOrder,
  ReadType,
  ShakeSettings,
  SpectrumSettings,
)
from pylabrobot.molecular_devices.spectramax.spectramax_m5 import (
  SpectraMaxM5FluorescenceBackend,
  SpectraMaxM5LuminescenceBackend,
)
from pylabrobot.resources.agenbio.plates import AGenBio_96_wellplate_Ub_2200ul


class TestMolecularDevicesBackend(unittest.IsolatedAsyncioTestCase):
  """Tests for MolecularDevicesAbsorbanceBackend and the protocol mixin."""

  backend: MolecularDevicesAbsorbanceBackend
  driver: MolecularDevicesDriver
  mock_serial: MagicMock
  send_command_mock: AsyncMock

  def setUp(self):
    self.mock_serial = MagicMock()
    self.mock_serial.setup = AsyncMock()
    self.mock_serial.stop = AsyncMock()
    self.mock_serial.write = AsyncMock()
    self.mock_serial.readline = AsyncMock(return_value=b"OK>\r\n")

    with patch("pylabrobot.io.serial.Serial", return_value=self.mock_serial):
      self.driver = MolecularDevicesDriver(port="COM1")
      self.driver.io = self.mock_serial
    self.backend = MolecularDevicesAbsorbanceBackend(driver=self.driver)
    self.send_command_mock = patch.object(
      self.driver, "send_command", new_callable=AsyncMock
    ).start()
    self.addCleanup(patch.stopall)

  async def test_setup_stop(self):
    with patch.object(
      self.driver, "send_command", wraps=self.driver.send_command
    ) as wrapped_send_command:
      await self.driver.setup()
      self.mock_serial.setup.assert_called_once()
      wrapped_send_command.assert_called_with("!")
      await self.driver.stop()
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
    await self.backend._set_plate_position(settings)
    self.send_command_mock.assert_has_calls(
      [call("!XPOS 13.380 9.000 12"), call("!YPOS 12.240 9.000 8")]
    )

  async def test_set_strip(self):
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

  async def test_read_absorbance(self):
    with (
      patch.object(self.backend, "_read_now", new_callable=AsyncMock) as mock_read_now,
      patch.object(self.driver, "wait_for_idle", new_callable=AsyncMock) as mock_wait,
      patch.object(
        self.backend,
        "_transfer_data",
        new_callable=AsyncMock,
        return_value=[{"data": [[0.1]], "wavelength": 500, "temperature": 25.0, "time": 12345.6}],
      ) as mock_transfer,
    ):
      plate = AGenBio_96_wellplate_Ub_2200ul("test_plate")
      results = await self.backend.read_absorbance(plate, plate.get_wells(), 500)

      self.assertIsInstance(results, list)
      self.assertEqual(len(results), 1)
      self.assertIsInstance(results[0], AbsorbanceResult)
      self.assertEqual(results[0].wavelength, 500)
      self.assertEqual(results[0].temperature, 25.0)
      self.assertEqual(results[0].timestamp, 12345.6)

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
      mock_wait.assert_called_once()
      mock_transfer.assert_called_once()


class TestSpectraMaxM5Backend(unittest.IsolatedAsyncioTestCase):
  """Tests for SpectraMaxM5 fluorescence and luminescence backends."""

  flu_backend: SpectraMaxM5FluorescenceBackend
  lum_backend: SpectraMaxM5LuminescenceBackend
  driver: MolecularDevicesDriver
  mock_serial: MagicMock
  send_command_mock: AsyncMock

  def setUp(self):
    self.mock_serial = MagicMock()
    self.mock_serial.setup = AsyncMock()
    self.mock_serial.stop = AsyncMock()
    self.mock_serial.write = AsyncMock()
    self.mock_serial.readline = AsyncMock(return_value=b"OK>\r\n")

    with patch("pylabrobot.io.serial.Serial", return_value=self.mock_serial):
      self.driver = MolecularDevicesDriver(
        port="COM1", human_readable_device_name="Molecular Devices SpectraMax M5"
      )
      self.driver.io = self.mock_serial
    self.flu_backend = SpectraMaxM5FluorescenceBackend(driver=self.driver)
    self.lum_backend = SpectraMaxM5LuminescenceBackend(driver=self.driver)
    self.send_command_mock = patch.object(
      self.driver, "send_command", new_callable=AsyncMock
    ).start()
    self.addCleanup(patch.stopall)

  async def test_read_fluorescence(self):
    with (
      patch.object(self.flu_backend, "_read_now", new_callable=AsyncMock) as mock_read_now,
      patch.object(self.driver, "wait_for_idle", new_callable=AsyncMock) as mock_wait,
      patch.object(
        self.flu_backend,
        "_transfer_data",
        new_callable=AsyncMock,
        return_value=[
          {
            "data": [[100.0]],
            "ex_wavelength": 485,
            "em_wavelength": 520,
            "temperature": 25.0,
            "time": 12345.6,
          }
        ],
      ) as mock_transfer,
    ):
      plate = AGenBio_96_wellplate_Ub_2200ul("test_plate")
      results = await self.flu_backend.read_fluorescence(
        plate, plate.get_wells(), excitation_wavelength=485, emission_wavelength=520, focal_height=0
      )

      self.assertIsInstance(results, list)
      self.assertEqual(len(results), 1)
      self.assertIsInstance(results[0], FluorescenceResult)
      self.assertEqual(results[0].excitation_wavelength, 485)
      self.assertEqual(results[0].emission_wavelength, 520)
      self.assertEqual(results[0].temperature, 25.0)
      self.assertEqual(results[0].timestamp, 12345.6)

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
      self.assertIn("!PMTCAL ONCE", commands)
      self.assertIn("!MODE ENDPOINT", commands)
      self.assertIn("!ORDER COLUMN", commands)
      self.assertIn("!READSTAGE TOP", commands)

      readtype_call = next(
        c for c in self.send_command_mock.call_args_list if c.args[0] == "!READTYPE FLU"
      )
      self.assertEqual(readtype_call.kwargs, {"num_res_fields": 1})

      mock_read_now.assert_called_once()
      mock_wait.assert_called_once()
      mock_transfer.assert_called_once()

  async def test_read_luminescence(self):
    with (
      patch.object(self.lum_backend, "_read_now", new_callable=AsyncMock) as mock_read_now,
      patch.object(self.driver, "wait_for_idle", new_callable=AsyncMock) as mock_wait,
      patch.object(
        self.lum_backend,
        "_transfer_data",
        new_callable=AsyncMock,
        return_value=[
          {"data": [[1000.0]], "em_wavelength": 590, "temperature": 25.0, "time": 12345.6}
        ],
      ) as mock_transfer,
    ):
      plate = AGenBio_96_wellplate_Ub_2200ul("test_plate")
      results = await self.lum_backend.read_luminescence(
        plate,
        plate.get_wells(),
        focal_height=0,
        backend_params=SpectraMaxM5LuminescenceBackend.LuminescenceParams(
          emission_wavelengths=[590]
        ),
      )

      self.assertIsInstance(results, list)
      self.assertEqual(len(results), 1)
      self.assertIsInstance(results[0], LuminescenceResult)
      self.assertEqual(results[0].temperature, 25.0)
      self.assertEqual(results[0].timestamp, 12345.6)

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

      mock_read_now.assert_called_once()
      mock_wait.assert_called_once()
      mock_transfer.assert_called_once()
