"""Driver for the Formulatrix Mantis contactless liquid dispenser.

Owns the FTDI/FMLX connection and exposes high-level instrument operations
(homing, motion, chip lifecycle, PPI sequence execution, pressure control)
used by capability backends.

Responsibility split:

* :class:`MantisDriver` (this file) — hardware-level: connection lifecycle,
  motion in machine-frame coordinates, chip attach/detach/prime, raw PPI
  sequence playback, pressure init/shutdown. Knows which physical chips are
  loaded (``chip_type_map``) so that ``execute_ppi_sequence`` and
  ``prime_chip`` can pick the right sequence variant per chip type.

* :class:`pylabrobot.formulatrix.mantis.diaphragm_dispenser_backend.MantisDiaphragmDispenserBackend`
  — capability-level: translating per-container dispense ops into ``move_to``
  + N×``execute_ppi_sequence`` calls, applying the PLR-→Mantis coordinate
  conversion, and exposing per-call parameters via ``BackendParams``.

The plate-aware Z height (``dispense_z``) deliberately does *not* live on the
driver — it's a per-call calibration that depends on the plate and chip in
use, so it travels with each call as part of ``BackendParams``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver
from pylabrobot.io.ftdi import FTDI

from .fmlx_driver import (
  FmlxDriver,
  cmd_clear_motor_faults,
  cmd_clear_sequencer,
  cmd_get_following_error_config,
  cmd_get_motor_limits,
  cmd_get_motor_position,
  cmd_get_motor_status,
  cmd_get_sensor_limits,
  cmd_get_version,
  cmd_home,
  cmd_is_sensor_enabled,
  cmd_move_absolute,
  cmd_p_get_aux,
  cmd_p_get_pump_on,
  cmd_p_get_status,
  cmd_p_read_feedback_sensor,
  cmd_p_set_aux,
  cmd_p_set_controller_enabled,
  cmd_p_set_feedback_sensor_params,
  cmd_p_set_proportional_valve,
  cmd_p_set_pump_on,
  cmd_p_set_solenoid_valve,
  cmd_p_set_target_pressure,
  cmd_set_motor_position,
  cmd_start_sequencer,
)
from .mantis_constants import (
  CHIP_PATHS,
  PPI_SEQUENCES,
  SENSOR_PRESSURE,
  SENSOR_VACUUM,
  VEL_DEFAULT,
  VEL_HOME,
  XY_HOME,
  XY_READY,
  XY_WASTE_PATH,
  MotorStatusCode,
  PressureControlStatus,
)
from .mantis_kinematics import (
  MOTOR_1_CONFIG,
  MOTOR_2_CONFIG,
  MOTOR_3_CONFIG,
  MantisKinematics,
)
from .mantis_chipchanger_config import load_attach_path
from .mantis_robotarm_config import HomeArgs, MantisHomingConfig

logger = logging.getLogger(__name__)

# Default chip-type mapping (chip number → chip type key in PPI_SEQUENCES)
DEFAULT_CHIP_TYPE_MAP: Dict[int, str] = {
  3: "high_volume",
  4: "high_volume",
  5: "high_volume",
}

# All per-machine homing calibration (offsets, directions, velocities, the
# pre-home retract/enforce steps, the post-home ready walk, the concurrent-home
# delay) is loaded from the vendor RobotArm.config via
# :class:`MantisHomingConfig`. The only genuinely machine-independent knob is how
# many retract passes to attempt while seeking the hard stop.
#
# The pre-home is position-ROBUST: each arm motor is retracted into a hard stop
# (the expected following-error is tolerated as the stall signal), then nudged one
# EnforceStep off the stop. The enforce is a RELATIVE move
# (MoveAbsolute(step + live_position)), so it self-corrects from any start pose —
# a hardcoded absolute enforce is what crashed intermittently on warm starts.
_PREHOME_MAX_PASSES = 8  # retract passes before giving up seeking the hard stop


class MantisDriver(Driver):
  """Hardware driver for the Formulatrix Mantis.

  Args:
    serial_number: FTDI serial number of the Mantis device (e.g. ``"M-000438"``).
    chip_type_map: Mapping from chip number (1-6) to chip type string (key in
      ``PPI_SEQUENCES``). Defaults to chips 3-5 as ``"high_volume"``.
    robotarm_config_path: Path to this machine's vendor ``RobotArm.config``. The
      per-machine homing calibration (offsets, directions, velocities, pre-home
      retract/enforce, post-home walk, concurrent-home delay) is read from it. If
      omitted, the validated reference-machine fallback values are used.
    homing_config: A pre-built :class:`MantisHomingConfig` (takes precedence over
      ``robotarm_config_path``); mainly for tests.
  """

  def __init__(
    self,
    serial_number: Optional[str] = None,
    chip_type_map: Optional[Dict[int, str]] = None,
    robotarm_config_path: Optional[str] = None,
    homing_config: Optional[MantisHomingConfig] = None,
    install_root: Optional[str] = None,
  ) -> None:
    super().__init__()
    self._serial_number = serial_number
    self._chip_type_map = chip_type_map if chip_type_map is not None else DEFAULT_CHIP_TYPE_MAP

    # Vendor install folder (the one containing ``Data/``). Chip-changer dock paths
    # are read from it; if a RobotArm.config path is given but no install_root, derive it.
    self._install_root = install_root
    if self._install_root is None and robotarm_config_path is not None:
      # .../Data/Device/Configs/RobotArm.config -> install root
      self._install_root = os.path.dirname(robotarm_config_path)
      for _ in range(3):
        self._install_root = os.path.dirname(self._install_root)

    self._robotarm_config_path = robotarm_config_path
    if homing_config is not None:
      self._homing = homing_config
    elif robotarm_config_path is not None:
      self._homing = MantisHomingConfig.from_robotarm_config(robotarm_config_path)
    else:
      self._homing = MantisHomingConfig()

    self._fmlx: Optional[FmlxDriver] = None
    self._current_chip: Optional[int] = None
    self._is_primed = False
    # Monotonic counter bumped each time the move queue drains (SequenceProgress
    # in_queue==0) or stops. Used for race-free waits on queued chip moves.
    self._seq_drain_count = 0

  @property
  def fmlx(self) -> FmlxDriver:
    if self._fmlx is None:
      raise RuntimeError("Driver not initialised. Call setup() first.")
    return self._fmlx

  @property
  def current_chip(self) -> Optional[int]:
    return self._current_chip

  @property
  def is_primed(self) -> bool:
    return self._is_primed

  def get_chip_type(self, chip_number: int) -> str:
    return self._chip_type_map.get(chip_number, "high_volume")

  def default_chip(self) -> int:
    """Return the first configured chip number."""
    if self._chip_type_map:
      return next(iter(self._chip_type_map))
    raise ValueError("No chips configured in chip_type_map.")

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "serial_number": self._serial_number,
      "chip_type_map": self._chip_type_map,
      "robotarm_config_path": self._robotarm_config_path,
    }

  # -- Driver interface --

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    """Connect to the Mantis, home all axes, and initialise pressure."""
    logger.info("Setting up Mantis (serial=%s) ...", self._serial_number)

    ftdi = FTDI(
      human_readable_device_name="Formulatrix Mantis",
      device_id=self._serial_number,
      vid=0x0403,
      pid=0x6010,
      interface_select=2,
    )
    self._fmlx = FmlxDriver(ftdi)
    self._fmlx.on_event = self._event_handler

    await self._fmlx.connect()
    await self._run_init_sequence()
    logger.info("Mantis setup complete.")

  async def stop(self) -> None:
    """Detach chip, shut down pressures, and disconnect."""
    logger.info("Shutting down Mantis ...")
    if self._fmlx is None:
      return

    if self._current_chip is not None:
      await self.detach_chip(self._current_chip)

    await self.move_to_home()
    await self.move_to_ready()
    await self._shutdown_pressures()
    await self._fmlx.disconnect()
    self._fmlx = None
    logger.info("Mantis shutdown complete.")

  # -- public high-level operations --

  async def move_to(
    self,
    x: float,
    y: float,
    z: float,
    vel_acc: Tuple[float, ...] = VEL_DEFAULT,
    wait: bool = True,
  ) -> int:
    """Queue a coordinated XYZ move to a Mantis machine-frame coordinate."""
    return await self._queue_move_xy((x, y, z), vel_acc, wait)

  async def move_to_home(
    self, vel_acc: Tuple[float, ...] = VEL_HOME, wait: bool = True
  ) -> int:
    return await self._queue_move_xy(XY_HOME, vel_acc, wait)

  async def move_to_ready(
    self, vel_acc: Tuple[float, ...] = VEL_DEFAULT, wait: bool = True
  ) -> int:
    return await self._queue_move_xy(XY_READY, vel_acc, wait)

  async def attach_chip(self, chip_number: int) -> None:
    """Pick up a chip, exactly as the vendor does (config-driven geometry).

    The dock path is loaded from the vendor install (``ChipChanger.config`` + the
    ``.seq.txt`` files), NOT hardcoded. The head is pressed to the plunge depth and
    HELD ``dwell_s`` so the passive magnet captures the chip (no valve/PPI fires —
    ``AttachDetachStatusEnabled`` is off on this machine). The dwell is a barrier:
    queue through the plunge → wait for the queue to drain → dwell → queue the lift
    and retract → drain.
    """
    if self._current_chip == chip_number:
      logger.info("Chip %d is already attached.", chip_number)
      return
    if self._current_chip is not None:
      logger.info(
        "Detaching current chip %d before attaching %d ...", self._current_chip, chip_number
      )
      await self.detach_chip(self._current_chip)
    if self._install_root is None:
      raise RuntimeError(
        "attach_chip needs the vendor install (pass install_root= or robotarm_config_path=)"
      )

    path = load_attach_path(self._install_root, chip_number)
    logger.info(
      "Attaching chip %d via %d-waypoint dock (plunge+dwell %.2fs at index %d) ...",
      chip_number,
      len(path.waypoints),
      path.dwell_s,
      path.dwell_index,
    )
    await self._run_dock(path.waypoints, path.dwell_index, path.dwell_s)
    self._current_chip = chip_number
    self._is_primed = False

  async def detach_chip(self, chip_number: int, recover_liquid: bool = False) -> None:
    """Put a chip back, exactly reversing the (config-driven) attach path.

    The vendor detach is ``ReverseSequence`` of the attach: press the chip back
    onto the nest, hold ``dwell_s`` so the nest's magnet recaptures it, then lift
    away. Verified against the chip-3 detach in homethenprime.pcap."""
    if self._current_chip != chip_number:
      logger.warning(
        "Requested to detach chip %d but current chip is %s", chip_number, self._current_chip
      )
      return
    if self._install_root is None:
      raise RuntimeError("detach_chip needs the vendor install (install_root=)")

    path = load_attach_path(self._install_root, chip_number)
    rev = list(reversed(path.waypoints))
    # Dwell at the plunge (deepest Z) on the way to releasing — last waypoint at
    # the max plunge depth before the lift.
    max_z = max(z for _, _, z in rev)
    dwell_index = max(i for i, (_, _, z) in enumerate(rev) if abs(z - max_z) < 1e-6)
    logger.info("Detaching chip %d (reverse dock, dwell %.2fs at index %d) ...",
                chip_number, path.dwell_s, dwell_index)
    await self._run_dock(rev, dwell_index, path.dwell_s)
    self._current_chip = None
    self._is_primed = False

  async def _run_dock(self, waypoints, dwell_index: int, dwell_s: float) -> None:
    """Run a chip dock path as two pipelined batches with a hold at the plunge:
    queue through the plunge → wait for the queue to drain → dwell → queue the
    rest → drain. (Chip moves go through the sequencer, op68 QueueMoveItem.)"""

    async def _batch(wps) -> None:
      baseline = self._seq_drain_count
      for wp in wps:
        await self._queue_move_xy(wp)
      await self._wait_queue_drained(baseline)

    await _batch(waypoints[: dwell_index + 1])
    await asyncio.sleep(dwell_s)
    await _batch(waypoints[dwell_index + 1 :])

  async def prime_chip(self, chip_number: int, volume: float = 20.0) -> None:
    logger.info("Priming chip %d ...", chip_number)
    await self.attach_chip(chip_number)

    for xy_tuple in XY_WASTE_PATH:
      await self._queue_move_xy(*xy_tuple)

    c_type = self.get_chip_type(chip_number)
    vol_per_cycle = 0.5 if "low_volume" in c_type else 5.0
    cycles = max(1, int(volume / vol_per_cycle))

    for _ in range(cycles):
      await self.execute_ppi_sequence(chip_number, "primepump")

    await self.execute_ppi_sequence(chip_number, "postprime")

    for i in range(len(XY_WASTE_PATH) - 1, -1, -1):
      await self._queue_move_xy(*XY_WASTE_PATH[i])

    await self.move_to_home()
    sid = await self.move_to_ready()
    await self.wait_for_seq_progress(sid)
    self._is_primed = True

  async def execute_ppi_sequence(self, chip_number: int, sequence_name: str) -> None:
    c_type = self.get_chip_type(chip_number)
    if c_type not in PPI_SEQUENCES:
      raise ValueError(f"Chip type {c_type!r} not found in PPI_SEQUENCES.")
    seq = PPI_SEQUENCES[c_type].get(sequence_name)
    if not seq:
      raise ValueError(f"Sequence {sequence_name!r} not found for chip type {c_type!r}")
    for dur, addr, vals in seq:
      await self.fmlx.queue_write_ppi(dur, addr, vals)

  async def wait_for_seq_progress(self, seq_id: int, timeout: float = 60.0) -> None:
    logger.info("Waiting for seq_id %d to finish ...", seq_id)
    try:
      await self.fmlx.wait_for_event(
        lambda e: e["event"] == "SequenceProgress" and e["seq_id"] == seq_id and e["in_queue"] == 0,
        timeout=timeout,
      )
    except asyncio.TimeoutError as exc:
      raise TimeoutError(f"Sequencer timed out waiting for seq_id {seq_id}") from exc

  async def _wait_queue_drained(self, baseline: int, timeout: float = 60.0) -> None:
    """Wait until the move queue drains AFTER the batch you just queued.

    Snapshot ``self._seq_drain_count`` BEFORE queuing a batch, queue it, then call
    this. Because the counter is bumped synchronously in the event handler, a drain
    that lands between queuing and this call is not missed (the contributor's
    edge-triggered ``wait_for_event`` could hang on fast moves)."""
    deadline = time.time() + timeout
    while self._seq_drain_count <= baseline:
      remaining = deadline - time.time()
      if remaining <= 0:
        raise TimeoutError(f"Sequencer queue did not drain within {timeout}s")
      try:
        await self.fmlx.wait_for_event(
          lambda e: (e["event"] == "SequenceProgress" and e.get("in_queue") == 0)
          or e["event"] == "SequenceStopped",
          timeout=min(0.5, remaining),
        )
      except asyncio.TimeoutError:
        pass  # re-check the counter (covers a missed event)

  # -- internal helpers --

  def _event_handler(self, evt: Dict[str, Any]) -> None:
    name = evt["event"]
    if name != "SequenceProgress":
      logger.debug("[EVENT] %s", evt)
    if (name == "SequenceProgress" and evt.get("in_queue") == 0) or name == "SequenceStopped":
      self._seq_drain_count += 1
    if name in ("MotorErrorOccured", "SequenceStopped"):
      logger.error("[ALERT] %s: %s", name, evt)

  async def _queue_move_xy(
    self,
    pos: Tuple[float, float, float],
    vel_acc: Tuple[float, ...] = VEL_DEFAULT,
    wait: bool = True,
  ) -> int:
    x, y, z = pos
    v1, a1, v2, a2, v_z, a_z = vel_acc

    if x is None and y is None:
      pos_1, pos_2 = 0.0, 0.0
    else:
      theta1, theta2 = MantisKinematics.xy_to_theta(x, y)
      pos_1 = MOTOR_1_CONFIG.to_packet_units(theta1)
      pos_2 = MOTOR_2_CONFIG.to_packet_units(theta2)

    vel_1 = MOTOR_1_CONFIG.to_packet_units(v1, is_velocity_or_accel=True)
    acc_1 = MOTOR_1_CONFIG.to_packet_units(a1, is_velocity_or_accel=True)
    vel_2 = MOTOR_2_CONFIG.to_packet_units(v2, is_velocity_or_accel=True)
    acc_2 = MOTOR_2_CONFIG.to_packet_units(a2, is_velocity_or_accel=True)

    pos_3 = MOTOR_3_CONFIG.to_packet_units(z)
    vel_3 = MOTOR_3_CONFIG.to_packet_units(v_z, is_velocity_or_accel=True)
    acc_3 = MOTOR_3_CONFIG.to_packet_units(a_z, is_velocity_or_accel=True)

    triplets = [
      [pos_1, vel_1, acc_1],
      [pos_2, vel_2, acc_2],
      [pos_3, vel_3, acc_3],
    ]
    return await self.fmlx.queue_move_item(False, wait, triplets)

  async def _execute_path(self, path) -> int:
    sid = 0
    for xy_tuple in path:
      sid = await self._queue_move_xy(*xy_tuple)
    return sid

  async def _wait_for_motor_idle(
    self,
    motor_id: int,
    timeout: float = 30.0,
    raise_on_error: bool = True,
    error_mask: Optional[int] = None,
  ) -> int:
    mask = MotorStatusCode.error_mask() if error_mask is None else error_mask
    start_time = time.time()
    last_status = 0
    while time.time() - start_time < timeout:
      res = await self.fmlx.send_command(cmd_get_motor_status(motor_id))
      status = res.get("status", 0)
      last_status = status

      is_busy = (status & (MotorStatusCode.IS_MOVING | MotorStatusCode.IS_HOMING)) != 0
      if not is_busy:
        if (status & mask) and raise_on_error:
          raise RuntimeError(f"Motor {motor_id} stopped with error status: 0x{status:04X}")
        return int(status)
      await asyncio.sleep(0.1)

    raise TimeoutError(
      f"Motor {motor_id} failed to settle within {timeout}s. Last status: 0x{last_status:04X}"
    )

  async def _verify_motor_status(
    self, motor_id: int, must_be_homed: bool = False, error_mask: Optional[int] = None
  ) -> int:
    mask = MotorStatusCode.error_mask() if error_mask is None else error_mask
    res = await self.fmlx.send_command(cmd_get_motor_status(motor_id))
    status = res.get("status", 0)
    if status & mask:
      raise RuntimeError(f"Motor {motor_id} CRITICAL STATUS: 0x{status:04X} (errors detected)")
    if must_be_homed and not (status & MotorStatusCode.IS_HOMED):
      raise RuntimeError(
        f"Motor {motor_id} expected to be HOMED but is not (status: 0x{status:04X})"
      )
    return int(status)

  async def _wait_for_pressure_settled(self, sensor_id: int, timeout: float = 30.0) -> None:
    start_time = time.time()
    while time.time() - start_time < timeout:
      res = await self.fmlx.send_command(cmd_p_get_status(sensor_id))
      status = res.get("value", 0)
      await self.fmlx.send_command(cmd_p_read_feedback_sensor(sensor_id))
      if status == PressureControlStatus.SETTLED:
        return
      if status == PressureControlStatus.OFF:
        raise RuntimeError(f"Pressure controller {sensor_id} turned off while waiting to settle.")
      await asyncio.sleep(0.2)
    raise TimeoutError(f"Pressure controller {sensor_id} failed to settle within {timeout}s")

  async def _wait_for_pump(self, expected_on: bool, timeout: float = 10.0) -> None:
    start_time = time.time()
    while time.time() - start_time < timeout:
      res = await self.fmlx.send_command(cmd_p_get_pump_on())
      if bool(res.get("value")) == expected_on:
        return
      await asyncio.sleep(0.2)
    raise TimeoutError(f"Pump did not reach expected state: {expected_on}")

  async def _wait_for_aux(self, aux_id: int, expected_value: int, timeout: float = 10.0) -> None:
    start_time = time.time()
    while time.time() - start_time < timeout:
      res = await self.fmlx.send_command(cmd_p_get_aux(aux_id))
      if res.get("value") == expected_value:
        return
      await asyncio.sleep(0.2)
    raise TimeoutError(f"Aux {aux_id} did not reach expected value {expected_value}")

  async def _shutdown_pressures(self) -> None:
    logger.info("Shutting down pressures ...")
    await self.fmlx.send_command(cmd_p_get_aux(2))
    await self.fmlx.send_command(cmd_p_set_aux(2, False))
    await self._wait_for_aux(2, 0)

    for pid in (0, 1, 2):
      await self.fmlx.send_command(cmd_p_set_controller_enabled(pid, False))

    await self.fmlx.send_command(cmd_p_set_pump_on(False))
    await self._wait_for_pump(False)

    crit = MotorStatusCode.critical_error_mask()
    for m in (0, 1, 2):
      await self._verify_motor_status(m, error_mask=crit)

  # -- homing primitives (mirror the vendor StandardHoming / PreHomeMotors) --

  async def _motor_skipped(self, motor_id: int) -> bool:
    """A motor has stalled ('skipped') against the hard stop when it trips a
    following-error. This is the expected, tolerated signal the vendor uses to
    end the retract loop."""
    res = await self.fmlx.send_command(cmd_get_motor_status(motor_id))
    status = res.get("status", 0)
    return bool(
      status
      & (
        MotorStatusCode.FOLLOWING_ERROR_MOVING
        | MotorStatusCode.FOLLOWING_ERROR_IDLE
        | MotorStatusCode.ABORTED
      )
    )

  async def _prehome_arm_motors(self, motor_ids: Tuple[int, ...] = (0, 1)) -> None:
    """Vendor ``PreHomeMotors``: retract each arm motor into its hard stop
    (re-zeroing each pass, tolerating the expected following-error), then nudge
    one ``EnforceStep`` off the stop.

    The retract and enforce are RELATIVE moves: the enforce target on the wire is
    ``EnforceStep + live_position`` (read fresh from the controller), so it is
    correct from ANY start pose (cold, warm, or wherever the last run parked the
    arm). This is what makes homing deterministic instead of intermittent.
    """
    fmlx = self.fmlx
    for m in motor_ids:
      await fmlx.send_command(cmd_clear_motor_faults(m))

    # Retract toward the hard stop until the motor stalls (skips). Faithful to the
    # vendor loop `while (!CheckIsSkipped() || firstTime)`: the `first_time` flag
    # forces an extra clear-faults-and-reseat pass the FIRST time a stall is seen,
    # so both arm motors are firmly seated (the vendor's warm runs show 2 retract
    # passes for this reason). Skip = any motor tripped a following-error.
    first_time = True
    seated = False
    for _ in range(_PREHOME_MAX_PASSES):
      skipped = any([await self._motor_skipped(m) for m in motor_ids])
      if skipped and not first_time:
        seated = True
        break
      if skipped and first_time:
        # First stall detected: clear it and do one more firm seating pass.
        for m in motor_ids:
          await fmlx.send_command(cmd_clear_motor_faults(m))
        first_time = False
      for m in motor_ids:
        await fmlx.send_command(cmd_set_motor_position(m, 0.0))
      for m in motor_ids:
        await fmlx.send_command(
          cmd_move_absolute(m, self._homing.retract, self._homing.jog_vel, self._homing.jog_acc)
        )
      for m in motor_ids:
        await self._wait_for_motor_idle(m, raise_on_error=False)
    if not seated:
      logger.warning(
        "Pre-home: arm motors %s did not seat against the hard stop in %d passes",
        motor_ids,
        _PREHOME_MAX_PASSES,
      )

    # Enforce: one relative step off the stop, computed from the LIVE position.
    for m in motor_ids:
      await fmlx.send_command(cmd_clear_motor_faults(m))
    for m in motor_ids:
      res = await fmlx.send_command(cmd_get_motor_position(m))
      current = res.get("demand_pos", 0.0)
      await fmlx.send_command(
        cmd_move_absolute(
          m, self._homing.enforce + current, self._homing.jog_vel, self._homing.jog_acc
        )
      )
    for m in motor_ids:
      await self._wait_for_motor_idle(m, raise_on_error=False)
    for m in motor_ids:
      await fmlx.send_command(cmd_set_motor_position(m, 0.0))

  async def _home_motor(self, motor_id: int, args: HomeArgs) -> None:
    """Clear faults, issue the firmware Home, wait, and verify the axis homed.
    Following-errors are tolerated (the seek routinely trips them); only critical
    faults abort."""
    crit = MotorStatusCode.critical_error_mask()
    await self.fmlx.send_command(cmd_clear_motor_faults(motor_id))
    await self.fmlx.send_command(
      cmd_home(motor_id, args.method, args.pos_edge, args.pos_dir, args.slow, args.fast, args.acc)
    )
    await self._wait_for_motor_idle(motor_id, raise_on_error=True, error_mask=crit)
    await self._verify_motor_status(motor_id, must_be_homed=True, error_mask=crit)

  async def _home_arm_motors(self) -> None:
    """Vendor ``HomeMotorsStandard``: home both arm motors CONCURRENTLY in reverse
    order (m1 then m0), starting each ``MotorXYHomingDelay`` apart, then wait for
    both to finish. Homing them together keeps the two-arm geometry coordinated;
    homing one while the other holds still sweeps the head through the tubing."""
    crit = MotorStatusCode.critical_error_mask()
    for m in (1, 0):
      args = self._homing.home_args[m]
      await self.fmlx.send_command(cmd_clear_motor_faults(m))
      await self.fmlx.send_command(
        cmd_home(m, args.method, args.pos_edge, args.pos_dir, args.slow, args.fast, args.acc)
      )
      await asyncio.sleep(self._homing.xy_homing_delay_s)
    for m in (1, 0):
      await self._wait_for_motor_idle(m, raise_on_error=True, error_mask=crit)
    for m in (1, 0):
      await self._verify_motor_status(m, must_be_homed=True, error_mask=crit)

  async def _override_home_position(self, motor_id: int, position_n: float) -> None:
    """Vendor ``OverrideHomePosition``: settle to 0, then DEFINE the position
    counter to the calibrated home angle (HomingPositionN)."""
    await self.fmlx.send_command(cmd_move_absolute(motor_id, 0.0, 500.0, 100.0))
    await self._wait_for_motor_idle(motor_id, raise_on_error=False)
    await self.fmlx.send_command(cmd_set_motor_position(motor_id, position_n))

  async def _post_home_walk(self) -> None:
    """Vendor ``MoveToPostHomingPosition``: the 3-step Z-then-XY ready walk.
    Targets are constant across all captured runs (cold and warm identical)."""
    fmlx = self.fmlx
    crit = MotorStatusCode.critical_error_mask()
    h = self._homing
    for a0, a1 in h.walk:
      await fmlx.send_command(
        cmd_move_absolute(2, h.z_reset_pos, h.z_reset_vel, h.z_reset_acc)
      )
      await self._wait_for_motor_idle(2, raise_on_error=True, error_mask=crit)
      await fmlx.send_command(cmd_move_absolute(0, a0, h.walk_vel, h.walk_acc))
      await fmlx.send_command(cmd_move_absolute(1, a1, h.walk_vel, h.walk_acc))
      await self._wait_for_motor_idle(0, raise_on_error=True, error_mask=crit)
      await self._wait_for_motor_idle(1, raise_on_error=True, error_mask=crit)

  async def _run_init_sequence(self) -> None:
    """Execute the full Mantis initialisation sequence."""
    fmlx = self.fmlx

    # PHASE 1: Handshake & limits
    logger.info("[PHASE 1] Handshake & Limits")
    for _ in range(4):
      await fmlx.send_command(cmd_get_version())
    for m in (0, 1, 2):
      if m != 0:
        await fmlx.send_command(cmd_get_version())
      await fmlx.send_command(cmd_get_motor_limits(m))
    await fmlx.send_command(cmd_clear_motor_faults(0))
    await fmlx.send_command(cmd_clear_motor_faults(1))

    # PHASE 2: Initial status checks
    logger.info("[PHASE 2] Initial Status Checks")
    for _ in range(2):
      for m in (0, 1, 2):
        await fmlx.send_command(cmd_get_following_error_config(m))
        await self._verify_motor_status(m)

    # PHASE 3: Position-robust homing (vendor StandardHoming).
    #
    # Pre-home seats both arm motors against their hard stops and nudges them one
    # EnforceStep off, COMPUTED FROM THE LIVE POSITION so it works from any start
    # pose. Then home Z, re-pre-home, home Z again, then home the arm motors in
    # reverse order (m1, m0) — matching the vendor exactly.
    logger.info("[PHASE 3] Pre-homing arm motors (pass 1)")
    await self._prehome_arm_motors((0, 1))

    logger.info("[PHASE 4] Homing Z")
    await self._home_motor(2, self._homing.home_args[2])

    await fmlx.send_command(cmd_is_sensor_enabled(SENSOR_VACUUM))
    await fmlx.send_command(cmd_is_sensor_enabled(SENSOR_PRESSURE))
    await fmlx.send_command(cmd_get_sensor_limits(SENSOR_VACUUM))
    await fmlx.send_command(cmd_get_sensor_limits(SENSOR_PRESSURE))

    logger.info("[PHASE 5] Pre-homing arm motors (pass 2)")
    await self._prehome_arm_motors((0, 1))
    await self._home_motor(2, self._homing.home_args[2])

    logger.info("[PHASE 6] Homing arm motors concurrently (m1, m0)")
    await self._home_arm_motors()

    # PHASE 7: Define the homed coordinate frame at the calibrated home angles.
    logger.info("[PHASE 7] Setting home frame (offsets %s)", self._homing.home_offset)
    await self._override_home_position(0, self._homing.home_offset[0])
    await self._override_home_position(1, self._homing.home_offset[1])

    # PHASE 8: Walk to the ready pose.
    logger.info("[PHASE 8] Post-home ready walk")
    await self._post_home_walk()
    await self._verify_motor_status(2)

    # PHASE 9: Pressure controller initialisation.
    logger.info("[PHASE 9] Pressure init")

    for pid in (0, 1):
      await fmlx.send_command(cmd_p_set_controller_enabled(pid, False))
      await fmlx.send_command(cmd_p_set_proportional_valve(pid, 0))
      await fmlx.send_command(cmd_p_set_solenoid_valve(pid, 10000))
      offset = -14.738 if pid == 0 else -14.581
      await fmlx.send_command(cmd_p_set_feedback_sensor_params(pid, 0.01124, offset))
      await fmlx.send_command(cmd_p_set_solenoid_valve(pid, 0))

    await fmlx.send_command(cmd_clear_sequencer())
    await fmlx.send_command(cmd_start_sequencer())

    await fmlx.send_command(cmd_p_set_pump_on(True))
    for pid in (0, 1, 2):
      await fmlx.send_command(cmd_p_set_controller_enabled(pid, True))

    await fmlx.send_command(cmd_p_set_target_pressure(2, -14.0))
    try:
      await self._wait_for_pressure_settled(2, timeout=3.0)
    except TimeoutError:
      pass

    await fmlx.send_command(cmd_p_set_target_pressure(0, 0.0))
    await self._wait_for_pressure_settled(0, timeout=5.0)

    await fmlx.send_command(cmd_p_set_target_pressure(1, 12.0))
    try:
      await self._wait_for_pressure_settled(1, timeout=3.0)
    except TimeoutError:
      pass
