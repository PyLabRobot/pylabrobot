"""What the MPE2 reports: its pressures, its status, and pressures recorded over time."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pylabrobot.hamilton.mpe2 import commands as cmd
from pylabrobot.hamilton.mpe2.states import MPE2State, WasteContainerNumber


@dataclass(frozen=True)
class PressureReadings:
  """The MPE2's pressure sensors, in psi, and its flow sensor, in standard L/min (slpm).

  Attributes:
    input_psi: the gas supply; the firmware works between 80 and 120 psi.
    manifold_psi: in the manifold, over the plate.
    high_path_psi: in the high pressure path.
    low_path_psi: in the low pressure path.
    low_path_flow_slpm: the flow in the low pressure path.
    differential_psi: across the flow sensor.
    waste_vacuum_psi: in the waste line; negative while the pump draws.
  """

  input_psi: float
  manifold_psi: float
  high_path_psi: float
  low_path_psi: float
  low_path_flow_slpm: float
  differential_psi: float
  waste_vacuum_psi: float


@dataclass(frozen=True)
class MPE2Status:
  """Where the MPE2 is.

  Attributes:
    state: the plate handling state, as a snake_case name ("ready", "filter_plate_clamped").
    initialized: the firmware's IsInitialized, True only when it is ready: with a plate placed or
      clamped it answers False although nothing was lost. ``state ==
      "uninitialized"`` is what says the MPE2 has to be initialized again.
    labware: the plate heights it holds.
    vacuum_on: whether the vacuum pump runs.
    selected_waste_container: the waste container the vacuum draws into, 1 or 2.
    waste: each waste container's presence and fullness.
  """

  state: MPE2State
  initialized: bool
  labware: cmd.LabwareParameters
  vacuum_on: bool
  selected_waste_container: Optional[WasteContainerNumber]
  waste: Dict[WasteContainerNumber, cmd.WasteStatus] = field(default_factory=dict)


@dataclass(frozen=True)
class PressureSample:
  """One reading of the gas supply and the manifold, in psi, at ``time`` s from the start; and the
  high pressure path (between the supply and the MPE2's valve) when it was asked for."""

  time: float
  input_psi: float
  manifold_psi: float
  high_path_psi: Optional[float] = None


@dataclass
class PressureRecording:
  """The readings ``MPE2.record_pressures()`` took, in order."""

  samples: List[PressureSample] = field(default_factory=list)

  @property
  def lowest_input(self) -> Optional[PressureSample]:
    """The reading with the lowest gas supply pressure: the dip."""
    return min(self.samples, key=lambda x: x.input_psi, default=None)

  def __str__(self) -> str:
    """The recording in one line: its length, the supply's dip, the manifold's peak."""
    if not self.samples:
      return "no readings"
    low = self.lowest_input
    assert low is not None
    first = self.samples[0]
    return (
      f"{len(self.samples)} readings over {self.samples[-1].time:.1f} s; input gas from "
      f"{first.input_psi:.1f} psi, lowest {low.input_psi:.1f} psi at {low.time:.2f} s (a dip of "
      f"{first.input_psi - low.input_psi:.1f} psi); manifold up to "
      f"{max(x.manifold_psi for x in self.samples):.1f} psi"
    )
