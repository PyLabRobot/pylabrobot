class CellSorterError(Exception):
  """Base class for cell sorter errors."""


class SortNotReadyError(CellSorterError):
  """Raised when a sort is requested but the instrument is not ready to sort."""


class SortActuationError(CellSorterError):
  """Raised when an actuating command is issued without the required safety opt-in.

  Sorters combine a laser with pressurized fluidics, so backends should refuse to
  physically actuate unless the caller has explicitly armed the instrument and
  allowed actuation.
  """


class SortTimeoutError(CellSorterError):
  """Raised when a sort does not complete within the allotted time."""
