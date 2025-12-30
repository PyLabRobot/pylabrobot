import warnings

from .bmg_labtech.clario_star_backend import CLARIOstarBackend

warnings.warn(
  "pylabrobot.plate_reading.clario_star_backend is deprecated and will be removed in a future release. "
  "Please use pylabrobot.plate_reading.bmg_labtech.clario_star_backend instead.",
)
