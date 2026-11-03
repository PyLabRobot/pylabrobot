import json
import unittest
from typing import Any, Optional
from unittest.mock import patch

from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.legacy.thermocycling.backend import ThermocyclerBackend
from pylabrobot.legacy.thermocycling.standard import (
  BlockStatus,
  LidStatus,
  Protocol,
  Stage,
  Step,
)
from pylabrobot.legacy.thermocycling.thermocycler import Thermocycler
from pylabrobot.resources import Coordinate
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb


class _RecordingThermocyclerBackend(ThermocyclerBackend):
  """Complete test backend that preserves opaque results and forwarded arguments."""

  def __init__(self) -> None:
    super().__init__()
    self.result = object()
    self.failure: Optional[BaseException] = None
    self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

  def _record(self, operation: str, *args: Any, **kwargs: Any) -> object:
    self.calls.append((operation, args, kwargs))
    if self.failure is not None:
      raise self.failure
    return self.result

  async def setup(self, **backend_kwargs: Any) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def open_lid(self, **backend_kwargs: Any) -> object:
    return self._record("open_lid", **backend_kwargs)

  async def close_lid(self, **backend_kwargs: Any) -> object:
    return self._record("close_lid", **backend_kwargs)

  async def set_block_temperature(self, temperature: list[float], **backend_kwargs: Any) -> object:
    return self._record("set_block_temperature", temperature, **backend_kwargs)

  async def set_lid_temperature(self, temperature: list[float], **backend_kwargs: Any) -> object:
    return self._record("set_lid_temperature", temperature, **backend_kwargs)

  async def deactivate_block(self, **backend_kwargs: Any) -> object:
    return self._record("deactivate_block", **backend_kwargs)

  async def deactivate_lid(self, **backend_kwargs: Any) -> object:
    return self._record("deactivate_lid", **backend_kwargs)

  async def run_protocol(
    self,
    protocol: Protocol,
    block_max_volume: float,
    **backend_kwargs: Any,
  ) -> object:
    return self._record("run_protocol", protocol, block_max_volume, **backend_kwargs)

  async def get_block_current_temperature(self, **backend_kwargs: Any) -> list[float]:
    return [25.0]

  async def get_block_target_temperature(self, **backend_kwargs: Any) -> list[float]:
    return [25.0]

  async def get_lid_current_temperature(self, **backend_kwargs: Any) -> list[float]:
    return [25.0]

  async def get_lid_target_temperature(self, **backend_kwargs: Any) -> list[float]:
    return [25.0]

  async def get_lid_open(self, **backend_kwargs: Any) -> bool:
    return False

  async def get_lid_status(self, **backend_kwargs: Any) -> LidStatus:
    return LidStatus.IDLE

  async def get_block_status(self, **backend_kwargs: Any) -> BlockStatus:
    return BlockStatus.IDLE

  async def get_hold_time(self, **backend_kwargs: Any) -> float:
    return 0.0

  async def get_current_cycle_index(self, **backend_kwargs: Any) -> int:
    return 0

  async def get_total_cycle_count(self, **backend_kwargs: Any) -> int:
    return 0

  async def get_current_step_index(self, **backend_kwargs: Any) -> int:
    return 0

  async def get_total_step_count(self, **backend_kwargs: Any) -> int:
    return 0


def _protocol() -> Protocol:
  return Protocol(
    stages=[
      Stage(
        steps=[
          Step(temperature=[95.0, 96.0], hold_seconds=30.0),
          Step(temperature=[55.0, 56.0], hold_seconds=20.0),
        ],
        repeats=3,
      ),
      Stage(
        steps=[Step(temperature=[72.0, 73.0], hold_seconds=60.0)],
        repeats=2,
      ),
    ]
  )


class ThermocyclerEventTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    self.backend = _RecordingThermocyclerBackend()
    self.thermocycler = Thermocycler(
      name="thermocycler",
      size_x=1,
      size_y=1,
      size_z=1,
      backend=self.backend,
      child_location=Coordinate.zero(),
    )
    self.plate = cor_96_wellplate_360uL_Fb(name="plate")
    self.thermocycler.assign_child_resource(self.plate)

  @staticmethod
  def _assert_correlated_pairs(events: list[PLREvent]) -> None:
    for started, terminal in zip(events[::2], events[1::2]):
      assert started.name.endswith(".started")
      assert terminal.name.removesuffix(".completed").removesuffix(
        ".failed"
      ) == started.name.removesuffix(".started")
      assert started.context["operation_id"] == terminal.context["operation_id"]

  async def test_public_primitives_emit_bounded_correlated_success_lifecycles(self) -> None:
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)
    protocol = _protocol()

    with use_event_bus(event_bus):
      results = [
        await self.thermocycler.open_lid(vendor="open"),
        await self.thermocycler.close_lid(vendor="close"),
        await self.thermocycler.set_block_temperature([95.0, 96.0], vendor="block"),
        await self.thermocycler.set_lid_temperature(temperature=[105.0], vendor="lid"),
        await self.thermocycler.deactivate_block(vendor="block-off"),
        await self.thermocycler.deactivate_lid(vendor="lid-off"),
        await self.thermocycler.run_protocol(protocol, 50.0, vendor="protocol"),
      ]

    self.assertTrue(all(result is self.backend.result for result in results))
    operation_names = [
      "thermocycler.open_lid",
      "thermocycler.close_lid",
      "thermocycler.set_block_temperature",
      "thermocycler.set_lid_temperature",
      "thermocycler.deactivate_block",
      "thermocycler.deactivate_lid",
      "thermocycler.run_protocol",
    ]
    self.assertEqual(
      [event.name for event in events],
      [f"{name}.{phase}" for name in operation_names for phase in ("started", "completed")],
    )
    self._assert_correlated_pairs(events)

    for event in events:
      self.assertEqual(event.data["device"]["name"], "thermocycler")
      self.assertEqual([resource["name"] for resource in event.data["resources"]], ["plate"])
      json.dumps(event.as_dict(), allow_nan=False)
      self.assertNotIn("backend_kwargs", event.data)
      self.assertNotIn("protocol", event.data)
      self.assertNotIn("result", event.data)

    self.assertEqual(events[4].data["target_temperatures"], [95.0, 96.0])
    self.assertEqual(events[6].data["target_temperatures"], [105.0])
    run_data = events[12].data
    self.assertEqual(run_data["block_max_volume"], 50.0)
    self.assertEqual(run_data["stage_count"], 2)
    self.assertEqual(run_data["step_definition_count"], 3)
    self.assertEqual(run_data["step_execution_count"], 8)
    self.assertEqual(run_data["temperature_zone_count"], 2)

    self.assertEqual(self.backend.calls[0][2], {"vendor": "open"})
    self.assertIs(self.backend.calls[-1][1][0], protocol)
    self.assertEqual(self.backend.calls[-1][1][1], 50.0)
    self.assertEqual(self.backend.calls[-1][2], {"vendor": "protocol"})

  async def test_operation_without_loaded_resource_omits_resources(self) -> None:
    self.plate.unassign()
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.thermocycler.open_lid()

    self.assertNotIn("resources", events[0].data)
    self.assertNotIn("resources", events[1].data)

  async def test_backend_failure_emits_failed_and_preserves_exception_identity(self) -> None:
    error = RuntimeError("thermal failure")
    self.backend.failure = error
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with self.assertRaises(RuntimeError) as raised:
        await self.thermocycler.set_block_temperature([37.0])

    self.assertIs(raised.exception, error)
    self.assertEqual(
      [event.name for event in events],
      ["thermocycler.set_block_temperature.started", "thermocycler.set_block_temperature.failed"],
    )
    self._assert_correlated_pairs(events)
    self.assertEqual(events[1].data["error_type"], "RuntimeError")
    self.assertEqual(events[1].data["error_message"], "thermal failure")

  async def test_run_protocol_validation_failure_is_inside_lifecycle(self) -> None:
    protocol = Protocol(
      stages=[
        Stage(
          steps=[
            Step(temperature=[95.0, 96.0], hold_seconds=1.0),
            Step(temperature=[55.0], hold_seconds=1.0),
          ],
          repeats=1,
        )
      ]
    )
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with self.assertRaises(ValueError):
        await self.thermocycler.run_protocol(protocol, block_max_volume=25.0)

    self.assertEqual(
      [event.name for event in events],
      ["thermocycler.run_protocol.started", "thermocycler.run_protocol.failed"],
    )
    self._assert_correlated_pairs(events)
    self.assertEqual(events[0].data["step_definition_count"], 2)
    self.assertEqual(events[1].data["error_type"], "ValueError")
    self.assertFalse(any(call[0] == "run_protocol" for call in self.backend.calls))

  async def test_metadata_is_lazy_without_an_interested_listener(self) -> None:
    protocol = _protocol()

    with patch(
      "pylabrobot.legacy.thermocycling.thermocycler.resource_reference",
      side_effect=AssertionError("metadata should be lazy"),
    ):
      first = await self.thermocycler.set_block_temperature([37.0])
      second = await self.thermocycler.run_protocol(protocol, 25.0)
      with use_event_bus(EventBus()):
        third = await self.thermocycler.set_lid_temperature([105.0])
        fourth = await self.thermocycler.run_protocol(protocol, 25.0)

    self.assertIs(first, self.backend.result)
    self.assertIs(second, self.backend.result)
    self.assertIs(third, self.backend.result)
    self.assertIs(fourth, self.backend.result)

  async def test_composite_emits_only_primitive_operations(self) -> None:
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.thermocycler.run_pcr_profile(
        denaturation_temp=[98.0],
        denaturation_time=10.0,
        annealing_temp=[55.0],
        annealing_time=30.0,
        extension_temp=[72.0],
        extension_time=60.0,
        num_cycles=2,
        block_max_volume=25.0,
        lid_temperature=[105.0],
      )

    self.assertEqual(
      [event.name for event in events],
      [
        "thermocycler.set_lid_temperature.started",
        "thermocycler.set_lid_temperature.completed",
        "thermocycler.run_protocol.started",
        "thermocycler.run_protocol.completed",
      ],
    )
    self.assertFalse(any("run_pcr_profile" in event.name for event in events))
    self.assertFalse(any("wait" in event.name for event in events))

  async def test_wait_and_status_methods_are_not_instrumented(self) -> None:
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.thermocycler.get_block_current_temperature()
      await self.thermocycler.get_block_target_temperature()
      await self.thermocycler.get_lid_current_temperature()
      await self.thermocycler.get_lid_target_temperature()
      await self.thermocycler.get_lid_open()
      await self.thermocycler.get_lid_status()
      await self.thermocycler.get_block_status()
      await self.thermocycler.get_hold_time()
      await self.thermocycler.get_current_cycle_index()
      await self.thermocycler.get_total_cycle_count()
      await self.thermocycler.get_current_step_index()
      await self.thermocycler.get_total_step_count()
      await self.thermocycler.wait_for_block()
      await self.thermocycler.wait_for_lid()
      await self.thermocycler.is_profile_running()
      await self.thermocycler.wait_for_profile_completion(poll_interval=0)

    self.assertEqual(events, [])
