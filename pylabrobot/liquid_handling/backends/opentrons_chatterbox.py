"""A chatterbox backend for the Opentrons OT-2.

Dry-runs the real OpentronsOT2Backend without hardware or the ``ot_api`` library
by swapping the backend's transport handle (``self._ot``) for a recorder that
logs every call and returns canned data for the few reads the backend makes back.

This mirrors how ``STARChatterboxBackend`` dry-runs ``STARBackend``: only the
transport is replaced, so all the real high-level logic (pipette selection, tip
and volume bookkeeping, the per-operation wire calls) runs unchanged. Contrast
with ``OpentronsOT2Simulator``, which overrides the high-level methods themselves.
"""

import logging
from typing import Dict, List, Optional, Tuple, cast

from pylabrobot.io import LOG_LEVEL_IO
from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.liquid_handling.backends.opentrons_backend import (
  _OT_DECK_IS_ADDRESSABLE_AREA_VERSION,
  OpentronsOT2Backend,
)

logger = logging.getLogger(__name__)


class _RecordingNamespace:
  """An ot_api sub-namespace (e.g. ``lh``, ``health``) that records every call.

  Unknown attributes resolve to a function that appends ``(name, args, kwargs)``
  to the shared recorder and returns the canned value registered in ``returns``
  (``None`` if none is registered).
  """

  def __init__(self, recorder: "_OTChatterboxModule", prefix: str, returns=None):
    self._recorder = recorder
    self._prefix = prefix
    self._returns = returns or {}

  def __getattr__(self, name: str):
    if name.startswith("_"):
      raise AttributeError(name)
    qualified = f"{self._prefix}.{name}"
    returns = self._returns
    recorder = self._recorder

    def _record(*args, **kwargs):
      recorder.log(qualified, args, kwargs)
      canned = returns.get(name)
      return canned() if callable(canned) else canned

    return _record


class _OTChatterboxModule:
  """Stand-in for the ``ot_api`` module that records calls instead of issuing them.

  Provides the sub-namespaces and reads the real backend touches: ``runs.create``,
  ``lh.add_mounted_pipettes``, ``health.get``, ``labware.define``,
  ``modules.list_connected_modules`` and ``lh.save_position`` return canned data;
  everything else is recorded and returns ``None``. ``run_id`` stays ``None`` so
  ``stop()`` skips the cancel request.
  """

  def __init__(self, left_pipette, right_pipette, api_version: str, verbose: bool = True):
    self.calls: List[Tuple[str, tuple, dict]] = []
    self.run_id: Optional[str] = None
    self._verbose = verbose

    self.runs = _RecordingNamespace(self, "runs", {"create": lambda: "chatterbox-run"})
    self.health = _RecordingNamespace(self, "health", {"get": lambda: {"api_version": api_version}})
    self.labware = _RecordingNamespace(
      self, "labware", {"define": lambda: {"data": {"definitionUri": "pylabrobot/chatterbox/1"}}}
    )
    self.modules = _RecordingNamespace(self, "modules", {"list_connected_modules": lambda: []})
    self.requestor = _RecordingNamespace(self, "requestor")
    self.lh = _RecordingNamespace(
      self,
      "lh",
      {
        "add_mounted_pipettes": lambda: (left_pipette, right_pipette),
        "save_position": lambda: {"data": {"result": {"position": {"x": 0, "y": 0, "z": 0}}}},
      },
    )

  def log(self, qualified: str, args: tuple, kwargs: dict):
    self.calls.append((qualified, args, kwargs))
    parts = [repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()]
    rendered = f"{qualified}({', '.join(parts)})"
    # log at LOG_LEVEL_IO so a dry run captures the same wire trace as a real run
    logger.log(LOG_LEVEL_IO, "%s", rendered)
    if self._verbose:
      print(rendered)

  def __getattr__(self, name: str):
    # top-level functions the backend calls directly: set_host, set_port, set_run
    if name.startswith("_"):
      raise AttributeError(name)

    def _record(*args, **kwargs):
      self.log(name, args, kwargs)
      return None

    return _record


class OpentronsOT2ChatterboxBackend(OpentronsOT2Backend):
  """Chatterbox backend for the Opentrons OT-2.

  Runs the real OpentronsOT2Backend logic with its transport replaced by a
  recorder - no hardware and no ``ot_api`` library required. Every issued call is
  printed and collected in :attr:`commands`.

  Example:
    >>> from pylabrobot.liquid_handling import LiquidHandler
    >>> from pylabrobot.liquid_handling.backends import OpentronsOT2ChatterboxBackend
    >>> from pylabrobot.resources.opentrons import OTDeck
    >>> lh = LiquidHandler(backend=OpentronsOT2ChatterboxBackend(), deck=OTDeck())
    >>> await lh.setup()
  """

  def __init__(
    self,
    left_pipette_name: Optional[str] = "p300_single_gen2",
    right_pipette_name: Optional[str] = "p20_single_gen2",
    host: str = "chatterbox",
    port: int = 31950,
    api_version: str = _OT_DECK_IS_ADDRESSABLE_AREA_VERSION,
    verbose: bool = True,
  ):
    """Initialize the chatterbox.

    Args:
      left_pipette_name: pipette mounted on the left (``None`` for none).
      right_pipette_name: pipette mounted on the right (``None`` for none).
      api_version: reported Opentrons API version; defaults to the version at
        which tip drops route through the addressable-area trash.
      verbose: if True, print every recorded call.
    """
    # Skip OpentronsOT2Backend.__init__ (it requires ot_api); set up state directly.
    LiquidHandlerBackend.__init__(self)

    pv = OpentronsOT2Backend.pipette_name2volume
    if left_pipette_name is not None and left_pipette_name not in pv:
      raise ValueError(f"Unknown left pipette: {left_pipette_name}")
    if right_pipette_name is not None and right_pipette_name not in pv:
      raise ValueError(f"Unknown right pipette: {right_pipette_name}")

    self._left_pipette_name = left_pipette_name
    self._right_pipette_name = right_pipette_name
    self.host = host
    self.port = port

    left = (
      {"name": left_pipette_name, "pipetteId": "chatterbox-left"} if left_pipette_name else None
    )
    right = (
      {"name": right_pipette_name, "pipetteId": "chatterbox-right"} if right_pipette_name else None
    )
    self._ot = _OTChatterboxModule(left, right, api_version, verbose=verbose)

    self.ot_api_version: Optional[str] = None
    self.left_pipette: Optional[Dict[str, str]] = None
    self.right_pipette: Optional[Dict[str, str]] = None
    self.traversal_height = 120
    self._tip_racks: Dict[str, int] = {}
    self._plr_name_to_load_name: Dict[str, str] = {}

  @property
  def commands(self) -> List[Tuple[str, tuple, dict]]:
    """Recorded ``(qualified_name, args, kwargs)`` for every call issued so far."""
    return cast(List[Tuple[str, tuple, dict]], self._ot.calls)

  def serialize(self) -> dict:
    return {
      **LiquidHandlerBackend.serialize(self),
      "left_pipette_name": self._left_pipette_name,
      "right_pipette_name": self._right_pipette_name,
      "host": self.host,
      "port": self.port,
    }
