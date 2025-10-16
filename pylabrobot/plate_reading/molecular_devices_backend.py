import asyncio
import logging
import re
import time
from abc import ABCMeta
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Literal, Optional, Tuple, Union

from pylabrobot.io.serial import Serial
from pylabrobot.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources.plate import Plate

logger = logging.getLogger("pylabrobot")


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
  "OPTION": 2,
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


class MolecularDevicesUnrecognizedCommandError(MolecularDevicesError):
  """Unrecognized command errors sent from the computer."""


class MolecularDevicesFirmwareError(MolecularDevicesError):
  """Firmware errors."""


class MolecularDevicesHardwareError(MolecularDevicesError):
  """Hardware errors."""


class MolecularDevicesMotionError(MolecularDevicesError):
  """Motion errors."""


class MolecularDevicesNVRAMError(MolecularDevicesError):
  """NVRAM errors."""


ERROR_CODES: Dict[int, Tuple[str, type]] = {
  100: ("command not found", MolecularDevicesUnrecognizedCommandError),
  101: ("invalid argument", MolecularDevicesUnrecognizedCommandError),
  102: ("too many arguments", MolecularDevicesUnrecognizedCommandError),
  103: ("not enough arguments", MolecularDevicesUnrecognizedCommandError),
  104: ("input line too long", MolecularDevicesUnrecognizedCommandError),
  105: ("command invalid, system busy", MolecularDevicesUnrecognizedCommandError),
  106: ("command invalid, measurement in progress", MolecularDevicesUnrecognizedCommandError),
  107: ("no data to transfer", MolecularDevicesUnrecognizedCommandError),
  108: ("data buffer full", MolecularDevicesUnrecognizedCommandError),
  109: ("error buffer overflow", MolecularDevicesUnrecognizedCommandError),
  110: ("stray light cuvette, door open?", MolecularDevicesUnrecognizedCommandError),
  111: ("invalid read settings", MolecularDevicesUnrecognizedCommandError),
  200: ("assert failed", MolecularDevicesFirmwareError),
  201: ("bad error number", MolecularDevicesFirmwareError),
  202: ("receive queue overflow", MolecularDevicesFirmwareError),
  203: ("serial port parity error", MolecularDevicesFirmwareError),
  204: ("serial port overrun error", MolecularDevicesFirmwareError),
  205: ("serial port framing error", MolecularDevicesFirmwareError),
  206: ("cmd generated too much output", MolecularDevicesFirmwareError),
  207: ("fatal trap", MolecularDevicesFirmwareError),
  208: ("RTOS error", MolecularDevicesFirmwareError),
  209: ("stack overflow", MolecularDevicesFirmwareError),
  210: ("unknown interrupt", MolecularDevicesFirmwareError),
  300: ("thermistor faulty", MolecularDevicesHardwareError),
  301: ("safe temperature limit exceeded", MolecularDevicesHardwareError),
  302: ("low light", MolecularDevicesHardwareError),
  303: ("unable to cal dark current", MolecularDevicesHardwareError),
  304: ("signal level saturation", MolecularDevicesHardwareError),
  305: ("reference level saturation", MolecularDevicesHardwareError),
  306: ("plate air cal fail, low light", MolecularDevicesHardwareError),
  307: ("cuv air ref fail", MolecularDevicesHardwareError),
  308: ("stray light", MolecularDevicesHardwareError),
  312: ("gain calibration failed", MolecularDevicesHardwareError),
  313: ("reference gain check fail", MolecularDevicesHardwareError),
  314: ("low lamp level warning", MolecularDevicesHardwareError),
  315: ("can't find zero order", MolecularDevicesHardwareError),
  316: ("grating motor driver faulty", MolecularDevicesHardwareError),
  317: ("monitor ADC faulty", MolecularDevicesHardwareError),
  400: ("carriage motion error", MolecularDevicesMotionError),
  401: ("filter wheel error", MolecularDevicesMotionError),
  402: ("grating error", MolecularDevicesMotionError),
  403: ("stage error", MolecularDevicesMotionError),
  500: ("NVRAM CRC corrupt", MolecularDevicesNVRAMError),
  501: ("NVRAM Grating cal data bad", MolecularDevicesNVRAMError),
  502: ("NVRAM Cuvette air cal data error", MolecularDevicesNVRAMError),
  503: ("NVRAM Plate air cal data error", MolecularDevicesNVRAMError),
  504: ("NVRAM Carriage offset error", MolecularDevicesNVRAMError),
  505: ("NVRAM Stage offset error", MolecularDevicesNVRAMError),
}


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
  settling_time: int = 0
  is_settling_time_on: bool = False


