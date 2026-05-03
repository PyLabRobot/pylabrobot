from __future__ import annotations

from dataclasses import dataclass


@dataclass
class InstrumentStatusReading:
  """Generic instrument status snapshot.

  Vendor backends populate the fields they have a value for; missing
  fields keep their defaults.
  """

  state: str
  current_user: str = ""
  progress: float = 0.0
  time_remaining: str = ""
  lid_open: bool = False
