"""The sensors an instrument can be asked about."""

from __future__ import annotations

import enum


class Sensor(enum.IntEnum):
  """A sensor whose presence and enabled state can be queried."""

  VACUUM = 0
  WASTE = 1
  FLUID = 2
  FLOW = 3
  FILTER_VACUUM = 4
  PLATE = 5
