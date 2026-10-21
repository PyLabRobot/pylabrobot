import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

from pylabrobot.molecular_devices.spectramax.base import (
  KineticSettings,
  MolecularDevicesPlateReader,
  MolecularDevicesSettings,
  ShakeSettings,
  SpectrumSettings,
)
from pylabrobot.molecular_devices.spectramax.results import (
  AbsorbanceResult,
  FluorescenceResult,
  LuminescenceResult,
)
from pylabrobot.molecular_devices.spectramax.spectramax_m5 import SpectraMaxM5
from pylabrobot.resources.agenbio.plates import AGenBio_96_wellplate_Ub_2200ul


class TestMolecularDevicesPlateReader(unittest.IsolatedAsyncioTestCase):
  """Tests for MolecularDevicesPlateReader."""

  device: MolecularDevicesPlateReader
  mock_serial: MagicMock
  send_command_mock: AsyncMock

  def setUp(self):
    self.mock_serial = MagicMock()
    self.mock_serial.setup = AsyncMock()
    self.mock_serial.stop = AsyncMock()
    self.mock_serial.write = AsyncMock()
    self.mock_serial.readline = AsyncMock(return_value=b"OK>\r\n")

    with patch("pylabrobot.io.serial.Serial", return_value=self.mock_serial):
      self.device = MolecularDevicesPlateReader(port="COM1")
      self.device.io = self.mock_serial
    self.send_command_mock = patch.object(
      self.device, "send_command", new_callable=AsyncMock
    ).start()
    self.addCleanup(patch.stopall)

  async def test_setup_stop(self):
    with patch.object(
      self.device, "send_command", wraps=self.device.send_command
    ) as wrapped_send_command:
      await self.device.setup()
      self.mock_serial.setup.assert_called_once()
      wrapped_send_command.assert_called_with("!")
      await self.device.stop()
      self.mock_serial.stop.assert_called_once()

  async def test_set_clear(self):
    await self.device._set_clear()
    self.send_command_mock.assert_called_once_with("!CLEAR DATA")

  async def test_set_mode(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode="absorbance",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      kinetic_settings=None,
      spectrum_settings=None,
    )
    await self.device._set_mode(settings)
    self.send_command_mock.assert_called_once_with("!MODE ENDPOINT")

    self.send_command_mock.reset_mock()
    settings.read_type = "kinetic"
    settings.kinetic_settings = KineticSettings(interval=10, num_readings=5)
    await self.device._set_mode(settings)
    self.send_command_mock.assert_called_once_with("!MODE KINETIC 10 5")

    self.send_command_mock.reset_mock()
    settings.read_type = "spectrum"
    settings.spectrum_settings = SpectrumSettings(start_wavelength=200, step=10, num_steps=50)
    await self.device._set_mode(settings)
    self.send_command_mock.assert_called_once_with("!MODE SPECTRUM 200 10 50")

    self.send_command_mock.reset_mock()
    settings.spectrum_settings.excitation_emission_type = "EXSPECTRUM"
    await self.device._set_mode(settings)
    self.send_command_mock.assert_called_once_with("!MODE EXSPECTRUM 200 10 50")

  async def test_set_wavelengths(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode="absorbance",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      wavelengths=[500, (600, True)],
      kinetic_settings=None,
      spectrum_settings=None,
    )
    await self.device._set_wavelengths(settings)
    self.send_command_mock.assert_called_once_with("!WAVELENGTH 500 F600")

    self.send_command_mock.reset_mock()
    settings.path_check = True
    await self.device._set_wavelengths(settings)
    self.send_command_mock.assert_called_once_with("!WAVELENGTH 500 F600 900 998")

    self.send_command_mock.reset_mock()
    settings.read_mode = "fluorescence"
    settings.excitation_wavelengths = [485]
    settings.emission_wavelengths = [520]
    await self.device._set_wavelengths(settings)
    self.send_command_mock.assert_has_calls([call("!EXWAVELENGTH 485"), call("!EMWAVELENGTH 520")])

    self.send_command_mock.reset_mock()
    settings.read_mode = "luminescence"
    settings.emission_wavelengths = [590]
    await self.device._set_wavelengths(settings)
    self.send_command_mock.assert_called_once_with("!EMWAVELENGTH 590")

  async def test_set_plate_position(self):
    plate = AGenBio_96_wellplate_Ub_2200ul("test_plate")
    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode="absorbance",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      kinetic_settings=None,
      spectrum_settings=None,
    )
    await self.device._set_plate_position(settings)
    self.send_command_mock.assert_has_calls(
      [call("!XPOS 13.380 9.000 12"), call("!YPOS 12.240 9.000 8")]
    )

  async def test_set_strip(self):
    plate = AGenBio_96_wellplate_Ub_2200ul("test_plate")
    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode="absorbance",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      kinetic_settings=None,
      spectrum_settings=None,
    )
    await self.device._set_strip(settings)
    self.send_command_mock.assert_called_once_with("!STRIP 1 12")

  async def test_set_shake(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode="absorbance",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      kinetic_settings=None,
      spectrum_settings=None,
    )
    await self.device._set_shake(settings)
    self.send_command_mock.assert_called_once_with("!SHAKE OFF")

    self.send_command_mock.reset_mock()
    settings.shake_settings = ShakeSettings(before_read=True, before_read_duration=5)
    await self.device._set_shake(settings)
    self.send_command_mock.assert_has_calls([call("!SHAKE ON"), call("!SHAKE 5 0 0 0 0")])

    self.send_command_mock.reset_mock()
    settings.shake_settings = ShakeSettings(between_reads=True, between_reads_duration=3)
    settings.kinetic_settings = KineticSettings(interval=10, num_readings=5)
    await self.device._set_shake(settings)
    self.send_command_mock.assert_has_calls([call("!SHAKE ON"), call("!SHAKE 0 10 7 3 0")])

  async def test_set_carriage_speed(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode="absorbance",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      kinetic_settings=None,
      spectrum_settings=None,
    )
    await self.device._set_carriage_speed(settings)
    self.send_command_mock.assert_called_once_with("!CSPEED 8")
    self.send_command_mock.reset_mock()
    settings.carriage_speed = "slow"
    await self.device._set_carriage_speed(settings)
    self.send_command_mock.assert_called_once_with("!CSPEED 1")

  async def test_set_read_stage(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode="fluorescence",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      kinetic_settings=None,
      spectrum_settings=None,
    )
    await self.device._set_read_stage(settings)
    self.send_command_mock.assert_called_once_with("!READSTAGE TOP")
    self.send_command_mock.reset_mock()
    settings.read_from_bottom = True
    await self.device._set_read_stage(settings)
    self.send_command_mock.assert_called_once_with("!READSTAGE BOT")
    self.send_command_mock.reset_mock()
    settings.read_mode = "absorbance"
    await self.device._set_read_stage(settings)
    self.send_command_mock.assert_not_called()

  async def test_set_flashes_per_well(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode="fluorescence",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      flashes_per_well=10,
      kinetic_settings=None,
      spectrum_settings=None,
    )
    await self.device._set_flashes_per_well(settings)
    self.send_command_mock.assert_called_once_with("!FPW 10")
    self.send_command_mock.reset_mock()
    settings.read_mode = "absorbance"
    await self.device._set_flashes_per_well(settings)
    self.send_command_mock.assert_not_called()

  async def test_set_pmt(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode="fluorescence",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      pmt_gain="auto",
      kinetic_settings=None,
      spectrum_settings=None,
    )
    await self.device._set_pmt(settings)
    self.send_command_mock.assert_called_once_with("!AUTOPMT ON")
    self.send_command_mock.reset_mock()
    settings.pmt_gain = "high"
    await self.device._set_pmt(settings)
    self.send_command_mock.assert_has_calls([call("!AUTOPMT OFF"), call("!PMT HIGH")])
    self.send_command_mock.reset_mock()
    settings.pmt_gain = 9
    await self.device._set_pmt(settings)
    self.send_command_mock.assert_has_calls([call("!AUTOPMT OFF"), call("!PMT 9")])
    self.send_command_mock.reset_mock()
    settings.read_mode = "absorbance"
    await self.device._set_pmt(settings)
    self.send_command_mock.assert_not_called()

  async def test_set_filter(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode="fluorescence",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      cutoff_filters=[self.device._get_cutoff_filter_index_from_wavelength(535), 9],
      kinetic_settings=None,
      spectrum_settings=None,
    )
    await self.device._set_filter(settings)
    self.send_command_mock.assert_has_calls([call("!AUTOFILTER OFF"), call("!EMFILTER 8 9")])
    self.send_command_mock.reset_mock()
    settings.cutoff_filters = []
    await self.device._set_filter(settings)
    self.send_command_mock.assert_called_once_with("!AUTOFILTER ON")
    self.send_command_mock.reset_mock()
    settings.read_mode = "absorbance"
    settings.cutoff_filters = [515, 530]
    await self.device._set_filter(settings)
    self.send_command_mock.assert_called_once_with("!AUTOFILTER ON")

  async def test_set_calibrate(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode="absorbance",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      kinetic_settings=None,
      spectrum_settings=None,
    )
    await self.device._set_calibrate(settings)
    self.send_command_mock.assert_called_once_with("!CALIBRATE ON")
    self.send_command_mock.reset_mock()
    settings.read_mode = "fluorescence"
    await self.device._set_calibrate(settings)
    self.send_command_mock.assert_called_once_with("!PMTCAL ON")

  async def test_set_order(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode="absorbance",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      kinetic_settings=None,
      spectrum_settings=None,
    )
    await self.device._set_order(settings)
    self.send_command_mock.assert_called_once_with("!ORDER COLUMN")
    self.send_command_mock.reset_mock()
    settings.read_order = "wavelength"
    await self.device._set_order(settings)
    self.send_command_mock.assert_called_once_with("!ORDER WAVELENGTH")

  async def test_set_speed(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode="absorbance",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=True,
      kinetic_settings=None,
      spectrum_settings=None,
    )
    await self.device._set_speed(settings)
    self.send_command_mock.assert_called_once_with("!SPEED ON")
    self.send_command_mock.reset_mock()
    settings.speed_read = False
    await self.device._set_speed(settings)
    self.send_command_mock.assert_called_once_with("!SPEED OFF")
    self.send_command_mock.reset_mock()
    settings.read_mode = "fluorescence"
    await self.device._set_speed(settings)
    self.send_command_mock.assert_not_called()

  async def test_set_integration_time(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode="time_resolved",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      kinetic_settings=None,
      spectrum_settings=None,
    )
    await self.device._set_integration_time(settings, 10, 100)
    self.send_command_mock.assert_has_calls([call("!COUNTTIMEDELAY 10"), call("!COUNTTIME 0.1")])
    self.send_command_mock.reset_mock()
    settings.read_mode = "absorbance"
    await self.device._set_integration_time(settings, 10, 100)
    self.send_command_mock.assert_not_called()

  async def test_set_nvram_polar(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode="polarization",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      kinetic_settings=None,
      spectrum_settings=None,
      settling_time=5,
    )
    await self.device._set_nvram(settings)
    self.send_command_mock.assert_called_once_with("!NVRAM FPSETTLETIME 5")

  async def test_set_nvram_other(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode="absorbance",
      read_type="endpoint",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      kinetic_settings=None,
      spectrum_settings=None,
      settling_time=10,
    )
    await self.device._set_nvram(settings)
    self.send_command_mock.assert_called_once_with("!NVRAM CARCOL 100")
    self.send_command_mock.reset_mock()
    settings.settling_time = 110
    await self.device._set_nvram(settings)
    self.send_command_mock.assert_called_once_with("!NVRAM CARCOL 110")

  async def test_set_tag(self):
    settings = MolecularDevicesSettings(
      plate=MagicMock(),
      read_mode="polarization",
      read_type="kinetic",
      read_order="column",
      calibrate="on",
      shake_settings=None,
      carriage_speed="normal",
      speed_read=False,
      kinetic_settings=KineticSettings(interval=10, num_readings=5),
      spectrum_settings=None,
    )
    await self.device._set_tag(settings)
    self.send_command_mock.assert_called_once_with("!TAG ON")
    self.send_command_mock.reset_mock()
    settings.read_type = "endpoint"
    await self.device._set_tag(settings)
    self.send_command_mock.assert_called_once_with("!TAG OFF")
    self.send_command_mock.reset_mock()
    settings.read_mode = "absorbance"
    settings.read_type = "kinetic"
    await self.device._set_tag(settings)
    self.send_command_mock.assert_called_once_with("!TAG OFF")

  async def test_read_absorbance(self):
    with (
      patch.object(self.device, "_read_now", new_callable=AsyncMock) as mock_read_now,
      patch.object(self.device, "wait_for_idle", new_callable=AsyncMock) as mock_wait,
      patch.object(
        self.device,
        "_transfer_data",
        new_callable=AsyncMock,
        return_value=[{"data": [[0.1]], "wavelength": 500, "temperature": 25.0, "time": 12345.6}],
      ) as mock_transfer,
    ):
      plate = AGenBio_96_wellplate_Ub_2200ul("test_plate")
      results = await self.device.read_absorbance(plate, plate.get_wells(), 500)

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


