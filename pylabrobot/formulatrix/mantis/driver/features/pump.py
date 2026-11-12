"""The pump: the pressure board (board 10), its air pump and its three pressure controllers.

Each controller regulates one line, bottle, chip or vacuum, from its feedback sensor; the bottle
and chip lines each have their own exhaust valves. The pump runs only while something has started
it (``start_pump``); stop turns it off and vents both positive lines. On a v4, setup zeroes the
bottle and chip sensors and runs the connect self-test, as in the recorded v4 startup, and a
chip type's valve sequences from the configuration folder are queued on the chip valve bank, its
drops too: a prime plays them at the waste station, a dispense over the wells.
"""

import asyncio
import logging
import math
import struct
import time
from typing import TYPE_CHECKING, Callable, Dict, List, Literal, Optional, Tuple

from ...liquid_classes.base import MantisLiquidClass
from ...resource_model.chip import MANTIS_CHIP_FACTORIES
from ..errors import MantisConfigurationError, MantisPressureError, MantisPrimeError
from ..framing import PRESSURE_BOARD
from ..valve_sequences import (
  VALVE_SEQUENCE_FILES,
  is_empty_state,
  is_fill_state,
  is_recovery_fill_state,
)

if TYPE_CHECKING:
  from ..master import MantisDriver
  from .drop_sensor import DropSensor

logger = logging.getLogger(__name__)

PressureController = Literal["bottle", "chip", "vacuum"]
# The pressure controllers by name, with their number on the wire.
PRESSURE_CONTROLLERS: Dict[PressureController, int] = {"bottle": 0, "chip": 1, "vacuum": 2}
# A controller's status, as it reads it.
PRESSURE_CONTROLLER_STATUS_DESCRIPTIONS: Dict[int, str] = {
  0: "off",
  1: "settled",
  2: "unsettled",
}
CONTROLLER_OFF = 0
CONTROLLER_SETTLED = 1
CONTROLLER_UNSETTLED = 2

# The positive lines: each has a solenoid and a proportional valve, numbered as its controller.
# The vacuum's valves are never driven.
VENTED_CONTROLLERS: Tuple[PressureController, ...] = ("bottle", "chip")
# The controller whose valves release the reservoir on a v4; a v3's install names none.
RESERVOIR_RELEASE_CONTROLLER: PressureController = "bottle"
# A valve's PWM while it releases a line, and the solenoid's PWM that vents a line for the tare,
# as in the recorded v4 startup.
RELEASE_PWM = 20000
TARE_SOLENOID_PWM = 10000
# Each controller's lowest and highest target: psi on the bottle and chip, to the self-test's
# highest point; inHg on the vacuum, to the deepest a liquid class sets (``mantis_mapping``).
_TARGET_RANGES: Dict[PressureController, Tuple[float, float]] = {
  "bottle": (0.0, 20.0),
  "chip": (0.0, 20.0),
  "vacuum": (-16.0, 0.0),
}
# Seconds the bottle line's solenoid exhausts it when a prime turns the bottle off, at
# ``TARE_SOLENOID_PWM``, as in the recorded v4 prime.
BOTTLE_OFF_DELAY = 0.5
# Seconds the reservoir release holds its valves open.
RELEASE_RESERVOIR_DELAY = 2.0
# Seconds a started pump runs before anything else is sent.
START_PUMP_WAIT = 2.0
# Seconds the vacuum is held at a target of 0 before it is given a new one.
RELEASE_VACUUM_DELAY = 0.2
# A target's settle wait reads the controller's status every 50 ms.
PRESSURE_SETTLE_POLL_INTERVAL = 0.05
# At stop, the pump is read back every 0.2 s until it reads off, for at most 10 s.
PUMP_OFF_POLL_INTERVAL, PUMP_OFF_TIMEOUT = 0.2, 10.0
# A vented line reads within this of atmospheric, psi.
VENT_TOLERANCE = 2.0
# The tare: with the line vented, read every 0.3 s until two readings agree within 0.1 psi, for
# at most 5 s.
TUNE_DELAY, TUNE_TOLERANCE, TUNE_TIMEOUT = 0.3, 0.1, 5.0
# The self-test's settle check: read every 20 ms for 0.8 s windows until a window is settled, for
# at most 8 s.
SETTLE_CHECK_DURATION = 0.8
SETTLE_CHECK_READING_INTERVAL = 0.02
SETTLE_CHECK_TIMEOUT = 8.0
SETTLE_CHECK_MAXIMUM_FLUCTUATION = 1.0
# How near its target a settled window's mean is, by controller.
SETTLE_CHECK_TOLERANCE: Dict[PressureController, float] = {
  "bottle": 2.0,
  "chip": 1.0,
  "vacuum": 1.0,
}
# The connect self-test: its operating point, the positive lines' and the vacuum's test points,
# and the solenoid toggles' targets and count.
CONNECT_CHIP_PRESSURE, CONNECT_BOTTLE_PRESSURE, CONNECT_VACUUM = 12.0, 0.0, -14.0
PRESSURE_TEST_POINTS = (5.0, 10.0, 15.0, 20.0, 0.0)
VACUUM_TEST_POINTS = (-5.0, -10.0, -15.0, 0.0)
SOLENOID_CHECK_TARGET_PRESSURE, SOLENOID_CHECK_TARGET_VACUUM = 20.0, -15.0
SOLENOID_CHECK_TOGGLE_COUNT = 5
# The chip valve bank (PPI address 40) states the self-test queues: every valve closed, every
# valve open, and the state after its connect block; each held 10 or 500 time steps.
VALVE_BANK_PPI_ADDRESS = 40
VALVES_CLOSED, VALVES_OPEN, VALVES_AFTER_CONNECT = 31, 0, 17
VALVE_STATE_SHORT, VALVE_STATE_HOLD = 10, 500
# Seconds to wait for queued valve states to be reported played; for a full queue of drops.
VALVE_STATE_TIMEOUT = 10.0
DROP_QUEUE_TIMEOUT = 60.0
# Where a chip's input is filled from: each takes its own default volumes, for each use.
InputSource = Literal["tube", "pipette_tip"]
INPUT_SOURCES: Tuple[InputSource, ...] = ("tube", "pipette_tip")
InputVolumeUse = Literal["prime", "pre_dispense"]
# Whose delays a valve sequence's states take: a large or a small drop's, or the safe state's that
# closes a dispense.
DropDelays = Literal["large", "small", "safe_state"]
# A precise dispense's first pre-dispense, uL, by chip type, as in the recorded v4 low volume Hi-Fi
# chip (1 large and 3 small drops); and the large drops it pre-dispenses before its large drops.
PRECISE_PRE_DISPENSE_VOLUMES: Dict[str, float] = {"low_volume_hi_fi": 0.8}
PRECISE_PRE_DISPENSE_LARGE_DROPS = 2
# Seconds a prime waits for the vacuum and the chip to settle, where a chip type's install waits
# other than the pump's default_settle_timeout (2 s).
PREPARING_PRESSURE_SETTLE_TIMEOUTS: Dict[str, float] = {"xl_volume_pfe": 4.0}
# The most prime pump cycles a prime that stops on drops plays, by chip type: the install's cap
# (100 for high volume and XL chips, 400 for low volume), less one.
MAXIMUM_AUTO_PRIME_CYCLES: Dict[str, int] = {
  "high_volume": 99,
  "high_volume_pfe": 99,
  "high_volume_3pfe": 99,
  "high_volume_hi_fi": 99,
  "xl_volume_pfe": 99,
  "low_volume": 399,
  "low_volume_pfe": 399,
  "low_volume_3pfe": 399,
  "low_volume_hi_fi": 399,
}


