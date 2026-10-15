"""Tests for :mod:`liquid_class_resolver`."""

from __future__ import annotations

from typing import Any, Dict

import pytest

from pylabrobot.hamilton.liquid_class_resolver import (
  ASPIRATE_CLASS_ATTRIBUTES,
  DISPENSE_CLASS_ATTRIBUTES,
  check_volume_arguments,
  from_class,
  get_volumes_and_classes,
)
from pylabrobot.hamilton.liquid_classes import HamiltonLiquidClass
from pylabrobot.resources.container import Container
from pylabrobot.resources.hamilton import HamiltonTip, TipPickupMethod, TipSize
from pylabrobot.resources.liquid import Liquid


def _hlc(**overrides: Any) -> HamiltonLiquidClass:
  base: Dict[str, Any] = dict(
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


def _tip(maximal_volume: float = 300.0) -> HamiltonTip:
  return HamiltonTip(
    name="tip",
    has_filter=False,
    size_z=59.9,
    maximal_volume=maximal_volume,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def test_from_class_takes_what_is_given_else_the_class_else_none():
  hlc = _hlc()
  assert from_class("flow_rates", [3.0], 1, [hlc], ASPIRATE_CLASS_ATTRIBUTES) == [3.0]
  assert from_class("flow_rates", None, 1, [hlc], ASPIRATE_CLASS_ATTRIBUTES) == [1.0]
  assert from_class("flow_rates", None, 1, [hlc], DISPENSE_CLASS_ATTRIBUTES) == [9.0]
  assert from_class("flow_rates", None, 1, None, DISPENSE_CLASS_ATTRIBUTES) is None
  assert from_class("flow_rates", [None], 1, [hlc], ASPIRATE_CLASS_ATTRIBUTES) == [1.0]
  assert from_class("flow_rates", [None], 1, None, ASPIRATE_CLASS_ATTRIBUTES) == [None]
  with pytest.raises(ValueError, match="one entry per container"):
    from_class("flow_rates", [1.0, 2.0], 1, [hlc], ASPIRATE_CLASS_ATTRIBUTES)


def test_check_volume_arguments_wants_one_of_volumes_and_piston_volumes():
  check_volume_arguments([1.0], None, None, "drawn")
  check_volume_arguments(None, [1.0], None, "drawn")
  for volumes, pistons, classes in (
    ([1.0], [1.0], None),
    (None, None, None),
    (None, [1.0], [_hlc()]),
  ):
    with pytest.raises(ValueError):
      check_volume_arguments(volumes, pistons, classes, "drawn")


def test_get_volumes_and_classes_looks_up_corrects_and_rounds():
  hlc = _hlc(curve={0.0: 0.0, 100.0: 100.123456})
  keys: list = []

  def lookup(**kwargs):
    keys.append(kwargs)
    return hlc

  well = Container(name="w", size_x=1, size_y=1, size_z=1)
  liquid, piston, classes = get_volumes_and_classes(
    [well], [0], [_tip()], [100.0], None, None, [True], [False], lookup=lookup
  )
  assert (liquid, piston, classes) == ([100.0], [100.12], [hlc])
  assert keys[0]["jet"] is True and keys[0]["blow_out"] is False
  assert keys[0]["liquid"] == Liquid.WATER and keys[0]["tip_volume"] == 300.0
  assert get_volumes_and_classes([well], [0], [_tip()], None, [5.0], None, [False], [False]) == (
    [5.0],
    [5.0],
    None,
  )
  with pytest.raises(ValueError, match="no liquid class is known for channel 1's tip on w"):
    get_volumes_and_classes(
      [well], [1], [_tip()], [1.0], None, None, [False], [False], lookup=lambda **kw: None
    )
