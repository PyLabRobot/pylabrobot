"""PIPBackend for the Tecan EVO with Air LiHa (ZaapMotion controllers).

Overrides conversion factors and adds ZaapMotion boot exit, motor
configuration, and force mode wrapping around plunger operations.

See keyser-testing/AirLiHa_Investigation.md for reverse-engineering details.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.standard import (
  Aspiration,
  Dispense,
  Mix,
  Pickup,
  TipDrop,
)
from pylabrobot.resources import Resource, TecanTipRack

from pylabrobot.tecan.evo.driver import TecanEVODriver
from pylabrobot.tecan.evo.errors import TecanError
from pylabrobot.tecan.evo.firmware.arm_base import EVOArm
from pylabrobot.tecan.evo.firmware.zaapmotion import ZaapMotion
from pylabrobot.tecan.evo.pip_backend import EVOPIPBackend

logger = logging.getLogger(__name__)

# ZaapMotion motor configuration sequence, sent via transparent pipeline T2x.
# Captured from EVOware USB traffic (zaapmotiondriver.dll scan phase).
ZAAPMOTION_CONFIG = [
  "CFE 255,500",
  "CAD ADCA,0,12.5",
  "CAD ADCB,1,12.5",
  "EDF1",
  "EDF4",
  "CDO 11",
  "EDF5",
  "SIC 10,5",
  "SEA ADD,H,4,STOP,1,0,0",
  "CMTBLDC,1",
  "CETQEP2,256,R",
  "CECPOS,QEP2",
  "CECCUR,QEP2",
  "CEE OFF",
  "STL80",
  "SVL12,8,16",
  "SVL24,20,28",
  "SCL1,900,3.5",
  "SCE HOLD,500",
  "SCE MOVE,500",
  "CIR0",
  "PIDHOLD,D,1.2,1,-1,0.003,0,0,OFF",
  "PIDMOVE,D,0.8,1,-1,0.004,0,0,OFF",
  "PIDHOLD,Q,1.2,1,-1,0.003,0,0,OFF",
  "PIDMOVE,Q,0.8,1,-1,0.004,0,0,OFF",
  "PIDHOLD,POS,0.2,1,-1,0.02,4,0,OFF",
  "PIDMOVE,POS,0.35,1,-1,0.1,3,0,OFF",
  "PIDSPDELAY,0",
  "SFF 0.045,0.4,0.041",
  "SES 0",
  "SPO0",
  "SIA 0.01, 0.28, 0.0",
  "WRP",
]


class AirEVOPIPBackend(EVOPIPBackend):
  """PIPBackend for the Tecan EVO with Air LiHa (ZaapMotion controllers).

  Air displacement uses BLDC motor controllers instead of syringe dilutors.
  After power cycle, the ZaapMotion controllers boot into bootloader mode
  and require firmware exit + motor configuration before PIA can succeed.

  Conversion factors for ZaapMotion (air displacement):
    - Volume: 106.4 full plunger steps per uL
    - Speed: 213 half-steps/sec per uL/s
  """

  STEPS_PER_UL = 106.4
  SPEED_FACTOR = 213.0

  # ZaapMotion force ramp values
  SFR_ACTIVE = 133120
  SFR_IDLE = 3752
  SDP_DEFAULT = 1400

  def __init__(
    self,
    driver: TecanEVODriver,
    deck: Resource,
    diti_count: int = 0,
  ):
    super().__init__(driver=driver, deck=deck, diti_count=diti_count)
    self.zaap: Optional[ZaapMotion] = None
    self._agt_z_start: Optional[int] = None  # set during pickup, used to validate drop

  def _apply_calibration_offsets(
    self,
    x: int,
    y: int,
    ops: list,
  ) -> tuple:
    """Apply per-labware X/Y calibration offsets if defined."""
    par = ops[0].resource.parent
    # Walk up to find the labware with offsets (plate or tip rack)
    while par is not None:
      if hasattr(par, "x_offset"):
        x += getattr(par, "x_offset", 0)
        y += getattr(par, "y_offset", 0)
        break
      par = getattr(par, "parent", None)
    return x, y

  async def _on_setup(self) -> None:
    """Configure ZaapMotion controllers, then run standard LiHa init."""

    # Check if already initialized (skip ZaapMotion config + PIA)
    if await self._is_initialized():
      logger.info("Axes already initialized — skipping ZaapMotion config + PIA.")
      await self._setup_quick()
      return

    logger.info("Running full Air LiHa setup...")
    await self._configure_zaapmotion()
    await self._setup_safety_module()

    # ZaapMotion SDO config (from EVOware: sent right before PIA)
    assert self.zaap is not None
    try:
      await self.zaap.set_sdo("11,1")
    except TecanError:
      pass

    # Standard LiHa init (PIA, plunger init, etc.)
    await super()._on_setup()

  async def _is_initialized(self) -> bool:
    """Check if LiHa axes are already initialized."""
    try:
      arm = EVOArm(self._driver, "C5")  # type: ignore[arg-type]
      err = await arm.read_error_register(0)
      err = str(err)  # may be int if all digits
      # A = init failed (1), G = not initialized (7)
      if err and not any(c in ("A", "G") for c in err):
        return True
    except (TecanError, TimeoutError):
      pass
    return False

  async def _setup_quick(self) -> None:
    """Fast setup when axes are already initialized."""
    from pylabrobot.tecan.evo.firmware import LiHa

    self.liha = LiHa(self._driver, "C5")  # type: ignore[arg-type]
    self.zaap = ZaapMotion(self._driver)  # type: ignore[arg-type]
    self._num_channels = await self.liha.report_number_tips()
    self._x_range = await self.liha.report_x_param(5)
    self._y_range = (await self.liha.report_y_param(5))[0]
    self._z_range = (await self.liha.report_z_param(5))[0]
    logger.info("Quick setup complete: %d channels, z_range=%d", self._num_channels, self._z_range)

  async def _configure_zaapmotion(self) -> None:
    """Exit boot mode and configure all 8 ZaapMotion motor controllers."""
    zaap = ZaapMotion(self._driver)  # type: ignore[arg-type]
    all_failed_tips = []
    for tip in range(8):
      # Check current mode
      try:
        firmware = await zaap.read_firmware_version(tip)
      except TecanError:
        firmware = ""

      if "BOOT" in str(firmware):
        logger.info("ZaapMotion tip %d in boot mode, sending exit command", tip + 1)
        await zaap.exit_boot_mode(tip)
        await asyncio.sleep(1)

        # Verify transition
        try:
          firmware = await zaap.read_firmware_version(tip)
        except TecanError:
          firmware = ""

        if "BOOT" in str(firmware):
          raise TecanError(f"ZaapMotion tip {tip + 1} failed to exit boot mode", "C5", 1)

      # Check if already configured
      try:
        await zaap.read_config_status(tip)
        logger.info("ZaapMotion tip %d already configured, skipping", tip + 1)
        continue
      except TecanError:
        pass

      # Send motor configuration
      logger.info("Configuring ZaapMotion tip %d (%d commands)", tip + 1, len(ZAAPMOTION_CONFIG))
      failures = 0
      for cmd in ZAAPMOTION_CONFIG:
        try:
          await zaap.configure_motor(tip, cmd)
        except TecanError as e:
          failures += 1
          logger.warning("ZaapMotion tip %d config '%s' failed: %s", tip + 1, cmd, e)

      if failures == len(ZAAPMOTION_CONFIG):
        all_failed_tips.append(tip + 1)

    if all_failed_tips:
      raise TecanError(
        f"ZaapMotion controllers not responding (tips {all_failed_tips}). "
        "Power cycle the EVO and try again.",
        "C5",
        5,
      )

    self.zaap = zaap

  async def _setup_safety_module(self) -> None:
    """Send safety module commands to enable motor power."""
    try:
      await self._driver.send_command("O1", command="SPN")
      await self._driver.send_command("O1", command="SPS3")
    except TecanError as e:
      logger.warning("Safety module command failed: %s", e)

  # ============== ZaapMotion force mode ==============

  async def _zaapmotion_force_on(self) -> None:
    """Enable ZaapMotion force mode before plunger operations."""
    assert self.zaap is not None
    for tip in range(8):
      await self.zaap.set_force_ramp(tip, self.SFR_ACTIVE)
    for tip in range(8):
      await self.zaap.set_force_mode(tip)

  async def _zaapmotion_force_off(self) -> None:
    """Restore ZaapMotion to idle after plunger operations."""
    assert self.zaap is not None
    for tip in range(8):
      await self.zaap.set_force_ramp(tip, self.SFR_IDLE)
    for tip in range(8):
      await self.zaap.set_default_position(tip, self.SDP_DEFAULT)

  # ============== Force-mode overrides for mixing and blow-out ==============

  async def _perform_mix(self, mix: Mix, use_channels: List[int]) -> None:
    await self._zaapmotion_force_on()
    await super()._perform_mix(mix, use_channels)
    await self._zaapmotion_force_off()

  async def _perform_blow_out(self, ops: List[Dispense], use_channels: List[int]) -> None:
    await self._zaapmotion_force_on()
    await super()._perform_blow_out(ops, use_channels)
    await self._zaapmotion_force_off()

  # ============== Override operations with force mode ==============

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

    # Use _liha_positions for X/Y only; Z comes from tip rack directly
    x_positions, y_positions, _ = self._liha_positions(ops, use_channels)
    ys = self._get_ys(ops)
    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None

    x, y_adj = self._apply_calibration_offsets(x, y - yi * ys, ops)
    logger.info("pick_up_tips: X=%d Y=%d ys=%d (taught tip top: X=3893 Y=146)", x, y_adj, ys)

    await self.liha.set_z_travel_height([self._z_range] * self.num_channels)
    await self.liha.position_absolute_all_axis(x, y_adj, ys, [self._z_range] * self.num_channels)

    # Aspirate small air gap with force mode
    pvl: List[Optional[int]] = [None] * self.num_channels
    sep: List[Optional[int]] = [None] * self.num_channels
    ppr: List[Optional[int]] = [None] * self.num_channels
    for channel in use_channels:
      pvl[channel] = 0
      sep[channel] = int(70 * self.SPEED_FACTOR)
      ppr[channel] = int(10 * self.STEPS_PER_UL)
    await self._zaapmotion_force_on()
    await self.liha.position_valve_logical(pvl)
    await self.liha.set_end_speed_plunger(sep)
    await self.liha.move_plunger_relative(ppr)
    await self._zaapmotion_force_off()

    # AGT using tip rack z_start directly
    par = ops[0].resource.parent
    assert isinstance(par, TecanTipRack), f"Expected TecanTipRack, got {type(par)}"
    agt_z_start = int(par.z_start)
    agt_z_search = abs(int(par.z_max - par.z_start))
    logger.info("pick_up_tips AGT: z_start=%d z_search=%d", agt_z_start, agt_z_search)
    await self.liha.get_disposable_tip(
      self._bin_use_channels(use_channels), agt_z_start, agt_z_search
    )
    self._agt_z_start = agt_z_start

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

    x, y_adj = self._apply_calibration_offsets(x, y - yi * ys, ops)
    logger.info("drop_tips: X=%d Y=%d ys=%d", x, y_adj, ys)

    await self.liha.set_z_travel_height([self._z_range] * self.num_channels)
    await self.liha.position_absolute_all_axis(x, y_adj, ys, [self._z_range] * self.num_channels)

    # Empty plunger before discard
    await self.liha.position_valve_logical([0] * self.num_channels)
    sep_vals: List[Optional[int]] = [int(600 * self.SPEED_FACTOR)] * self.num_channels
    await self._zaapmotion_force_on()
    await self.liha.set_end_speed_plunger(sep_vals)
    await self.liha.position_plunger_absolute([0] * self.num_channels)
    await self._zaapmotion_force_off()

    # Position at tip rack z_start and eject using mode=0 (above rack).
    # Mode=1 (in rack) uses z_discard to push further down, which crashes
    # on taller tip racks. Mode=0 ejects at the current Z reliably.
    par = ops[0].resource.parent
    assert isinstance(par, TecanTipRack), f"Expected TecanTipRack, got {type(par)}"
    z_start = int(par.z_start)
    await self.liha.move_absolute_z([z_start] * self.num_channels)
    await self._driver.send_command("C5", command="SDT0,50,200")
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

    from pylabrobot.resources import TecanPlate

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

    # Use plate z_start/z_max directly (absolute Tecan Z coordinates).
    # Adjust for mounted tip: tip extends reach, so Z target moves UP
    # by (tip_length - nesting_depth).
    first_par = ops[0].resource.parent
    if isinstance(first_par, TecanPlate):
      z_asp: List[Optional[int]] = [None] * self.num_channels
      z_asp_max: List[Optional[int]] = [None] * self.num_channels
      for i, channel in enumerate(use_channels):
        tip_ext = int(ops[i].tip.total_tip_length * 10) - int(ops[i].tip.fitting_depth * 10)
        z_asp[channel] = int(first_par.z_start) + tip_ext
        z_asp_max[channel] = int(first_par.z_max) + tip_ext
    else:
      z_asp = z_positions.get("start", [self._z_range] * self.num_channels)
      z_asp_max = z_positions.get("max", [self._z_range] * self.num_channels)

    x, y_adj = self._apply_calibration_offsets(x, y - yi * ys, ops)
    z_asp_first = z_asp[next(i for i, v in enumerate(z_asp) if v is not None)]
    logger.info("aspirate: X=%d Y=%d ys=%d Z=%d", x, y_adj, ys, z_asp_first)

    await self.liha.set_z_travel_height([self._z_range] * self.num_channels)
    await self.liha.position_absolute_all_axis(
      x,
      y_adj,
      ys,
      [self._z_range] * self.num_channels,
    )

    # Leading airgap with force mode
    pvl, sep, ppr = self._aspirate_airgap(use_channels, tecan_liquid_classes, "lag")
    if any(ppr):
      await self._zaapmotion_force_on()
      await self.liha.position_valve_logical(pvl)
      await self.liha.set_end_speed_plunger(sep)
      await self.liha.move_plunger_relative(ppr)
      await self._zaapmotion_force_off()

    # Liquid level detection
    if any(tlc.aspirate_lld if tlc is not None else None for tlc in tecan_liquid_classes):
      tlc, _ = self._first_valid(tecan_liquid_classes)
      assert tlc is not None
      await self.liha.set_detection_mode(tlc.lld_mode, tlc.lld_conductivity)
      ssl, sdl, sbl = self._liquid_detection(use_channels, tecan_liquid_classes)
      await self.liha.set_search_speed(ssl)
      await self.liha.set_search_retract_distance(sdl)
      await self.liha.set_search_z_start([z if z is not None else self._z_range for z in z_asp])
      await self.liha.set_search_z_max([z if z is not None else self._z_range for z in z_asp_max])
      await self.liha.set_search_submerge(sbl)
      shz = [self._z_range] * self.num_channels
      await self.liha.set_z_travel_height(shz)
      await self.liha.move_detect_liquid(self._bin_use_channels(use_channels), zadd)
      await self.liha.set_z_travel_height([self._z_range] * self.num_channels)

    # Aspirate + retract with force mode
    zadd = [min(z, 32) if z else None for z in zadd]
    ssz, sep, stz, mtr, ssz_r = self._aspirate_action(ops, use_channels, tecan_liquid_classes, zadd)
    await self.liha.set_slow_speed_z(ssz)
    await self._zaapmotion_force_on()
    await self.liha.set_end_speed_plunger(sep)
    await self.liha.set_tracking_distance_z(stz)
    await self.liha.move_tracking_relative(mtr)
    await self._zaapmotion_force_off()
    await self.liha.set_slow_speed_z(ssz_r)
    await self.liha.move_absolute_z(z_asp)  # retract to aspirate start height

    # Trailing airgap with force mode
    pvl, sep, ppr = self._aspirate_airgap(use_channels, tecan_liquid_classes, "tag")
    await self._zaapmotion_force_on()
    await self.liha.position_valve_logical(pvl)
    await self.liha.set_end_speed_plunger(sep)
    await self.liha.move_plunger_relative(ppr)
    await self._zaapmotion_force_off()

  async def dispense(
    self,
    ops: List[Dispense],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    assert self.liha is not None and self._z_range is not None

    x_positions, y_positions, z_positions = self._liha_positions(ops, use_channels)
    tecan_liquid_classes = self._get_liquid_classes(ops)

    from pylabrobot.resources import TecanPlate

    ys = self._get_ys(ops)
    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None

    # Use plate z_dispense directly (absolute Tecan Z coordinate).
    # Adjust for mounted tip.
    first_par = ops[0].resource.parent
    if isinstance(first_par, TecanPlate):
      z_disp: List[Optional[int]] = [None] * self.num_channels
      for i, channel in enumerate(use_channels):
        tip_ext = int(ops[i].tip.total_tip_length * 10) - int(ops[i].tip.fitting_depth * 10)
        z_disp[channel] = int(first_par.z_dispense) + tip_ext
    else:
      z_disp = [z if z else self._z_range for z in z_positions["dispense"]]

    x, y_adj = self._apply_calibration_offsets(x, y - yi * ys, ops)
    z_disp_first = z_disp[next(i for i, v in enumerate(z_disp) if v is not None)]
    logger.info("dispense: X=%d Y=%d ys=%d Z=%d", x, y_adj, ys, z_disp_first)

    await self.liha.set_z_travel_height([self._z_range] * self.num_channels)
    await self.liha.position_absolute_all_axis(
      x,
      y_adj,
      ys,
      z_disp,  # type: ignore[arg-type]
    )

    sep, spp, stz, mtr = self._dispense_action(ops, use_channels, tecan_liquid_classes)
    await self._zaapmotion_force_on()
    await self.liha.set_end_speed_plunger(sep)
    await self.liha.set_stop_speed_plunger(spp)
    await self.liha.set_tracking_distance_z(stz)
    await self.liha.move_tracking_relative(mtr)
    await self._zaapmotion_force_off()
