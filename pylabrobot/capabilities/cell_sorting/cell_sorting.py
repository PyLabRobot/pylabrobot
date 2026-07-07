"""Cell sorting (FACS) capability.

Why this exists
---------------
Sorting is the one step in plate-based single-cell sequencing that usually has to
happen off-automation. A sorter gates a stream of cells and deposits a controlled
number of them into each well; the rest of the library prep can run on a liquid
handler, but the sort itself is typically a manual hand-off. Modeling the sorter
as a capability closes that gap: a deck can call ``sort_to_plate`` the same way it
calls ``spin`` on a centrifuge, depositing one gated cell per well for single-cell
work or a targeted, enriched population, then letting the deck finish the prep.

Sorting for a target population is the other half: enrich a specific cell type, a
marker-positive subset, or live singlets before the assay. That is what gives
cell-type resolution to downstream measurements, for example cell-type-specific
epigenomics, where you want to read regulatory state in the exact cell type a
variant acts in rather than in a bulk average.

Scope
-----
This capability covers the sort/deposition side of a FACS instrument. Acquisition
and analysis (event rates, gate statistics, population readout) belong to a
sibling cytometry capability that would reuse the same fluidics and gate-template
primitives; the two are designed to sit next to each other so a single instrument
can expose both without duplicating an interface.
"""

from typing import Optional

from pylabrobot.capabilities.capability import BackendParams, Capability, need_capability_ready

from .backend import CellSorterBackend

_SUPPORTED_PLATE_FORMATS = ("96", "384")


class CellSorter(Capability):
  """Cell sorting capability: deposit gated events into the wells of a plate.

  The frontend owns validation, orchestration, and convenience methods. The
  backend owns the wire protocol for one instrument. See the module docstring for
  what this capability unlocks scientifically.
  """

  def __init__(self, backend: CellSorterBackend):
    super().__init__(backend=backend)
    self.backend: CellSorterBackend = backend

  @need_capability_ready
  async def get_status(self) -> str:
    """Return a coarse instrument state, e.g. ``idle``, ``running``, ``error``."""
    return await self.backend.get_status()

  @need_capability_ready
  async def sort_to_plate(
    self,
    cells_per_well: int,
    wells: int,
    template: str,
    plate_format: str = "96",
    poll_interval: float = 5.0,
    timeout: float = 3600.0,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Run a full sort-to-plate cycle.

    Sequences the backend primitives: load the gate template, configure the
    deposition target, prime the stream, start the sort, wait for completion, and
    clean. Vendors that need a different order can override on the backend.

    Args:
      cells_per_well: Target number of cells to deposit per well (must be > 0).
      wells: Number of wells to fill (must be > 0).
      template: Name of a pre-built sort template (gate hierarchy + sort logic).
      plate_format: Plate format, one of ``96`` or ``384``.
      poll_interval: Seconds between status polls while waiting.
      timeout: Maximum seconds to wait for the sort to complete.
      backend_params: Vendor-specific parameters passed to ``start_sort``.
    """
    if cells_per_well <= 0:
      raise ValueError(f"cells_per_well must be positive, got {cells_per_well}")
    if wells <= 0:
      raise ValueError(f"wells must be positive, got {wells}")
    if plate_format not in _SUPPORTED_PLATE_FORMATS:
      raise ValueError(
        f"plate_format must be one of {_SUPPORTED_PLATE_FORMATS}, got {plate_format!r}"
      )

    await self.backend.load_template(name=template)
    await self.backend.set_deposition(cells_per_well=cells_per_well, plate_format=plate_format)
    await self.backend.prime()
    await self.backend.start_sort(wells=wells, backend_params=backend_params)
    await self.backend.wait_for_completion(poll_interval=poll_interval, timeout=timeout)
    await self.backend.clean()

  @need_capability_ready
  async def abort(self) -> None:
    """Stop the current sort immediately."""
    await self.backend.abort()

  @need_capability_ready
  async def clean(self) -> None:
    """Run the clean/flush cycle between samples."""
    await self.backend.clean()
