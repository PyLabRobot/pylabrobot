"""Which wash manifold is fitted."""

from __future__ import annotations

import enum


class WasherManifold(enum.IntEnum):
  """The wash manifold fitted to the instrument.

  The tube count and whether the manifold is dual-action (separate dispense and aspirate tubes per
  well) decide which plate formats and wash step types are usable.
  """

  TUBE_96_DUAL = 0
  TUBE_192 = 1
  TUBE_128 = 2
  TUBE_96_SINGLE = 3
  DEEP_PIN_96 = 4
  NOT_INSTALLED = 255
