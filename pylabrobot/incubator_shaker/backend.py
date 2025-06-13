from pylabrobot.incubators.backend import IncubatorBackend
from pylabrobot.shaking.backend import ShakerBackend


class IncubatorShakerBackend(IncubatorBackend, ShakerBackend):
    """Abstract base class for incubators with shaking capabilities."""

    pass
