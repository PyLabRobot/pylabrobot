"""The capability objects an instrument exposes for the hardware it is fitted with."""

from __future__ import annotations

from pylabrobot.agilent.biotek.lhc.devices.components.peristaltic_dispenser import (
  PeristalticDispenser,
)
from pylabrobot.agilent.biotek.lhc.devices.components.syringe_dispenser import SyringeDispenser
from pylabrobot.agilent.biotek.lhc.devices.components.washer import PlateWasher

__all__ = ["PeristalticDispenser", "PlateWasher", "SyringeDispenser"]
