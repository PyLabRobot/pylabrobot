"""Legacy. Use pylabrobot.hamilton.tilt_module instead."""

from pylabrobot.capabilities.tilting.backend import TiltModuleError  # noqa: F401
from pylabrobot.hamilton.tilt_module.backend import (  # noqa: F401
  HamiltonTiltModuleBackend,
  HamiltonTiltModuleChatterboxBackend,
)
