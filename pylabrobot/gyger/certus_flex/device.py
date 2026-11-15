"""Control a CERTUS FLEX through its INCO controller interface."""

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import Literal, Optional, Sequence, Union

from ._inco import Argument, Inco
from ._inco_udp import IncoUDP

logger = logging.getLogger(__name__)

_ROOT = "Machine.Entity.Dispenser"
_MODULE = _ROOT + ".Module.Entity."
_POSITION = _MODULE + "PosSystem"
_PRESSURE = _MODULE + "PressureRegulator"
_HEAD = _MODULE + "Head"
_STATES = ("startup", "off", "wait", "on", "ready", "busy", "error")
Speed = Literal["init", "joystick", "medium", "rapid", "slow", "park", "clamp_close"]
Head = Literal["flex5", "flex8", "flex4_angled", "flex8_angled"]
_SPEEDS = {
  "init": "Init",
  "joystick": "JoyStick",
  "medium": "Medium",
  "rapid": "Rapid",
  "slow": "Slow",
  "park": "Park",
  "clamp_close": "ClampClose",
}
_OUTPUTS = {
  "flex5": (1, 2, 3, 4, 5),
  "flex8": tuple(range(8)),
  "flex4_angled": tuple(range(4)),
  "flex8_angled": tuple(range(8)),
}


def _finite(value: float, name: str) -> float:
  """Reject nonfinite values before serializing a controller argument."""
  if isinstance(value, bool) or not math.isfinite(value):
    raise ValueError(f"{name} must be a finite number")
  return value


def _speed(speed: Speed) -> str:
  """Translate a public speed profile into the controller spelling."""
  if speed not in _SPEEDS:
    raise ValueError(f"Unknown speed profile: {speed!r}")
  return _SPEEDS[speed]


class CertusFlexError(RuntimeError):
  """The dispenser reported a fault or cannot perform the requested operation."""


@dataclass(frozen=True)
class ValveCalibration:
  """Measured (volume in µL, opening duration in seconds) points at a given pressure.

  Points must increase in both volume and duration. Interpolation is linear, as in
  CERTUS CONTROL. Extrapolation is rejected. Use the calibration for the installed
  valve and fluid, at the stated pressure in bar.
  """

  points: tuple[tuple[float, float], ...]
  pressure: float

  def __post_init__(self) -> None:
    """Validate the measured range and pressure."""
    if not 0.051 <= _finite(self.pressure, "pressure") <= 1:
      raise ValueError("Calibration pressure must be between 0.051 and 1 bar")
    if len(self.points) < 2:
      raise ValueError("At least two calibration points are required")
    previous = (0.0, 0.0)
    for volume, duration in self.points:
      if _finite(volume, "volume") <= previous[0] or _finite(duration, "duration") <= previous[1]:
        raise ValueError("Calibration volumes and durations must be positive and increasing")
      previous = (volume, duration)

  def duration_for_volume(self, volume: float, *, correction_factor: float = 1.0) -> float:
    """Interpolate seconds for a volume in µL, after applying a volume correction.

    The factor uses CERTUS CONTROL's supported range of 0.5–1.5. It multiplies
    the requested volume before interpolation, not the resulting opening time.
    The corrected volume must remain within the measured calibration range.
    """
    _finite(volume, "volume")
    if not 0.5 <= _finite(correction_factor, "correction_factor") <= 1.5:
      raise ValueError("correction_factor must be between 0.5 and 1.5")
    volume *= correction_factor
    if not self.points[0][0] <= volume <= self.points[-1][0]:
      raise ValueError("Volume is outside the measured calibration range")
    for (v0, t0), (v1, t1) in zip(self.points, self.points[1:]):
      if v0 <= volume <= v1:
        return t0 + (volume - v0) * (t1 - t0) / (v1 - v0)
    raise ValueError("Volume is outside the measured calibration range")


