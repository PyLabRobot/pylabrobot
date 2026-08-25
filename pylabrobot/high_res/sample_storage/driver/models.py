from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class ModelInfo:
  has_environment_control: bool
  temperature_range: Optional[Tuple[float, float]]
  humidity_range: Optional[Tuple[float, float]]
  supports_heating: bool
  supports_active_cooling: bool
  supports_humidity_control: bool
  supports_co2_control: bool
  supports_o2_control: bool


_STERISTORE_INFO = ModelInfo(
  has_environment_control=True,
  temperature_range=(4.0, 100.0),
  humidity_range=(0.0, 0.98),
  supports_heating=True,
  supports_active_cooling=True,
  supports_humidity_control=True,
  supports_co2_control=True,
  # O2 regulation is optional, so it is discovered from environmentstatus.
  supports_o2_control=False,
)


_MODEL_INFO: Dict[str, ModelInfo] = {
  "HighResSampleStorage": ModelInfo(
    has_environment_control=True,
    temperature_range=None,
    humidity_range=None,
    supports_heating=False,
    supports_active_cooling=False,
    supports_humidity_control=False,
    supports_co2_control=False,
    supports_o2_control=False,
  ),
  "AmbiStore": ModelInfo(
    has_environment_control=False,
    temperature_range=None,
    humidity_range=None,
    supports_heating=False,
    supports_active_cooling=False,
    supports_humidity_control=False,
    supports_co2_control=False,
    supports_o2_control=False,
  ),
  "SteriStore": _STERISTORE_INFO,
  "SteriStore2": _STERISTORE_INFO,
  "TundraStore": ModelInfo(
    has_environment_control=True,
    temperature_range=(-20.0, 4.0),
    # The supported RH range depends on the configured temperature.
    humidity_range=None,
    supports_heating=False,
    supports_active_cooling=True,
    supports_humidity_control=True,
    supports_co2_control=False,
    supports_o2_control=False,
  ),
}


def get_model_info(model_name: str) -> ModelInfo:
  """Return the known values for a user-configured sample-store model."""
  try:
    return _MODEL_INFO[model_name]
  except KeyError as exc:
    raise ValueError(f"Unknown HighRes sample store model: {model_name!r}") from exc
