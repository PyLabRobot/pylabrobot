from dataclasses import dataclass


@dataclass
class Step:
  """Represents a single step in a thermocycler profile."""

  temperature: float
  hold_seconds: float
