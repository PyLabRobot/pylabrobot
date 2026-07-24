from typing import List

from pylabrobot.capabilities.cell_sorting.errors import (
  CellSorterError,
  SortActuationError,
  SortNotReadyError,
  SortTimeoutError,
)

__all__ = [
  "FACSMelodyError",
  "ProtocolMapIncompleteError",
  "SortActuationError",
  "SortNotReadyError",
  "SortTimeoutError",
]


class FACSMelodyError(CellSorterError):
  """Base class for BD FACSMelody backend errors."""


class ProtocolMapIncompleteError(FACSMelodyError):
  """Raised when a live sort is requested but the ProtocolMap is not fully decoded.

  The backend refuses to drive the instrument until every required command has
  been reverse-engineered, so a half-mapped protocol can never actuate hardware.
  """

  def __init__(self, missing: List[str]):
    self.missing = missing
    super().__init__(
      "ProtocolMap is incomplete; the following required commands are not yet "
      f"decoded: {missing}. Finish the RE decode (see docs/facsmelody-re.md) "
      "before a live sort."
    )
