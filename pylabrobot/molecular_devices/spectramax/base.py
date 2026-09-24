import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Tuple, Union

from pylabrobot.io.serial import Serial
from pylabrobot.molecular_devices.spectramax.errors import ERROR_CODES, SpectraMaxError
from pylabrobot.molecular_devices.spectramax.results import AbsorbanceResult
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well

logger = logging.getLogger(__name__)

RES_TERM_CHAR = b">"
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


MolecularDevicesResponse = List[str]


# ---------------------------------------------------------------------------
# Enums & settings dataclasses
# ---------------------------------------------------------------------------


# The read mode of the plate reader. Selects which read path runs; never sent to the device
# directly, so it needs no wire mapping.
ReadMode = Literal["absorbance", "fluorescence", "luminescence", "polarization", "time_resolved"]

# The type of read to perform.
ReadType = Literal["endpoint", "kinetic", "spectrum", "well_scan"]
READ_TYPE_TO_WIRE: Dict[str, str] = {
  "endpoint": "ENDPOINT",
  "kinetic": "KINETIC",
  "spectrum": "SPECTRUM",
  "well_scan": "WELLSCAN",
}

# The order in which to read the plate wells.
ReadOrder = Literal["column", "wavelength"]
READ_ORDER_TO_WIRE: Dict[str, str] = {"column": "COLUMN", "wavelength": "WAVELENGTH"}

# The calibration mode for the read.
Calibrate = Literal["on", "once", "off"]
CALIBRATE_TO_WIRE: Dict[str, str] = {"on": "ON", "once": "ONCE", "off": "OFF"}

# The speed of the plate carriage.
CarriageSpeed = Literal["normal", "slow"]
CARRIAGE_SPEED_TO_WIRE: Dict[str, str] = {"normal": "8", "slow": "1"}

# The photomultiplier tube gain setting.
PmtGain = Literal["auto", "high", "medium", "low"]
PMT_GAIN_TO_WIRE: Dict[str, str] = {
  "auto": "ON",
  "high": "HIGH",
  "medium": "MED",
  "low": "LOW",
}


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
  pmt_gain: Union[PmtGain, int] = "auto"
  flashes_per_well: int = 1
  cuvette: bool = False
  settling_time: int = 0


# ---------------------------------------------------------------------------
# Driver — serial I/O and device-level operations
# ---------------------------------------------------------------------------


