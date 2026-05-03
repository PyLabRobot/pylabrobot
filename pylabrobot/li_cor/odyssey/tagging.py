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

import io
import json
import logging
from typing import Any, Optional, Union

from pylabrobot.device_card import DeviceCard

logger = logging.getLogger(__name__)

DEFAULT_SOFTWARE_TAG = "PyLabRobot Odyssey"


def _resolve_identity(
  source: Union[DeviceCard, dict[str, Any], None]
) -> dict[str, Any]:
  """Coerce the identity source into a plain dict.

  Accepts a :class:`DeviceCard` (reads ``card.identity``), a plain
  dict, or None.
  """
  if source is None:
    return {}
  if isinstance(source, DeviceCard):
    return dict(source.identity or {})
  return dict(source)


def build_identity_description(
  identity: Union[DeviceCard, dict[str, Any], None] = None,
  *,
  scan_name: str = "",
  channel: Optional[int] = None,
  extra: Optional[dict[str, Any]] = None,
) -> str:
  """Render the identity payload as a compact JSON string.

  Suitable for TIFF ImageDescription, PNG ``tEXt`` chunks, JSON
  sidecars, or anywhere else a self-describing identity blob fits.
  ``identity`` may be a :class:`DeviceCard` (in which case its
  ``identity`` slot is read) or a plain dict.
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
  identity: Union[DeviceCard, dict[str, Any], None] = None,
  *,
  scan_name: str = "",
  channel: Optional[int] = None,
  software_tag: str = DEFAULT_SOFTWARE_TAG,
) -> bytes:
  """Re-emit a TIFF with the identity payload in tags 270 + 305.

  ``identity`` may be a :class:`DeviceCard` (its ``identity`` slot is
  read) or a plain dict. Returns ``raw_bytes`` unchanged when no
  identity / scan_name / channel are supplied (so unconfigured users
  see no behavior change), when PIL is not importable, or when the
  TIFF fails to parse / save.
  """
  if not raw_bytes:
    return raw_bytes
  resolved = _resolve_identity(identity)
  if not (resolved or scan_name or channel is not None):
    return raw_bytes
  try:
    from PIL import Image  # type: ignore[import-not-found]
  except ImportError:
    return raw_bytes
  try:
    img = Image.open(io.BytesIO(raw_bytes))
    img.load()
  except Exception as e:
    logger.info("TIFF re-tag skipped (parse failed): %s", e)
    return raw_bytes
  description = build_identity_description(
    resolved, scan_name=scan_name, channel=channel,
  )
  try:
    out = io.BytesIO()
    img.save(out, format="TIFF", tiffinfo={
      270: description,
      305: software_tag,
    })
    return out.getvalue()
  except Exception as e:
    logger.info("TIFF re-tag skipped (save failed): %s", e)
    return raw_bytes
