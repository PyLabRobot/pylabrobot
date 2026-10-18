"""StoreX cassettes with explicit plate clearance and calibrated lift pitch."""

import math
from typing import Optional

from pylabrobot.resources import Coordinate, PlateCarrier, PlateHolder

# StoreX Remote Operation V0711, cassette configuration table (DM230..DM239).
PITCH_TO_STEPS: dict[float, int] = {
  11: 377,
  17: 582,
  21: 719,
  23: 788,
  28: 959,
  33: 1131,
  50: 1713,
  63: 2158,
  72: 2467,
  110: 3769,
}


class StoreXCassette(PlateCarrier):
  """One cassette, whose zero-based sites map to firmware levels 1..num_sites.

  ``pitch`` and ``site_height`` are millimeters; ``pitch_steps`` is the calibrated
  DM23 value for this instrument. Supply it explicitly on instruments with a
  different lift reduction ratio (including third-generation instruments).
  Internal coordinates describe slot spacing only; the cassette has no measured
  robot-accessible position within the store.
  """

  def __init__(
    self,
    name: str,
    *,
    num_sites: int,
    site_height: float,
    pitch: float,
    pitch_steps: Optional[int] = None,
  ):
    if isinstance(num_sites, bool) or not isinstance(num_sites, int) or not 1 <= num_sites <= 255:
      raise ValueError("num_sites must be an integer in 1..255")
    if not math.isfinite(pitch) or not math.isfinite(site_height) or not 0 < site_height <= pitch:
      raise ValueError("Require finite 0 < site_height <= pitch")
    if pitch_steps is None:
      if pitch not in PITCH_TO_STEPS:
        raise ValueError("Supply calibrated pitch_steps for this cassette pitch")
      pitch_steps = PITCH_TO_STEPS[pitch]
    if (
      isinstance(pitch_steps, bool)
      or not isinstance(pitch_steps, int)
      or not 1 <= pitch_steps <= 65535
    ):
      raise ValueError("pitch_steps must be an integer in 1..65535")
    self.pitch = pitch
    self.pitch_steps = pitch_steps
    self.site_height = site_height
    super().__init__(
      name=name,
      size_x=85.48,
      size_y=127.76,
      size_z=pitch * (num_sites - 1) + site_height,
      model="StoreX cassette",
      sites={
        i: PlateHolder(
          name=f"{name}_slot_{i + 1}",
          size_x=85.48,
          size_y=127.76,
          size_z=site_height,
          pedestal_size_z=0,
        ).at(Coordinate(z=i * pitch))
        for i in range(num_sites)
      },
    )


def _create_legacy_rack(
  name: str,
  site_height: int,
  num_sites: int,
  model: str,
  total_height: int = 505,
) -> PlateCarrier:
  """Build a PlateCarrier with the fixed geometry used by the legacy rack factories."""
  start = 17.2  # rough height of first plate position
  return PlateCarrier(
    name=name,
    size_x=109,  # based off cytomat rack dimensions roughly the same
    size_y=142,
    size_z=total_height,
    sites={
      i: PlateHolder(
        size_x=85.48,
        size_y=127.27,
        # estimates
        size_z=max(site_height, total_height - site_height) if i == num_sites - 1 else site_height,
        name=f"{name}-{i}",
        pedestal_size_z=0,
      ).at(
        Coordinate(
          x=11.76,  # estimate
          y=0,
          z=start + site_height * i,
        )
      )
      for i in range(num_sites)
    },
    model=model,
  )
