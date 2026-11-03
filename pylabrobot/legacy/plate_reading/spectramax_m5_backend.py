import warnings

from .molecular_devices.spectramax_m5_backend import (
  MolecularDevicesSpectraMaxM5Backend,  # noqa: F401
)

warnings.warn(
  "pylabrobot.legacy.plate_reading.spectramax_m5_backend is deprecated and will be removed in a future release. "
  "Please use pylabrobot.legacy.plate_reading.molecular_devices.spectramax_m5_backend instead.",
)
