"""Tests for Head8.

Covers core logic that must survive refactors:
  - _resolve_probe_positions: pitch validation for 96-well columns
  - _validate_container_span: minimum Y-span check for trough path
  - all-8-channel enforcement (ganged head constraint)
  - V1/V2 aspirate/dispense dispatch and LLD/TADM kwargs
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from pylabrobot.hamilton.prep import PrepDriver, PrepSimulationDriver
from pylabrobot.hamilton.prep.driver import prep_commands as PrepCmd
from pylabrobot.hamilton.prep.driver.features.head8 import PROBE_PITCH_MM, Head8
from pylabrobot.hamilton.prep.driver.features.pipettes import (
  Pipettes,
  _build_pipettor_gantry_move_parameters,
  _get_container_segments,
)
from pylabrobot.hamilton.prep.driver.simulator import RECORDING_PREP_HEAD8
from pylabrobot.resources import Coordinate, Resource
from pylabrobot.resources.corning.axygen.plates import Cor_Axy_96_wellplate_500uL_Ub
from pylabrobot.resources.hamilton import PrepDeck, hamilton_96_tiprack_50uL_NTR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_deck():
  deck = PrepDeck()
  tip_rack = deck[3] = hamilton_96_tiprack_50uL_NTR(name="ntr", with_tips=True)
  src_plate = deck[0] = Cor_Axy_96_wellplate_500uL_Ub("src")
  dst_plate = deck[4] = Cor_Axy_96_wellplate_500uL_Ub("dst")
  return deck, tip_rack, src_plate, dst_plate


def _make_head8() -> Head8:
  return Head8(None)  # type: ignore[arg-type]


def _record_send(prep: PrepDriver) -> tuple[list[Any], Any]:
  captured: list[Any] = []
  orig_send = prep.send_command

  async def recording(command, **kw):
    captured.append(command)
    return await orig_send(command, **kw)

  prep.send_command = recording  # type: ignore[method-assign, assignment]
  return captured, orig_send


# ---------------------------------------------------------------------------
# Group 1: _resolve_probe_positions / _validate_container_span
# ---------------------------------------------------------------------------


def test_resolve_probe_positions_valid_96well_column():
  """96-well column A→H has exactly 9mm pitch — should pass and return expected Ys."""
  plate = Cor_Axy_96_wellplate_500uL_Ub("p")
  plate.location = Coordinate(100, 200, 0)
  wells = plate.column(0)

  be = _make_head8()
  ys = be._resolve_probe_positions(wells)

  assert len(ys) == 8
  ref_y = wells[0].get_absolute_location("c", "c", "cavity_bottom").y
  for i, y in enumerate(ys):
    assert y == pytest.approx(ref_y - i * PROBE_PITCH_MM), (
      f"probe {i}: expected {ref_y - i * PROBE_PITCH_MM}, got {y}"
    )


def test_resolve_probe_positions_misaligned_raises():
  """Wells not at 9mm pitch must raise ValueError with a descriptive message."""
  plate = Cor_Axy_96_wellplate_500uL_Ub("p")
  plate.location = Coordinate(100, 200, 0)
  col = plate.column(0)
  # Swap rows 0 and 1 — now the pitch from well[0] to well[1] is wrong.
  bad_wells = [col[1], col[0]] + list(col[2:])

  be = _make_head8()
  with pytest.raises(ValueError, match="9.0 mm probe pitch"):
    be._resolve_probe_positions(bad_wells)


def test_validate_container_span_sufficient():
  """Container wider than 63mm passes without error."""
  plate = Cor_Axy_96_wellplate_500uL_Ub("p")
  # Cor_Axy_96 is 85.48mm in Y — well above 63mm minimum.
  be = _make_head8()
  be._validate_container_span(plate)  # should not raise


def test_validate_container_span_too_narrow():
  """Container narrower than 63mm raises ValueError."""
  narrow = Resource("narrow_container", size_x=100.0, size_y=40.0, size_z=10.0)
  be = _make_head8()
  with pytest.raises(ValueError, match="too narrow"):
    be._validate_container_span(narrow)


# ---------------------------------------------------------------------------
# Group 2: all-8-channel enforcement + Head8 wiring
# ---------------------------------------------------------------------------


def test_partial_channel_pickup_raises_value_error():
  """Head8 rejects pick_up_tips with fewer than all 8 channels."""

  async def _run() -> None:
    deck, tip_rack, _, _ = _make_deck()
    p = PrepSimulationDriver(deck=deck, declared_configuration_json=RECORDING_PREP_HEAD8)
    await p.setup()
    assert p.head8 is not None

    spots = tip_rack.column(1)[4:]  # E2, F2, G2, H2
    with pytest.raises(ValueError, match="fully-ganged head"):
      await p.head8.pick_up_tips(spots, use_channels=(4, 5, 6, 7))

    await p.stop()

  asyncio.run(_run())


def test_head8_full_flow():
  """pick_up_tips → aspirate → dispense → drop_tips on the simulator."""

  async def _run() -> None:
    deck, tip_rack, src_plate, dst_plate = _make_deck()
    p = PrepSimulationDriver(deck=deck, declared_configuration_json=RECORDING_PREP_HEAD8)
    await p.setup()
    assert p.head8 is not None

    spots = tip_rack.column(0)
    await p.head8.pick_up_tips(spots)
    await p.head8.aspirate(wells=src_plate.column(0), volume=20)
    await p.head8.dispense(wells=dst_plate.column(0), volume=20)
    await p.head8.drop_tips(spots)

    await p.stop()

  asyncio.run(_run())


def test_head8_tips_move_between_spots_and_shafts():
  """With tip tracking on, each tip moves off its spot onto its probe's shaft, and back."""
  from pylabrobot.resources.tip_tracking import set_tip_tracking

  async def _run() -> None:
    set_tip_tracking(True)
    try:
      deck, tip_rack, _, _ = _make_deck()
      p = PrepSimulationDriver(deck=deck, declared_configuration_json=RECORDING_PREP_HEAD8)
      await p.setup()
      assert p.head8 is not None
      spots = tip_rack.column(0)
      tips = [s.tip for s in spots]
      assert all(t is not None for t in tips)
      await p.head8.pick_up_tips(spots)
      assert all(s.tip is None for s in spots)
      assert p.head8.get_mounted_tips() == tips
      assert all(t.parent is p.head8.shaft(i) for i, t in enumerate(tips))
      await p.head8.drop_tips(spots)
      assert [s.tip for s in spots] == tips
      assert all(t is None for t in p.head8.get_mounted_tips())
      await p.stop()
    finally:
      set_tip_tracking(False)

  asyncio.run(_run())


