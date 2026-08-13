"""Which load names Opentrons' own labware catalogue defines.

A ``loadLabware`` command names labware by load name and the robot resolves it
against this catalogue, so a name that is not in it fails on the robot with no
client-side warning. Checking here turns that into an error the caller can read.
"""

from functools import lru_cache
from typing import FrozenSet

from pylabrobot.opentrons.shared_data import require_shared_data


@lru_cache(maxsize=1)
def catalogue_load_names() -> FrozenSet[str]:
  """Every load name the shipped catalogue defines, across schema versions."""
  require_shared_data()
  from opentrons_shared_data.labware import list_definitions

  return frozenset(load_name for load_name, _version, _schema in list_definitions())


def is_catalogue_labware(load_name: str) -> bool:
  """Whether Opentrons ships a definition under this load name."""
  return load_name in catalogue_load_names()
