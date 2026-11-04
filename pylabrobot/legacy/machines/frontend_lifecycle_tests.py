from enum import IntEnum
from typing import Any, Optional, cast

import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.legacy.heating_shaking import HeaterShaker
from pylabrobot.legacy.heating_shaking.backend import HeaterShakerBackend
from pylabrobot.legacy.liquid_handling import LiquidHandler
from pylabrobot.legacy.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.legacy.liquid_handling.backends.chatterbox import (
  LiquidHandlerChatterboxBackend,
)
from pylabrobot.legacy.machines.backend import MachineBackend
from pylabrobot.legacy.only_fans import Fan
from pylabrobot.legacy.only_fans.backend import FanBackend
from pylabrobot.legacy.storage import Incubator
from pylabrobot.legacy.storage.backend import IncubatorBackend
from pylabrobot.legacy.storage.inheco.incubator_shaker import IncubatorShakerStack
from pylabrobot.legacy.storage.inheco.incubator_shaker_backend import (
  InhecoIncubatorShakerStackBackend,
)
from pylabrobot.legacy.temperature_controlling import TemperatureController
from pylabrobot.legacy.temperature_controlling.backend import TemperatureControllerBackend
from pylabrobot.legacy.tip_tracker import TipTracker
from pylabrobot.resources import Coordinate, Deck, Plate, PlateCarrier, PlateHolder, Tip
from pylabrobot.testing import AnyioTestBase


class ChannelIntEnum(IntEnum):
  ONE = 1


class FrontendRecordingBackend(MachineBackend):
  def __init__(self, events: list[str]) -> None:
    super().__init__()
    self.events = events
    self.options: dict[str, Any] = {}
    self.num_channels = 2
    self.head96_installed = False
    self.num_arms = 1
    self.number_of_connected_units = 0
    self.unit_composition: list[str] = []
    self.fail_stop = False
    self.fail_turn_off = False
    self.fail_deactivate = False
    self.fail_stop_shaking = False
    self.fail_set_racks = False
    self.fail_set_heads_call: Optional[int] = None
    self.set_heads_calls = 0
    self.published_heads: list[tuple[dict[int, TipTracker], Optional[dict[int, TipTracker]]]] = []

  async def _record_stop(self) -> None:
    self.events.append("stop")
    if self.fail_stop:
      raise RuntimeError("stop failed")

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    self.options = backend_kwargs
    self.events.append("setup")
    stack.push_shielded_async_callback(self._record_stop)

  def set_deck(self, deck: Deck) -> None:
    self.events.append("set_deck")

  def set_heads(
    self,
    head: dict[int, TipTracker],
    head96: Optional[dict[int, TipTracker]] = None,
  ) -> None:
    self.set_heads_calls += 1
    self.events.append("set_heads")
    self.published_heads.append((head, head96))
    if self.fail_set_heads_call == self.set_heads_calls:
      raise RuntimeError("set_heads failed")

  async def turn_off(self) -> None:
    self.events.append("turn_off")
    if self.fail_turn_off:
      raise RuntimeError("turn_off failed")

  async def deactivate(self) -> None:
    self.events.append("deactivate")
    if self.fail_deactivate:
      raise RuntimeError("deactivate failed")

  async def stop_shaking(self, **backend_kwargs: Any) -> None:
    self.events.append("stop_shaking")
    if self.fail_stop_shaking:
      raise RuntimeError("stop_shaking failed")

  async def set_racks(self, racks: list[PlateCarrier]) -> None:
    self.events.append("set_racks")
    if self.fail_set_racks:
      raise RuntimeError("set_racks failed")


class TaskGroupFrontendBackend(FrontendRecordingBackend):
  """Record frontend teardown while owning a real AnyIO task-group scope."""

  async def _enter_lifespan(
    self, stack: AsyncExitStackWithShielding, **backend_kwargs: Any
  ) -> None:
    await super()._enter_lifespan(stack, **backend_kwargs)
    group = await stack.enter_task_group()
    stack.callback(group.cancel_scope.cancel)


