import warnings

from .agilent.biotek_backend import BioTekPlateReaderBackend  # noqa: F401

warnings.warn(
  "pylabrobot.legacy.plate_reading.biotek_backend is deprecated and will be removed in a future release. "
  "Please use pylabrobot.legacy.plate_reading.agilent.biotek_backend instead.",
)
