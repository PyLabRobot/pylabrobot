"""Which firmware variant an instrument is running."""

from __future__ import annotations

import enum


class Basecode(enum.IntEnum):
  """The firmware variant an instrument is running.

  The variant is a property of the installed firmware image, not a fitted option, and decides
  whether the random-access and peristaltic-wash step types are available.
  """

  BASIC = 0
  RANDOM_ACCESS = 1
  PERI_WASH = 2