@dataclass
class MolecularDevicesData:
  """Data from a Molecular Devices plate reader."""

  measurement_time: float
  temperature: float
  data: List[List[float]]


@dataclass
class MolecularDevicesDataAbsorbance(MolecularDevicesData):
  """Absorbance data from a Molecular Devices plate reader."""

  absorbance_wavelength: int
  path_lengths: Optional[List[List[float]]] = None


@dataclass
class MolecularDevicesDataFluorescence(MolecularDevicesData):
  """Fluorescence data from a Molecular Devices plate reader."""

  excitation_wavelength: int
  emission_wavelength: int


@dataclass
class MolecularDevicesDataLuminescence(MolecularDevicesData):
  """Luminescence data from a Molecular Devices plate reader."""

  emission_wavelength: int


@dataclass
class MolecularDevicesDataCollection:
  """A collection of MolecularDevicesData objects from multiple reads."""

  container_type: str
  reads: List["MolecularDevicesData"]
  data_ordering: str


@dataclass
class MolecularDevicesDataCollectionAbsorbance(MolecularDevicesDataCollection):
  """A collection of MolecularDevicesDataAbsorbance objects from multiple reads."""

  reads: List["MolecularDevicesDataAbsorbance"]
  all_absorbance_wavelengths: List[int]


@dataclass
class MolecularDevicesDataCollectionFluorescence(MolecularDevicesDataCollection):
  """A collection of MolecularDevicesDataFluorescence objects from multiple reads."""

  reads: List["MolecularDevicesDataFluorescence"]
  all_excitation_wavelengths: List[int]
  all_emission_wavelengths: List[int]


@dataclass
class MolecularDevicesDataCollectionLuminescence(MolecularDevicesDataCollection):
  """A collection of MolecularDevicesDataLuminescence objects from multiple reads."""

  reads: List["MolecularDevicesDataLuminescence"]
  all_emission_wavelengths: List[int]


