"""Control a CERTUS FLEX through its INCO controller interface."""

import asyncio
import json
import logging
import math
import time
from pathlib import Path
from typing import Literal, Optional, Sequence, Union

from pylabrobot.resources import Plate, Well

from ._inco import Argument, Inco
from ._inco_udp import IncoUDP
from .calibration import ValveCalibration
from .geometry import MotionBounds, PlateRegistration, Position
from .plans import DispensePlan, DispenseStep, Order, PreparedDispense, ordered_steps
from .run import ControllerFault, RunAborted, RunControl

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
_HEAD_IDS = {"flex5": 1, "flex8": 2, "flex4_angled": 3, "flex8_angled": 4}
_DATA = _ROOT + ".Data.Entity.Module."
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


class CertusValve:
  """One valve, addressed by its physical controller output."""

  def __init__(self, device: "CertusFlex", output_id: int) -> None:
    """Bind a valve to its parent and controller output."""
    self._device = device
    self.output_id = output_id
    self._path = _MODULE + f"Valve{output_id}"
    self.calibration: Optional[ValveCalibration] = None
    self.correction_factor = 1.0
    self.fluid: Optional[str] = None
    self.valve_serial: Optional[str] = None

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

  def _volume_duration(self, volume: float, correction_factor: Optional[float] = None) -> float:
    """Convert using this channel's measured curve and configured correction factor."""
    if self.calibration is None:
      raise ValueError("Assign this valve's calibration before using volumes")
    duration = self.calibration.duration_for_volume(
      volume,
      correction_factor=self.correction_factor if correction_factor is None else correction_factor,
    )
    self._validate_duration(duration)
    return duration

  async def _check_calibration_pressure(self) -> None:
    """Require the measured pressure for every calibrated opening."""
    if self.calibration is None:
      raise ValueError("Assign this valve's calibration before using volumes")
    actual = await self._device.get_pressure()
    setpoint = await self._device._inco.get_number(_PRESSURE + ".Prop.SetPressure")
    if any(
      not math.isfinite(p) or abs(p - self.calibration.pressure) > 0.01 for p in (actual, setpoint)
    ):
      raise CertusFlexError("Current pressure does not match the valve calibration")

  async def prime_volume(self, volume: float) -> None:
    """Prime a calibrated volume in µL at the current waste position.

    A continuous opening uses the channel's measured curve and correction factor.
    The requested volume must be within that curve and the controller prime limit.
    """
    async with self._device._operation_lock:
      duration = self._volume_duration(volume)
      await self._check_calibration_pressure()
      await self._prime(duration)

  def _purge_volume_durations(
    self, volume: float, pulse_volume: float
  ) -> tuple[tuple[float, float], ...]:
    """Prepare whole pulses and a calibrated remainder before any fluid command."""
    if _finite(volume, "volume") <= 0 or _finite(pulse_volume, "pulse_volume") <= 0:
      raise ValueError("Purge volumes must be positive")
    if pulse_volume > volume:
      raise ValueError("pulse_volume must not exceed volume")
    pulses = int(volume / pulse_volume)
    remainder = volume - pulses * pulse_volume
    if math.isclose(remainder, pulse_volume, rel_tol=1e-10, abs_tol=1e-12):
      pulses += 1
      remainder = 0
    duration = self._volume_duration(pulse_volume)
    result = [(pulses * duration, duration)] if pulses else []
    if not math.isclose(remainder, 0, abs_tol=1e-12):
      remainder_duration = self._volume_duration(remainder)
      result.append((remainder_duration, remainder_duration))
    for total, pulse in result:
      self._validate_purge(total, pulse, None)
    return tuple(result)

  async def purge_volume(self, volume: float, *, pulse_volume: float) -> None:
    """Purge µL as calibrated pulses at the current waste position.

    Each pulse (including a remainder) must lie within the measured curve. Volume
    is converted per pulse, rather than treating cumulative volume as one opening.
    """
    async with self._device._operation_lock:
      requests = self._purge_volume_durations(volume, pulse_volume)
      await self._check_calibration_pressure()
      try:
        for total, pulse in requests:
          await self._purge(total, pulse, None)
      except BaseException:
        await self._device._stop_outputs([self._device.valves.index(self)])
        raise

  async def dispense(self, volume: float, *, correction_factor: Optional[float] = None) -> None:
    """Dispense a calibrated volume in µL at the current position.

    Attach a :class:`ValveCalibration` to ``calibration`` first. The pressure
    setpoint and actual pressure must match that calibration within 0.01 bar.
    Supply the channel's CERTUS CONTROL volume correction factor when applicable;
    omitting it uses the configured channel factor (initially 1.0). The factor is applied before interpolation.
    """
    async with self._device._operation_lock:
      duration = self._volume_duration(volume, correction_factor)
      await self._check_calibration_pressure()
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
    self._active_run: Optional[RunControl] = None
    self._idle_run: Optional[RunControl] = None
    self._abort_lock = asyncio.Lock()
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
    if self._active_run is not None:
      self._active_run._request_abort()
    if self._idle_run is not None:
      self._idle_run._request_abort()
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
    if self._active_run is not None:
      self._active_run._check_abort()
    logger.info("CERTUS FLEX motion: %s", command)
    await self._inco.call(_POSITION + ".Cmd." + command, *arguments)
    await self._wait_state(_POSITION, ("ready",), initial="ready")

  async def validate_head(self) -> None:
    """Check the installed head against the explicitly selected channel mapping."""
    actual = await self._inco.get_number(_HEAD + ".Act.Id")
    if actual != _HEAD_IDS[self.head]:
      raise CertusFlexError(f"Installed head ID {actual} does not match {self.head}")

  async def configure_channel(
    self,
    channel: int,
    *,
    calibration: ValveCalibration,
    fluid: str,
    valve_serial: str,
    correction_factor: float = 1.0,
  ) -> None:
    """Validate declared fluid/valve identity and installed head, then bind calibration.

    Fluid and valve serial are operator declarations; the controller does not
    independently identify the liquid or individual valve serial numbers.
    """
    if (
      isinstance(channel, bool)
      or not isinstance(channel, int)
      or not 0 <= channel < len(self.valves)
    ):
      raise ValueError("Invalid channel")
    calibration.validate_for(fluid=fluid, valve_serial=valve_serial)
    if not 0.5 <= _finite(correction_factor, "correction_factor") <= 1.5:
      raise ValueError("correction_factor must be between 0.5 and 1.5")
    async with self._operation_lock:
      await self.validate_head()
      valve = self.valves[channel]
      valve.calibration = calibration
      valve.fluid, valve.valve_serial = fluid.strip(), valve_serial.strip()
      valve.correction_factor = correction_factor

  def save_configuration(self, path: str | Path) -> None:
    """Save declared channel identities, correction factors, and curves for this device/head."""
    if self.serial_number is None:
      raise RuntimeError("Call setup before saving configuration")
    channels = []
    for index, valve in enumerate(self.valves):
      if valve.calibration is not None:
        if not valve.fluid or not valve.valve_serial:
          raise ValueError("Use configure_channel before saving a reusable channel configuration")
        valve.calibration.validate_for(fluid=valve.fluid, valve_serial=valve.valve_serial)
        channels.append(
          {
            "channel": index,
            "fluid": valve.fluid,
            "valve_serial": valve.valve_serial,
            "correction_factor": valve.correction_factor,
            "calibration": valve.calibration.to_dict(),
          }
        )
    Path(path).write_text(
      json.dumps(
        {
          "version": 1,
          "serial_number": self.serial_number,
          "head": self.head,
          "channels": channels,
        },
        indent=2,
        allow_nan=False,
      )
      + "\n"
    )

  async def load_configuration(self, path: str | Path) -> None:
    """Atomically load declared channel configuration after identity and head checks.

    Loading makes no physical changes and does not set pressure. Channels absent
    from the file are cleared so old calibration is not silently retained.
    """
    data = json.loads(Path(path).read_text())
    if (
      data.get("version") != 1
      or self.serial_number is None
      or data.get("serial_number") != self.serial_number
      or data.get("head") != self.head
    ):
      raise ValueError("Configuration version, device serial, or head does not match")
    prepared = {}
    for entry in data["channels"]:
      channel = entry["channel"]
      if (
        isinstance(channel, bool)
        or not isinstance(channel, int)
        or not 0 <= channel < len(self.valves)
        or channel in prepared
      ):
        raise ValueError("Invalid or duplicated configured channel")
      calibration = ValveCalibration.from_dict(entry["calibration"])
      calibration.validate_for(fluid=entry["fluid"], valve_serial=entry["valve_serial"])
      factor = entry["correction_factor"]
      if not 0.5 <= _finite(factor, "correction_factor") <= 1.5:
        raise ValueError("Invalid correction factor")
      prepared[channel] = (calibration, entry["fluid"], entry["valve_serial"], factor)
    async with self._operation_lock:
      await self.validate_head()
      for index, valve in enumerate(self.valves):
        if index in prepared:
          valve.calibration, valve.fluid, valve.valve_serial, valve.correction_factor = prepared[
            index
          ]
        else:
          valve.calibration, valve.fluid, valve.valve_serial, valve.correction_factor = (
            None,
            None,
            None,
            1.0,
          )

  async def read_plate_registration(
    self,
    plate: Plate,
    *,
    bounds: MotionBounds,
    height: Optional[float] = None,
  ) -> PlateRegistration:
    """Read the active controller plate definition and channel offsets without moving.

    The controller plate rows, columns, and pitch must match the PLR resource.
    For another plate geometry, construct PlateRegistration with measured origins.
    Bounds must be supplied from a verified machine envelope, not guessed travel limits.
    """
    async with self._operation_lock:
      await self.validate_head()
      if self.serial_number is None:
        raise RuntimeError("Call setup before reading plate registration")
      prefix = _DATA + "Wellplatedefinition.Prop."
      for name, expected in (
        ("RowNum", plate.num_items_y),
        ("ColNum", plate.num_items_x),
        ("XGrid", plate.item_dx),
        ("YGrid", plate.item_dy),
      ):
        actual = await self._inco.get_number(prefix + name)
        if not math.isclose(actual, expected, abs_tol=0.01, rel_tol=0):
          raise ValueError(f"Controller plate {name}={actual} does not match {expected}")
      origin = (
        await self._inco.get_number(prefix + "XPosFirstWell"),
        -await self._inco.get_number(prefix + "YPosFirstWell"),
      )
      offsets: list[Position] = []
      for valve in self.valves:
        xyz = [
          await self._inco.get_number(_DATA + f"Position.Valve.Prop.{axis}Valve{valve.output_id}")
          for axis in "XYZ"
        ]
        offsets.append((xyz[0], xyz[1], xyz[2]))
      plate_height = (
        await self._inco.get_number(prefix + "WellplateHeight") if height is None else height
      )
      clearance = await self._inco.get_number(_DATA + "Position.Park.Prop.PosZ")
      return PlateRegistration(
        plate,
        first_well=origin,
        valve_offsets=tuple(offsets),
        height=plate_height,
        clearance=clearance,
        bounds=bounds,
        serial_number=self.serial_number,
        head=self.head,
      )

  async def _check_registration(self, registration: PlateRegistration) -> None:
    """Check resource geometry, controller identity, and installed head before motion."""
    registration.validate()
    if registration.serial_number != self.serial_number or registration.head != self.head:
      raise ValueError("Plate registration belongs to another dispenser or head")
    await self.validate_head()

  async def _move_registered(
    self,
    registration: PlateRegistration,
    target: Position,
    profile: str,
  ) -> None:
    """Raise vertically, traverse at clearance, lower, and verify position under the lock."""
    current = await self.get_position()
    registration.bounds.check(current)
    high = (current[0], current[1], registration.clearance)
    registration.bounds.check(high)
    registration.bounds.check(target)
    await self._move("MoveXYZ", *high, profile)
    await self._move("MoveXYZ", target[0], target[1], registration.clearance, profile)
    await self._move("MoveXYZ", *target, profile)
    actual = await self.get_position()
    if any(abs(a - b) > 0.05 for a, b in zip(actual, target)):
      raise CertusFlexError(f"Position {actual} does not match target {target}")

  async def move_to_well(
    self,
    registration: PlateRegistration,
    well: Well,
    *,
    channel: int,
    speed: Speed = "slow",
  ) -> None:
    """Position a registered well under one channel using vertical clearance moves."""
    target = registration.target(well, channel)
    profile = _speed(speed)
    async with self._operation_lock:
      await self._check_registration(registration)
      await self._move_registered(registration, target, profile)

  async def dispense_to_well(
    self,
    registration: PlateRegistration,
    well: Well,
    volume: float,
    *,
    channel: int,
    correction_factor: Optional[float] = None,
    speed: Speed = "slow",
  ) -> None:
    """Move to a PLR well and deliver a calibrated volume in µL.

    Capacity is checked against the well's tracked starting volume. This method
    does not infer existing liquid or update the resource volume tracker.
    """
    target = registration.target(well, channel)
    profile = _speed(speed)
    valve = self.valves[channel]
    if valve.calibration is None:
      raise ValueError("Assign the selected valve's calibration before dispensing")
    valve._volume_duration(volume, correction_factor)
    if volume + well.tracker.get_used_volume() > well.max_volume:
      raise ValueError("Dispense would exceed the well capacity")
    async with self._operation_lock:
      duration = valve._volume_duration(volume, correction_factor)
      await self._check_registration(registration)
      await valve._require_ready()
      await valve._check_calibration_pressure()
      await self._move_registered(registration, target, profile)
      await valve._dispense_for(duration)

  def plan_dispense(
    self,
    registration: PlateRegistration,
    steps: Sequence[DispenseStep],
    *,
    repeats: int = 1,
    order: Order = "given",
    parallel: bool = True,
  ) -> DispensePlan:
    """Validate an entire plate recipe before any hardware commands.

    Volume limits include repeats, all channels, and the well's tracked initial
    contents. Timed requests cannot provide a capacity guarantee. Calibration
    and durations are frozen in the returned plan; rebuild after changing them.
    """
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
      raise ValueError("repeats must be a positive integer")
    registration.validate()
    if registration.head != self.head or len(registration.valve_offsets) != len(self.valves):
      raise ValueError("Registration does not match selected head")
    for step in steps:
      registration.target(step.well, step.channel)
    ordered = ordered_steps(steps, registration, order)
    prepared: list[PreparedDispense] = []
    totals: dict[int, float] = {}
    for _ in range(repeats):
      for step in ordered:
        position = registration.target(step.well, step.channel)
        if step.volume == 0:
          continue
        pressure = None
        if step.volume is not None:
          calibration = self.valves[step.channel].calibration
          if calibration is None:
            raise ValueError(f"Channel {step.channel} needs a calibration")
          duration = calibration.duration_for_volume(
            step.volume,
            correction_factor=self.valves[step.channel].correction_factor
            if step.correction_factor is None
            else step.correction_factor,
          )
          pressure = calibration.pressure
          total = totals.get(id(step.well), step.well.tracker.get_used_volume()) + step.volume
          if total > step.well.max_volume:
            raise ValueError(f"Plan exceeds capacity of {step.well.name}")
          totals[id(step.well)] = total
        else:
          assert step.duration is not None
          duration = step.duration
        CertusValve._validate_duration(duration)
        prepared.append(PreparedDispense(step, position, duration, pressure))
    pressures = {item.pressure for item in prepared if item.pressure is not None}
    if len(pressures) > 1:
      raise ValueError("All channels in a plan must use the same calibration pressure")
    groups: list[list[PreparedDispense]] = []
    for item in prepared:
      if (
        parallel
        and groups
        and item.step.channel not in [p.step.channel for p in groups[-1]]
        and all(abs(a - b) <= 0.001 for a, b in zip(item.position, groups[-1][0].position))
      ):
        groups[-1].append(item)
      else:
        groups.append([item])
    return DispensePlan(registration, tuple(tuple(group) for group in groups))

  async def _check_plan(self, plan: DispensePlan) -> None:
    """Recheck geometry, capacity, regulated pressure, and all participating valves."""
    original = tuple(item for group in plan.groups for item in group)
    rebuilt = self.plan_dispense(
      plan.registration, [item.step for item in original], parallel=False
    )
    if tuple(item for group in rebuilt.groups for item in group) != original:
      raise ValueError("Prepared plan or calibration changed; rebuild the plan")
    await self._check_registration(plan.registration)
    totals: dict[int, float] = {}
    for group in plan.groups:
      channels: set[int] = set()
      if not group:
        raise ValueError("Empty dispense group")
      for item in group:
        target = plan.registration.target(item.step.well, item.step.channel)
        if target != item.position or item.step.channel in channels:
          raise ValueError("Invalid or changed prepared dispense group")
        if any(abs(a - b) > 0.001 for a, b in zip(target, group[0].position)):
          raise ValueError("Grouped channels do not share a stage position")
        channels.add(item.step.channel)
        CertusValve._validate_duration(item.duration)
        if item.step.volume is not None:
          total = (
            totals.get(id(item.step.well), item.step.well.tracker.get_used_volume())
            + item.step.volume
          )
          if total > item.step.well.max_volume:
            raise ValueError("Plan exceeds current well capacity")
          totals[id(item.step.well)] = total
        await self.valves[item.step.channel]._require_ready()
    actual = await self.get_pressure()
    setpoint = await self._inco.get_number(_PRESSURE + ".Prop.SetPressure")
    if not math.isfinite(actual) or not 0.051 <= setpoint <= 1 or abs(actual - setpoint) > 0.01:
      raise CertusFlexError("Plate dispensing requires regulated pressure")
    if any(
      item.pressure is not None and abs(item.pressure - setpoint) > 0.01
      for group in plan.groups
      for item in group
    ):
      raise CertusFlexError("Plan calibration pressure does not match the regulator")

  async def _stop_outputs(self, channels: Sequence[int]) -> None:
    """Best-effort stop of selected outputs without masking a failed operation."""
    for channel in set(channels):
      try:
        await self._inco.call(self.valves[channel]._path + ".Cmd.StopDispens")
      except BaseException:
        logger.exception("Could not confirm stopping CERTUS channel %d", channel)

  async def _close_outputs(self, channels: Sequence[int]) -> None:
    """Stop and close selected valves, reporting failures after attempting every output."""
    failures = []
    for channel in channels:
      for command in ("StopDispens", "CloseValve"):
        try:
          await self._inco.call(self.valves[channel]._path + ".Cmd." + command)
        except Exception as exc:
          failures.append(f"channel {channel} {command}: {exc}")
    if failures:
      raise CertusFlexError("Could not confirm valve closure: " + "; ".join(failures))
    await self._sync_valves()
    for channel in channels:
      await self._wait_state(self.valves[channel]._path, ("ready", "on"))

  async def close_all(self) -> None:
    """Close all selected-head valves after any active operation finishes.

    Use :meth:`abort` to interrupt an active plan instead of waiting for its lock.
    Neither method discharges pressure or stops an in-flight stage move.
    """
    async with self._operation_lock:
      await self._close_outputs(range(len(self.valves)))

  async def abort(self) -> None:
    """Abort the active controlled operation and close valves without a recovery move.

    A submitted dose remains uncertain if completion was not confirmed. An in-flight
    stage move may finish. This is a software stop, not the physical emergency stop.
    Failure to communicate raises; it must not be interpreted as confirmed closure.
    """
    async with self._abort_lock:
      if self._idle_run is not None:
        self._idle_run._request_abort()
      if self._active_run is not None:
        self._active_run._request_abort()
        await self._close_outputs(range(len(self.valves)))
        return
    await self.close_all()

  async def get_faults(self) -> tuple[ControllerFault, ...]:
    """Read pending native issues without acknowledging or clearing them."""
    count = await self._inco.get_number(_ROOT + ".Msg.Msg")
    if not math.isfinite(count) or count != int(count) or not 0 <= count <= 1024:
      raise CertusFlexError(f"Invalid controller issue count: {count}")
    faults = []
    for index in range(int(count)):
      entity = await self._inco.get_string(_ROOT + f".Msg.Entity!Object{index}")
      if not entity or any(c in entity for c in ".!()\\"):
        raise CertusFlexError("Invalid controller issue entity")
      path = _ROOT + ".Msg.Entity." + entity + "."
      faults.append(
        ControllerFault(
          entity=entity,
          identifier=int(await self._inco.get_number(path + "Id")),
          code=int(await self._inco.get_number(path + "Code")),
          task=await self._inco.get_string(path + "Task"),
          text=await self._inco.get_string(path + "Text"),
          flags=int(await self._inco.get_number(path + "Flags")),
          native_time=await self._inco.get_number(path + "Time"),
        )
      )
    return tuple(faults)

  async def execute_plan(
    self, plan: DispensePlan, *, speed: Speed = "slow", control: Optional[RunControl] = None
  ) -> int:
    """Execute grouped requests and return the number of confirmed doses.

    Pause/resume applies at completed, raised group boundaries. Aborted and failed
    runs cannot resume. The progress snapshot identifies an unconfirmed group.
    Consecutive valves are queued before one barrier; simultaneous edges are not
    guaranteed. Park and pressure discharge remain explicit.
    """
    profile = _speed(speed)
    run = RunControl() if control is None else control
    channels = sorted({p.step.channel for group in plan.groups for p in group})
    async with self._operation_lock:
      run._start(len(plan.groups))
      self._active_run = run
      try:
        if plan.groups:
          await self._check_plan(plan)
        for index, group in enumerate(plan.groups):
          await run._boundary()
          await self._move_registered(plan.registration, group[0].position, profile)
          run._check_abort()
          for item in group:
            await self.valves[item.step.channel]._require_ready()
          for item in group:
            run._check_abort()
            run._update(uncertain_group=index)
            await self._inco.call(
              self.valves[item.step.channel]._path + ".Cmd.Dispens", item.duration * 1e6
            )
          await self._sync_valves()
          run._check_abort()
          for item in group:
            await self.valves[item.step.channel]._require_ready()
          run._check_abort()
          run._update(
            completed_groups=index + 1,
            completed_doses=run.progress.completed_doses + len(group),
            uncertain_group=None,
          )
          await self._move(
            "MoveXYZ",
            group[0].position[0],
            group[0].position[1],
            plan.registration.clearance,
            profile,
          )
        run._check_abort()
        run._update(state="completed")
      except BaseException as exc:
        run._update(state="aborted" if isinstance(exc, RunAborted) else "failed", error=str(exc))
        await self._stop_outputs(channels)
        raise
      finally:
        # Do not release the operation lock while an abort is still closing outputs.
        async with self._abort_lock:
          self._active_run = None
    return run.progress.completed_doses

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

  def _channels(self, channels: Sequence[int]) -> tuple[int, ...]:
    """Validate a nonempty selection without duplicates before motion or opening."""
    selected = tuple(channels)
    if not selected or any(
      isinstance(c, bool) or not isinstance(c, int) or not 0 <= c < len(self.valves)
      for c in selected
    ):
      raise ValueError("channels must contain valid zero-based valve indexes")
    if len(set(selected)) != len(selected):
      raise ValueError("channels must not contain duplicates")
    return selected

  async def _maintenance_pressure(self) -> None:
    """Require stable regulated pressure without changing its setpoint."""
    actual = await self.get_pressure()
    setpoint = await self._inco.get_number(_PRESSURE + ".Prop.SetPressure")
    if not math.isfinite(actual) or not 0.051 <= setpoint <= 1 or abs(actual - setpoint) > 0.01:
      raise CertusFlexError("Maintenance requires regulated dispensing pressure")

  async def flush(
    self,
    channels: Sequence[int],
    *,
    duration: float,
    speed: Speed = "slow",
    control: Optional[RunControl] = None,
  ) -> None:
    """Flush continuously into prime waste for a controller-enforced duration in seconds.

    Valves are queued together using timed Prime, avoiding an indefinite opening on
    connection loss. Duration must fit every selected valve's PrimeTimeout. Run in
    a task and call ``abort()`` to stop early. Success parks; pressure is unchanged.
    """
    selected = self._channels(channels)
    CertusValve._validate_duration(duration)
    profile = _speed(speed)
    run = RunControl() if control is None else control
    async with self._operation_lock:
      run._start(1)
      self._active_run = run
      try:
        await self.validate_head()
        for channel in selected:
          await self.valves[channel]._validate_prime_duration(duration)
          await self.valves[channel]._require_ready()
        await self._maintenance_pressure()
        await run._boundary()
        await self._move("MoveZToPark", profile)
        await self._move("MoveXYToPrime", profile)
        await self._move("MoveZToPrime", profile)
        for channel in selected:
          run._check_abort()
          run._update(uncertain_group=0)
          await self._inco.call(self.valves[channel]._path + ".Cmd.Prime", duration * 1e6)
        await self._sync_valves()
        run._check_abort()
        for channel in selected:
          await self.valves[channel]._require_ready()
        run._check_abort()
        run._update(completed_groups=1, completed_doses=len(selected), uncertain_group=None)
        await self._move("MoveZToPark", profile)
        await self._move("MoveXYToPark", profile)
        run._check_abort()
        run._update(state="completed")
      except BaseException as exc:
        run._update(state="aborted" if isinstance(exc, RunAborted) else "failed", error=str(exc))
        if run.progress.uncertain_group is not None:
          await self._stop_outputs(selected)
        raise
      finally:
        async with self._abort_lock:
          self._active_run = None

  async def idle_purge(
    self,
    channels: Sequence[int],
    *,
    interval: float,
    cycles: int,
    duration: float,
    pulse_duration: float,
    speed: Speed = "slow",
    control: Optional[RunControl] = None,
  ) -> None:
    """Run a finite host-scheduled purge into waste whenever the device is available.

    Wait ``interval`` seconds before each cycle, then acquire the operation lock.
    Active plate plans (including paused plans) finish before a cycle can move.
    Each cycle parks on success and releases the lock. No persistent firmware timer
    is installed. Abort wakes the interval wait; cancellation never repeats a pulse.
    """
    selected = self._channels(channels)
    if _finite(interval, "interval") <= 0:
      raise ValueError("interval must be positive")
    if isinstance(cycles, bool) or not isinstance(cycles, int) or cycles < 1:
      raise ValueError("cycles must be a positive integer")
    CertusValve._validate_purge(duration, pulse_duration, None)
    profile = _speed(speed)
    if self._idle_run is not None:
      raise CertusFlexError("An idle-purge schedule is already active")
    run = RunControl() if control is None else control
    run._start(cycles)
    self._idle_run = run
    try:
      for cycle in range(cycles):
        await run._sleep(interval)
        await run._boundary()
        async with self._operation_lock:
          self._active_run = run
          try:
            run._check_abort()
            await self.validate_head()
            await self._maintenance_pressure()
            for channel in selected:
              await self.valves[channel]._require_ready()
            await self._move("MoveZToPark", profile)
            await self._move("MoveXYToPurge", profile)
            await self._move("MoveZToPurge", profile)
            for channel in selected:
              run._check_abort()
              run._update(uncertain_group=cycle)
              await self.valves[channel]._purge(duration, pulse_duration, None)
              run._check_abort()
              run._update(completed_doses=run.progress.completed_doses + 1)
            run._update(completed_groups=cycle + 1, uncertain_group=None)
            await self._move("MoveZToPark", profile)
            await self._move("MoveXYToPark", profile)
          except BaseException:
            await self._stop_outputs(selected)
            raise
          finally:
            async with self._abort_lock:
              self._active_run = None
      run._check_abort()
      run._update(state="completed")
    except BaseException as exc:
      run._update(state="aborted" if isinstance(exc, RunAborted) else "failed", error=str(exc))
      raise
    finally:
      self._idle_run = None

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
