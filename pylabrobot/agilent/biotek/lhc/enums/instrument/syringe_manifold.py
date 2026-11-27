"""Which syringe manifold is fitted."""

from __future__ import annotations

import enum


class SyringeManifold(enum.IntEnum):
  """The syringe dispense manifold fitted to the instrument.

  Tube manifolds are named by tube count and bore size; plate manifolds are named by the plate
  format they dispense into.
  """

  NOT_INSTALLED = 0
  TUBE_16 = 1
  TUBE_32_LARGE_BORE = 2
  TUBE_32_SMALL_BORE = 3
  TUBE_16_7 = 4
  TUBE_8 = 5
  PLATE_6_WELL = 6
  PLATE_12_WELL = 7
  PLATE_24_WELL = 8
  PLATE_48_WELL = 9
