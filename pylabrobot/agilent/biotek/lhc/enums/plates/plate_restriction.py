"""Which plates an instrument has been configured to accept."""

from __future__ import annotations

import enum


class PlateRestriction(enum.IntEnum):
  """Which plates an instrument has been configured to accept.

  Set on the instrument rather than by a protocol. A step naming a plate the instrument does not
  accept is rejected before it runs.
  """

  ALLOW_ALL = 0
  ALLOW_96_WELL_ONLY = 1
  ALLOW_1536_WELL_ONLY = 2
