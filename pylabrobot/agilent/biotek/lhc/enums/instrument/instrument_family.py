"""Which hardware family a model belongs to."""

from __future__ import annotations

import enum


class InstrumentFamily(enum.IntEnum):
  """The hardware family a model belongs to.

  Several models share one family. The family determines which motor numbering applies, which
  option queries the instrument answers, and which step types can be offered.
  """

  EL406 = 0
  MULTIFLO = 1
  MODEL_405_TS = 2
  MULTIFLO_FX = 3
