"""EL406 low-level communication methods — legacy re-export.

Implementation has moved to pylabrobot.agilent.biotek.el406.driver.
"""

from pylabrobot.agilent.biotek.el406.driver import (  # noqa: F401
  LONG_READ_TIMEOUT,
  DevicePollResult,
)
from pylabrobot.agilent.biotek.el406.driver import (  # noqa: F401
  EL406Driver as EL406CommunicationMixin,
)
