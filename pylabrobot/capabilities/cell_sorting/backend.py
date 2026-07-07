from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.capabilities.capability import BackendParams, CapabilityBackend


class CellSorterBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for cell sorters (FACS) that deposit sorted events into wells.

  A cell sorter gates a stream of cells and dispenses a controlled number of them
  into each well of a plate. This is the one step in plate-based single-cell
  sequencing that usually has to happen off-automation; exposing it as a
  capability lets a deck orchestrate sort-to-plate as a normal device call.

  The interface is intentionally small and event-oriented so it can be shared with
  an acquisition/cytometry capability that reuses the same fluidics and gate
  template concepts (see the module docstring in ``cell_sorting.py``). Only the
  operations a sorter must support are declared here; convenience and validation
  live on the ``CellSorter`` frontend.
  """

  @abstractmethod
  async def get_status(self) -> str:
    """Return a coarse instrument state, e.g. ``idle``, ``running``, ``error``."""

  @abstractmethod
  async def load_template(self, name: str) -> None:
    """Select a pre-built sort template (gate hierarchy + sort logic) by name."""

  @abstractmethod
  async def set_deposition(self, cells_per_well: int, plate_format: str) -> None:
    """Configure the deposition target: cells per well and plate format."""

  @abstractmethod
  async def prime(self) -> None:
    """Prime fluidics and stabilize the stream before sorting."""

  @abstractmethod
  async def start_sort(
    self,
    wells: int,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Begin depositing into the staged plate.

    Args:
      wells: Number of wells to fill.
      backend_params: Vendor-specific parameters.
    """

  @abstractmethod
  async def wait_for_completion(self, poll_interval: float, timeout: float) -> None:
    """Block until the sort completes, polling ``get_status``.

    Args:
      poll_interval: Seconds between status polls.
      timeout: Maximum seconds to wait before raising.
    """

  @abstractmethod
  async def abort(self) -> None:
    """Stop the current sort immediately."""

  @abstractmethod
  async def clean(self) -> None:
    """Run the clean/flush cycle between samples."""
