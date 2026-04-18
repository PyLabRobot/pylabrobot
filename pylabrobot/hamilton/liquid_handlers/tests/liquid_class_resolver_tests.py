"""Tests for :mod:`liquid_class_resolver`."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pylabrobot.hamilton.liquid_handlers.liquid_class import HamiltonLiquidClass
from pylabrobot.hamilton.liquid_handlers.liquid_class_resolver import (
  corrected_volumes_for_ops,
  resolve_hamilton_liquid_classes,
)
from pylabrobot.hamilton.liquid_handlers.star.liquid_classes import get_star_liquid_class
from pylabrobot.resources.hamilton import HamiltonTip, TipPickupMethod, TipSize
from pylabrobot.resources.liquid import Liquid


def _hlc(**overrides: float) -> HamiltonLiquidClass:
  base = dict(
    curve={0.0: 0.0, 1000.0: 1000.0},
    aspiration_flow_rate=1.0,
    aspiration_mix_flow_rate=2.0,
    aspiration_air_transport_volume=3.0,
    aspiration_blow_out_volume=4.0,
    aspiration_swap_speed=5.0,
    aspiration_settling_time=6.0,
    aspiration_over_aspirate_volume=7.0,
    aspiration_clot_retract_height=8.0,
    dispense_flow_rate=9.0,
    dispense_mode=0.0,
    dispense_mix_flow_rate=10.0,
    dispense_air_transport_volume=11.0,
    dispense_blow_out_volume=12.0,
    dispense_swap_speed=13.0,
    dispense_settling_time=14.0,
    dispense_stop_flow_rate=15.0,
    dispense_stop_back_volume=16.0,
  )
  base.update(overrides)
  return HamiltonLiquidClass(**base)


def test_resolve_explicit_returns_copy():
  h = _hlc()
  out = resolve_hamilton_liquid_classes([h], [], jet=False, blow_out=False)
  assert out == [h]
  out[0] = None  # type: ignore[assignment]
  assert h is not None


def test_resolve_auto_non_hamilton_tip_is_none():
  op = SimpleNamespace(tip=object())
  assert resolve_hamilton_liquid_classes(None, [op], jet=False, blow_out=False) == [None]


def test_resolve_auto_hamilton_tip_matches_get_star():
  tip = HamiltonTip(
    has_filter=False,
    total_tip_length=59.9,
    maximal_volume=300.0,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )
  op = SimpleNamespace(tip=tip)
  a = resolve_hamilton_liquid_classes(None, [op], jet=False, blow_out=False)[0]
  b = get_star_liquid_class(
    tip_volume=tip.maximal_volume,
    is_core=False,
    is_tip=True,
    has_filter=tip.has_filter,
    liquid=Liquid.WATER,
    jet=False,
    blow_out=False,
  )
  assert a is not None and b is not None
  assert a.aspiration_flow_rate == b.aspiration_flow_rate


def test_resolve_custom_lookup():
  custom = _hlc(aspiration_flow_rate=99.0)

  def lookup(**kwargs):  # noqa: ARG001
    return custom

  tip = HamiltonTip(
    has_filter=False,
    total_tip_length=59.9,
    maximal_volume=300.0,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )
  op = SimpleNamespace(tip=tip)
  got = resolve_hamilton_liquid_classes(None, [op], jet=False, blow_out=False, lookup=lookup)[0]
  assert got is not None
  assert got.aspiration_flow_rate == 99.0


def test_corrected_volumes_respects_disable_and_none_hlc():
  ops = [SimpleNamespace(volume=100.0)]
  hlc = _hlc(curve={0.0: 0.0, 100.0: 200.0, 200.0: 400.0})
  assert corrected_volumes_for_ops(ops, [hlc], None) == [200.0]
  assert corrected_volumes_for_ops(ops, [hlc], [True]) == [100.0]
  assert corrected_volumes_for_ops(ops, [None], None) == [100.0]


def test_corrected_volumes_length_mismatch_raises():
  with pytest.raises(ValueError, match="hlcs length"):
    corrected_volumes_for_ops([SimpleNamespace(volume=1.0)], [])
