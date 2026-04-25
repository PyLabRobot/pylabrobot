"""PIPBackend for the Tecan EVO with syringe-based LiHa.

Translates v1b1 PIP operations (Pickup, TipDrop, Aspiration, Dispense) into
Tecan firmware commands via the TecanEVODriver and LiHa firmware wrapper.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Tuple, TypeVar, Union

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.pip_backend import PIPBackend
from pylabrobot.capabilities.liquid_handling.standard import (
  Aspiration,
  Dispense,
  Mix,
  Pickup,
  TipDrop,
)
from pylabrobot.legacy.liquid_handling.liquid_classes.tecan import (
  TecanLiquidClass,
  get_liquid_class,
)
from pylabrobot.resources import Liquid, Resource, TecanPlate, TecanTipRack, Tip
from pylabrobot.resources.tecan.tip_creators import TecanTip

from pylabrobot.tecan.evo.driver import TecanEVODriver
from pylabrobot.tecan.evo.errors import TecanError
from pylabrobot.tecan.evo.firmware import LiHa
from pylabrobot.tecan.evo.firmware.arm_base import EVOArm
from pylabrobot.tecan.evo.params import TecanPIPParams

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Module identifiers
LIHA = "C5"
MCA = "W1"


class EVOPIPBackend(PIPBackend):
  """PIPBackend for the Tecan EVO with syringe-based LiHa.

  Conversion factors for syringe dilutors (XP2000/XP6000):
    - Volume: 3 full plunger steps per uL
    - Speed: 6 half-steps/sec per uL/s
  """

  STEPS_PER_UL = 3.0
  SPEED_FACTOR = 6.0

  def __init__(
    self,
    driver: TecanEVODriver,
    deck: Resource,
    diti_count: int = 0,
  ):
    """Create a new EVO PIP backend.

    Args:
      driver: The TecanEVODriver that owns the USB connection.
      deck: The deck resource (for coordinate calculations).
      diti_count: Number of channels configured for disposable tips.
    """
    self._driver = driver
    self._deck = deck
    self.diti_count = diti_count

    self._num_channels: Optional[int] = None
    self._x_range: Optional[int] = None
    self._y_range: Optional[int] = None
    self._z_range: Optional[int] = None
    self._z_traversal_height = 210  # mm

    self.liha: Optional[LiHa] = None

  @property
  def num_channels(self) -> int:
    if self._num_channels is None:
      raise RuntimeError("Not yet set up. Call setup() first.")
    return self._num_channels

  async def _on_setup(self) -> None:
    """Initialize LiHa arm: PIA, query ranges, init plungers."""
    # Setup arm (PIA + BMX)
    await self._setup_arm(LIHA)

    self.liha = LiHa(self._driver, LIHA)  # type: ignore[arg-type]
    await self.liha.position_initialization_x()

    self._num_channels = await self.liha.report_number_tips()
    self._x_range = await self.liha.report_x_param(5)
    self._y_range = (await self.liha.report_y_param(5))[0]
    self._z_range = (await self.liha.report_z_param(5))[0]

    # Initialize plungers (assumes wash station at rail 1)
    await self.liha.set_z_travel_height([self._z_range] * self.num_channels)
    await self.liha.position_absolute_all_axis(45, 1031, 90, [1200] * self.num_channels)
    await self.liha.initialize_plunger(self._bin_use_channels(list(range(self.num_channels))))
    await self.liha.position_valve_logical([1] * self.num_channels)
    await self.liha.move_plunger_relative([100] * self.num_channels)
    await self.liha.position_valve_logical([0] * self.num_channels)
    await self.liha.set_end_speed_plunger([1800] * self.num_channels)
    await self.liha.move_plunger_relative([-100] * self.num_channels)
    await self.liha.position_absolute_all_axis(45, 1031, 90, [self._z_range] * self.num_channels)
    logger.info("LiHa initialized: %d channels, z_range=%d", self._num_channels, self._z_range)

  async def _setup_arm(self, module: str) -> bool:
    """Send PIA + BMX to initialize an arm module."""
    arm = EVOArm(self._driver, module)  # type: ignore[arg-type]
    try:
      if module == MCA:
        await arm.position_init_bus()
      await arm.position_init_all()
    except TecanError as e:
      if e.error_code == 5:
        return False
      raise
    if module != MCA:
      await arm.set_bus_mode(2)
    return True

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    return isinstance(tip, TecanTip)

  async def request_tip_presence(self) -> List[Optional[bool]]:
    """Query tip mounted status for each channel via RTS firmware command."""
    assert self.liha is not None
    statuses = await self.liha.read_tip_status()
    result: List[Optional[bool]] = [None] * self.num_channels
    for i in range(min(len(statuses), self.num_channels)):
      result[i] = statuses[i]
    return result

  # ============== Utility methods ==============

  def _first_valid(self, lst: List[Optional[T]]) -> Tuple[Optional[T], int]:
    for i, v in enumerate(lst):
      if v is not None:
        return v, i
    return None, -1

  def _bin_use_channels(self, use_channels: List[int]) -> int:
    b = 0
    for channel in use_channels:
      b += 1 << channel
    return b

  def _get_ys(self, ops: Sequence[Union[Aspiration, Dispense, Pickup, TipDrop]]) -> int:
    """Get Y-spacing from plate well pitch or resource size."""
    par = ops[0].resource.parent
    if hasattr(par, "item_dy"):
      return int(par.item_dy * 10)  # type: ignore[union-attr]
    return int(ops[0].resource.get_absolute_size_y() * 10)

  def _liha_positions(
    self,
    ops: Sequence[Union[Aspiration, Dispense, Pickup, TipDrop]],
    use_channels: List[int],
  ) -> Tuple[List[Optional[int]], List[Optional[int]], Dict[str, List[Optional[int]]]]:
    """Compute X, Y, Z positions for LiHa operations."""
    assert self._z_range is not None

    x_positions: List[Optional[int]] = [None] * self.num_channels
    y_positions: List[Optional[int]] = [None] * self.num_channels
    z_positions: Dict[str, List[Optional[int]]] = {
      "travel": [None] * self.num_channels,
      "start": [None] * self.num_channels,
      "dispense": [None] * self.num_channels,
      "max": [None] * self.num_channels,
    }

    z_range = self._z_range

    def get_z_position(z: float, z_off: float, tip_length: int) -> int:
      return int(z_range - z + z_off * 10 + tip_length)

    for i, (op, channel) in enumerate(zip(ops, use_channels)):
      location = op.resource.get_location_wrt(self._deck) + op.resource.center()
      x_positions[channel] = int((location.x - 100 + op.offset.x) * 10)
      y_positions[channel] = int((346.5 - location.y + op.offset.y) * 10)

      par = op.resource.parent
      if not isinstance(par, (TecanPlate, TecanTipRack)):
        raise ValueError(f"Operation is not supported by resource {par}.")

      tip_length = int(op.tip.total_tip_length * 10)

      if isinstance(op, (Aspiration, Dispense)):
        z_positions["travel"][channel] = round(self._z_traversal_height * 10)

      z_positions["start"][channel] = get_z_position(
        par.z_start, par.get_location_wrt(self._deck).z + op.offset.z, tip_length
      )
      z_positions["dispense"][channel] = get_z_position(
        par.z_dispense, par.get_location_wrt(self._deck).z + op.offset.z, tip_length
      )
      z_positions["max"][channel] = get_z_position(
        par.z_max, par.get_location_wrt(self._deck).z + op.offset.z, tip_length
      )

    return x_positions, y_positions, z_positions

  # ============== Parameter computation ==============

  def _aspirate_airgap(
    self,
    use_channels: List[int],
    tecan_liquid_classes: List[Optional[TecanLiquidClass]],
    airgap: str,
  ) -> Tuple[List[Optional[int]], List[Optional[int]], List[Optional[int]]]:
    pvl: List[Optional[int]] = [None] * self.num_channels
    sep: List[Optional[int]] = [None] * self.num_channels
    ppr: List[Optional[int]] = [None] * self.num_channels

    for i, channel in enumerate(use_channels):
      tlc = tecan_liquid_classes[i]
      assert tlc is not None
      pvl[channel] = 0
      if airgap == "lag":
        sep[channel] = int(tlc.aspirate_lag_speed * self.SPEED_FACTOR)
        ppr[channel] = int(tlc.aspirate_lag_volume * self.STEPS_PER_UL)
      elif airgap == "tag":
        sep[channel] = int(tlc.aspirate_tag_speed * self.SPEED_FACTOR)
        ppr[channel] = int(tlc.aspirate_tag_volume * self.STEPS_PER_UL)

    return pvl, sep, ppr

  def _liquid_detection(
    self,
    use_channels: List[int],
    tecan_liquid_classes: List[Optional[TecanLiquidClass]],
  ) -> Tuple[List[Optional[int]], List[Optional[int]], List[Optional[int]]]:
    ssl: List[Optional[int]] = [None] * self.num_channels
    sdl: List[Optional[int]] = [None] * self.num_channels
    sbl: List[Optional[int]] = [None] * self.num_channels

    for i, channel in enumerate(use_channels):
      tlc = tecan_liquid_classes[i]
      assert tlc is not None
      ssl[channel] = int(tlc.lld_speed * 10)
      sdl[channel] = int(tlc.lld_distance * 10)
      sbl[channel] = int(tlc.aspirate_lld_offset * 10)

    return ssl, sdl, sbl

  def _aspirate_action(
    self,
    ops: Sequence[Aspiration],
    use_channels: List[int],
    tecan_liquid_classes: List[Optional[TecanLiquidClass]],
    zadd: List[Optional[int]],
  ) -> Tuple[
    List[Optional[int]],
    List[Optional[int]],
    List[Optional[int]],
    List[Optional[int]],
    List[Optional[int]],
  ]:
    ssz: List[Optional[int]] = [None] * self.num_channels
    sep: List[Optional[int]] = [None] * self.num_channels
    stz: List[Optional[int]] = [-z if z else None for z in zadd]
    mtr: List[Optional[int]] = [None] * self.num_channels
    ssz_r: List[Optional[int]] = [None] * self.num_channels

    for i, channel in enumerate(use_channels):
      tlc = tecan_liquid_classes[i]
      z = zadd[channel]
      assert tlc is not None and z is not None
      flow_rate = ops[i].flow_rate or tlc.aspirate_speed
      sep[channel] = int(flow_rate * self.SPEED_FACTOR)
      ssz[channel] = round(z * flow_rate / ops[i].volume)
      volume = tlc.compute_corrected_volume(ops[i].volume)
      mtr[channel] = round(volume * self.STEPS_PER_UL)
      ssz_r[channel] = int(tlc.aspirate_retract_speed * 10)

    return ssz, sep, stz, mtr, ssz_r

  def _dispense_action(
    self,
    ops: Sequence[Dispense],
    use_channels: List[int],
    tecan_liquid_classes: List[Optional[TecanLiquidClass]],
  ) -> Tuple[
    List[Optional[int]],
    List[Optional[int]],
    List[Optional[int]],
    List[Optional[int]],
  ]:
    sep: List[Optional[int]] = [None] * self.num_channels
    spp: List[Optional[int]] = [None] * self.num_channels
    stz: List[Optional[int]] = [None] * self.num_channels
    mtr: List[Optional[int]] = [None] * self.num_channels

    for i, channel in enumerate(use_channels):
      tlc = tecan_liquid_classes[i]
      assert tlc is not None
      flow_rate = ops[i].flow_rate or tlc.dispense_speed
      sep[channel] = int(flow_rate * self.SPEED_FACTOR)
      spp[channel] = int(tlc.dispense_breakoff * self.SPEED_FACTOR)
      stz[channel] = 0
      volume = (
        tlc.compute_corrected_volume(ops[i].volume)
        + tlc.aspirate_lag_volume
        + tlc.aspirate_tag_volume
      )
      mtr[channel] = -round(volume * self.STEPS_PER_UL)

    return sep, spp, stz, mtr

  def _get_liquid_classes(
    self, ops: Sequence[Union[Aspiration, Dispense]]
  ) -> List[Optional[TecanLiquidClass]]:
    return [
      get_liquid_class(
        target_volume=op.volume,
        liquid_class=Liquid.WATER,
        tip_type=op.tip.tip_type,
      )
      if isinstance(op.tip, TecanTip)
      else None
      for op in ops
    ]

  # ============== Mixing and blow-out ==============

  async def _perform_mix(self, mix: Mix, use_channels: List[int]) -> None:
    """Perform mix cycles at the current tip position.

    Repeatedly aspirates and dispenses the mix volume.

    Args:
      mix: Mix parameters (volume, repetitions, flow_rate).
      use_channels: Which channels to mix on.
    """
    assert self.liha is not None

    pvl: List[Optional[int]] = [None] * self.num_channels
    sep: List[Optional[int]] = [None] * self.num_channels
    ppr_asp: List[Optional[int]] = [None] * self.num_channels
    ppr_disp: List[Optional[int]] = [None] * self.num_channels

    for channel in use_channels:
      pvl[channel] = 0  # outlet
      sep[channel] = int(mix.flow_rate * self.SPEED_FACTOR)
      steps = int(mix.volume * self.STEPS_PER_UL)
      ppr_asp[channel] = steps
      ppr_disp[channel] = -steps

    await self.liha.position_valve_logical(pvl)
    await self.liha.set_end_speed_plunger(sep)

    for _ in range(mix.repetitions):
      await self.liha.move_plunger_relative(ppr_asp)
      await self.liha.move_plunger_relative(ppr_disp)

  async def _perform_blow_out(self, ops: List[Dispense], use_channels: List[int]) -> None:
    """Push extra air volume after dispense to expel remaining liquid.

    Args:
      ops: Dispense operations (checks blow_out_air_volume).
      use_channels: Channels to blow out.
    """
    assert self.liha is not None

    pvl: List[Optional[int]] = [None] * self.num_channels
    sep: List[Optional[int]] = [None] * self.num_channels
    ppr: List[Optional[int]] = [None] * self.num_channels
    has_blowout = False

    for i, channel in enumerate(use_channels):
      bov = ops[i].blow_out_air_volume
      if bov is not None and bov > 0:
        has_blowout = True
        pvl[channel] = 0  # outlet
        sep[channel] = int(100 * self.SPEED_FACTOR)
        ppr[channel] = -int(bov * self.STEPS_PER_UL)

    if has_blowout:
      await self.liha.position_valve_logical(pvl)
      await self.liha.set_end_speed_plunger(sep)
      await self.liha.move_plunger_relative(ppr)

  # ============== PIPBackend implementation ==============

  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    assert self.liha is not None and self._z_range is not None

    assert min(use_channels) >= self.num_channels - self.diti_count, (
      f"DiTis can only be configured for the last {self.diti_count} channels"
    )

    x_positions, y_positions, z_positions = self._liha_positions(ops, use_channels)
    ys = self._get_ys(ops)
    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None

    await self.liha.set_z_travel_height([self._z_range] * self.num_channels)
    await self.liha.position_absolute_all_axis(
      x, y - yi * ys, ys, [self._z_range] * self.num_channels
    )

    # Aspirate small air gap before tip pickup
    pvl: List[Optional[int]] = [None] * self.num_channels
    sep: List[Optional[int]] = [None] * self.num_channels
    ppr: List[Optional[int]] = [None] * self.num_channels
    for channel in use_channels:
      pvl[channel] = 0
      sep[channel] = int(70 * self.SPEED_FACTOR)
      ppr[channel] = int(10 * self.STEPS_PER_UL)
    await self.liha.position_valve_logical(pvl)
    await self.liha.set_end_speed_plunger(sep)
    await self.liha.move_plunger_relative(ppr)

    first_z_start, _ = self._first_valid(z_positions["start"])
    assert first_z_start is not None
    await self.liha.get_disposable_tip(
      self._bin_use_channels(use_channels), first_z_start - 227, 210
    )

  async def drop_tips(
    self,
    ops: List[TipDrop],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    assert self.liha is not None and self._z_range is not None

    assert min(use_channels) >= self.num_channels - self.diti_count, (
      f"DiTis can only be configured for the last {self.diti_count} channels"
    )

    x_positions, y_positions, _ = self._liha_positions(ops, use_channels)
    ys = self._get_ys(ops)
    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None

    await self.liha.set_z_travel_height([self._z_range] * self.num_channels)
    await self.liha.position_absolute_all_axis(
      x, y - yi * ys, ys, [self._z_range] * self.num_channels
    )
    await self.liha.drop_disposable_tip(self._bin_use_channels(use_channels), discard_height=0)

  async def aspirate(
    self,
    ops: List[Aspiration],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    assert self.liha is not None and self._z_range is not None

    x_positions, y_positions, z_positions = self._liha_positions(ops, use_channels)
    tecan_liquid_classes = self._get_liquid_classes(ops)

    ys = self._get_ys(ops)
    zadd: List[Optional[int]] = [0] * self.num_channels
    for i, channel in enumerate(use_channels):
      par = ops[i].resource.parent
      if par is None:
        continue
      if not isinstance(par, TecanPlate):
        raise ValueError(f"Operation is not supported by resource {par}.")
      zadd[channel] = round(ops[i].volume / par.area * 10)

    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None

    await self.liha.set_z_travel_height([self._z_range] * self.num_channels)
    await self.liha.position_absolute_all_axis(
      x,
      y - yi * ys,
      ys,
      [z if z else self._z_range for z in z_positions["travel"]],
    )

    # Leading airgap
    pvl, sep, ppr = self._aspirate_airgap(use_channels, tecan_liquid_classes, "lag")
    if any(ppr):
      await self.liha.position_valve_logical(pvl)
      await self.liha.set_end_speed_plunger(sep)
      await self.liha.move_plunger_relative(ppr)

    # Liquid level detection
    if any(tlc.aspirate_lld if tlc is not None else None for tlc in tecan_liquid_classes):
      tlc, _ = self._first_valid(tecan_liquid_classes)
      assert tlc is not None
      lld_proc = tlc.lld_mode
      lld_sense = tlc.lld_conductivity
      if isinstance(backend_params, TecanPIPParams):
        if backend_params.liquid_detection_proc is not None:
          lld_proc = backend_params.liquid_detection_proc
        if backend_params.liquid_detection_sense is not None:
          lld_sense = backend_params.liquid_detection_sense
      await self.liha.set_detection_mode(lld_proc, lld_sense)
      ssl, sdl, sbl = self._liquid_detection(use_channels, tecan_liquid_classes)
      await self.liha.set_search_speed(ssl)
      await self.liha.set_search_retract_distance(sdl)
      await self.liha.set_search_z_start(z_positions["start"])
      await self.liha.set_search_z_max(list(z if z else self._z_range for z in z_positions["max"]))
      await self.liha.set_search_submerge(sbl)
      shz = [min(z for z in z_positions["travel"] if z)] * self.num_channels
      await self.liha.set_z_travel_height(shz)
      await self.liha.move_detect_liquid(self._bin_use_channels(use_channels), zadd)
      await self.liha.set_z_travel_height([self._z_range] * self.num_channels)

    # Aspirate + retract
    zadd = [min(z, 32) if z else None for z in zadd]
    ssz, sep, stz, mtr, ssz_r = self._aspirate_action(ops, use_channels, tecan_liquid_classes, zadd)
    await self.liha.set_slow_speed_z(ssz)
    await self.liha.set_end_speed_plunger(sep)
    await self.liha.set_tracking_distance_z(stz)
    await self.liha.move_tracking_relative(mtr)
    await self.liha.set_slow_speed_z(ssz_r)
    await self.liha.move_absolute_z(z_positions["start"])

    # Trailing airgap
    pvl, sep, ppr = self._aspirate_airgap(use_channels, tecan_liquid_classes, "tag")
    await self.liha.position_valve_logical(pvl)
    await self.liha.set_end_speed_plunger(sep)
    await self.liha.move_plunger_relative(ppr)

    # Post-aspirate mix
    mix_channels = [ch for ch, op in zip(use_channels, ops) if op.mix is not None]
    if mix_channels:
      mix_op = next(op for op in ops if op.mix is not None)
      assert mix_op.mix is not None
      await self._perform_mix(mix_op.mix, mix_channels)

  async def dispense(
    self,
    ops: List[Dispense],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    assert self.liha is not None and self._z_range is not None

    x_positions, y_positions, z_positions = self._liha_positions(ops, use_channels)
    tecan_liquid_classes = self._get_liquid_classes(ops)

    ys = self._get_ys(ops)
    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None

    await self.liha.set_z_travel_height([z if z else self._z_range for z in z_positions["travel"]])
    await self.liha.position_absolute_all_axis(
      x,
      y - yi * ys,
      ys,
      [z if z else self._z_range for z in z_positions["dispense"]],
    )

    sep, spp, stz, mtr = self._dispense_action(ops, use_channels, tecan_liquid_classes)
    await self.liha.set_end_speed_plunger(sep)
    await self.liha.set_stop_speed_plunger(spp)
    await self.liha.set_tracking_distance_z(stz)
    await self.liha.move_tracking_relative(mtr)

    # Blow-out
    await self._perform_blow_out(ops, use_channels)

    # Tip touch
    if isinstance(backend_params, TecanPIPParams) and backend_params.tip_touch:
      touch_offset = int(backend_params.tip_touch_offset_y * 10)
      await self.liha.position_absolute_all_axis(
        x,
        y - yi * ys + touch_offset,
        ys,
        [z if z else self._z_range for z in z_positions["dispense"]],
      )
      await self.liha.position_absolute_all_axis(
        x,
        y - yi * ys,
        ys,
        [z if z else self._z_range for z in z_positions["dispense"]],
      )

    # Post-dispense mix
    mix_channels = [ch for ch, op in zip(use_channels, ops) if op.mix is not None]
    if mix_channels:
      mix_op = next(op for op in ops if op.mix is not None)
      assert mix_op.mix is not None
      await self._perform_mix(mix_op.mix, mix_channels)
