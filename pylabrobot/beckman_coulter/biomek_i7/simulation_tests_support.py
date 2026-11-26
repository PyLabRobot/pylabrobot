"""What the package's tests build their simulated Biomek i7 from: the generic one in `defaults/`,
named explicitly (never through `$BIOMEK_I7_SIMULATOR_RECORDING`). Not a test module itself."""

import copy
import json
from typing import Any, Dict, List, Optional, Sequence

from pylabrobot.beckman_coulter.biomek_i7.defaults import GENERIC_RECORDING
from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.master import BiomekI7Driver
from pylabrobot.beckman_coulter.biomek_i7.driver.simulator import ReplayAceUSB

_GENERIC: Optional[Dict[str, Any]] = None


def generic_recording() -> Dict[str, Any]:
  """The generic cold-start recording, a fresh copy a test may change: `boards` maps each module
  (`chassis`, `multichannel`, `span8`) to its [request key, [replies]] pairs."""
  global _GENERIC
  if _GENERIC is None:
    with open(GENERIC_RECORDING, encoding="utf-8") as fh:
      _GENERIC = json.load(fh)
  return copy.deepcopy(_GENERIC)


def generic_boards(
  recording: Optional[Dict[str, Any]] = None,
  modules: Optional[Sequence[str]] = None,
  default_timeout: float = 1.0,
) -> List[AceBoard]:
  """A replayed board per module of `recording` (by default the generic one), as
  `simulator.replay_boards` builds them; `modules` picks and orders them."""
  data = recording if recording is not None else generic_recording()
  names = list(data["boards"]) if modules is None else list(modules)
  return [
    AceBoard(
      io=ReplayAceUSB(dict(data["boards"][module]), name=f"replayed {module}"),
      name=f"replayed {module}",
      default_timeout=default_timeout,
    )
    for module in names
  ]


def generic_driver(boards: Optional[Sequence[AceBoard]] = None) -> BiomekI7Driver:
  """A driver over `boards`, by default the generic instrument replayed."""
  return BiomekI7Driver(boards=generic_boards() if boards is None else boards)
