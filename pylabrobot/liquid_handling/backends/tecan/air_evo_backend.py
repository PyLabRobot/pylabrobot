"""Backend for Tecan Freedom EVO with Air LiHa (ZaapMotion controllers).

The Air LiHa uses ZaapMotion BLDC motor controllers for air displacement
pipetting, as opposed to the syringe-based XP2000/XP6000 dilutors. This
requires:

1. Boot mode exit and motor configuration on startup
2. Different plunger conversion factors (106.4 steps/uL vs 3 for syringe)
3. ZaapMotion force mode commands around each plunger operation

See keyser-testing/AirLiHa_Investigation.md for full reverse-engineering details.
"""

import asyncio
import logging
from typing import List, Optional, Sequence, Tuple, Union

from pylabrobot.liquid_handling.backends.tecan.EVO_backend import EVOBackend, EVOArm, LiHa
from pylabrobot.liquid_handling.backends.tecan.errors import TecanError
from pylabrobot.liquid_handling.liquid_classes.tecan import (
  TecanLiquidClass,
  get_liquid_class,
)
from pylabrobot.liquid_handling.standard import (
  Drop,
  Mix,
  Pickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources import Liquid, TecanTip, TipSpot, Trash

logger = logging.getLogger(__name__)

# ZaapMotion motor configuration sequence, sent via transparent pipeline T2x.
# Captured from EVOware USB traffic (zaapmotiondriver.dll scan phase).
# Same config for all 8 tips.
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


class ZaapMotion:
  """Commands for ZaapMotion motor controllers (T2x pipeline)."""

  def __init__(self, backend: EVOBackend, module: str = "C5"):
    self.backend = backend
    self.module = module

  def _prefix(self, tip: int) -> str:
    return f"T2{tip}"

  async def exit_boot_mode(self, tip: int) -> None:
    await self.backend.send_command(self.module, command=f"{self._prefix(tip)}X")

  async def read_firmware_version(self, tip: int) -> str:
    resp = await self.backend.send_command(self.module, command=f"{self._prefix(tip)}RFV")
    return str(resp["data"][0]) if resp and resp.get("data") else ""

  async def read_config_status(self, tip: int) -> None:
    await self.backend.send_command(self.module, command=f"{self._prefix(tip)}RCS")

  async def set_force_ramp(self, tip: int, value: int) -> None:
    await self.backend.send_command(self.module, command=f"{self._prefix(tip)}SFR{value}")

  async def set_force_mode(self, tip: int) -> None:
    await self.backend.send_command(self.module, command=f"{self._prefix(tip)}SFP1")

  async def set_default_position(self, tip: int, value: int) -> None:
    await self.backend.send_command(self.module, command=f"{self._prefix(tip)}SDP{value}")

  async def configure_motor(self, tip: int, command: str) -> None:
    await self.backend.send_command(self.module, command=f"{self._prefix(tip)}{command}")

  async def set_sdo(self, param: str) -> None:
    await self.backend.send_command(self.module, command=f"T23SDO{param}")


class AirEVOBackend(EVOBackend):
  """Backend for Tecan Freedom EVO with Air LiHa (ZaapMotion controllers).

  Usage::

    from pylabrobot.liquid_handling import LiquidHandler
    from pylabrobot.liquid_handling.backends.tecan import AirEVOBackend
    from pylabrobot.resources.tecan.tecan_decks import EVO150Deck

    backend = AirEVOBackend(diti_count=8)
    deck = EVO150Deck()
    lh = LiquidHandler(backend=backend, deck=deck)
    await lh.setup()
  """

  # Air LiHa plunger conversion factors (derived from USB capture analysis).
  # Syringe LiHa uses 3 steps/uL and 6 half-steps/sec per uL/s.
  STEPS_PER_UL = 106.4
  SPEED_FACTOR = 213.0

  # ZaapMotion force ramp values
  SFR_ACTIVE = 133120  # high force ramp during plunger movement
  SFR_IDLE = 3752  # low force ramp at rest
  SDP_DEFAULT = 1400  # default dispense parameter

  def __init__(
    self,
    diti_count: int = 0,
    packet_read_timeout: int = 30,
    read_timeout: int = 120,
    write_timeout: int = 120,
  ):
    """Create a new Air EVO interface.

    Args:
      diti_count: number of channels configured for disposable tips.
      packet_read_timeout: timeout in seconds for reading a single packet.
      read_timeout: timeout in seconds for reading a full response.
      write_timeout: timeout in seconds for writing a command.
    """

    super().__init__(
      diti_count=diti_count,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
    )
    self.zaap: Optional[ZaapMotion] = None

  async def setup(self):
    """Setup the Air EVO.

    Checks if axes are already initialized (e.g. from a previous session).
    If so, skips ZaapMotion configuration and PIA. Otherwise performs full
    boot exit, motor configuration, and initialization.
    """

    # Connect USB with short packet timeout for fast buffer drain
    logger.info("Connecting USB...")
    saved_prt = self.io.packet_read_timeout
    self.io.packet_read_timeout = 1
    await self.io.setup()
    self.io.packet_read_timeout = saved_prt

    # Check if already initialized
    if await self._is_initialized():
      logger.info("Axes already initialized — skipping ZaapMotion config + PIA.")
      await self._setup_quick()
    else:
      logger.info("Axes not initialized — running full setup.")
      await self._setup_full()

  async def _is_initialized(self) -> bool:
    """Check if the LiHa axes are already initialized."""
    try:
      arm = EVOArm(self, "C5")
      err = await arm.read_error_register(0)
      err = str(err)
      # A = init failed (1), G = not initialized (7) — these mean we need full init
      # Any other code (including @=OK, Y=tip not fetched, etc.) means axes are initialized
      if err and not any(c in ("A", "G") for c in err):
        return True
    except (TecanError, TimeoutError):
      pass
    return False

  async def _setup_quick(self):
    """Fast setup when axes are already initialized. Skips ZaapMotion config and PIA."""
    self._liha_connected = True
    self._mca_connected = False
    self._roma_connected = False
    self.liha = LiHa(self, EVOBackend.LIHA)
    self.zaap = ZaapMotion(self)
    self._num_channels = await self.liha.report_number_tips()
    self._x_range = await self.liha.report_x_param(5)
    self._y_range = (await self.liha.report_y_param(5))[0]
    self._z_range = (await self.liha.report_z_param(5))[0]
    logger.info("Quick setup complete: %d channels, z_range=%d", self._num_channels, self._z_range)

  async def _setup_full(self):
    """Full setup: ZaapMotion config, safety module, PIA, dilutor init."""

    # Configure ZaapMotion controllers before PIA
    logger.info("Configuring ZaapMotion controllers...")
    await self._configure_zaapmotion()

    # Safety module: enable motor power
    logger.info("Enabling safety module / motor power...")
    await self._setup_safety_module()

    # ZaapMotion SDO config (from EVOware: sent right before PIA)
    assert self.zaap is not None
    try:
      await self.zaap.set_sdo("11,1")
    except TecanError:
      pass  # may fail if already in app mode, non-critical

    # Standard arm init (PIA, BMX, etc.)
    logger.info("Initializing arms (PIA)...")
    self._liha_connected = await self.setup_arm(EVOBackend.LIHA)
    self._mca_connected = await self.setup_arm(EVOBackend.MCA)
    self._roma_connected = await self.setup_arm(EVOBackend.ROMA)

    if self.roma_connected:
      from pylabrobot.liquid_handling.backends.tecan.EVO_backend import RoMa

      self.roma = RoMa(self, EVOBackend.ROMA)
      await self.roma.position_initialization_x()
      await self._park_roma()

    if self.liha_connected:
      self.liha = LiHa(self, EVOBackend.LIHA)
      await self.liha.position_initialization_x()

    self._num_channels = await self.liha.report_number_tips()
    self._x_range = await self.liha.report_x_param(5)
    self._y_range = (await self.liha.report_y_param(5))[0]
    self._z_range = (await self.liha.report_z_param(5))[0]

    # Initialize dilutors (Air LiHa uses same PID/PVL/PPR sequence as syringe)
    await self.liha.set_z_travel_height([self._z_range] * self.num_channels)
    await self.liha.position_absolute_all_axis(45, 1031, 90, [1200] * self.num_channels)
    await self.liha.initialize_plunger(self._bin_use_channels(list(range(self.num_channels))))
    await self.liha.position_valve_logical([1] * self.num_channels)
    await self.liha.move_plunger_relative([100] * self.num_channels)
    await self.liha.position_valve_logical([0] * self.num_channels)
    await self.liha.set_end_speed_plunger([1800] * self.num_channels)
    await self.liha.move_plunger_relative([-100] * self.num_channels)
    await self.liha.position_absolute_all_axis(45, 1031, 90, [self._z_range] * self.num_channels)

  async def _configure_zaapmotion(self):
    """Exit boot mode and configure all 8 ZaapMotion motor controllers.

    Each controller boots into bootloader mode (XP2-BOOT) after power cycle.
    This sends the 'X' command to jump to application firmware, then sends
    33 motor configuration commands (PID gains, current limits, encoder config).
    """
    zaap = ZaapMotion(self)
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

      # Check if already configured (RCS returns OK if configured)
      try:
        await zaap.read_config_status(tip)
        logger.info("ZaapMotion tip %d already configured, skipping", tip + 1)
        continue
      except TecanError:
        pass  # not configured, proceed with config

      # Send motor configuration
      logger.info("Configuring ZaapMotion tip %d (%d commands)", tip + 1, len(ZAAPMOTION_CONFIG))
      for cmd in ZAAPMOTION_CONFIG:
        try:
          await zaap.configure_motor(tip, cmd)
        except TecanError as e:
          logger.warning("ZaapMotion tip %d config command '%s' failed: %s", tip + 1, cmd, e)

    self.zaap = zaap

  async def _setup_safety_module(self):
    """Send safety module commands to enable motor power."""
    try:
      await self.send_command("O1", command="SPN")
      await self.send_command("O1", command="SPS3")
    except TecanError as e:
      logger.warning("Safety module command failed: %s", e)

  # ============== ZaapMotion force mode helpers ==============

  async def _zaapmotion_force_on(self):
    """Enable ZaapMotion force mode before plunger operations."""
    assert self.zaap is not None
    for tip in range(8):
      await self.zaap.set_force_ramp(tip, self.SFR_ACTIVE)
    for tip in range(8):
      await self.zaap.set_force_mode(tip)

  async def _zaapmotion_force_off(self):
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

  async def _perform_blow_out(
    self, ops: List[SingleChannelDispense], use_channels: List[int]
  ) -> None:
    await self._zaapmotion_force_on()
    await super()._perform_blow_out(ops, use_channels)
    await self._zaapmotion_force_off()

  # ============== Override conversion factors ==============

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

  def _aspirate_action(
    self,
    ops: Sequence[Union[SingleChannelAspiration, SingleChannelDispense]],
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
    ops: Sequence[Union[SingleChannelAspiration, SingleChannelDispense]],
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

  # ============== Override liquid handling methods ==============

  async def aspirate(self, ops: List[SingleChannelAspiration], use_channels: List[int]):
    x_positions, y_positions, z_positions = self._liha_positions(ops, use_channels)

    tecan_liquid_classes = [
      get_liquid_class(
        target_volume=op.volume,
        liquid_class=Liquid.WATER,
        tip_type=op.tip.tip_type,
      )
      if isinstance(op.tip, TecanTip)
      else None
      for op in ops
    ]

    from pylabrobot.resources import TecanPlate

    # Y-spacing: use the plate's well pitch (item_dy), not individual well size
    plate = ops[0].resource.parent
    if plate is not None and hasattr(plate, "item_dy"):
      ys = int(plate.item_dy * 10)  # type: ignore[union-attr]
    else:
      ys = int(ops[0].resource.get_absolute_size_y() * 10)
    zadd: List[Optional[int]] = [0] * self.num_channels
    for i, channel in enumerate(use_channels):
      par = ops[i].resource.parent
      if par is None:
        continue
      if not isinstance(par, TecanPlate):
        raise ValueError(f"Operation is not supported by resource {par}.")
      zadd[channel] = round(ops[i].volume / par.area * 10)  # type: ignore[call-overload]

    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None

    # The z_positions from _liha_positions use get_z_position which adds carrier
    # offset and tip_length to absolute Z values — producing out-of-range results.
    # Use the plate's z_start/z_dispense directly instead.
    from pylabrobot.resources import TecanPlate as _TP

    first_par = ops[0].resource.parent
    if isinstance(first_par, _TP):
      z_travel = [self._z_range] * self.num_channels
      z_aspirate: List[Optional[int]] = [None] * self.num_channels
      for i, channel in enumerate(use_channels):
        z_aspirate[channel] = int(first_par.z_start)
    else:
      z_travel = [z if z else self._z_range for z in z_positions["travel"]]
      z_aspirate = z_positions["start"]

    paa_y = y - yi * ys
    print(f"  [DEBUG] PAA: x={x}, y={paa_y}, ys={ys}, z_travel={z_travel}")
    print(f"  [DEBUG] z_range={self._z_range}, z_aspirate={z_aspirate}")

    await self.liha.set_z_travel_height(z_travel)
    await self.liha.position_absolute_all_axis(
      x,
      paa_y,
      ys,
      z_travel,
    )

    # Aspirate leading airgap (with force mode)
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
      detproc = tlc.lld_mode
      sense = tlc.lld_conductivity
      await self.liha.set_detection_mode(detproc, sense)
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

    # Aspirate + retract (with force mode)
    zadd = [min(z, 32) if z else None for z in zadd]
    ssz, sep, stz, mtr, ssz_r = self._aspirate_action(ops, use_channels, tecan_liquid_classes, zadd)
    await self.liha.set_slow_speed_z(ssz)
    await self._zaapmotion_force_on()
    await self.liha.set_end_speed_plunger(sep)
    await self.liha.set_tracking_distance_z(stz)
    await self.liha.move_tracking_relative(mtr)
    await self._zaapmotion_force_off()
    await self.liha.set_slow_speed_z(ssz_r)
    await self.liha.move_absolute_z(z_positions["start"])

    # Aspirate trailing airgap (with force mode)
    pvl, sep, ppr = self._aspirate_airgap(use_channels, tecan_liquid_classes, "tag")
    await self._zaapmotion_force_on()
    await self.liha.position_valve_logical(pvl)
    await self.liha.set_end_speed_plunger(sep)
    await self.liha.move_plunger_relative(ppr)
    await self._zaapmotion_force_off()

  async def dispense(self, ops: List[SingleChannelDispense], use_channels: List[int]):
    x_positions, y_positions, z_positions = self._liha_positions(ops, use_channels)
    plate = ops[0].resource.parent
    if plate is not None and hasattr(plate, "item_dy"):
      ys = int(plate.item_dy * 10)  # type: ignore[union-attr]
    else:
      ys = int(ops[0].resource.get_absolute_size_y() * 10)

    tecan_liquid_classes = [
      get_liquid_class(
        target_volume=op.volume,
        liquid_class=Liquid.WATER,
        tip_type=op.tip.tip_type,
      )
      if isinstance(op.tip, TecanTip)
      else None
      for op in ops
    ]

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
    await self._zaapmotion_force_on()
    await self.liha.set_end_speed_plunger(sep)
    await self.liha.set_stop_speed_plunger(spp)
    await self.liha.set_tracking_distance_z(stz)
    await self.liha.move_tracking_relative(mtr)
    await self._zaapmotion_force_off()

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    assert min(use_channels) >= self.num_channels - self.diti_count, (
      f"DiTis can only be configured for the last {self.diti_count} channels"
    )

    # Use _liha_positions for X/Y only; Z for tip pickup is computed directly
    # from the tip rack's z_start (absolute Tecan Z coordinate).
    x_positions, y_positions, _ = self._liha_positions(ops, use_channels)

    ys = int(ops[0].resource.get_absolute_size_y() * 10)
    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None
    # TODO: calibrate X offset properly in resource definitions
    x += 60  # temporary 6mm X offset from taught position
    await self.liha.set_z_travel_height([self._z_range] * self.num_channels)
    await self.liha.position_absolute_all_axis(
      x, y - yi * ys, ys, [self._z_range] * self.num_channels
    )

    # Aspirate small air gap before tip pickup (with Air LiHa conversion factors)
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

    # AGT Z-start: use the tip rack's z_start directly.
    # Tecan Z coordinates: 0=top (home), z_range=worktable.
    # z_start is an absolute position — the force feedback handles the rest.
    from pylabrobot.resources import TecanTipRack

    par = ops[0].resource.parent
    assert isinstance(par, TecanTipRack), f"Expected TecanTipRack, got {type(par)}"
    agt_z_start = int(par.z_start)
    agt_z_search = abs(int(par.z_max - par.z_start))
    logger.info("AGT z_start=%d, z_search=%d", agt_z_start, agt_z_search)
    await self.liha.get_disposable_tip(
      self._bin_use_channels(use_channels), agt_z_start, agt_z_search
    )

  async def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    assert min(use_channels) >= self.num_channels - self.diti_count, (
      f"DiTis can only be configured for the last {self.diti_count} channels"
    )
    assert all(isinstance(op.resource, (Trash, TipSpot)) for op in ops), (
      "Must drop in waste container or tip rack"
    )

    x_positions, y_positions, _ = self._liha_positions(ops, use_channels)

    ys = int(ops[0].resource.get_absolute_size_y() * 10)
    x, _ = self._first_valid(x_positions)
    y, yi = self._first_valid(y_positions)
    assert x is not None and y is not None
    await self.liha.set_z_travel_height([self._z_range] * self.num_channels)
    await self.liha.position_absolute_all_axis(
      x,
      y - yi * ys,
      ys,
      [self._z_range] * self.num_channels,
    )

    # Empty plunger before discard (from EVOware capture: PPA0 = absolute position 0)
    await self.liha.position_valve_logical([0] * self.num_channels)
    await self._zaapmotion_force_on()
    sep_vals: List[Optional[int]] = [int(600 * self.SPEED_FACTOR)] * self.num_channels
    await self.liha.set_end_speed_plunger(sep_vals)
    await self.liha.position_plunger_absolute([0] * self.num_channels)
    await self._zaapmotion_force_off()

    # Set DiTi discard parameters and drop
    await self.liha.set_disposable_tip_params(1, 1000, 200)
    await self.liha._drop_disposable_tip(self._bin_use_channels(use_channels), discard_height=1)