class IncubatorRecordingBackend(IncubatorBackend):
  """Incubator backend double for frontend lifecycle ordering tests."""

  def __init__(self, events: list[str]) -> None:
    super().__init__()
    self.events = events
    self.options: dict[str, Any] = {}
    self.fail_stop = False
    self.fail_set_racks = False

  async def _record_stop(self) -> None:
    self.events.append("stop")
    if self.fail_stop:
      raise RuntimeError("stop failed")

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    self.options = backend_kwargs
    self.events.append("setup")
    stack.push_shielded_async_callback(self._record_stop)

  async def set_racks(self, racks: list[PlateCarrier]) -> None:
    self.events.append("set_racks")
    if self.fail_set_racks:
      raise RuntimeError("set_racks failed")
    await super().set_racks(racks)

  async def open_door(self) -> None:
    pass

  async def close_door(self) -> None:
    pass

  async def fetch_plate_to_loading_tray(self, plate: Plate, **backend_kwargs: Any) -> None:
    pass

  async def take_in_plate(self, plate: Plate, site: PlateHolder, **backend_kwargs: Any) -> None:
    pass

  async def set_temperature(self, temperature: float) -> None:
    pass

  async def get_temperature(self) -> float:
    return 37.0

  async def _start_shaking_hardware(
    self, frequency: float, shakers: Optional[list[int]] = None
  ) -> None:
    self.events.append("start_shaking")

  async def _stop_shaking_hardware(self) -> None:
    self.events.append("stop_shaking")


class SetupDiscoveredBackend(LiquidHandlerChatterboxBackend):
  """Resource-free backend whose capabilities become available during setup."""

  def __init__(self, *, discovered_num_channels: int) -> None:
    super().__init__(num_channels=discovered_num_channels)
    self._capabilities_discovered = False
    self._head96_installed = None
    self.events: list[str] = []
    self.head_seen_during_setup: Optional[dict[int, TipTracker]] = None

  @property
  def num_channels(self) -> int:
    if not self._capabilities_discovered:
      raise RuntimeError("num_channels is unavailable before setup")
    return self._num_channels

  @property
  def head96_installed(self) -> Optional[bool]:
    if self._head96_installed is None:
      raise RuntimeError("head96_installed is unavailable before setup")
    return self._head96_installed

  async def _discover_capabilities(self) -> None:
    self.events.append("setup")
    self.head_seen_during_setup = self.head
    self._capabilities_discovered = True
    self._head96_installed = False

  async def _record_stop(self) -> None:
    self.events.append("stop")

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    if backend_kwargs:
      raise TypeError("setup-discovered backend does not accept lifespan options")
    self._validate_lifespan_context()
    await self._discover_capabilities()
    stack.push_shielded_async_callback(self._record_stop)


def _temperature_controller(backend: FrontendRecordingBackend) -> TemperatureController:
  return TemperatureController(
    name="controller",
    size_x=1,
    size_y=1,
    size_z=1,
    backend=cast(TemperatureControllerBackend, backend),
    child_location=Coordinate.zero(),
  )


def _heater_shaker(backend: FrontendRecordingBackend) -> HeaterShaker:
  return HeaterShaker(
    name="heater_shaker",
    size_x=1,
    size_y=1,
    size_z=1,
    backend=cast(HeaterShakerBackend, backend),
    child_location=Coordinate.zero(),
  )


def _incubator(backend: IncubatorRecordingBackend) -> Incubator:
  return Incubator(
    backend=backend,
    name="incubator",
    size_x=1,
    size_y=1,
    size_z=1,
    racks=[],
    loading_tray_location=Coordinate.zero(),
  )


