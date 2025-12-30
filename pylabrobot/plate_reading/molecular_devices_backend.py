import warnings

from .molecular_devices.backend import (  # noqa: F401s
  MolecularDevicesBackend,
  MolecularDevicesSettings,
)

warnings.warn(
  "pylabrobot.plate_reading.molecular_devices_backend is deprecated and will be removed in a future release. "
  "Please use pylabrobot.plate_reading.molecular_devices.molecular_devices_backend instead.",
)
