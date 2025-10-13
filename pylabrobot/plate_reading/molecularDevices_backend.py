import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Literal, Optional, Union, Tuple, Dict

from pylabrobot.io.serial import Serial
from pylabrobot.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources.plate import Plate

logger = logging.getLogger("pylabrobot")

# This map is a direct translation of the `ConstructCommandList` method in MaxlineModel.cs
# It maps the base command string to the number of terminating characters (response fields) expected.
COMMAND_TERMINATORS: Dict[str, int] = {
    "!AUTOFILTER": 1,
    "!AUTOPMT": 1,
    "!BAUD": 1,
    "!CALIBRATE": 1,
    "!CANCEL": 1,
    "!CLEAR": 1,
    "!CLOSE": 1,
    "!CSPEED": 1,
    "!REFERENCE": 1,
    "!EMFILTER": 1,
    "!EMWAVELENGTH": 1,
    "!ERROR": 2,
    "!EXWAVELENGTH": 1,
    "!FPW": 1,
    "!INIT": 1,
    "!MODE": 1,
    "!NVRAM": 1,
    "!OPEN": 1,
    "!ORDER": 1,
    "!AIR_CAL": 1,
    "!PMT": 1,
    "!PMTCAL": 1,
    "!QUEUE": 2,
    "!READ": 1,
    "!TOP": 1,
    "!READSTAGE": 2,
    "!READTYPE": 2,
    "!RESEND": 1,
    "!RESET": 1,
    "!SHAKE": 1,
    "!SPEED": 2,
    "!STATUS": 2,
    "!STRIP": 1,
    "!TAG": 1,
    "!TEMP": 2,
    "!TRANSFER": 2,
    "!USER_NUMBER": 2,
    "!XPOS": 1,
    "!YPOS": 1,
    "!WAVELENGTH": 1,
    "!WELLSCANMODE": 2,
    "!PATHCAL": 2,
    "!COUNTTIME": 1,
    "!COUNTTIMEDELAY": 1,
}


class MolecularDevicesError(Exception):
  """Exceptions raised by a Molecular Devices plate reader."""


MolecularDevicesResponse = List[str]


class ReadMode(Enum):
  """The read mode of the plate reader (e.g., Absorbance, Fluorescence)."""
  ABS = "ABS"
  FLU = "FLU"
  LUM = "LUM"
  POLAR = "POLAR"
  TIME = "TIME"


class ReadType(Enum):
  """The type of read to perform (e.g., Endpoint, Kinetic)."""
  ENDPOINT = "ENDPOINT"
  KINETIC = "KINETIC"
  SPECTRUM = "SPECTRUM"
  WELL_SCAN = "WELLSCAN"


class ReadOrder(Enum):
  """The order in which to read the plate wells."""
  COLUMN = "COLUMN"
  WAVELENGTH = "WAVELENGTH"


class Calibrate(Enum):
  """The calibration mode for the read."""
  ON = "ON"
  ONCE = "ONCE"
  OFF = "OFF"


class CarriageSpeed(Enum):
  """The speed of the plate carriage."""
  NORMAL = "8"
  SLOW = "1"


class PmtGain(Enum):
  """The photomultiplier tube gain setting."""
  AUTO = "ON"
  HIGH = "HIGH"
  MEDIUM = "MED"
  LOW = "LOW"


@dataclass
class ShakeSettings:
  """Settings for shaking the plate during a read."""
  before_read: bool = False
  before_read_duration: int = 0
  between_reads: bool = False
  between_reads_duration: int = 0


@dataclass
class KineticSettings:
  """Settings for kinetic reads."""
  interval: int
  num_readings: int


@dataclass
class SpectrumSettings:
  """Settings for spectrum reads."""
  start_wavelength: int
  step: int
  num_steps: int
  excitation_emission_type: Optional[Literal["EXSPECTRUM", "EMSPECTRUM"]] = None


