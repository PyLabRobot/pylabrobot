"""Top luminescence: endpoint reads.  Not tested on a reader with luminescence."""

import logging
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from pylabrobot.lib.luminescence.results import LuminescenceResult
from pylabrobot.resources import Plate, Well

from .measurement import (
  DEFAULT_APERTURE_DISTANCE,
  LUMINESCENCE_NO_FILTER,
  Measurement,
  MeasurementSettings,
  MonochromatorSettings,
  ShakeSettings,
  _get_identifier,
  _get_measured_wells,
  get_plate_geometry,
)

logger = logging.getLogger(__name__)

# Settling time when the measurement settings give none: Voyager's luminescence protocol.
_DEFAULT_SETTLING_TIME = 0.02


class Luminescence(Measurement):
  """Top luminescence measurement (RUN, MeasurementMode 1).  Not tested on a reader with
  luminescence.

  Reached as ``driver.luminescence``.
  """

  def _build_measure(
    self,
    integration_time: float,
    gain: Optional[int],
    aperture_distance: float,
    monochromator: MonochromatorSettings,
    plate: Optional[Plate],
    wells: Optional[Sequence[Well]],
    measurement_settings: Optional[MeasurementSettings],
  ) -> Tuple[Plate, MeasurementSettings, List[Tuple[int, int]], bytes]:
    """Check a read and build its RUN fields: the plate, the settings, the measured wells (row,
    column) and the fields.

    Raises:
      RuntimeError: if no plate is on the plate holder.
      ValueError: if an argument is out of range.
    """
    if not 0.01 <= integration_time <= 655.35:
      raise ValueError(f"integration_time must be 0.01-655.35 s, got {integration_time}")
    if measurement_settings is not None and (
      measurement_settings.flashes is not None or measurement_settings.well_scan != "point"
    ):
      raise ValueError("a luminescence read takes no flashes and no well scan")
    plate = self._get_plate(plate)
    settings = self._get_settings(
      measurement_settings, plate, flashes=1, settling_time=_DEFAULT_SETTLING_TIME
    )
    measured = _get_measured_wells(plate, wells)
    parameters = self._driver.build_luminescence_run(
      [monochromator],
      gains=None if gain is None else [gain],
      integration_time=integration_time,
      settling_time=_DEFAULT_SETTLING_TIME
      if settings.settling_time is None
      else settings.settling_time,
      aperture_distance=aperture_distance,
      geometry=get_plate_geometry(plate),
      well_mask=self._get_well_mask(plate, wells),
      corner=settings.corner,
      reading_direction=settings.reading_direction,
      bidirectional=settings.bidirectional,
    )
    return plate, settings, measured, parameters

  async def measure(
    self,
    *,
    integration_time: float = 0.8,
    gain: Optional[int] = None,
    aperture_distance: float = DEFAULT_APERTURE_DISTANCE,
    monochromator: MonochromatorSettings = LUMINESCENCE_NO_FILTER,
    plate: Optional[Plate] = None,
    wells: Optional[Sequence[Well]] = None,
    measurement_settings: Optional[MeasurementSettings] = None,
    shake_settings: Optional[ShakeSettings] = None,
    simulated_data: Optional[LuminescenceResult] = None,
    timeout: float = 600.0,
  ) -> LuminescenceResult:
    """Measure the luminescence of the plate from the top (endpoint).  NOT TESTED on a reader with
    luminescence; a reader without the luminescence module refuses it (``CommandRefusedError``, quit
    code 205).

    The defaults are those of Voyager's luminescence protocol: all the emitted light, automatic
    gain, 0.8 s per well, 0.5 mm aperture distance, 20 ms settling.  Neither the command nor the
    decoding of its data has run on a reader with luminescence.  Please report how it behaves.

    Args:
      integration_time: seconds of light collection per well (0.01-655.35, 10 ms steps).
      gain: the detector gain; None for the reader's automatic gain.
      aperture_distance: mm between the top optic's aperture and the plate (0.01 mm steps).
      monochromator: the emission settings; the default reads all the emitted light, as
        Voyager's luminescence protocol does.
      plate: the plate to measure; None uses the plate on the plate holder.
      wells: the wells to measure; None measures every well.
      measurement_settings: how to read; None reads with the defaults (20 ms settling).  Its
        ``flashes`` and ``well_scan`` do not apply: a luminescence read integrates at the centre.
      shake_settings: shake the plate before the read; None does not shake.
      simulated_data: ignored by a reader; a simulated one (``VANTAstar(simulation=True)``)
        reads its values from it.
      timeout: seconds the run may take.

    Returns:
      The counts of the measured wells.

    Raises:
      RuntimeError: if no plate is on the plate holder or the run ends without its data.
      ValueError: if an argument is out of range.
    """
    logger.warning("luminescence has not been tested on a VANTAstar; please report how it behaves")
    _, settings, measured, parameters = self._build_measure(
      integration_time, gain, aperture_distance, monochromator, plate, wells, measurement_settings
    )
    await self._prepare(settings, shake_settings)
    start = datetime.now()
    data = await self._driver.run_measurement(parameters, timeout=timeout)
    end = datetime.now()
    self._check_wells(data, measured)
    return LuminescenceResult(
      data={_get_identifier(r, c): float(v) for (r, c), v in zip(measured, data.group(0))},
      emission_wavelength=None,
      emission_bandwidth=None,
      temperature=data.temperature,
      start_timestamp=start,
      end_timestamp=end,
    )
