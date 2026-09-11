"""LI-COR Odyssey Classic control, simulation, and TIFF metadata helpers."""

from .chatterbox import OdysseyChatterbox
from .errors import OdysseyError, OdysseyImageError, OdysseyScanError, OdysseyStatusError
from .odyssey import OdysseyClassic, OdysseyState, OdysseyStatus, StopResult, normalize_state
from .tagging import DEFAULT_SOFTWARE_TAG, build_identity_description, tag_tiff_with_identity
