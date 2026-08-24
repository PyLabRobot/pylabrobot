"""STAR firmware response parsing utilities.

Moved to `pylabrobot.hamilton.protocol.text.framing`, which holds the wire format on its own rather
than inside the USB transport that happens to carry it. Re-exported here under the old names so
existing imports keep working.
"""

from pylabrobot.hamilton.protocol.text.framing import (
  parse_firmware_version_date as parse_star_firmware_version_date,
)
from pylabrobot.hamilton.protocol.text.framing import (
  parse_fw_string as parse_star_fw_string,
)

__all__ = ["parse_star_fw_string", "parse_star_firmware_version_date"]