def test_build_pipettor_gantry_move_parameters_maps_rear_front():
  m = _build_pipettor_gantry_move_parameters(10.0, [0, 1], [20.0, 30.0], [40.0, 50.0])
  assert m.gantry_x_position == 10.0
  assert len(m.axis_parameters) == 2
  assert m.axis_parameters[0].channel == PrepCmd.ChannelIndex.RearChannel
  assert m.axis_parameters[0].y_position == 20.0
  assert m.axis_parameters[0].z_position == 40.0
  assert m.axis_parameters[1].channel == PrepCmd.ChannelIndex.FrontChannel
  assert m.axis_parameters[1].y_position == 30.0
  assert m.axis_parameters[1].z_position == 50.0


def test_head8_move_to_position_sends_mph_wire_commands():
  """Head8.move_to_position sends MphMoveToPosition / ViaLane."""

  async def _run() -> None:
    deck, _, _, _ = _make_deck()
    p = PrepSimulationDriver(deck=deck, declared_configuration_json=RECORDING_PREP_HEAD8)
    await p.setup()
    assert p.head8 is not None

    captured, _ = _record_send(p)

    await p.head8.move_to_position(11.0, 22.5, 99.0)
    direct = [c for c in captured if isinstance(c, PrepCmd.MphMoveToPosition)]
    assert len(direct) == 1
    assert direct[0].x_position == 11.0
    assert direct[0].y_position == 22.5
    assert direct[0].z_position == 99.0

    await p.head8.move_to_position(1.0, 2.0, 3.0, via_lane=True)
    lanes = [c for c in captured if isinstance(c, PrepCmd.MphMoveToPositionViaLane)]
    assert len(lanes) == 1
    assert lanes[0].x_position == 1.0 and lanes[0].y_position == 2.0 and lanes[0].z_position == 3.0

    await p.stop()

  asyncio.run(_run())


