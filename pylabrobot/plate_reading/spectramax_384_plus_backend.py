import warnings

from .molecular_devices.spectramax_384_plus_backend import (
  MolecularDevicesSpectraMax384PlusBackend,  # noqa: F401
)

warnings.warn(
  "pylabrobot.plate_reading.spectramax_384_plus_backend is deprecated and will be removed in a future release. "
  "Please use pylabrobot.plate_reading.molecular_devices.spectramax_384_plus_backend instead.",
)
