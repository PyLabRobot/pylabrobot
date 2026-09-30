"""Low-level wire-protocol constants and parsing helpers."""

from typing import Dict, List

# Completion-status tokens that terminate a command's reply (see the manual,
# "Message Formatting"). Every command ends with exactly one of these.
COMPLETION_OK = "OK!"
COMPLETION_ABORTED = "ABORTED!"
COMPLETION_ERROR = "ERROR!"
COMPLETION_TOKENS = (COMPLETION_OK, COMPLETION_ABORTED, COMPLETION_ERROR)

# Immediate command-receipt echo prefix.
ACK_TOKEN = "ACK!"


def parse_kv(lines: List[str]) -> Dict[str, str]:
  """Parse ``Key: value`` lines into a dict (first colon splits)."""
  out: Dict[str, str] = {}
  for line in lines:
    if ":" in line:
      key, _, value = line.partition(":")
      out[key.strip()] = value.strip()
  return out
