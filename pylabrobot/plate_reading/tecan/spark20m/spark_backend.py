import logging
import time
from typing import Dict, List, Optional

from pylabrobot.plate_reading.backend import PlateReaderBackend
from pylabrobot.plate_reading.utils import _get_min_max_row_col_tuples
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well

from .controls.config_control import ConfigControl
from .controls.data_control import DataControl
from .controls.measurement_control import MeasurementControl, MeasurementMode, ScanDirection
from .controls.optics_control import FilterType, FluorescenceCarrier, MirrorType, OpticsControl
from .controls.plate_transport_control import MovementSpeed, PlateControl, PlatePosition
from .controls.sensor_control import InstrumentMessageType, SensorControl
from .controls.system_control import SystemControl
from .spark_processor import AbsorbanceProcessor, FluorescenceProcessor
from .spark_reader_async import SparkDevice, SparkEndpoint, SparkReaderAsync

logger = logging.getLogger(__name__)


class SparkBackend(PlateReaderBackend):
  """Backend for Tecan Spark plate reader."""

  def __init__(self, vid: int = 0x0C47) -> None:
    self.vid = vid
    self.reader = SparkReaderAsync(vid=self.vid)

    self.absorbance_processor = AbsorbanceProcessor()
    self.fluorescence_processor = FluorescenceProcessor()

    # Initialize controls
    self.config = ConfigControl(self.reader.send_command)
    self.plate = PlateControl(self.reader.send_command)
    self.measure = MeasurementControl(self.reader.send_command)
    self.optics = OpticsControl(self.reader.send_command)
    self.system = SystemControl(self.reader.send_command)
    self.sensors = SensorControl(self.reader.send_command)
    self.data = DataControl(self.reader.send_command)

  async def setup(self) -> None:
    """Set up the plate reader."""
    await self.reader.connect()
    await self.config.init_module()
    await self.data.turn_all_interval_messages_off()

  async def get_average_temperature(self) -> Optional[float]:
    """Calculate average chamber temperature from recorded messages (ID 100)."""
    temp_msgs = [m for m in self.reader.msgs if m.get("number") == 100]
    if not temp_msgs:
      return None

    total_temp = 0.0
    count = 0
    for msg in temp_msgs:
      try:
        total_temp += float(msg["args"][0])
        count += 1
      except (IndexError, ValueError, KeyError):
        continue

    if count == 0:
      return None

    return (total_temp / count) / 100.0

  async def stop(self) -> None:
    """Close connections."""
    await self.reader.close()

  async def open(self) -> None:
    """Move the plate carrier out."""
    await self.plate.move_to_position(PlatePosition.OUT_RIGHT)

  async def close(self, plate: Optional[Plate] = None) -> None:
    """Move the plate carrier in."""
    await self.plate.move_to_position(PlatePosition.PLATE_IN)

  async def scan_plate_range(self, plate: Plate, wells: Optional[List[Well]], z: float = 9150):
    """Scan the plate range."""
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
        await self.measure.measure_range_in_x_pointwise(start_x, end_x, y_pos, z, num_points_x)

  async def read_absorbance(
    self,
    plate: Plate,
    wells: Optional[List[Well]],
    wavelength: int,
    bandwidth: int = 200,
    num_reads: int = 10,
  ) -> List[Dict]:
    """Read absorbance."""

    # Initialize
    self.reader.clear_messages()
    await self.data.set_interval(InstrumentMessageType.TEMPERATURE, 200)
    # Setup Measurement
    await self.measure.set_measurement_mode(MeasurementMode.ABSORBANCE)
    await self.measure.start_measurement()
    await self.plate.set_motor_speed(MovementSpeed.NORMAL)
    await self.measure.set_scan_direction(ScanDirection.UP)
    await self.system.set_settle_time(50000)
    await self.measure.set_number_of_reads(num_reads, label=1)
    await self.optics.set_excitation_filter(
      FilterType.BANDPASS, wavelength=wavelength * 10, bandwidth=bandwidth, label=1
    )

    # Start Background Read
    bg_task, stop_event, results = await self.reader.start_background_read(
      SparkDevice.ABSORPTION, SparkEndpoint.BULK_IN
    )

    try:
      # Execute Measurement Sequence
      await self.measure.prepare_instrument(measure_reference=True)

      await self.scan_plate_range(plate, wells)
      measurement_time = time.time()

    finally:
      stop_event.set()
      await bg_task

      await self.measure.end_measurement()
      await self.data.turn_all_interval_messages_off()

    # Process results
    data_matrix = self.absorbance_processor.process(results)
    avg_temp = await self.get_average_temperature()

    # Construct the response
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
    focal_height: float = 20000,  # Unused
    bandwidth: int = 200,
    num_reads: int = 30,
    gain: int = 117,
  ) -> List[Dict]:
    """Read fluorescence."""

    ex_wavelength = excitation_wavelength * 10
    em_wavelength = emission_wavelength * 10

    # Initialize
    self.reader.clear_messages()
    await self.data.set_interval(InstrumentMessageType.TEMPERATURE, 200)

    # Setup Measurement
    await self.measure.set_measurement_mode(MeasurementMode.FLUORESCENCE_TOP)
    await self.measure.start_measurement()
    await self.plate.set_motor_speed(MovementSpeed.NORMAL)

    # System Settings
    await self.system.set_integration_time(40)
    await self.system.set_lag_time(0)
    await self.system.set_settle_time(0)

    # Optics Settings
    await self.optics.set_beam_diameter(5400)
    await self.optics.set_emission_filter(
      FilterType.BANDPASS,
      wavelength=em_wavelength,
      bandwidth=bandwidth,
      carrier=FluorescenceCarrier.MONOCHROMATOR_EMISSION,
    )
    await self.optics.set_excitation_filter(
      FilterType.BANDPASS,
      wavelength=ex_wavelength,
      bandwidth=bandwidth,
      carrier=FluorescenceCarrier.MONOCHROMATOR_EXCITATION,
    )

    await self.measure.set_scan_direction(ScanDirection.ALTERNATE_UP)
    await self.optics.set_mirror(mirror_type=MirrorType.AUTOMATIC)
    await self.optics.set_signal_gain(gain)
    await self.measure.set_number_of_reads(num_reads)

    # Start Background Read
    bg_task, stop_event, results = await self.reader.start_background_read(
      SparkDevice.FLUORESCENCE, SparkEndpoint.BULK_IN1
    )

    try:
      # Execute Measurement Sequence
      await self.measure.prepare_instrument(measure_reference=True)
      await self.scan_plate_range(plate, wells, focal_height)
      measurement_time = time.time()

    finally:
      stop_event.set()
      await bg_task

      await self.measure.end_measurement()
      await self.data.turn_all_interval_messages_off()

    # Process results
    data_matrix = self.fluorescence_processor.process(results)
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

  async def read_luminescence(
    self, plate: Plate, wells: List[Well], focal_height: float
  ) -> List[Dict]:
    raise NotImplementedError("Luminescence will be implemented in the future.")
