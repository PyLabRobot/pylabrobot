"""BD FACSMelody cell sorter.

Why this matters
----------------
The FACSMelody is a benchtop sorter with a plate-deposition module. Sorting is the
one step in plate-based single-cell sequencing that usually has to happen
off-automation, so bringing the Melody under PyLabRobot lets a deck run the sort
as a normal device call: one gated cell per well for single-cell work, or a
targeted, enriched population before the assay. That population mode (a cell type,
a marker-positive subset, live singlets) is what gives cell-type resolution to
downstream measurements such as cell-type-specific epigenomics.

Validation status
-----------------
This backend drives the instrument by replaying a decoded ``ProtocolMap`` produced
by an external reverse-engineering step (see ``docs/facsmelody-re.md``). As
shipped it is not yet hardware-validated: it runs end-to-end dry (building and
logging every frame) and refuses to open a live link until a complete map is
supplied and the instrument is armed. It does not report a sort it has not run.
"""

from typing import Optional

from pylabrobot.capabilities.cell_sorting import CellSorter
from pylabrobot.device import Device

from .backend import FACSMelodyCellSorterBackend, FACSMelodyDriver

__all__ = ["FACSMelody"]


class FACSMelody(Device):
  """BD FACSMelody sorter exposing the :class:`CellSorter` capability.

  Args:
    protocol_path: Path to a decoded ProtocolMap JSON. Required for a live run;
      omit it to build the device in dry-run.
    armed: Open the physical link and allow transmission. Off by default.
    allow_actuation: Permit commands that physically actuate the sorter. Off by
      default; a human should be present when this is on.
  """

  driver: FACSMelodyDriver

  def __init__(
    self,
    protocol_path: Optional[str] = None,
    *,
    armed: bool = False,
    allow_actuation: bool = False,
  ):
    driver = FACSMelodyDriver(
      protocol_path=protocol_path,
      armed=armed,
      allow_actuation=allow_actuation,
    )
    super().__init__(driver=driver)
    self.driver: FACSMelodyDriver = driver
    self.sorter = CellSorter(backend=FACSMelodyCellSorterBackend(driver))
    self._capabilities = [self.sorter]

  def serialize(self) -> dict:
    return {**Device.serialize(self)}

  @classmethod
  def deserialize(cls, data: dict) -> "FACSMelody":
    driver_data = data.get("driver") or data.get("backend") or {}
    return cls(
      protocol_path=driver_data.get("protocol_path"),
      armed=driver_data.get("armed", False),
      allow_actuation=driver_data.get("allow_actuation", False),
    )