class MolecularDevicesBackend(PlateReaderBackend, metaclass=ABCMeta):
  """Backend for Molecular Devices plate readers."""

  def __init__(self, port: str, res_term_char: bytes = b">") -> None:
    self.port = port
    self.io = Serial(self.port, baudrate=9600, timeout=0.2)
    self.res_term_char = res_term_char

  async def setup(self) -> None:
    await self.io.setup()
    await self.send_command("!")

  async def stop(self) -> None:
    await self.io.stop()

  def serialize(self) -> dict:
    return {**super().serialize(), "port": self.port}

  async def send_command(
    self, command: str, timeout: int = 60, num_res_fields=None
  ) -> MolecularDevicesResponse:
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
    response = [r.strip() for r in response if r.strip() != ""]
    self._parse_basic_errors(response, command)
    return response

  async def _send_commands(self, commands: List[Union[Optional[str], Tuple[str, int]]]) -> None:
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
    if not response:
      raise MolecularDevicesError(f"Command '{command}' failed with empty response.")

    # Check for FAIL in the response
    error_code_msg = response[0] if "FAIL" in response[0] else response[-1]
    if "FAIL" in error_code_msg:
      parts = error_code_msg.split("\t")
      try:
        error_code_str = parts[-1]
        error_code = int(error_code_str.strip())
        if error_code in ERROR_CODES:
          message, err_class = ERROR_CODES[error_code]
          raise err_class(f"Command '{command}' failed with error {error_code}: {message}")
        else:
          raise MolecularDevicesError(
            f"Command '{command}' failed with unknown error code: {error_code}"
          )
      except (ValueError, IndexError):
        raise MolecularDevicesError(
          f"Command '{command}' failed with unparsable error: {response[0]}"
        )

    if "OK" not in response[0]:
      raise MolecularDevicesError(f"Command '{command}' failed with response: {response}")
    elif "warning" in response[0].lower():
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
    return (float(parts[1]), float(parts[0]))  # current, set_point

  async def set_temperature(self, temperature: float) -> None:
    if not (0 <= temperature <= 45):
      raise ValueError("Temperature must be between 0 and 45°C.")
    await self.send_command(f"!TEMP {temperature}")

  async def get_firmware_version(self) -> str:
    await self.send_command("!OPTION")

  async def start_shake(self) -> None:
    await self.send_command("!SHAKE NOW")

  async def stop_shake(self) -> None:
    await self.send_command("!SHAKE STOP")

  async def _read_now(self) -> None:
    await self.send_command("!READ")

  async def _transfer_data(
    self, settings: MolecularDevicesSettings
  ) -> "MolecularDevicesDataCollection":
    """Transfer data from the plate reader. For kinetic/spectrum reads, this will transfer data for each
    reading and combine them into a single collection.
    """

    if (settings.read_type == ReadType.KINETIC and settings.kinetic_settings) or (
      settings.read_type == ReadType.SPECTRUM and settings.spectrum_settings
    ):
      num_readings = (
        settings.kinetic_settings.num_readings
        if settings.kinetic_settings
        else settings.spectrum_settings.num_steps
      )
      all_reads: List["MolecularDevicesData"] = []
      collection: Optional["MolecularDevicesDataCollection"] = None

      for _ in range(num_readings):
        res = await self.send_command("!TRANSFER")
        data_str = res[1]
        parsed_data_collection = self._parse_data(data_str)

        if collection is None:
          collection = parsed_data_collection
        all_reads.extend(parsed_data_collection.reads)

      collection.reads = all_reads
      return collection
    else:
      res = await self.send_command("!TRANSFER")
      data_str = res[1]
      return self._parse_data(data_str)

  def _parse_data(self, data_str: str) -> "MolecularDevicesDataCollection":
    lines = re.split(r"\r\n|\n", data_str.strip())
    lines = [line.strip() for line in lines if line.strip()]

    # 1. Parse header
    header_parts = lines[0].split("\t")
    measurement_time = float(header_parts[0])
    temperature = float(header_parts[1])
    container_type = header_parts[2]

    # 2. Parse wavelengths
    absorbance_wavelengths = []
    excitation_wavelengths = []
    emission_wavelengths = []
    line_idx = 1
    while line_idx < len(lines):
      line = lines[line_idx]
      if line.startswith("L:") and line_idx == 1:
        parts = line.split("\t")
        for part in parts[1:]:
          if part.strip():
            absorbance_wavelengths.append(int(part.strip()))
      elif line.startswith("exL:"):
        parts = line.split("\t")
        for part in parts[1:]:
          if part.strip() and part.strip().isdigit():
            excitation_wavelengths.append(int(part.strip()))
      elif line.startswith("emL:"):
        parts = line.split("\t")
        for part in parts[1:]:
          if part.strip():
            emission_wavelengths.append(int(part.strip()))
      elif line.startswith("L:") and line_idx > 1:
        # Data section started
        break
      line_idx += 1
    data_collection = []
    cur_read_wavelengths = []
    # 3. Parse data
    data_columns = []
    # The data section starts at line_idx
    for i in range(line_idx, len(lines)):
      line = lines[i]
      if line.startswith("L:"):
        # start of a new data with different wavelength
        cur_read_wavelengths.append(line.split("\t")[1:])
        if i > line_idx and data_columns:
          data_collection.append(data_columns)
          data_columns = []
      match = re.match(r"^\s*(\d+):\s*(.*)", line)
      if match:
        values_str = re.split(r"\s+", match.group(2).strip())
        values = []
        for v in values_str:
          if v.strip().replace(".", "", 1).isdigit():
            values.append(float(v.strip()))
          elif v.strip() == "#SAT":
            values.append(float("inf"))
          else:
            values.append(float("nan"))
        data_columns.append(values)
    if data_columns:
      data_collection.append(data_columns)

    # 4. Transpose data to be row-major
    data_collection_transposed = []
    for data_columns in data_collection:
      data_rows = []
      if data_columns:
        num_rows = len(data_columns[0])
        num_cols = len(data_columns)
        for i in range(num_rows):
          row = [data_columns[j][i] for j in range(num_cols)]
          data_rows.append(row)
      data_collection_transposed.append(data_rows)

    if absorbance_wavelengths:
      reads = []
      for i, data_rows in enumerate(data_collection_transposed):
        wl = int(cur_read_wavelengths[i][0])
        reads.append(
          MolecularDevicesDataAbsorbance(
            measurement_time=measurement_time,
            temperature=temperature,
            data=data_rows,
            absorbance_wavelength=wl,
            path_lengths=None,
          )
        )
      return MolecularDevicesDataCollectionAbsorbance(
        container_type=container_type,
        reads=reads,
        all_absorbance_wavelengths=absorbance_wavelengths,
        data_ordering="row-major",
      )

    elif excitation_wavelengths and emission_wavelengths:
      reads = []
      for i, data_rows in enumerate(data_collection_transposed):
        ex_wl = int(cur_read_wavelengths[i][0])
        em_wl = int(cur_read_wavelengths[i][1])
        reads.append(
          MolecularDevicesDataFluorescence(
            measurement_time=measurement_time,
            temperature=temperature,
            data=data_rows,
            excitation_wavelength=ex_wl,
            emission_wavelength=em_wl,
          )
        )
      return MolecularDevicesDataCollectionFluorescence(
        container_type=container_type,
        reads=reads,
        all_excitation_wavelengths=excitation_wavelengths,
        all_emission_wavelengths=emission_wavelengths,
        data_ordering="row-major",
      )
    elif emission_wavelengths:
      reads = []
      for i, data_rows in enumerate(data_collection_transposed):
        em_wl = int(cur_read_wavelengths[i][1])
        reads.append(
          MolecularDevicesDataLuminescence(
            measurement_time=measurement_time,
            temperature=temperature,
            data=data_rows,
            emission_wavelength=em_wl,
          )
        )
      return MolecularDevicesDataCollectionLuminescence(
        container_type=container_type,
        reads=reads,
        all_emission_wavelengths=emission_wavelengths,
        data_ordering="row-major",
      )
    # Default to generic MolecularDevicesData if no specific wavelengths found
    raise ValueError("Unable to determine data type from response.")

  def _get_clear_command(self) -> str:
    return "!CLEAR DATA"

  def _get_mode_command(self, settings: MolecularDevicesSettings) -> str:
    cmd = f"!MODE {settings.read_type.value}"
    if settings.read_type == ReadType.KINETIC and settings.kinetic_settings:
      ks = settings.kinetic_settings
      cmd += f" {ks.interval} {ks.num_readings}"
    elif settings.read_type == ReadType.SPECTRUM and settings.spectrum_settings:
      ss = settings.spectrum_settings
      cmd = "!MODE"
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
    top_left_well_center = top_left_well.location + top_left_well.get_anchor(x="c", y="c")
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
      f"!SHAKE {before_duration} {ki} {wait_duration} {between_duration} 0",
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
    if (
      settings.read_mode in (ReadMode.FLU, ReadMode.POLAR, ReadMode.TIME)
      and settings.cutoff_filters
    ):
      cf_str = " ".join(map(str, settings.cutoff_filters))
      return ["!AUTOFILTER OFF", f"!EMFILTER {cf_str}"]
    return ["!AUTOFILTER ON"]

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

  def _get_nvram_commands(self, settings: MolecularDevicesSettings) -> List[str]:
    if settings.read_mode == ReadMode.POLAR:
      command = "FPSETTLETIME"
      value = settings.settling_time if settings.is_settling_time_on else 0
    else:
      command = "CARCOL"
      value = settings.settling_time if settings.is_settling_time_on else 100
    return [f"!NVRAM {command} {value}"]

  def _get_tag_command(self, settings: MolecularDevicesSettings) -> str:
    if settings.read_mode == ReadMode.POLAR and settings.read_type == ReadType.KINETIC:
      return "!TAG ON"
    return "!TAG OFF"

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
      num_res_fields = 1
    elif settings.read_mode == ReadMode.TIME:
      cmd = "!READTYPE TIME 0 250"
      num_res_fields = 1
    else:
      raise ValueError(f"Unsupported read mode: {settings.read_mode}")

    return (cmd, num_res_fields)

  def _get_integration_time_commands(
    self, settings: MolecularDevicesSettings, delay_time: int, integration_time: int
  ) -> List[str]:
    if settings.read_mode == ReadMode.TIME:
      return [f"!COUNTTIMEDELAY {delay_time}", f"!COUNTTIME {integration_time * 0.001}"]
    return []

  def _get_cutoff_filter_index_from_wavelength(self, wavelength: int) -> int:
    """Converts a wavelength to a cutoff filter index."""
    # This map is a direct translation of the `EmissionCutoff.CutoffFilter` in MaxlineModel.cs
    # (min_wavelength, max_wavelength, cutoff_filter_index)
    FILTERS = [
      (0, 322, 1),
      (325, 415, 16),
      (420, 435, 2),
      (435, 455, 3),
      (455, 475, 4),
      (475, 495, 5),
      (495, 515, 6),
      (515, 530, 7),
      (530, 550, 8),
      (550, 570, 9),
      (570, 590, 10),
      (590, 610, 11),
      (610, 630, 12),
      (630, 665, 13),
      (665, 695, 14),
      (695, 900, 15),
    ]
    for min_wl, max_wl, cutoff_filter_index in FILTERS:
      if min_wl <= wavelength < max_wl:
        return cutoff_filter_index
    raise ValueError(f"No cutoff filter found for wavelength {wavelength}")

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
    cuvette: bool = False,
    settling_time: int = 0,
    is_settling_time_on: bool = False,
  ) -> MolecularDevicesDataCollection:
    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode=ReadMode.ABS,
      read_type=read_type,
      read_order=read_order,
      calibrate=calibrate,
      shake_settings=shake_settings,
      carriage_speed=carriage_speed,
      speed_read=speed_read,
      path_check=path_check,
      kinetic_settings=kinetic_settings,
      spectrum_settings=spectrum_settings,
      wavelengths=wavelengths,
      cuvette=cuvette,
      settling_time=settling_time,
      is_settling_time_on=is_settling_time_on,
    )
    commands: List[Union[Optional[str], Tuple[str, int]]] = [self._get_clear_command()]
    if not cuvette:
      # commands.extend()
      commands.extend(
        [
          *self._get_plate_position_commands(settings),
          self._get_strip_command(settings),
          self._get_carriage_speed_command(settings),
        ]
      )
    commands.extend(
      [
        *self._get_shake_commands(settings),
        *self._get_wavelength_commands(settings),
        self._get_calibrate_command(settings),
        self._get_mode_command(settings),
        self._get_order_command(settings),
        self._get_speed_command(settings),
        self._get_tag_command(settings),
        *self._get_nvram_commands(settings),
        self._get_readtype_command(settings),
      ]
    )

    await self._send_commands(commands)
    await self._read_now()
    await self._wait_for_idle()
    return await self._transfer_data(settings)

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
    cuvette: bool = False,
    settling_time: int = 0,
    is_settling_time_on: bool = False,
  ) -> MolecularDevicesDataCollection:
    """use  _get_cutoff_filter_index_from_wavelength for cutoff_filters"""
    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode=ReadMode.FLU,
      read_type=read_type,
      read_order=read_order,
      calibrate=calibrate,
      shake_settings=shake_settings,
      carriage_speed=carriage_speed,
      read_from_bottom=read_from_bottom,
      pmt_gain=pmt_gain,
      flashes_per_well=flashes_per_well,
      kinetic_settings=kinetic_settings,
      spectrum_settings=spectrum_settings,
      excitation_wavelengths=excitation_wavelengths,
      emission_wavelengths=emission_wavelengths,
      cutoff_filters=cutoff_filters,
      cuvette=cuvette,
      speed_read=False,
      settling_time=settling_time,
      is_settling_time_on=is_settling_time_on,
    )
    commands: List[Union[Optional[str], Tuple[str, int]]] = [self._get_clear_command()]
    # commands.append(self._get_read_stage_command(settings))
    if not cuvette:
      commands.extend(self._get_plate_position_commands(settings))
      commands.extend(
        [self._get_strip_command(settings), self._get_carriage_speed_command(settings)]
      )
    commands.extend(
      [
        *self._get_shake_commands(settings),
        self._get_flashes_per_well_command(settings),
        *self._get_pmt_commands(settings),
        *self._get_wavelength_commands(settings),
        *self._get_filter_commands(settings),
        self._get_read_stage_command(settings),
        self._get_calibrate_command(settings),
        self._get_mode_command(settings),
        self._get_order_command(settings),
        self._get_tag_command(settings),
        *self._get_nvram_commands(settings),
        self._get_readtype_command(settings),
      ]
    )

    await self._send_commands(commands)
    await self._read_now()
    await self._wait_for_idle()
    return await self._transfer_data(settings)

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
    flashes_per_well: int = 0,
    kinetic_settings: Optional[KineticSettings] = None,
    spectrum_settings: Optional[SpectrumSettings] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    is_settling_time_on: bool = False,
  ) -> MolecularDevicesDataCollection:
    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode=ReadMode.LUM,
      read_type=read_type,
      read_order=read_order,
      calibrate=calibrate,
      shake_settings=shake_settings,
      carriage_speed=carriage_speed,
      read_from_bottom=read_from_bottom,
      pmt_gain=pmt_gain,
      flashes_per_well=flashes_per_well,
      kinetic_settings=kinetic_settings,
      spectrum_settings=spectrum_settings,
      emission_wavelengths=emission_wavelengths,
      cuvette=cuvette,
      speed_read=False,
      settling_time=settling_time,
      is_settling_time_on=is_settling_time_on,
    )
    commands: List[Union[Optional[str], Tuple[str, int]]] = [
      self._get_clear_command(),
      self._get_read_stage_command(settings),
    ]
    if not cuvette:
      commands.extend(self._get_plate_position_commands(settings))
      commands.extend(
        [self._get_strip_command(settings), self._get_carriage_speed_command(settings)]
      )
    commands.extend(
      [
        *self._get_shake_commands(settings),
        *self._get_pmt_commands(settings),
        *self._get_wavelength_commands(settings),
        self._get_read_stage_command(settings),
        self._get_calibrate_command(settings),
        self._get_mode_command(settings),
        self._get_order_command(settings),
        self._get_tag_command(settings),
        *self._get_nvram_commands(settings),
        self._get_readtype_command(settings),
      ]
    )

    await self._send_commands(commands)
    await self._read_now()
    await self._wait_for_idle()
    return await self._transfer_data(settings)

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
    cuvette: bool = False,
    settling_time: int = 0,
    is_settling_time_on: bool = False,
  ) -> MolecularDevicesDataCollection:
    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode=ReadMode.POLAR,
      read_type=read_type,
      read_order=read_order,
      calibrate=calibrate,
      shake_settings=shake_settings,
      carriage_speed=carriage_speed,
      read_from_bottom=read_from_bottom,
      pmt_gain=pmt_gain,
      flashes_per_well=flashes_per_well,
      kinetic_settings=kinetic_settings,
      spectrum_settings=spectrum_settings,
      excitation_wavelengths=excitation_wavelengths,
      emission_wavelengths=emission_wavelengths,
      cutoff_filters=cutoff_filters,
      cuvette=cuvette,
      speed_read=False,
      settling_time=settling_time,
      is_settling_time_on=is_settling_time_on,
    )
    commands: List[Union[Optional[str], Tuple[str, int]]] = [self._get_clear_command()]
    # commands.append(self._get_read_stage_command(settings))
    if not cuvette:
      commands.extend(self._get_plate_position_commands(settings))
      commands.extend(
        [self._get_strip_command(settings), self._get_carriage_speed_command(settings)]
      )
    commands.extend(
      [
        *self._get_shake_commands(settings),
        self._get_flashes_per_well_command(settings),
        *self._get_pmt_commands(settings),
        *self._get_wavelength_commands(settings),
        *self._get_filter_commands(settings),
        self._get_read_stage_command(settings),
        self._get_calibrate_command(settings),
        self._get_mode_command(settings),
        self._get_order_command(settings),
        self._get_tag_command(settings),
        *self._get_nvram_commands(settings),
        self._get_readtype_command(settings),
      ]
    )

    await self._send_commands(commands)
    await self._read_now()
    await self._wait_for_idle()
    return await self._transfer_data(settings)

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
    flashes_per_well: int = 50,
    kinetic_settings: Optional[KineticSettings] = None,
    spectrum_settings: Optional[SpectrumSettings] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    is_settling_time_on: bool = False,
  ) -> MolecularDevicesDataCollection:
    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode=ReadMode.TIME,
      read_type=read_type,
      read_order=read_order,
      calibrate=calibrate,
      shake_settings=shake_settings,
      carriage_speed=carriage_speed,
      read_from_bottom=read_from_bottom,
      pmt_gain=pmt_gain,
      flashes_per_well=flashes_per_well,
      kinetic_settings=kinetic_settings,
      spectrum_settings=spectrum_settings,
      excitation_wavelengths=excitation_wavelengths,
      emission_wavelengths=emission_wavelengths,
      cutoff_filters=cutoff_filters,
      cuvette=cuvette,
      speed_read=False,
      settling_time=settling_time,
      is_settling_time_on=is_settling_time_on,
    )
    commands: List[Union[Optional[str], Tuple[str, int]]] = [
      self._get_clear_command(),
      self._get_readtype_command(settings),
      *self._get_integration_time_commands(settings, delay_time, integration_time),
    ]
    if not cuvette:
      commands.extend(self._get_plate_position_commands(settings))
      commands.extend(
        [self._get_strip_command(settings), self._get_carriage_speed_command(settings)]
      )
    commands.extend(
      [
        *self._get_shake_commands(settings),
        self._get_flashes_per_well_command(settings),
        *self._get_pmt_commands(settings),
        *self._get_wavelength_commands(settings),
        *self._get_filter_commands(settings),
        self._get_calibrate_command(settings),
        self._get_read_stage_command(settings),
        self._get_mode_command(settings),
        self._get_order_command(settings),
        self._get_tag_command(settings),
        *self._get_nvram_commands(settings),
      ]
    )

    await self._send_commands(commands)
    await self._read_now()
    await self._wait_for_idle()
    return await self._transfer_data(settings)
