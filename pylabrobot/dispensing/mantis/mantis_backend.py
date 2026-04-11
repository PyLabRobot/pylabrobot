"""Formulatrix Mantis backend for :class:`pylabrobot.dispensing.Dispenser`.

This backend drives the Formulatrix Mantis chip-based contactless liquid
dispenser over an FTDI/USB serial link using the FMLX protocol.

Example::

    >>> from pylabrobot.dispensing import Dispenser
    >>> from pylabrobot.dispensing.mantis import MantisBackend
    >>> d = Dispenser(backend=MantisBackend(serial_number="M-000438"))
    >>> await d.setup()
    >>> await d.dispense(plate["A1:H12"], volume=5.0, chip=3)
    >>> await d.stop()
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from pylabrobot.dispensing.backend import DispenserBackend
from pylabrobot.dispensing.standard import DispenseOp
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import Plate, Well

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
  apply_stage_homography,
)

logger = logging.getLogger(__name__)

# Default chip-type mapping (chip number → chip type key in PPI_SEQUENCES)
DEFAULT_CHIP_TYPE_MAP: Dict[int, str] = {
  3: "high_volume",
  4: "high_volume",
  5: "high_volume",
}


class MantisBackend(DispenserBackend):
  """Backend for the Formulatrix Mantis contactless liquid dispenser.

  Args:
    serial_number: FTDI serial number of the Mantis device (e.g. ``"M-000438"``).
    chip_type_map: Mapping from chip number (1-6) to chip type string
      (key in ``PPI_SEQUENCES``). If ``None``, defaults to chips 3-5 as
      ``"high_volume"``.
    dispense_z: Machine-frame Z height at which to dispense, in mm. This is a
      hardware calibration (chip-to-plate clearance), not a plate property.
  """

  def __init__(
    self,
    serial_number: Optional[str] = None,
    chip_type_map: Optional[Dict[int, str]] = None,
    dispense_z: float = 44.331,
  ) -> None:
    super().__init__()
    self._serial_number = serial_number
    self._chip_type_map = chip_type_map if chip_type_map is not None else DEFAULT_CHIP_TYPE_MAP
    self._dispense_z = dispense_z

    self._driver: Optional[FmlxDriver] = None
    self._current_chip: Optional[int] = None
    self._is_primed = False

  # -- public properties --

  @property
  def driver(self) -> FmlxDriver:
    if self._driver is None:
      raise RuntimeError("Driver not initialised. Call setup() first.")
    return self._driver

  # -- DispenserBackend interface --

  async def setup(self) -> None:
    """Connect to the Mantis, home all axes, and initialise pressure."""
    logger.info("Setting up Mantis (serial=%s) ...", self._serial_number)

    # Create FTDI transport and FMLX driver
    ftdi = FTDI(
      human_readable_device_name="Formulatrix Mantis",
      device_id=self._serial_number,
      vid=0x0403,
      pid=0x6010,
      interface_select=2,
    )
    self._driver = FmlxDriver(ftdi)
    self._driver.on_event = self._event_handler

    await self._driver.connect()
    await self._run_init_sequence()
    logger.info("Mantis setup complete.")

  async def stop(self) -> None:
    """Detach chip, shut down pressures, and disconnect."""
    logger.info("Shutting down Mantis ...")
    if self._driver is None:
      return

    if self._current_chip is not None:
      await self._detach_chip(self._current_chip)

    await self._move_to_home()
    await self._move_to_ready()
    await self._shutdown_pressures()
    await self._driver.disconnect()
    self._driver = None
    logger.info("Mantis shutdown complete.")

  async def dispense(self, ops: List[DispenseOp], **backend_kwargs) -> None:
    """Execute dispense operations.

    Groups ops by chip number, then for each chip: attaches, primes,
    dispenses to all target wells, and detaches.
    """
    if not ops:
      return

    # Group by chip
    by_chip: Dict[Optional[int], List[DispenseOp]] = {}
    for op in ops:
      by_chip.setdefault(op.chip, []).append(op)

    for chip, chip_ops in by_chip.items():
      chip_number = chip if chip is not None else self._default_chip()
      logger.info(
        "Dispensing to %d well(s) using chip %d",
        len(chip_ops),
        chip_number,
      )

      # Ensure primed
      if not (self._current_chip == chip_number and self._is_primed):
        prime_volume = backend_kwargs.get("prime_volume", 20.0)
        await self._prime_chip(chip_number, volume=prime_volume)

      try:
        dispense_list: List[Tuple[Tuple[float, float, float], float]] = []
        for op in chip_ops:
          x, y, z = self._well_to_machine_coord(op.resource)
          dispense_list.append(((x, y, z), op.volume))

        c_type = self._get_chip_type(chip_number)
        if "low_volume" in c_type:
          large_vol, small_vol = 0.5, 0.1
          large_seq, small_seq = "dispense_500nL", "dispense_100nL"
        else:
          large_vol, small_vol = 5.0, 1.0
          large_seq, small_seq = "dispense_5uL", "dispense_1uL"

        for pos, vol in dispense_list:
          await self._queue_move_xy(pos, VEL_DEFAULT)

          num_large = int(vol / large_vol)
          rem = vol - (num_large * large_vol)
          num_small = int(round(rem / small_vol))

          if num_large == 0 and num_small == 0 and vol > 0:
            num_small = 1

          for _ in range(num_large):
            await self._execute_ppi_sequence(chip_number, large_seq)
          for _ in range(num_small):
            await self._execute_ppi_sequence(chip_number, small_seq)

        await self._move_to_home()
        sid = await self._move_to_ready()
        await self._wait_for_seq_progress(sid)

      finally:
        await self._detach_chip(chip_number)

  # -- serialization --

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "serial_number": self._serial_number,
      "chip_type_map": self._chip_type_map,
      "dispense_z": self._dispense_z,
    }

  # -- helpers --

  def _default_chip(self) -> int:
    """Return the first configured chip number."""
    if self._chip_type_map:
      return next(iter(self._chip_type_map))
    raise ValueError("No chips configured in chip_type_map.")

  def _well_to_machine_coord(self, well: Well) -> Tuple[float, float, float]:
    """Compute the Mantis machine-frame (x, y, z) for a well.

    PLR wells store locations as LFB (left-front-bottom) in the plate frame,
    with A1 at the back (high y). The Mantis plate frame has A1 at the front
    (low y), so y is mirrored across the plate before applying the stage
    homography. Z is a machine-level calibration constant from ``dispense_z``.
    """
    plate = well.parent
    if not isinstance(plate, Plate):
      raise ValueError(
        f"Well {well.name!r} has no Plate parent; cannot compute Mantis coordinate."
      )
    center = well.get_location_wrt(plate, x="c", y="c", z="b")
    ideal_x = center.x
    ideal_y = plate.get_size_y() - center.y
    mx, my = apply_stage_homography(ideal_x, ideal_y)
    return mx, my, self._dispense_z

  def _get_chip_type(self, chip_number: int) -> str:
    return self._chip_type_map.get(chip_number, "high_volume")

  def _event_handler(self, evt: Dict[str, Any]) -> None:
    if evt["event"] != "SequenceProgress":
      logger.debug("[EVENT] %s", evt)
    if evt["event"] in ("MotorErrorOccured", "SequenceStopped"):
      logger.error("[ALERT] %s: %s", evt["event"], evt)

  # -- PPI sequence execution --

  async def _execute_ppi_sequence(self, chip_number: int, sequence_name: str) -> None:
    c_type = self._get_chip_type(chip_number)
    if c_type not in PPI_SEQUENCES:
      raise ValueError(f"Chip type {c_type!r} not found in PPI_SEQUENCES.")
    seq = PPI_SEQUENCES[c_type].get(sequence_name)
    if not seq:
      raise ValueError(f"Sequence {sequence_name!r} not found for chip type {c_type!r}")
    for dur, addr, vals in seq:
      await self.driver.queue_write_ppi(dur, addr, vals)

  async def _pre_attach(self, chip_number: int) -> None:
    await self._execute_ppi_sequence(chip_number, "preattach")

  async def _pre_detach(self, chip_number: int) -> None:
    await self._execute_ppi_sequence(chip_number, "predetach")

  async def _post_detach(self, chip_number: int) -> None:
    await self._execute_ppi_sequence(chip_number, "detachrecovery")

  async def _post_prime(self, chip_number: int) -> None:
    await self._execute_ppi_sequence(chip_number, "postprime")

  # -- motor wait helpers --

  async def _wait_for_seq_progress(self, seq_id: int, timeout: float = 60.0) -> None:
    logger.info("Waiting for seq_id %d to finish ...", seq_id)
    try:
      await self.driver.wait_for_event(
        lambda e: e["event"] == "SequenceProgress" and e["seq_id"] == seq_id and e["in_queue"] == 0,
        timeout=timeout,
      )
    except asyncio.TimeoutError as exc:
      raise TimeoutError(f"Sequencer timed out waiting for seq_id {seq_id}") from exc

  async def _wait_for_motor_idle(
    self, motor_id: int, timeout: float = 30.0, raise_on_error: bool = True
  ) -> int:
    start_time = time.time()
    last_status = 0
    while time.time() - start_time < timeout:
      res = await self.driver.send_command(cmd_get_motor_status(motor_id))
      status = res.get("status", 0)
      last_status = status

      is_busy = (status & (MotorStatusCode.IS_MOVING | MotorStatusCode.IS_HOMING)) != 0
      if not is_busy:
        if (status & MotorStatusCode.error_mask()) and raise_on_error:
          raise RuntimeError(f"Motor {motor_id} stopped with error status: 0x{status:04X}")
        return int(status)
      await asyncio.sleep(0.1)

    raise TimeoutError(
      f"Motor {motor_id} failed to settle within {timeout}s. Last status: 0x{last_status:04X}"
    )

  async def _verify_motor_status(self, motor_id: int, must_be_homed: bool = False) -> int:
    res = await self.driver.send_command(cmd_get_motor_status(motor_id))
    status = res.get("status", 0)
    if status & MotorStatusCode.error_mask():
      raise RuntimeError(f"Motor {motor_id} CRITICAL STATUS: 0x{status:04X} (errors detected)")
    if must_be_homed and not (status & MotorStatusCode.IS_HOMED):
      raise RuntimeError(
        f"Motor {motor_id} expected to be HOMED but is not (status: 0x{status:04X})"
      )
    return int(status)

  async def _wait_for_pressure_settled(self, sensor_id: int, timeout: float = 30.0) -> None:
    start_time = time.time()
    while time.time() - start_time < timeout:
      res = await self.driver.send_command(cmd_p_get_status(sensor_id))
      status = res.get("value", 0)
      await self.driver.send_command(cmd_p_read_feedback_sensor(sensor_id))
      if status == PressureControlStatus.SETTLED:
        return
      if status == PressureControlStatus.OFF:
        raise RuntimeError(f"Pressure controller {sensor_id} turned off while waiting to settle.")
      await asyncio.sleep(0.2)
    raise TimeoutError(f"Pressure controller {sensor_id} failed to settle within {timeout}s")

  async def _wait_for_pump(self, expected_on: bool, timeout: float = 10.0) -> None:
    start_time = time.time()
    while time.time() - start_time < timeout:
      res = await self.driver.send_command(cmd_p_get_pump_on())
      if bool(res.get("value")) == expected_on:
        return
      await asyncio.sleep(0.2)
    raise TimeoutError(f"Pump did not reach expected state: {expected_on}")

  async def _wait_for_aux(self, aux_id: int, expected_value: int, timeout: float = 10.0) -> None:
    start_time = time.time()
    while time.time() - start_time < timeout:
      res = await self.driver.send_command(cmd_p_get_aux(aux_id))
      if res.get("value") == expected_value:
        return
      await asyncio.sleep(0.2)
    raise TimeoutError(f"Aux {aux_id} did not reach expected value {expected_value}")

  # -- movement --

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
    return await self.driver.queue_move_item(False, wait, triplets)

  async def _move_to_home(self, vel_acc: Tuple[float, ...] = VEL_HOME, wait: bool = True) -> int:
    return await self._queue_move_xy(XY_HOME, vel_acc, wait)

  async def _move_to_ready(
    self, vel_acc: Tuple[float, ...] = VEL_DEFAULT, wait: bool = True
  ) -> int:
    return await self._queue_move_xy(XY_READY, vel_acc, wait)

  async def _execute_path(self, path) -> int:
    sid = 0
    for xy_tuple in path:
      sid = await self._queue_move_xy(*xy_tuple)
    return sid

  # -- chip lifecycle --

  async def _attach_chip(self, chip_number: int) -> None:
    if chip_number not in CHIP_PATHS:
      raise ValueError(f"Invalid chip number: {chip_number}")
    if self._current_chip == chip_number:
      logger.info("Chip %d is already attached.", chip_number)
      return
    if self._current_chip is not None:
      logger.info(
        "Detaching current chip %d before attaching %d ...", self._current_chip, chip_number
      )
      await self._detach_chip(self._current_chip)

    logger.info("Attaching chip %d ...", chip_number)
    await self._pre_attach(chip_number)
    sid = await self._execute_path(CHIP_PATHS[chip_number])
    await self._wait_for_seq_progress(sid)
    self._current_chip = chip_number
    self._is_primed = False

  async def _detach_chip(self, chip_number: int, recover_liquid: bool = False) -> None:
    if self._current_chip != chip_number:
      logger.warning(
        "Requested to detach chip %d but current chip is %s", chip_number, self._current_chip
      )
      return
    logger.info("Detaching chip %d ...", chip_number)
    await self._pre_detach(chip_number)
    sid = await self._execute_path(reversed(CHIP_PATHS[chip_number]))
    await self._wait_for_seq_progress(sid)
    if recover_liquid:
      await self._post_detach(chip_number)
    self._current_chip = None
    self._is_primed = False

  async def _prime_chip(self, chip_number: int, volume: float = 20.0) -> None:
    logger.info("Priming chip %d ...", chip_number)
    await self._attach_chip(chip_number)

    # Waste / predispense cycle
    for xy_tuple in XY_WASTE_PATH:
      await self._queue_move_xy(*xy_tuple)

    c_type = self._get_chip_type(chip_number)
    vol_per_cycle = 0.5 if "low_volume" in c_type else 5.0
    cycles = max(1, int(volume / vol_per_cycle))

    for _ in range(cycles):
      await self._execute_ppi_sequence(chip_number, "primepump")

    await self._post_prime(chip_number)

    # Return from waste
    for i in range(len(XY_WASTE_PATH) - 1, -1, -1):
      await self._queue_move_xy(*XY_WASTE_PATH[i])

    await self._move_to_home()
    sid = await self._move_to_ready()
    await self._wait_for_seq_progress(sid)
    self._is_primed = True

  # -- pressure management --

  async def _prepare_pressure(self) -> None:
    logger.info("Preparing pressure ...")
    await self.driver.send_command(cmd_clear_sequencer())
    await self.driver.send_command(cmd_start_sequencer())

    await self.driver.send_command(cmd_p_get_aux(2))
    await self.driver.send_command(cmd_p_set_aux(2, True))
    await self._wait_for_aux(2, 1)

    await self.driver.send_command(cmd_p_get_pump_on())
    await self.driver.send_command(cmd_p_set_pump_on(True))
    await self._wait_for_pump(True)

    await self.driver.send_command(cmd_p_set_controller_enabled(0, True))
    await self.driver.send_command(cmd_p_set_target_pressure(0, 0.0))
    await self.driver.send_command(cmd_p_set_controller_enabled(1, True))
    await self.driver.send_command(cmd_p_set_target_pressure(1, 12.0))
    await self.driver.send_command(cmd_p_set_controller_enabled(2, True))
    await self.driver.send_command(cmd_p_set_target_pressure(2, -14.0))

    await self._wait_for_pump(True)
    await self._wait_for_aux(2, 1)

  async def _shutdown_pressures(self) -> None:
    logger.info("Shutting down pressures ...")
    await self.driver.send_command(cmd_p_get_aux(2))
    await self.driver.send_command(cmd_p_set_aux(2, False))
    await self._wait_for_aux(2, 0)

    for pid in (0, 1, 2):
      await self.driver.send_command(cmd_p_set_controller_enabled(pid, False))

    await self.driver.send_command(cmd_p_set_pump_on(False))
    await self._wait_for_pump(False)

    for m in (0, 1, 2):
      await self._verify_motor_status(m)

  # -- full init sequence (matching original mantis_backend.setup) --

  async def _run_init_sequence(self) -> None:
    """Execute the full Mantis initialisation sequence (homing, calibration, pressure)."""

    # PHASE 1: Handshake & limits
    logger.info("[PHASE 1] Handshake & Limits")
    for _ in range(4):
      await self.driver.send_command(cmd_get_version())
    for m in (0, 1, 2):
      if m != 0:
        await self.driver.send_command(cmd_get_version())
      await self.driver.send_command(cmd_get_motor_limits(m))
    await self.driver.send_command(cmd_clear_motor_faults(0))
    await self.driver.send_command(cmd_clear_motor_faults(1))

    # PHASE 2: Initial status checks
    logger.info("[PHASE 2] Initial Status Checks")
    for _ in range(2):
      for m in (0, 1, 2):
        await self.driver.send_command(cmd_get_following_error_config(m))
        await self._verify_motor_status(m)

    # PHASE 3: Zeroing & forced error recovery
    logger.info("[PHASE 3] Zeroing & Forced Error Recovery")
    await self._verify_motor_status(0)
    await self.driver.send_command(cmd_set_motor_position(0, 0.0))
    await self._verify_motor_status(1)
    await self.driver.send_command(cmd_set_motor_position(1, 0.0))

    await self.driver.send_command(cmd_get_motor_position(0))
    await self.driver.send_command(cmd_move_absolute(0, -27.77777777777778, 5555.56, 833.33))
    await self.driver.send_command(cmd_get_motor_position(1))
    await self.driver.send_command(cmd_move_absolute(1, -27.77777777777778, 5555.56, 833.33))

    await self._wait_for_motor_idle(0, raise_on_error=False)
    await self._wait_for_motor_idle(1, raise_on_error=False)

    await self.driver.send_command(cmd_get_following_error_config(0))
    await self.driver.send_command(cmd_get_motor_status(0))
    await self.driver.send_command(cmd_get_following_error_config(0))
    await self.driver.send_command(cmd_get_motor_status(0))

    await self.driver.send_command(cmd_clear_motor_faults(0))
    await self.driver.send_command(cmd_clear_motor_faults(1))

    await self._verify_motor_status(0)
    await self.driver.send_command(cmd_set_motor_position(0, 0.0))
    await self._verify_motor_status(1)
    await self.driver.send_command(cmd_set_motor_position(1, 0.0))

    # PHASE 4: Calibration cycles
    logger.info("[PHASE 4] Calibration Cycles")
    await self.driver.send_command(cmd_move_absolute(0, -27.77777777777778, 5555.56, 833.33))
    await self.driver.send_command(cmd_move_absolute(1, -27.77777777777778, 5555.56, 833.33))
    await self._wait_for_motor_idle(0, raise_on_error=False)
    await self._wait_for_motor_idle(1, raise_on_error=False)

    await self.driver.send_command(cmd_get_following_error_config(0))
    await self.driver.send_command(cmd_get_motor_status(0))
    await self.driver.send_command(cmd_clear_motor_faults(0))
    await self.driver.send_command(cmd_clear_motor_faults(1))

    # PHASE 5: Successful positioning
    logger.info("[PHASE 5] Successful Positioning")
    await self.driver.send_command(cmd_move_absolute(0, 30.861095852322048, 5555.56, 833.33))
    await self.driver.send_command(cmd_move_absolute(1, -12.63888888888889, 5555.56, 833.33))
    await self._wait_for_motor_idle(0, raise_on_error=True)
    await self._wait_for_motor_idle(1, raise_on_error=True)
    await self.driver.send_command(cmd_set_motor_position(0, 0.0))
    await self.driver.send_command(cmd_set_motor_position(1, 0.0))

    # PHASE 6: Homing Z
    logger.info("[PHASE 6] Homing Z")
    for m in (0, 1, 2):
      await self.driver.send_command(cmd_get_following_error_config(m))
      await self._verify_motor_status(m)

    await self.driver.send_command(cmd_clear_motor_faults(2))
    await self.driver.send_command(
      cmd_home(2, 0, True, False, 59.05561811023622, 590.5561811023622, 15748.03649606299)
    )
    await self._wait_for_motor_idle(2, raise_on_error=True)
    await self._verify_motor_status(2, must_be_homed=True)
    await self._verify_motor_status(2, must_be_homed=True)

    await self.driver.send_command(cmd_is_sensor_enabled(SENSOR_VACUUM))
    await self.driver.send_command(cmd_is_sensor_enabled(SENSOR_PRESSURE))
    await self.driver.send_command(cmd_get_sensor_limits(SENSOR_VACUUM))
    await self.driver.send_command(cmd_get_sensor_limits(SENSOR_PRESSURE))

    # PHASE 7: Re-verify & calibration
    logger.info("[PHASE 7] Re-Verify & Calibration")
    await self.driver.send_command(cmd_get_version())
    for m in (0, 1, 2):
      if m != 0:
        await self.driver.send_command(cmd_get_version())
      await self.driver.send_command(cmd_get_motor_limits(m))

    await self.driver.send_command(cmd_clear_motor_faults(0))
    await self.driver.send_command(cmd_clear_motor_faults(1))

    for _ in range(2):
      for m in (0, 1, 2):
        await self.driver.send_command(cmd_get_following_error_config(m))
        await self._verify_motor_status(m)

    await self._verify_motor_status(0)
    await self.driver.send_command(cmd_set_motor_position(0, 0.0))
    await self._verify_motor_status(1)
    await self.driver.send_command(cmd_set_motor_position(1, 0.0))

    # Repeated move/recovery cycles
    for _ in range(2):
      await self.driver.send_command(cmd_move_absolute(0, -27.77777777777778, 5555.56, 833.33))
      await self.driver.send_command(cmd_move_absolute(1, -27.77777777777778, 5555.56, 833.33))
      await self._wait_for_motor_idle(0, raise_on_error=False)
      await self._wait_for_motor_idle(1, raise_on_error=False)
      await self.driver.send_command(cmd_clear_motor_faults(0))
      await self.driver.send_command(cmd_clear_motor_faults(1))
      await self.driver.send_command(cmd_set_motor_position(0, 0.0))
      await self.driver.send_command(cmd_set_motor_position(1, 0.0))

    # Positive move (success)
    await self.driver.send_command(cmd_move_absolute(0, 10.61111111111111, 5555.56, 833.33))
    await self.driver.send_command(cmd_move_absolute(1, 12.11111111111111, 5555.56, 833.33))
    await self._wait_for_motor_idle(0, raise_on_error=True)
    await self._wait_for_motor_idle(1, raise_on_error=True)
    await self.driver.send_command(cmd_set_motor_position(0, 0.0))
    await self.driver.send_command(cmd_set_motor_position(1, 0.0))

    # PHASE 8: Final homing sequence
    logger.info("[PHASE 8] Final Homing Sequence")
    for m in (0, 1, 2):
      await self.driver.send_command(cmd_get_following_error_config(m))
      await self._verify_motor_status(m)

    await self.driver.send_command(cmd_clear_motor_faults(2))
    await self.driver.send_command(
      cmd_home(2, 0, True, False, 59.05561811023622, 590.5561811023622, 15748.03649606299)
    )
    await self._wait_for_motor_idle(2, raise_on_error=True)
    await self._verify_motor_status(2, must_be_homed=True)
    await self._verify_motor_status(2, must_be_homed=True)

    for m in (0, 1, 2):
      await self.driver.send_command(cmd_get_following_error_config(m))
      await self._verify_motor_status(m)

    # Homing XY
    await self.driver.send_command(cmd_clear_motor_faults(0))
    await self.driver.send_command(cmd_clear_motor_faults(1))
    await self.driver.send_command(
      cmd_home(0, 3, True, False, 5.556055555555556, 55.56055555555556, 1388.893888888889)
    )
    await self.driver.send_command(
      cmd_home(1, 3, True, True, 5.556055555555556, 55.56055555555556, 1388.893888888889)
    )
    await self._wait_for_motor_idle(0, raise_on_error=True)
    await self._wait_for_motor_idle(1, raise_on_error=True)

    for m in (0, 1):
      await self._verify_motor_status(m, must_be_homed=True)
      await self._verify_motor_status(m, must_be_homed=True)

    # PHASE 9: Post-home positioning
    logger.info("[PHASE 9] Post-Home Positioning")
    await self.driver.send_command(cmd_move_absolute(0, 0.0, 500.0, 100.0))
    await self._wait_for_motor_idle(0, raise_on_error=True)
    await self._verify_motor_status(0)
    await self.driver.send_command(cmd_set_motor_position(0, -52.2))

    await self.driver.send_command(cmd_move_absolute(1, 0.0, 500.0, 100.0))
    await self._wait_for_motor_idle(1, raise_on_error=True)
    await self._verify_motor_status(1)
    await self.driver.send_command(cmd_set_motor_position(1, 121.23))

    await self.driver.send_command(cmd_move_absolute(2, 0.0, 3937.012874015748, 15748.03649606299))
    await self._wait_for_motor_idle(2, raise_on_error=True)

    logger.info("Executing coordinated move sequence ...")

    # Coordinated moves
    coord_moves = [
      (0, -52.19948822707371, 55.56055555555556, 1388.893888888889),
      (1, 48.927484449958044, 55.56055555555556, 1388.893888888889),
    ]
    for mid, p, v, a in coord_moves:
      await self.driver.send_command(cmd_move_absolute(mid, p, v, a))
      if mid == 1:
        await self.driver.send_command(cmd_get_motor_status(1))
        await self._verify_motor_status(0)
      await self._wait_for_motor_idle(mid, raise_on_error=True)
      await self._verify_motor_status(mid)

    await self._verify_motor_status(2)
    await self.driver.send_command(cmd_move_absolute(2, 0.0, 3937.012874015748, 15748.03649606299))
    await self._wait_for_motor_idle(2, raise_on_error=True)
    await self._verify_motor_status(2)

    # Second coordinated move pair
    coord_moves_2 = [
      (0, -19.341858780286216, 55.56055555555556, 1388.893888888889),
      (1, 46.4985830283458, 55.56055555555556, 1388.893888888889),
    ]
    for mid, p, v, a in coord_moves_2:
      await self.driver.send_command(cmd_move_absolute(mid, p, v, a))
      if mid == 1:
        await self.driver.send_command(cmd_get_motor_status(1))
        await self._verify_motor_status(0)
      await self._wait_for_motor_idle(mid, raise_on_error=True)
      await self._verify_motor_status(mid)

    await self._verify_motor_status(2)
    await self.driver.send_command(cmd_move_absolute(2, 0.0, 3937.012874015748, 15748.03649606299))
    await self._wait_for_motor_idle(2, raise_on_error=True)
    await self._verify_motor_status(2)

    # Final zeroing move
    for mid in (0, 1):
      await self.driver.send_command(
        cmd_move_absolute(mid, 4.3180815265170873e-05, 55.56055555555556, 1388.893888888889)
      )
      if mid == 1:
        await self.driver.send_command(cmd_get_motor_status(1))
        await self._verify_motor_status(0)
      await self._wait_for_motor_idle(mid, raise_on_error=True)
      await self._verify_motor_status(mid)

    await self._verify_motor_status(2)

    # Pressure initialisation
    for pid in (0, 1):
      await self.driver.send_command(cmd_p_set_controller_enabled(pid, False))
      await self.driver.send_command(cmd_p_set_proportional_valve(pid, 0))
      await self.driver.send_command(cmd_p_set_solenoid_valve(pid, 10000))
      offset = -14.738 if pid == 0 else -14.581
      await self.driver.send_command(cmd_p_set_feedback_sensor_params(pid, 0.01124, offset))
      await self.driver.send_command(cmd_p_set_solenoid_valve(pid, 0))

    await self.driver.send_command(cmd_clear_sequencer())
    await self.driver.send_command(cmd_start_sequencer())

    await self.driver.send_command(cmd_p_set_pump_on(True))
    for pid in (0, 1, 2):
      await self.driver.send_command(cmd_p_set_controller_enabled(pid, True))

    await self.driver.send_command(cmd_p_set_target_pressure(2, -14.0))
    try:
      await self._wait_for_pressure_settled(2, timeout=3.0)
    except TimeoutError:
      pass

    await self.driver.send_command(cmd_p_set_target_pressure(0, 0.0))
    await self._wait_for_pressure_settled(0, timeout=5.0)

    await self.driver.send_command(cmd_p_set_target_pressure(1, 12.0))
    try:
      await self._wait_for_pressure_settled(1, timeout=3.0)
    except TimeoutError:
      pass
