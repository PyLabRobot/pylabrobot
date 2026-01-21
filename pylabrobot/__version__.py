"""Definition file for various version numbers."""

import os

# Version number for pylabrobot
_version_file = os.path.join(os.path.dirname(__file__), "version.txt")
with open(_version_file, "r", encoding="utf-8") as f:
  __version__ = f.read().strip()

# Version number of the standard form protocol used by the server.
STANDARD_FORM_JSON_VERSION = "0.1.0"