def test_pick_up_tips_sends_the_move_over_the_spots_then_the_pickup():
  """The head is taken over the spots before the one MphPickupTips, so it descends straight down."""

  async def _run() -> None:
    deck, tip_rack, _, _ = _make_deck()
    p = PrepSimulationDriver(deck=deck, declared_configuration_json=RECORDING_PREP_HEAD8)
    await p.setup()
    assert p.head8 is not None

    captured, _ = _record_send(p)

    await p.head8.pick_up_tips(tip_rack.column(0))

    pickups = [i for i, c in enumerate(captured) if isinstance(c, PrepCmd.MphPickupTips)]
    assert len(pickups) == 1
    assert any(isinstance(c, PrepCmd.MphMoveToPosition) for c in captured[: pickups[0]])

    await p.stop()

  asyncio.run(_run())


def test_head8_partial_channel_aspirate_raises_value_error():
  """Head8 rejects aspirate with fewer than all 8 channels."""

  async def _run() -> None:
    deck, tip_rack, src_plate, _ = _make_deck()
    p = PrepSimulationDriver(deck=deck, declared_configuration_json=RECORDING_PREP_HEAD8)
    await p.setup()
    assert p.head8 is not None

    spots = tip_rack.column(0)
    await p.head8.pick_up_tips(spots)

    with pytest.raises(ValueError, match="fully-ganged head"):
      await p.head8.aspirate(
        wells=src_plate.column(0)[:4],
        volume=10,
        use_channels=(0, 1, 2, 3),
      )

    await p.stop()

  asyncio.run(_run())


# ---------------------------------------------------------------------------
# Group 3: V2 aspirate/dispense dispatch
# ---------------------------------------------------------------------------


def test_head8_v2_aspirate_sends_mphaspiratenolldmonitoring2():
  """Simulator default (use_v1=False) → V2 command class is sent."""

  async def _run() -> None:
    deck, tip_rack, src_plate, _ = _make_deck()
    p = PrepSimulationDriver(deck=deck, declared_configuration_json=RECORDING_PREP_HEAD8)
    await p.setup()
    assert p.head8 is not None

    captured, _ = _record_send(p)

    spots = tip_rack.column(0)
    await p.head8.pick_up_tips(spots)
    await p.head8.aspirate(wells=src_plate.column(0), volume=10)

    asp_cmds = [c for c in captured if isinstance(c, PrepCmd.MphAspirateNoLldMonitoring2)]
    v1_cmds = [
      c
      for c in captured
      if isinstance(c, PrepCmd.MphAspirateNoLldMonitoring)
      and not isinstance(c, PrepCmd.MphAspirateNoLldMonitoring2)
    ]
    assert len(asp_cmds) == 1, f"Expected 1 MphAspirateNoLldMonitoring2, got {len(asp_cmds)}"
    assert len(v1_cmds) == 0, "V1 aspirate command should not be sent when V2 is supported"
    assert len(asp_cmds[0].aspirate_parameters) == 1, (
      "MPH sends a single struct element (probe-0 reference); firmware drives all 8 probes"
    )

    await p.stop()

  asyncio.run(_run())


def test_head8_aspirate_container_segments_start_at_z_minimum():
  """With auto_container_geometry, segment 0 begins at the z_minimum the command sends."""

  async def _run() -> None:
    deck, tip_rack, src_plate, _ = _make_deck()
    p = PrepSimulationDriver(deck=deck, declared_configuration_json=RECORDING_PREP_HEAD8)
    await p.setup()
    assert p.head8 is not None

    captured, _ = _record_send(p)

    await p.head8.pick_up_tips(tip_rack.column(0))
    wells = src_plate.column(0)
    cavity_bottom_z = wells[0].get_location_wrt(deck, "c", "c", "cavity_bottom").z
    profile_top = sum(s.height for s in _get_container_segments(wells[0]))
    await p.head8.aspirate(
      wells=wells, volume=10, z_minimum=cavity_bottom_z + 1.5, auto_container_geometry=True
    )

    asp = [c for c in captured if isinstance(c, PrepCmd.MphAspirateNoLldMonitoring2)]
    params = asp[0].aspirate_parameters[0]
    assert params.common.z_minimum == pytest.approx(cavity_bottom_z + 1.5)
    sent_height = sum(s.height for s in params.container_description)
    assert sent_height == pytest.approx(profile_top - 1.5)

    await p.stop()

  asyncio.run(_run())


