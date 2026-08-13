"""Opentrons' own data package, imported only where it is used.

``opentrons-shared-data`` ships the labware catalogue and the pipette
definitions the robot itself loads, so reading them beats vendoring numbers
that drift. It is an optional extra, and importing it at module scope would
make ``import pylabrobot.opentrons`` fail for anyone who installed PyLabRobot
without it.
"""

from importlib.util import find_spec


def has_shared_data() -> bool:
  """Whether Opentrons' data package is installed."""
  return find_spec("opentrons_shared_data") is not None


def require_shared_data() -> None:
  """Raise unless Opentrons' data package is importable."""
  if not has_shared_data():
    raise RuntimeError(
      "opentrons-shared-data is required for Opentrons labware and pipette data. "
      'Install with: pip install "pylabrobot[opentrons]"'
    )