class TestSpectraMaxM5(unittest.IsolatedAsyncioTestCase):
  """Tests for SpectraMaxM5 fluorescence and luminescence reads."""

  device: SpectraMaxM5
  mock_serial: MagicMock
  send_command_mock: AsyncMock

  def setUp(self):
    self.mock_serial = MagicMock()
    self.mock_serial.setup = AsyncMock()
    self.mock_serial.stop = AsyncMock()
    self.mock_serial.write = AsyncMock()
    self.mock_serial.readline = AsyncMock(return_value=b"OK>\r\n")

    with patch("pylabrobot.io.serial.Serial", return_value=self.mock_serial):
      self.device = SpectraMaxM5(name="m5", port="COM1")
      self.device.io = self.mock_serial
    self.send_command_mock = patch.object(
      self.device, "send_command", new_callable=AsyncMock
    ).start()
    self.addCleanup(patch.stopall)

  async def test_read_fluorescence(self):
    with (
      patch.object(self.device, "_read_now", new_callable=AsyncMock) as mock_read_now,
      patch.object(self.device, "wait_for_idle", new_callable=AsyncMock) as mock_wait,
      patch.object(
        self.device,
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
      results = await self.device.read_fluorescence(
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
      patch.object(self.device, "_read_now", new_callable=AsyncMock) as mock_read_now,
      patch.object(self.device, "wait_for_idle", new_callable=AsyncMock) as mock_wait,
      patch.object(
        self.device,
        "_transfer_data",
        new_callable=AsyncMock,
        return_value=[
          {"data": [[1000.0]], "em_wavelength": 590, "temperature": 25.0, "time": 12345.6}
        ],
      ) as mock_transfer,
    ):
      plate = AGenBio_96_wellplate_Ub_2200ul("test_plate")
      results = await self.device.read_luminescence(
        plate,
        plate.get_wells(),
        focal_height=0,
        emission_wavelengths=[590],
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
