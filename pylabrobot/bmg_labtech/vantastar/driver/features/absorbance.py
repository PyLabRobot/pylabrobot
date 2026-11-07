"""Absorbance: endpoint and spectrum reads through the reader's spectrometer."""

from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from pylabrobot.lib.absorbance.results import AbsorbanceResult, AbsorbanceSpectrumResult
from pylabrobot.resources import Plate, Well

from ..responses import (
  MeasurementData,
  compute_optical_densities,
  compute_spectrum_optical_densities,
)
from .measurement import (
  Measurement,
  MeasurementSettings,
  ShakeSettings,
  _get_identifier,
  _get_measured_wells,
  get_plate_geometry,
)

# Absorbance wavelengths Voyager offers, in nm.
MINIMUM_ABSORBANCE_WAVELENGTH = 220
MAXIMUM_ABSORBANCE_WAVELENGTH = 1000
# Flashes and settling time when the measurement settings give none: Voyager's "precise" preset.
_DEFAULT_FLASHES = 22
_DEFAULT_SETTLING_TIME = 0.5


class Absorbance(Measurement):
  """Absorbance measurement (RUN, MeasurementMode 2).

  Reached as ``driver.absorbance``.
  """

  @staticmethod
  def _check_wavelengths(wavelengths: Sequence[int]) -> None:
    """Raise unless 1 to 8 wavelengths, each 220-1000 nm."""
    if not 1 <= len(wavelengths) <= 8:
      raise ValueError(f"1 to 8 wavelengths, got {len(wavelengths)}")
    for wavelength in wavelengths:
      if not MINIMUM_ABSORBANCE_WAVELENGTH <= wavelength <= MAXIMUM_ABSORBANCE_WAVELENGTH:
        raise ValueError(
          f"wavelength must be {MINIMUM_ABSORBANCE_WAVELENGTH}-{MAXIMUM_ABSORBANCE_WAVELENGTH} nm, "
          f"got {wavelength}"
        )

  def _get_absorbance_settings(
    self, measurement_settings: Optional[MeasurementSettings], plate: Plate
  ) -> MeasurementSettings:
    """``measurement_settings`` with absorbance's default flashes and settling time filled in."""
    return self._get_settings(
      measurement_settings, plate, flashes=_DEFAULT_FLASHES, settling_time=_DEFAULT_SETTLING_TIME
    )

  def _get_results(
    self,
    data: MeasurementData,
    measured: List[Tuple[int, int]],
    wavelengths: Sequence[int],
    start: datetime,
    end: datetime,
  ) -> List[AbsorbanceResult]:
    """One ``AbsorbanceResult`` per wavelength of an absorbance block."""
    self._check_wells(data, measured)
    identifiers = [_get_identifier(r, c) for r, c in measured]
    return [
      AbsorbanceResult(
        data=dict(zip(identifiers, compute_optical_densities(data, i))),
        wavelength=wavelength,
        temperature=data.temperature,
        start_timestamp=start,
        end_timestamp=end,
      )
      for i, wavelength in enumerate(wavelengths)
    ]

  def _build_measure(
    self,
    wavelengths: Sequence[int],
    plate: Optional[Plate],
    wells: Optional[Sequence[Well]],
    measurement_settings: Optional[MeasurementSettings],
  ) -> Tuple[Plate, MeasurementSettings, List[Tuple[int, int]], bytes]:
    """Check an endpoint read and build its RUN fields: the plate, the settings, the measured
    wells (row, column) and the fields.

    Raises:
      RuntimeError: if no plate is on the plate holder.
      ValueError: if an argument is out of range.
    """
    self._check_wavelengths(wavelengths)
    plate = self._get_plate(plate)
    settings = self._get_absorbance_settings(measurement_settings, plate)
    measured = _get_measured_wells(plate, wells)
    parameters = self._driver.build_absorbance_run(
      wavelengths,
      geometry=get_plate_geometry(plate),
      well_mask=self._get_well_mask(plate, wells),
      **self._get_run_fields(settings),
    )
    return plate, settings, measured, parameters

  async def measure(
    self,
    wavelengths: Sequence[int],
    *,
    plate: Optional[Plate] = None,
    wells: Optional[Sequence[Well]] = None,
    measurement_settings: Optional[MeasurementSettings] = None,
    shake_settings: Optional[ShakeSettings] = None,
    simulated_data: Optional[List[AbsorbanceResult]] = None,
    timeout: float = 300.0,
  ) -> List[AbsorbanceResult]:
    """Measure the absorbance of the plate at each wavelength (endpoint).

    The default flashes and settling time (22, 0.5 s) are Voyager's "precise" preset.  The optical
    density is computed as Voyager computes it, against the reader's 100% and reference values; it
    is not pathlength-corrected.  Voyager reports optical densities above 3.5 as 3.5; these are the
    computed values, ``inf`` for a well that lets no measurable light through.  Averaging over an
    orbit or spiral reads higher optical densities than the centre, near the well's edge (Voyager's
    too).

    Args:
      wavelengths: 1 to 8 wavelengths in nm (220-1000).
      plate: the plate to measure; None uses the plate on the plate holder.
      wells: the wells to measure; None measures every well.
      measurement_settings: how to read; None reads with the defaults.
      shake_settings: shake the plate before the read; None does not shake.
      simulated_data: ignored by a reader; a simulated one (``VANTAstar(simulation=True)``)
        reads its values from it.
      timeout: seconds the run may take.

    Returns:
      One ``AbsorbanceResult`` per wavelength, in the order given.

    Raises:
      RuntimeError: if no plate is on the plate holder or the run ends without its data.
      ValueError: if an argument is out of range.
    """
    _, settings, measured, parameters = self._build_measure(
      wavelengths, plate, wells, measurement_settings
    )
    await self._prepare(settings, shake_settings)
    start = datetime.now()
    data = await self._driver.run_measurement(parameters, timeout=timeout)
    end = datetime.now()
    return self._get_results(data, measured, wavelengths, start, end)

  def _build_spectrum(
    self,
    start_wavelength: int,
    end_wavelength: int,
    step_size: int,
    plate: Optional[Plate],
    wells: Optional[Sequence[Well]],
    measurement_settings: Optional[MeasurementSettings],
  ) -> Tuple[Plate, MeasurementSettings, List[Tuple[int, int]], List[int], bytes]:
    """Check a spectrum and build its RUN fields: the plate, the settings, the measured wells
    (row, column), the wavelengths and the fields.

    Raises:
      RuntimeError: if no plate is on the plate holder.
      ValueError: if an argument is out of range.
    """
    self._check_wavelengths([start_wavelength, end_wavelength])
    scanned = self._get_scan_wavelengths(start_wavelength, end_wavelength, step_size)
    plate = self._get_plate(plate)
    settings = self._get_absorbance_settings(measurement_settings, plate)
    measured = _get_measured_wells(plate, wells)
    parameters = self._driver.build_absorbance_spectrum_run(
      start_wavelength,
      end_wavelength,
      step_size,
      geometry=get_plate_geometry(plate),
      well_mask=self._get_well_mask(plate, wells),
      **self._get_run_fields(settings),
    )
    return plate, settings, measured, scanned, parameters

  async def measure_spectrum(
    self,
    start_wavelength: int,
    end_wavelength: int,
    step_size: int = 1,
    *,
    plate: Optional[Plate] = None,
    wells: Optional[Sequence[Well]] = None,
    measurement_settings: Optional[MeasurementSettings] = None,
    shake_settings: Optional[ShakeSettings] = None,
    simulated_data: Optional[AbsorbanceSpectrumResult] = None,
    timeout: float = 600.0,
  ) -> AbsorbanceSpectrumResult:
    """Measure the absorbance spectrum of the plate: every wavelength at once, well by well (the
    reader's spectrometer).  The optical densities are computed as in ``measure``.

    Args:
      start_wavelength: the first wavelength in nm (220-1000).
      end_wavelength: the last wavelength in nm, above ``start_wavelength`` (220-1000).
      step_size: nm between wavelengths, a whole nm dividing the range.
      plate: the plate to measure; None uses the plate on the plate holder.
      wells: the wells to measure; None measures every well.
      measurement_settings: how to read; None reads with the defaults.
      shake_settings: shake the plate before the read; None does not shake.
      simulated_data: ignored by a reader; a simulated one (``VANTAstar(simulation=True)``)
        reads its values from it.
      timeout: seconds the run may take.

    Returns:
      Each measured well's spectrum, keyed by well identifier.

    Raises:
      RuntimeError: if no plate is on the plate holder or the run ends without its data.
      ValueError: if an argument is out of range.
    """
    _, settings, measured, scanned, parameters = self._build_spectrum(
      start_wavelength, end_wavelength, step_size, plate, wells, measurement_settings
    )
    await self._prepare(settings, shake_settings)
    start = datetime.now()
    blocks = await self._driver.run_absorbance_spectrum(parameters, timeout=timeout)
    end = datetime.now()
    positions: List[Tuple[int, int]] = []
    for block in blocks:
      if block.well is None:
        raise RuntimeError("an absorbance spectrum block holds no single well")
      positions.append((block.well[1] - 1, block.well[0] - 1))
    if sorted(positions) != measured:
      raise RuntimeError(f"the reader sent wells {positions}, {measured} were asked for")
    spectra = compute_spectrum_optical_densities(blocks, len(scanned))
    data = {
      _get_identifier(*position): dict(zip(scanned, spectrum))
      for position, spectrum in sorted(zip(positions, spectra))
    }
    return AbsorbanceSpectrumResult(
      data=data,
      temperature=blocks[0].temperature,
      start_timestamp=start,
      end_timestamp=end,
    )
