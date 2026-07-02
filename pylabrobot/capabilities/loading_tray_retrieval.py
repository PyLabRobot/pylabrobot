from typing import Optional

from pylabrobot.capabilities.capability import Capability, CapabilityBackend
from pylabrobot.resources import Plate, PlateHolder, ResourceNotFoundError


class LoadingTrayRetrieval(Capability):
  """Shared base for storage-retrieval capabilities that move plates to and from a single
  transfer position -- the "loading tray".

  Concrete capabilities differ only in how storage locations are addressed:

  * :class:`~pylabrobot.capabilities.automated_retrieval.AutomatedRetrieval` is *random access*
    -- individually addressable rack sites.
  * :class:`~pylabrobot.capabilities.stacker.Stacker` is *sequential* -- single-ended LIFO stacks.

  This base owns the loading tray and the small amount of plate-movement plumbing the two share
  (loading-tray access and the summary table), so the concrete capabilities only implement their
  location-addressing logic.
  """

  def __init__(self, backend: CapabilityBackend, loading_tray: Optional[PlateHolder] = None):
    super().__init__(backend=backend)
    self.loading_tray = loading_tray

  def _require_loading_tray(self) -> PlateHolder:
    if self.loading_tray is None:
      raise RuntimeError("No loading tray configured for this capability.")
    return self.loading_tray

  def _plate_on_loading_tray(self) -> Plate:
    tray = self._require_loading_tray()
    plate = tray.resource
    if not isinstance(plate, Plate):
      raise ResourceNotFoundError("No plate on the loading tray.")
    return plate

  @staticmethod
  def _pretty_table(header, *columns) -> str:
    col_widths = [
      max(len(str(item)) for item in [header[i]] + list(columns[i])) for i in range(len(header))
    ]

    def format_row(row, border="|") -> str:
      return (
        f"{border} "
        + " | ".join(f"{str(row[i]).ljust(col_widths[i])}" for i in range(len(row)))
        + f" {border}"
      )

    def separator_line(cross: str = "+", line: str = "-") -> str:
      return cross + cross.join(line * (width + 2) for width in col_widths) + cross

    table = [separator_line(), format_row(header), separator_line()]
    for row in zip(*columns):
      table.append(format_row(row))
    table.append(separator_line())
    return "\n".join(table)