class CertusValve:
  """One valve, addressed by its physical controller output."""

  def __init__(self, device: "CertusFlex", output_id: int) -> None:
    """Bind a valve to its parent and controller output."""
    self._device = device
    self.output_id = output_id
    self._path = _MODULE + f"Valve{output_id}"
    self.calibration: Optional[ValveCalibration] = None

  async def get_state(self) -> str:
    """Read this valve's module state."""
    return await self._device._state(self._path)

  async def close(self) -> None:
    """Close the valve and wait for its command to finish."""
    async with self._device._operation_lock:
      await self._device._inco.call(self._path + ".Cmd.CloseValve")
      await self._device._sync_valves()
      await self._device._wait_state(self._path, ("ready", "on"))

  @staticmethod
  def _validate_duration(duration: float) -> None:
    """Enforce the controller's pulse minimum and numeric field range."""
    # CERTUS CONTROL's protocol validator enforces a 50 µs minimum. The
    # installed valve/fluid may require a longer pulse; use measured calibration.
    if not 0.00005 <= _finite(duration, "duration") < 2**31 / 1e6:
      raise ValueError("duration must be at least 50 µs and fit the controller's 32.32 field")

  async def _require_ready(self) -> None:
    """Require ready motion, pressure, head, dispenser, and selected valve modules."""
    for path in (_ROOT, self._path, _POSITION, _HEAD, _PRESSURE):
      await self._device._require_ready(path)

  async def _validate_prime_duration(self, duration: float) -> None:
    """Check the requested opening against the controller's per-valve prime limit."""
    self._validate_duration(duration)
    limit = await self._device._inco.get_number(self._path + ".Prop.PrimeTimeout")
    if not math.isfinite(limit) or limit <= 0 or duration * 1e6 > limit:
      raise ValueError(f"Prime duration exceeds the valve limit of {limit / 1e6} s")

  @staticmethod
  def _validate_purge(duration: float, pulse_duration: float, interval: Optional[float]) -> None:
    """Validate cumulative open time, pulse width, and optional inter-pulse wait."""
    CertusValve._validate_duration(duration)
    CertusValve._validate_duration(pulse_duration)
    if pulse_duration > duration:
      raise ValueError("pulse_duration must not exceed the total purge duration")
    if interval is not None and not 0 <= _finite(interval, "interval") < 2**31 / 1e6:
      raise ValueError("interval must be nonnegative and fit the controller's 32.32 field")

  async def _prime(self, duration: float) -> None:
    """Run one firmware-timed prime while the parent operation lock is held."""
    await self._validate_prime_duration(duration)
    await self._require_ready()
    logger.info("Priming output %d for %s s", self.output_id, duration)
    await self._device._inco.call(self._path + ".Cmd.Prime", duration * 1e6)
    await self._device._sync_valves()
    await self._device._require_ready(self._path)

  async def prime(self, duration: float) -> None:
    """Open continuously for a duration in seconds at the current position.

    Position the head over waste and set pressure before calling. The controller
    enforces the opening time; this method waits for completion without retries.
    """
    self._validate_duration(duration)
    async with self._device._operation_lock:
      await self._prime(duration)

  async def _purge(self, duration: float, pulse_duration: float, interval: Optional[float]) -> None:
    """Run a firmware-timed pulse train while the parent operation lock is held."""
    self._validate_purge(duration, pulse_duration, interval)
    await self._require_ready()
    arguments = [duration * 1e6, pulse_duration * 1e6]
    if interval is not None:
      arguments.append(interval * 1e6)
    logger.info("Purging output %d for %s s of opening time", self.output_id, duration)
    await self._device._inco.call(self._path + ".Cmd.Purge", *arguments)
    await self._device._sync_valves()
    await self._device._require_ready(self._path)

  async def purge(
    self, duration: float, *, pulse_duration: float, interval: Optional[float] = None
  ) -> None:
    """Pulse a valve for a cumulative opening duration in seconds at its current position.

    ``duration`` excludes waits between pulses. ``pulse_duration`` and ``interval``
    also use seconds. Omit ``interval`` to use firmware timing; explicit intervals
    require firmware supporting the three-argument Purge command. An unsupported
    command raises without resubmitting a different pulse train.
    """
    self._validate_purge(duration, pulse_duration, interval)
    async with self._device._operation_lock:
      await self._purge(duration, pulse_duration, interval)

  async def _dispense_for(self, duration: float) -> None:
    """Dispense while holding the parent operation lock."""
    self._validate_duration(duration)
    await self._require_ready()
    logger.info("Dispensing with output %d for %s s", self.output_id, duration)
    await self._device._inco.call(self._path + ".Cmd.Dispens", duration * 1e6)
    await self._device._sync_valves()
    await self._device._require_ready(self._path)

  async def dispense_for(self, duration: float) -> None:
    """Open this valve for a duration in seconds at the current position.

    The controller times the pulse. This method does not move the head or change
    pressure. A failed or cancelled call must not be retried automatically: the
    dose may already have been delivered.
    """
    self._validate_duration(duration)
    async with self._device._operation_lock:
      await self._dispense_for(duration)

  async def dispense(self, volume: float, *, correction_factor: float = 1.0) -> None:
    """Dispense a calibrated volume in µL at the current position.

    Attach a :class:`ValveCalibration` to ``calibration`` first. The pressure
    setpoint and actual pressure must match that calibration within 0.01 bar.
    Supply the channel's CERTUS CONTROL volume correction factor when applicable;
    the default 1.0 applies no correction. The factor is applied before interpolation.
    """
    if self.calibration is None:
      raise ValueError("Assign this valve's calibration before dispensing by volume")
    calibration = self.calibration
    duration = calibration.duration_for_volume(volume, correction_factor=correction_factor)
    async with self._device._operation_lock:
      pressure = await self._device.get_pressure()
      setpoint = await self._device._inco.get_number(_PRESSURE + ".Prop.SetPressure")
      if abs(pressure - calibration.pressure) > 0.01 or abs(setpoint - calibration.pressure) > 0.01:
        raise CertusFlexError("Current pressure does not match the valve calibration")
      await self._dispense_for(duration)


