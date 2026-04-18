"""Tests for NimbusPIPBackend liquid-class integration."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from pylabrobot.capabilities.liquid_handling.standard import Aspiration, Dispense
from pylabrobot.hamilton.liquid_handlers.liquid_class import HamiltonLiquidClass
from pylabrobot.hamilton.liquid_handlers.nimbus.commands import Aspirate, Dispense as DispenseCmd
from pylabrobot.hamilton.liquid_handlers.nimbus.pip_backend import (
  NimbusPIPAspirateParams,
  NimbusPIPDispenseParams,
  NimbusPIPBackend,
)
from pylabrobot.hamilton.liquid_handlers.star.pip_backend import STARPIPBackend
from pylabrobot.hamilton.tcp.packets import Address
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb
from pylabrobot.resources.hamilton import HamiltonTip, TipPickupMethod, TipSize
from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck
from pylabrobot.resources.hamilton.tip_racks import hamilton_96_tiprack_300uL


def _make_hlc_for_volume_double() -> HamiltonLiquidClass:
  """Correction curve: requested 100 µL liquid -> 200 µL piston displacement."""
  return HamiltonLiquidClass(
    curve={0.0: 0.0, 100.0: 200.0, 200.0: 400.0},
    aspiration_flow_rate=88.0,
    aspiration_mix_flow_rate=1.0,
    aspiration_air_transport_volume=2.0,
    aspiration_blow_out_volume=3.0,
    aspiration_swap_speed=4.0,
    aspiration_settling_time=5.0,
    aspiration_over_aspirate_volume=6.0,
    aspiration_clot_retract_height=7.0,
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


def test_nimbus_aspirate_volume_correction_and_param_override():
  async def _run() -> None:
    deck = NimbusDeck()
    tip_rack = hamilton_96_tiprack_300uL("tips")
    deck.assign_child_resource(tip_rack, rails=1)
    plate = Cor_96_wellplate_360ul_Fb("plate")
    deck.assign_child_resource(plate, rails=10)

    tip = HamiltonTip(
      has_filter=False,
      total_tip_length=59.9,
      maximal_volume=300.0,
      tip_size=TipSize.STANDARD_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    well = plate.get_well("A1")
    op = Aspiration(
      resource=well,
      offset=Coordinate.zero(),
      tip=tip,
      volume=100.0,
      flow_rate=None,
      liquid_height=None,
      blow_out_air_volume=None,
      mix=None,
    )

    driver = AsyncMock()
    driver.send_command = AsyncMock(return_value={"enabled": [True]})

    backend = NimbusPIPBackend(
      driver=driver,  # type: ignore[arg-type]
      deck=deck,
      address=Address(1, 1, 100),
      num_channels=8,
    )

    hlc = _make_hlc_for_volume_double()
    await backend.aspirate(
      [op],
      use_channels=[0],
      backend_params=NimbusPIPAspirateParams(
        hamilton_liquid_classes=[hlc],
        transport_air_volume=[42.0],
        disable_volume_correction=[False],
      ),
    )

    aspirate_cmds = [c for c in driver.send_command.call_args_list if isinstance(c.args[0], Aspirate)]
    assert len(aspirate_cmds) == 1
    cmd = aspirate_cmds[0].args[0]
    assert isinstance(cmd, Aspirate)
    # Volume correction: 100 µL target -> 200 µL internal; firmware units = round(µL * 10)
    assert cmd.aspirate_volume[0] == 2000
    # Explicit backend_params override liquid-class default for transport air
    assert cmd.transport_air_volume[0] == 420
    # Flow rate from liquid class when op.flow_rate is None
    assert cmd.aspiration_speed[0] == round(88.0 * 10)

  asyncio.run(_run())


def test_nimbus_aspirate_disable_volume_correction_keeps_nominal_volume():
  async def _run() -> None:
    deck = NimbusDeck()
    tip_rack = hamilton_96_tiprack_300uL("tips")
    deck.assign_child_resource(tip_rack, rails=1)
    plate = Cor_96_wellplate_360ul_Fb("plate")
    deck.assign_child_resource(plate, rails=10)

    tip = HamiltonTip(
      has_filter=False,
      total_tip_length=59.9,
      maximal_volume=300.0,
      tip_size=TipSize.STANDARD_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    well = plate.get_well("A1")
    op = Aspiration(
      resource=well,
      offset=Coordinate.zero(),
      tip=tip,
      volume=100.0,
      flow_rate=None,
      liquid_height=None,
      blow_out_air_volume=None,
      mix=None,
    )

    driver = AsyncMock()
    driver.send_command = AsyncMock(return_value={"enabled": [True]})

    backend = NimbusPIPBackend(
      driver=driver,  # type: ignore[arg-type]
      deck=deck,
      address=Address(1, 1, 100),
      num_channels=8,
    )

    hlc = _make_hlc_for_volume_double()
    await backend.aspirate(
      [op],
      use_channels=[0],
      backend_params=NimbusPIPAspirateParams(
        hamilton_liquid_classes=[hlc],
        disable_volume_correction=[True],
      ),
    )

    aspirate_cmds = [c for c in driver.send_command.call_args_list if isinstance(c.args[0], Aspirate)]
    cmd = aspirate_cmds[0].args[0]
    assert cmd.aspirate_volume[0] == 1000

  asyncio.run(_run())


def test_nimbus_aspirate_explicit_swap_speed_wire_units():
  """15 mm/s → 1500 (0.01 mm/s wire units) on channel 0."""

  async def _run() -> None:
    deck = NimbusDeck()
    tip_rack = hamilton_96_tiprack_300uL("tips")
    deck.assign_child_resource(tip_rack, rails=1)
    plate = Cor_96_wellplate_360ul_Fb("plate")
    deck.assign_child_resource(plate, rails=10)

    tip = HamiltonTip(
      has_filter=False,
      total_tip_length=59.9,
      maximal_volume=300.0,
      tip_size=TipSize.STANDARD_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    well = plate.get_well("A1")
    op = Aspiration(
      resource=well,
      offset=Coordinate.zero(),
      tip=tip,
      volume=50.0,
      flow_rate=None,
      liquid_height=None,
      blow_out_air_volume=None,
      mix=None,
    )

    driver = AsyncMock()
    driver.send_command = AsyncMock(return_value={"enabled": [True]})

    backend = NimbusPIPBackend(
      driver=driver,  # type: ignore[arg-type]
      deck=deck,
      address=Address(1, 1, 100),
      num_channels=8,
    )

    await backend.aspirate(
      [op],
      use_channels=[0],
      backend_params=NimbusPIPAspirateParams(swap_speed=[15.0]),
    )

    aspirate_cmds = [c for c in driver.send_command.call_args_list if isinstance(c.args[0], Aspirate)]
    cmd = aspirate_cmds[0].args[0]
    assert isinstance(cmd, Aspirate)
    assert cmd.swap_speed[0] == 1500

  asyncio.run(_run())


def test_nimbus_aspirate_no_hlc_uses_25_mm_s_default():
  """Explicit None liquid class → 25 mm/s → 2500 wire units (HamiltonTip still required for defaults)."""

  async def _run() -> None:
    deck = NimbusDeck()
    tip_rack = hamilton_96_tiprack_300uL("tips")
    deck.assign_child_resource(tip_rack, rails=1)
    plate = Cor_96_wellplate_360ul_Fb("plate")
    deck.assign_child_resource(plate, rails=10)

    tip = HamiltonTip(
      has_filter=False,
      total_tip_length=59.9,
      maximal_volume=300.0,
      tip_size=TipSize.STANDARD_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    well = plate.get_well("A1")
    op = Aspiration(
      resource=well,
      offset=Coordinate.zero(),
      tip=tip,
      volume=50.0,
      flow_rate=None,
      liquid_height=None,
      blow_out_air_volume=None,
      mix=None,
    )

    driver = AsyncMock()
    driver.send_command = AsyncMock(return_value={"enabled": [True]})

    backend = NimbusPIPBackend(
      driver=driver,  # type: ignore[arg-type]
      deck=deck,
      address=Address(1, 1, 100),
      num_channels=8,
    )

    await backend.aspirate(
      [op],
      use_channels=[0],
      backend_params=NimbusPIPAspirateParams(hamilton_liquid_classes=[None]),
    )

    aspirate_cmds = [c for c in driver.send_command.call_args_list if isinstance(c.args[0], Aspirate)]
    cmd = aspirate_cmds[0].args[0]
    assert isinstance(cmd, Aspirate)
    assert cmd.swap_speed[0] == 2500

  asyncio.run(_run())


def test_nimbus_dispense_no_hlc_uses_10_mm_s_default():
  """Explicit None liquid class → 10 mm/s → 1000 wire units."""

  async def _run() -> None:
    deck = NimbusDeck()
    tip_rack = hamilton_96_tiprack_300uL("tips")
    deck.assign_child_resource(tip_rack, rails=1)
    plate = Cor_96_wellplate_360ul_Fb("plate")
    deck.assign_child_resource(plate, rails=10)

    tip = HamiltonTip(
      has_filter=False,
      total_tip_length=59.9,
      maximal_volume=300.0,
      tip_size=TipSize.STANDARD_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    well = plate.get_well("A1")
    op = Dispense(
      resource=well,
      offset=Coordinate.zero(),
      tip=tip,
      volume=50.0,
      flow_rate=None,
      liquid_height=None,
      blow_out_air_volume=None,
      mix=None,
    )

    driver = AsyncMock()
    driver.send_command = AsyncMock(return_value={"enabled": [True]})

    backend = NimbusPIPBackend(
      driver=driver,  # type: ignore[arg-type]
      deck=deck,
      address=Address(1, 1, 100),
      num_channels=8,
    )

    await backend.dispense(
      [op],
      use_channels=[0],
      backend_params=NimbusPIPDispenseParams(hamilton_liquid_classes=[None]),
    )

    dispense_cmds = [
      c for c in driver.send_command.call_args_list if isinstance(c.args[0], DispenseCmd)
    ]
    cmd = dispense_cmds[0].args[0]
    assert isinstance(cmd, DispenseCmd)
    assert cmd.swap_speed[0] == 1000

  asyncio.run(_run())


def test_nimbus_aspirate_coerces_star_aspirate_params_swap_speed():
  """Overlapping fields from STARPIPBackend.AspirateParams are not dropped."""

  async def _run() -> None:
    deck = NimbusDeck()
    tip_rack = hamilton_96_tiprack_300uL("tips")
    deck.assign_child_resource(tip_rack, rails=1)
    plate = Cor_96_wellplate_360ul_Fb("plate")
    deck.assign_child_resource(plate, rails=10)

    tip = HamiltonTip(
      has_filter=False,
      total_tip_length=59.9,
      maximal_volume=300.0,
      tip_size=TipSize.STANDARD_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    well = plate.get_well("A1")
    op = Aspiration(
      resource=well,
      offset=Coordinate.zero(),
      tip=tip,
      volume=50.0,
      flow_rate=None,
      liquid_height=None,
      blow_out_air_volume=None,
      mix=None,
    )

    driver = AsyncMock()
    driver.send_command = AsyncMock(return_value={"enabled": [True]})

    backend = NimbusPIPBackend(
      driver=driver,  # type: ignore[arg-type]
      deck=deck,
      address=Address(1, 1, 100),
      num_channels=8,
    )

    star_params = STARPIPBackend.AspirateParams(swap_speed=[42.0])
    await backend.aspirate([op], use_channels=[0], backend_params=star_params)

    aspirate_cmds = [c for c in driver.send_command.call_args_list if isinstance(c.args[0], Aspirate)]
    cmd = aspirate_cmds[0].args[0]
    assert isinstance(cmd, Aspirate)
    assert cmd.swap_speed[0] == 4200

  asyncio.run(_run())
