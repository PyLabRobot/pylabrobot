from __future__ import annotations

import importlib
import sys
import types
import warnings
from typing import Any

# Old module paths that users might still import -> new canonical modules.
_DEPRECATED_MODULE_ALIASES: dict[str, str] = {
  # Agilent BioTek
  "pylabrobot.plate_reading.agilent_biotek_backend": "pylabrobot.plate_reading.agilent.biotek_backend",
  "pylabrobot.plate_reading.agilent_biotek_cytation_backend": "pylabrobot.plate_reading.agilent.biotek_cytation_backend",
  "pylabrobot.plate_reading.agilent_biotek_synergyh1_backend": "pylabrobot.plate_reading.agilent.biotek_synergyh1_backend",
  # Molecular Devices (keep these if they truly exist under molecular_devices/)
  "pylabrobot.plate_reading.molecular_devices_backend": "pylabrobot.plate_reading.molecular_devices.molecular_devices_backend",
  "pylabrobot.plate_reading.molecular_devices_spectramax_m5_backend": "pylabrobot.plate_reading.molecular_devices.molecular_devices_spectramax_m5_backend",
  "pylabrobot.plate_reading.molecular_devices_spectramax_384_plus_backend": "pylabrobot.plate_reading.molecular_devices.molecular_devices_spectramax_384_plus_backend",
}


def _make_deprecated_module(old_name: str, new_name: str) -> types.ModuleType:
  """Lazy proxy module: warns on first attribute access, then forwards to new module."""
  proxy = types.ModuleType(old_name)
  proxy.__package__ = old_name.rpartition(".")[0]
  proxy.__doc__ = f"DEPRECATED: use {new_name} instead."

  state: dict[str, Any] = {"mod": None, "warned": False}

  def _load() -> types.ModuleType:
    if state["mod"] is None:
      if not state["warned"]:
        warnings.warn(
          f"{old_name} is deprecated and will be removed soon; " f"use {new_name} instead.",
          DeprecationWarning,
          stacklevel=3,
        )
        state["warned"] = True
      state["mod"] = importlib.import_module(new_name)
    return state["mod"]

  def __getattr__(name: str) -> Any:
    return getattr(_load(), name)

  def __dir__() -> list[str]:
    return sorted(set(dir(_load())))

  proxy.__getattr__ = __getattr__  # type: ignore[attr-defined]
  proxy.__dir__ = __dir__  # type: ignore[assignment]
  return proxy


for old, new in _DEPRECATED_MODULE_ALIASES.items():
  if old not in sys.modules:
    sys.modules[old] = _make_deprecated_module(old, new)


# Keep top-level imports lightweight; don't import hardware backends here.
from .chatterbox import PlateReaderChatterboxBackend
from .image_reader import ImageReader
from .imager import Imager
from .plate_reader import PlateReader
from .standard import (
  Exposure,
  FocalPosition,
  Gain,
  ImagingMode,
  ImagingResult,
  Objective,
)

__all__ = [
  "PlateReader",
  "ImageReader",
  "Imager",
  "PlateReaderChatterboxBackend",
  "Exposure",
  "FocalPosition",
  "Gain",
  "ImagingMode",
  "ImagingResult",
  "Objective",
]
