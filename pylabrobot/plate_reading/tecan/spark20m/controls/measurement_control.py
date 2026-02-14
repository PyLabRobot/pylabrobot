import logging
from typing import List, Optional, Union

from .base_control import BaseControl
from .spark_enums import MeasurementMode, ModuleType, ScanDarkState, ScanDirection


class MeasurementControl(BaseControl):
  """
  This class provides methods for controlling measurement operations on the device.
  It includes functionalities to start/end measurements, set/get measurement modes,
  prepare the instrument, and perform various types of scans.
  """

  async def start_measurement(self) -> Optional[str]:
    """Starts the measurement process."""
    return await self.send_command("MEASUREMENT START")

  async def end_measurement(self) -> Optional[str]:
    """Ends the measurement process."""
    return await self.send_command("MEASUREMENT END")

  async def set_measurement_mode(self, mode: MeasurementMode) -> Optional[str]:
    """Sets the measurement mode (e.g., ABS, LUM, FLUOR)."""
    return await self.send_command(f"MODE MEASUREMENT={mode.value}")

  async def get_available_measurement_modes(self) -> Optional[str]:
    """Gets the available measurement modes."""
    response = await self.send_command("#MODE MEASUREMENT")
    return response

  async def get_current_measurement_mode(self) -> Optional[str]:
    """Gets the current measurement mode."""
    return await self.send_command("?MODE MEASUREMENT")

  async def get_label_range(self) -> Optional[str]:
    """Gets the range of available labels for scans."""
    return await self.send_command("#SCAN LABEL")

  async def prepare_instrument(
    self,
    measure_reference: bool = True,
    mode: Optional[MeasurementMode] = None,
    labels: Optional[List[int]] = None,
  ) -> Optional[str]:
    """Prepares the instrument for measurement."""
    command = f"PREPARE REFERENCE={'YES' if measure_reference else 'NO'}"
    if mode and labels:
      command += f" MODE={mode.value} LABEL={'|'.join(map(str, labels))}"
    return await self.send_command(command)

  async def set_scan_direction(self, direction: ScanDirection) -> Optional[str]:
    """Sets the scan direction."""
    return await self.send_command(f"SCAN DIRECTION={direction.value}")

  async def get_available_scan_directions(self) -> Optional[str]:
    """Gets the available scan directions."""
    response = await self.send_command("#SCAN DIRECTION")
    return response

  async def get_current_scan_direction(self) -> Optional[str]:
    """Gets the current scan direction."""
    return await self.send_command("?SCAN DIRECTION")

  @staticmethod
  def _format_scan_range(
    coordinate: str,
    from_val: Optional[int],
    to_val: Optional[int],
    step_type_is_delta: Optional[bool],
    steps: Optional[int],
  ) -> str:
    if all(v is not None for v in [coordinate, from_val, to_val, step_type_is_delta, steps]):
      step_char = ":" if step_type_is_delta else "%"
      return f" {coordinate}={from_val}~{to_val}{step_char}{steps}"
    return ""

  async def _measure_in(
    self,
    scale: Optional[str],
    from_val: Optional[int] = None,
    to_val: Optional[int] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
    z: Optional[Union[int, float]] = None,
    steps: Optional[int] = None,
    step_type_is_delta: Optional[bool] = None,
    mode: Optional[MeasurementMode] = None,
    labels: Optional[List[int]] = None,
  ) -> Optional[str]:
    command = "SCAN"
    if scale == "X":
      command += self._format_scan_range("X", from_val, to_val, step_type_is_delta, steps)
      if y is not None:
        command += f" Y={y}"
      if z is not None:
        command += f" Z={z}"
    elif scale == "Y":
      if x is not None:
        command += f" X={x}"
      command += self._format_scan_range("Y", from_val, to_val, step_type_is_delta, steps)
      if z is not None:
        command += f" Z={z}"
    elif scale == "Z":
      if x is not None:
        command += f" X={x}"
      if y is not None:
        command += f" Y={y}"
      command += self._format_scan_range("Z", from_val, to_val, step_type_is_delta, steps)
    elif scale == "T":
      if x is not None:
        command += f" X={x}"
      if y is not None:
        command += f" Y={y}"
      if z is not None:
        command += f" Z={z}"
      command += self._format_scan_range("T", from_val, to_val, step_type_is_delta, steps)
    else:  # None
      if x is not None:
        command += f" X={x}"
      if y is not None:
        command += f" Y={y}"
      if z is not None:
        command += f" Z={z}"

    if mode and labels:
      command += f" MODE={mode.value} LABEL={'|'.join(map(str, labels))}"

    return await self.send_command(command)

  async def measure_range_in_x_pointwise(
    self,
    from_x: int,
    to_x: int,
    position_y: int,
    position_z: Union[int, float],
    num_points: int,
    mode: Optional[MeasurementMode] = None,
    labels: Optional[List[int]] = None,
  ) -> Optional[str]:
    return await self._measure_in(
      "X",
      from_x,
      to_x,
      y=position_y,
      z=position_z,
      steps=num_points,
      step_type_is_delta=False,
      mode=mode,
      labels=labels,
    )

  async def measure_range_in_x_by_delta(
    self,
    from_x: int,
    to_x: int,
    position_y: int,
    position_z: Union[int, float],
    delta: int,
    mode: Optional[MeasurementMode] = None,
    labels: Optional[List[int]] = None,
  ) -> Optional[str]:
    return await self._measure_in(
      "X",
      from_x,
      to_x,
      y=position_y,
      z=position_z,
      steps=delta,
      step_type_is_delta=True,
      mode=mode,
      labels=labels,
    )

  async def measure_range_in_y_pointwise(
    self,
    position_x: int,
    from_y: int,
    to_y: int,
    position_z: Union[int, float],
    num_points: int,
    mode: Optional[MeasurementMode] = None,
    labels: Optional[List[int]] = None,
  ) -> Optional[str]:
    return await self._measure_in(
      "Y",
      from_y,
      to_y,
      x=position_x,
      z=position_z,
      steps=num_points,
      step_type_is_delta=False,
      mode=mode,
      labels=labels,
    )

  async def measure_range_in_y_by_delta(
    self,
    position_x: int,
    from_y: int,
    to_y: int,
    position_z: Union[int, float],
    delta: int,
    mode: Optional[MeasurementMode] = None,
    labels: Optional[List[int]] = None,
  ) -> Optional[str]:
    return await self._measure_in(
      "Y",
      from_y,
      to_y,
      x=position_x,
      z=position_z,
      steps=delta,
      step_type_is_delta=True,
      mode=mode,
      labels=labels,
    )

  async def measure_range_in_z_pointwise(
    self,
    position_x: int,
    position_y: int,
    from_z: int,
    to_z: int,
    num_points: int,
    mode: Optional[MeasurementMode] = None,
    labels: Optional[List[int]] = None,
  ) -> Optional[str]:
    return await self._measure_in(
      "Z",
      from_z,
      to_z,
      x=position_x,
      y=position_y,
      steps=num_points,
      step_type_is_delta=False,
      mode=mode,
      labels=labels,
    )

  async def measure_range_in_z_by_delta(
    self,
    position_x: int,
    position_y: int,
    from_z: int,
    to_z: int,
    delta: int,
    mode: Optional[MeasurementMode] = None,
    labels: Optional[List[int]] = None,
  ) -> Optional[str]:
    return await self._measure_in(
      "Z",
      from_z,
      to_z,
      x=position_x,
      y=position_y,
      steps=delta,
      step_type_is_delta=True,
      mode=mode,
      labels=labels,
    )

  async def measure_range_in_t_pointwise(
    self,
    position_x: int,
    position_y: int,
    position_z: Union[int, float],
    from_t: int,
    to_t: int,
    num_points: int,
    mode: Optional[MeasurementMode] = None,
    labels: Optional[List[int]] = None,
  ) -> Optional[str]:
    return await self._measure_in(
      "T",
      from_t,
      to_t,
      x=position_x,
      y=position_y,
      z=position_z,
      steps=num_points,
      step_type_is_delta=False,
      mode=mode,
      labels=labels,
    )

  async def measure_range_in_t_by_delta(
    self,
    position_x: int,
    position_y: int,
    position_z: Union[int, float],
    from_t: int,
    to_t: int,
    delta: int,
    mode: Optional[MeasurementMode] = None,
    labels: Optional[List[int]] = None,
  ) -> Optional[str]:
    return await self._measure_in(
      "T",
      from_t,
      to_t,
      x=position_x,
      y=position_y,
      z=position_z,
      steps=delta,
      step_type_is_delta=True,
      mode=mode,
      labels=labels,
    )

  async def measure_position(
    self,
    x: Optional[int] = None,
    y: Optional[int] = None,
    z: Optional[Union[int, float]] = None,
    mode: Optional[MeasurementMode] = None,
    labels: Optional[List[int]] = None,
  ) -> Optional[str]:
    return await self._measure_in(None, x=x, y=y, z=z, mode=mode, labels=labels)

  async def measure_current_position(
    self, mode: Optional[MeasurementMode] = None, labels: Optional[List[int]] = None
  ) -> Optional[str]:
    return await self._measure_in(None, mode=mode, labels=labels)

  async def ensure_measurement_mode(self, mode: MeasurementMode) -> Optional[str]:
    """Ensures the measurement mode is set, only if different from current."""
    current_mode = await self.get_current_measurement_mode()
    if current_mode != f"MEASUREMENT={mode.value.upper()}":
      return await self.set_measurement_mode(mode)
    logging.info(f"Measurement Mode already set to: {mode.value}")
    return "OK"

  async def set_scan_dark(
    self, state: ScanDarkState, module: ModuleType = ModuleType.FLUORESCENCE
  ) -> Optional[str]:
    """Sets the scan dark state for a module."""
    return await self.send_command(f"SCAN DARK={state.value} MODULE={module.value}")

  async def set_parallel_excitation_polarisation(self) -> Optional[str]:
    """Sets the excitation polarisation to parallel."""
    return await self.send_command("POLARISATION EXCITATION=PARALLEL")

  async def set_perpendicular_excitation_polarisation(self) -> Optional[str]:
    """Sets the excitation polarisation to perpendicular."""
    return await self.send_command("POLARISATION EXCITATION=PERPENDICULAR")

  async def set_parallel_emission_polarisation(self) -> Optional[str]:
    """Sets the emission polarisation to parallel."""
    return await self.send_command("POLARISATION EMISSION=PARALLEL")

  async def set_perpendicular_emission_polarisation(self) -> Optional[str]:
    """Sets the emission polarisation to perpendicular."""
    return await self.send_command("POLARISATION EMISSION=PERPENDICULAR")

  async def set_polarisation_emission_mode(self, mode: str) -> Optional[str]:
    """Sets the polarisation emission mode."""
    return await self.send_command(f"POLARISATION MODE_EMISSION={mode.upper()}")

  async def get_possible_number_of_reads_range(self) -> Optional[str]:
    """Gets the possible range for the number of reads."""
    return await self.send_command("#READ COUNT")

  async def set_number_of_reads(self, count: int, label: Optional[int] = None) -> Optional[str]:
    """Sets the number of reads."""
    command = "READ"
    if label is not None:
      command += f" LABEL={label}"
    command += f" COUNT={count}"
    return await self.send_command(command)

  async def get_current_number_of_reads(self, label: Optional[int] = None) -> Optional[str]:
    """Gets the current number of reads."""
    command = "?READ"
    if label is not None:
      command += f" LABEL={label}"
    command += " COUNT"
    return await self.send_command(command)

  async def get_allowed_read_speed_range(self) -> Optional[str]:
    """Gets the allowed range for read speed."""
    return await self.send_command("#READ SPEED")

  async def set_read_speed(self, speed: int) -> Optional[str]:
    """Sets the read speed."""
    return await self.send_command(f"READ SPEED={speed}")

  async def get_current_read_speed(self) -> Optional[str]:
    """Gets the current read speed."""
    return await self.send_command("?READ SPEED")
