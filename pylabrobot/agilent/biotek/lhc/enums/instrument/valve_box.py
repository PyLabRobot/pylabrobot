"""Which valve box is fitted."""

from __future__ import annotations

import enum


class ValveBox(enum.IntEnum):
  """The buffer selection valve fitted to the instrument.

  Any value other than :attr:`NOT_INSTALLED` means a step may select a buffer other than A.
  """

  NOT_INSTALLED = 0
  WASHER = 1
  SYRINGE = 2
  INTERNAL_1 = 3
  INTERNAL_2 = 4
  INTERNAL_3 = 5
  INTERNAL_4 = 6