@dataclass
class MolecularDevicesSettings:
  """A comprehensive, internal container for all plate reader settings."""
  plate: Plate = field(repr=False)
  read_mode: ReadMode
  read_type: ReadType
  read_order: ReadOrder
  calibrate: Calibrate
  shake_settings: Optional[ShakeSettings]
  carriage_speed: CarriageSpeed
  speed_read: bool
  kinetic_settings: Optional[KineticSettings]
  spectrum_settings: Optional[SpectrumSettings]
  wavelengths: List[Union[int, Tuple[int, bool]]] = field(default_factory=list)
  excitation_wavelengths: List[int] = field(default_factory=list)
  emission_wavelengths: List[int] = field(default_factory=list)
  cutoff_filters: List[int] = field(default_factory=list)
  path_check: bool = False
  read_from_bottom: bool = False
  pmt_gain: Union[PmtGain, int] = PmtGain.AUTO
  flashes_per_well: int = 1
  cuvette: bool = False


class MolecularDevicesBackend(PlateReaderBackend):
  """Backend for Molecular Devices Spectralmax plate readers.

  This backend is a faithful implementation based on the "Maxline" command set
  as detailed in the reverse-engineered C# source code.
  """

  def __init__(self, port: str, res_term_char: bytes = b'>') -> None:
    self.port = port
    self.io = Serial(self.port, baudrate=9600, timeout=1)
    self.res_term_char = res_term_char

  async def setup(self) -> None:
    await self.io.setup()
    await self.send_command("!")

  async def stop(self) -> None:
    await self.io.stop()

  def serialize(self) -> dict:
    return {**super().serialize(), "port": self.port}

  async def send_command(self, command: str, timeout: int = 60, num_res_fields=None) -> MolecularDevicesResponse:
    """Send a command and receive the response, automatically determining the number of
    response fields.
    """
    base_command = command.split(" ")[0]
    if num_res_fields is None:
      num_res_fields = COMMAND_TERMINATORS.get(base_command, 1)
    else:
      num_res_fields = max(1, num_res_fields)

    await self.io.write(command.encode() + b"\r")
    raw_response = b""
    timeout_time = time.time() + timeout
    while True:
      raw_response += await self.io.readline()
      await asyncio.sleep(0.001)
      if time.time() > timeout_time:
        raise TimeoutError(f"Timeout waiting for response to command: {command}")
      if raw_response.count(self.res_term_char) >= num_res_fields:
        break
    logger.debug("[plate reader] Command: %s, Response: %s", command, raw_response)
    response = raw_response.decode("utf-8").strip().split(self.res_term_char.decode())
    response = [r.strip() for r in response if r.strip() != '']
    self._parse_basic_errors(response, command)
    return response

  async def _send_commands(
    self,
    commands: List[Union[Optional[str], Tuple[str, int]]]
  ) -> None:
    """Send a sequence of commands to the plate reader."""
    for command_info in commands:
      if not command_info:
        continue
      if isinstance(command_info, tuple):
        command, num_res_fields = command_info
        await self.send_command(command, num_res_fields=num_res_fields)
      else:
        await self.send_command(command_info)

  def _parse_basic_errors(self, response: List[str], command: str) -> None:
    if not response or 'OK' not in response[0]:
      raise MolecularDevicesError(f"Command '{command}' failed with response: {response}")
    elif 'warning' in response[0].lower():
      logger.warning("Warning for command '%s': %s", command, response)

  async def open(self) -> None:
    await self.send_command("!OPEN")

  async def close(self, plate: Optional[Plate] = None) -> None:
    await self.send_command("!CLOSE")

  async def get_status(self) -> List[str]:
    res = await self.send_command("!STATUS")
    return res[1].split()

  async def read_error_log(self) -> str:
    res = await self.send_command("!ERROR")
    return res[1]

  async def clear_error_log(self) -> None:
    await self.send_command("!CLEAR ERROR")

  async def get_temperature(self) -> Tuple[float, float]:
    res = await self.send_command("!TEMP")
    parts = res[1].split()
    return (float(parts[1]), float(parts[0])) # current, set_point

  async def set_temperature(self, temperature: float) -> None:
    if not (0 <= temperature <= 45):
      raise ValueError("Temperature must be between 0 and 45°C.")
    await self.send_command(f"!TEMP {temperature}")

  async def get_firmware_version(self) -> str:
    await self.io.write(b"!OPTIONS\r")
    raw_response = b""
    timeout_time = time.time() + 10
    while True:
      raw_response += await self.io.read()
      await asyncio.sleep(0.001)
      if time.time() > timeout_time:
        raise TimeoutError("Timeout waiting for firmware version.")
      if raw_response.count(self.res_term_char) >= 1: # !OPTIONS is not in the map, assume 1
        break
    response_str = raw_response.decode("utf-8")
    lines = response_str.strip().split('\n')
    return lines[5].strip() if len(lines) >= 6 else lines[-1].strip().replace(">", "").strip()

  async def start_shake(self) -> None:
    await self.send_command("!SHAKE NOW")

  async def stop_shake(self) -> None:
    await self.send_command("!SHAKE STOP")

  async def _read_now(self) -> None:
    await self.send_command("!READ")

  async def _transfer_data(self) -> str:
    res = await self.send_command("!TRANSFER")
    return res[1]

  def _parse_data(self, data_str: str) -> List[List[float]]:
    data = []
    rows = data_str.strip().split('\r')
    for row in rows:
      if not row:
        continue
      try:
        values_str = row.strip().split('\t')
        if len(values_str) == 1:
          values_str = row.strip().split()
        values = [float(v) for v in values_str]
        data.append(values)
      except (ValueError, IndexError):
        logger.warning("Could not parse row: %s", row)
    return data

  def _get_clear_command(self) -> str:
    return "!CLEAR DATA"

  def _get_mode_command(self, settings: MolecularDevicesSettings) -> str:
    cmd = f"!MODE {settings.read_type.value}"
    if settings.read_type == ReadType.KINETIC and settings.kinetic_settings:
      ks = settings.kinetic_settings
      cmd += f" {ks.interval} {ks.num_readings}"
    elif settings.read_type == ReadType.SPECTRUM and settings.spectrum_settings:
      ss = settings.spectrum_settings
      scan_type = ss.excitation_emission_type or "SPECTRUM"
      cmd += f" {scan_type} {ss.start_wavelength} {ss.step} {ss.num_steps}"
    return cmd

  def _get_wavelength_commands(self, settings: MolecularDevicesSettings) -> List[str]:
    if settings.read_mode == ReadMode.ABS:
      wl_parts = []
      for wl in settings.wavelengths:
        wl_parts.append(f"F{wl[0]}" if isinstance(wl, tuple) and wl[1] else str(wl))
      wl_str = " ".join(wl_parts)
      if settings.path_check:
        wl_str += " 900 998"
      return [f"!WAVELENGTH {wl_str}"]
    if settings.read_mode in (ReadMode.FLU, ReadMode.POLAR, ReadMode.TIME):
      ex_wl_str = " ".join(map(str, settings.excitation_wavelengths))
      em_wl_str = " ".join(map(str, settings.emission_wavelengths))
      return [f"!EXWAVELENGTH {ex_wl_str}", f"!EMWAVELENGTH {em_wl_str}"]
    if settings.read_mode == ReadMode.LUM:
      wl_str = " ".join(map(str, settings.emission_wavelengths))
      return [f"!EMWAVELENGTH {wl_str}"]
    return []

  def _get_plate_position_commands(self, settings: MolecularDevicesSettings) -> List[str]:
    plate = settings.plate
    num_cols, num_rows, size_y = plate.num_items_x, plate.num_items_y, plate.get_size_y()
    if num_cols < 2 or num_rows < 2:
        raise ValueError("Plate must have at least 2 rows and 2 columns to calculate well spacing.")
    top_left_well = plate.get_item(0)
    top_left_well_center=top_left_well.location + top_left_well.get_anchor(x="c", y="c")
    loc_A1 = plate.get_item("A1").location
    loc_A2 = plate.get_item("A2").location
    loc_B1 = plate.get_item("B1").location
    dx = loc_A2.x - loc_A1.x
    dy = loc_A1.y - loc_B1.y

    x_pos_cmd = f"!XPOS {top_left_well_center.x:.3f} {dx:.3f} {num_cols}"
    y_pos_cmd = f"!YPOS {size_y-top_left_well_center.y:.3f} {dy:.3f} {num_rows}"
    return [x_pos_cmd, y_pos_cmd]

  def _get_strip_command(self, settings: MolecularDevicesSettings) -> str:
    return f"!STRIP 1 {settings.plate.num_items_x}"

  def _get_shake_commands(self, settings: MolecularDevicesSettings) -> List[str]:
    if not settings.shake_settings:
      return ["!SHAKE OFF"]
    ss = settings.shake_settings
    shake_mode = "ON" if ss.before_read or ss.between_reads else "OFF"
    before_duration = ss.before_read_duration if ss.before_read else 0
    ki = settings.kinetic_settings.interval if settings.kinetic_settings else 0
    if ss.between_reads and ki > 0:
      between_duration = ss.between_reads_duration
      wait_duration = ki - between_duration
    else:
      between_duration = 0
      wait_duration = 0
    return [
        f"!SHAKE {shake_mode}",
        f"!SHAKE {before_duration} {ki} {wait_duration} {between_duration} 0"
    ]

  def _get_carriage_speed_command(self, settings: MolecularDevicesSettings) -> str:
    return f"!CSPEED {settings.carriage_speed.value}"

  def _get_read_stage_command(self, settings: MolecularDevicesSettings) -> Optional[str]:
    if settings.read_mode in (ReadMode.FLU, ReadMode.LUM, ReadMode.POLAR, ReadMode.TIME):
      stage = "BOT" if settings.read_from_bottom else "TOP"
      return f"!READSTAGE {stage}"
    return None

  def _get_flashes_per_well_command(self, settings: MolecularDevicesSettings) -> Optional[str]:
    if settings.read_mode in (ReadMode.FLU, ReadMode.LUM, ReadMode.POLAR, ReadMode.TIME):
      return f"!FPW {settings.flashes_per_well}"
    return None

  def _get_pmt_commands(self, settings: MolecularDevicesSettings) -> List[str]:
    if settings.read_mode not in (ReadMode.FLU, ReadMode.LUM, ReadMode.POLAR, ReadMode.TIME):
      return []
    gain = settings.pmt_gain
    if gain == PmtGain.AUTO:
      return ["!AUTOPMT ON"]
    gain_val = gain.value if isinstance(gain, PmtGain) else gain
    return ["!AUTOPMT OFF", f"!PMT {gain_val}"]

  def _get_filter_commands(self, settings: MolecularDevicesSettings) -> List[str]:
    if settings.read_mode in (ReadMode.FLU, ReadMode.POLAR, ReadMode.TIME) and settings.cutoff_filters:
      cf_str = " ".join(map(str, settings.cutoff_filters))
      return ["!AUTOFILTER OFF", f"!EMFILTER {cf_str}"]
    return []

  def _get_calibrate_command(self, settings: MolecularDevicesSettings) -> str:
    if settings.read_mode == ReadMode.ABS:
      return f"!CALIBRATE {settings.calibrate.value}"
    return f"!PMTCAL {settings.calibrate.value}"

  def _get_order_command(self, settings: MolecularDevicesSettings) -> str:
    return f"!ORDER {settings.read_order.value}"

  def _get_speed_command(self, settings: MolecularDevicesSettings) -> Optional[str]:
    if settings.read_mode == ReadMode.ABS:
      mode = "ON" if settings.speed_read else "OFF"
      return f"!SPEED {mode}"
    return None

  def _get_readtype_command(self, settings: MolecularDevicesSettings) -> Tuple[str, int]:
    """Get the READTYPE command and the expected number of response fields."""
    cuvette = settings.cuvette
    num_res_fields = COMMAND_TERMINATORS.get("!READTYPE", 2)

    if settings.read_mode == ReadMode.ABS:
      cmd = f"!READTYPE ABS{'CUV' if cuvette else 'PLA'}"
    elif settings.read_mode == ReadMode.FLU:
      cmd = f"!READTYPE FLU{'CUV' if cuvette else ''}"
      num_res_fields = 2 if cuvette else 1
    elif settings.read_mode == ReadMode.LUM:
      cmd = f"!READTYPE LUM{'CUV' if cuvette else ''}"
      num_res_fields = 2 if cuvette else 1
    elif settings.read_mode == ReadMode.POLAR:
      cmd = "!READTYPE POLAR"
    elif settings.read_mode == ReadMode.TIME:
      cmd = "!READTYPE TIME 0 250"
      num_res_fields = 1
    else:
      raise ValueError(f"Unsupported read mode: {settings.read_mode}")

    return (cmd, num_res_fields)

  def _get_integration_time_commands(
    self,
    settings: MolecularDevicesSettings,
    delay_time: int,
    integration_time: int
  ) -> List[str]:
    if settings.read_mode == ReadMode.TIME:
        return [
            f"!COUNTTIMEDELAY {delay_time}",
            f"!COUNTTIME {integration_time * 0.001}"
        ]
    return []

  async def _wait_for_idle(self, timeout: int = 120):
    """Wait for the plate reader to become idle."""
    start_time = time.time()
    while True:
      if time.time() - start_time > timeout:
        raise TimeoutError("Timeout waiting for plate reader to become idle.")
      status = await self.get_status()
      if status and status[1] == "IDLE":
        break
      await asyncio.sleep(1)

  async def read_absorbance(
      self,
      plate: Plate,
      wavelengths: List[Union[int, Tuple[int, bool]]],
      read_type: ReadType = ReadType.ENDPOINT,
      read_order: ReadOrder = ReadOrder.COLUMN,
      calibrate: Calibrate = Calibrate.ONCE,
      shake_settings: Optional[ShakeSettings] = None,
      carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
      speed_read: bool = False,
      path_check: bool = False,
      kinetic_settings: Optional[KineticSettings] = None,
      spectrum_settings: Optional[SpectrumSettings] = None,
      cuvette: bool = False
  ) -> List[List[float]]:
    settings = MolecularDevicesSettings(
        plate=plate, read_mode=ReadMode.ABS, read_type=read_type,
        read_order=read_order, calibrate=calibrate, shake_settings=shake_settings,
        carriage_speed=carriage_speed, speed_read=speed_read, path_check=path_check,
        kinetic_settings=kinetic_settings, spectrum_settings=spectrum_settings,
        wavelengths=wavelengths, cuvette=cuvette
    )
    commands: List[Union[Optional[str], Tuple[str, int]]] = [self._get_clear_command()]
    if not cuvette:
      # commands.extend(self._get_plate_position_commands(settings))
      commands.extend([
        self._get_strip_command(settings),
        self._get_carriage_speed_command(settings)
      ])
    commands.extend([
        *self._get_shake_commands(settings),
        *self._get_wavelength_commands(settings),
        self._get_calibrate_command(settings),
        self._get_mode_command(settings),
        self._get_order_command(settings),
        self._get_speed_command(settings),
        self._get_readtype_command(settings)
    ])

    await self._send_commands(commands)
    await self._read_now()
    await self._wait_for_idle()
    data_str = await self._transfer_data()
    return self._parse_data(data_str)

  async def read_fluorescence(
      self,
      plate: Plate,
      excitation_wavelengths: List[int],
      emission_wavelengths: List[int],
      cutoff_filters: List[int],
      read_type: ReadType = ReadType.ENDPOINT,
      read_order: ReadOrder = ReadOrder.COLUMN,
      calibrate: Calibrate = Calibrate.ONCE,
      shake_settings: Optional[ShakeSettings] = None,
      carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
      read_from_bottom: bool = False,
      pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
      flashes_per_well: int = 10,
      kinetic_settings: Optional[KineticSettings] = None,
      spectrum_settings: Optional[SpectrumSettings] = None,
      cuvette: bool = False
  ) -> List[List[float]]:
    settings = MolecularDevicesSettings(
        plate=plate, read_mode=ReadMode.FLU, read_type=read_type,
        read_order=read_order, calibrate=calibrate, shake_settings=shake_settings,
        carriage_speed=carriage_speed, read_from_bottom=read_from_bottom,
        pmt_gain=pmt_gain, flashes_per_well=flashes_per_well,
        kinetic_settings=kinetic_settings, spectrum_settings=spectrum_settings,
        excitation_wavelengths=excitation_wavelengths,
        emission_wavelengths=emission_wavelengths, cutoff_filters=cutoff_filters,
        cuvette=cuvette,speed_read=False
    )
    commands: List[Union[Optional[str], Tuple[str, int]]] = [self._get_clear_command()]
    # commands.append(self._get_read_stage_command(settings))
    if not cuvette:
        commands.extend(self._get_plate_position_commands(settings))
        commands.extend([
          self._get_strip_command(settings),
          self._get_carriage_speed_command(settings)
        ])
    commands.extend([
        *self._get_shake_commands(settings),
        self._get_flashes_per_well_command(settings),
        *self._get_pmt_commands(settings),
        *self._get_wavelength_commands(settings),
        *self._get_filter_commands(settings),
        self._get_calibrate_command(settings),
        self._get_mode_command(settings),
        self._get_order_command(settings),
        self._get_readtype_command(settings)
    ])

    await self._send_commands(commands)
    await self._read_now()
    await self._wait_for_idle()
    data_str = await self._transfer_data()
    return self._parse_data(data_str)

  async def read_luminescence(
      self,
      plate: Plate,
      emission_wavelengths: List[int],
      read_type: ReadType = ReadType.ENDPOINT,
      read_order: ReadOrder = ReadOrder.COLUMN,
      calibrate: Calibrate = Calibrate.ONCE,
      shake_settings: Optional[ShakeSettings] = None,
      carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
      read_from_bottom: bool = False,
      pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
      flashes_per_well: int = 10,
      kinetic_settings: Optional[KineticSettings] = None,
      spectrum_settings: Optional[SpectrumSettings] = None,
      cuvette: bool = False
  ) -> List[List[float]]:
    settings = MolecularDevicesSettings(
        plate=plate, read_mode=ReadMode.LUM, read_type=read_type,
        read_order=read_order, calibrate=calibrate, shake_settings=shake_settings,
        carriage_speed=carriage_speed, read_from_bottom=read_from_bottom,
        pmt_gain=pmt_gain, flashes_per_well=flashes_per_well,
        kinetic_settings=kinetic_settings, spectrum_settings=spectrum_settings,
        emission_wavelengths=emission_wavelengths, cuvette=cuvette, speed_read=False
    )
    commands: List[Union[Optional[str], Tuple[str, int]]] = [
        self._get_clear_command(),
        self._get_read_stage_command(settings)
    ]
    if not cuvette:
        commands.extend(self._get_plate_position_commands(settings))
        commands.extend([
          self._get_strip_command(settings),
          self._get_carriage_speed_command(settings)
        ])
    commands.extend([
        *self._get_shake_commands(settings),
        self._get_flashes_per_well_command(settings),
        *self._get_pmt_commands(settings),
        *self._get_wavelength_commands(settings),
        self._get_calibrate_command(settings),
        self._get_mode_command(settings),
        self._get_order_command(settings),
        self._get_readtype_command(settings)
    ])

    await self._send_commands(commands)
    await self._read_now()
    await self._wait_for_idle()
    data_str = await self._transfer_data()
    return self._parse_data(data_str)

  async def read_fluorescence_polarization(
      self,
      plate: Plate,
      excitation_wavelengths: List[int],
      emission_wavelengths: List[int],
      cutoff_filters: List[int],
      read_type: ReadType = ReadType.ENDPOINT,
      read_order: ReadOrder = ReadOrder.COLUMN,
      calibrate: Calibrate = Calibrate.ONCE,
      shake_settings: Optional[ShakeSettings] = None,
      carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
      read_from_bottom: bool = False,
      pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
      flashes_per_well: int = 10,
      kinetic_settings: Optional[KineticSettings] = None,
      spectrum_settings: Optional[SpectrumSettings] = None,
      cuvette: bool = False
  ) -> List[List[float]]:
    settings = MolecularDevicesSettings(
        plate=plate, read_mode=ReadMode.POLAR, read_type=read_type,
        read_order=read_order, calibrate=calibrate, shake_settings=shake_settings,
        carriage_speed=carriage_speed, read_from_bottom=read_from_bottom,
        pmt_gain=pmt_gain, flashes_per_well=flashes_per_well,
        kinetic_settings=kinetic_settings, spectrum_settings=spectrum_settings,
        excitation_wavelengths=excitation_wavelengths,
        emission_wavelengths=emission_wavelengths, cutoff_filters=cutoff_filters,
        cuvette=cuvette,speed_read=False
    )
    commands: List[Union[Optional[str], Tuple[str, int]]] = [self._get_clear_command()]
    # commands.append(self._get_read_stage_command(settings))
    if not cuvette:
        commands.extend(self._get_plate_position_commands(settings))
        commands.extend([
          self._get_strip_command(settings),
          self._get_carriage_speed_command(settings)
        ])
    commands.extend([
        *self._get_shake_commands(settings),
        self._get_flashes_per_well_command(settings),
        *self._get_pmt_commands(settings),
        *self._get_wavelength_commands(settings),
        *self._get_filter_commands(settings),
        self._get_calibrate_command(settings),
        self._get_mode_command(settings),
        self._get_order_command(settings),
        self._get_readtype_command(settings)
    ])

    await self._send_commands(commands)
    await self._read_now()
    await self._wait_for_idle()
    data_str = await self._transfer_data()
    return self._parse_data(data_str)

  async def read_time_resolved_fluorescence(
      self,
      plate: Plate,
      excitation_wavelengths: List[int],
      emission_wavelengths: List[int],
      cutoff_filters: List[int],
      delay_time: int,
      integration_time: int,
      read_type: ReadType = ReadType.ENDPOINT,
      read_order: ReadOrder = ReadOrder.COLUMN,
      calibrate: Calibrate = Calibrate.ONCE,
      shake_settings: Optional[ShakeSettings] = None,
      carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
      read_from_bottom: bool = False,
      pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
      flashes_per_well: int = 10,
      kinetic_settings: Optional[KineticSettings] = None,
      spectrum_settings: Optional[SpectrumSettings] = None,
      cuvette: bool = False
  ) -> List[List[float]]:
    settings = MolecularDevicesSettings(
        plate=plate, read_mode=ReadMode.TIME, read_type=read_type,
        read_order=read_order, calibrate=calibrate, shake_settings=shake_settings,
        carriage_speed=carriage_speed, read_from_bottom=read_from_bottom,
        pmt_gain=pmt_gain, flashes_per_well=flashes_per_well,
        kinetic_settings=kinetic_settings, spectrum_settings=spectrum_settings,
        excitation_wavelengths=excitation_wavelengths,
        emission_wavelengths=emission_wavelengths, cutoff_filters=cutoff_filters,
        cuvette=cuvette,speed_read=False
    )
    commands: List[Union[Optional[str], Tuple[str, int]]] = [
        self._get_clear_command(),
        self._get_readtype_command(settings),
        *self._get_integration_time_commands(settings, delay_time, integration_time)
    ]
    if not cuvette:
        commands.extend(self._get_plate_position_commands(settings))
        commands.extend([
          self._get_strip_command(settings),
          self._get_carriage_speed_command(settings)
        ])
    commands.extend([
        *self._get_shake_commands(settings),
        self._get_flashes_per_well_command(settings),
        *self._get_pmt_commands(settings),
        *self._get_wavelength_commands(settings),
        *self._get_filter_commands(settings),
        self._get_calibrate_command(settings),
        self._get_mode_command(settings),
        self._get_order_command(settings)
    ])

    await self._send_commands(commands)
    await self._read_now()
    await self._wait_for_idle()
    data_str = await self._transfer_data()
    return self._parse_data(data_str)
