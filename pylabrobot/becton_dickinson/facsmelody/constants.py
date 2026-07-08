"""Constants for the BD FACSMelody sorter backend.

The command set and transport model here are the consumer-side contract: a
``ProtocolMap`` produced by an external reverse-engineering step (see
``docs/facsmelody-re.md``) is expected to decode exactly these commands. This
module holds no decoded bytes; it only names what a complete map must contain.
"""

from enum import Enum
from typing import List, Tuple

DEFAULT_SORT_TEMPLATE = "singlet_deposit"
SUPPORTED_PLATE_FORMATS = ("96", "384")


class Transport(str, Enum):
  """How the host reaches the instrument's control link."""

  USB = "usb"  # PyUSB bulk/interrupt endpoints
  SERIAL = "serial"  # pyserial COM/tty
  TCP = "tcp"  # raw TCP socket (some BD carts expose an Ethernet link)
  UNKNOWN = "unknown"


# The minimum command set a sort-to-plate run needs. A ProtocolMap must decode all
# of these before the backend will drive live hardware. Kept as (name, purpose)
# pairs so an incomplete map can report exactly what is missing.
#
# Note: `connect` and `wait_complete` are part of the required decode set so a
# validated map is provably complete, but the current backend does not yet emit
# them as discrete frames -- setup() opens the link directly and wait_for_completion
# polls get_status. Whether a real Melody needs an explicit connect handshake and a
# blocking wait_complete (vs. status polling) is resolved during hardware validation;
# until then they are decode-required but unsent. See docs/facsmelody-re.md.
REQUIRED_COMMANDS: List[Tuple[str, str]] = [
  ("connect", "open the control link / handshake with the cart"),
  ("get_status", "poll instrument state (idle/running/clog/error)"),
  ("load_template", "select a pre-built sort template (gate hierarchy) by name"),
  ("set_deposition", "set plate format + target cells-per-well for sort-to-plate"),
  ("prime", "prime fluidics / start stream, verify break-off stable"),
  ("start_sort", "begin depositing into the staged plate"),
  ("wait_complete", "block/poll until the plate is fully sorted"),
  ("abort", "emergency stop the sort"),
  ("clean", "run the clean/flush cycle between samples"),
]

# Commands that physically actuate the instrument (laser + pressurized fluidics).
# Sending any of these requires an explicit actuation opt-in on the driver.
ACTUATING_COMMANDS = frozenset({"prime", "start_sort", "clean", "set_deposition"})
