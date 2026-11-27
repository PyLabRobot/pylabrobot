"""Length-prefixed 64-byte HID report framing used by KingFisher instruments."""


def encode_reports(payload: bytes) -> tuple[bytes, ...]:
  """Encode payload bytes into 64-byte reports, excluding the HID report ID."""
  if not payload:
    raise ValueError("Cannot encode an empty payload")
  return tuple(
    bytes([len(payload[offset : offset + 63])]) + payload[offset : offset + 63].ljust(63, b"\x00")
    for offset in range(0, len(payload), 63)
  )


def decode_report(report: bytes) -> bytes:
  """Extract a report's declared payload, rejecting invalid lengths and ignoring padding."""
  if not report or len(report) > 64 or report[0] > 63 or report[0] > len(report) - 1:
    raise ValueError("Invalid KingFisher HID payload length")
  return report[1 : 1 + report[0]]
