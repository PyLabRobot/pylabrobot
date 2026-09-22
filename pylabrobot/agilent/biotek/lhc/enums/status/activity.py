"""The timed phase a running step is in."""

from __future__ import annotations

import enum


class Activity(enum.IntEnum):
  """The timed phase a running step is in, as reported by a status poll.

  Reported alongside the time remaining in that phase. :attr:`NONE` means the step is running but
  is not in a phase with a countdown.
  """

  NONE = 0
  SOAKING = 1
  SHAKING = 2
  SUBMERGING_TIPS = 3
  AUTO_CLEANING = 4
  RESERVED_5 = 5
  RESERVED_6 = 6
