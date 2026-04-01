"""EL406 low-level communication methods — legacy re-export.

Implementation has moved to pylabrobot.agilent.biotek.el406.driver.
"""

from pylabrobot.agilent.biotek.el406.driver import (  # noqa: F401
  LONG_READ_TIMEOUT,
  DevicePollResult,
  EL406Driver as EL406CommunicationMixin,
)
