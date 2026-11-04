"""Resource-free integration tests for the legacy OT-2 chatterbox."""

import threading
from typing import Dict, List, Tuple

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.liquid_handling import LiquidHandler
from pylabrobot.legacy.liquid_handling.backends import (
  OpentronsOT2ChatterboxBackend,
  OpentronsOT2Simulator,
)
from pylabrobot.legacy.liquid_handling.backends.opentrons_backend import OpentronsOT2Backend
from pylabrobot.resources import (
  does_tip_tracking,
  does_volume_tracking,
  set_tip_tracking,
  set_volume_tracking,
)
from pylabrobot.resources.celltreat import celltreat_96_wellplate_350uL_Fb
from pylabrobot.resources.opentrons import OTDeck, opentrons_96_filtertiprack_20ul
from pylabrobot.testing.concurrency import AnyioTestBase

Call = Tuple[str, tuple, dict]
RequestFailure = Tuple[str, str]


def names(backend: OpentronsOT2ChatterboxBackend) -> List[str]:
  """Return the recorded command names in call order."""
  return [call[0] for call in backend.commands]


def make_liquid_handler() -> Tuple[LiquidHandler, OpentronsOT2ChatterboxBackend, OTDeck]:
  """Build a resource-free chatterbox composition."""
  backend = OpentronsOT2ChatterboxBackend(
    left_pipette_name="p20_single_gen2",
    right_pipette_name="p20_single_gen2",
    verbose=False,
  )
  deck = OTDeck()
  return LiquidHandler(backend=backend, deck=deck), backend, deck


