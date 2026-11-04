"""Resource-free lifecycle tests for the legacy OT-2 simulator."""

from typing import List

import anyio
import pytest

from pylabrobot.concurrency import HasLifespan
from pylabrobot.legacy.liquid_handling import LiquidHandler
from pylabrobot.legacy.liquid_handling.backends.opentrons_backend import OpentronsOT2Backend
from pylabrobot.legacy.liquid_handling.backends.opentrons_chatterbox import (
  OpentronsOT2ChatterboxBackend,
)
from pylabrobot.legacy.liquid_handling.backends.opentrons_simulator import OpentronsOT2Simulator
from pylabrobot.resources import Coordinate
from pylabrobot.resources.opentrons import OTDeck
from pylabrobot.testing.concurrency import AnyioTestBase


class RecordingOpentronsSimulator(OpentronsOT2Simulator):
  """Record simulated homing without changing simulator lifecycle behavior."""

  def __init__(self) -> None:
    super().__init__()
    self.home_calls = 0

  async def home(self) -> None:
    """Record one simulated home operation."""
    self.home_calls += 1


class BlockingHomeSimulator(RecordingOpentronsSimulator):
  """Expose explicit synchronization around simulated homing."""

  def __init__(self) -> None:
    super().__init__()
    self.home_started = anyio.Event()
    self.release_home = anyio.Event()

  async def home(self) -> None:
    """Block homing until the test releases it or entry is cancelled."""
    await super().home()
    self.home_started.set()
    await self.release_home.wait()


def make_liquid_handler(
  backend: OpentronsOT2Simulator,
) -> LiquidHandler:
  """Build a simulator composition without optional Opentrons dependencies."""
  return LiquidHandler(backend=backend, deck=OTDeck())


def assert_simulator_state_cleared(backend: OpentronsOT2Simulator) -> None:
  """Assert all state published for one simulator lifespan is cleared."""
  assert backend.left_pipette is None
  assert backend.right_pipette is None
  assert backend.left_pipette_has_tip is False
  assert backend.right_pipette_has_tip is False
  assert backend._positions == {}


class TestOpentronsSimulatorLifecycle(AnyioTestBase):
  """Prove native simulator ownership under asyncio and Trio."""

  async def test_native_dispatch_uses_managed_setup_and_stop(self) -> None:
    assert OpentronsOT2Simulator._enter_lifespan is not OpentronsOT2Backend._enter_lifespan
    assert OpentronsOT2Backend.setup is HasLifespan.setup
    assert OpentronsOT2Backend.stop is HasLifespan.stop
    assert OpentronsOT2Simulator.setup is HasLifespan.setup
    assert OpentronsOT2Simulator.stop is HasLifespan.stop
    assert OpentronsOT2ChatterboxBackend.setup is HasLifespan.setup
    assert OpentronsOT2ChatterboxBackend.stop is HasLifespan.stop

  async def test_scope_publishes_configured_pipettes_and_homes(self) -> None:
    backend = RecordingOpentronsSimulator()
    liquid_handler = make_liquid_handler(backend)

    assert_simulator_state_cleared(backend)
    async with liquid_handler:
      assert backend.left_pipette == {
        "name": "p300_single_gen2",
        "pipetteId": "sim-left",
      }
      assert backend.right_pipette == {
        "name": "p20_single_gen2",
        "pipetteId": "sim-right",
      }
      assert backend._positions == {
        "sim-left": Coordinate.zero(),
        "sim-right": Coordinate.zero(),
      }
      assert backend.home_calls == 1

    assert_simulator_state_cleared(backend)

  async def test_skip_home_publishes_state_without_homing(self) -> None:
    backend = RecordingOpentronsSimulator()
    liquid_handler = make_liquid_handler(backend)

    async with liquid_handler.lifespan(skip_home=True):
      assert backend.num_channels == 2
      assert backend.home_calls == 0

    assert_simulator_state_cleared(backend)

  async def test_missing_frontend_context_fails_without_publication(self) -> None:
    backend = RecordingOpentronsSimulator()

    with pytest.raises(RuntimeError, match="^Deck not set$"):
      async with backend:
        pytest.fail("invalid entry reached the body")

    assert backend.home_calls == 0
    assert_simulator_state_cleared(backend)

  async def test_unknown_option_fails_without_publication(self) -> None:
    backend = RecordingOpentronsSimulator()
    liquid_handler = make_liquid_handler(backend)

    with pytest.raises(TypeError, match="unexpected keyword argument 'unknown'"):
      async with liquid_handler.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert backend.home_calls == 0
    assert_simulator_state_cleared(backend)

  async def test_cancelled_entry_clears_published_state(self) -> None:
    backend = BlockingHomeSimulator()
    liquid_handler = make_liquid_handler(backend)
    scopes: List[anyio.CancelScope] = []
    scope_ready = anyio.Event()
    finished = anyio.Event()
    cancellations: List[BaseException] = []

    async def enter() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        scope_ready.set()
        try:
          async with liquid_handler:
            pytest.fail("cancelled entry reached the body")
        except anyio.get_cancelled_exc_class() as error:
          cancellations.append(error)
          raise
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      await scope_ready.wait()
      await backend.home_started.wait()
      assert backend.left_pipette is not None
      scopes[0].cancel()
      await finished.wait()

    assert len(cancellations) == 1
    assert_simulator_state_cleared(backend)

  async def test_cancelled_body_clears_published_state(self) -> None:
    backend = RecordingOpentronsSimulator()
    liquid_handler = make_liquid_handler(backend)
    scopes: List[anyio.CancelScope] = []
    body_started = anyio.Event()
    finished = anyio.Event()
    cancellations: List[BaseException] = []

    async def run_body() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with liquid_handler:
            backend.left_pipette_has_tip = True
            await backend.move_channel_x(channel=0, x=50)
            body_started.set()
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class() as error:
          cancellations.append(error)
          raise
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_body)
      await body_started.wait()
      scopes[0].cancel()
      await finished.wait()

    assert len(cancellations) == 1
    assert_simulator_state_cleared(backend)

  async def test_sequential_reuse_republishes_fresh_positions(self) -> None:
    backend = RecordingOpentronsSimulator()
    liquid_handler = make_liquid_handler(backend)

    async with liquid_handler:
      await backend.move_pipette_head(Coordinate(10, 20, 30), pipette_id="sim-left")
    assert_simulator_state_cleared(backend)

    async with liquid_handler:
      assert backend._positions == {
        "sim-left": Coordinate.zero(),
        "sim-right": Coordinate.zero(),
      }
    assert_simulator_state_cleared(backend)
    assert backend.home_calls == 2

  async def test_public_channel_jogs_preserve_other_axes(self) -> None:
    backend = RecordingOpentronsSimulator()
    liquid_handler = make_liquid_handler(backend)

    async with liquid_handler:
      await backend.move_channel_x(channel=0, x=50)
      assert backend._positions["sim-left"] == Coordinate(50, 0, 0)
      await backend.move_channel_y(channel=0, y=60)
      assert backend._positions["sim-left"] == Coordinate(50, 60, 0)
      await backend.move_channel_z(channel=0, z=70)
      assert backend._positions["sim-left"] == Coordinate(50, 60, 70)