def test_head8_v2_dispense_sends_mphdispensetnolld2():
  """Simulator default (use_v1=False) → V2 dispense command class is sent."""

  async def _run() -> None:
    deck, tip_rack, src_plate, dst_plate = _make_deck()
    p = PrepSimulationDriver(deck=deck, declared_configuration_json=RECORDING_PREP_HEAD8)
    await p.setup()
    assert p.head8 is not None

    captured, _ = _record_send(p)

    spots = tip_rack.column(0)
    await p.head8.pick_up_tips(spots)
    await p.head8.aspirate(wells=src_plate.column(0), volume=10)
    await p.head8.dispense(wells=dst_plate.column(0), volume=10)

    disp_cmds = [c for c in captured if isinstance(c, PrepCmd.MphDispenseNoLld2)]
    v1_cmds = [
      c
      for c in captured
      if isinstance(c, PrepCmd.MphDispenseNoLld) and not isinstance(c, PrepCmd.MphDispenseNoLld2)
    ]
    assert len(disp_cmds) == 1, f"Expected 1 MphDispenseNoLld2, got {len(disp_cmds)}"
    assert len(v1_cmds) == 0, "V1 dispense command should not be sent when V2 is supported"

    await p.stop()

  asyncio.run(_run())


def test_head8_v1_fallback_when_use_v1_flag_set():
  """use_v1_aspirate_dispense=True → V1 command classes are sent for MPH too."""

  async def _run() -> None:
    deck, tip_rack, src_plate, dst_plate = _make_deck()
    p = PrepSimulationDriver(deck=deck, declared_configuration_json=RECORDING_PREP_HEAD8)
    await p.setup(use_v1_aspirate_dispense=True)
    assert p.head8 is not None

    captured, _ = _record_send(p)

    spots = tip_rack.column(0)
    await p.head8.pick_up_tips(spots)
    await p.head8.aspirate(wells=src_plate.column(0), volume=10)
    await p.head8.dispense(wells=dst_plate.column(0), volume=10)

    v2_asp = [c for c in captured if isinstance(c, PrepCmd.MphAspirateNoLldMonitoring2)]
    v2_disp = [c for c in captured if isinstance(c, PrepCmd.MphDispenseNoLld2)]
    v1_asp = [
      c
      for c in captured
      if isinstance(c, PrepCmd.MphAspirateNoLldMonitoring)
      and not isinstance(c, PrepCmd.MphAspirateNoLldMonitoring2)
    ]
    v1_disp = [
      c
      for c in captured
      if isinstance(c, PrepCmd.MphDispenseNoLld) and not isinstance(c, PrepCmd.MphDispenseNoLld2)
    ]

    assert len(v2_asp) == 0, "V2 aspirate should not be sent with use_v1=True"
    assert len(v2_disp) == 0, "V2 dispense should not be sent with use_v1=True"
    assert len(v1_asp) == 1, f"Expected 1 V1 aspirate, got {len(v1_asp)}"
    assert len(v1_disp) == 1, f"Expected 1 V1 dispense, got {len(v1_disp)}"

    await p.stop()

  asyncio.run(_run())


# ---------------------------------------------------------------------------
# Group 4: LLD and TADM dispatch
# ---------------------------------------------------------------------------


def test_head8_aspirate_tadm_sends_mphaspirate_tadm2():
  """tadm= kwargs → MphAspirateTadm2 (v2, no LLD)."""

  async def _run() -> None:
    deck, tip_rack, src_plate, _ = _make_deck()
    p = PrepSimulationDriver(deck=deck, declared_configuration_json=RECORDING_PREP_HEAD8)
    await p.setup()
    assert p.head8 is not None

    captured, _ = _record_send(p)

    spots = tip_rack.column(0)
    await p.head8.pick_up_tips(spots)
    await p.head8.aspirate(
      wells=src_plate.column(0),
      volume=10,
      tadm=PrepCmd.TadmParameters.default(),
    )

    tadm_cmds = [c for c in captured if isinstance(c, PrepCmd.MphAspirateTadm2)]
    assert len(tadm_cmds) == 1, f"Expected 1 MphAspirateTadm2, got {len(tadm_cmds)}"
    no_lld_cmds = [c for c in captured if isinstance(c, PrepCmd.MphAspirateNoLldMonitoring2)]
    assert len(no_lld_cmds) == 0, "NoLldMonitoring2 should not be sent when tadm= is set"

    await p.stop()

  asyncio.run(_run())


