from typing import Dict, List, Literal, Optional, Tuple, Union

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well

from .backend import (
  Calibrate,
  CarriageSpeed,
  KineticSettings,
  MolecularDevicesBackend,
  MolecularDevicesSettings,
  PmtGain,
  ReadMode,
  ReadOrder,
  ReadType,
  ShakeSettings,
  SpectrumSettings,
)


class MolecularDevicesSpectraMaxGeminiEMBackend(MolecularDevicesBackend):
  """Backend for Molecular Devices SpectraMax Gemini EM plate readers."""

  WELLSCAN_OFFSET_MM = 1.133

  def __init__(self, port: str) -> None:
    super().__init__(port)

  def _assert_full_plate(self, plate: Plate, wells: Optional[List[Well]]) -> None:
    if wells is None:
      return
    if {id(well) for well in wells} != {id(well) for well in plate.get_all_items()}:
      raise NotImplementedError("Partial-plate reads are not supported by the Gemini EM backend.")

  def _get_well_region(
    self,
    plate: Plate,
    wells: Optional[List[Well]],
  ) -> Tuple[int, int, int, int]:
    if wells is None:
      return (0, plate.num_items_y, 0, plate.num_items_x)
    if {id(well) for well in wells} == {id(well) for well in plate.get_all_items()}:
      return (0, plate.num_items_y, 0, plate.num_items_x)

    indices = [plate.index_of_item(well) for well in wells]
    if any(index is None for index in indices):
      raise ValueError("All wells must belong to the plate.")

    rows = sorted({index % plate.num_items_y for index in indices if index is not None})
    cols = sorted({index // plate.num_items_y for index in indices if index is not None})
    row_range = list(range(rows[0], rows[-1] + 1))
    col_range = list(range(cols[0], cols[-1] + 1))
    expected = {col * plate.num_items_y + row for col in col_range for row in row_range}
    if set(indices) != expected:
      raise NotImplementedError("Only rectangular contiguous well regions are supported.")

    return (rows[0], len(row_range), cols[0], len(col_range))

  async def _set_plate_region(self, plate: Plate, wells: Optional[List[Well]]) -> None:
    start_row, row_count, start_col, col_count = self._get_well_region(plate, wells)
    top_left_well = plate.get_item(0)
    if top_left_well.location is None:
      raise ValueError("Top left well location is not set.")
    top_left_well_center = top_left_well.location + top_left_well.get_anchor(x="c", y="c")

    x_spacing = plate.item_dx
    y_spacing = plate.item_dy
    y_origin = plate.get_size_y() - top_left_well_center.y + start_row * y_spacing

    await self.send_command(
      f"!XPOS {top_left_well_center.x:.3f} {x_spacing:.3f} {plate.num_items_x}"
    )
    await self.send_command(f"!YPOS {y_origin:.3f} {y_spacing:.3f} {row_count}")
    await self.send_command(f"!STRIP {start_col + 1} {col_count}")

  async def _set_read_stage(self, settings: MolecularDevicesSettings) -> None:
    if settings.read_mode == ReadMode.LUM:
      if settings.read_from_bottom:
        raise NotImplementedError("Bottom luminescence reads have not been validated.")
      await self.send_command("!TOPREADCLEAR OFF")
      await self.send_command("!READSTAGE TOP", num_res_fields=1)
      return

    if settings.read_mode in (ReadMode.FLU, ReadMode.TIME):
      await self.send_command("!TOPREADCLEAR ON")
      stage = "BOT" if settings.read_from_bottom else "TOP"
      await self.send_command(f"!READSTAGE {stage}", num_res_fields=1)
      return

    return

  async def _set_nvram(self, settings: MolecularDevicesSettings) -> None:
    pass

  async def set_temperature(self, temperature: float) -> None:
    if not (0 <= temperature <= 45):
      raise ValueError("Temperature must be between 0 and 45 C.")
    await self.send_command(f"!TEMP {temperature}", num_res_fields=1)

  async def _set_wellscan_mode(self, enabled: bool) -> None:
    mode = "ON" if enabled else "OFF"
    await self.send_command(f"!WELLSCANMODE {mode}", num_res_fields=1)

  async def _set_time_resolved_readtype(self, delay_time: int, integration_time: int) -> None:
    await self.send_command(
      f"!READTYPE TIME {delay_time} {integration_time}",
      num_res_fields=1,
    )

  async def _set_gemini_plate_position(
    self,
    x_origin: float,
    y_origin: float,
    x_spacing: float,
    y_spacing: float,
    columns: int,
    rows: int,
  ) -> None:
    await self.send_command(f"!XPOS {x_origin:.3f} {x_spacing:g} {columns}")
    await self.send_command(f"!YPOS {y_origin:.3f} {y_spacing:g} {rows}")

  def _wellscan_positions(
    self,
    pattern: Literal["horizontal", "vertical", "cross", "fill"],
    center_x: float,
    center_y: float,
    spacing: float = WELLSCAN_OFFSET_MM,
  ) -> List[Tuple[float, float]]:
    left = round(center_x - spacing, 3)
    right = round(center_x + spacing, 3)
    top = round(center_y - spacing, 3)
    bottom = round(center_y + spacing, 3)

    if pattern == "horizontal":
      return [(left, center_y), (center_x, center_y), (right, center_y)]
    if pattern == "vertical":
      return [(center_x, top), (center_x, center_y), (center_x, bottom)]
    if pattern == "cross":
      return [
        (center_x, top),
        (left, center_y),
        (center_x, center_y),
        (right, center_y),
        (center_x, bottom),
      ]
    if pattern == "fill":
      return [(x, y) for y in (top, center_y, bottom) for x in (left, center_x, right)]
    raise ValueError(f"Unsupported wellscan pattern: {pattern}")

  async def experimental_read_fluorescence_wellscan(
    self,
    plate: Plate,
    wells: Optional[List[Well]] = None,
    excitation_wavelength: Optional[int] = None,
    emission_wavelength: Optional[int] = None,
    focal_height: Optional[float] = None,
    cutoff_filters: Optional[List[int]] = None,
    pattern: Literal["horizontal", "vertical", "cross", "fill"] = "fill",
    center_x: Optional[float] = None,
    center_y: Optional[float] = None,
    x_spacing: float = 9,
    y_spacing: float = 9,
    columns: int = 12,
    rows: int = 6,
    first_strip: int = 2,
    strip_count: int = 6,
    read_order: ReadOrder = ReadOrder.WAVELENGTH,
    calibrate: Calibrate = Calibrate.ON,
    shake_settings: Optional[ShakeSettings] = None,
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
    flashes_per_well: int = 6,
    timeout: int = 600,
  ) -> List[Dict]:
    """Run a Gemini EM fluorescence wellscan using the SoftMax Pro scan pattern model.

    The Gemini EM firmware exposes wellscan by enabling ``!WELLSCANMODE ON`` and performing
    separate reads at shifted plate origins. This Gemini-specific method preserves that model and
    annotates each parsed read with the scan point index and coordinates.
    """
    self._assert_full_plate(plate, wells)

    if focal_height is not None:
      raise NotImplementedError("focal_height is not used by the Gemini EM wellscan command path.")
    if excitation_wavelength is None:
      raise ValueError("excitation_wavelength is required.")
    if emission_wavelength is None:
      raise ValueError("emission_wavelength is required.")

    if cutoff_filters is None:
      cutoff_filters = [self._get_cutoff_filter_index_from_wavelength(emission_wavelength)]

    if center_x is None or center_y is None:
      top_left_well = plate.get_item(0)
      if top_left_well.location is None:
        raise ValueError("Top left well location is not set.")
      top_left_well_center = top_left_well.location + top_left_well.get_anchor(x="c", y="c")
      if center_x is None:
        center_x = top_left_well_center.x
      if center_y is None:
        center_y = plate.get_size_y() - top_left_well_center.y

    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode=ReadMode.FLU,
      read_type=ReadType.ENDPOINT,
      read_order=read_order,
      calibrate=calibrate,
      shake_settings=shake_settings,
      carriage_speed=carriage_speed,
      read_from_bottom=read_from_bottom,
      pmt_gain=pmt_gain,
      flashes_per_well=flashes_per_well,
      kinetic_settings=None,
      spectrum_settings=None,
      excitation_wavelengths=[excitation_wavelength],
      emission_wavelengths=[emission_wavelength],
      cutoff_filters=cutoff_filters,
      cuvette=False,
      speed_read=False,
      settling_time=0,
    )

    positions = self._wellscan_positions(pattern, center_x, center_y)

    await self._set_clear()
    await self._set_wellscan_mode(True)
    await self._set_gemini_plate_position(
      positions[0][0], positions[0][1], x_spacing, y_spacing, columns, rows
    )
    await self._set_shake(settings)
    await self.send_command(f"!STRIP {first_strip} {strip_count}")
    await self._set_carriage_speed(settings)
    await self._set_flashes_per_well(settings)
    await self._set_pmt(settings)
    await self._set_wavelengths(settings)
    await self._set_filter(settings)
    await self._set_read_stage(settings)
    await self._set_calibrate(settings)
    await self._set_mode(settings)
    await self._set_order(settings)
    await self._set_readtype(settings)

    all_reads = []
    for i, (x_origin, y_origin) in enumerate(positions):
      if i > 0:
        await self._set_gemini_plate_position(
          x_origin, y_origin, x_spacing, y_spacing, columns, rows
        )
        await self._set_shake(settings)
        await self.send_command("!PMTCAL OFF")
        await self.send_command(f"!STRIP {first_strip} {strip_count}")

      await self._read_now()
      await self._wait_for_idle(timeout=timeout)
      reads = await self._transfer_data(settings)
      for read in reads:
        read["wellscan_point"] = i
        read["wellscan_x"] = x_origin
        read["wellscan_y"] = y_origin
      all_reads.extend(reads)

    return all_reads

  async def read_fluorescence(  # type: ignore[override]
    self,
    plate: Plate,
    wells: Optional[List[Well]] = None,
    excitation_wavelength: Optional[int] = None,
    emission_wavelength: Optional[int] = None,
    focal_height: Optional[float] = None,
    cutoff_filters: Optional[List[int]] = None,
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
    timeout: int = 600,
  ) -> List[Dict]:
    if focal_height is not None:
      raise NotImplementedError("focal_height is not used by the Gemini EM fluorescence path.")
    if excitation_wavelength is None:
      raise ValueError("excitation_wavelength is required.")
    if emission_wavelength is None:
      raise ValueError("emission_wavelength is required.")

    if cutoff_filters is None:
      cutoff_filters = [self._get_cutoff_filter_index_from_wavelength(emission_wavelength)]

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
      excitation_wavelengths=[excitation_wavelength],
      emission_wavelengths=[emission_wavelength],
      cutoff_filters=cutoff_filters,
      cuvette=cuvette,
      settling_time=settling_time,
      speed_read=False,
    )

    await self._set_clear()
    if not cuvette:
      await self._set_plate_region(plate, wells)
      await self._set_carriage_speed(settings)

    await self._set_shake(settings)
    await self._set_flashes_per_well(settings)
    await self._set_pmt(settings)
    await self._set_wavelengths(settings)
    await self._set_filter(settings)
    await self._set_read_stage(settings)
    await self._set_calibrate(settings)
    await self._set_mode(settings)
    await self._set_order(settings)
    await self._set_tag(settings)
    await self._set_wellscan_mode(False)
    await self._set_nvram(settings)
    await self._set_readtype(settings)

    await self._read_now()
    await self._wait_for_idle(timeout=timeout)
    return await self._transfer_data(settings)

  async def experimental_read_fluorescence_emission_spectrum(
    self,
    plate: Plate,
    wells: Optional[List[Well]] = None,
    excitation_wavelength: int = 350,
    start_emission_wavelength: int = 400,
    step: int = 10,
    num_steps: int = 36,
    cutoff_filters: Optional[List[int]] = None,
    read_order: ReadOrder = ReadOrder.WAVELENGTH,
    calibrate: Calibrate = Calibrate.ON,
    shake_settings: Optional[ShakeSettings] = None,
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
    flashes_per_well: int = 7,
    timeout: int = 600,
  ) -> List[Dict]:
    if cutoff_filters is None:
      cutoff_filters = [1]

    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode=ReadMode.FLU,
      read_type=ReadType.SPECTRUM,
      read_order=read_order,
      calibrate=calibrate,
      shake_settings=shake_settings,
      carriage_speed=carriage_speed,
      read_from_bottom=read_from_bottom,
      pmt_gain=pmt_gain,
      flashes_per_well=flashes_per_well,
      kinetic_settings=None,
      spectrum_settings=SpectrumSettings(
        start_wavelength=start_emission_wavelength,
        step=step,
        num_steps=num_steps,
        excitation_emission_type="EMSPECTRUM",
      ),
      excitation_wavelengths=[excitation_wavelength],
      emission_wavelengths=[],
      cutoff_filters=cutoff_filters,
      cuvette=False,
      speed_read=False,
      settling_time=0,
    )

    await self._set_clear()
    await self._set_tag(settings)
    await self._set_wellscan_mode(False)
    await self._set_plate_region(plate, wells)
    await self._set_shake(settings)
    await self._set_readtype(settings)
    await self.send_command(f"!EXWAVELENGTH {excitation_wavelength}")
    await self._set_filter(settings)
    await self._set_flashes_per_well(settings)
    await self._set_read_stage(settings)
    await self._set_pmt(settings)
    await self._set_carriage_speed(settings)
    await self._set_calibrate(settings)
    await self._set_mode(settings)
    await self._set_order(settings)

    await self._read_now()
    await self._wait_for_idle(timeout=timeout)
    return await self._transfer_data(settings)

  async def experimental_read_fluorescence_excitation_spectrum(
    self,
    plate: Plate,
    wells: Optional[List[Well]] = None,
    emission_wavelength: int = 600,
    start_excitation_wavelength: int = 350,
    step: int = 20,
    num_steps: int = 4,
    cutoff_filters: Optional[List[int]] = None,
    read_order: ReadOrder = ReadOrder.WAVELENGTH,
    calibrate: Calibrate = Calibrate.ON,
    shake_settings: Optional[ShakeSettings] = None,
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
    flashes_per_well: int = 7,
    timeout: int = 600,
  ) -> List[Dict]:
    if cutoff_filters is None:
      cutoff_filters = [1]

    settings = MolecularDevicesSettings(
      plate=plate,
      read_mode=ReadMode.FLU,
      read_type=ReadType.SPECTRUM,
      read_order=read_order,
      calibrate=calibrate,
      shake_settings=shake_settings,
      carriage_speed=carriage_speed,
      read_from_bottom=read_from_bottom,
      pmt_gain=pmt_gain,
      flashes_per_well=flashes_per_well,
      kinetic_settings=None,
      spectrum_settings=SpectrumSettings(
        start_wavelength=start_excitation_wavelength,
        step=step,
        num_steps=num_steps,
        excitation_emission_type="EXSPECTRUM",
      ),
      excitation_wavelengths=[],
      emission_wavelengths=[emission_wavelength],
      cutoff_filters=cutoff_filters,
      cuvette=False,
      speed_read=False,
      settling_time=0,
    )

    await self._set_clear()
    await self._set_tag(settings)
    await self._set_wellscan_mode(False)
    await self._set_plate_region(plate, wells)
    await self._set_shake(settings)
    await self._set_readtype(settings)
    await self.send_command(f"!EMWAVELENGTH {emission_wavelength}")
    await self._set_filter(settings)
    await self.send_command("!AUTOFILTER EX OFF")
    await self._set_flashes_per_well(settings)
    await self._set_read_stage(settings)
    await self._set_pmt(settings)
    await self._set_carriage_speed(settings)
    await self._set_calibrate(settings)
    await self._set_mode(settings)
    await self._set_order(settings)

    await self._read_now()
    await self._wait_for_idle(timeout=timeout)
    return await self._transfer_data(settings)

  async def read_absorbance(  # type: ignore[override]
    self,
    plate: Plate,
    wells: List[Well],
    wavelength: int,
    **backend_kwargs,
  ) -> List[Dict]:
    raise NotImplementedError("Absorbance reading is not supported by the SpectraMax Gemini EM.")

  async def read_luminescence(  # type: ignore[override]
    self,
    plate: Plate,
    wells: Optional[List[Well]] = None,
    focal_height: Optional[float] = None,
    emission_wavelength: int = 0,
    read_type: ReadType = ReadType.ENDPOINT,
    read_order: ReadOrder = ReadOrder.COLUMN,
    calibrate: Calibrate = Calibrate.ON,
    shake_settings: Optional[ShakeSettings] = None,
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
    flashes_per_well: int = 6,
    timeout: int = 600,
  ) -> List[Dict]:
    if focal_height is not None:
      raise NotImplementedError("focal_height is not used by the Gemini EM luminescence path.")
    if read_type != ReadType.ENDPOINT:
      raise NotImplementedError("Only endpoint luminescence has been validated.")
    if read_from_bottom:
      raise NotImplementedError("Bottom luminescence reads have not been validated.")

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
      kinetic_settings=None,
      spectrum_settings=None,
      emission_wavelengths=[emission_wavelength],
      cuvette=False,
      speed_read=False,
      settling_time=0,
    )

    await self._set_clear()
    await self._set_tag(settings)
    await self._set_wellscan_mode(False)
    await self._set_plate_region(plate, wells)
    await self._set_shake(settings)
    await self._set_readtype(settings)
    await self._set_wavelengths(settings)
    await self._set_flashes_per_well(settings)
    await self._set_read_stage(settings)
    await self._set_pmt(settings)
    await self._set_carriage_speed(settings)
    await self._set_calibrate(settings)
    await self._set_mode(settings)
    await self._set_order(settings)

    await self._read_now()
    await self._wait_for_idle(timeout=timeout)
    return await self._transfer_data(settings)

  async def read_fluorescence_polarization(
    self,
    plate: Plate,
    excitation_wavelengths: List[int],
    emission_wavelengths: List[int],
    cutoff_filters: List[int],
    **backend_kwargs,
  ) -> List[Dict]:
    raise NotImplementedError(
      "Fluorescence polarization reading is not supported by the SpectraMax Gemini EM."
    )

  async def read_time_resolved_fluorescence(self, *args, **kwargs) -> List[Dict]:  # type: ignore[override]
    raise NotImplementedError(
      "Use experimental_read_time_resolved_fluorescence on the Gemini EM backend; "
      "the inherited MolecularDevicesBackend implementation has not been validated for this device."
    )

  async def experimental_read_time_resolved_fluorescence(
    self,
    plate: Plate,
    excitation_wavelengths: List[int],
    emission_wavelengths: List[int],
    cutoff_filters: List[int],
    delay_time: int,
    integration_time: int,
    wells: Optional[List[Well]] = None,
    read_type: ReadType = ReadType.ENDPOINT,
    read_order: ReadOrder = ReadOrder.COLUMN,
    calibrate: Calibrate = Calibrate.ON,
    shake_settings: Optional[ShakeSettings] = None,
    carriage_speed: CarriageSpeed = CarriageSpeed.NORMAL,
    read_from_bottom: bool = False,
    pmt_gain: Union[PmtGain, int] = PmtGain.AUTO,
    flashes_per_well: int = 6,
    kinetic_settings: Optional[KineticSettings] = None,
    timeout: int = 600,
  ) -> List[Dict]:
    if read_type not in (ReadType.ENDPOINT, ReadType.KINETIC):
      raise NotImplementedError(
        "Only endpoint and kinetic time-resolved fluorescence are supported."
      )
    if read_type == ReadType.KINETIC and kinetic_settings is None:
      raise ValueError("kinetic_settings is required for kinetic time-resolved fluorescence.")

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
      spectrum_settings=None,
      excitation_wavelengths=excitation_wavelengths,
      emission_wavelengths=emission_wavelengths,
      cutoff_filters=cutoff_filters,
      cuvette=False,
      speed_read=False,
      settling_time=0,
    )

    await self._set_clear()
    await self._set_tag(settings)
    await self._set_wellscan_mode(False)
    await self._set_plate_region(plate, wells)
    await self._set_shake(settings)
    await self._set_time_resolved_readtype(delay_time, integration_time)
    await self._set_wavelengths(settings)
    await self._set_filter(settings)
    await self._set_flashes_per_well(settings)
    await self._set_read_stage(settings)
    await self._set_pmt(settings)
    await self._set_carriage_speed(settings)
    await self._set_calibrate(settings)
    await self._set_mode(settings)
    await self._set_order(settings)

    await self._read_now()
    await self._wait_for_idle(timeout=timeout)
    return await self._transfer_data(settings)
