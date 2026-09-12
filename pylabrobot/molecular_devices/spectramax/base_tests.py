from typing import Any, Awaitable, Callable, Dict, List, Optional, cast
from unittest.mock import AsyncMock, MagicMock, call, patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.molecular_devices.spectramax.base import (
  KineticSettings,
  MolecularDevicesPlateReader,
  MolecularDevicesSettings,
  ShakeSettings,
  SpectrumSettings,
)
from pylabrobot.molecular_devices.spectramax.errors import SpectraMaxError
from pylabrobot.molecular_devices.spectramax.results import (
  AbsorbanceResult,
  FluorescenceResult,
  LuminescenceResult,
)
from pylabrobot.molecular_devices.spectramax.spectramax_384_plus import SpectraMax384Plus
from pylabrobot.molecular_devices.spectramax.spectramax_m5 import SpectraMaxM5
from pylabrobot.resources.agenbio.plates import agenbio_96_wellplate_Ub_2200uL
from pylabrobot.testing.concurrency import AnyioTestBase

ReadHook = Callable[[], Awaitable[None]]


class FakeSpectraMaxSerial(HasLifespan):
  """In-memory Serial stand-in with one scripted response per command write."""

  def __init__(
    self,
    script: List[List[bytes]],
    *,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    block_entry: Optional[anyio.Event] = None,
    after_write: Optional[Dict[str, ReadHook]] = None,
    before_read: Optional[Dict[str, ReadHook]] = None,
  ) -> None:
    super().__init__()
    self.script = list(script)
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.block_entry = block_entry
    self.after_write = after_write or {}
    self.before_read = before_read or {}
    self.written: List[str] = []
    self.current_command = ""
    self.response: List[bytes] = []
    self.timeout = 0.2

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeSpectraMaxSerial does not accept lifespan options")
    self.events.append("serial.enter")
    stack.push_shielded_async_callback(self._close)
    if self.entry_error is not None:
      raise self.entry_error
    if self.block_entry is not None:
      self.block_entry.set()
      await anyio.sleep_forever()

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("serial.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def write(self, data: bytes) -> None:
    await anyio.lowlevel.checkpoint()
    self.current_command = data.decode("ascii").rstrip("\r")
    self.written.append(self.current_command)
    self.events.append(f"write:{self.current_command}")
    self.response = self.script.pop(0) if self.script else []
    hook = self.after_write.get(self.current_command)
    if hook is not None:
      await hook()

  async def readline(self) -> bytes:
    await anyio.lowlevel.checkpoint()
    hook = self.before_read.pop(self.current_command, None)
    if hook is not None:
      await hook()
    value = self.response.pop(0) if self.response else b""
    self.events.append(f"read:{self.current_command}:{value!r}")
    return value

  async def reset_input_buffer(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.response.clear()
    self.events.append("input.reset")


class TestMolecularDevicesPlateReader(AnyioTestBase):
  """Tests for MolecularDevicesPlateReader."""

  device: MolecularDevicesPlateReader
  mock_serial: MagicMock
  send_command_mock: AsyncMock

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    self.mock_serial = MagicMock()
    self.mock_serial.setup = AsyncMock()
    self.mock_serial.stop = AsyncMock()
    self.mock_serial.write = AsyncMock()
    self.mock_serial.readline = AsyncMock(return_value=b"OK>\r\n")

    with patch("pylabrobot.io.serial.Serial", return_value=self.mock_serial):
      self.device = MolecularDevicesPlateReader(port="COM1")
      self.device.io = self.mock_serial
    send_command_patcher = patch.object(self.device, "send_command", new_callable=AsyncMock)
    self.send_command_mock = stack.enter_context(send_command_patcher)

  async def test_lifespan_handshake(self):
    with patch.object(
      self.device, "send_command", wraps=self.device.send_command
    ) as wrapped_send_command:
      async with self.device:
        pass
      self.mock_serial.lifespan.assert_called_once_with()
      wrapped_send_command.assert_called_with("!")

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
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
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
    plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
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
      plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
      results = await self.device.read_absorbance(plate, plate.get_wells(), 500)

      assert isinstance(results, list)
      assert len(results) == 1
      assert isinstance(results[0], AbsorbanceResult)
      assert results[0].wavelength == 500
      assert results[0].temperature == 25.0
      assert results[0].timestamp == 12345.6

      commands = [c.args[0] for c in self.send_command_mock.call_args_list]
      assert "!CLEAR DATA" in commands
      assert "!STRIP 1 12" in commands
      assert "!CSPEED 8" in commands
      assert "!SHAKE OFF" in commands
      assert "!WAVELENGTH 500" in commands
      assert "!CALIBRATE ONCE" in commands
      assert "!MODE ENDPOINT" in commands
      assert "!ORDER COLUMN" in commands
      assert "!SPEED OFF" in commands

      readtype_call = next(
        c for c in self.send_command_mock.call_args_list if c.args[0] == "!READTYPE ABSPLA"
      )
      assert readtype_call.kwargs == {"num_res_fields": 2}

      mock_read_now.assert_called_once()
      mock_wait.assert_called_once()
      mock_transfer.assert_called_once()


class TestSpectraMaxM5(AnyioTestBase):
  """Tests for SpectraMaxM5 fluorescence and luminescence reads."""

  device: SpectraMaxM5
  mock_serial: MagicMock
  send_command_mock: AsyncMock

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    self.mock_serial = MagicMock()
    self.mock_serial.setup = AsyncMock()
    self.mock_serial.stop = AsyncMock()
    self.mock_serial.write = AsyncMock()
    self.mock_serial.readline = AsyncMock(return_value=b"OK>\r\n")

    with patch("pylabrobot.io.serial.Serial", return_value=self.mock_serial):
      self.device = SpectraMaxM5(name="m5", port="COM1")
      self.device.io = self.mock_serial
    send_command_patcher = patch.object(self.device, "send_command", new_callable=AsyncMock)
    self.send_command_mock = stack.enter_context(send_command_patcher)

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
      plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
      results = await self.device.read_fluorescence(
        plate, plate.get_wells(), excitation_wavelength=485, emission_wavelength=520, focal_height=0
      )

      assert isinstance(results, list)
      assert len(results) == 1
      assert isinstance(results[0], FluorescenceResult)
      assert results[0].excitation_wavelength == 485
      assert results[0].emission_wavelength == 520
      assert results[0].temperature == 25.0
      assert results[0].timestamp == 12345.6

      commands = [c.args[0] for c in self.send_command_mock.call_args_list]
      assert "!CLEAR DATA" in commands
      assert any(cmd.startswith("!XPOS") for cmd in commands)
      assert any(cmd.startswith("!YPOS") for cmd in commands)
      assert "!STRIP 1 12" in commands
      assert "!CSPEED 8" in commands
      assert "!SHAKE OFF" in commands
      assert "!FPW 10" in commands
      assert "!AUTOPMT ON" in commands
      assert "!EXWAVELENGTH 485" in commands
      assert "!EMWAVELENGTH 520" in commands
      assert "!PMTCAL ONCE" in commands
      assert "!MODE ENDPOINT" in commands
      assert "!ORDER COLUMN" in commands
      assert "!READSTAGE TOP" in commands

      readtype_call = next(
        c for c in self.send_command_mock.call_args_list if c.args[0] == "!READTYPE FLU"
      )
      assert readtype_call.kwargs == {"num_res_fields": 1}

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
      plate = agenbio_96_wellplate_Ub_2200uL("test_plate")
      results = await self.device.read_luminescence(
        plate,
        plate.get_wells(),
        focal_height=0,
        emission_wavelengths=[590],
      )

      assert isinstance(results, list)
      assert len(results) == 1
      assert isinstance(results[0], LuminescenceResult)
      assert results[0].temperature == 25.0
      assert results[0].timestamp == 12345.6

      commands = [c.args[0] for c in self.send_command_mock.call_args_list]
      assert "!CLEAR DATA" in commands
      assert any(cmd.startswith("!XPOS") for cmd in commands)
      assert any(cmd.startswith("!YPOS") for cmd in commands)
      assert "!STRIP 1 12" in commands
      assert "!CSPEED 8" in commands
      assert "!SHAKE OFF" in commands
      assert "!EMWAVELENGTH 590" in commands
      assert "!PMTCAL ONCE" in commands
      assert "!MODE ENDPOINT" in commands

      mock_read_now.assert_called_once()
      mock_wait.assert_called_once()
      mock_transfer.assert_called_once()


def test_managed_methods_are_inherited() -> None:
  for cls in (MolecularDevicesPlateReader, SpectraMaxM5, SpectraMax384Plus):
    assert cls.__aenter__ is HasLifespan.__aenter__
    assert cls.__aexit__ is HasLifespan.__aexit__
    assert cls.setup is HasLifespan.setup
    assert cls.stop is HasLifespan.stop


class TestSpectraMaxConcurrency(AnyioTestBase):
  def _make(self, script: List[List[bytes]], **fake_kwargs: Any) -> MolecularDevicesPlateReader:
    device = MolecularDevicesPlateReader(port="FAKE")
    device.io = cast(Serial, FakeSpectraMaxSerial(script, **fake_kwargs))
    return device

  async def test_handshake_failure_closes_serial(self) -> None:
    events: List[str] = []
    device = self._make([[b"FAIL\t1>\r\n"]], events=events)

    with pytest.raises(Exception):
      async with device:
        pytest.fail("failed handshake reached body")

    assert events[0:2] == ["serial.enter", "write:!"]
    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_cancelled_serial_entry_closes_serial(self) -> None:
    events: List[str] = []
    entry_started = anyio.Event()
    device = self._make([], events=events, block_entry=entry_started)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(device.__aenter__)
      await entry_started.wait()
      task_group.cancel_scope.cancel()

    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_cancelled_handshake_reconciles_then_closes_serial(self) -> None:
    events: List[str] = []
    read_started = anyio.Event()
    release_response = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def block_read() -> None:
      read_started.set()
      await release_response.wait()

    device = self._make([[b"OK>\r\n"]], events=events, before_read={"!": block_read})

    async def enter_device() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await device.__aenter__()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_device)
      await read_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "serial.exit" not in events
      release_response.set()

    assert any(event.startswith("read:!:b'OK>") for event in events)
    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_body_cancellation_closes_serial(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    device = self._make([[b"OK>\r\n"]], events=events)

    async def use_device() -> None:
      async with device:
        body_started.set()
        await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_device)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_sequential_reuse(self) -> None:
    events: List[str] = []
    device = self._make([[b"OK>\r\n"], [b"OK>\r\n"]], events=events)

    async with device:
      assert device.is_active
    async with device:
      assert device.is_active

    assert events.count("serial.enter") == 2
    assert events.count("serial.exit") == 2

  async def test_serial_close_failure_propagates(self) -> None:
    failure = RuntimeError("serial close failed")
    device = self._make([[b"OK>\r\n"]], exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pass

    assert error_info.value is failure

  async def test_concurrent_commands_hold_complete_transaction_lock(self) -> None:
    first_read_started = anyio.Event()
    release_first = anyio.Event()

    async def block_first_read() -> None:
      first_read_started.set()
      await release_first.wait()

    device = self._make([[b"OK>\r\n"], [b"OK>\r\n"]], before_read={"!OPEN": block_first_read})
    serial = cast(FakeSpectraMaxSerial, device.io)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(device.send_command, "!OPEN")
      await first_read_started.wait()
      task_group.start_soon(device.send_command, "!CLOSE")
      await anyio.sleep(0)
      assert serial.written == ["!OPEN"]
      release_first.set()

    assert serial.written == ["!OPEN", "!CLOSE"]

  async def test_cancelled_command_waiting_for_lock_is_not_written(self) -> None:
    first_read_started = anyio.Event()
    release_first = anyio.Event()
    second_started = anyio.Event()
    second_scopes: List[anyio.CancelScope] = []

    async def block_first_read() -> None:
      first_read_started.set()
      await release_first.wait()

    device = self._make([[b"OK>\r\n"]], before_read={"!OPEN": block_first_read})
    serial = cast(FakeSpectraMaxSerial, device.io)

    async def second_command() -> None:
      with anyio.CancelScope() as scope:
        second_scopes.append(scope)
        second_started.set()
        await device.send_command("!CLOSE")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(device.send_command, "!OPEN")
      await first_read_started.wait()
      task_group.start_soon(second_command)
      await second_started.wait()
      await anyio.sleep(0)
      second_scopes[0].cancel()
      release_first.set()

    assert serial.written == ["!OPEN"]

  async def test_driver_deadline_does_not_change_transport_timeout(self) -> None:
    device = self._make([[]])
    serial = cast(FakeSpectraMaxSerial, device.io)

    with pytest.raises(TimeoutError, match="Timeout waiting for response to command: !OPEN"):
      await device.send_command("!OPEN", timeout=0.01)

    assert serial.timeout == 0.2

  async def test_cancellation_after_write_consumes_response_before_propagating(self) -> None:
    write_finished = anyio.Event()
    release_response = anyio.Event()
    cancelled = anyio.Event()
    returned: List[bool] = []

    async def signal_write() -> None:
      write_finished.set()

    async def block_response() -> None:
      await release_response.wait()

    device = self._make(
      [[b"OK>\r\n"]],
      after_write={"!OPEN": signal_write},
      before_read={"!OPEN": block_response},
    )
    serial = cast(FakeSpectraMaxSerial, device.io)
    scopes: List[anyio.CancelScope] = []

    async def run_command() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          await device.send_command("!OPEN")
          returned.append(True)
      finally:
        cancelled.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_command)
      await write_finished.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert not cancelled.is_set()
      release_response.set()

    assert any(event.startswith("read:!OPEN:b'OK>") for event in serial.events)
    assert returned == []

  @pytest.mark.parametrize("read_command", [False, True])
  async def test_cancellation_during_write_does_not_start_response_reconciliation(
    self, read_command: bool
  ) -> None:
    write_started = anyio.Event()
    release_write = anyio.Event()
    operation_finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    command = "!READ" if read_command else "!OPEN"

    async def finish_non_abandoning_write() -> None:
      write_started.set()
      with anyio.CancelScope(shield=True):
        await release_write.wait()
      await anyio.lowlevel.checkpoint()

    script = [[b"OK>\r\n"]]
    if read_command:
      script.append([b"OK>\r\n"])
    device = self._make(script, after_write={command: finish_non_abandoning_write})
    serial = cast(FakeSpectraMaxSerial, device.io)

    async def run_operation() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          if read_command:
            await device._read_now()
          else:
            await device.send_command(command)
      finally:
        operation_finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_operation)
      await write_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert not operation_finished.is_set()
      release_write.set()

    assert serial.written == [command]
    assert not any(event.startswith(f"read:{command}:") for event in serial.events)

  async def test_cancellation_during_acknowledged_read_sends_cancel_then_resets_input(self) -> None:
    read_written = anyio.Event()
    release_ack = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def signal_read_write() -> None:
      read_written.set()

    async def block_ack() -> None:
      await release_ack.wait()

    device = self._make(
      [[b"OK>\r\n"], [b"OK>\r\n"]],
      after_write={"!READ": signal_read_write},
      before_read={"!READ": block_ack},
    )
    serial = cast(FakeSpectraMaxSerial, device.io)

    async def read_now() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await device._read_now()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(read_now)
      await read_written.wait()
      scopes[0].cancel()
      release_ack.set()

    assert serial.written == ["!READ", "!CANCEL"]
    cancel_write = serial.events.index("write:!CANCEL")
    cancel_read = serial.events.index("read:!CANCEL:b'OK>\\r\\n'")
    input_reset = serial.events.index("input.reset")
    assert cancel_write < cancel_read < input_reset

  async def test_cancelled_active_read_sends_cancel_then_resets_input(self) -> None:
    status_read = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def signal_status_read() -> None:
      status_read.set()

    device = self._make(
      [
        [b"OK>\r\n"],
        [b"OK>\r\nBUSY RUN>\r\n"],
        [b"OK>\r\n"],
      ],
      before_read={"!STATUS": signal_status_read},
    )
    serial = cast(FakeSpectraMaxSerial, device.io)

    async def run_read_cycle() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await device._read_now()
        await device.wait_for_idle(timeout=10)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_read_cycle)
      await status_read.wait()
      await anyio.sleep(0)
      scopes[0].cancel()

    assert serial.written == ["!READ", "!STATUS", "!CANCEL"]
    cancel_write = serial.events.index("write:!CANCEL")
    cancel_read = serial.events.index("read:!CANCEL:b'OK>\\r\\n'")
    input_reset = serial.events.index("input.reset")
    assert cancel_write < cancel_read < input_reset


