"""PIPBackend for the Tecan EVO with syringe-based LiHa.

Translates v1b1 PIP operations (Pickup, TipDrop, Aspiration, Dispense) into
Tecan firmware commands via the TecanEVODriver. The backend is itself an
EVOArm, owning the LiHa firmware command vocabulary directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
from pylabrobot.tecan.evo.liquid_classes import (
  TecanLiquidClass,
  get_liquid_class,
)
from pylabrobot.resources import Liquid, Resource, TecanPlate, TecanTipRack, Tip
from pylabrobot.resources.tecan.tip_creators import TecanTip

from pylabrobot.tecan.evo.driver import TecanEVODriver
from pylabrobot.tecan.evo.errors import TecanError
from pylabrobot.tecan.evo.firmware.arm_base import EVOArm

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Module identifier
LIHA = "C5"


class EVOPIPBackend(PIPBackend, EVOArm):
  """PIPBackend for the Tecan EVO with syringe-based LiHa.

  The backend is itself an :class:`EVOArm`, owning the LiHa firmware command
  vocabulary directly (no separate wrapper) and sharing the cross-arm
  collision cache.

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
    EVOArm.__init__(self, driver, LIHA)
    self._deck = deck
    self.diti_count = diti_count

    self._num_channels: Optional[int] = None
    self._x_range: Optional[int] = None
    self._y_range: Optional[int] = None
    self._z_range: Optional[int] = None
    self._z_traversal_height = 210  # mm

  @property
  def num_channels(self) -> int:
    if self._num_channels is None:
      raise RuntimeError("Not yet set up. Call setup() first.")
    return self._num_channels

  # ============== LiHa firmware commands ==============

  async def initialize_plunger(self, tips: int) -> None:
    """Initializes plunger and valve drive.

    Args:
      tips: binary coded tip select
    """
    await self.driver.send_command(module=self.module, command="PID", params=[tips])

  async def report_z_param(self, param: int) -> List[int]:
    """Report current parameters for z-axis.

    Args:
      param: 0=position, 1=accel, 2=fast_speed, 3=init_speed, 4=init_offset,
             5=range, 6=encoder_deviation, 9=slow_speed, 10=scale, 11=target, 12=travel
    """
    resp: List[int] = (
      await self.driver.send_command(module=self.module, command="RPZ", params=[param])
    )["data"]
    return resp

  async def report_number_tips(self) -> int:
    """Report number of tips on arm."""
    resp: List[int] = (
      await self.driver.send_command(module=self.module, command="RNT", params=[1])
    )["data"]
    return resp[0]

  async def position_absolute_all_axis(self, x: int, y: int, ys: int, z: List[int]) -> None:
    """Position absolute for all LiHa axes.

    Args:
      x: absolute x position in 1/10 mm
      y: absolute y position in 1/10 mm
      ys: absolute y spacing in 1/10 mm (90-380)
      z: absolute z position in 1/10 mm for each channel

    Raises:
      TecanError: if moving to the target position causes a collision
    """
    cur_x = EVOArm._pos_cache.setdefault(self.module, await self.report_x_param(0))
    for module, pos in EVOArm._pos_cache.items():
      if module == self.module:
        continue
      if cur_x < x and cur_x < pos < x:
        raise TecanError("Invalid command (collision)", self.module, 2)
      if cur_x > x and cur_x > pos > x:
        raise TecanError("Invalid command (collision)", self.module, 2)
      if abs(pos - x) < 1500:
        raise TecanError("Invalid command (collision)", self.module, 2)

    await self.driver.send_command(
      module=self.module, command="PAA", params=list([x, y, ys] + z)
    )
    EVOArm._pos_cache[self.module] = x

  async def position_valve_logical(self, param: List[Optional[int]]) -> None:
    """Position valve logical for each channel.

    Args:
      param: 0=outlet, 1=inlet, 2=bypass
    """
    await self.driver.send_command(module=self.module, command="PVL", params=param)

  async def set_end_speed_plunger(self, speed: List[Optional[int]]) -> None:
    """Set end speed for plungers.

    Args:
      speed: half steps/sec per channel (5-6000)
    """
    await self.driver.send_command(module=self.module, command="SEP", params=speed)

  async def move_plunger_relative(self, rel: List[Optional[int]]) -> None:
    """Move plunger relative (positive=aspirate, negative=dispense).

    Args:
      rel: full steps per channel (-3150 to 3150)
    """
    await self.driver.send_command(module=self.module, command="PPR", params=rel)

  async def set_stop_speed_plunger(self, speed: List[Optional[int]]) -> None:
    """Set stop speed for plungers.

    Args:
      speed: half steps/sec per channel (50-2700)
    """
    await self.driver.send_command(module=self.module, command="SPP", params=speed)

  async def set_detection_mode(self, proc: int, sense: int) -> None:
    """Set liquid detection mode.

    Args:
      proc: detection procedure (7 = double detection sequential)
      sense: conductivity (1 = high)
    """
    await self.driver.send_command(module=self.module, command="SDM", params=[proc, sense])

  async def set_search_speed(self, speed: List[Optional[int]]) -> None:
    """Set search speed for liquid search commands.

    Args:
      speed: 1/10 mm/s per channel (1-1500)
    """
    await self.driver.send_command(module=self.module, command="SSL", params=speed)

  async def set_search_retract_distance(self, dist: List[Optional[int]]) -> None:
    """Set z-axis retract distance for liquid search commands.

    Args:
      dist: 1/10 mm per channel
    """
    await self.driver.send_command(module=self.module, command="SDL", params=dist)

  async def set_search_submerge(self, dist: List[Optional[int]]) -> None:
    """Set submerge for liquid search commands.

    Args:
      dist: 1/10 mm per channel (-1000 to z_range)
    """
    await self.driver.send_command(module=self.module, command="SBL", params=dist)

  async def set_search_z_start(self, z: List[Optional[int]]) -> None:
    """Set z-start for liquid search commands.

    Args:
      z: 1/10 mm per channel
    """
    await self.driver.send_command(module=self.module, command="STL", params=z)

  async def set_search_z_max(self, z: List[Optional[int]]) -> None:
    """Set z-max for liquid search commands.

    Args:
      z: 1/10 mm per channel
    """
    await self.driver.send_command(module=self.module, command="SML", params=z)

  async def set_z_travel_height(self, z: List[int]) -> None:
    """Set z-travel height.

    Args:
      z: travel heights in 1/10 mm per channel
    """
    await self.driver.send_command(module=self.module, command="SHZ", params=z)

  async def move_detect_liquid(self, channels: int, zadd: List[Optional[int]]) -> None:
    """Move tip, detect liquid, submerge.

    Args:
      channels: binary coded tip select
      zadd: distance to travel downwards in 1/10 mm per channel
    """
    await self.driver.send_command(
      module=self.module,
      command="MDT",
      params=[channels] + [None] * 3 + zadd,
    )

  async def set_slow_speed_z(self, speed: List[Optional[int]]) -> None:
    """Set slow speed for z.

    Args:
      speed: 1/10 mm/s per channel (1-4000)
    """
    await self.driver.send_command(module=self.module, command="SSZ", params=speed)

  async def set_tracking_distance_z(self, rel: List[Optional[int]]) -> None:
    """Set z-axis relative tracking distance for aspirate/dispense.

    Args:
      rel: 1/10 mm per channel (-2100 to 2100)
    """
    await self.driver.send_command(module=self.module, command="STZ", params=rel)

  async def move_tracking_relative(self, rel: List[Optional[int]]) -> None:
    """Move tracking relative (synchronous Z and plunger movement).

    Args:
      rel: full steps per channel (-3150 to 3150)
    """
    await self.driver.send_command(module=self.module, command="MTR", params=rel)

  async def move_absolute_z(self, z: List[Optional[int]]) -> None:
    """Position absolute with slow speed z-axis.

    Args:
      z: absolute position in 1/10 mm per channel
    """
    await self.driver.send_command(module=self.module, command="MAZ", params=z)

  async def get_disposable_tip(self, tips: int, z_start: int, z_search: int) -> None:
    """Pick up disposable tips.

    Args:
      tips: binary coded tip select
      z_start: position in 1/10 mm where searching begins
      z_search: search distance in 1/10 mm
    """
    await self.driver.send_command(
      module=self.module,
      command="AGT",
      params=[tips, z_start, z_search, 0],
    )

  async def position_plunger_absolute(self, positions: List[Optional[int]]) -> None:
    """Move plunger to absolute position (PPA).

    Args:
      positions: absolute plunger position in full steps per channel (0-3150).
                 0 = fully dispensed, 3150 = fully aspirated.
    """
    await self.driver.send_command(module=self.module, command="PPA", params=positions)

  async def set_disposable_tip_params(self, mode: int, z_discard: int, z_retract: int) -> None:
    """Set disposable tip discard parameters (SDT).

    Args:
      mode: 1 = discard in rack
      z_discard: Z discard distance in 1/10 mm
      z_retract: Z retract distance in 1/10 mm
    """
    await self.driver.send_command(
      module=self.module, command="SDT", params=[mode, z_discard, z_retract]
    )

  async def discard_disposable_tip_high(self, tips: int) -> None:
    """Discard tips at Z-axis initialization height.

    Args:
      tips: binary coded tip select
    """
    await self.driver.send_command(module=self.module, command="ADT", params=[tips])

  async def drop_disposable_tip(self, tips: int, discard_height: int) -> None:
    """Discard tips at variable height.

    Args:
      tips: binary coded tip select
      discard_height: 0=above tip rack, 1=in tip rack
    """
    await self.driver.send_command(
      module=self.module, command="AST", params=[tips, discard_height]
    )

  async def read_plunger_positions(self) -> List[int]:
    """Read current plunger positions (RPP).

    Returns:
      List of plunger positions in full steps per channel.
    """
    resp: List[int] = (
      await self.driver.send_command(module=self.module, command="RPP", params=[0])
    )["data"]
    return resp

  async def read_z_after_liquid_detection(self) -> List[int]:
    """Read Z values after liquid detection (RVZ).

    Returns:
      List of Z positions in 1/10 mm where liquid was detected, per channel.
    """
    resp: List[int] = (
      await self.driver.send_command(module=self.module, command="RVZ", params=[0])
    )["data"]
    return resp

  async def read_tip_status(self) -> List[bool]:
    """Read tip mounted status for each channel (RTS).

    Returns:
      List of booleans: True if tip is mounted on that channel.

    Note:
      Response format needs hardware validation. This implementation assumes
      the response is a per-channel list of int values (0=no tip, 1=tip).
    """
    resp: List[int] = (
      await self.driver.send_command(module=self.module, command="RTS", params=[0])
    )["data"]
    return [bool(v) for v in resp]

  async def position_absolute_z_bulk(self, z: List[Optional[int]]) -> None:
    """Position absolute Z for all channels simultaneously (PAZ).

    Unlike :meth:`move_absolute_z` which uses slow speed, PAZ uses fast speed.

    Args:
      z: absolute Z position in 1/10 mm per channel
    """
    await self.driver.send_command(module=self.module, command="PAZ", params=z)

  # ============== Setup ==============

  async def _on_setup(self) -> None:
    """Initialize LiHa arm: PIA, query ranges, init plungers."""
    # PIA + BMX
    await self.position_init_all()
    await self.set_bus_mode(2)
    await self.position_initialization_x()

    self._num_channels = await self.report_number_tips()
    self._x_range = await self.report_x_param(5)
    self._y_range = (await self.report_y_param(5))[0]
    self._z_range = (await self.report_z_param(5))[0]

    # Initialize plungers (assumes wash station at rail 1)
    await self.set_z_travel_height([self._z_range] * self.num_channels)
    await self.position_absolute_all_axis(45, 1031, 90, [1200] * self.num_channels)
    await self.initialize_plunger(self._bin_use_channels(list(range(self.num_channels))))
    await self.position_valve_logical([1] * self.num_channels)
    await self.move_plunger_relative([100] * self.num_channels)
    await self.position_valve_logical([0] * self.num_channels)
    await self.set_end_speed_plunger([1800] * self.num_channels)
    await self.move_plunger_relative([-100] * self.num_channels)
    await self.position_absolute_all_axis(45, 1031, 90, [self._z_range] * self.num_channels)
    logger.info("LiHa initialized: %d channels, z_range=%d", self._num_channels, self._z_range)

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    return isinstance(tip, TecanTip)

  async def request_tip_presence(self) -> List[Optional[bool]]:
    """Query tip mounted status for each channel via RTS firmware command."""
    statuses = await self.read_tip_status()
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

    await self.position_valve_logical(pvl)
    await self.set_end_speed_plunger(sep)

    for _ in range(mix.repetitions):
      await self.move_plunger_relative(ppr_asp)
      await self.move_plunger_relative(ppr_disp)

  async def _perform_blow_out(self, ops: List[Dispense], use_channels: List[int]) -> None:
    """Push extra air volume after dispense to expel remaining liquid.

    Args:
      ops: Dispense operations (checks blow_out_air_volume).
      use_channels: Channels to blow out.
    """

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
      await self.position_valve_logical(pvl)
      await self.set_end_speed_plunger(sep)
      await self.move_plunger_relative(ppr)

  # ============== PIPBackend implementation ==============

  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    assert self._z_range is not None

    assert min(use_channels) >= self.num_channels - self.diti_count, (
      f"DiTis can only be configured for the last {self.diti_count} channels"
    )

    x_positions, y_positions, z_positions = self._liha_positions(ops, use_channels)
    ys = self._get_ys(ops)
    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None

    await self.set_z_travel_height([self._z_range] * self.num_channels)
    await self.position_absolute_all_axis(
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
    await self.position_valve_logical(pvl)
    await self.set_end_speed_plunger(sep)
    await self.move_plunger_relative(ppr)

    first_z_start, _ = self._first_valid(z_positions["start"])
    assert first_z_start is not None
    await self.get_disposable_tip(
      self._bin_use_channels(use_channels), first_z_start - 227, 210
    )

  async def drop_tips(
    self,
    ops: List[TipDrop],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    assert self._z_range is not None

    assert min(use_channels) >= self.num_channels - self.diti_count, (
      f"DiTis can only be configured for the last {self.diti_count} channels"
    )

    x_positions, y_positions, _ = self._liha_positions(ops, use_channels)
    ys = self._get_ys(ops)
    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None

    await self.set_z_travel_height([self._z_range] * self.num_channels)
    await self.position_absolute_all_axis(
      x, y - yi * ys, ys, [self._z_range] * self.num_channels
    )
    await self.drop_disposable_tip(self._bin_use_channels(use_channels), discard_height=0)

  @dataclass(frozen=True)
  class TecanPIPParams(BackendParams):
    """EVO-specific parameters for PIP operations.

    Attributes:
      liquid_detection_proc: Detection procedure for LLD.
          7 = double detection sequential (default).
      liquid_detection_sense: Conductivity setting for LLD.
          1 = high conductivity (default).
      tip_touch: If True, touch vessel wall after dispense to remove droplet.
      tip_touch_offset_y: Y offset for tip touch in mm.
    """

    liquid_detection_proc: Optional[int] = None
    liquid_detection_sense: Optional[int] = None
    tip_touch: bool = False
    tip_touch_offset_y: float = 1.0

  async def aspirate(
    self,
    ops: List[Aspiration],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    assert self._z_range is not None

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

    await self.set_z_travel_height([self._z_range] * self.num_channels)
    await self.position_absolute_all_axis(
      x,
      y - yi * ys,
      ys,
      [z if z else self._z_range for z in z_positions["travel"]],
    )

    # Leading airgap
    pvl, sep, ppr = self._aspirate_airgap(use_channels, tecan_liquid_classes, "lag")
    if any(ppr):
      await self.position_valve_logical(pvl)
      await self.set_end_speed_plunger(sep)
      await self.move_plunger_relative(ppr)

    # Liquid level detection
    if any(tlc.aspirate_lld if tlc is not None else None for tlc in tecan_liquid_classes):
      tlc, _ = self._first_valid(tecan_liquid_classes)
      assert tlc is not None
      lld_proc = tlc.lld_mode
      lld_sense = tlc.lld_conductivity
      if isinstance(backend_params, EVOPIPBackend.TecanPIPParams):
        if backend_params.liquid_detection_proc is not None:
          lld_proc = backend_params.liquid_detection_proc
        if backend_params.liquid_detection_sense is not None:
          lld_sense = backend_params.liquid_detection_sense
      await self.set_detection_mode(lld_proc, lld_sense)
      ssl, sdl, sbl = self._liquid_detection(use_channels, tecan_liquid_classes)
      await self.set_search_speed(ssl)
      await self.set_search_retract_distance(sdl)
      await self.set_search_z_start(z_positions["start"])
      await self.set_search_z_max(list(z if z else self._z_range for z in z_positions["max"]))
      await self.set_search_submerge(sbl)
      shz = [min(z for z in z_positions["travel"] if z)] * self.num_channels
      await self.set_z_travel_height(shz)
      await self.move_detect_liquid(self._bin_use_channels(use_channels), zadd)
      await self.set_z_travel_height([self._z_range] * self.num_channels)

    # Aspirate + retract
    zadd = [min(z, 32) if z else None for z in zadd]
    ssz, sep, stz, mtr, ssz_r = self._aspirate_action(ops, use_channels, tecan_liquid_classes, zadd)
    await self.set_slow_speed_z(ssz)
    await self.set_end_speed_plunger(sep)
    await self.set_tracking_distance_z(stz)
    await self.move_tracking_relative(mtr)
    await self.set_slow_speed_z(ssz_r)
    await self.move_absolute_z(z_positions["start"])

    # Trailing airgap
    pvl, sep, ppr = self._aspirate_airgap(use_channels, tecan_liquid_classes, "tag")
    await self.position_valve_logical(pvl)
    await self.set_end_speed_plunger(sep)
    await self.move_plunger_relative(ppr)

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
    assert self._z_range is not None

    x_positions, y_positions, z_positions = self._liha_positions(ops, use_channels)
    tecan_liquid_classes = self._get_liquid_classes(ops)

    ys = self._get_ys(ops)
    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None

    await self.set_z_travel_height([z if z else self._z_range for z in z_positions["travel"]])
    await self.position_absolute_all_axis(
      x,
      y - yi * ys,
      ys,
      [z if z else self._z_range for z in z_positions["dispense"]],
    )

    sep, spp, stz, mtr = self._dispense_action(ops, use_channels, tecan_liquid_classes)
    await self.set_end_speed_plunger(sep)
    await self.set_stop_speed_plunger(spp)
    await self.set_tracking_distance_z(stz)
    await self.move_tracking_relative(mtr)

    # Blow-out
    await self._perform_blow_out(ops, use_channels)

    # Tip touch
    if isinstance(backend_params, EVOPIPBackend.TecanPIPParams) and backend_params.tip_touch:
      touch_offset = int(backend_params.tip_touch_offset_y * 10)
      await self.position_absolute_all_axis(
        x,
        y - yi * ys + touch_offset,
        ys,
        [z if z else self._z_range for z in z_positions["dispense"]],
      )
      await self.position_absolute_all_axis(
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