class CertusFlex:
  """CERTUS FLEX dispenser with explicit motion and pressure control.

  ``setup()`` only connects and reads identity. ``initialize()`` may move axes;
  call it separately with the workspace clear. ``valves[0]`` is the first
  channel printed on the selected head (channel 1 in CERTUS CONTROL).

  Args:
    host: Controller IP for direct UDP, or the PC running INCO server when target is set.
    target: Omit for direct UDP; set to a local INCO server target name to use TCP.
    head: Installed head geometry, required to select the correct valve outputs.
    port: Override the default UDP port 1964 or INCO server TCP port 7979.
    source_ip: Optional local IPv4 address for the direct UDP connection.
    timeout: Maximum seconds for a command or completion wait.
    poll_interval: Seconds between state queries.
  """

  def __init__(
    self,
    host: str,
    *,
    head: Head,
    target: Optional[str] = None,
    port: Optional[int] = None,
    timeout: float = 60,
    poll_interval: float = 0.05,
    source_ip: Optional[str] = None,
  ) -> None:
    """Configure a disconnected dispenser."""
    if head not in _OUTPUTS:
      raise ValueError(f"Unsupported valve head: {head!r}")
    if _finite(timeout, "timeout") <= 0 or _finite(poll_interval, "poll_interval") <= 0:
      raise ValueError("timeout and poll_interval must be positive")
    self._inco: Union[Inco, IncoUDP]
    if target is None:
      self._inco = IncoUDP(
        host, port=1964 if port is None else port, timeout=timeout, source_ip=source_ip
      )
    else:
      if source_ip is not None:
        raise ValueError("source_ip is only supported for direct UDP connections")
      self._inco = Inco(host, target, port=7979 if port is None else port, timeout=timeout)
    self._operation_lock = asyncio.Lock()
    self.timeout = timeout
    self.poll_interval = poll_interval
    self.head = head
    self.serial_number: Optional[str] = None
    self.firmware_version: Optional[str] = None
    self.valves = tuple(CertusValve(self, output) for output in _OUTPUTS[head])

  async def setup(self) -> None:
    """Connect and read identity without moving or pressurizing the machine."""
    await self._inco.setup()
    try:
      self.serial_number = await self._inco.get_string(_ROOT + ".Act.Serialnumber")
      self.firmware_version = await self._inco.get_string("Software.Revision.Dispenser")
    except BaseException:
      await self._inco.stop()
      raise
    logger.info(
      "Connected to CERTUS FLEX %s, firmware %s", self.serial_number, self.firmware_version
    )

  async def stop(self) -> None:
    """Disconnect; this does not abort controller motion or turn off pressure."""
    await self._inco.stop()
    logger.info("Disconnected from CERTUS FLEX")

  async def _state(self, path: str) -> str:
    """Read and validate a module's state code."""
    value = await self._inco.get_number(path + ".Prop.State")
    if not math.isfinite(value) or value != int(value) or not 0 <= value < len(_STATES):
      raise CertusFlexError(f"Invalid state {value!r} from {path}")
    return _STATES[int(value)]

  async def _require_ready(self, path: str) -> None:
    """Reject commands when a participating module is not ready."""
    state = await self._state(path)
    if state != "ready":
      raise CertusFlexError(f"{path} is {state}, expected ready")

  async def _poll_state(self, path: str, states: Sequence[str], initial: Optional[str]) -> None:
    """Poll a state under the enclosing completion deadline."""
    started = time.monotonic()
    departed = initial is None
    while True:
      state = await self._state(path)
      elapsed = time.monotonic() - started
      if state == "error":
        raise CertusFlexError(f"{path} entered error state")
      departed = departed or state != initial or elapsed >= 5
      if departed and state in states:
        return
      await asyncio.sleep(self.poll_interval)

  async def _wait_state(
    self, path: str, states: Sequence[str], *, initial: Optional[str] = None
  ) -> None:
    """Wait with a deadline; allow five seconds to observe command acceptance.

    A short move may finish between polls. CERTUS CONTROL observes the initial
    state for up to five seconds, then accepts ready even if busy was missed.
    """
    try:
      await asyncio.wait_for(self._poll_state(path, states, initial), self.timeout)
    except asyncio.TimeoutError as exc:
      raise asyncio.TimeoutError(
        f"Timed out waiting for {path}; the controller operation may still be running"
      ) from exc

  async def get_status(self) -> dict[str, str]:
    """Read dispenser, coordinator, positioning, head, and pressure states."""
    paths = {
      "dispenser": _ROOT,
      "coordinator": _ROOT + ".Coord",
      "position": _POSITION,
      "head": _HEAD,
      "pressure": _PRESSURE,
    }
    return {name: await self._state(path) for name, path in paths.items()}

  async def get_position(self) -> tuple[float, float, float]:
    """Read controller X/Y/Z positions in mm (controller coordinates)."""
    return (
      await self._inco.get_number(_POSITION + ".Act.Position.0"),
      await self._inco.get_number(_POSITION + ".Act.Position.1"),
      await self._inco.get_number(_POSITION + ".Act.Position.2"),
    )

  async def get_pressure(self) -> float:
    """Read actual dispensing pressure in bar."""
    return await self._inco.get_number(_PRESSURE + ".Act.Pressure")

  async def _poll_pressure(self, pressure: float) -> None:
    """Wait for a charged regulator and the requested actual pressure."""
    while True:
      state = await self._state(_PRESSURE)
      charge = await self._inco.get_number(_PRESSURE + ".Act.ChargeState")
      if state == "error" or charge in (0, 1, 2, 6):
        raise CertusFlexError(f"Pressure failed: module={state}, charge={charge}")
      actual = await self.get_pressure()
      if state == "ready" and charge in (3, 7) and abs(actual - pressure) <= 0.01:
        return
      await asyncio.sleep(self.poll_interval)

  async def _poll_discharged(self) -> None:
    """Wait for an idle regulator with actual pressure near zero."""
    while True:
      state = await self._state(_PRESSURE)
      if state == "error":
        raise CertusFlexError("Pressure regulator entered error while discharging")
      if state == "on" and abs(await self.get_pressure()) <= 0.01:
        return
      await asyncio.sleep(self.poll_interval)

  async def _poll_valves(self) -> None:
    """Read the valve barrier flag under a completion deadline."""
    while True:
      state = await self._state(_HEAD)
      if state == "error":
        raise CertusFlexError("Valve head entered error state")
      if await self._inco.get_number(_HEAD + ".Prop.SyncValves") > 0:
        return
      await asyncio.sleep(self.poll_interval)

  async def _sync_valves(self) -> None:
    """Use the head's explicit barrier to await completion of queued pulses."""
    await self._inco.put_number(_HEAD + ".Prop.SyncValves", 0)
    await self._inco.call(_HEAD + ".Cmd.SyncValves")
    try:
      await asyncio.wait_for(self._poll_valves(), self.timeout)
    except asyncio.TimeoutError as exc:
      raise asyncio.TimeoutError(
        "Timed out waiting for valves; a dose may have been delivered, do not retry automatically"
      ) from exc

  async def initialize(self) -> None:
    """Start the dispenser if needed and reference the axes; this can move hardware.

    Existing error states are reported without automatically acknowledging them.
    Already referenced axes are left ready.
    """
    async with self._operation_lock:
      # Start can advance Off -> On -> Ready in separate coordinator cycles.
      for _ in range(2):
        state = await self._state(_ROOT)
        if state == "ready":
          break
        if state not in ("off", "on"):
          raise CertusFlexError(f"Dispenser is {state}; cannot initialize")
        await self._inco.call(_ROOT + ".Cmd.Start")
        await self._wait_state(_ROOT + ".Coord", ("ready",), initial="ready")
        await self._wait_state(_ROOT, ("on", "ready"))
      await self._require_ready(_ROOT)
      state = await self._state(_POSITION)
      if state == "on":
        logger.info("Referencing CERTUS FLEX axes")
        await self._inco.call(_POSITION + ".Cmd.Init", "")
        await self._wait_state(_POSITION, ("ready",))
      else:
        await self._require_ready(_POSITION)

  async def _move(self, command: str, *arguments: Argument) -> None:
    """Submit a positioning command and await completion."""
    await self._require_ready(_POSITION)
    logger.info("CERTUS FLEX motion: %s", command)
    await self._inco.call(_POSITION + ".Cmd." + command, *arguments)
    await self._wait_state(_POSITION, ("ready",), initial="ready")

  async def move_to(self, x: float, y: float, z: float, *, speed: Speed = "slow") -> None:
    """Move to controller coordinates in mm using a named firmware speed profile.

    Coordinates are machine coordinates, not PLR deck or well coordinates.
    """
    for value, name in ((x, "x"), (y, "y"), (z, "z")):
      _finite(value, name)
    profile = _speed(speed)
    async with self._operation_lock:
      await self._move("MoveXYZ", x, y, z, profile)

  async def park(self, *, speed: Speed = "slow") -> None:
    """Park Z first, then XY, using the controller's configured park positions."""
    profile = _speed(speed)
    async with self._operation_lock:
      await self._move("MoveZToPark", profile)
      await self._move("MoveXYToPark", profile)

  async def move_to_prime(self, *, speed: Speed = "slow") -> None:
    """Raise Z to park, position XY at prime, then lower Z to prime."""
    profile = _speed(speed)
    async with self._operation_lock:
      await self._move("MoveZToPark", profile)
      await self._move("MoveXYToPrime", profile)
      await self._move("MoveZToPrime", profile)

  async def move_to_purge(self, *, speed: Speed = "slow") -> None:
    """Raise Z to park, position XY at purge, then lower Z to purge."""
    profile = _speed(speed)
    async with self._operation_lock:
      await self._move("MoveZToPark", profile)
      await self._move("MoveXYToPurge", profile)
      await self._move("MoveZToPurge", profile)

  async def set_pressure(self, pressure: float) -> None:
    """Set dispensing pressure to 0.051–1 bar, or zero to discharge."""
    _finite(pressure, "pressure")
    if pressure != 0 and not 0.051 <= pressure <= 1:
      raise ValueError("pressure must be zero or between 0.051 and 1")
    async with self._operation_lock:
      state = await self._state(_PRESSURE)
      if state not in ("on", "ready"):
        raise CertusFlexError(f"Pressure regulator is {state}")
      logger.info("Setting CERTUS FLEX pressure to %s bar", pressure)
      if pressure == 0:
        await self._inco.call(_PRESSURE + ".Cmd.Discharge")
        await self._wait_state(_PRESSURE, ("busy", "on"))
        await self._inco.put_number(_PRESSURE + ".Prop.SetPressure", 0)
        await asyncio.wait_for(self._poll_discharged(), self.timeout)
        return
      await self._inco.put_number(_PRESSURE + ".Prop.SetPressure", pressure)
      if state == "on":
        await self._inco.call(_PRESSURE + ".Cmd.Init")
        await self._wait_state(_PRESSURE, ("busy", "ready"))
      await asyncio.wait_for(self._poll_pressure(pressure), self.timeout)

  async def clean_cycle(
    self,
    channels: Sequence[int],
    *,
    prime_duration: float,
    purge_duration: float,
    purge_pulse_duration: float,
    cycles: int = 5,
    purge_interval: Optional[float] = None,
    speed: Speed = "slow",
  ) -> None:
    """Move to purge, repeat purge → 250 ms wait → prime, then park.

    Channels are zero-based indexes into ``valves``. Each phase processes the
    selected channels sequentially. Durations are seconds per channel per cycle;
    purge duration is cumulative valve-open time, not elapsed wall time.
    Initialize the machine and set dispensing pressure before calling. Pressure
    and its setpoint are left unchanged; the caller controls discharge explicitly.

    All operations share one lock. On failure or cancellation, attempt to stop
    the selected valves, preserve the original error, and issue no recovery move
    or repeated dose. A lost connection can prevent stopping a valve; disconnecting
    is not an emergency stop. Cleaning effectiveness requires physical verification.
    """
    selected = tuple(channels)
    if not selected or any(
      isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.valves)
      for index in selected
    ):
      raise ValueError("channels must contain valid zero-based valve indexes")
    if len(set(selected)) != len(selected):
      raise ValueError("channels must not contain duplicates")
    if isinstance(cycles, bool) or not isinstance(cycles, int) or cycles < 1:
      raise ValueError("cycles must be a positive integer")
    CertusValve._validate_duration(prime_duration)
    CertusValve._validate_purge(purge_duration, purge_pulse_duration, purge_interval)
    profile = _speed(speed)
    valves = tuple(self.valves[index] for index in selected)

    async with self._operation_lock:
      for valve in valves:
        await valve._validate_prime_duration(prime_duration)
        await valve._require_ready()
      actual = await self.get_pressure()
      setpoint = await self._inco.get_number(_PRESSURE + ".Prop.SetPressure")
      if (
        not math.isfinite(actual)
        or not math.isfinite(setpoint)
        or not 0.051 <= setpoint <= 1
        or abs(actual - setpoint) > 0.01
      ):
        raise CertusFlexError("Clean cycle requires regulated dispensing pressure")
      await self._move("MoveZToPark", profile)
      await self._move("MoveXYToPurge", profile)
      await self._move("MoveZToPurge", profile)
      try:
        for cycle in range(cycles):
          logger.info("CERTUS FLEX clean cycle %d/%d on channels %s", cycle + 1, cycles, selected)
          for valve in valves:
            await valve._purge(purge_duration, purge_pulse_duration, purge_interval)
          await asyncio.sleep(0.25)
          for valve in valves:
            await valve._prime(prime_duration)
      except BaseException:
        for valve in valves:
          try:
            await self._inco.call(valve._path + ".Cmd.StopDispens")
          except BaseException:
            logger.exception("Could not confirm abort of CERTUS FLEX output %d", valve.output_id)
        raise
      await self._move("MoveZToPark", profile)
      await self._move("MoveXYToPark", profile)
