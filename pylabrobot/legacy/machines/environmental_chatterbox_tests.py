from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.legacy.heating_shaking import HeaterShaker, HeaterShakerChatterboxBackend
from pylabrobot.legacy.machines.backend import MachineBackend
from pylabrobot.legacy.only_fans import FanBackend
from pylabrobot.legacy.only_fans.chatterbox import FanChatterboxBackend
from pylabrobot.legacy.shaking import ShakerChatterboxBackend
from pylabrobot.legacy.storage import Incubator, IncubatorChatterboxBackend
from pylabrobot.legacy.temperature_controlling import TemperatureControllerChatterboxBackend
from pylabrobot.legacy.thermocycling import ThermocyclerChatterboxBackend
from pylabrobot.legacy.tilting.chatterbox import TilterChatterboxBackend
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.testing import AnyioTestBase

EntryHook = Callable[[AsyncExitStackWithShielding], Awaitable[None]]


@dataclass(frozen=True)
class ChatterboxCase:
  name: str
  factory: Callable[[], MachineBackend]
  hooked_factory: Callable[[EntryHook], MachineBackend]
  option_error: str
  setup_output: str
  stop_output: str


def make_hooked_fan(hook: EntryHook) -> MachineBackend:
  class HookedFan(FanChatterboxBackend):
    async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
      await super()._enter_lifespan(stack, **kwargs)
      await hook(stack)

  return HookedFan()


def make_hooked_shaker(hook: EntryHook) -> MachineBackend:
  class HookedShaker(ShakerChatterboxBackend):
    async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
      await super()._enter_lifespan(stack, **kwargs)
      await hook(stack)

  return HookedShaker()


def make_hooked_temperature_controller(hook: EntryHook) -> MachineBackend:
  class HookedTemperatureController(TemperatureControllerChatterboxBackend):
    async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
      await super()._enter_lifespan(stack, **kwargs)
      await hook(stack)

  return HookedTemperatureController()


def make_hooked_heater_shaker(hook: EntryHook) -> MachineBackend:
  class HookedHeaterShaker(HeaterShakerChatterboxBackend):
    async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
      await super()._enter_lifespan(stack, **kwargs)
      await hook(stack)

  return HookedHeaterShaker()


def make_hooked_incubator(hook: EntryHook) -> MachineBackend:
  class HookedIncubator(IncubatorChatterboxBackend):
    async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
      await super()._enter_lifespan(stack, **kwargs)
      await hook(stack)

  return HookedIncubator()


def make_hooked_thermocycler(hook: EntryHook) -> MachineBackend:
  class HookedThermocycler(ThermocyclerChatterboxBackend):
    async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
      await super()._enter_lifespan(stack, **kwargs)
      await hook(stack)

  return HookedThermocycler()


def make_hooked_tilter(hook: EntryHook) -> MachineBackend:
  class HookedTilter(TilterChatterboxBackend):
    async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
      await super()._enter_lifespan(stack, **kwargs)
      await hook(stack)

  return HookedTilter()


def make_heater_shaker_frontend() -> HeaterShaker:
  """Build a real heater shaker frontend over a real chatterbox backend."""
  return HeaterShaker(
    name="chatterbox_heater_shaker",
    size_x=10,
    size_y=10,
    size_z=10,
    backend=HeaterShakerChatterboxBackend(),
    child_location=Coordinate.zero(),
  )


def make_incubator_frontend() -> Incubator:
  """Build a real incubator frontend over a real chatterbox backend."""
  return Incubator(
    name="chatterbox_incubator",
    size_x=10,
    size_y=10,
    size_z=10,
    backend=IncubatorChatterboxBackend(),
    loading_tray_location=Coordinate.zero(),
    racks=[],
  )


CHATTERBOX_CASES = [
  ChatterboxCase(
    "fan",
    FanChatterboxBackend,
    make_hooked_fan,
    "unexpected keyword argument",
    "Setting up the fan.\n",
    "Stopping the fan.\n",
  ),
  ChatterboxCase(
    "shaker",
    ShakerChatterboxBackend,
    make_hooked_shaker,
    "unexpected keyword argument",
    "Setting up shaker\n",
    "Stopping shaker\n",
  ),
  ChatterboxCase(
    "temperature-controller",
    TemperatureControllerChatterboxBackend,
    make_hooked_temperature_controller,
    "unexpected keyword argument",
    "Setting up the temperature controller.\n",
    "Stopping the temperature controller.\n",
  ),
  ChatterboxCase(
    "heater-shaker",
    HeaterShakerChatterboxBackend,
    make_hooked_heater_shaker,
    "unexpected keyword argument",
    "Setting up shaker\n",
    "Stopping shaker\n",
  ),
  ChatterboxCase(
    "incubator",
    IncubatorChatterboxBackend,
    make_hooked_incubator,
    "unexpected keyword argument",
    "Setting up incubator backend\n",
    "Stopping incubator backend\n",
  ),
  ChatterboxCase(
    "thermocycler",
    ThermocyclerChatterboxBackend,
    make_hooked_thermocycler,
    "unexpected keyword argument",
    "Setting up thermocycler.\n",
    "Stopping thermocycler.\n",
  ),
  ChatterboxCase(
    "tilter",
    TilterChatterboxBackend,
    make_hooked_tilter,
    "unexpected keyword argument",
    "Setting up tilter.\n",
    "Stopping tilter.\n",
  ),
]


