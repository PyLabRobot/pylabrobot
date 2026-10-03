"""Dry-run the real Opentrons OT-2 backend without hardware or ``ot_api``.

``OpentronsOT2ChatterboxBackend`` replaces the backend's ``self._ot`` transport
with an explicit recorder. It logs calls and supplies canned responses for the
reads the backend makes, while the real high-level logic for pipette selection,
tip and volume tracking, and operations runs unchanged. This mirrors
``STARChatterboxBackend``; ``OpentronsOT2Simulator`` instead overrides the
high-level operations themselves.
"""

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple, cast

from pylabrobot.io import LOG_LEVEL_IO
from pylabrobot.legacy.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.legacy.liquid_handling.backends.opentrons_backend import (
  _OT_DECK_IS_ADDRESSABLE_AREA_VERSION,
  OpentronsOT2Backend,
)

logger = logging.getLogger(__name__)

Call = Tuple[str, tuple, dict]
RequestFailure = Tuple[str, str]


class _RecordingRuns:
  """Record the run-creation API used by the OT-2 backend."""

  def __init__(self, recorder: "_OTChatterboxModule") -> None:
    self._recorder = recorder

  def create(self) -> str:
    """Record run creation and return the stable chatterbox run ID."""
    self._recorder.log("runs.create", (), {})
    return "chatterbox-run"


class _RecordingHealth:
  """Record health and homing calls."""

  def __init__(self, recorder: "_OTChatterboxModule", api_version: str) -> None:
    self._recorder = recorder
    self._api_version = api_version

  def get(self) -> Dict[str, str]:
    """Record a health read and return the configured API version."""
    self._recorder.log("health.get", (), {})
    return {"api_version": self._api_version}

  def home(self) -> None:
    """Record a simulated home request."""
    self._recorder.log("health.home", (), {})


class _RecordingLabware:
  """Record labware definition and loading calls."""

  def __init__(self, recorder: "_OTChatterboxModule") -> None:
    self._recorder = recorder

  def define(self, definition: dict) -> Dict[str, Any]:
    """Record a definition and return a canned definition URI."""
    self._recorder.log("labware.define", (definition,), {})
    return {"data": {"definitionUri": "pylabrobot/chatterbox/1"}}

  def add(self, **kwargs: Any) -> None:
    """Record one labware load."""
    self._recorder.log("labware.add", (), kwargs)


class _RecordingModules:
  """Record module discovery calls."""

  def __init__(self, recorder: "_OTChatterboxModule") -> None:
    self._recorder = recorder

  def list_connected_modules(self) -> List[dict]:
    """Record module discovery and return no attached modules."""
    self._recorder.log("modules.list_connected_modules", (), {})
    return []


class _RecordingRequestor:
  """Record direct request fallbacks with deterministic failure injection."""

  def __init__(self, recorder: "_OTChatterboxModule") -> None:
    self._recorder = recorder
    self.failures: Dict[RequestFailure, BaseException] = {}

  def post(self, path: str) -> None:
    """Record a POST and raise its configured chatterbox failure, if any."""
    self._recorder.log("requestor.post", (path,), {})
    failure = self.failures.get(("POST", path))
    if failure is not None:
      raise failure

  def delete(self, path: str) -> None:
    """Record a DELETE and raise its configured chatterbox failure, if any."""
    self._recorder.log("requestor.delete", (path,), {})
    failure = self.failures.get(("DELETE", path))
    if failure is not None:
      raise failure


class _RecordingLiquidHandling:
  """Record the explicit liquid-handling surface consumed by the parent backend."""

  def __init__(
    self,
    recorder: "_OTChatterboxModule",
    left_pipette: Optional[Dict[str, str]],
    right_pipette: Optional[Dict[str, str]],
  ) -> None:
    self._recorder = recorder
    self._left_pipette = left_pipette
    self._right_pipette = right_pipette

  def add_mounted_pipettes(
    self,
  ) -> Tuple[Optional[Dict[str, str]], Optional[Dict[str, str]]]:
    """Record pipette discovery and return the configured mounts."""
    self._recorder.log("lh.add_mounted_pipettes", (), {})
    return self._left_pipette, self._right_pipette

  def save_position(self, **kwargs: Any) -> Dict[str, Any]:
    """Record a position read and return the origin."""
    self._recorder.log("lh.save_position", (), kwargs)
    return {"data": {"result": {"position": {"x": 0, "y": 0, "z": 0}}}}

  def pick_up_tip(self, **kwargs: Any) -> None:
    """Record a tip pickup."""
    self._recorder.log("lh.pick_up_tip", (), kwargs)

  def move_to_addressable_area_for_drop_tip(self, **kwargs: Any) -> None:
    """Record movement to fixed trash."""
    self._recorder.log("lh.move_to_addressable_area_for_drop_tip", (), kwargs)

  def drop_tip_in_place(self, **kwargs: Any) -> None:
    """Record an in-place tip drop."""
    self._recorder.log("lh.drop_tip_in_place", (), kwargs)

  def drop_tip(self, *args: Any, **kwargs: Any) -> None:
    """Record a labware-relative tip drop."""
    self._recorder.log("lh.drop_tip", args, kwargs)

  def aspirate_in_place(self, **kwargs: Any) -> None:
    """Record an aspiration."""
    self._recorder.log("lh.aspirate_in_place", (), kwargs)

  def dispense_in_place(self, **kwargs: Any) -> None:
    """Record a dispense."""
    self._recorder.log("lh.dispense_in_place", (), kwargs)

  def move_arm(self, **kwargs: Any) -> None:
    """Record an arm movement."""
    self._recorder.log("lh.move_arm", (), kwargs)


