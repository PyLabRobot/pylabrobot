import logging
import statistics
import time
from typing import Dict, List, Optional, Union

from pylabrobot.plate_reading.backend import PlateReaderBackend
from pylabrobot.plate_reading.utils import _get_min_max_row_col_tuples
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well

from .controls.config_control import ConfigControl
from .controls.data_control import DataControl
from .controls.measurement_control import MeasurementControl
from .controls.optics_control import OpticsControl
from .controls.plate_transport_control import PlateControl
from .controls.sensor_control import SensorControl
from .controls.spark_enums import (
  FilterType,
  FluorescenceCarrier,
  InstrumentMessageType,
  MeasurementMode,
  MirrorType,
  MovementSpeed,
  PlatePosition,
  ScanDirection,
)
from .controls.system_control import SystemControl
from .enums import SparkDevice
from .spark_processor import (
  process_absorbance,
  process_absorbance_spectrum,
  process_fluorescence,
  process_fluorescence_spectrum,
)
from .spark_reader_async import SparkReaderAsync

logger = logging.getLogger(__name__)


class ExperimentalSparkBackend(PlateReaderBackend):
  """Backend for Tecan Spark plate reader."""

  def __init__(self, vid: int = 0x0C47) -> None:
    self.vid = vid
    self.reader = SparkReaderAsync(vid=self.vid)

    # Initialize controls
    self.config_control = ConfigControl(self.reader.send_command)
    self.plate_control = PlateControl(self.reader.send_command)
    self.measurement_control = MeasurementControl(self.reader.send_command)
    self.optics_control = OpticsControl(self.reader.send_command)
    self.system_control = SystemControl(self.reader.send_command)
    self.sensor_control = SensorControl(self.reader.send_command)
    self.data_control = DataControl(self.reader.send_command)

  async def setup(self) -> None:
    """Set up the plate reader."""
    await self.reader.connect()
    await self.config_control.init_module()
    await self.data_control.turn_all_interval_messages_off()

  async def get_average_temperature(self) -> Optional[float]:
    """Calculate average chamber temperature from recorded messages (ID 100)."""
    temp_msgs = [m for m in self.reader.msgs if m.get("number") == 100]
    if not temp_msgs:
      return None

    temps = []
    for msg in temp_msgs:
      try:
        temps.append(float(msg["args"][0]))
      except (IndexError, ValueError, KeyError):
        continue

    if not temps:
      return None

    return statistics.mean(temps) / 100.0

  async def stop(self) -> None:
    """Close connections."""
    await self.reader.close()

  async def open(self) -> None:
    """Move the plate carrier out."""
    await self.plate_control.move_to_position(PlatePosition.OUT_RIGHT)

  async def close(self, plate: Optional[Plate] = None) -> None:
    """Move the plate carrier in."""
    await self.plate_control.move_to_position(PlatePosition.PLATE_IN)

  async def scan_plate_range(
    self, plate: Plate, wells: Optional[List[Well]], z: float = 9150
  ) -> None:
    """Scan the plate range."""
    num_cols, num_rows, size_y = plate.num_items_x, plate.num_items_y, plate.get_size_y()
    top_left_well = plate.get_item(0)
    if top_left_well.location is None:
      raise ValueError("Top left well location is not set.")
    top_left_well_center = top_left_well.location + top_left_well.get_anchor(x="c", y="c")
    dx = plate.item_dx
    dy = plate.item_dy

    # Determine rectangles to scan
    if wells is None:
      # Scan entire plate
      rects = [(0, 0, num_rows - 1, num_cols - 1)]
    else:
      rects = _get_min_max_row_col_tuples(wells, plate)

    for min_row, min_col, max_row, max_col in rects:
      for row_idx in range(min_row, max_row + 1):
        y_pos = round((size_y - top_left_well_center.y + dy * row_idx) * 1000)

        start_x = round((top_left_well_center.x + dx * min_col) * 1000)
        end_x = round((top_left_well_center.x + dx * max_col) * 1000)
        num_points_x = max_col - min_col + 1
        await self.measurement_control.measure_range_in_x_pointwise(
          start_x, end_x, y_pos, z, num_points_x
        )

  async def _run_measurement(
    self, device: SparkDevice, plate: Plate, wells: Optional[List[Well]], z: float = 9150
  ) -> List[bytes]:
    """Execute a measurement: start background read, scan plate, collect results.

    This is the shared orchestration for all measurement types.
    """
    bg_task, stop_event, results = await self.reader.start_background_read(device)

    if bg_task is None or stop_event is None or results is None:
      raise RuntimeError(f"Failed to start background read for {device.name}")

    try:
      await self.measurement_control.prepare_instrument(measure_reference=True)
      await self.scan_plate_range(plate, wells, z)
    finally:
      stop_event.set()
      await bg_task
      await self.data_control.turn_all_interval_messages_off()
      await self.measurement_control.end_measurement()

    return results

  async def _setup_absorbance(
    self, wavelength: Union[int, str], bandwidth: int, num_reads: int
  ) -> None:
    """Configure the instrument for an absorbance measurement."""
    self.reader.clear_messages()
    await self.data_control.set_interval(InstrumentMessageType.TEMPERATURE, 200)
    await self.measurement_control.set_measurement_mode(MeasurementMode.ABSORBANCE)
    await self.measurement_control.start_measurement()
    await self.plate_control.set_motor_speed(MovementSpeed.NORMAL)
    await self.measurement_control.set_scan_direction(ScanDirection.UP)
    await self.system_control.set_settle_time(50000)
    await self.measurement_control.set_number_of_reads(num_reads, label=1)
    await self.optics_control.set_excitation_filter(
      FilterType.BANDPASS, wavelength=wavelength, bandwidth=bandwidth, label=1
    )

  async def _setup_fluorescence(
    self,
    ex_wavelength: Union[int, str],
    em_wavelength: int,
    bandwidth: int,
    num_reads: int,
    gain: int,
  ) -> None:
    """Configure the instrument for a fluorescence measurement."""
    self.reader.clear_messages()
    await self.data_control.set_interval(InstrumentMessageType.TEMPERATURE, 200)
    await self.measurement_control.set_measurement_mode(MeasurementMode.FLUORESCENCE_TOP)
    await self.measurement_control.start_measurement()
    await self.plate_control.set_motor_speed(MovementSpeed.NORMAL)

    await self.system_control.set_integration_time(40)
    await self.system_control.set_lag_time(0)
    await self.system_control.set_settle_time(0)

    await self.optics_control.set_beam_diameter(5400)
    await self.optics_control.set_emission_filter(
      FilterType.BANDPASS,
      wavelength=em_wavelength,
      bandwidth=bandwidth,
      carrier=FluorescenceCarrier.MONOCHROMATOR,
    )
    await self.optics_control.set_excitation_filter(
      FilterType.BANDPASS,
      wavelength=ex_wavelength,
      bandwidth=bandwidth,
      carrier=FluorescenceCarrier.MONOCHROMATOR,
    )

    await self.measurement_control.set_scan_direction(ScanDirection.ALTERNATE_UP)
    await self.optics_control.set_mirror(mirror_type=MirrorType.AUTOMATIC)
    await self.optics_control.set_signal_gain(gain)
    await self.measurement_control.set_number_of_reads(num_reads)

  async def read_absorbance(
    self,
    plate: Plate,
    wells: Optional[List[Well]],
    wavelength: int,
    bandwidth: int = 200,
    num_reads: int = 10,
  ) -> List[Dict[str, object]]:
    """Read absorbance."""

    if SparkDevice.ABSORPTION not in self.reader.devices:
      raise RuntimeError(
        "ABSORPTION device is not connected. Cannot perform absorbance measurement."
      )

    await self._setup_absorbance(wavelength * 10, bandwidth, num_reads)
    results = await self._run_measurement(SparkDevice.ABSORPTION, plate, wells)
    measurement_time = time.time()

    data_matrix = process_absorbance(results)
    avg_temp = await self.get_average_temperature()

    return [
      {
        "wavelength": wavelength,
        "time": measurement_time,
        "temperature": avg_temp if avg_temp is not None else 0.0,
        "data": data_matrix,
      }
    ]

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float = 20000,
    bandwidth: int = 200,
    num_reads: int = 30,
    gain: int = 117,
  ) -> List[Dict[str, object]]:
    """Read fluorescence."""

    if SparkDevice.FLUORESCENCE not in self.reader.devices:
      raise RuntimeError(
        "FLUORESCENCE device is not connected. Cannot perform fluorescence measurement."
      )

    await self._setup_fluorescence(
      excitation_wavelength * 10, emission_wavelength * 10, bandwidth, num_reads, gain
    )
    results = await self._run_measurement(SparkDevice.FLUORESCENCE, plate, wells, focal_height)
    measurement_time = time.time()

    data_matrix = process_fluorescence(results)
    avg_temp = await self.get_average_temperature()

    return [
      {
        "ex_wavelength": excitation_wavelength,
        "em_wavelength": emission_wavelength,
        "time": measurement_time,
        "temperature": avg_temp if avg_temp is not None else 0.0,
        "data": data_matrix,
      }
    ]

  async def read_absorbance_spectrum(
    self,
    plate: Plate,
    wells: Optional[List[Well]],
    wavelength_start: int,
    wavelength_end: int,
    wavelength_step: int = 10,
    bandwidth: int = 200,
    num_reads: int = 1,
  ) -> List[Dict[str, object]]:
    """Read absorbance across a range of wavelengths (spectrum scan).

    The Spark firmware handles the wavelength sweep natively via a range syntax.
    A single SCAN command per well position sweeps through all wavelengths.

    Args:
      plate: The plate resource.
      wells: Wells to scan. None = all wells.
      wavelength_start: Start wavelength in nm.
      wavelength_end: End wavelength in nm.
      wavelength_step: Step size in nm (default 10).
      bandwidth: Monochromator bandwidth in deci-tenths of nm (default 200 = 20nm).
      num_reads: Number of flashes per wavelength step (default 1).

    Returns:
      A list of dicts, one per wavelength step. Each contains:
        ``wavelength`` (int), ``time`` (float), ``temperature`` (float),
        ``data`` (List[List[float]]).
    """

    if SparkDevice.ABSORPTION not in self.reader.devices:
      raise RuntimeError(
        "ABSORPTION device is not connected. Cannot perform absorbance spectrum scan."
      )

    if wavelength_start >= wavelength_end:
      raise ValueError("wavelength_start must be less than wavelength_end")
    if wavelength_step <= 0:
      raise ValueError("wavelength_step must be positive")

    # Firmware range syntax: FROM~TO:STEP in deci-tenths of nm
    wavelength_range = f"{wavelength_start * 10}~{wavelength_end * 10}:{wavelength_step * 10}"

    await self._setup_absorbance(wavelength_range, bandwidth, num_reads)
    results = await self._run_measurement(SparkDevice.ABSORPTION, plate, wells)
    measurement_time = time.time()

    spectrum_data = process_absorbance_spectrum(results)
    avg_temp = await self.get_average_temperature()

    return [
      {
        "wavelength": wl,
        "time": measurement_time,
        "temperature": avg_temp if avg_temp is not None else 0.0,
        "data": data_matrix,
      }
      for wl, data_matrix in sorted(spectrum_data.items())
    ]

  async def read_fluorescence_spectrum(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength_start: int,
    excitation_wavelength_end: int,
    emission_wavelength: int,
    excitation_wavelength_step: int = 10,
    focal_height: float = 20000,
    bandwidth: int = 200,
    num_reads: int = 30,
    gain: int = 100,
  ) -> List[Dict[str, object]]:
    """Read fluorescence across a range of excitation wavelengths (spectrum scan).

    The Spark firmware handles the wavelength sweep natively. The emission
    wavelength is fixed, and the excitation wavelength sweeps across the range.

    Args:
      plate: The plate resource.
      wells: Wells to scan.
      excitation_wavelength_start: Start excitation wavelength in nm.
      excitation_wavelength_end: End excitation wavelength in nm.
      emission_wavelength: Fixed emission wavelength in nm.
      excitation_wavelength_step: Step size in nm (default 10).
      focal_height: Z focal height in device units (default 20000).
      bandwidth: Monochromator bandwidth in deci-tenths of nm (default 200 = 20nm).
      num_reads: Number of reads per wavelength step (default 30).
      gain: Signal gain (default 100).

    Returns:
      A list of dicts, one per excitation wavelength step. Each contains:
        ``ex_wavelength`` (float), ``em_wavelength`` (int), ``time`` (float),
        ``temperature`` (float), ``data`` (List[List[float]]).
    """

    if SparkDevice.FLUORESCENCE not in self.reader.devices:
      raise RuntimeError(
        "FLUORESCENCE device is not connected. Cannot perform fluorescence spectrum scan."
      )

    if excitation_wavelength_start >= excitation_wavelength_end:
      raise ValueError("excitation_wavelength_start must be less than excitation_wavelength_end")
    if excitation_wavelength_step <= 0:
      raise ValueError("excitation_wavelength_step must be positive")

    # Firmware range syntax for excitation sweep
    ex_range = (
      f"{excitation_wavelength_start * 10}~"
      f"{excitation_wavelength_end * 10}:{excitation_wavelength_step * 10}"
    )

    await self._setup_fluorescence(ex_range, emission_wavelength * 10, bandwidth, num_reads, gain)
    results = await self._run_measurement(SparkDevice.FLUORESCENCE, plate, wells, focal_height)
    measurement_time = time.time()

    spectrum_data = process_fluorescence_spectrum(results)
    avg_temp = await self.get_average_temperature()

    return [
      {
        "ex_wavelength": ex_wl,
        "em_wavelength": emission_wavelength,
        "time": measurement_time,
        "temperature": avg_temp if avg_temp is not None else 0.0,
        "data": data_matrix,
      }
      for ex_wl, data_matrix in sorted(spectrum_data.items())
    ]

  async def read_luminescence(
    self, plate: Plate, wells: List[Well], focal_height: float
  ) -> List[Dict[str, object]]:
    raise NotImplementedError("Luminescence will be implemented in the future.")