class TestFrontendLifespans(AnyioTestBase):
  async def test_fan_exits_task_group_backend_after_safety_cleanup(self) -> None:
    events: list[str] = []
    backend = TaskGroupFrontendBackend(events)
    fan = Fan(cast(FanBackend, backend))
    async with fan:
      assert backend.is_active
    assert not backend.is_active
    assert events == ["setup", "turn_off", "stop"]

  async def test_fan_body_error_reaches_task_group_backend(self) -> None:
    events: list[str] = []
    backend = TaskGroupFrontendBackend(events)
    fan = Fan(cast(FanBackend, backend))
    failure = ValueError("body failed")
    with pytest.raises(ValueError) as raised:
      async with fan:
        raise failure
    assert raised.value is failure
    assert not backend.is_active
    assert events == ["setup", "turn_off", "stop"]

  async def test_fan_turns_off_before_backend_exit(self) -> None:
    events: list[str] = []
    fan = Fan(cast(FanBackend, FrontendRecordingBackend(events)))
    assert events == []
    async with fan:
      events.append("body")
    assert events == ["setup", "body", "turn_off", "stop"]

  async def test_fan_failure_still_exits_backend(self) -> None:
    events: list[str] = []
    backend = FrontendRecordingBackend(events)
    backend.fail_turn_off = True
    fan = Fan(cast(FanBackend, backend))
    with pytest.raises(RuntimeError, match="turn_off failed"):
      async with fan:
        pass
    assert events == ["setup", "turn_off", "stop"]

  async def test_temperature_controller_deactivates_before_backend_exit(self) -> None:
    events: list[str] = []
    controller = _temperature_controller(FrontendRecordingBackend(events))
    controller.target_temperature = 37
    async with controller:
      pass
    assert controller.target_temperature is None
    assert events == ["setup", "deactivate", "stop"]

  async def test_temperature_controller_failure_still_exits_backend(self) -> None:
    events: list[str] = []
    backend = FrontendRecordingBackend(events)
    backend.fail_deactivate = True
    with pytest.raises(RuntimeError, match="deactivate failed"):
      async with _temperature_controller(backend):
        pass
    assert events == ["setup", "deactivate", "stop"]

  async def test_heater_shaker_runs_each_safety_action_once(self) -> None:
    events: list[str] = []
    heater_shaker = _heater_shaker(FrontendRecordingBackend(events))
    async with heater_shaker:
      pass
    assert events == ["setup", "deactivate", "stop_shaking", "stop"]
    assert events.count("deactivate") == 1
    assert events.count("stop_shaking") == 1

  async def test_heater_shaker_failure_still_exits_backend(self) -> None:
    events: list[str] = []
    backend = FrontendRecordingBackend(events)
    backend.fail_stop_shaking = True
    with pytest.raises(RuntimeError, match="stop_shaking failed"):
      async with _heater_shaker(backend):
        pass
    assert events == ["setup", "deactivate", "stop_shaking", "stop"]

  async def test_heater_shaker_deactivation_failure_still_exits_backend(self) -> None:
    events: list[str] = []
    backend = FrontendRecordingBackend(events)
    backend.fail_deactivate = True
    with pytest.raises(RuntimeError, match="deactivate failed"):
      async with _heater_shaker(backend):
        pass
    assert events == ["setup", "deactivate", "stop_shaking", "stop"]

  async def test_liquid_handler_publishes_state_around_backend_entry(self) -> None:
    events: list[str] = []
    backend = FrontendRecordingBackend(events)
    liquid_handler = LiquidHandler(
      cast(LiquidHandlerBackend, backend),
      deck=Deck(size_x=1, size_y=1, size_z=1),
    )
    async with liquid_handler.lifespan(port="mock"):
      assert len(liquid_handler.head) == 2
      assert liquid_handler._resource_pickups == {0: None}
    assert backend.options == {"port": "mock"}
    assert events == ["set_deck", "set_heads", "setup", "set_heads", "stop"]

  async def test_liquid_handler_initializes_empty_head_after_backend_entry(self) -> None:
    events: list[str] = []
    backend = FrontendRecordingBackend(events)
    liquid_handler = LiquidHandler(
      cast(LiquidHandlerBackend, backend),
      deck=Deck(size_x=1, size_y=1, size_z=1),
    )

    assert liquid_handler.head == {}
    async with liquid_handler:
      assert set(liquid_handler.head) == {0, 1}
    assert backend.published_heads[0][0] == {}
    assert set(backend.published_heads[1][0]) == {0, 1}

  async def test_liquid_handler_preserves_preloaded_head_across_sequential_entry(self) -> None:
    source_backend = FrontendRecordingBackend([])
    source = LiquidHandler(
      cast(LiquidHandlerBackend, source_backend),
      deck=Deck(size_x=1, size_y=1, size_z=1),
    )
    source.head = {channel: TipTracker(thing=f"Channel {channel}") for channel in range(2)}
    source.head[0].add_tip(
      Tip(
        has_filter=False,
        diameter=10,
        size_z=10,
        maximal_volume=10,
        fitting_depth=10,
        name="test_tip",
      )
    )
    source.head[0].get_tip().tracker.set_volume(3)

    events: list[str] = []
    backend = FrontendRecordingBackend(events)
    target = LiquidHandler(
      cast(LiquidHandlerBackend, backend),
      deck=Deck(size_x=1, size_y=1, size_z=1),
    )
    target.load_state(source.serialize_state())
    channel_zero = target.head[0]
    channel_one = target.head[1]

    for _ in range(2):
      async with target:
        assert target.head[0] is channel_zero
        assert target.head[1] is channel_one
        assert target.head[0].get_tip().tracker.get_used_volume() == 3
      assert target.head[0] is channel_zero
      assert target.head[1] is channel_one
      assert target.head[0].get_tip().tracker.get_used_volume() == 3

    assert all(head[0] is channel_zero for head, _ in backend.published_heads)
    assert all(head[1] is channel_one for head, _ in backend.published_heads)
    assert events == [
      "set_deck",
      "set_heads",
      "setup",
      "set_heads",
      "stop",
      "set_deck",
      "set_heads",
      "setup",
      "set_heads",
      "stop",
    ]

  async def test_liquid_handler_restores_state_before_setup_discovers_capabilities(self) -> None:
    source = LiquidHandler(
      LiquidHandlerChatterboxBackend(num_channels=2),
      deck=Deck(size_x=1, size_y=1, size_z=1),
    )
    source.head = {channel: TipTracker(thing=f"Channel {channel}") for channel in range(2)}
    source.head[0].add_tip(
      Tip(
        has_filter=False,
        diameter=10,
        size_z=10,
        maximal_volume=10,
        fitting_depth=10,
        name="test_tip",
      )
    )
    source.head[0].get_tip().tracker.set_volume(3)

    backend = SetupDiscoveredBackend(discovered_num_channels=2)
    target = LiquidHandler(backend, deck=Deck(size_x=1, size_y=1, size_z=1))
    target.load_state(source.serialize_state())
    restored_head = target.head
    restored_channel = target.head[0]

    async with target:
      assert backend.head_seen_during_setup is restored_head
      assert target.head[0] is restored_channel
      assert target.head[0].get_tip().tracker.get_used_volume() == 3

    assert target.head is restored_head
    assert backend.events == ["setup", "stop"]

  async def test_liquid_handler_rejects_state_after_setup_discovers_mismatch(self) -> None:
    source = LiquidHandler(
      LiquidHandlerChatterboxBackend(num_channels=2),
      deck=Deck(size_x=1, size_y=1, size_z=1),
    )
    source.head = {channel: TipTracker(thing=f"Channel {channel}") for channel in range(2)}

    backend = SetupDiscoveredBackend(discovered_num_channels=3)
    target = LiquidHandler(backend, deck=Deck(size_x=1, size_y=1, size_z=1))
    target.load_state(source.serialize_state())
    restored_head = target.head

    with pytest.raises(ValueError, match="head"):
      async with target:
        pytest.fail("incompatible state must not reach the body")

    assert target.head is restored_head
    assert set(target.head) == {0, 1}
    assert backend.events == ["setup", "stop"]

  async def test_liquid_handler_rejects_incompatible_live_head_before_publication(self) -> None:
    events: list[str] = []
    backend = FrontendRecordingBackend(events)
    liquid_handler = LiquidHandler(
      cast(LiquidHandlerBackend, backend),
      deck=Deck(size_x=1, size_y=1, size_z=1),
    )
    liquid_handler.head = {0: TipTracker(thing="Channel 0")}

    with pytest.raises(ValueError, match="head"):
      async with liquid_handler:
        pytest.fail("invalid head must not reach the body")

    assert events == []

  @pytest.mark.parametrize(
    ("channel", "invalid_channel"),
    [(0, 0.0), (1, True), (1, ChannelIntEnum.ONE)],
    ids=["float", "bool", "int-enum"],
  )
  async def test_liquid_handler_rejects_non_exact_int_live_head_key_before_publication(
    self,
    channel: int,
    invalid_channel: object,
  ) -> None:
    events: list[str] = []
    backend = FrontendRecordingBackend(events)
    liquid_handler = LiquidHandler(
      cast(LiquidHandlerBackend, backend),
      deck=Deck(size_x=1, size_y=1, size_z=1),
    )
    head: dict[object, TipTracker] = {
      channel: TipTracker(thing=f"Channel {channel}") for channel in range(2)
    }
    head[invalid_channel] = head.pop(channel)
    liquid_handler.head = cast(dict[int, TipTracker], head)

    with pytest.raises(ValueError, match="head"):
      async with liquid_handler:
        pytest.fail("non-integer head key must not reach the body")

    assert events == []

  async def test_liquid_handler_rejects_live_head96_when_not_installed(self) -> None:
    events: list[str] = []
    backend = FrontendRecordingBackend(events)
    liquid_handler = LiquidHandler(
      cast(LiquidHandlerBackend, backend),
      deck=Deck(size_x=1, size_y=1, size_z=1),
    )
    liquid_handler.head96 = {
      channel: TipTracker(thing=f"Channel {channel}") for channel in range(96)
    }

    with pytest.raises(ValueError, match="head96"):
      async with liquid_handler:
        pytest.fail("unavailable head96 must not reach the body")

    assert events == []

  async def test_liquid_handler_rejects_incompatible_live_head96_before_publication(self) -> None:
    events: list[str] = []
    backend = FrontendRecordingBackend(events)
    backend.head96_installed = True
    liquid_handler = LiquidHandler(
      cast(LiquidHandlerBackend, backend),
      deck=Deck(size_x=1, size_y=1, size_z=1),
    )
    liquid_handler.head96 = {0: TipTracker(thing="Channel 0")}

    with pytest.raises(ValueError, match="head96"):
      async with liquid_handler:
        pytest.fail("invalid head96 must not reach the body")

    assert events == []

  @pytest.mark.parametrize(
    ("channel", "invalid_channel"),
    [(0, 0.0), (1, True), (1, ChannelIntEnum.ONE)],
    ids=["float", "bool", "int-enum"],
  )
  async def test_liquid_handler_rejects_non_exact_int_live_head96_key_before_publication(
    self,
    channel: int,
    invalid_channel: object,
  ) -> None:
    events: list[str] = []
    backend = FrontendRecordingBackend(events)
    backend.head96_installed = True
    liquid_handler = LiquidHandler(
      cast(LiquidHandlerBackend, backend),
      deck=Deck(size_x=1, size_y=1, size_z=1),
    )
    head96: dict[object, TipTracker] = {
      channel: TipTracker(thing=f"Channel {channel}") for channel in range(96)
    }
    head96[invalid_channel] = head96.pop(channel)
    liquid_handler.head96 = cast(dict[int, TipTracker], head96)

    with pytest.raises(ValueError, match="head96"):
      async with liquid_handler:
        pytest.fail("non-integer head96 key must not reach the body")

    assert events == []

  async def test_liquid_handler_post_entry_failure_exits_backend(self) -> None:
    events: list[str] = []
    backend = FrontendRecordingBackend(events)
    backend.fail_set_heads_call = 2
    liquid_handler = LiquidHandler(
      cast(LiquidHandlerBackend, backend),
      deck=Deck(size_x=1, size_y=1, size_z=1),
    )
    emitted: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(emitted.append)
    with use_event_bus(event_bus):
      with pytest.raises(RuntimeError, match="set_heads failed"):
        async with liquid_handler:
          pytest.fail("failed publication must not reach the body")
    assert events == ["set_deck", "set_heads", "setup", "set_heads", "stop"]
    assert [event.name for event in emitted] == [
      "machine.setup.started",
      "machine.setup.failed",
    ]

  async def test_incubator_ignores_options_then_publishes_racks(self) -> None:
    events: list[str] = []
    backend = IncubatorRecordingBackend(events)
    incubator = _incubator(backend)
    emitted: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(emitted.append)
    with use_event_bus(event_bus):
      async with incubator.lifespan(port="ignored"):
        pass
    assert backend.options == {}
    assert events == ["setup", "set_racks", "stop"]
    setup_event = emitted[0]
    assert setup_event.name == "machine.setup.started"
    assert setup_event.data["device"] == {
      "name": "incubator",
      "type": "Incubator",
      "rotation": {"x": 0, "y": 0, "z": 0},
    }

  async def test_incubator_publication_failure_exits_backend(self) -> None:
    events: list[str] = []
    backend = IncubatorRecordingBackend(events)
    backend.fail_set_racks = True
    with pytest.raises(RuntimeError, match="set_racks failed"):
      async with _incubator(backend):
        pytest.fail("failed publication must not reach the body")
    assert events == ["setup", "set_racks", "stop"]

  async def test_incubator_scope_uses_backend_owner_and_preserves_cleanup_order(self) -> None:
    events: list[str] = []
    backend = IncubatorRecordingBackend(events)
    incubator = _incubator(backend)

    async with incubator:
      async with incubator.shaking(4):
        events_before_rejections = list(events)
        with pytest.raises(RuntimeError, match="scope"):
          await backend.start_shaking(5)
        with pytest.raises(RuntimeError, match="scope"):
          await backend.stop_shaking()
        assert events == events_before_rejections

    assert events == ["setup", "set_racks", "start_shaking", "stop_shaking", "stop"]

  async def test_incubator_shaker_stack_clears_anomalous_state(self) -> None:
    events: list[str] = []
    backend = FrontendRecordingBackend(events)
    stack = IncubatorShakerStack(cast(InhecoIncubatorShakerStackBackend, backend))
    stack.units.append(cast(Any, "existing-unit"))
    stack.loading_trays.append(cast(Any, "existing-tray"))
    emitted: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(emitted.append)
    with use_event_bus(event_bus):
      async with stack:
        assert not stack.setup_finished
        assert stack.units == []
        assert stack.loading_trays == []
      async with stack:
        assert stack.units == []
        assert stack.loading_trays == []
    assert events == ["setup", "stop", "setup", "stop"]
    assert not any(event.name.startswith("machine.") for event in emitted)
    assert stack.get_size_z() == 58

  async def test_incubator_shaker_stack_post_entry_failure_exits_backend(self) -> None:
    events: list[str] = []
    backend = FrontendRecordingBackend(events)
    backend.number_of_connected_units = 1
    backend.unit_composition = ["incubator_mp"]
    stack = IncubatorShakerStack(cast(InhecoIncubatorShakerStackBackend, backend))
    emitted: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(emitted.append)
    with use_event_bus(event_bus):
      with pytest.raises(ValueError, match="Loading tray location.*not defined"):
        async with stack:
          pytest.fail("failed construction must not reach the body")
    assert events == ["setup", "stop"]
    assert not stack.setup_finished
    assert not any(event.name.startswith("machine.") for event in emitted)