def _exception_context_contains(error: BaseException, target: BaseException) -> bool:
  while error.__context__ is not None:
    error = error.__context__
    if error is target:
      return True
  return False


class TestSpectraMaxShakingScope(AnyioTestBase):
  def _make(self, reader_type: Any, script: List[List[bytes]], **fake_kwargs: Any) -> Any:
    reader = reader_type(name="reader", port="FAKE")
    reader.io = cast(Serial, FakeSpectraMaxSerial(script, **fake_kwargs))
    return reader

  @pytest.mark.parametrize("reader_type", [SpectraMaxM5, SpectraMax384Plus])
  async def test_scope_uses_only_exact_now_then_stop_commands(self, reader_type: Any) -> None:
    reader = self._make(reader_type, [[b"OK>\r\n"], [b"OK>\r\n"], [b"OK>\r\n"]])
    serial = cast(FakeSpectraMaxSerial, reader.io)

    async with reader:
      async with reader.shaking():
        assert reader._shaking_active

    assert serial.written == ["!", "!SHAKE NOW", "!SHAKE STOP"]

  @pytest.mark.parametrize("reader_type", [SpectraMaxM5, SpectraMax384Plus])
  async def test_scope_body_failure_still_stops(self, reader_type: Any) -> None:
    reader = self._make(reader_type, [[b"OK>\r\n"], [b"OK>\r\n"], [b"OK>\r\n"]])
    serial = cast(FakeSpectraMaxSerial, reader.io)
    body_error = ValueError("body failed")

    async with reader:
      with pytest.raises(ValueError, match="body failed") as error_info:
        async with reader.shaking():
          raise body_error
      assert error_info.value is body_error

    assert serial.written == ["!", "!SHAKE NOW", "!SHAKE STOP"]

  @pytest.mark.parametrize("reader_type", [SpectraMaxM5, SpectraMax384Plus])
  async def test_stop_failure_retains_retry_state_and_body_context(self, reader_type: Any) -> None:
    reader = self._make(
      reader_type,
      [[b"OK>\r\n"], [b"OK>\r\n"], [b"FAIL\t105>\r\n"], [b"OK>\r\n"]],
    )
    serial = cast(FakeSpectraMaxSerial, reader.io)
    body_error = ValueError("body failed")

    async with reader:
      with pytest.raises(SpectraMaxError) as error_info:
        async with reader.shaking():
          raise body_error
      assert _exception_context_contains(error_info.value, body_error)
      assert reader._shaking_active
      assert reader._shaking_state_unknown
      assert reader._shaking_scope_token is None
      await reader.stop_shake()
      assert not reader._shaking_active
      assert not reader._shaking_state_unknown

    assert serial.written == ["!", "!SHAKE NOW", "!SHAKE STOP", "!SHAKE STOP"]

  @pytest.mark.parametrize("reader_type", [SpectraMaxM5, SpectraMax384Plus])
  async def test_cancellation_after_now_commits_stops_before_propagating(
    self, reader_type: Any
  ) -> None:
    now_written = anyio.Event()
    release_now_response = anyio.Event()
    finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def signal_now() -> None:
      now_written.set()

    async def block_now_response() -> None:
      await release_now_response.wait()

    reader = self._make(
      reader_type,
      [[b"OK>\r\n"], [b"OK>\r\n"], [b"OK>\r\n"]],
      after_write={"!SHAKE NOW": signal_now},
      before_read={"!SHAKE NOW": block_now_response},
    )
    serial = cast(FakeSpectraMaxSerial, reader.io)

    async def use_scope() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          async with reader.shaking():
            pytest.fail("cancelled scope reached body")
      finally:
        finished.set()

    async with reader:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_scope)
        await now_written.wait()
        scopes[0].cancel()
        await anyio.sleep(0)
        assert not finished.is_set()
        release_now_response.set()
        await finished.wait()

    assert serial.written == ["!", "!SHAKE NOW", "!SHAKE STOP"]

  @pytest.mark.parametrize("reader_type", [SpectraMaxM5, SpectraMax384Plus])
  async def test_cancellation_waiting_for_transition_lock_is_pre_mutation(
    self, reader_type: Any
  ) -> None:
    reader = self._make(reader_type, [[b"OK>\r\n"]])
    serial = cast(FakeSpectraMaxSerial, reader.io)
    started = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def enter_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        started.set()
        async with reader.shaking():
          pytest.fail("cancelled lock waiter reached body")

    async with reader:
      await reader._shaking_transition_lock.acquire()
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter_scope)
        await started.wait()
        await anyio.sleep(0)
        scopes[0].cancel()
        reader._shaking_transition_lock.release()

    assert serial.written == ["!"]

  @pytest.mark.parametrize("reader_type", [SpectraMaxM5, SpectraMax384Plus])
  async def test_scope_rejects_overlap_and_direct_transitions(self, reader_type: Any) -> None:
    reader = self._make(reader_type, [[b"OK>\r\n"], [b"OK>\r\n"], [b"OK>\r\n"]])
    serial = cast(FakeSpectraMaxSerial, reader.io)
    failures: List[RuntimeError] = []

    async def enter_concurrent_scope() -> None:
      try:
        async with reader.shaking():
          pytest.fail("concurrent scope reached body")
      except RuntimeError as error:
        failures.append(error)

    async with reader:
      async with reader.shaking():
        with pytest.raises(RuntimeError, match="already scoped"):
          async with reader.shaking():
            pytest.fail("nested scope reached body")
        with pytest.raises(RuntimeError, match="scope"):
          await reader.start_shake()
        with pytest.raises(RuntimeError, match="scope"):
          await reader.stop_shake()
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(enter_concurrent_scope)

    assert len(failures) == 1
    assert "already scoped" in str(failures[0])
    assert serial.written == ["!", "!SHAKE NOW", "!SHAKE STOP"]

  @pytest.mark.parametrize("reader_type", [SpectraMaxM5, SpectraMax384Plus])
  async def test_scope_rejects_preexisting_durable_shaking(self, reader_type: Any) -> None:
    reader = self._make(reader_type, [[b"OK>\r\n"], [b"OK>\r\n"], [b"OK>\r\n"]])
    serial = cast(FakeSpectraMaxSerial, reader.io)

    async with reader:
      await reader.start_shake()
      with pytest.raises(RuntimeError, match="already active"):
        async with reader.shaking():
          pytest.fail("scope adopted durable shaking")
      await reader.stop_shake()

    assert serial.written == ["!", "!SHAKE NOW", "!SHAKE STOP"]

  @pytest.mark.parametrize("reader_type", [SpectraMaxM5, SpectraMax384Plus])
  async def test_parent_cleanup_force_stops_and_invalidates_live_scope(
    self, reader_type: Any
  ) -> None:
    reader = self._make(reader_type, [[b"OK>\r\n"], [b"OK>\r\n"], [b"OK>\r\n"]])
    serial = cast(FakeSpectraMaxSerial, reader.io)
    lifespan = reader.lifespan()
    scope = reader.shaking()

    await lifespan.__aenter__()
    await scope.__aenter__()
    await lifespan.__aexit__(None, None, None)
    assert reader._shaking_scope_token is None
    stop_count = serial.written.count("!SHAKE STOP")
    await scope.__aexit__(None, None, None)

    assert serial.written == ["!", "!SHAKE NOW", "!SHAKE STOP"]
    assert serial.written.count("!SHAKE STOP") == stop_count

  @pytest.mark.parametrize("reader_type", [SpectraMaxM5, SpectraMax384Plus])
  async def test_parent_stop_failure_retains_retry_state_across_reentry(
    self, reader_type: Any
  ) -> None:
    reader = self._make(
      reader_type,
      [
        [b"OK>\r\n"],
        [b"OK>\r\n"],
        [b"FAIL\t105>\r\n"],
        [b"OK>\r\n"],
        [b"OK>\r\n"],
        [b"OK>\r\n"],
        [b"OK>\r\n"],
      ],
    )
    serial = cast(FakeSpectraMaxSerial, reader.io)
    lifespan = reader.lifespan()
    stale_scope = reader.shaking()

    await lifespan.__aenter__()
    await stale_scope.__aenter__()
    with pytest.raises(SpectraMaxError):
      await lifespan.__aexit__(None, None, None)
    assert reader._shaking_active
    assert reader._shaking_state_unknown
    assert reader._shaking_scope_token is None

    commands_after_parent_exit = list(serial.written)
    await stale_scope.__aexit__(None, None, None)
    assert serial.written == commands_after_parent_exit

    async with reader:
      await reader.stop_shake()
      assert not reader._shaking_active
      assert not reader._shaking_state_unknown
      async with reader.shaking():
        assert reader._shaking_active

    assert serial.written == [
      "!",
      "!SHAKE NOW",
      "!SHAKE STOP",
      "!",
      "!SHAKE STOP",
      "!SHAKE NOW",
      "!SHAKE STOP",
    ]
