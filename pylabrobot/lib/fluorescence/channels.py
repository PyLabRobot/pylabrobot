"""A fluorescence channel: the optics of one fluorescence read, for any fluorescence device.

What excites the sample, splits the light and passes the emission, on a plate reader, a microscope
or a cytometer.
"""

from dataclasses import dataclass
from typing import List, Optional, Union

from pylabrobot.lib.optics.filters import DichroicFilter, OpticalFilter


def _check_excitation_or_emission(
  excitation_or_emission: str,
  wavelength: Optional[float],
  bandwidth: Optional[float],
  optical_filter: Optional[Union[OpticalFilter, int, str]],
) -> List[str]:
  """The violations of excitation or emission.

  A filter with a wavelength or a bandwidth, a wavelength or a bandwidth not positive.
  """
  errors = []
  for field, value in (("wavelength", wavelength), ("bandwidth", bandwidth)):
    if value is None:
      continue
    if optical_filter is not None:
      errors.append(
        f"{excitation_or_emission}_filter replaces {excitation_or_emission}_{field}, got {value!r}"
      )
    if not value > 0:
      errors.append(f"{excitation_or_emission}_{field} must be positive, got {value!r}")
  return errors


def _check_dichroic(
  dichroic_filter: Optional[Union[DichroicFilter, int, str]], split_wavelength: Optional[float]
) -> List[str]:
  """The violations of the dichroic: a fixed dichroic with a tunable split, a split not positive."""
  errors = []
  if dichroic_filter is not None and split_wavelength is not None:
    errors.append(
      "dichroic_filter (a fixed dichroic) replaces dichroic_split_wavelength (a tunable split); "
      f"give one, got dichroic_split_wavelength {split_wavelength!r} with dichroic_filter "
      f"{dichroic_filter!r}"
    )
  if split_wavelength is not None and not split_wavelength > 0:
    errors.append(f"dichroic_split_wavelength must be positive, got {split_wavelength!r}")
  return errors


@dataclass(frozen=True, kw_only=True)
class FluorescenceChannel:
  """One fluorescence channel of a read: excitation, dichroic and emission, as the light passes.

  Excitation and emission each select a wavelength through a monochromator (wavelength and
  bandwidth) or a filter; an epi-fluorescence device's dichroic separates them. The detector
  gain is a setting of each read.

  Attributes:
    excitation_wavelength: nm through a monochromator or a tunable source; None with a filter.
    excitation_bandwidth: nm about `excitation_wavelength`; None with a filter or the default.
    excitation_filter: the excitation filter instead of a wavelength: a filter, or its slot or name
      on the device's slides.
    dichroic_split_wavelength: nm where a tunable dichroic splits excitation from emission; None
      otherwise.
    dichroic_filter: a fixed dichroic instead of a split: a filter, or its slot or name on the
      device's slides.
    emission_wavelength: nm through a monochromator; None with a filter.
    emission_bandwidth: nm about `emission_wavelength`; None with a filter or the default.
    emission_filter: the emission filter instead of a wavelength: a filter, or its slot or name on
      the device's slides.
  """

  excitation_wavelength: Optional[float] = None
  excitation_bandwidth: Optional[float] = None
  excitation_filter: Optional[Union[OpticalFilter, int, str]] = None
  dichroic_split_wavelength: Optional[float] = None
  dichroic_filter: Optional[Union[DichroicFilter, int, str]] = None
  emission_wavelength: Optional[float] = None
  emission_bandwidth: Optional[float] = None
  emission_filter: Optional[Union[OpticalFilter, int, str]] = None

  def __post_init__(self) -> None:
    """Raise listing every component of the wrong combination or sign.

    Excitation and emission (`_check_excitation_or_emission`), the dichroic (`_check_dichroic`).
    """
    errors = (
      _check_excitation_or_emission(
        "excitation", self.excitation_wavelength, self.excitation_bandwidth, self.excitation_filter
      )
      + _check_excitation_or_emission(
        "emission", self.emission_wavelength, self.emission_bandwidth, self.emission_filter
      )
      + _check_dichroic(self.dichroic_filter, self.dichroic_split_wavelength)
    )
    if errors:
      raise ValueError("; ".join(errors))
