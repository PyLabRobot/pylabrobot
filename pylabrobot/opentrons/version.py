"""Robot software version comparisons."""

import re
from typing import Tuple

OFFLINE_API_VERSION = "dry-run"


def version_tuple(version: str) -> Tuple[int, ...]:
  """Parse a robot software version into comparable numeric components."""
  parts = []
  for part in version.split("."):
    match = re.match(r"\d+", part)
    if match is None:
      break
    parts.append(int(match.group()))
  if not parts:
    raise ValueError(f"Unrecognized robot software version {version!r}")
  return tuple(parts)


def version_at_least(version: str, required: str) -> bool:
  """Compare versions with missing components treated as zero."""
  actual, minimum = version_tuple(version), version_tuple(required)
  width = max(len(actual), len(minimum))
  return actual + (0,) * (width - len(actual)) >= minimum + (0,) * (width - len(minimum))
