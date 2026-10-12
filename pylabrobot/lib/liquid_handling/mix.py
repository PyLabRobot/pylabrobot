"""Mixing during a transfer, as any pipetting device takes it."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Mix:
  """Repeated aspirate/dispense cycles to mix the liquid during a transfer.

  Args:
    volume: The volume drawn then expelled each cycle.
    repetitions: The number of aspirate/dispense cycles.
    flow_rate: The flow rate of the mix.
    surface_following_distance: The distance (mm) the tip follows the liquid surface each cycle on
      backends that support it (e.g. Hamilton STAR); others ignore it. 0 does not follow.
    auto_surface_following: Follow by what one draw moves the surface, from the surface the call
      finds or is given. Refused beside a `surface_following_distance`.
  """

  volume: float
  repetitions: int
  flow_rate: float
  surface_following_distance: float = 0.0
  auto_surface_following: bool = False

  def __post_init__(self):
    if self.auto_surface_following and self.surface_following_distance != 0.0:
      raise ValueError(
        "auto_surface_following beside a surface_following_distance: give one of them"
      )