def _round_half_away_from_zero(*, value: float, digits: int) -> float:
  """``value`` rounded to ``digits`` decimals, a half away from zero."""
  scale = 10.0**digits
  fraction, whole = math.modf(value * scale)
  if abs(fraction) >= 0.5:
    whole += math.copysign(1.0, fraction)
  return whole / scale


def _round_half_to_even(*, value: float, digits: int) -> float:
  """``value`` rounded to ``digits`` decimals, a half to even, after scaling: 0.05 rounds to 0."""
  scale = 10.0**digits
  return round(value * scale) / scale


def compute_pump_cycle(
  *,
  target_volume: float,
  large_diaphragm_volume: float,
  small_diaphragm_volume: float,
  allow_all_zero: bool,
) -> Tuple[int, int]:
  """The (large, small) drops that come closest to a volume, as in the recorded v4 primes.

  The large drops rounded down and rounded up are each topped up with small drops; the split
  closer to the volume wins, the one rounded down on a tie. A remainder within half a small drop of
  a large drop is a large drop.

  Args:
    target_volume: uL.
    large_diaphragm_volume: one large drop, uL.
    small_diaphragm_volume: one small drop, uL.
    allow_all_zero: whether a volume above 0 may give no drop; if not, it gives one small drop.
  """

  def split(round_large: Callable[[float], int]) -> Tuple[int, int]:
    large = int(round_large(target_volume / large_diaphragm_volume + 0.0005))
    remainder = target_volume - large * large_diaphragm_volume
    if remainder < small_diaphragm_volume / 2:
      return large, 0
    if large_diaphragm_volume - remainder < small_diaphragm_volume / 2:
      return large + 1, 0
    small = _round_half_away_from_zero(value=remainder / small_diaphragm_volume + 0.0005, digits=0)
    return large, int(small)

  def get_error(drops: Tuple[int, int]) -> float:
    large, small = drops
    return abs(target_volume - (large * large_diaphragm_volume + small * small_diaphragm_volume))

  down, up = split(round_large=math.floor), split(round_large=math.ceil)
  large, small = down if get_error(drops=down) <= get_error(drops=up) else up
  if large + small == 0 and target_volume != 0 and not allow_all_zero:
    small = 1
  return large, small


def compute_dispense_drops(
  *,
  volume: float,
  large_diaphragm_volume: float,
  small_diaphragm_volume: float,
  minimum_small_drops: int,
) -> Tuple[int, int]:
  """The (large, small) drops a v4 dispenses for one well's volume.

  None when the volume, rounded to 0.001 a half away from zero then to 0.1 a half to even, is below
  half a small drop. Else ``minimum_small_drops`` small drops, at most as many as fit in the volume,
  then the closest split of the rest (``compute_pump_cycle``).

  Args:
    volume: uL.
    large_diaphragm_volume: one large drop, uL.
    small_diaphragm_volume: one small drop, uL.
    minimum_small_drops: the small drops dispensed first.
  """
  rounded = _round_half_to_even(value=_round_half_away_from_zero(value=volume, digits=3), digits=1)
  if rounded < small_diaphragm_volume / 2:
    return 0, 0
  first_small = min(minimum_small_drops, math.floor(volume / small_diaphragm_volume))
  large, small = compute_pump_cycle(
    target_volume=volume - first_small * small_diaphragm_volume,
    large_diaphragm_volume=large_diaphragm_volume,
    small_diaphragm_volume=small_diaphragm_volume,
    allow_all_zero=True,
  )
  return large, small + first_small


def _apply_liquid_class_delays(
  *,
  states: List[Tuple[int, int]],
  liquid_class: MantisLiquidClass,
  drop: DropDelays,
) -> List[Tuple[int, int]]:
  """A drop's valve states with their diaphragm's fill and empty steps held for the liquid class's
  delays, in the sequencer's time steps; every other state as the install's file writes it.

  The files hold the 1 cP class's delays, so the 1 cP class changes no drop. The safe state that
  closes a dispense holds its fill from the outputs for the large drop's fill delay, as in the
  recorded v4 dispense, not its file's duration.

  Args:
    states: the (state, duration) pairs.
    liquid_class: whose delays to take.
    drop: whose: "large" or "small" drop, or "safe_state".
  """
  if drop == "safe_state":
    return [
      (state, liquid_class.large_fill_delay if is_recovery_fill_state(state=state) else duration)
      for state, duration in states
    ]
  if drop == "large":
    fill, empty = liquid_class.large_fill_delay, liquid_class.large_empty_delay
  else:
    fill, empty = liquid_class.small_fill_delay, liquid_class.small_empty_delay
  return [
    (
      state,
      fill if is_fill_state(state=state) else empty if is_empty_state(state=state) else duration,
    )
    for state, duration in states
  ]