class MolecularDevicesPlateReader:
  """Serial driver for Molecular Devices plate readers.

  Owns the serial connection, command protocol, and device-level operations
  (open/close tray, status, error log, shake).
  """

  def __init__(
    self, port: str, human_readable_device_name: str = "Molecular Devices Plate Reader"
  ) -> None:
    super().__init__()
    self.port = port
    self.io = Serial(
      human_readable_device_name=human_readable_device_name,
      port=self.port,
      baudrate=9600,
      timeout=0.2,
    )

  async def setup(self) -> None:
    await self.io.setup()
    await self.send_command("!")
    logger.info("[SpectraMax %s] connected", self.port)

  async def stop(self) -> None:
    await self.io.stop()
    logger.info("[SpectraMax %s] disconnected", self.port)

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
        logger.error(
          "[SpectraMax %s] timeout waiting for response to command: %s", self.port, command
        )
        raise TimeoutError(f"Timeout waiting for response to command: {command}")
      if raw_response.count(RES_TERM_CHAR) >= num_res_fields:
        break
    response = raw_response.decode("utf-8", errors="replace").strip().split(RES_TERM_CHAR.decode())
    response = [r.strip() for r in response if r.strip() != ""]
    self._parse_basic_errors(response, command)
    return response

  def _parse_basic_errors(self, response: List[str], command: str) -> None:
    if not response:
      logger.error("[SpectraMax %s] command '%s' returned empty response", self.port, command)
      raise SpectraMaxError(f"Command '{command}' failed with empty response.")

    error_code_msg = response[0] if "FAIL" in response[0] else response[-1]
    if "FAIL" in error_code_msg:
      parts = error_code_msg.split("\t")
      try:
        error_code_str = parts[-1]
        error_code = int(error_code_str.strip())
        if error_code in ERROR_CODES:
          message, err_class = ERROR_CODES[error_code]
          raise err_class(f"Command '{command}' failed with error {error_code}: {message}")
        raise SpectraMaxError(f"Command '{command}' failed with unknown error code: {error_code}")
      except (ValueError, IndexError):
        raise SpectraMaxError(f"Command '{command}' failed with unparsable error: {response[0]}")

    if not any("OK" in r for r in response):
      raise SpectraMaxError(f"Command '{command}' failed with response: {response}")
    if "warning" in response[0].lower():
      logger.warning("[SpectraMax %s] warning for command '%s': %s", self.port, command, response)

  # -- device-level operations --

  async def open(self) -> None:
    """Open the plate tray."""
    await self.send_command("!OPEN")

  async def close(self) -> None:
    """Close the plate tray."""
    await self.send_command("!CLOSE")

  async def request_status(self) -> List[str]:
    """Get the current device status."""
    res = await self.send_command("!STATUS")
    if len(res) > 1:
      return res[1].split()
    raise ValueError(f"Could not parse status from response: {res}")

  async def read_error_log(self) -> List[str]:
    """Read the device error log."""
    res = await self.send_command("!ERROR")
    if len(res) > 1:
      return res[1].split()
    raise ValueError(f"Could not parse error log from response: {res}")

  async def clear_error_log(self) -> None:
    """Clear the device error log."""
    await self.send_command("!CLEAR ERROR")

  async def request_firmware_version(self) -> List[str]:
    """Get the firmware version."""
    res = await self.send_command("!OPTION")
    return res[1].split()

  async def start_shake(self) -> None:
    """Start shaking."""
    await self.send_command("!SHAKE NOW")

  async def stop_shake(self) -> None:
    """Stop shaking."""
    await self.send_command("!SHAKE STOP")

  async def wait_for_idle(self, timeout: int = 600):
    """Wait for the plate reader to become idle."""
    start_time = time.time()
    while True:
      if time.time() - start_time > timeout:
        logger.error("[SpectraMax %s] timeout waiting for idle after %ds", self.port, timeout)
        raise TimeoutError("Timeout waiting for plate reader to become idle.")
      status = await self.request_status()
      if status and status[1] == "IDLE":
        break
      await asyncio.sleep(1)

  async def _read_now(self) -> None:
    await self.send_command("!READ")

  async def _transfer_data(self, settings: MolecularDevicesSettings) -> List[Dict]:
    """Transfer data from the plate reader."""

    if (settings.read_type == "kinetic" and settings.kinetic_settings) or (
      settings.read_type == "spectrum" and settings.spectrum_settings
    ):
      if settings.kinetic_settings:
        num_readings = settings.kinetic_settings.num_readings
      elif settings.spectrum_settings:
        num_readings = settings.spectrum_settings.num_steps
      else:
        raise ValueError("Kinetic or Spectrum settings must be provided for this read type.")

      all_reads = []
      for _ in range(num_readings):
        res = await self.send_command("!TRANSFER")
        data_str = res[1]
        read_data = self._parse_data(data_str, settings)
        all_reads.extend(read_data)
      return all_reads

    res = await self.send_command("!TRANSFER")
    data_str = res[1]
    return self._parse_data(data_str, settings)

  def _parse_data(self, data_str: str, settings: MolecularDevicesSettings) -> List[Dict]:
    lines = re.split(r"\r\n|\n", data_str.strip())
    lines = [line.strip() for line in lines if line.strip()]

    header_parts = lines[0].split("\t")
    measurement_time = float(header_parts[0])
    temperature = float(header_parts[1])

    line_idx = 1
    while line_idx < len(lines):
      line = lines[line_idx]
      if line.startswith("L:") and line_idx > 1:
        break
      line_idx += 1

    data_collection = []
    cur_read_wavelengths = []
    data_columns: List[List[float]] = []
    for i in range(line_idx, len(lines)):
      line = lines[i]
      if line.startswith("L:"):
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

    measurements = []
    read_mode = settings.read_mode
    for i, data_rows in enumerate(data_collection_transposed):
      measurement = {
        "data": data_rows,
        "temperature": temperature,
        "time": measurement_time,
      }
      if read_mode == "absorbance":
        wl = int(cur_read_wavelengths[i][0])
        measurement["wavelength"] = wl
      elif read_mode in ("fluorescence", "polarization", "time_resolved"):
        ex_wl = int(cur_read_wavelengths[i][0])
        em_wl = int(cur_read_wavelengths[i][1])
        measurement["ex_wavelength"] = ex_wl
        measurement["em_wavelength"] = em_wl
      elif read_mode == "luminescence":
        em_wl = int(cur_read_wavelengths[i][1])
        measurement["em_wavelength"] = em_wl
      measurements.append(measurement)

    return measurements

  async def _set_clear(self) -> None:
    await self.send_command("!CLEAR DATA")

  async def _set_mode(self, settings: MolecularDevicesSettings) -> None:
    cmd = f"!MODE {READ_TYPE_TO_WIRE[settings.read_type]}"
    if settings.read_type == "kinetic" and settings.kinetic_settings:
      ks = settings.kinetic_settings
      cmd += f" {ks.interval} {ks.num_readings}"
    elif settings.read_type == "spectrum" and settings.spectrum_settings:
      ss = settings.spectrum_settings
      cmd = "!MODE"
      scan_type = ss.excitation_emission_type or "SPECTRUM"
      cmd += f" {scan_type} {ss.start_wavelength} {ss.step} {ss.num_steps}"
    await self.send_command(cmd)

  async def _set_wavelengths(self, settings: MolecularDevicesSettings) -> None:
    if settings.read_mode == "absorbance":
      wl_parts = []
      for wl in settings.wavelengths:
        wl_parts.append(f"F{wl[0]}" if isinstance(wl, tuple) and wl[1] else str(wl))
      wl_str = " ".join(wl_parts)
      if settings.path_check:
        wl_str += " 900 998"
      await self.send_command(f"!WAVELENGTH {wl_str}")
    elif settings.read_mode in ("fluorescence", "polarization", "time_resolved"):
      ex_wl_str = " ".join(map(str, settings.excitation_wavelengths))
      em_wl_str = " ".join(map(str, settings.emission_wavelengths))
      await self.send_command(f"!EXWAVELENGTH {ex_wl_str}")
      await self.send_command(f"!EMWAVELENGTH {em_wl_str}")
    elif settings.read_mode == "luminescence":
      wl_str = " ".join(map(str, settings.emission_wavelengths))
      await self.send_command(f"!EMWAVELENGTH {wl_str}")
    else:
      raise NotImplementedError(f"{settings.read_mode} not supported")

  async def _set_plate_position(self, settings: MolecularDevicesSettings) -> None:
    plate = settings.plate
    num_cols, num_rows, size_y = plate.num_items_x, plate.num_items_y, plate.get_size_y()
    if num_cols < 2 or num_rows < 2:
      raise ValueError("Plate must have at least 2 rows and 2 columns to calculate well spacing.")
    top_left_well = plate.get_item(0)
    if top_left_well.location is None:
      raise ValueError("Top left well location is not set.")
    top_left_well_center = top_left_well.location + top_left_well.get_anchor(x="c", y="c")
    loc_A1 = plate.get_item("A1").location
    loc_A2 = plate.get_item("A2").location
    loc_B1 = plate.get_item("B1").location
    if loc_A1 is None or loc_A2 is None or loc_B1 is None:
      raise ValueError("Well locations for A1, A2, or B1 are not set.")
    dx = loc_A2.x - loc_A1.x
    dy = loc_A1.y - loc_B1.y

    x_pos_cmd = f"!XPOS {top_left_well_center.x:.3f} {dx:.3f} {num_cols}"
    y_pos_cmd = f"!YPOS {size_y - top_left_well_center.y:.3f} {dy:.3f} {num_rows}"
    await self.send_command(x_pos_cmd)
    await self.send_command(y_pos_cmd)

  async def _set_strip(self, settings: MolecularDevicesSettings) -> None:
    await self.send_command(f"!STRIP 1 {settings.plate.num_items_x}")

  async def _set_shake(self, settings: MolecularDevicesSettings) -> None:
    if not settings.shake_settings:
      await self.send_command("!SHAKE OFF")
      return
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
    await self.send_command(f"!SHAKE {shake_mode}")
    await self.send_command(f"!SHAKE {before_duration} {ki} {wait_duration} {between_duration} 0")

  async def _set_carriage_speed(self, settings: MolecularDevicesSettings) -> None:
    await self.send_command(f"!CSPEED {CARRIAGE_SPEED_TO_WIRE[settings.carriage_speed]}")

  async def _set_read_stage(self, settings: MolecularDevicesSettings) -> None:
    if settings.read_mode in ("fluorescence", "luminescence", "polarization", "time_resolved"):
      stage = "BOT" if settings.read_from_bottom else "TOP"
      await self.send_command(f"!READSTAGE {stage}")

  async def _set_flashes_per_well(self, settings: MolecularDevicesSettings) -> None:
    if settings.read_mode in ("fluorescence", "luminescence", "polarization", "time_resolved"):
      await self.send_command(f"!FPW {settings.flashes_per_well}")

  async def _set_pmt(self, settings: MolecularDevicesSettings) -> None:
    if settings.read_mode not in ("fluorescence", "luminescence", "polarization", "time_resolved"):
      return
    gain = settings.pmt_gain
    if gain == "auto":
      await self.send_command("!AUTOPMT ON")
    else:
      # gain is either one of the named settings or a raw numeric PMT value.
      gain_val = PMT_GAIN_TO_WIRE[gain] if isinstance(gain, str) else gain
      await self.send_command("!AUTOPMT OFF")
      await self.send_command(f"!PMT {gain_val}")

  async def _set_filter(self, settings: MolecularDevicesSettings) -> None:
    if (
      settings.read_mode in ("fluorescence", "polarization", "time_resolved")
      and settings.cutoff_filters
    ):
      cf_str = " ".join(map(str, settings.cutoff_filters))
      await self.send_command("!AUTOFILTER OFF")
      await self.send_command(f"!EMFILTER {cf_str}")
    else:
      await self.send_command("!AUTOFILTER ON")

  async def _set_calibrate(self, settings: MolecularDevicesSettings) -> None:
    if settings.read_mode == "absorbance":
      await self.send_command(f"!CALIBRATE {CALIBRATE_TO_WIRE[settings.calibrate]}")
    else:
      await self.send_command(f"!PMTCAL {CALIBRATE_TO_WIRE[settings.calibrate]}")

  async def _set_order(self, settings: MolecularDevicesSettings) -> None:
    await self.send_command(f"!ORDER {READ_ORDER_TO_WIRE[settings.read_order]}")

  async def _set_speed(self, settings: MolecularDevicesSettings) -> None:
    if settings.read_mode == "absorbance":
      mode = "ON" if settings.speed_read else "OFF"
      await self.send_command(f"!SPEED {mode}")

  async def _set_nvram(self, settings: MolecularDevicesSettings) -> None:
    if settings.read_mode == "polarization":
      command = "FPSETTLETIME"
      value = settings.settling_time
    else:
      command = "CARCOL"
      value = settings.settling_time if settings.settling_time > 100 else 100
    await self.send_command(f"!NVRAM {command} {value}")

  async def _set_tag(self, settings: MolecularDevicesSettings) -> None:
    if settings.read_mode == "polarization" and settings.read_type == "kinetic":
      await self.send_command("!TAG ON")
    else:
      await self.send_command("!TAG OFF")

  async def _set_readtype(self, settings: MolecularDevicesSettings) -> None:
    """Set the READTYPE command and the expected number of response fields."""
    cuvette = settings.cuvette
    num_res_fields = COMMAND_TERMINATORS.get("!READTYPE", 2)

    if settings.read_mode == "absorbance":
      cmd = f"!READTYPE ABS{'CUV' if cuvette else 'PLA'}"
    elif settings.read_mode == "fluorescence":
      cmd = f"!READTYPE FLU{'CUV' if cuvette else ''}"
      num_res_fields = 2 if cuvette else 1
    elif settings.read_mode == "luminescence":
      cmd = f"!READTYPE LUM{'CUV' if cuvette else ''}"
      num_res_fields = 2 if cuvette else 1
    elif settings.read_mode == "polarization":
      cmd = "!READTYPE POLAR"
      num_res_fields = 1
    elif settings.read_mode == "time_resolved":
      cmd = "!READTYPE TIME 0 250"
      num_res_fields = 1
    else:
      raise ValueError(f"Unsupported read mode: {settings.read_mode}")

    await self.send_command(cmd, num_res_fields=num_res_fields)

  async def _set_integration_time(
    self, settings: MolecularDevicesSettings, delay_time: int, integration_time: int
  ) -> None:
    if settings.read_mode == "time_resolved":
      await self.send_command(f"!COUNTTIMEDELAY {delay_time}")
      await self.send_command(f"!COUNTTIME {integration_time * 0.001}")

  def _get_cutoff_filter_index_from_wavelength(self, wavelength: int) -> int:
    """Converts a wavelength to a cutoff filter index."""
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

  async def read_absorbance(
    self,
    plate: Plate,
    wells: List[Well],
    wavelength: int,
    wavelengths: Optional[List[Union[int, Tuple[int, bool]]]] = None,
    read_type: ReadType = "endpoint",
    read_order: ReadOrder = "column",
    calibrate: Calibrate = "once",
    shake_settings: Optional[ShakeSettings] = None,
    carriage_speed: CarriageSpeed = "normal",
    speed_read: bool = False,
    path_check: bool = False,
    kinetic_settings: Optional[KineticSettings] = None,
    spectrum_settings: Optional[SpectrumSettings] = None,
    cuvette: bool = False,
    settling_time: int = 0,
    timeout: int = 600,
  ) -> List[AbsorbanceResult]:
    """Read absorbance.

    Args:
      plate: Plate to read.
      wells: Wells to read.
      wavelength: Wavelength in nm, used when ``wavelengths`` is not given.
      wavelengths: List of wavelengths to read. Each entry is either an int (wavelength
        in nm) or a tuple of ``(wavelength, pathcheck_reference)`` where the bool
        indicates if that wavelength is used as a PathCheck reference. If None, uses
        ``wavelength``.
      read_type: Read type (endpoint, kinetic, spectrum, or well scan).
      read_order: Read order (column or wavelength).
      calibrate: Calibration mode (on, once, or off).
      shake_settings: Optional shake settings to apply before reading.
      carriage_speed: Carriage speed (normal or slow).
      speed_read: If True, enable speed read mode for faster measurements with reduced
        accuracy.
      path_check: If True, enable PathCheck pathlength correction for absorbance values.
      kinetic_settings: Optional kinetic read settings (for kinetic read type).
      spectrum_settings: Optional spectrum scan settings (for spectrum read type).
      cuvette: If True, read a cuvette instead of a plate.
      settling_time: Settling time in milliseconds before reading.
      timeout: Read timeout in seconds.
    """
    wavelengths = wavelengths if wavelengths is not None else [wavelength]
    logger.info(
      "[SpectraMax %s] read absorbance: plate='%s', wavelengths=%s nm",
      self.port,
      plate.name,
      wavelengths,
    )
    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode="absorbance",
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
    )
    await self._set_clear()
    if not cuvette:
      await self._set_plate_position(settings)
      await self._set_strip(settings)
      await self._set_carriage_speed(settings)

    await self._set_shake(settings)
    await self._set_wavelengths(settings)
    await self._set_calibrate(settings)
    await self._set_mode(settings)
    await self._set_order(settings)
    await self._set_speed(settings)
    await self._set_tag(settings)
    await self._set_nvram(settings)
    await self._set_readtype(settings)

    await self._read_now()
    await self.wait_for_idle(timeout=timeout)
    dicts = await self._transfer_data(settings)
    return [
      AbsorbanceResult(
        data=d["data"],
        wavelength=d["wavelength"],
        temperature=d["temperature"],
        timestamp=d["time"],
      )
      for d in dicts
    ]

  @property
  def supports_active_cooling(self) -> bool:
    return False

  async def request_temperature(self) -> Tuple[float, float]:
    """Get (current_temp, set_point) from the device."""
    res = await self.send_command("!TEMP")
    if len(res) > 1:
      parts = res[1].split()
    else:
      parts = res[0].replace("OK", "").split()

    if len(parts) >= 2:
      return (float(parts[1]), float(parts[0]))
    raise ValueError(f"Could not parse temperature from response: {res}")

  async def request_current_temperature(self) -> float:
    current, _ = await self.request_temperature()
    logger.info("[SpectraMax %s] read temperature: actual=%.1f C", self.port, current)
    return current

  async def set_temperature(self, temperature: float) -> None:
    if not (0 <= temperature <= 45):
      raise ValueError("Temperature must be between 0 and 45°C.")
    logger.info("[SpectraMax %s] set temperature: target=%.1f C", self.port, temperature)
    await self.send_command(f"!TEMP {temperature}")

  async def deactivate(self) -> None:
    logger.info("[SpectraMax %s] deactivate temperature control", self.port)
    await self.send_command("!TEMP 0")
