"""Which syringe box is fitted."""

from __future__ import annotations

import enum


class SyringeBoxType(enum.IntEnum):
  """The syringe box fitted to the instrument.

  Whether the box is autoclavable determines the cleaning procedures it supports.
  """

  NOT_INSTALLED = 0
  AUTOCLAVABLE = 1
  NON_AUTOCLAVABLE = 2