class TestOpentronsChatterbox(AnyioTestBase):
  """Run the real OT-2 operations against only the explicit recorder."""

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: object,
  ) -> None:
    old_tip_tracking = does_tip_tracking()
    old_volume_tracking = does_volume_tracking()
    set_tip_tracking(True)
    stack.callback(set_tip_tracking, old_tip_tracking)
    set_volume_tracking(True)
    stack.callback(set_volume_tracking, old_volume_tracking)

  async def test_scope_and_representative_operations_only_record_commands(self) -> None:
    liquid_handler, backend, deck = make_liquid_handler()

    async with liquid_handler:
      tips = opentrons_96_filtertiprack_20ul(name="tips")
      deck.assign_child_at_slot(tips, slot=1)
      plate = celltreat_96_wellplate_350uL_Fb(name="plate")
      deck.assign_child_at_slot(plate, slot=2)
      plate.get_well("A1").tracker.set_volume(15)

      await liquid_handler.pick_up_tips(tips["A1"])
      await liquid_handler.aspirate(plate["A1"], vols=[10])
      await liquid_handler.dispense(plate["B1"], vols=[10])
      await liquid_handler.discard_tips()

      assert backend.num_channels == 2
      assert names(backend) == [
        "runs.create",
        "set_run",
        "lh.add_mounted_pipettes",
        "health.get",
        "health.home",
        "labware.define",
        "labware.add",
        "lh.pick_up_tip",
        "lh.move_arm",
        "lh.aspirate_in_place",
        "lh.move_arm",
        "lh.move_arm",
        "lh.dispense_in_place",
        "lh.move_arm",
        "lh.move_to_addressable_area_for_drop_tip",
        "lh.drop_tip_in_place",
      ]

    assert names(backend)[-1] == "requestor.post"
    assert backend.left_pipette is None
    assert backend.right_pipette is None
    assert backend.ot_api_version is None

  async def test_parent_calls_use_workers_while_run_selection_stays_async(self) -> None:
    liquid_handler, backend, _ = make_liquid_handler()
    async_thread = threading.get_ident()

    async with liquid_handler:
      await backend.list_connected_modules()

    assert len(backend.commands) == len(backend.command_threads)
    for command, thread_id in zip(backend.commands, backend.command_threads):
      if command[0] == "set_run":
        assert thread_id == async_thread
      else:
        assert thread_id != async_thread

  @pytest.mark.parametrize(
    ("failures", "expected_cleanup"),
    [
      ({}, ["requestor.post"]),
      (
        {("POST", "/runs/chatterbox-run/cancel"): RuntimeError("first failed")},
        ["requestor.post", "requestor.post"],
      ),
      (
        {
          ("POST", "/runs/chatterbox-run/cancel"): RuntimeError("first failed"),
          ("POST", "/runs/chatterbox-run/actions/cancel"): RuntimeError("second failed"),
        },
        ["requestor.post", "requestor.post", "requestor.delete"],
      ),
    ],
  )
  async def test_cleanup_fallbacks_only_advance_after_failure(
    self,
    failures: Dict[RequestFailure, BaseException],
    expected_cleanup: List[str],
  ) -> None:
    liquid_handler, backend, _ = make_liquid_handler()
    backend.request_failures.update(failures)

    async with liquid_handler:
      pass

    assert names(backend)[-len(expected_cleanup) :] == expected_cleanup

  async def test_all_cleanup_failures_preserve_the_final_error(self) -> None:
    liquid_handler, backend, _ = make_liquid_handler()
    first = RuntimeError("first failed")
    second = RuntimeError("second failed")
    final = RuntimeError("delete failed")
    backend.request_failures.update(
      {
        ("POST", "/runs/chatterbox-run/cancel"): first,
        ("POST", "/runs/chatterbox-run/actions/cancel"): second,
        ("DELETE", "/runs/chatterbox-run"): final,
      }
    )

    with pytest.raises(RuntimeError) as error_info:
      async with liquid_handler:
        pass

    assert error_info.value is final
    assert final.__context__ is second
    assert second.__context__ is first
    assert names(backend)[-3:] == [
      "requestor.post",
      "requestor.post",
      "requestor.delete",
    ]

  async def test_body_cancellation_runs_recorder_cleanup(self) -> None:
    liquid_handler, backend, _ = make_liquid_handler()
    body_started = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    finished = anyio.Event()
    cancellations: List[BaseException] = []

    async def run_body() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with liquid_handler:
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
    assert names(backend)[-1] == "requestor.post"
    assert backend.left_pipette is None
    assert backend.right_pipette is None

  async def test_sequential_reuse_records_complete_lifecycles(self) -> None:
    liquid_handler, backend, _ = make_liquid_handler()

    async with liquid_handler.lifespan(skip_home=True):
      pass
    async with liquid_handler.lifespan(skip_home=True):
      pass

    cycle = [
      "runs.create",
      "set_run",
      "lh.add_mounted_pipettes",
      "health.get",
      "requestor.post",
    ]
    assert names(backend) == cycle + cycle

  async def test_unknown_pipette_name_raises(self) -> None:
    with pytest.raises(ValueError, match="Unknown left pipette"):
      OpentronsOT2ChatterboxBackend(left_pipette_name="not_a_pipette")

  async def test_serialize_preserves_configuration(self) -> None:
    backend = OpentronsOT2ChatterboxBackend(
      left_pipette_name="p20_single_gen2",
      right_pipette_name=None,
      host="dry-run",
      port=1234,
      verbose=False,
    )

    assert backend.serialize() == {
      "type": "OpentronsOT2ChatterboxBackend",
      "left_pipette_name": "p20_single_gen2",
      "right_pipette_name": None,
      "host": "dry-run",
      "port": 1234,
    }

  async def test_matches_simulator_tracked_outcome(self) -> None:
    async def run_protocol(backend: OpentronsOT2Backend) -> Tuple[bool, float, float]:
      deck = OTDeck()
      liquid_handler = LiquidHandler(backend=backend, deck=deck)
      async with liquid_handler:
        tips = opentrons_96_filtertiprack_20ul(name="tips")
        deck.assign_child_at_slot(tips, slot=1)
        plate = celltreat_96_wellplate_350uL_Fb(name="plate")
        deck.assign_child_at_slot(plate, slot=2)
        plate.get_well("A1").tracker.set_volume(15)
        await liquid_handler.pick_up_tips(tips["A1"])
        await liquid_handler.aspirate(plate["A1"], vols=[10])
        await liquid_handler.dispense(plate["B1"], vols=[10])
        return (
          liquid_handler.head[0].has_tip,
          round(plate.get_well("A1").tracker.get_used_volume(), 3),
          round(plate.get_well("B1").tracker.get_used_volume(), 3),
        )

    simulator_outcome = await run_protocol(
      OpentronsOT2Simulator(
        left_pipette_name="p20_single_gen2",
        right_pipette_name="p20_single_gen2",
      )
    )
    chatterbox_outcome = await run_protocol(
      OpentronsOT2ChatterboxBackend(
        left_pipette_name="p20_single_gen2",
        right_pipette_name="p20_single_gen2",
        verbose=False,
      )
    )

    assert simulator_outcome == (True, 5.0, 10.0)
    assert chatterbox_outcome == simulator_outcome
