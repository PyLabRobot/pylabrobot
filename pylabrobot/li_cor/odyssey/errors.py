"""Odyssey driver exceptions — dual-base for capability + vendor reach.

Each backend-level error inherits from BOTH the vendor's driver-level
exception (:class:`OdysseyError`) AND the capability-generic exception
(:class:`ScanningError`, :class:`ImageRetrievalError`,
:class:`InstrumentStatusError`). A single raise is then catchable on
either axis::

    try:
      await odyssey.scanning.start()
    except ScanningError:
      ...   # capability-generic recovery (works for any scanning backend)
    except OdysseyError:
      ...   # vendor-specific debugging
"""

from __future__ import annotations

from pylabrobot.capabilities.scanning.image_retrieval import ImageRetrievalError
from pylabrobot.capabilities.scanning.instrument_status import InstrumentStatusError
from pylabrobot.capabilities.scanning.scanning import ScanningError


class OdysseyError(Exception):
  """Base exception for the LI-COR Odyssey Classic driver.

  Raised at the connection / transport layer for protocol or HTTP
  failures. Capability-specific raises use a subclass that also
  inherits from the matching capability-generic exception.
  """


class OdysseyScanError(OdysseyError, ScanningError):
  """Odyssey scanning capability failed.

  Catchable as either :class:`OdysseyError` (vendor-specific) or
  :class:`ScanningError` (capability-generic).
  """


class OdysseyImageError(OdysseyError, ImageRetrievalError):
  """Odyssey image retrieval failed (download / list / preview)."""


class OdysseyStatusError(OdysseyError, InstrumentStatusError):
  """Odyssey status read failed."""


__all__ = [
  "OdysseyError",
  "OdysseyScanError",
  "OdysseyImageError",
  "OdysseyStatusError",
]
