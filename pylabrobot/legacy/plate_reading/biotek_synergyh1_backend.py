import warnings

from .agilent.biotek_synergyh1_backend import SynergyH1Backend  # noqa: F401

warnings.warn(
  "pylabrobot.legacy.plate_reading.biotek_synergyh1_backend is deprecated and will be removed in a future release. "
  "Please use pylabrobot.legacy.plate_reading.agilent.biotek_synergyh1_backend instead.",
)
