"""Mixing during a transfer, as any pipetting device takes it."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Mix:
  """Repeated aspirate/dispense cycles to mix the liquid during a transfer.

  Args:
    volume: The volume drawn then expelled each cycle.
    repetitions: The number of aspirate/dispense cycles.
    flow_rate: The flow rate of the mix.
    surface_following_distance: The distance (mm) the tip follows the liquid surface each cycle on
      backends that support it (e.g. Hamilton STAR); others ignore it.
  """

  volume: float
  repetitions: int
  flow_rate: float
  surface_following_distance: Optional[float] = None
