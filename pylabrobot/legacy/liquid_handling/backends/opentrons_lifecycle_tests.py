"""Software-only lifecycle tests for the legacy Opentrons OT-2 HTTP backend."""

import threading
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.liquid_handling import LiquidHandler
from pylabrobot.legacy.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.legacy.liquid_handling.backends.opentrons_backend import OpentronsOT2Backend
from pylabrobot.legacy.liquid_handling.standard import (
  Drop,
  Mix,
  Pickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources import Container, Coordinate, Resource, Tip
from pylabrobot.resources.celltreat import celltreat_96_wellplate_350uL_Fb
from pylabrobot.resources.opentrons import OTDeck, opentrons_96_filtertiprack_20ul
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.testing.concurrency import AnyioTestBase

Call = Tuple[str, tuple, dict]


class RecordingProtocolDict(dict[str, Any]):
  """Record where a protocol-shaped response mapping is traversed."""

  def __init__(self, api: "RecordingOTAPI", name: str, values: Dict[str, Any]) -> None:
    super().__init__(values)
    self.api = api
    self.name = name

  def __getitem__(self, key: str) -> Any:
    self.api.state_threads.append((f"protocol.{self.name}.{key}", threading.get_ident()))
    return super().__getitem__(key)


class RecordingRuns:
  """Record run creation calls."""

  def __init__(self, api: "RecordingOTAPI") -> None:
    self.api = api
    self.created = 0

  def create(self) -> str:
    """Return a distinct run identifier after recording the call."""
    self.api.record("runs.create", (), {})
    self.created += 1
    return f"run-{self.created}"


class RecordingHealth:
  """Record health and homing calls."""

  def __init__(self, api: "RecordingOTAPI") -> None:
    self.api = api

  def get(self) -> Dict[str, str]:
    """Return the configured API version."""
    self.api.record("health.get", (), {})
    return {"api_version": "7.1.0"}

  def home(self) -> None:
    """Record robot homing."""
    self.api.record("health.home", (), {})


class RecordingLiquidHandling:
  """Record liquid-handling calls with protocol-shaped return values."""

  def __init__(self, api: "RecordingOTAPI") -> None:
    self.api = api

  def add_mounted_pipettes(
    self,
  ) -> Tuple[Optional[Dict[str, str]], Optional[Dict[str, str]]]:
    """Return the two configured pipettes."""
    self.api.record("lh.add_mounted_pipettes", (), {})
    return (
      {"name": "p300_single_gen2", "pipetteId": "left-id"},
      {"name": "p20_single_gen2", "pipetteId": "right-id"},
    )

  def pick_up_tip(self, **kwargs: Any) -> None:
    """Record a tip pickup."""
    self.api.record("lh.pick_up_tip", (), kwargs)

  def move_to_addressable_area_for_drop_tip(self, **kwargs: Any) -> None:
    """Record movement to fixed trash."""
    self.api.record("lh.move_to_addressable_area_for_drop_tip", (), kwargs)

  def drop_tip_in_place(self, **kwargs: Any) -> None:
    """Record an in-place tip drop."""
    self.api.record("lh.drop_tip_in_place", (), kwargs)

  def drop_tip(self, *args: Any, **kwargs: Any) -> None:
    """Record a labware-relative tip drop."""
    self.api.record("lh.drop_tip", args, kwargs)

  def aspirate_in_place(self, **kwargs: Any) -> None:
    """Record an aspiration."""
    self.api.record("lh.aspirate_in_place", (), kwargs)

  def dispense_in_place(self, **kwargs: Any) -> None:
    """Record a dispense."""
    self.api.record("lh.dispense_in_place", (), kwargs)

  def move_arm(self, **kwargs: Any) -> None:
    """Record an arm movement."""
    self.api.record("lh.move_arm", (), kwargs)

  def save_position(self, **kwargs: Any) -> Dict[str, Any]:
    """Return a protocol-shaped current position."""
    self.api.record("lh.save_position", (), kwargs)
    position = RecordingPosition(self.api, {"x": 1, "y": 2, "z": 3})
    result = RecordingProtocolDict(self.api, "position_result", {"position": position})
    data = RecordingProtocolDict(self.api, "position_data", {"result": result})
    return RecordingProtocolDict(self.api, "position_response", {"data": data})


class RecordingPosition(dict[str, int]):
  """Record where a synchronous position response is interpreted."""

  def __init__(self, api: "RecordingOTAPI", position: Dict[str, int]) -> None:
    super().__init__(position)
    self.api = api

  def __getitem__(self, key: str) -> int:
    self.api.state_threads.append((f"position.{key}", threading.get_ident()))
    return super().__getitem__(key)


class RecordingLabware:
  """Record labware definition and loading calls."""

  def __init__(self, api: "RecordingOTAPI") -> None:
    self.api = api

  def define(self, definition: dict) -> Dict[str, Any]:
    """Return a protocol-shaped definition URI."""
    self.api.record("labware.define", (definition,), {})
    data = RecordingProtocolDict(
      self.api, "labware_data", {"definitionUri": "pylabrobot/recording/1"}
    )
    return RecordingProtocolDict(self.api, "labware_response", {"data": data})

  def add(self, **kwargs: Any) -> None:
    """Record labware loading."""
    self.api.record("labware.add", (), kwargs)


class RecordingModules:
  """Record module discovery calls."""

  def __init__(self, api: "RecordingOTAPI") -> None:
    self.api = api

  def list_connected_modules(self) -> List[dict]:
    """Return no connected modules."""
    self.api.record("modules.list_connected_modules", (), {})
    return []


class RecordingRequestor:
  """Record direct HTTP fallback requests."""

  def __init__(self, api: "RecordingOTAPI") -> None:
    self.api = api

  def post(self, path: str) -> None:
    """Record a POST request."""
    self.api.record("requestor.post", (path,), {})
    failure = self.api.request_failures.get(("POST", path))
    if failure is not None:
      raise failure

  def delete(self, path: str) -> None:
    """Record a DELETE request."""
    self.api.record("requestor.delete", (path,), {})
    failure = self.api.request_failures.get(("DELETE", path))
    if failure is not None:
      raise failure


class RecordingOTAPI:
  """Typed, thread-safe recording double for the synchronous ``ot_api`` surface."""

  def __init__(self) -> None:
    self.calls: List[Call] = []
    self.call_threads: List[Tuple[str, int]] = []
    self.state_threads: List[Tuple[str, int]] = []
    self.failures: Dict[str, BaseException] = {}
    self.request_failures: Dict[Tuple[str, str], BaseException] = {}
    self.block_at: Optional[str] = None
    self.block_started = threading.Event()
    self.block_release = threading.Event()
    self.block_timed_out = False
    self.run_id: Optional[str] = None
    self.cancel_on_set_run: Optional[anyio.CancelScope] = None
    self.cancellation_requested = threading.Event()
    self.cleanup_cancel_states: List[bool] = []
    self._lock = threading.Lock()

    self.runs = RecordingRuns(self)
    self.health = RecordingHealth(self)
    self.lh = RecordingLiquidHandling(self)
    self.labware = RecordingLabware(self)
    self.modules = RecordingModules(self)
    self.requestor = RecordingRequestor(self)

  def record(self, name: str, args: tuple, kwargs: dict) -> None:
    """Record one call, optionally block it, then expose its configured failure."""
    with self._lock:
      self.calls.append((name, args, kwargs))
      self.call_threads.append((name, threading.get_ident()))
    if name.startswith("requestor."):
      self.cleanup_cancel_states.append(self.cancellation_requested.is_set())
    if name == self.block_at:
      self.block_started.set()
      if not self.block_release.wait(timeout=1):
        self.block_timed_out = True
    failure = self.failures.get(name)
    if failure is not None:
      raise failure

  def set_run(self, run_id: str) -> None:
    """Select a run through dependency-local state only."""
    self.record("set_run", (run_id,), {})
    self.run_id = run_id
    if self.cancel_on_set_run is not None:
      self.cancellation_requested.set()
      self.cancel_on_set_run.cancel()


class RecordingTipRacks(dict[str, int]):
  """Record where successful labware assignment is published."""

  def __init__(self, api: RecordingOTAPI) -> None:
    super().__init__()
    self.api = api

  def __setitem__(self, name: str, slot: int) -> None:
    self.api.state_threads.append((f"tip_rack.{name}", threading.get_ident()))
    super().__setitem__(name, slot)


class RecordingOTDeck(OTDeck):
  """Record where OT deck geometry and slot assignment are inspected."""

  def __init__(self, api: RecordingOTAPI) -> None:
    self.api = api
    super().__init__()

  @property
  def slot_locations(self) -> List[Coordinate]:
    """Return slot geometry after recording its access thread."""
    self.api.state_threads.append(("deck.slot_locations", threading.get_ident()))
    return super().slot_locations

  def get_slot(self, resource: Resource) -> Optional[int]:
    """Return a resource slot after recording its access thread."""
    self.api.state_threads.append(("deck.get_slot", threading.get_ident()))
    return super().get_slot(resource)


class RecordingPickupOperation:
  """Typed pickup operation whose property reads record their thread."""

  def __init__(
    self,
    api: RecordingOTAPI,
    resource: TipSpot,
    offset: Coordinate,
    tip: Tip,
  ) -> None:
    self.api = api
    self._resource = resource
    self._offset = offset
    self._tip = tip

  @property
  def resource(self) -> TipSpot:
    self.api.state_threads.append(("operation.pickup.resource", threading.get_ident()))
    return self._resource

  @property
  def offset(self) -> Coordinate:
    self.api.state_threads.append(("operation.pickup.offset", threading.get_ident()))
    return self._offset

  @property
  def tip(self) -> Tip:
    self.api.state_threads.append(("operation.pickup.tip", threading.get_ident()))
    return self._tip


class RecordingDropOperation:
  """Typed drop operation whose property reads record their thread."""

  def __init__(
    self,
    api: RecordingOTAPI,
    resource: Resource,
    offset: Coordinate,
    tip: Tip,
  ) -> None:
    self.api = api
    self._resource = resource
    self._offset = offset
    self._tip = tip

  @property
  def resource(self) -> Resource:
    self.api.state_threads.append(("operation.drop.resource", threading.get_ident()))
    return self._resource

  @property
  def offset(self) -> Coordinate:
    self.api.state_threads.append(("operation.drop.offset", threading.get_ident()))
    return self._offset

  @property
  def tip(self) -> Tip:
    self.api.state_threads.append(("operation.drop.tip", threading.get_ident()))
    return self._tip


class RecordingContainer:
  """Typed container view that records geometry inspection."""

  def __init__(self, api: RecordingOTAPI, container: Container) -> None:
    self.api = api
    self.container = container

  def get_location_wrt(
    self,
    other: Resource,
    x: str = "l",
    y: str = "f",
    z: str = "b",
  ) -> Coordinate:
    """Delegate geometry lookup after recording its access thread."""
    self.api.state_threads.append(("resource.get_location_wrt", threading.get_ident()))
    return self.container.get_location_wrt(other, x, y, z)


class RecordingMix:
  """Typed mix protocol whose property reads record their thread."""

  def __init__(self, api: RecordingOTAPI) -> None:
    self.api = api

  @property
  def volume(self) -> float:
    self.api.state_threads.append(("mix.volume", threading.get_ident()))
    return 5

  @property
  def repetitions(self) -> int:
    self.api.state_threads.append(("mix.repetitions", threading.get_ident()))
    return 1

  @property
  def flow_rate(self) -> float:
    self.api.state_threads.append(("mix.flow_rate", threading.get_ident()))
    return 6


class RecordingLiquidOperation:
  """Typed aspiration/dispense operation whose reads record their thread."""

  def __init__(
    self,
    api: RecordingOTAPI,
    name: str,
    resource: Container,
    tip: Tip,
  ) -> None:
    self.api = api
    self.name = name
    self._resource = resource
    self._tip = tip
    self._mix = cast(Mix, RecordingMix(api))

  def _record(self, field: str) -> None:
    self.api.state_threads.append((f"operation.{self.name}.{field}", threading.get_ident()))

  @property
  def resource(self) -> Container:
    self._record("resource")
    return self._resource

  @property
  def offset(self) -> Coordinate:
    self._record("offset")
    return Coordinate(1, 2, 3)

  @property
  def tip(self) -> Tip:
    self._record("tip")
    return self._tip

  @property
  def volume(self) -> float:
    self._record("volume")
    return 7

  @property
  def flow_rate(self) -> Optional[float]:
    self._record("flow_rate")
    return 8

  @property
  def liquid_height(self) -> Optional[float]:
    self._record("liquid_height")
    return 9

  @property
  def mix(self) -> Optional[Mix]:
    self._record("mix")
    return self._mix


class RecordingOpentronsBackend(OpentronsOT2Backend):
  """Construct an OT-2 backend around the recording API without optional dependencies."""

  def __init__(self, api: RecordingOTAPI) -> None:
    LiquidHandlerBackend.__init__(self)
    self.host = "recording"
    self.port = 31950
    self._ot = api
    self.publication_threads: List[Tuple[str, int]] = []
    self._left_pipette: Optional[Dict[str, str]] = None
    self._right_pipette: Optional[Dict[str, str]] = None
    self._recorded_api_version: Optional[str] = None
    self.left_pipette_has_tip = False
    self.right_pipette_has_tip = False
    self.traversal_height = 120
    self._tip_racks: Dict[str, int] = RecordingTipRacks(api)
    self._plr_name_to_load_name: Dict[str, str] = {}

  def get_ot_name(self, plr_resource_name: str) -> str:
    """Record where resource names are resolved and cached."""
    self._ot.state_threads.append((f"get_ot_name.{plr_resource_name}", threading.get_ident()))
    return super().get_ot_name(plr_resource_name)

  def _set_tip_state(self, pipette_id: str, has_tip: bool) -> None:
    """Record where successful tip operations publish local state."""
    self._ot.state_threads.append(("set_tip_state", threading.get_ident()))
    super()._set_tip_state(pipette_id, has_tip)

  def _deck_to_robot_frame(self, location: Coordinate) -> Coordinate:
    """Record where operation coordinates are interpreted."""
    self._ot.state_threads.append(("deck_to_robot_frame", threading.get_ident()))
    return super()._deck_to_robot_frame(location)

  @property
  def left_pipette(self) -> Optional[Dict[str, str]]:
    """Return the published left pipette."""
    return self._left_pipette

  @left_pipette.setter
  def left_pipette(self, value: Optional[Dict[str, str]]) -> None:
    self.publication_threads.append(("left_pipette", threading.get_ident()))
    self._left_pipette = value

  @property
  def right_pipette(self) -> Optional[Dict[str, str]]:
    """Return the published right pipette."""
    return self._right_pipette

  @right_pipette.setter
  def right_pipette(self, value: Optional[Dict[str, str]]) -> None:
    self.publication_threads.append(("right_pipette", threading.get_ident()))
    self._right_pipette = value

  @property
  def ot_api_version(self) -> Optional[str]:
    """Return the published API version."""
    return self._recorded_api_version

  @ot_api_version.setter
  def ot_api_version(self, value: Optional[str]) -> None:
    self.publication_threads.append(("ot_api_version", threading.get_ident()))
    self._recorded_api_version = value


def make_liquid_handler() -> Tuple[LiquidHandler, RecordingOpentronsBackend, RecordingOTAPI]:
  """Build a software-only OT-2 composition."""
  api = RecordingOTAPI()
  backend = RecordingOpentronsBackend(api)
  liquid_handler = LiquidHandler(backend=backend, deck=OTDeck())
  return liquid_handler, backend, api


def assert_local_state_cleared(backend: RecordingOpentronsBackend) -> None:
  """Assert all lifespan-local OT-2 state is unpublished."""
  assert backend._plr_name_to_load_name == {}
  assert backend._tip_racks == {}
  assert backend.left_pipette is None
  assert backend.right_pipette is None
  assert backend.left_pipette_has_tip is False
  assert backend.right_pipette_has_tip is False
  assert backend.ot_api_version is None


def exception_chain(error: BaseException) -> List[BaseException]:
  """Return one exception and its non-cyclic context chain."""
  errors: List[BaseException] = []
  seen: set[int] = set()
  current: Optional[BaseException] = error
  while current is not None and id(current) not in seen:
    seen.add(id(current))
    errors.append(current)
    current = current.__context__
  return errors


class TestOpentronsLifecycle(AnyioTestBase):
  """Characterize OT-2 setup, rollback, cancellation, and worker behavior."""

  async def test_native_entry_preserves_setup_transcript(self) -> None:
    liquid_handler, backend, api = make_liquid_handler()

    async with liquid_handler:
      assert api.calls == [
        ("runs.create", (), {}),
        ("set_run", ("run-1",), {}),
        ("lh.add_mounted_pipettes", (), {}),
        ("health.get", (), {}),
        ("health.home", (), {}),
      ]
      assert backend.left_pipette == {
        "name": "p300_single_gen2",
        "pipetteId": "left-id",
      }
      assert backend.right_pipette == {
        "name": "p20_single_gen2",
        "pipetteId": "right-id",
      }
      assert backend.ot_api_version == "7.1.0"

    assert api.calls[-1] == ("requestor.post", ("/runs/run-1/cancel",), {})
    assert_local_state_cleared(backend)

  async def test_skip_home_preserves_setup_transcript_without_home(self) -> None:
    liquid_handler, backend, api = make_liquid_handler()

    async with liquid_handler.lifespan(skip_home=True):
      assert api.calls == [
        ("runs.create", (), {}),
        ("set_run", ("run-1",), {}),
        ("lh.add_mounted_pipettes", (), {}),
        ("health.get", (), {}),
      ]

    assert_local_state_cleared(backend)

  async def test_skip_home_cancellation_during_health_lookup_rolls_back(self) -> None:
    liquid_handler, backend, api = make_liquid_handler()
    api.block_at = "health.get"
    scopes: List[anyio.CancelScope] = []
    scope_ready = anyio.Event()
    finished = anyio.Event()
    body_reached = False
    observed_cancellation: List[BaseException] = []

    async def enter() -> None:
      nonlocal body_reached
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        scope_ready.set()
        try:
          async with liquid_handler.lifespan(skip_home=True):
            body_reached = True
        except anyio.get_cancelled_exc_class() as error:
          observed_cancellation.append(error)
          raise
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      await scope_ready.wait()
      await anyio.to_thread.run_sync(api.block_started.wait)
      api.cancellation_requested.set()
      scopes[0].cancel()
      api.block_release.set()
      await finished.wait()

    assert not body_reached
    assert len(observed_cancellation) == 1
    assert api.calls[-1] == ("requestor.post", ("/runs/run-1/cancel",), {})
    assert api.cleanup_cancel_states == [True]
    assert not api.block_timed_out
    assert_local_state_cleared(backend)

  async def test_unknown_option_fails_before_run_creation(self) -> None:
    liquid_handler, _, api = make_liquid_handler()

    with pytest.raises(TypeError, match="unexpected keyword argument 'unknown'"):
      async with liquid_handler.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert api.calls == []

  async def test_missing_liquid_handler_context_fails_before_run_creation(self) -> None:
    api = RecordingOTAPI()
    backend = RecordingOpentronsBackend(api)

    with pytest.raises(RuntimeError, match="^Deck not set$"):
      async with backend:
        pytest.fail("invalid entry reached the body")

    assert api.calls == []

  @pytest.mark.parametrize(
    "phase",
    ["set_run", "lh.add_mounted_pipettes", "health.get", "health.home"],
  )
  async def test_partial_entry_failure_cancels_run_and_clears_state(self, phase: str) -> None:
    liquid_handler, backend, api = make_liquid_handler()
    failure = RuntimeError(f"failed at {phase}")
    api.failures[phase] = failure
    backend._plr_name_to_load_name["plate"] = "load-name"
    backend._tip_racks["tips"] = 1

    with pytest.raises(RuntimeError) as error_info:
      async with liquid_handler:
        pytest.fail("failed entry reached the body")

    assert error_info.value is failure
    assert api.calls[-1] == ("requestor.post", ("/runs/run-1/cancel",), {})
    assert_local_state_cleared(backend)

  @pytest.mark.parametrize(
    "phase",
    ["runs.create", "set_run", "lh.add_mounted_pipettes", "health.get", "health.home"],
  )
  async def test_cancellation_during_each_setup_phase_rolls_back(self, phase: str) -> None:
    liquid_handler, backend, api = make_liquid_handler()
    scopes: List[anyio.CancelScope] = []
    scope_ready = anyio.Event()
    finished = anyio.Event()
    observed_cancellation: List[BaseException] = []

    if phase != "set_run":
      api.block_at = phase

    async def enter() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        if phase == "set_run":
          api.cancel_on_set_run = scope
        scope_ready.set()
        try:
          async with liquid_handler:
            pytest.fail("cancelled entry reached the body")
        except anyio.get_cancelled_exc_class() as error:
          observed_cancellation.append(error)
          raise
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      await scope_ready.wait()
      if phase != "set_run":
        await anyio.to_thread.run_sync(api.block_started.wait)
        api.cancellation_requested.set()
        scopes[0].cancel()
        api.block_release.set()
      await finished.wait()

    assert len(observed_cancellation) == 1
    assert api.calls[-1] == ("requestor.post", ("/runs/run-1/cancel",), {})
    assert api.cleanup_cancel_states == [True]
    assert not api.block_timed_out
    assert_local_state_cleared(backend)

  async def test_body_cancellation_attempts_cleanup_while_cancelled(self) -> None:
    liquid_handler, backend, api = make_liquid_handler()
    body_started = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    observed_cancellation: List[BaseException] = []

    async def run_body() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with liquid_handler:
            body_started.set()
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class() as error:
          observed_cancellation.append(error)
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_body)
      await body_started.wait()
      api.cancellation_requested.set()
      scopes[0].cancel()

    assert len(observed_cancellation) == 1
    assert api.cleanup_cancel_states == [True]
    assert api.calls[-1] == ("requestor.post", ("/runs/run-1/cancel",), {})
    assert_local_state_cleared(backend)

  async def test_operational_cancellation_reconciles_worker_before_cleanup(self) -> None:
    liquid_handler, backend, api = make_liquid_handler()
    scopes: List[anyio.CancelScope] = []
    body_started = anyio.Event()
    observer_progressed = anyio.Event()
    finished = anyio.Event()

    async def run_body() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with liquid_handler:
          api.block_at = "health.home"
          body_started.set()
          await backend.home()
      finished.set()

    async def observe() -> None:
      await anyio.lowlevel.checkpoint()
      observer_progressed.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_body)
      await body_started.wait()
      assert await anyio.to_thread.run_sync(api.block_started.wait, 2)
      scopes[0].cancel()
      task_group.start_soon(observe)
      await observer_progressed.wait()
      assert not any(name.startswith("requestor.") for name, _, _ in api.calls)
      api.block_release.set()
      await finished.wait()

    assert not api.block_timed_out
    assert api.calls[-2:] == [
      ("health.home", (), {}),
      ("requestor.post", ("/runs/run-1/cancel",), {}),
    ]
    assert_local_state_cleared(backend)

  @pytest.mark.parametrize(
    ("failed_endpoints", "expected_cleanup"),
    [
      ([], [("requestor.post", ("/runs/run-1/cancel",), {})]),
      (
        [("POST", "/runs/run-1/cancel")],
        [
          ("requestor.post", ("/runs/run-1/cancel",), {}),
          ("requestor.post", ("/runs/run-1/actions/cancel",), {}),
        ],
      ),
      (
        [
          ("POST", "/runs/run-1/cancel"),
          ("POST", "/runs/run-1/actions/cancel"),
        ],
        [
          ("requestor.post", ("/runs/run-1/cancel",), {}),
          ("requestor.post", ("/runs/run-1/actions/cancel",), {}),
          ("requestor.delete", ("/runs/run-1",), {}),
        ],
      ),
    ],
  )
  async def test_run_cancel_fallbacks_only_advance_after_failure(
    self,
    failed_endpoints: List[Tuple[str, str]],
    expected_cleanup: List[Call],
  ) -> None:
    liquid_handler, _, api = make_liquid_handler()
    first_post = RuntimeError("first cancel failed")
    second_post = RuntimeError("second cancel failed")

    if ("POST", "/runs/run-1/cancel") in failed_endpoints:
      api.request_failures[("POST", "/runs/run-1/cancel")] = first_post
    if ("POST", "/runs/run-1/actions/cancel") in failed_endpoints:
      api.request_failures[("POST", "/runs/run-1/actions/cancel")] = second_post

    async with liquid_handler:
      pass

    assert api.calls[-len(expected_cleanup) :] == expected_cleanup

  async def test_all_run_cancel_fallback_failures_preserve_context_chain(self) -> None:
    liquid_handler, backend, api = make_liquid_handler()
    first_post = RuntimeError("first cancel failed")
    second_post = RuntimeError("second cancel failed")
    delete = RuntimeError("delete failed")

    api.request_failures[("POST", "/runs/run-1/cancel")] = first_post
    api.request_failures[("POST", "/runs/run-1/actions/cancel")] = second_post
    api.request_failures[("DELETE", "/runs/run-1")] = delete

    with pytest.raises(RuntimeError) as error_info:
      async with liquid_handler:
        pass

    assert error_info.value is delete
    assert exception_chain(delete)[:3] == [delete, second_post, first_post]
    assert api.calls[-3:] == [
      ("requestor.post", ("/runs/run-1/cancel",), {}),
      ("requestor.post", ("/runs/run-1/actions/cancel",), {}),
      ("requestor.delete", ("/runs/run-1",), {}),
    ]
    assert_local_state_cleared(backend)

  async def test_sequential_reuse_repeats_setup_and_cleanup(self) -> None:
    liquid_handler, backend, api = make_liquid_handler()

    async with liquid_handler:
      pass
    assert_local_state_cleared(backend)
    async with liquid_handler:
      pass
    assert_local_state_cleared(backend)

    first_cycle: List[Call] = [
      ("runs.create", (), {}),
      ("set_run", ("run-1",), {}),
      ("lh.add_mounted_pipettes", (), {}),
      ("health.get", (), {}),
      ("health.home", (), {}),
      ("requestor.post", ("/runs/run-1/cancel",), {}),
    ]
    second_cycle: List[Call] = [
      ("runs.create", (), {}),
      ("set_run", ("run-2",), {}),
      ("lh.add_mounted_pipettes", (), {}),
      ("health.get", (), {}),
      ("health.home", (), {}),
      ("requestor.post", ("/runs/run-2/cancel",), {}),
    ]
    assert api.calls == first_cycle + second_cycle

  async def test_blocking_run_creation_keeps_async_thread_responsive(self) -> None:
    liquid_handler, backend, api = make_liquid_handler()
    api.block_at = "runs.create"
    progressed = anyio.Event()
    entered = anyio.Event()
    async_thread = threading.get_ident()

    async def enter() -> None:
      async with liquid_handler:
        entered.set()

    async def progress() -> None:
      await anyio.lowlevel.checkpoint()
      progressed.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      task_group.start_soon(progress)
      await anyio.to_thread.run_sync(api.block_started.wait)
      await progressed.wait()
      assert not api.block_timed_out
      api.block_release.set()
      await entered.wait()

    external_calls = {
      "runs.create",
      "lh.add_mounted_pipettes",
      "health.get",
      "health.home",
      "requestor.post",
    }
    assert all(
      thread_id != async_thread for name, thread_id in api.call_threads if name in external_calls
    )
    assert all(
      thread_id == async_thread for name, thread_id in api.call_threads if name == "set_run"
    )
    assert all(thread_id == async_thread for _, thread_id in backend.publication_threads)


