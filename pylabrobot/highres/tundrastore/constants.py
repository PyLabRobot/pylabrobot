import enum


class DoorState(enum.Enum):
  """State of a single TundraStore door, as reported by ``doorstatus``."""

  OPEN = "OPEN"
  CLOSED = "CLOSED"
  OPENING = "OPENING"
  CLOSING = "CLOSING"
  UNKNOWN = "UNKNOWN"


class NestState(enum.Enum):
  """State of a transfer nest, as reported by ``neststatus``."""

  CLEAR = "CLEAR"
  OCCUPIED = "OCCUPIED"
  UNKNOWN = "UNKNOWN"


# Completion-status tokens that terminate a command's reply (see the manual,
# "Message Formatting"). Every command ends with exactly one of these.
COMPLETION_OK = "OK!"
COMPLETION_ABORTED = "ABORTED!"
COMPLETION_ERROR = "ERROR!"
COMPLETION_TOKENS = (COMPLETION_OK, COMPLETION_ABORTED, COMPLETION_ERROR)

# Immediate command-receipt echo prefix.
ACK_TOKEN = "ACK!"

# Intermediate data line emitted by ``pick`` once the plate is clear of the nest.
PLATE_AVAILABLE = "PLATE_AVAILABLE"
