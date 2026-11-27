"""How many bottles the fitted syringe box holds."""

from __future__ import annotations

import enum


class SyringeBoxSize(enum.IntEnum):
  """How many syringes the fitted syringe box holds.

  A single box drives syringe A only; a double box drives A and B.
  """

  UNKNOWN = 0
  SINGLE = 1
  DOUBLE = 2
