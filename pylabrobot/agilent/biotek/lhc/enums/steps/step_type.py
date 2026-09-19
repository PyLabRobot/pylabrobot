"""Which operation a protocol step performs."""

from __future__ import annotations

import enum


class StepType(enum.IntEnum):
  """The operation a protocol step performs.

  Members are prefixed by the hardware that carries them out: ``PERI_`` for the peristaltic pumps,
  ``SYRINGE_`` for the syringe dispenser, ``MANIFOLD_`` for the wash manifold, ``STRIP_`` for the
  strip washer and ``PERI_WASH_`` for peristaltic media exchange. Which members an instrument
  offers depends on its fitted hardware and firmware.
  """

  UNDEFINED = 0
  PERI_DISPENSE = 1
  PERI_PRIME = 2
  PERI_PURGE = 3
  SYRINGE_DISPENSE = 4
  SYRINGE_PRIME = 5
  MANIFOLD_WASH = 6
  MANIFOLD_ASPIRATE = 7
  MANIFOLD_DISPENSE = 8
  MANIFOLD_PRIME = 9
  MANIFOLD_AUTO_CLEAN = 10
  SHAKE_SOAK = 11
  WASH_1536 = 12
  STRIP_WASH = 13
  STRIP_ASPIRATE = 14
  STRIP_DISPENSE = 15
  STRIP_PRIME = 16
  PERI_WASH_ASPIRATE = 17
  PERI_WASH_DISPENSE = 18