class TestEnvironmentalChatterboxLifespans(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    assert FanBackend.setup is HasLifespan.setup
    assert FanBackend.stop is HasLifespan.stop
    assert FanChatterboxBackend.__aenter__ is HasLifespan.__aenter__
    assert FanChatterboxBackend.__aexit__ is HasLifespan.__aexit__
    assert FanChatterboxBackend.setup is HasLifespan.setup
    assert FanChatterboxBackend.stop is HasLifespan.stop
    assert ShakerChatterboxBackend.__aenter__ is HasLifespan.__aenter__
    assert ShakerChatterboxBackend.__aexit__ is HasLifespan.__aexit__
    assert ShakerChatterboxBackend.setup is HasLifespan.setup
    assert ShakerChatterboxBackend.stop is HasLifespan.stop
    assert TemperatureControllerChatterboxBackend.__aenter__ is HasLifespan.__aenter__
    assert TemperatureControllerChatterboxBackend.__aexit__ is HasLifespan.__aexit__
    assert TemperatureControllerChatterboxBackend.setup is HasLifespan.setup
    assert TemperatureControllerChatterboxBackend.stop is HasLifespan.stop
    assert HeaterShakerChatterboxBackend.__aenter__ is HasLifespan.__aenter__
    assert HeaterShakerChatterboxBackend.__aexit__ is HasLifespan.__aexit__
    assert HeaterShakerChatterboxBackend.setup is HasLifespan.setup
    assert HeaterShakerChatterboxBackend.stop is HasLifespan.stop
    assert IncubatorChatterboxBackend.__aenter__ is HasLifespan.__aenter__
    assert IncubatorChatterboxBackend.__aexit__ is HasLifespan.__aexit__
    assert IncubatorChatterboxBackend.setup is HasLifespan.setup
    assert IncubatorChatterboxBackend.stop is HasLifespan.stop
    assert ThermocyclerChatterboxBackend.__aenter__ is HasLifespan.__aenter__
    assert ThermocyclerChatterboxBackend.__aexit__ is HasLifespan.__aexit__
    assert ThermocyclerChatterboxBackend.setup is HasLifespan.setup
    assert ThermocyclerChatterboxBackend.stop is HasLifespan.stop
    assert TilterChatterboxBackend.__aenter__ is HasLifespan.__aenter__
    assert TilterChatterboxBackend.__aexit__ is HasLifespan.__aexit__
    assert TilterChatterboxBackend.setup is HasLifespan.setup
    assert TilterChatterboxBackend.stop is HasLifespan.stop

  @pytest.mark.parametrize("case", CHATTERBOX_CASES, ids=lambda case: case.name)
  async def test_normal_exit_preserves_output(self, case: ChatterboxCase, capsys) -> None:
    async with case.factory():
      assert capsys.readouterr().out == case.setup_output
    assert capsys.readouterr().out == case.stop_output

    if case.name == "incubator":
      async with IncubatorChatterboxBackend() as entered_backend:
        assert isinstance(entered_backend, IncubatorChatterboxBackend)
        await entered_backend.start_shaking(8)
      assert capsys.readouterr().out == (
        "Setting up incubator backend\n"
        "Starting shaking at 8 Hz\n"
        "Stopping shaking\n"
        "Stopping incubator backend\n"
      )

  @pytest.mark.parametrize("case", CHATTERBOX_CASES, ids=lambda case: case.name)
  async def test_lifespan_options_are_rejected_before_output_or_mutation(
    self, case: ChatterboxCase, capsys
  ) -> None:
    backend = case.factory()
    with pytest.raises(TypeError, match=case.option_error):
      async with backend.lifespan(unused=True):
        pytest.fail("invalid entry reached body")
    assert capsys.readouterr().out == ""
    assert not backend.is_active

  @pytest.mark.parametrize("case", CHATTERBOX_CASES, ids=lambda case: case.name)
  async def test_failed_subclass_entry_runs_registered_cleanup(
    self, case: ChatterboxCase, capsys
  ) -> None:
    entry_error = RuntimeError("entry failed")

    async def fail_entry(stack: AsyncExitStackWithShielding) -> None:
      raise entry_error

    backend = case.hooked_factory(fail_entry)
    with pytest.raises(RuntimeError, match="entry failed") as error_info:
      async with backend:
        pytest.fail("failed entry reached body")
    assert error_info.value is entry_error
    assert capsys.readouterr().out == case.setup_output + case.stop_output
    assert not backend.is_active

  @pytest.mark.parametrize("case", CHATTERBOX_CASES, ids=lambda case: case.name)
  async def test_entry_cancellation_runs_registered_cleanup(
    self, case: ChatterboxCase, capsys
  ) -> None:
    entry_started = anyio.Event()
    finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def block_entry(stack: AsyncExitStackWithShielding) -> None:
      entry_started.set()
      await anyio.sleep_forever()

    backend = case.hooked_factory(block_entry)

    async def enter_backend() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          async with backend:
            pytest.fail("cancelled entry reached body")
      finally:
        finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_backend)
      await entry_started.wait()
      scopes[0].cancel()
      await finished.wait()

    assert capsys.readouterr().out == case.setup_output + case.stop_output
    assert not backend.is_active

  @pytest.mark.parametrize("case", CHATTERBOX_CASES, ids=lambda case: case.name)
  async def test_body_cancellation_runs_registered_cleanup(
    self, case: ChatterboxCase, capsys
  ) -> None:
    body_started = anyio.Event()
    finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    backend = case.factory()

    async def use_backend() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          async with backend:
            body_started.set()
            await anyio.sleep_forever()
      finally:
        finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_backend)
      await body_started.wait()
      scopes[0].cancel()
      await finished.wait()

    assert capsys.readouterr().out == case.setup_output + case.stop_output
    assert not backend.is_active

  @pytest.mark.parametrize("case", CHATTERBOX_CASES, ids=lambda case: case.name)
  async def test_cleanup_failure_propagates(self, case: ChatterboxCase, capsys) -> None:
    cleanup_error = RuntimeError("cleanup failed")

    async def register_failing_cleanup(stack: AsyncExitStackWithShielding) -> None:
      async def fail_cleanup() -> None:
        raise cleanup_error

      stack.push_shielded_async_callback(fail_cleanup)

    backend = case.hooked_factory(register_failing_cleanup)
    with pytest.raises(RuntimeError, match="cleanup failed") as error_info:
      async with backend:
        pass
    assert error_info.value is cleanup_error
    assert capsys.readouterr().out == case.setup_output + case.stop_output
    assert not backend.is_active

  @pytest.mark.parametrize("case", CHATTERBOX_CASES, ids=lambda case: case.name)
  async def test_sequential_reuse(self, case: ChatterboxCase, capsys) -> None:
    backend = case.factory()
    for _ in range(2):
      async with backend:
        pass
    assert capsys.readouterr().out == 2 * (case.setup_output + case.stop_output)
    assert not backend.is_active

  async def test_heater_shaker_frontend_exit_issues_each_cleanup_once(self, capsys) -> None:
    heater_shaker = make_heater_shaker_frontend()

    async with heater_shaker:
      await heater_shaker.shake(900)

    assert capsys.readouterr().out == (
      "Setting up shaker\n"
      "Locking plate\n"
      "Shaking at speed 900\n"
      "Deactivating the temperature controller.\n"
      "Stopping shaking\n"
      "Stopping shaker\n"
    )
    assert not heater_shaker.is_active

  async def test_heater_shaker_frontend_exit_without_shaking_issues_each_cleanup_once(
    self, capsys
  ) -> None:
    heater_shaker = make_heater_shaker_frontend()

    async with heater_shaker:
      pass

    assert capsys.readouterr().out == (
      "Setting up shaker\n"
      "Deactivating the temperature controller.\n"
      "Stopping shaking\n"
      "Stopping shaker\n"
    )
    assert not heater_shaker.is_active

  async def test_incubator_frontend_exit_issues_shaking_stop_once(self, capsys) -> None:
    incubator = make_incubator_frontend()

    async with incubator:
      await incubator.start_shaking(8)

    assert capsys.readouterr().out == (
      "Setting up incubator backend\n"
      "Starting shaking at 8 Hz\n"
      "Stopping shaking\n"
      "Stopping incubator backend\n"
    )
    assert not incubator.is_active