class Pump:
  """The pump and the pressure controllers, built when the pressure board answers.

  ``start_pump`` and ``stop_pump`` are counted: the pump runs from the first start to the last
  stop. Stop turns it off whatever the count, then vents.
  """

  # Where the ``default_*`` properties start.
  _default_settle_timeout: float = 2.0
  _default_vent_duration: float = RELEASE_RESERVOIR_DELAY

  def __init__(
    self,
    *,
    driver: "MantisDriver",
    reservoir_release_controller: Optional[PressureController] = None,
    tare_and_self_test: bool = False,
    chip_sequences: Optional[Dict[str, Dict[str, List[Tuple[int, int]]]]] = None,
    pump_sequence_volumes: Optional[Dict[str, Dict[str, Dict[str, float]]]] = None,
    input_volumes: Optional[Dict[str, Dict[str, Dict[str, float]]]] = None,
  ):
    """
    Args:
      driver: the driver it sends through.
      reservoir_release_controller: the controller whose valves release the reservoir when the
        pump stops (``RESERVOIR_RELEASE_CONTROLLER`` on a v4); None releases nothing.
      tare_and_self_test: whether ``initialize`` tares and runs the connect self-test (a v4).
      chip_sequences: each chip type's valve sequences, (state, duration) pairs by name, from the
        install; None when there are none, and then none is queued.
      pump_sequence_volumes: each chip type's uL per play of its valve sequences, by pump type
        ("dispense", "prime", "recovery") and name, from the install; None when there are none.
      input_volumes: each chip type's default volumes, uL, by input source and use ("prime",
        "pre_dispense"), from the install; None when there are none.
    """
    self._driver = driver
    self.reservoir_release_controller = reservoir_release_controller
    self.tare_and_self_test = tare_and_self_test
    self.chip_sequences = chip_sequences
    self.pump_sequence_volumes = pump_sequence_volumes
    self.input_volumes = input_volumes
    self._pump_starts = 0
    # Each controller's last target, set again when the pump starts.
    self._last_targets: Dict[PressureController, float] = {}

  @property
  def default_settle_timeout(self) -> float:
    """Seconds ``start_pressure_control`` waits for its controller to settle."""
    return self._default_settle_timeout

  @default_settle_timeout.setter
  def default_settle_timeout(self, value: float) -> None:
    if not value > 0:
      raise ValueError(f"default_settle_timeout must be above 0, got {value}")
    self._default_settle_timeout = value

  @property
  def default_vent_duration(self) -> float:
    """Seconds ``vent`` holds the exhaust valves open."""
    return self._default_vent_duration

  @default_vent_duration.setter
  def default_vent_duration(self, value: float) -> None:
    if not value > 0:
      raise ValueError(f"default_vent_duration must be above 0, got {value}")
    self._default_vent_duration = value

  def _get_controller_index(self, *, controller: PressureController) -> int:
    """The controller's number on the wire.

    Raises:
      ValueError: if ``controller`` is not in ``PRESSURE_CONTROLLERS``.
    """
    if controller not in PRESSURE_CONTROLLERS:
      raise ValueError(
        f"controller must be one of {tuple(PRESSURE_CONTROLLERS)}, got {controller!r}"
      )
    return PRESSURE_CONTROLLERS[controller]

  async def _sleep(self, *, seconds: float) -> None:
    """Hold the pneumatics as they are for ``seconds``."""
    await asyncio.sleep(seconds)

  # -- reads ------------------------------------------------------------------

  async def _send_controller_read(self, *, command: int, controller: PressureController) -> bytes:
    """Send a read of one controller and return its reply; the name is checked before sending."""
    index = self._get_controller_index(controller=controller)
    return await self._driver._send_command(
      board=PRESSURE_BOARD, command=command, payload=struct.pack("<h", index), attempts=3
    )

  async def measure_pressure(self, *, controller: PressureController) -> float:
    """The pressure a controller's feedback sensor reads: psi, inHg on the vacuum.

    Args:
      controller: "bottle", "chip" or "vacuum".
    """
    reply = await self._send_controller_read(command=30, controller=controller)
    (pressure,) = struct.unpack_from("<d", reply)
    return float(pressure)

  async def request_feedback_sensor_parameters(
    self, *, controller: PressureController
  ) -> Tuple[float, float]:
    """A controller's feedback sensor calibration, (scale, offset), as the pressure board holds it.

    Args:
      controller: "bottle", "chip" or "vacuum".
    """
    reply = await self._send_controller_read(command=20, controller=controller)
    scale, offset = struct.unpack_from("<dd", reply)
    return float(scale), float(offset)

  async def request_controller_status(self, *, controller: PressureController) -> int:
    """A controller's status; ``PRESSURE_CONTROLLER_STATUS_DESCRIPTIONS`` says what each means.

    Args:
      controller: "bottle", "chip" or "vacuum".
    """
    reply = await self._send_controller_read(command=16, controller=controller)
    (status,) = struct.unpack_from("<H", reply)
    return int(status)

  async def request_controller_enabled(self, *, controller: PressureController) -> bool:
    """Whether a controller regulates its line.

    Args:
      controller: "bottle", "chip" or "vacuum".
    """
    reply = await self._send_controller_read(command=12, controller=controller)
    (enabled,) = struct.unpack_from("<H", reply)
    return bool(enabled)

  async def request_pump_on(self, *, attempts: int = 3) -> bool:
    """Whether the air pump runs.

    Args:
      attempts: how often the read is sent before giving up.
    """
    reply = await self._driver._send_command(board=PRESSURE_BOARD, command=14, attempts=attempts)
    (on,) = struct.unpack_from("<H", reply)
    return bool(on)

  async def _request_calibration(self) -> Dict[PressureController, Tuple[float, float]]:
    """Every controller's feedback sensor calibration, by controller, read at setup."""
    return {
      controller: await self.request_feedback_sensor_parameters(controller=controller)
      for controller in PRESSURE_CONTROLLERS
    }

  # -- raw firmware calls ----------------------------------------------------

  async def _send_controller_write(
    self, *, command: int, controller: PressureController, arguments: bytes
  ) -> None:
    """Send a write to one controller, or to its valves, once; the name is checked first."""
    index = self._get_controller_index(controller=controller)
    await self._driver._send_command(
      board=PRESSURE_BOARD, command=command, payload=struct.pack("<h", index) + arguments
    )

  async def _unchecked_fw_set_target_pressure(
    self, *, controller: PressureController, pressure: float
  ) -> None:
    """Set a controller's target (board 10, command 11): psi, inHg on the vacuum."""
    await self._send_controller_write(
      command=11, controller=controller, arguments=struct.pack("<d", pressure)
    )

  async def _unchecked_fw_set_controller_enabled(
    self, *, controller: PressureController, enabled: bool
  ) -> None:
    """Make a controller regulate its line, or stop it (board 10, command 13)."""
    await self._send_controller_write(
      command=13, controller=controller, arguments=struct.pack("<H", enabled)
    )

  async def _unchecked_fw_set_pump_on(self, *, on: bool) -> None:
    """Turn the air pump on or off (board 10, command 15), sent once."""
    await self._driver._send_command(
      board=PRESSURE_BOARD, command=15, payload=struct.pack("<H", on)
    )

  async def _unchecked_fw_set_feedback_sensor_parameters(
    self, *, controller: PressureController, scale: float, offset: float
  ) -> None:
    """Write a controller's feedback sensor calibration (board 10, command 21)."""
    await self._send_controller_write(
      command=21, controller=controller, arguments=struct.pack("<dd", scale, offset)
    )

  async def _unchecked_fw_set_proportional_valve(
    self, *, controller: PressureController, pwm: int
  ) -> None:
    """Drive a line's proportional valve at a PWM (board 10, command 32)."""
    await self._send_controller_write(
      command=32, controller=controller, arguments=struct.pack("<H", pwm)
    )

  async def _unchecked_fw_set_solenoid_valve(
    self, *, controller: PressureController, pwm: int
  ) -> None:
    """Drive a line's solenoid valve at a PWM (board 10, command 34)."""
    await self._send_controller_write(
      command=34, controller=controller, arguments=struct.pack("<H", pwm)
    )

  # -- valves and pump-off ---------------------------------------------------

  async def _close_valves(
    self, *, controllers: Tuple[PressureController, ...]
  ) -> Optional[Exception]:
    """Close each line's solenoid, then its proportional valve; every close runs, and the first
    failure is logged and returned."""
    first_fault: Optional[Exception] = None
    for controller in controllers:
      for close in (
        self._unchecked_fw_set_solenoid_valve,
        self._unchecked_fw_set_proportional_valve,
      ):
        try:
          await close(controller=controller, pwm=0)
        except Exception as error:
          logger.warning(
            "[%s] could not close the %s line's valve", self._driver.name, controller, exc_info=True
          )
          first_fault = first_fault or error
    return first_fault

  async def _hold_valves_open(
    self, *, controllers: Tuple[PressureController, ...], seconds: float
  ) -> None:
    """Open each line's solenoid and proportional valve at ``RELEASE_PWM``, hold, then close them;
    they are closed on every path, and the first fault is raised."""
    try:
      for controller in controllers:
        await self._unchecked_fw_set_solenoid_valve(controller=controller, pwm=RELEASE_PWM)
        await self._unchecked_fw_set_proportional_valve(controller=controller, pwm=RELEASE_PWM)
      await self._sleep(seconds=seconds)
    except BaseException:
      await self._close_valves(controllers=controllers)
      raise
    fault = await self._close_valves(controllers=controllers)
    if fault is not None:
      raise fault

  async def _release_reservoir(self) -> None:
    """Open the reservoir's valves for ``RELEASE_RESERVOIR_DELAY``, then close them; nothing
    without ``reservoir_release_controller``."""
    if self.reservoir_release_controller is None:
      return
    await self._hold_valves_open(
      controllers=(self.reservoir_release_controller,), seconds=RELEASE_RESERVOIR_DELAY
    )

  async def _pump_off_unwind(self) -> Optional[Exception]:
    """Disable each controller, then turn the pump off; every step runs, and each failure is
    logged. Never raises: the first failure is returned."""
    first_fault: Optional[Exception] = None
    for controller in PRESSURE_CONTROLLERS:
      try:
        await self._unchecked_fw_set_controller_enabled(controller=controller, enabled=False)
      except Exception as error:
        logger.warning(
          "[%s] could not disable the %s controller", self._driver.name, controller, exc_info=True
        )
        first_fault = first_fault or error
    try:
      await self._unchecked_fw_set_pump_on(on=False)
    except Exception as error:
      logger.error("[%s] could not turn the pump off", self._driver.name, exc_info=True)
      first_fault = first_fault or error
    return first_fault

  # -- the pump and its targets ----------------------------------------------

  async def start_pump(self) -> None:
    """Start the air pump, each controller enabled at its last target; then hold
    ``START_PUMP_WAIT``.

    After setup the last targets are the self-test's: bottle and chip 20 psi, vacuum -15 inHg.
    Starts are counted: a start while the pump runs sends nothing. A failure turns the pump off
    again before it is raised.
    """
    if self._pump_starts == 0:
      try:
        await self._unchecked_fw_set_pump_on(on=True)
        for controller in PRESSURE_CONTROLLERS:
          await self._unchecked_fw_set_controller_enabled(controller=controller, enabled=True)
          target = self._last_targets.get(controller)
          if target is not None:
            await self._unchecked_fw_set_target_pressure(controller=controller, pressure=target)
        await self._sleep(seconds=START_PUMP_WAIT)
      except BaseException:
        await self._pump_off_unwind()
        raise
    self._pump_starts += 1

  async def stop_pump(self) -> None:
    """End one ``start_pump``; the last disables every controller, turns the pump off, then
    releases the reservoir. Every step runs whether or not the one before it succeeded.

    Raises:
      Exception: the first step's failure, once every step ran.
    """
    self._pump_starts = max(0, self._pump_starts - 1)
    if self._pump_starts > 0:
      return
    fault = await self._pump_off_unwind()
    try:
      await self._release_reservoir()
    except Exception as error:
      logger.warning("[%s] could not release the reservoir", self._driver.name, exc_info=True)
      fault = fault or error
    if fault is not None:
      raise fault

  async def _set_pressure(self, *, controller: PressureController, pressure: float) -> None:
    """Enable a controller, then give it its target."""
    await self._unchecked_fw_set_controller_enabled(controller=controller, enabled=True)
    await self._unchecked_fw_set_target_pressure(controller=controller, pressure=pressure)

  async def _set_vacuum(self, *, vacuum: float) -> None:
    """Enable the vacuum controller, release it (a target of 0 for ``RELEASE_VACUUM_DELAY``), then
    give it its target."""
    await self._set_pressure(controller="vacuum", pressure=0.0)
    await self._sleep(seconds=RELEASE_VACUUM_DELAY)
    await self._unchecked_fw_set_target_pressure(controller="vacuum", pressure=vacuum)

  async def _wait_for_pressure_settled(
    self, *, controller: PressureController, target: float, timeout: float
  ) -> None:
    """Read a controller's status, and its line, every ``PRESSURE_SETTLE_POLL_INTERVAL`` until it
    reports settled; a read begun after ``timeout`` is the last.

    Raises:
      MantisPressureError: if the controller reports off, or is not settled at the last read.
    """
    index = self._get_controller_index(controller=controller)
    deadline = time.monotonic() + timeout
    while True:
      expired = time.monotonic() >= deadline
      status = await self.request_controller_status(controller=controller)
      pressure = await self.measure_pressure(controller=controller)
      if status == CONTROLLER_SETTLED:
        return
      if status == CONTROLLER_OFF:
        raise MantisPressureError(
          f"{self._driver.name}: the {controller} controller turned off while settling at "
          f"{target}, reading {pressure:.2f}",
          sensor=index,
          value=pressure,
        )
      if expired:
        raise MantisPressureError(
          f"{self._driver.name}: the {controller} controller did not settle at {target} within "
          f"{timeout} s, reading {pressure:.2f}",
          sensor=index,
          value=pressure,
        )
      await self._sleep(seconds=PRESSURE_SETTLE_POLL_INTERVAL)

  async def start_pressure_control(
    self, *, controller: PressureController, pressure: float, timeout: Optional[float] = None
  ) -> None:
    """Regulate a line at ``pressure`` and wait until its controller reports settled.

    The pump must be started. The vacuum is released first. The target is set
    again whenever the pump starts; the line is regulated until ``stop_pump`` stops every line.

    Args:
      controller: "bottle", "chip" or "vacuum".
      pressure: psi on the bottle and chip, inHg on the vacuum, within the controller's
        ``_TARGET_RANGES``.
      timeout: seconds to wait for it to settle; None uses ``default_settle_timeout``.

    Raises:
      ValueError: if an argument is out of range; nothing is sent.
      MantisPressureError: if the pump is not started (nothing is sent), or the controller reports
        off or does not settle in time.
    """
    self._get_controller_index(controller=controller)
    lowest, highest = _TARGET_RANGES[controller]
    if not lowest <= pressure <= highest:
      unit = "inHg" if controller == "vacuum" else "psi"
      raise ValueError(
        f"the {controller} target must be from {lowest} to {highest} {unit}, got {pressure}"
      )
    timeout = self.default_settle_timeout if timeout is None else timeout
    if not timeout > 0:
      raise ValueError(f"timeout must be above 0, got {timeout}")
    if self._pump_starts == 0:
      raise MantisPressureError(f"{self._driver.name}: the pump is not started; call start_pump()")
    if controller == "vacuum":
      await self._set_vacuum(vacuum=pressure)
    else:
      await self._set_pressure(controller=controller, pressure=pressure)
    self._last_targets[controller] = pressure
    await self._wait_for_pressure_settled(controller=controller, target=pressure, timeout=timeout)

  # -- vent, tare and self-test ----------------------------------------------

  async def vent(self) -> Dict[PressureController, float]:
    """Vent the bottle and chip lines to atmosphere, then read them.

    Each line's exhaust valves open at ``RELEASE_PWM`` for ``default_vent_duration`` and close; a
    line still above ``VENT_TOLERANCE`` is logged. The vacuum is neither vented nor read.

    Returns:
      Each vented line's pressure after, psi.
    """
    await self._hold_valves_open(controllers=VENTED_CONTROLLERS, seconds=self.default_vent_duration)
    readings = {
      controller: await self.measure_pressure(controller=controller)
      for controller in VENTED_CONTROLLERS
    }
    trapped = {
      controller: round(pressure, 1)
      for controller, pressure in readings.items()
      if abs(pressure) > VENT_TOLERANCE
    }
    if trapped:
      logger.warning("[%s] not vented to atmosphere, psi: %s", self._driver.name, trapped)
    return readings

  async def tare_pressure_sensors(self) -> None:
    """Zero the bottle and chip sensors at atmosphere, as in the recorded v4 startup.

    Per line: disable its controller, vent it, read it every ``TUNE_DELAY`` until two readings
    agree within ``TUNE_TOLERANCE``, write its offset less that reading, close the vent and enable
    the controller again if it was. Readings that do not agree within ``TUNE_TIMEOUT`` write
    nothing. The vacuum is not tared.

    Raises:
      MantisPressureError: if the pump runs; nothing is written.
    """
    if await self.request_pump_on():
      raise MantisPressureError(f"{self._driver.name}: the pump runs; a tare needs it off")
    for controller in VENTED_CONTROLLERS:
      scale, offset = await self.request_feedback_sensor_parameters(controller=controller)
      enabled = await self.request_controller_enabled(controller=controller)
      await self._unchecked_fw_set_controller_enabled(controller=controller, enabled=False)
      await self._unchecked_fw_set_proportional_valve(controller=controller, pwm=0)
      await self._unchecked_fw_set_solenoid_valve(controller=controller, pwm=TARE_SOLENOID_PWM)
      try:
        start = time.monotonic()
        previous: Optional[float] = None
        while True:
          reading = await self.measure_pressure(controller=controller)
          await self._sleep(seconds=TUNE_DELAY)
          agreed = previous is not None and abs(reading - previous) <= TUNE_TOLERANCE
          if agreed or time.monotonic() - start >= TUNE_TIMEOUT:
            break
          previous = reading
        if not agreed:
          logger.warning(
            "[%s] the %s sensor did not read steady vented; not tared",
            self._driver.name,
            controller,
          )
        elif reading != 0.0:
          await self._unchecked_fw_set_feedback_sensor_parameters(
            controller=controller, scale=scale, offset=offset - reading
          )
          self._driver.configuration.feedback_sensor_parameters[controller] = (
            scale,
            offset - reading,
          )
          logger.info(
            "[%s] %s sensor tared: read %.3f psi vented, offset %.4f -> %.4f",
            self._driver.name,
            controller,
            reading,
            offset,
            offset - reading,
          )
      except BaseException:
        try:
          await self._unchecked_fw_set_solenoid_valve(controller=controller, pwm=0)
        except Exception:
          logger.warning("[%s] could not close the %s vent", self._driver.name, controller)
        raise
      await self._unchecked_fw_set_solenoid_valve(controller=controller, pwm=0)
      if enabled:
        await self._unchecked_fw_set_controller_enabled(controller=controller, enabled=True)

  async def _queue_valve_states(self, *, states: List[Tuple[int, int]]) -> int:
    """Queue chip valve bank states, each (state, duration), and return the last one's item."""
    item = 0
    for state, duration in states:
      item = await self._driver._queue_ppi_write(
        duration=duration, start_address=VALVE_BANK_PPI_ADDRESS, values=[state]
      )
    return item

  async def _play_valve_states(self, *, states: List[Tuple[int, int]]) -> None:
    """Queue chip valve bank states, each (state, duration), and wait until the last is played."""
    item = await self._queue_valve_states(states=states)
    await self._driver._wait_for_queue_item(item=item, timeout=VALVE_STATE_TIMEOUT)

  def _get_valve_sequence(self, *, chip_type: str, name: str) -> List[Tuple[int, int]]:
    """A chip type's valve sequence, (state, duration) pairs, as the install's file writes it.

    Args:
      chip_type: a key of ``MANTIS_CHIP_FACTORIES``.
      name: a key of ``VALVE_SEQUENCE_FILES``.

    Raises:
      ValueError: if ``chip_type`` or ``name`` is not one.
      MantisConfigurationError: without the install's sequence.
    """
    if chip_type not in MANTIS_CHIP_FACTORIES:
      raise ValueError(
        f"chip_type must be one of {sorted(MANTIS_CHIP_FACTORIES)}, got {chip_type!r}"
      )
    if name not in VALVE_SEQUENCE_FILES:
      raise ValueError(f"name must be one of {sorted(VALVE_SEQUENCE_FILES)}, got {name!r}")
    if self.chip_sequences is None:
      raise MantisConfigurationError(
        f"{self._driver.name}: no valve sequences for this unit; give its configuration folder "
        "(oem_config_directory) or a configuration saved with it"
      )
    sequence = self.chip_sequences.get(chip_type, {}).get(name)
    if sequence is None:
      raise MantisConfigurationError(
        f"{self._driver.name}: the install holds no {name} valve sequence for {chip_type}"
      )
    return sequence

  async def _queue_valve_sequence(self, *, chip_type: str, name: str) -> Optional[int]:
    """Queue a chip type's valve sequence on the sequencer, state by state, as the install's file
    writes it; return its last item, None for a sequence with no state. The caller waits for it.

    Args:
      chip_type: a key of ``MANTIS_CHIP_FACTORIES``.
      name: a key of ``VALVE_SEQUENCE_FILES``.

    Raises:
      ValueError, MantisConfigurationError: as ``_get_valve_sequence``; nothing is sent.
    """
    sequence = self._get_valve_sequence(chip_type=chip_type, name=name)
    if not sequence:
      return None
    return await self._queue_valve_states(states=sequence)

  async def _play_valve_sequence(self, *, chip_type: str, name: str) -> None:
    """Clear and start the sequencer, queue a chip type's valve sequence, and wait until its last
    state is played. If the wait fails, the sequencer is aborted so nothing more plays.

    Args:
      chip_type: a key of ``MANTIS_CHIP_FACTORIES``.
      name: a key of ``VALVE_SEQUENCE_FILES``.

    Raises:
      ValueError, MantisConfigurationError: as ``_get_valve_sequence``; nothing is sent.
      MantisTimeoutError: if the last state is not reported played within ``VALVE_STATE_TIMEOUT``.
    """
    sequence = self._get_valve_sequence(chip_type=chip_type, name=name)
    if not sequence:
      return
    await self._driver._unchecked_fw_clear_sequencer()
    await self._driver._unchecked_fw_start_sequencer()
    try:
      item = await self._queue_valve_states(states=sequence)
      await self._driver._wait_for_queue_item(item=item, timeout=VALVE_STATE_TIMEOUT)
    except BaseException:
      try:
        await self._driver._unchecked_fw_abort_sequencer()
      except Exception:
        logger.warning("[%s] could not abort the sequencer", self._driver.name, exc_info=True)
      raise

  async def _end_pump_block(self, *, release_reservoir: bool) -> None:
    """Disable every controller and turn the pump off, as each self-test block ends; then release
    the reservoir if asked."""
    for controller in PRESSURE_CONTROLLERS:
      await self._unchecked_fw_set_controller_enabled(controller=controller, enabled=False)
    await self._unchecked_fw_set_pump_on(on=False)
    if release_reservoir:
      await self._release_reservoir()

  async def _check_pressure_settled(self, *, controller: PressureController, target: float) -> None:
    """Read a line in ``SETTLE_CHECK_DURATION`` windows until one is settled, for at most
    ``SETTLE_CHECK_TIMEOUT``; one that never settles is logged, not raised.

    A window reads the line every ``SETTLE_CHECK_READING_INTERVAL``, then the controller's status;
    it is settled when the status is, its readings span at most
    ``SETTLE_CHECK_MAXIMUM_FLUCTUATION`` and their mean is within the controller's
    ``SETTLE_CHECK_TOLERANCE`` of ``target``.
    """
    start = time.monotonic()
    mean = 0.0
    while time.monotonic() - start <= SETTLE_CHECK_TIMEOUT:
      window_start = time.monotonic()
      readings: List[float] = []
      while time.monotonic() - window_start < SETTLE_CHECK_DURATION:
        readings.append(await self.measure_pressure(controller=controller))
        await self._sleep(seconds=SETTLE_CHECK_READING_INTERVAL)
      status = await self.request_controller_status(controller=controller)
      mean = sum(readings) / len(readings)
      steady = max(readings) - min(readings) <= SETTLE_CHECK_MAXIMUM_FLUCTUATION
      near = abs(mean - target) <= SETTLE_CHECK_TOLERANCE[controller]
      if status == CONTROLLER_SETTLED and steady and near:
        return
    logger.warning(
      "[%s] self-test: the %s controller did not settle at %s within %s s (mean %.2f)",
      self._driver.name,
      controller,
      target,
      SETTLE_CHECK_TIMEOUT,
      mean,
    )

  async def _run_self_test(self) -> None:
    """The v4 connect self-test, write for write as in the recorded v4 startup.

    The operating point, then a ramp through each line's test points, each checked for settling,
    then ``SOLENOID_CHECK_TOGGLE_COUNT`` solenoid toggles. Each block runs the pump only while its
    controllers are enabled and ends with them disabled and the pump off. With the LED bar showing
    actions, each test point checked shows one more step of progress; an LED fault only warns.
    """
    bar = self._driver.led_bar
    if bar is not None and not bar.show_actions:
      bar = None
    points = 2 * len(PRESSURE_TEST_POINTS) + len(VACUUM_TEST_POINTS)
    checked = 0
    await self._play_valve_states(states=[(VALVES_CLOSED, VALVE_STATE_SHORT)])
    # The connect block: every controller on, the operating point, then parked.
    await self._unchecked_fw_set_pump_on(on=True)
    for controller in PRESSURE_CONTROLLERS:
      await self._unchecked_fw_set_controller_enabled(controller=controller, enabled=True)
    await self._set_pressure(controller="chip", pressure=CONNECT_CHIP_PRESSURE)
    await self._set_vacuum(vacuum=CONNECT_VACUUM)
    await self._set_pressure(controller="bottle", pressure=CONNECT_BOTTLE_PRESSURE)
    await self._set_pressure(controller="chip", pressure=CONNECT_CHIP_PRESSURE)
    await self._end_pump_block(release_reservoir=True)
    await self._driver._unchecked_fw_clear_sequencer()
    await self._driver._unchecked_fw_start_sequencer()
    await self._play_valve_states(states=[(VALVES_AFTER_CONNECT, VALVE_STATE_SHORT)])
    await self._driver._unchecked_fw_clear_sequencer()
    await self._driver._unchecked_fw_start_sequencer()
    await self._play_valve_states(states=[(VALVES_OPEN, VALVE_STATE_HOLD)] * 3)
    # The ramp: the chip with the valves open, the vacuum with them closed, the bottle open again.
    await self._unchecked_fw_set_pump_on(on=True)
    await self._set_pressure(controller="bottle", pressure=CONNECT_BOTTLE_PRESSURE)
    await self._set_pressure(controller="chip", pressure=CONNECT_CHIP_PRESSURE)
    await self._set_pressure(controller="vacuum", pressure=CONNECT_VACUUM)
    for pressure in PRESSURE_TEST_POINTS:
      await self._set_pressure(controller="chip", pressure=pressure)
      await self._check_pressure_settled(controller="chip", target=pressure)
      checked += 1
      if bar is not None:
        await bar._show_fill(done=checked, total=points)
    await self._play_valve_states(states=[(VALVES_CLOSED, VALVE_STATE_HOLD)])
    for vacuum in VACUUM_TEST_POINTS:
      await self._set_vacuum(vacuum=vacuum)
      await self._check_pressure_settled(controller="vacuum", target=vacuum)
      checked += 1
      if bar is not None:
        await bar._show_fill(done=checked, total=points)
    await self._play_valve_states(states=[(VALVES_OPEN, VALVE_STATE_HOLD)])
    for pressure in PRESSURE_TEST_POINTS:
      await self._set_pressure(controller="bottle", pressure=pressure)
      await self._check_pressure_settled(controller="bottle", target=pressure)
      checked += 1
      if bar is not None:
        await bar._show_fill(done=checked, total=points)
    await self._end_pump_block(release_reservoir=True)
    # The toggles: the chip's, the bottle's last, each then the vacuum's. A line is held at its
    # target with its controller off, then released; each block first sets every last target.
    targets: Dict[PressureController, float] = dict.fromkeys(PRESSURE_CONTROLLERS, 0.0)
    for toggle in range(SOLENOID_CHECK_TOGGLE_COUNT):
      toggled: PressureController = "chip" if toggle < SOLENOID_CHECK_TOGGLE_COUNT - 1 else "bottle"
      holds: List[Tuple[PressureController, float]] = [
        (toggled, SOLENOID_CHECK_TARGET_PRESSURE),
        ("vacuum", SOLENOID_CHECK_TARGET_VACUUM),
      ]
      for held, target in holds:
        await self._driver._unchecked_fw_clear_sequencer()
        await self._driver._unchecked_fw_start_sequencer()
        await self._unchecked_fw_set_pump_on(on=True)
        for controller in PRESSURE_CONTROLLERS:
          await self._set_pressure(controller=controller, pressure=targets[controller])
        if held == "vacuum":
          await self._set_vacuum(vacuum=target)
        else:
          await self._set_pressure(controller=held, pressure=target)
        await self._unchecked_fw_set_controller_enabled(controller=held, enabled=False)
        await self._end_pump_block(release_reservoir=False)
        targets[held] = target
        await self._unchecked_fw_set_pump_on(on=True)
        for controller in PRESSURE_CONTROLLERS:
          await self._set_pressure(controller=controller, pressure=targets[controller])
        if held == "vacuum":
          await self._set_pressure(controller="vacuum", pressure=0.0)
        else:
          await self._unchecked_fw_set_controller_enabled(controller=held, enabled=True)
        await self._end_pump_block(release_reservoir=True)

  async def initialize(self, *, skip_self_test: bool = False) -> None:
    """Zero the bottle and chip sensors, clear and start the sequencer, then run the connect
    self-test, as in the recorded v4 startup; the pump ends off, every controller disabled.

    Without ``tare_and_self_test`` it logs and sends nothing. A self-test point that does not
    settle is logged; the self-test gives no verdict. Any failure, a running pump's refusal
    included, turns the pump off and releases the reservoir before it is raised.

    Args:
      skip_self_test: skip the self-test, about a minute of pump switching, on a reconnect: the
        device keeps no record of one, so the caller vouches that one ran since power-on.

    Raises:
      MantisPressureError: if the pump runs; it is turned off first.
    """
    if not self.tare_and_self_test:
      logger.info(
        "[%s] pressure sensors not zeroed, no self-test: not run on a v3", self._driver.name
      )
      return
    self._pump_starts = 0
    try:
      await self.tare_pressure_sensors()
      await self._driver._unchecked_fw_clear_sequencer()
      await self._driver._unchecked_fw_start_sequencer()
      if skip_self_test:
        logger.info("[%s] connect self-test skipped", self._driver.name)
      else:
        await self._run_self_test()
    except BaseException:
      await self._pump_off_unwind()
      try:
        await self._release_reservoir()
      except Exception:
        logger.warning("[%s] could not release the reservoir", self._driver.name, exc_info=True)
      raise
    self._last_targets = {
      "bottle": SOLENOID_CHECK_TARGET_PRESSURE,
      "chip": SOLENOID_CHECK_TARGET_PRESSURE,
      "vacuum": SOLENOID_CHECK_TARGET_VACUUM,
    }

  async def _halt(self) -> None:
    """Disable every controller, turn the pump off and read it back off, then vent; each step
    runs, and a failure is logged. Never raises."""
    self._pump_starts = 0
    await self._pump_off_unwind()
    try:
      deadline = time.monotonic() + PUMP_OFF_TIMEOUT
      while await self.request_pump_on(attempts=1):
        if time.monotonic() >= deadline:
          logger.error("[%s] the pump still reads on", self._driver.name)
          break
        await self._sleep(seconds=PUMP_OFF_POLL_INTERVAL)
    except Exception:
      logger.warning("[%s] could not read the pump back", self._driver.name, exc_info=True)
    try:
      await self.vent()
    except Exception:
      logger.warning("[%s] could not vent", self._driver.name, exc_info=True)

  # -- prime ------------------------------------------------------------------

  def _get_dispense_sequences(
    self, *, chip_type: str
  ) -> Tuple[Tuple[str, float], Tuple[str, float]]:
    """A chip type's small and large drop: each its valve sequence's name and uL, from the
    install's pump sequence files. The smaller volume is the small drop; of two the same, the one
    the file lists first.

    Raises:
      MantisConfigurationError: without two dispense sequences for the chip type.
    """
    volumes = (self.pump_sequence_volumes or {}).get(chip_type, {}).get("dispense", {})
    if len(volumes) != 2:
      raise MantisConfigurationError(
        f"{self._driver.name}: the install names no small and large drop for {chip_type}; give its "
        "configuration folder (oem_config_directory) or a configuration saved with it"
      )
    small, large = sorted(volumes.items(), key=lambda item: item[1])
    return small, large

  def _get_input_volume(
    self, *, chip_type: str, input_source: InputSource, use: InputVolumeUse, argument: str
  ) -> float:
    """A chip type's default volume for an input source and a use, uL, from the install.

    Args:
      chip_type: a key of ``MANTIS_CHIP_FACTORIES``.
      input_source: one of ``INPUT_SOURCES``.
      use: "prime" or "pre_dispense".
      argument: the argument that gives the volume instead, for the error.

    Raises:
      ValueError: if ``input_source`` is not one of ``INPUT_SOURCES``.
      MantisConfigurationError: without it.
    """
    if input_source not in INPUT_SOURCES:
      raise ValueError(f"input_source must be one of {INPUT_SOURCES}, got {input_source!r}")
    volume = (self.input_volumes or {}).get(chip_type, {}).get(input_source, {}).get(use)
    if volume is None:
      raise MantisConfigurationError(
        f"{self._driver.name}: the install names no {input_source} {use} volume for {chip_type}; "
        f"pass {argument}, or give its configuration folder (oem_config_directory)"
      )
    return volume

  def _get_prime_volume(self, *, chip_type: str, input_source: InputSource) -> float:
    """A chip type's default prime volume for an input source, uL, from the install.

    Raises:
      ValueError, MantisConfigurationError: as ``_get_input_volume``.
    """
    return self._get_input_volume(
      chip_type=chip_type, input_source=input_source, use="prime", argument="volume"
    )

  def _get_drop_states(
    self,
    *,
    chip_type: str,
    name: str,
    liquid_class: MantisLiquidClass,
    drop: DropDelays,
  ) -> List[Tuple[int, int]]:
    """A valve sequence with the liquid class's delays (``_apply_liquid_class_delays``).

    Raises:
      ValueError, MantisConfigurationError: as ``_get_valve_sequence``.
    """
    return _apply_liquid_class_delays(
      states=self._get_valve_sequence(chip_type=chip_type, name=name),
      liquid_class=liquid_class,
      drop=drop,
    )

  async def _prepare_pressures(
    self, *, chip_pressure: float, vacuum: float, timeout: float
  ) -> None:
    """Set the pressures drops are played at, as in the recorded v4 prime: the vacuum, settled;
    the bottle off, its line exhausted for ``BOTTLE_OFF_DELAY``; the chip, settled.

    Args:
      chip_pressure: psi.
      vacuum: inHg.
      timeout: seconds each of the vacuum and the chip waits to settle.

    Raises:
      ValueError, MantisPressureError: as ``start_pressure_control``.
    """
    await self.start_pressure_control(controller="vacuum", pressure=vacuum, timeout=timeout)
    await self._unchecked_fw_set_controller_enabled(controller="bottle", enabled=False)
    await self._unchecked_fw_set_proportional_valve(controller="bottle", pwm=0)
    await self._unchecked_fw_set_solenoid_valve(controller="bottle", pwm=TARE_SOLENOID_PWM)
    try:
      await self._sleep(seconds=BOTTLE_OFF_DELAY)
    except BaseException:
      await self._close_valves(controllers=("bottle",))
      raise
    await self._unchecked_fw_set_solenoid_valve(controller="bottle", pwm=0)
    await self.start_pressure_control(controller="chip", pressure=chip_pressure, timeout=timeout)

  async def _play_drops(self, *, states: List[Tuple[int, int]]) -> None:
    """Queue valve states on the started sequencer and wait until the last is played; none, nothing.
    If a queue write or the wait fails, the sequencer is aborted so nothing more plays.

    The sequencer is not cleared or started here: ``move_to_waste_station`` left it started, and a
    prime's cycles follow on it, as in the recorded v4 prime.

    Raises:
      MantisTimeoutError: if the last is not reported played within ``DROP_QUEUE_TIMEOUT``.
    """
    if not states:
      return
    try:
      item = await self._queue_valve_states(states=states)
      await self._driver._wait_for_queue_item(item=item, timeout=DROP_QUEUE_TIMEOUT)
    except BaseException:
      try:
        await self._driver._unchecked_fw_abort_sequencer()
      except Exception:
        logger.warning("[%s] could not abort the sequencer", self._driver.name, exc_info=True)
      raise

  async def _prime(
    self,
    *,
    chip_pressure: float,
    vacuum: float,
    settle_timeout: float,
    drops: List[Tuple[int, int]],
    post_prime: List[Tuple[int, int]],
    drop_sensor: Optional["DropSensor"],
    cycles: Optional[int] = None,
    consecutive_drops: Optional[int] = None,
  ) -> None:
    """Prime the chip on the head, standing over the waste station: start the pump, prepare the
    pressures, play the drops and the post-prime; the pump is stopped on every path. A failed
    stop is raised after a prime that succeeded, and logged after one that failed.

    Without ``cycles`` the drops play once, and a drop sensor that saw no drop raises. With it,
    the drops (one prime pump cycle) play until the sensor has seen ``consecutive_drops`` in a row
    after a cycle, for at most ``cycles`` cycles. The sensor is reset first. With the LED bar
    showing actions, each cycle shows one more step of progress of ``cycles``; a fault only warns.

    Args:
      chip_pressure: psi.
      vacuum: inHg.
      settle_timeout: seconds each of the vacuum and the chip waits to settle.
      drops: the (state, duration) pairs to play, or one cycle's.
      post_prime: the chip's post-prime (state, duration) pairs, played last.
      drop_sensor: the drop sensor; None without one.
      cycles: the most cycles to play; None plays ``drops`` once.
      consecutive_drops: with ``cycles``, the drops in a row that end the prime.

    Raises:
      MantisConfigurationError: if ``cycles`` is given without a drop sensor or
        ``consecutive_drops``; nothing is sent.
      MantisPrimeError: if the sensor saw no drop, or not ``consecutive_drops`` in a row in
        ``cycles`` cycles.
      MantisPressureError, MantisTimeoutError: if a pressure does not settle, or the drops are not
        reported played.
    """
    if cycles is not None and (drop_sensor is None or consecutive_drops is None):
      raise MantisConfigurationError(
        f"{self._driver.name}: a prime that stops on drops needs a drop sensor and a count"
      )
    if drop_sensor is not None:
      drop_sensor.reset()
    await self.start_pump()
    try:
      await self._prepare_pressures(
        chip_pressure=chip_pressure, vacuum=vacuum, timeout=settle_timeout
      )
      if cycles is None or drop_sensor is None or consecutive_drops is None:
        await self._play_drops(states=drops)
        if drop_sensor is not None and not drop_sensor.drop_detected:
          raise MantisPrimeError(
            f"{self._driver.name}: the prime played its drops, but the drop sensor saw none: no "
            "liquid reached the nozzle"
          )
      else:
        bar = self._driver.led_bar
        if bar is not None and not bar.show_actions:
          bar = None
        for cycle in range(1, cycles + 1):
          await self._play_drops(states=drops)
          if bar is not None:
            await bar._show_fill(done=cycle, total=cycles)
          if drop_sensor.consecutive_drops >= consecutive_drops:
            break
        else:
          raise MantisPrimeError(
            f"{self._driver.name}: the drop sensor saw no {consecutive_drops} drops in a row in "
            f"{cycles} prime cycles (last {drop_sensor.consecutive_drops}): no steady flow"
          )
      await self._play_drops(states=post_prime)
    except BaseException:
      try:
        await self.stop_pump()
      except Exception:
        logger.warning(
          "[%s] could not stop the pump after the prime", self._driver.name, exc_info=True
        )
      raise
    await self.stop_pump()