class _OTChatterboxModule:
  """Explicit stand-in for the synchronous ``ot_api`` surface used by OT-2."""

  def __init__(
    self,
    left_pipette: Optional[Dict[str, str]],
    right_pipette: Optional[Dict[str, str]],
    api_version: str,
    verbose: bool = True,
  ) -> None:
    self.calls: List[Call] = []
    self.call_threads: List[int] = []
    self.run_id: Optional[str] = None
    self._verbose = verbose
    self._lock = threading.Lock()

    self.runs = _RecordingRuns(self)
    self.health = _RecordingHealth(self, api_version)
    self.labware = _RecordingLabware(self)
    self.modules = _RecordingModules(self)
    self.requestor = _RecordingRequestor(self)
    self.lh = _RecordingLiquidHandling(self, left_pipette, right_pipette)

  def log(self, qualified: str, args: tuple, kwargs: dict) -> None:
    """Record and emit one call using the standard OT-2 I/O trace format."""
    with self._lock:
      self.calls.append((qualified, args, kwargs))
      self.call_threads.append(threading.get_ident())
    parts = [repr(arg) for arg in args] + [f"{key}={value!r}" for key, value in kwargs.items()]
    rendered = f"{qualified}({', '.join(parts)})"
    logger.log(LOG_LEVEL_IO, "%s", rendered)
    if self._verbose:
      print(rendered)

  def set_host(self, host: str) -> None:
    """Record host configuration for compatibility with the real module."""
    self.log("set_host", (host,), {})

  def set_port(self, port: int) -> None:
    """Record port configuration for compatibility with the real module."""
    self.log("set_port", (port,), {})

  def set_run(self, run_id: str) -> None:
    """Record and publish the selected run ID."""
    self.log("set_run", (run_id,), {})
    self.run_id = run_id


class OpentronsOT2ChatterboxBackend(OpentronsOT2Backend):
  """Run OT-2 logic against a recorder without hardware or ``ot_api``."""

  def __init__(
    self,
    left_pipette_name: Optional[str] = "p300_single_gen2",
    right_pipette_name: Optional[str] = "p20_single_gen2",
    host: str = "chatterbox",
    port: int = 31950,
    api_version: str = _OT_DECK_IS_ADDRESSABLE_AREA_VERSION,
    verbose: bool = True,
  ) -> None:
    """Initialize the recorder and canned mounted-pipette responses."""
    LiquidHandlerBackend.__init__(self)

    pipette_volumes = OpentronsOT2Backend.pipette_name2volume
    if left_pipette_name is not None and left_pipette_name not in pipette_volumes:
      raise ValueError(f"Unknown left pipette: {left_pipette_name}")
    if right_pipette_name is not None and right_pipette_name not in pipette_volumes:
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
  def commands(self) -> List[Call]:
    """Return recorded ``(qualified_name, args, kwargs)`` calls."""
    return cast(List[Call], self._ot.calls)

  @property
  def command_threads(self) -> List[int]:
    """Return the thread identity that issued each recorded command."""
    return cast(List[int], self._ot.call_threads)

  @property
  def request_failures(self) -> Dict[RequestFailure, BaseException]:
    """Configure deterministic failures for recorder cleanup requests."""
    return cast(Dict[RequestFailure, BaseException], self._ot.requestor.failures)

  def serialize(self) -> dict:
    return {
      **LiquidHandlerBackend.serialize(self),
      "left_pipette_name": self._left_pipette_name,
      "right_pipette_name": self._right_pipette_name,
      "host": self.host,
      "port": self.port,
    }
