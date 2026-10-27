"""Parsers for VANTAstar replies.

A reply is ``[response id] + status header + payload``.  The header's first byte packs its length N
in the low nibble (N counts that byte, so N - 1 status flag bytes follow) with the refused, busy,
error and warning bits (bits 4-7).  The payload starts after the status flag bytes.  Only the
ConfirmationResponse (id 0x01) opens its payload with a device-info section: a count byte, then the
five codes (quit codes of a refused command, warning or error codes, or the busy codes, which are the
id of the command running).  Data replies carry their fields from the first payload byte.

The layouts below hold for firmware 1.11 and 1.12.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

CONFIRMATION_ID = 0x01
MEASUREMENT_DATA_ID = 0x02
GAIN_DATA_ID = 0x05

_HEADER_LENGTH_MASK = 0x0F

# MeasurementDataResponse payload: what the first byte says the block holds.
_MEASUREMENT_BLOCK = 0x01
# Fixed fields before the per-channel chromatic counts.
_MD_TOTAL_VALUES = 2  # u32
_MD_VALID_VALUES = 6  # u32
_MD_OVERFLOW = 10  # u32, the count a saturated reading shows
_MD_CHANNELS = 15  # u8
_MD_CHROMATICS_1 = 17  # u8; with two channels the second channel's count follows as u16

# GainDataResponse payload.
_GD_GAIN = 1  # u16
_GD_FOCAL_HEIGHT = 13  # u16, 0.01 mm


def split_reply(reply: bytes) -> Tuple[int, bytes, bytes]:
  """Split a reply into its header's first byte, its status flag bytes and its payload.

  Raises:
    ValueError: if the reply is shorter than its header.
  """
  if len(reply) < 2:
    raise ValueError(f"reply too short for a status header: {reply.hex()}")
  first = reply[1]
  length = first & _HEADER_LENGTH_MASK
  if length < 1 or len(reply) < 1 + length:
    raise ValueError(f"reply shorter than its {length}-byte status header: {reply.hex()}")
  return first, reply[2 : 1 + length], reply[1 + length :]


@dataclass(frozen=True)
class Confirmation:
  """The device-info part of a ConfirmationResponse.

  Attributes:
    codes: the five codes; their meaning follows from the header bits (see ``errors.py``).
    temperatures: the three incubator sensor readings in degrees C, None when the reply carries
      none or the reader reports 0 (as it does for a moment after a temperature command).  The
      first is the one Voyager displays.
  """

  codes: Tuple[int, int, int, int, int]
  temperatures: Optional[Tuple[float, float, float]] = None


def parse_confirmation(reply: bytes) -> Confirmation:
  """Parse a ConfirmationResponse's codes and incubator temperatures.

  After the codes come a byte (observed 0) and the incubator block: its length (9), three u16
  temperatures in 0.1 degrees C, then three bytes not decoded here.

  Raises:
    ValueError: if ``reply`` is not a ConfirmationResponse or is shorter than its header.
  """
  if not reply or reply[0] != CONFIRMATION_ID:
    raise ValueError(f"not a ConfirmationResponse: {reply.hex()}")
  _, _, payload = split_reply(reply)
  count = payload[0] if payload else 0
  info = payload[1 : 1 + count]
  codes = tuple(info[i] if i < len(info) else 0 for i in range(5))
  temperatures: Optional[Tuple[float, float, float]] = None
  incubator = payload[1 + count + 1 :]
  if len(incubator) >= 7 and incubator[0] >= 6:
    raw = [int.from_bytes(incubator[1 + 2 * i : 3 + 2 * i], "big") for i in range(3)]
    if any(raw):
      temperatures = (raw[0] / 10, raw[1] / 10, raw[2] / 10)
  return Confirmation(codes=codes, temperatures=temperatures)  # type: ignore[arg-type]


@dataclass
class MeasurementData:
  """A MeasurementDataResponse page: the reader's current data block of a read.

  Each page carries the whole block; ``valid_values`` counts how much of it is measured so far.
  The values are grouped by channel and chromatic, each group one value per measured well in row
  order (A1, A2, ..., B1, ...), then any calibration values (absorbance: per chromatic of channel 1
  and for channel 2, a 100% and a dark value).

  Attributes:
    total_values: values in the finished block.
    valid_values: values measured so far.
    overflow: the count a saturated reading shows.
    chromatics: chromatics per channel: absorbance reads every wavelength plus two reference
      wavelengths on channel 1 and one value on the reference channel 2; fluorescence one per
      chromatic.
    wells: measured wells.
    temperature: degrees C, None when the reader did not monitor it.
    values: the block's values.
  """

  total_values: int
  valid_values: int
  overflow: int
  chromatics: Tuple[int, ...]
  wells: int
  temperature: Optional[float]
  values: List[int] = field(default_factory=list)

  @property
  def complete(self) -> bool:
    """Whether every value of the block is measured."""
    return self.valid_values >= self.total_values

  def group(self, index: int) -> List[int]:
    """The ``index``-th group of one value per well (channel 1's chromatics first)."""
    return self.values[index * self.wells : (index + 1) * self.wells]

  @property
  def calibration(self) -> List[int]:
    """The values after the per-well groups."""
    return self.values[sum(self.chromatics) * self.wells :]


def is_measurement_page(reply: bytes) -> bool:
  """Whether ``reply`` is a MeasurementDataResponse page of a read (not of a focus scan)."""
  if not reply or reply[0] != MEASUREMENT_DATA_ID:
    return False
  try:
    _, _, payload = split_reply(reply)
  except ValueError:
    return False
  return bool(payload) and payload[0] == _MEASUREMENT_BLOCK


def parse_measurement_data(reply: bytes) -> MeasurementData:
  """Parse a MeasurementDataResponse page.

  The values fill the payload to its end; the temperature (u16, 0.1 degrees C) is the field four
  bytes before them.

  Raises:
    ValueError: if ``reply`` is not a measurement page or is truncated.
  """
  if not is_measurement_page(reply):
    raise ValueError(f"not a MeasurementDataResponse page: {reply[:16].hex()}")
  _, _, p = split_reply(reply)

  def u32(offset: int) -> int:
    """The u32 BE at ``offset`` of the payload."""
    return int.from_bytes(p[offset : offset + 4], "big")

  total = u32(_MD_TOTAL_VALUES)
  channels = p[_MD_CHANNELS]
  chromatics: Tuple[int, ...] = (p[_MD_CHROMATICS_1],)
  wells_offset = _MD_CHROMATICS_1 + 1
  if channels == 2:
    chromatics += (int.from_bytes(p[wells_offset : wells_offset + 2], "big"),)
    wells_offset += 2
  wells = int.from_bytes(p[wells_offset : wells_offset + 2], "big")
  start = len(p) - 4 * total
  if start < wells_offset + 2 + 4:
    raise ValueError(f"MeasurementDataResponse too short for {total} values")
  raw_temperature = int.from_bytes(p[start - 4 : start - 2], "big")
  return MeasurementData(
    total_values=total,
    valid_values=u32(_MD_VALID_VALUES),
    overflow=u32(_MD_OVERFLOW),
    chromatics=chromatics,
    wells=wells,
    temperature=raw_temperature / 10 if raw_temperature else None,
    values=[u32(start + 4 * i) for i in range(total)],
  )


def compute_optical_densities(data: MeasurementData, wavelength_index: int = 0) -> List[float]:
  """The optical density of every well at the ``wavelength_index``-th wavelength.

  OD = -log10((sample / sample_100) * (reference_100 / reference)): the sample is the wavelength's
  group on channel 1, the reference the channel-2 group; the 100% values are the first value of
  their calibration pairs.  A sample of 0 gives infinity.

  Raises:
    ValueError: if the block has no reference channel or a 100% or reference value is 0.
  """
  if len(data.chromatics) != 2:
    raise ValueError("not an absorbance block: no reference channel")
  reference_index = data.chromatics[0]
  calibration = data.calibration
  sample_100 = calibration[2 * wavelength_index]
  reference_100 = calibration[2 * reference_index]
  if sample_100 == 0 or reference_100 == 0:
    raise ValueError("absorbance block has a 100% value of 0")
  densities = []
  for sample, reference in zip(data.group(wavelength_index), data.group(reference_index)):
    if reference == 0:
      raise ValueError("absorbance block has a reference value of 0")
    transmittance = (sample / sample_100) * (reference_100 / reference)
    densities.append(math.inf if transmittance <= 0 else -math.log10(transmittance))
  return densities


@dataclass(frozen=True)
class GainFocus:
  """The result of a gain and focus adjustment (GainDataResponse).

  Attributes:
    gain: the gain the adjustment found.
    focal_height: the focal height it found, in mm.
  """

  gain: int
  focal_height: float


def parse_gain_data(reply: bytes) -> GainFocus:
  """Parse the gain and focal height of a GainDataResponse.

  Raises:
    ValueError: if ``reply`` is not a GainDataResponse or is truncated.
  """
  if not reply or reply[0] != GAIN_DATA_ID:
    raise ValueError(f"not a GainDataResponse: {reply[:16].hex()}")
  _, _, p = split_reply(reply)
  if len(p) < _GD_FOCAL_HEIGHT + 2:
    raise ValueError(f"GainDataResponse too short: {reply.hex()}")
  return GainFocus(
    gain=int.from_bytes(p[_GD_GAIN : _GD_GAIN + 2], "big"),
    focal_height=int.from_bytes(p[_GD_FOCAL_HEIGHT : _GD_FOCAL_HEIGHT + 2], "big") / 100,
  )
