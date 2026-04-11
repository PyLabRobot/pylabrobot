"""EL406 protocol framing utilities — legacy re-export.

Implementation has moved to pylabrobot.agilent.biotek.el406.protocol.
"""

from pylabrobot.agilent.biotek.el406.protocol import (  # noqa: F401
  build_framed_message,
  columns_to_column_mask,
  encode_column_mask,
)