def test_head8_aspirate_clld_sends_mphaspirate_with_lld2():
  """lld_mode=CAPACITIVE → MphAspirateWithLld2 (v2, LLD, no TADM).

  Capacitive seeks leave pLLD on firmware defaults so the dispenser is not
  started at speed 0; cLLD stays explicit so detection still runs.
  """

  async def _run() -> None:
    deck, tip_rack, src_plate, _ = _make_deck()
    p = PrepSimulationDriver(deck=deck, declared_configuration_json=RECORDING_PREP_HEAD8)
    await p.setup()
    assert p.head8 is not None

    captured, _ = _record_send(p)

    spots = tip_rack.column(0)
    await p.head8.pick_up_tips(spots)
    await p.head8.aspirate(
      wells=src_plate.column(0),
      volume=10,
      lld_mode=Pipettes.LLDMode.CAPACITIVE,
    )

    lld_cmds = [c for c in captured if isinstance(c, PrepCmd.MphAspirateWithLld2)]
    assert len(lld_cmds) == 1, f"Expected 1 MphAspirateWithLld2, got {len(lld_cmds)}"
    params = lld_cmds[0].aspirate_parameters[0]
    assert params.p_lld.default_values is True
    assert params.c_lld.default_values is False
    assert params.c_lld.sensitivity == 3

    await p.stop()

  asyncio.run(_run())


def test_head8_aspirate_lld_and_tadm_sends_mphaspirate_with_lld_tadm2():
  """lld_mode=CAPACITIVE + tadm= → MphAspirateWithLldTadm2."""

  async def _run() -> None:
    deck, tip_rack, src_plate, _ = _make_deck()
    p = PrepSimulationDriver(deck=deck, declared_configuration_json=RECORDING_PREP_HEAD8)
    await p.setup()
    assert p.head8 is not None

    captured, _ = _record_send(p)

    spots = tip_rack.column(0)
    await p.head8.pick_up_tips(spots)
    await p.head8.aspirate(
      wells=src_plate.column(0),
      volume=10,
      lld_mode=Pipettes.LLDMode.CAPACITIVE,
      tadm=PrepCmd.TadmParameters.default(),
    )

    lld_tadm_cmds = [c for c in captured if isinstance(c, PrepCmd.MphAspirateWithLldTadm2)]
    assert len(lld_tadm_cmds) == 1, f"Expected 1 MphAspirateWithLldTadm2, got {len(lld_tadm_cmds)}"

    await p.stop()

  asyncio.run(_run())


def test_head8_dispense_lld_pressure_raises():
  """lld_mode=PRESSURE on dispense raises ValueError — pressure LLD needs aspiration."""

  async def _run() -> None:
    deck, tip_rack, src_plate, dst_plate = _make_deck()
    p = PrepSimulationDriver(deck=deck, declared_configuration_json=RECORDING_PREP_HEAD8)
    await p.setup()
    assert p.head8 is not None

    spots = tip_rack.column(0)
    await p.head8.pick_up_tips(spots)
    await p.head8.aspirate(wells=src_plate.column(0), volume=10)

    with pytest.raises(ValueError, match="PRESSURE"):
      await p.head8.dispense(
        wells=dst_plate.column(0),
        volume=10,
        lld_mode=Pipettes.LLDMode.PRESSURE,
      )

    await p.stop()

  asyncio.run(_run())


def test_head8_command_version_override_v1():
  """command_version='v1' per-call override forces v1 even when v2 is available."""

  async def _run() -> None:
    deck, tip_rack, src_plate, dst_plate = _make_deck()
    p = PrepSimulationDriver(deck=deck, declared_configuration_json=RECORDING_PREP_HEAD8)
    await p.setup()
    assert p.head8 is not None

    captured, _ = _record_send(p)

    spots = tip_rack.column(0)
    await p.head8.pick_up_tips(spots)
    await p.head8.aspirate(
      wells=src_plate.column(0),
      volume=10,
      command_version="v1",
    )
    await p.head8.dispense(
      wells=dst_plate.column(0),
      volume=10,
      command_version="v1",
    )

    v1_asp = [c for c in captured if type(c) is PrepCmd.MphAspirateNoLldMonitoring]
    v2_asp = [c for c in captured if isinstance(c, PrepCmd.MphAspirateNoLldMonitoring2)]
    v1_disp = [c for c in captured if type(c) is PrepCmd.MphDispenseNoLld]
    v2_disp = [c for c in captured if isinstance(c, PrepCmd.MphDispenseNoLld2)]

    assert len(v1_asp) == 1, f"Expected 1 V1 aspirate with override, got {len(v1_asp)}"
    assert len(v2_asp) == 0, "V2 aspirate must not be sent with command_version='v1'"
    assert len(v1_disp) == 1, f"Expected 1 V1 dispense with override, got {len(v1_disp)}"
    assert len(v2_disp) == 0, "V2 dispense must not be sent with command_version='v1'"

    await p.stop()

  asyncio.run(_run())
