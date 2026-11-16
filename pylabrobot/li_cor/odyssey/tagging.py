"""TIFF identity tagging for Odyssey scans.

Embeds an identity payload (e.g. PIDInst Handle URI, landing page,
friendly name) into standard TIFF tags so a scan lifted out of its
surrounding metadata still resolves back to the instrument it came
from. Identity is supplied as a plain dict — populate per
deployment.

Tags written:

- ``270`` ImageDescription — JSON blob with the identity fields plus
  optional ``scan_name`` / ``channel`` for self-describing scans.
- ``305`` Software — application name.

The functions are no-ops when ``identity`` is empty AND no per-call
``scan_name`` / ``channel`` is supplied. They never raise on a parse
or save failure — the original bytes are returned so a download is
never lost to a tagging failure.
"""

from __future__ import annotations

import json
import logging
import struct
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_SOFTWARE_TAG = "PyLabRobot Odyssey"


def _resolve_identity(source: Optional[dict[str, Any]]) -> dict[str, Any]:
  """Coerce the identity source into a plain dict.

  Accepts a plain identity dictionary or None.
  """
  if source is None:
    return {}
  return dict(source)


def build_identity_description(
  identity: Optional[dict[str, Any]] = None,
  *,
  scan_name: str = "",
  channel: Optional[int] = None,
  extra: Optional[dict[str, Any]] = None,
) -> str:
  """Render the identity payload as a compact JSON string.

  Suitable for TIFF ImageDescription, PNG ``tEXt`` chunks, JSON
  sidecars, or anywhere else a self-describing identity blob fits.
  ``identity`` is a dictionary of per-instrument metadata.
  """
  payload: dict[str, Any] = _resolve_identity(identity)
  if scan_name:
    payload["scan_name"] = scan_name
  if channel is not None:
    payload["channel"] = channel
  if extra:
    payload.update(extra)
  return json.dumps(payload, separators=(",", ":"))


def tag_tiff_with_identity(
  raw_bytes: bytes,
  identity: Optional[dict[str, Any]] = None,
  *,
  scan_name: str = "",
  channel: Optional[int] = None,
  software_tag: str = DEFAULT_SOFTWARE_TAG,
) -> bytes:
  """Add identity metadata without decoding or rewriting the TIFF images.

  Classic TIFF files retain every page, image payload, and untouched tag. Replacement image
  directories and strings are appended, preserving the offsets of all original data. BigTIFF
  and malformed directories are returned unchanged.
  """
  if not raw_bytes:
    return raw_bytes
  resolved = _resolve_identity(identity)
  if not (resolved or scan_name or channel is not None):
    return raw_bytes
  if raw_bytes[:4] not in (b"II*\x00", b"MM\x00*"):
    logger.info("TIFF re-tag skipped: unsupported TIFF header")
    return raw_bytes
  try:
    description = build_identity_description(resolved, scan_name=scan_name, channel=channel)
    return _append_identity_directories(raw_bytes, description, software_tag)
  except (ValueError, struct.error, OverflowError, UnicodeError) as error:
    logger.info("TIFF re-tag skipped: %s", error)
    return raw_bytes


def _append_identity_directories(raw_bytes: bytes, description: str, software: str) -> bytes:
  """Append classic TIFF directories while leaving every original data offset valid."""
  order = "<" if raw_bytes[:2] == b"II" else ">"
  offset = struct.unpack_from(order + "I", raw_bytes, 4)[0]
  directories: list[list[bytes]] = []
  visited: set[int] = set()
  while offset:
    if offset < 8 or offset in visited:
      raise ValueError("Invalid or cyclic TIFF directory chain")
    visited.add(offset)
    count = struct.unpack_from(order + "H", raw_bytes, offset)[0]
    end = offset + 2 + count * 12
    if end + 4 > len(raw_bytes):
      raise ValueError("Truncated TIFF directory")
    entries = []
    for position in range(offset + 2, end, 12):
      entry = raw_bytes[position : position + 12]
      tag = struct.unpack_from(order + "H", entry)[0]
      if tag not in (270, 305):
        entries.append(entry)
    directories.append(entries)
    offset = struct.unpack_from(order + "I", raw_bytes, end)[0]
  if not directories:
    raise ValueError("TIFF contains no image directory")

  output = bytearray(raw_bytes)
  identity_entries = []
  for tag, value in ((270, description), (305, software)):
    value_bytes = value.encode("ascii") + b"\x00"
    if len(value_bytes) <= 4:
      value_field = value_bytes.ljust(4, b"\x00")
    else:
      if len(output) % 2:
        output.append(0)
      value_field = struct.pack(order + "I", len(output))
      output.extend(value_bytes)
    identity_entries.append(struct.pack(order + "HHI", tag, 2, len(value_bytes)) + value_field)

  next_pointer = 4  # The header initially points to the first image directory.
  for entries in directories:
    if len(output) % 2:
      output.append(0)
    struct.pack_into(order + "I", output, next_pointer, len(output))
    entries = sorted(
      entries + identity_entries, key=lambda entry: struct.unpack_from(order + "H", entry)[0]
    )
    output.extend(struct.pack(order + "H", len(entries)))
    output.extend(b"".join(entries))
    next_pointer = len(output)
    output.extend(b"\x00" * 4)
  if len(output) > 0xFFFFFFFF:
    raise ValueError("Tagged TIFF exceeds classic TIFF's 32-bit offset limit")
  return bytes(output)
