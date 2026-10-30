"""Framing and endpoint absorbance records for the BioTek RDOP22 protocol."""

from typing import Optional, Tuple


def parse_response(response: Optional[bytes], command: str) -> bytes:
  """Check the hexadecimal status of a reply and return its data, excluding ACK."""
  if response is None or len(response) < 5 or response[-1:] != b"\x03":
    raise ValueError(f"BioTek {command}: incomplete response: {response!r}")
  status = response[-5:-1]
  if any(c not in b"0123456789ABCDEFabcdef" for c in status):
    raise ValueError(f"BioTek {command}: invalid status: {status!r}")
  if int(status, 16) != 0:
    raise RuntimeError(f"BioTek {command}: instrument status 0x{status.decode()}")
  data = response[:-5]
  return data[1:] if data.startswith(b"\x06") else data


def parse_assay_response(response: Optional[bytes]) -> bytes:
  """Validate a D reply: length3 + data + checksum3 + status4 + ETX.

  The length counts everything after the length field, including status and ETX.
  The checksum is the unsigned byte sum of the data alone.
  """
  framed = parse_response(response, "D")
  if len(framed) < 6 or not framed[:3].isdigit() or not framed[-3:].isdigit():
    raise ValueError("BioTek D: invalid length/checksum fields")
  if int(framed[:3]) != len(framed) + 2:
    raise ValueError("BioTek D: response length mismatch")
  data = framed[3:-3]
  if int(framed[-3:]) != sum(data) % 256:
    raise ValueError("BioTek D: response checksum mismatch")
  return data


def encode_assay(body: str) -> str:
  """Frame a D assay with length4 and checksum3; no ETX follows the checksum."""
  length = len(body) + 3
  if length > 9999:
    raise ValueError("BioTek assay exceeds the four-digit length field")
  checksum = sum(body.encode("ascii")) % 256
  return f"{length:04}{body}{checksum:03}"


def encode_absorbance(
  bounds: Tuple[int, int, int, int],
  wavelength: int,
  extended_optics: bool,
  extra_read_field: bool,
) -> str:
  """Encode a single-wavelength endpoint assay with one-based, inclusive bounds.

  Capability 2064 controls the optical-path byte, and 2056 controls an extra
  three-digit read field. These capabilities are independent of firmware major
  version. The record selects normal plate mode and eight flashes.
  """
  min_row, min_col, max_row, max_col = bounds
  if not (1 <= min_row <= max_row <= 99 and 1 <= min_col <= max_col <= 99):
    raise ValueError("BioTek assay bounds must be ordered and between 1 and 99")
  if not 1 <= wavelength <= 9999:
    raise ValueError("BioTek wavelength must fit four decimal digits")
  header = f"01{min_row:02}{min_col:02}{max_row:02}{max_col:02}0001200100001"
  read_event = "1001000001"
  optics = "0" + ("6" if extended_optics else "") + "00008"
  optics += ("000" if extra_read_field else "") + f"{wavelength:04}"
  return encode_assay(header + read_event + optics)
