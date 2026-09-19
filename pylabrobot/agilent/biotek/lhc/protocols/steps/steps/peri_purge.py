"""Purging a peristaltic pump."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_prime import PeriPrime


@dataclass
class PeriPurge(PeriPrime):
  """Pump a cassette empty, running fluid to waste rather than to the plate.

  Parameters, stored layout and payload are the same as a peristaltic prime; only the operation
  differs.
  """

  step_type: ClassVar[StepType] = StepType.PERI_PURGE
