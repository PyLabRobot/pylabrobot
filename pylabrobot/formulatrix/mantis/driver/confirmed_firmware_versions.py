"""Mantis firmware versions seen on a device, per generation, by board."""

from typing import Dict, Optional, Tuple

CONFIRMED_FIRMWARE_VERSIONS: Dict[str, Tuple[Dict[int, Optional[str]], ...]] = {
  "v3": ({0: "4.20.6", 10: "1.0.5", 11: None, 12: None},),
  "v4": ({0: "4.20.6.2", 10: "1.0.8", 11: "0.1.7 DBG", 12: "0.1.8 DBG"},),
}
