"""Which strip wash manifold is fitted."""

from __future__ import annotations

import enum


class StripWasherManifold(enum.IntEnum):
  """The strip-washer manifold fitted to the instrument.

  Named by the plate format it services. Strip washing is a separate wash head from the main wash
  manifold and is offered only by the models that can carry one.
  """

  PLATE_6_WELL = 0
  PLATE_12_WELL = 1
  PLATE_24_WELL = 2
  PLATE_48_WELL = 3
  PLATE_96_WELL = 4
  NOT_INSTALLED = 255