class TestOpentronsOperations(AnyioTestBase):
  """Prove every operational HTTP call is isolated from async state."""

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    self.api = RecordingOTAPI()
    self.backend = RecordingOpentronsBackend(self.api)
    self.deck = RecordingOTDeck(self.api)
    self.liquid_handler = LiquidHandler(backend=self.backend, deck=self.deck)
    await stack.enter_async_context(self.liquid_handler.lifespan(skip_home=True))
    self.tip_rack = opentrons_96_filtertiprack_20ul(name="tip_rack")
    self.deck.assign_child_at_slot(self.tip_rack, slot=1)
    self.plate = celltreat_96_wellplate_350uL_Fb(name="plate")
    self.deck.assign_child_at_slot(self.plate, slot=11)
    self.async_thread = threading.get_ident()
    self.api.calls.clear()
    self.api.call_threads.clear()
    self.api.state_threads.clear()

  def _pickup(self) -> Pickup:
    tip_spot = self.tip_rack.get_item("A1")
    return cast(
      Pickup,
      RecordingPickupOperation(self.api, tip_spot, Coordinate(1, 2, 3), tip_spot.get_tip()),
    )

  def _drop(self, *, fixed_trash: bool = False) -> Drop:
    tip_spot = self.tip_rack.get_item("A1")
    resource = self.deck.get_trash_area() if fixed_trash else tip_spot
    return cast(
      Drop,
      RecordingDropOperation(self.api, resource, Coordinate(4, 5, 6), tip_spot.get_tip()),
    )

  def _aspiration(self) -> SingleChannelAspiration:
    resource = cast(
      Container,
      RecordingContainer(self.api, self.plate.get_well("A1")),
    )
    return cast(
      SingleChannelAspiration,
      RecordingLiquidOperation(
        self.api, "aspiration", resource, self.tip_rack.get_item("A1").get_tip()
      ),
    )

  def _dispense(self) -> SingleChannelDispense:
    resource = cast(
      Container,
      RecordingContainer(self.api, self.plate.get_well("A1")),
    )
    return cast(
      SingleChannelDispense,
      RecordingLiquidOperation(
        self.api, "dispense", resource, self.tip_rack.get_item("A1").get_tip()
      ),
    )

  def _operation_for(self, blocked_call: str) -> Callable[[], Awaitable[Any]]:
    if blocked_call in {"labware.define", "labware.add", "lh.pick_up_tip"}:
      return lambda: self.backend.pick_up_tips([self._pickup()], [0])
    if blocked_call == "lh.drop_tip":
      self.backend.right_pipette_has_tip = True
      self.backend._tip_racks["tip_rack"] = 1
      return lambda: self.backend.drop_tips([self._drop()], [0])
    if blocked_call in {
      "lh.move_to_addressable_area_for_drop_tip",
      "lh.drop_tip_in_place",
    }:
      self.backend.right_pipette_has_tip = True
      return lambda: self.backend.drop_tips([self._drop(fixed_trash=True)], [0])
    if blocked_call == "lh.move_arm":
      return lambda: self.backend.move_pipette_head(
        Coordinate(10, 20, 30),
        speed=40,
        minimum_z_height=50,
        pipette_id="right-id",
        force_direct=True,
      )
    if blocked_call == "lh.aspirate_in_place":
      self.backend.right_pipette_has_tip = True
      return lambda: self.backend.aspirate([self._aspiration()], [0])
    if blocked_call == "lh.dispense_in_place":
      self.backend.right_pipette_has_tip = True
      return lambda: self.backend.dispense([self._dispense()], [0])
    if blocked_call == "health.home":
      return self.backend.home
    if blocked_call == "modules.list_connected_modules":
      return self.backend.list_connected_modules
    if blocked_call == "lh.save_position":
      return lambda: self.backend.move_channel_x(0, 10)
    raise AssertionError(f"No operation for {blocked_call}")

  @pytest.mark.parametrize("operation", ["aspirate", "dispense"])
  async def test_cancelled_committed_liquid_action_retracts_before_return(
    self, operation: str
  ) -> None:
    self.backend.right_pipette_has_tip = True
    self.api.block_at = f"lh.{operation}_in_place"
    observed: List[str] = []

    async def run() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          if operation == "aspirate":
            await self.backend.aspirate([self._aspiration()], [0])
          else:
            await self.backend.dispense([self._dispense()], [0])
        except anyio.get_cancelled_exc_class():
          observed.append("cancelled")
          raise

    scopes: List[anyio.CancelScope] = []
    with anyio.fail_after(3):
      async with anyio.create_task_group() as group:
        group.start_soon(run)
        assert await anyio.to_thread.run_sync(self.api.block_started.wait, 2)
        scopes[0].cancel()
        self.api.block_release.set()

    assert not self.api.block_timed_out
    assert observed == ["cancelled"]
    assert [name for name, _, _ in self.api.calls][-2:] == [
      f"lh.{operation}_in_place",
      "lh.move_arm",
    ]

  @pytest.mark.parametrize(
    "blocked_call",
    [
      "labware.define",
      "labware.add",
      "lh.pick_up_tip",
      "lh.drop_tip",
      "lh.move_to_addressable_area_for_drop_tip",
      "lh.drop_tip_in_place",
      "lh.move_arm",
      "lh.aspirate_in_place",
      "lh.dispense_in_place",
      "health.home",
      "modules.list_connected_modules",
      "lh.save_position",
    ],
  )
  async def test_blocked_operational_call_keeps_async_thread_responsive(
    self, blocked_call: str
  ) -> None:
    """An inline external call would prevent the progress task from running before timeout."""
    self.api.block_at = blocked_call
    progressed = anyio.Event()
    finished = anyio.Event()

    async def operate() -> None:
      await self._operation_for(blocked_call)()
      finished.set()

    async def progress() -> None:
      await anyio.lowlevel.checkpoint()
      progressed.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(operate)
      assert await anyio.to_thread.run_sync(self.api.block_started.wait, 2)
      task_group.start_soon(progress)
      await progressed.wait()
      assert not self.api.block_timed_out
      self.api.block_release.set()
      await finished.wait()

    assert (
      next(thread_id for name, thread_id in self.api.call_threads if name == blocked_call)
      != self.async_thread
    )

  async def test_operations_preserve_arguments_order_and_async_state(self) -> None:
    """Wrong captures, reordered commands, or worker-side state access change this transcript."""
    self.backend._plr_name_to_load_name.update(
      {
        "tip_rack": "rack-id",
        "tip_rack_A1": "well-id",
      }
    )
    pickup = self._pickup()
    await self.backend.pick_up_tips([pickup], [0])
    await self.backend.drop_tips([self._drop()], [0])
    self.backend.right_pipette_has_tip = True
    await self.backend.drop_tips([self._drop(fixed_trash=True)], [0])
    self.backend.right_pipette_has_tip = True
    await self.backend.aspirate([self._aspiration()], [0])
    await self.backend.dispense([self._dispense()], [0])
    await self.backend.home()
    assert await self.backend.list_connected_modules() == []
    await self.backend.move_channel_x(0, 10)

    calls = self.api.calls
    definition = calls[0][1][0]
    assert definition["metadata"] == {
      "displayName": "rack-id",
      "displayCategory": "tipRack",
      "displayVolumeUnits": "µL",
    }
    assert definition["parameters"] == {
      "format": "96Standard",
      "isTiprack": True,
      "tipLength": 39.2,
      "tipOverlap": 3.29,
      "loadName": "rack-id",
      "isMagneticModuleCompatible": False,
    }
    assert definition["ordering"][0][0] == "well-id"
    assert definition["wells"]["well-id"] == {
      "depth": 0.0,
      "x": 14.360000000000001,
      "y": 74.26,
      "z": 25.49,
      "shape": "circular",
      "diameter": 2.312,
      "totalLiquidVolume": 20,
    }
    assert calls[1] == (
      "labware.add",
      (),
      {
        "load_name": "recording",
        "namespace": "pylabrobot",
        "ot_location": 1,
        "version": "1",
        "labware_id": "rack-id",
        "display_name": "rack-id",
      },
    )
    assert calls[2:] == [
      (
        "lh.pick_up_tip",
        (),
        {
          "labware_id": "rack-id",
          "well_name": "well-id",
          "pipette_id": "right-id",
          "offset_x": 1,
          "offset_y": 2,
          "offset_z": 42.2,
        },
      ),
      (
        "lh.drop_tip",
        ("rack-id",),
        {
          "well_name": "well-id",
          "pipette_id": "right-id",
          "offset_x": 4,
          "offset_y": 5,
          "offset_z": 16,
        },
      ),
      (
        "lh.move_to_addressable_area_for_drop_tip",
        (),
        {
          "pipette_id": "right-id",
          "offset_x": 4,
          "offset_y": 5,
          "offset_z": 16,
        },
      ),
      ("lh.drop_tip_in_place", (), {"pipette_id": "right-id"}),
      (
        "lh.move_arm",
        (),
        {
          "pipette_id": "right-id",
          "location_x": 147.81,
          "location_y": 347.65,
          "location_z": 17.8,
          "minimum_z_height": 120,
          "speed": None,
          "force_direct": False,
        },
      ),
      (
        "lh.aspirate_in_place",
        (),
        {"volume": 5, "flow_rate": 6, "pipette_id": "right-id"},
      ),
      (
        "lh.dispense_in_place",
        (),
        {"volume": 5, "flow_rate": 6, "pipette_id": "right-id"},
      ),
      (
        "lh.aspirate_in_place",
        (),
        {"volume": 7, "flow_rate": 8, "pipette_id": "right-id"},
      ),
      (
        "lh.move_arm",
        (),
        {
          "pipette_id": "right-id",
          "location_x": 147.81,
          "location_y": 347.65,
          "location_z": 120,
          "minimum_z_height": 120,
          "speed": None,
          "force_direct": False,
        },
      ),
      (
        "lh.move_arm",
        (),
        {
          "pipette_id": "right-id",
          "location_x": 147.81,
          "location_y": 347.65,
          "location_z": 17.8,
          "minimum_z_height": 120,
          "speed": None,
          "force_direct": False,
        },
      ),
      (
        "lh.dispense_in_place",
        (),
        {"volume": 7, "flow_rate": 8, "pipette_id": "right-id"},
      ),
      (
        "lh.aspirate_in_place",
        (),
        {"volume": 5, "flow_rate": 6, "pipette_id": "right-id"},
      ),
      (
        "lh.dispense_in_place",
        (),
        {"volume": 5, "flow_rate": 6, "pipette_id": "right-id"},
      ),
      (
        "lh.move_arm",
        (),
        {
          "pipette_id": "right-id",
          "location_x": 147.81,
          "location_y": 347.65,
          "location_z": 120,
          "minimum_z_height": 120,
          "speed": None,
          "force_direct": False,
        },
      ),
      ("health.home", (), {}),
      ("modules.list_connected_modules", (), {}),
      ("lh.save_position", (), {"pipette_id": "left-id"}),
      (
        "lh.move_arm",
        (),
        {
          "pipette_id": "left-id",
          "location_x": 10,
          "location_y": 2,
          "location_z": 3,
          "minimum_z_height": 120,
          "speed": None,
          "force_direct": False,
        },
      ),
    ]
    assert all(thread_id != self.async_thread for _, thread_id in self.api.call_threads)
    state_names = {name for name, _ in self.api.state_threads}
    assert {
      "get_ot_name.tip_rack",
      "tip_rack.tip_rack",
      "set_tip_state",
      "deck_to_robot_frame",
      "position.x",
      "position.y",
      "position.z",
      "operation.pickup.resource",
      "operation.pickup.offset",
      "operation.pickup.tip",
      "operation.drop.resource",
      "operation.drop.offset",
      "operation.drop.tip",
      "operation.aspiration.resource",
      "operation.aspiration.offset",
      "operation.aspiration.volume",
      "operation.aspiration.flow_rate",
      "operation.aspiration.liquid_height",
      "operation.aspiration.mix",
      "operation.dispense.resource",
      "operation.dispense.offset",
      "operation.dispense.volume",
      "operation.dispense.flow_rate",
      "operation.dispense.liquid_height",
      "operation.dispense.mix",
      "mix.volume",
      "mix.repetitions",
      "mix.flow_rate",
      "resource.get_location_wrt",
      "deck.get_slot",
      "deck.slot_locations",
      "protocol.labware_response.data",
      "protocol.labware_data.definitionUri",
      "protocol.position_response.data",
      "protocol.position_data.result",
      "protocol.position_result.position",
    } <= state_names
    off_thread_state = [
      name for name, thread_id in self.api.state_threads if thread_id != self.async_thread
    ]
    assert off_thread_state == []

  @pytest.mark.parametrize(
    ("failed_call", "initial_tip_state", "expected_tip_state", "tip_rack_published"),
    [
      ("labware.define", False, False, False),
      ("labware.add", False, False, False),
      ("lh.pick_up_tip", False, False, True),
      ("lh.drop_tip", True, True, True),
    ],
  )
  async def test_failure_preserves_identity_without_publishing_success(
    self,
    failed_call: str,
    initial_tip_state: bool,
    expected_tip_state: bool,
    tip_rack_published: bool,
  ) -> None:
    failure = RuntimeError(f"failed at {failed_call}")
    self.api.failures[failed_call] = failure
    self.backend.right_pipette_has_tip = initial_tip_state
    operation: Awaitable[None]
    if failed_call == "lh.drop_tip":
      self.backend._tip_racks["tip_rack"] = 1
      operation = self.backend.drop_tips([self._drop()], [0])
    else:
      operation = self.backend.pick_up_tips([self._pickup()], [0])

    with pytest.raises(RuntimeError) as error_info:
      await operation

    assert error_info.value is failure
    assert self.backend.right_pipette_has_tip is expected_tip_state
    assert ("tip_rack" in self.backend._tip_racks) is tip_rack_published
