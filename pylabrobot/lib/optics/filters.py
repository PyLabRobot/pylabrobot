"""Optical filters a reader or imager holds in numbered positions: bandpass filters for excitation
and emission, and dichroic mirrors that split one from the other, each described by its bands."""

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

Band = Tuple[float, Optional[float]]
"""A band's lower and upper edge in nm, as its source states them (a reader's -3 dB points, a
data sheet's guaranteed range); an upper edge of None is open ("above 498 nm")."""


def _get_band_errors(name: str, bands: Sequence[Band]) -> list:
  """The violations of `bands`: an edge below 0, a lower edge not below its upper edge, an open
  band that is not the highest, bands that overlap."""
  errors = []
  for lower, upper in bands:
    if lower < 0 or (upper is not None and not lower < upper):
      errors.append(f"{name}: a band's lower edge must be at least 0 and below its upper edge")
  ordered = sorted(bands, key=lambda band: band[0])
  for (_, upper), (next_lower, _) in zip(ordered, ordered[1:]):
    if upper is None or upper > next_lower:
      errors.append(f"{name}: bands must not overlap")
      break
  return errors


@dataclass(frozen=True)
class _FilterBase:
  """A filter by the position its device holds it in.

  Attributes:
    slot: the position, numbered from 1 as the device numbers them; all a device is sent.
    name: what the filter is called, for the user ("615-18", "LP TR").
  """

  slot: int
  name: str = ""


@dataclass(frozen=True)
class OpticalFilter(_FilterBase):
  """A bandpass filter, single- or multi-band, for excitation or emission.

  Attributes:
    passbands: the bands it passes; empty when not known. A CLARIOstar reports one, as -3 dB
      edges (615-18: 604.9 to 623.2 nm); a quad-band filter has four.

  Raises:
    ValueError: if a band is not a band (`Band`) or two bands overlap.
  """

  passbands: Tuple[Band, ...] = ()

  def __post_init__(self) -> None:
    errors = _get_band_errors("passbands", self.passbands)
    if errors:
      raise ValueError("; ".join(errors))

  def _get_single_band(self) -> Optional[Tuple[float, float]]:
    """The passband, if there is exactly one and it is closed."""
    if len(self.passbands) != 1:
      return None
    lower, upper = self.passbands[0]
    return None if upper is None else (lower, upper)

  @property
  def center_wavelength(self) -> Optional[float]:
    """The centre of a single closed passband, in nm; None otherwise."""
    band = self._get_single_band()
    return None if band is None else (band[0] + band[1]) / 2

  @property
  def bandwidth(self) -> Optional[float]:
    """The width of a single closed passband, in nm; None otherwise."""
    band = self._get_single_band()
    return None if band is None else band[1] - band[0]


@dataclass(frozen=True)
class DichroicFilter(_FilterBase):
  """A dichroic or polychroic mirror: reflects excitation, transmits emission.

  The two sets are stated separately: between and outside them the mirror is not specified, not
  the other set (a quad-band dichroic's gaps; its coating's range).

  Attributes:
    reflection_bands: the bands it reflects; empty when not known.
    transmission_bands: the bands it transmits; empty when not known. A CLARIOstar reports a
      cut-on only (LP TR: one band from 421.8 nm, open above).

  Raises:
    ValueError: if a band is not a band (`Band`), or bands overlap within or across the sets.
  """

  reflection_bands: Tuple[Band, ...] = ()
  transmission_bands: Tuple[Band, ...] = ()

  def __post_init__(self) -> None:
    errors = _get_band_errors("reflection_bands", self.reflection_bands)
    errors += _get_band_errors("transmission_bands", self.transmission_bands)
    if not errors and _get_band_errors("", self.reflection_bands + self.transmission_bands):
      errors.append("reflection_bands and transmission_bands must not overlap")
    if errors:
      raise ValueError("; ".join(errors))
