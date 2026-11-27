"""The labware formats an instrument can be set to."""

from __future__ import annotations

import enum


class PlateType(enum.IntEnum):
  """The labware format the instrument is set to.

  This is how the instrument is told what sits on the carrier; it selects the well pitch, the
  travel limits and the default head heights the firmware uses. Public APIs take a
  :class:`~pylabrobot.resources.Plate` and resolve it to a member of this enum.

  Values 15 through 18 are unassigned. The two test plates are calibration labware, not
  general-purpose plates.
  """

  PLATE_1536_WELL = 0
  PLATE_384_WELL = 1
  PLATE_384_WELL_PCR = 2
  PLATE_384_DEEP_WELL = 3
  PLATE_96_WELL = 4
  PLATE_96_DEEP_WELL = 5
  PLATE_96_HALF_WELL = 6
  PLATE_96_MINI_TUBES = 7
  PLATE_48_WELL = 8
  PLATE_24_WELL = 9
  TUBES_20_12X75 = 10
  TUBES_20_13X100 = 11
  PLATE_12_WELL = 12
  PLATE_6_WELL = 13
  PLATE_1536_FLANGE = 14
  TEST_PLATE_96_WELL_MB = 19
  TEST_PLATE_96_WELL_BD = 20
