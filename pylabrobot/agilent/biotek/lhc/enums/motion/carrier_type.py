"""Which plate carrier is fitted."""

from __future__ import annotations

import enum


class CarrierType(enum.IntEnum):
  """The plate carrier fitted to the instrument.

  The carrier determines which labware the instrument can hold, and a step is rejected when the
  plate it names is incompatible with the fitted carrier.
  """

  STANDARD = 0
  MINI_TUBE = 1
  VACUUM_FILTRATION = 2
  MAG_BEAD = 3
