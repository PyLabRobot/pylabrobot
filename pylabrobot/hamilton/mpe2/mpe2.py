"""The Hamilton MPE2: a positive pressure station for filter plates.

The MPE2 takes a filter plate on its shuttle, with or without a collection plate under it, clamps
it under its manifold and pushes the liquid through with gas (pressure or flow control points),
into the collection plate or into a waste container that its vacuum pump draws on. Each step here
is one of Hamilton's HSL library (``HSLMPELib``), sent as Hamilton's own driver sends it.

Processing uses the firmware's own process commands rather than driving the pressure valves from
PLR: the firmware watches the intrusion sensors and holds the pressure timing itself, which is the
exception PLR's driver guide allows for firmware routines.

Pressures are in psi and gas flows in standard L/min, not PLR's default bar: the MPE2's gauges,
Hamilton's manual, its HSL library and the firmware all use them, so a method's values are taken
as they are written. The names say so (``minimum_input_pressure_psi``, ``input_psi``); a
``PlateStep``'s value is psi for pressure and standard L/min for flow.

Example::

  mpe = MPE2()  # found on USB
  await mpe.setup()
  await mpe.initialize()
  await mpe.filter_plate_placed(height=45.0, nozzle_height=55.0)
  await mpe.process_filter_to_waste_container([PlateStep("pressure", 5, 5)])
  await mpe.filter_plate_removed()
  await mpe.stop()
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from contextlib import asynccontextmanager
from typing import (
  Any,
  AsyncIterator,
  Awaitable,
  Callable,
  Dict,
  Literal,
  Optional,
  Protocol,
  Sequence,
  Tuple,
  Type,
  Union,
  cast,
)

from pylabrobot.hamilton.mpe2 import commands as cmd
from pylabrobot.hamilton.mpe2 import errors
from pylabrobot.hamilton.mpe2.confirmed_firmware_versions import CONFIRMED_FIRMWARE_VERSIONS
from pylabrobot.hamilton.mpe2.connection import MPE2Client
from pylabrobot.hamilton.mpe2.control_points import PlateStep, to_firmware, total_duration
from pylabrobot.hamilton.mpe2.errors import (
  MPE2ConnectionError,
  MPE2Error,
  MPE2InputPressureError,
  MPE2PressureError,
  MPE2StateError,
  MPE2TimeoutError,
  MPE2VacuumError,
  MPE2WasteError,
  RecoveryReport,
)
from pylabrobot.hamilton.mpe2.readings import (
  MPE2Status,
  PressureReadings,
  PressureRecording,
  PressureSample,
)
from pylabrobot.hamilton.mpe2.states import (
  CLAMPED,
  RETRIEVING,
  WASTE,
  WITH_COLLECTION_PLATE,
  MPE2State,
  S,
  WasteContainerNumber,
  state_name,
)
from pylabrobot.hamilton.mpe2.vacuum import NO_ADDRESS, Vacuum, stop_quietly
from pylabrobot.hamilton.transport.tcp.hoi_error import HoiError
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.hamilton.transport.tcp.protocol import Hoi2Action
from pylabrobot.resources import Coordinate, PlateHolder

logger = logging.getLogger(__name__)

# How long a query, or the acceptance of a command, may take, in seconds.
DEFAULT_TIMEOUT = 5.0
# How long a plate step may run: Hamilton's driver allows 60 s (Hamilton.MPE.UserSettings.xml).
PLATE_STEP_TIMEOUT = 60.0
INITIALIZE_TIMEOUT = 120.0
POLL_INTERVAL = 0.2
# How long recover() waits for an aborted command to stop.
ABORT_TIMEOUT = 30.0

# Commands that may be sent again when the link broke during them: they only make things safer.
_SAFE_TO_REPEAT = (cmd.StopVacuum, cmd.Abort)
# What happens after a failure: a pressure error (the gas, the valves, the
# vacuum, the waste) recovers by itself, the plate brought back. Anything else (an intrusion, a
# motor, a timeout, an error not known) stops everything that runs, moves nothing, and holds:
# nothing that moves is sent until someone has looked and calls recover().
_RECOVER_BY_ITSELF = (MPE2PressureError, MPE2VacuumError, MPE2WasteError)
# The objects whose errors the driver names.
_KNOWN_OBJECTS = (
  cmd.SPE_CONTROL_PATH,
  cmd.WASTE_SYSTEM_PATH,
  cmd.VACUUM_PATH,
  cmd.VACUUM_CALIBRATION_PATH,
  cmd.VALVES_AND_SENSORS_PATH,
  "SPEMainControllerRoot.MotorControllerRoot.SPEMotorControl",
  "SPEMainControllerRoot.MotorControllerRoot.UpperLifterAxis",
  "SPEMainControllerRoot.MotorControllerRoot.LowerLifterAxis",
  "SPEMainControllerRoot.MotorControllerRoot.PlateShuttleAxis",
  "SPEMainControllerRoot.MotorControllerRoot.DispenseAxis",
  "SPEMainControllerRoot.MotorControllerRoot.PlateShuttleStepper",
  "SPEMainControllerRoot.MotorControllerRoot.UpperLifterStepper",
  "SPEMainControllerRoot.MotorControllerRoot.LowerLifterStepper",
  "SPEMainControllerRoot.HighPressureValveControl",
  "SPEMainControllerRoot.LowPressureValveControl",
  "SPEMainControllerRoot.ManifoldPressureControl",
)
_PROCESS_COMMANDS = (cmd.ProcessFilterPlateToWaste, cmd.ProcessFilterPlateToCollection)


class MPE2Connection(Protocol):
  """What the MPE2 needs from a connection: ``MPE2Client``, or ``MPE2Simulator``."""

  async def setup(self) -> None:
    """Open the connection."""

  async def stop(self) -> None:
    """Close the connection."""

  async def resolve_path(self, path: str) -> Address:
    """The address of the firmware object at a dotted path."""

  async def execute(self, command: cmd.MPE2Command, *, read_timeout: Optional[float] = None) -> Any:
    """Send one command and return its parsed answer."""


class MPE2:
  """A Hamilton MPE2, controlled over its USB serial port or TCP.

  ``MPE2()`` finds an MPE2 on USB by itself at ``setup()``, on Windows, Linux and macOS (the only
  FTDI FT-X port plugged in); with several, give ``device_id`` or ``port``.

  Its two plate sites are ``filter_plate_holder`` and ``collection_plate_holder``, for placing it
  on a deck; on Hamilton's STAR base (VENUS's ``STAR_MPE2_Base`` template) the collection plate's
  site is 13.95 mm below the filter plate's, at the same x and y.

  Args:
    name: the MPE2's name; its plate holders are named after it.
    port: the USB serial port ("COM4", "/dev/ttyUSB0"), for an MPE2 on USB.
    host: its address, for an MPE2 on the network.
    tcp_port: its TCP port; Hamilton's is 2000.
    timeout: how long a query, or the acceptance of a command, may take, in seconds.
    recover_on_error: after a pressure error, recover by itself, bringing the plate back. Other
      errors always halt and hold until ``recover()`` is called.
    minimum_input_pressure_psi: refuse a clamp or process step below this gas pressure, in psi. None
      (the default) only warns. The firmware itself still aborts a
      process step below 80 psi, which can't be changed.
    device_id: the USB serial number of the MPE2 wanted, when several are plugged in (as on its
      FTDI chip; Windows adds a letter, "D30G5GV5A", and either is taken).
    io: a connection to use instead of a new one, such as ``MPE2Simulator()``.
  """

  def __init__(
    self,
    name: str = "mpe2",
    *,
    port: Optional[str] = None,
    host: Optional[str] = None,
    tcp_port: int = 2000,
    device_id: Optional[str] = None,
    io: Optional[MPE2Connection] = None,
    timeout: float = DEFAULT_TIMEOUT,
    recover_on_error: bool = True,
    minimum_input_pressure_psi: Optional[float] = None,
  ):
    if io is None:
      if port is not None and host is not None:
        raise ValueError("Give the MPE2's serial port or its host, not both.")
      if device_id is not None and (port is not None or host is not None):
        raise ValueError("A device id chooses among USB MPE2s: give it without a port or host.")
      io = MPE2Client(
        host=host,
        port=tcp_port,
        serial_port=port,
        usb=host is None,
        serial_number=device_id,
      )
    elif port is not None or host is not None or device_id is not None:
      raise ValueError("Give io, or a port, host or device id, not both.")
    if timeout <= 0:
      raise ValueError(f"The timeout is longer than 0 s ({timeout}).")
    self.name = name
    self.io = io
    # The plate sites, in an SBS footprint (127.76 x 85.48 mm).
    self.filter_plate_holder = PlateHolder(
      name=f"{name}_filter_plate_holder",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      pedestal_size_z=0,
      child_location=Coordinate.zero(),
    )
    self.collection_plate_holder = PlateHolder(
      name=f"{name}_collection_plate_holder",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      pedestal_size_z=0,
      child_location=Coordinate.zero(),
    )
    self.timeout = timeout
    self.recover_on_error = recover_on_error
    self.minimum_input_pressure_psi = minimum_input_pressure_psi
    # The error the MPE2 holds after, until recover() is called; None when not held.
    self.held_after: Optional[MPE2Error] = None
    self.vacuum = Vacuum(self)
    self.manifold: Optional[cmd.ManifoldInfo] = None
    self.control_point_ranges: Optional[cmd.ControlPointRanges] = None
    self.maximum_combined_height: float = cmd.MAXIMUM_STACK_HEIGHT
    self.firmware_version: Optional[str] = None
    self._addresses: Dict[str, Address] = {}
    self._object_names: Dict[Tuple[int, int, int], str] = {}
    self._uncertain = False
    # One reconnection at a time: the pressure recording and a command may both find the link
    # uncertain.
    self._reconnect_lock = asyncio.Lock()
    self._reconnections = 0
    self._set_up = False
    self._recovering = False
    self._vacuum_on_at_setup: Optional[bool] = None

  # ---------------------------------------------------------------------------
  # Connection
  # ---------------------------------------------------------------------------

  async def setup(self) -> None:
    """Connect, and read the firmware's version, manifold, limits and state. Nothing moves."""
    logger.warning(
      "The MPE2 driver has run on one MPE2 (firmware V1.9.5.619, a 96-port manifold, over USB): "
      "queries, initialize, the vacuum, the plate steps and processing. Other firmware versions, "
      "manifolds and TCP are untested; please report what works."
    )
    await self.io.setup()
    self._set_up = True
    try:
      self.firmware_version = await self._query(cmd.GetModuleVersion)
      if not any(v in (self.firmware_version or "") for v in CONFIRMED_FIRMWARE_VERSIONS):
        logger.warning("MPE2 firmware %r is not among those confirmed.", self.firmware_version)
      self.manifold = await self._query(cmd.GetManifoldInformation)
      self.control_point_ranges = await self._query(cmd.GetControlPointRanges)
      self.maximum_combined_height = await self._query(cmd.GetMaximumCombinedHeight)
      await self._resolve_known_objects()
      try:
        self._vacuum_on_at_setup = await self.vacuum.request_is_on()
      except MPE2Error as exc:
        logger.warning("Could not read the MPE2's vacuum pump at setup: %s", exc)
        self._vacuum_on_at_setup = None
      if self._vacuum_on_at_setup is not False:
        logger.warning(
          "The MPE2's vacuum pump runs (or can't be read) at setup; stop() or vacuum.stop() "
          "stops it."
        )
      state = await self._state()
    except BaseException:
      self._set_up = False
      await self.io.stop()
      raise
    assert self.manifold is not None
    logger.info(
      "MPE2 connected: %s, %d-port manifold %s, plates stacked up to %.1f mm, state %s",
      self.firmware_version,
      self.manifold.number_of_ports,
      self.manifold.serial_number,
      self.maximum_combined_height,
      state_name(state),
    )

  async def _resolve_known_objects(self) -> None:
    """Look up the objects that may report errors, so their errors are named. They only name
    errors, so a slow lookup doesn't fail setup: the lookups share one deadline, and a timed-out
    one reconnects before the next (its exchange may still be open)."""
    deadline = time.monotonic() + self.timeout * 6
    for path in _KNOWN_OBJECTS:
      if time.monotonic() > deadline:
        logger.warning("Looking up the MPE2's objects took too long; %s and after unnamed.", path)
        break
      try:
        if self._uncertain:
          await self._reconnect()
        await self._address(path)
      except (KeyError, MPE2Error, asyncio.TimeoutError) as exc:
        logger.debug("No %s on this MPE2: %s", path, exc)

  async def stop(self) -> None:
    """Stop everything that runs (as ``halt()``: a running command, the vacuum pump), then close
    the connection. Nothing moves.

    The pump is stopped even if it ran at setup, a deliberate exception to leaving a device as it
    was found: a pump left running is most likely a crashed session's, and nothing should run
    unwatched after closing. If stopping fails, the connection is still closed and the failure
    raised.
    """
    report: Optional[RecoveryReport] = None
    try:
      if self._set_up:
        report = await self.halt()
    finally:
      self._set_up = False
      await self.io.stop()
    if report is not None and report.failed_steps:
      raise MPE2Error(
        f"Something may still run on the MPE2: stopping it failed while closing ({report}). "
        "Check the MPE2."
      )

  # ---------------------------------------------------------------------------
  # Sending
  # ---------------------------------------------------------------------------

  async def _address(self, path: str) -> Address:
    """The address of a firmware object, looked up once and kept."""
    address = self._addresses.get(path)
    if address is None:
      try:
        address = await asyncio.wait_for(self.io.resolve_path(path), self.timeout * 6)
      except (asyncio.TimeoutError, asyncio.CancelledError):
        # The lookup's exchange may still be open: the session can't be trusted until set up again.
        self._uncertain = True
        raise
      self._addresses[path] = address
      self._object_names[(address.module, address.node, address.object)] = path.split(".")[-1]
    return address

  async def _reconnect(self) -> None:
    """Set the connection up again after an exchange that failed or may not have finished."""
    before = self._reconnections
    async with self._reconnect_lock:
      if self._reconnections != before:
        return  # reconnected meanwhile by whoever held the lock
      logger.warning("Reconnecting to the MPE2 after a lost or uncertain exchange.")
      try:
        await asyncio.wait_for(self.io.stop(), self.timeout)
      except Exception as exc:  # noqa: BLE001 - the old link may be gone already
        logger.debug("Closing the old link: %s", exc)
      await asyncio.wait_for(self.io.setup(), self.timeout * 6)
      self._addresses.clear()
      self._uncertain = False
      self._reconnections += 1

  async def _send(self, command: cmd.MPE2Command, timeout: Optional[float] = None) -> Any:
    """Send one command and return its answer, within a hard timeout.

    Args:
      command: the command; its ``dest`` is filled in from its ``target_path()``.
      timeout: how long the answer may take, in seconds; the driver's ``timeout`` if None.
    """
    if not self._set_up:
      raise RuntimeError("The MPE2 is not set up: call setup() first.")
    if self._uncertain:
      try:
        await self._reconnect()
      except Exception as exc:
        raise MPE2ConnectionError(f"The MPE2's link could not be restored: {exc}") from exc
    name = type(command).__name__
    limit = self.timeout if timeout is None else timeout
    # Queries, stops and aborts may be sent again after a broken link; anything else is not,
    # as it may have reached the MPE2 already.
    repeatable = command.action_code == Hoi2Action.STATUS_REQUEST or isinstance(
      command, _SAFE_TO_REPEAT
    )
    for attempt in (1, 2):
      try:
        address = await self._address(command.target_path())
        command = dataclasses.replace(command, dest=address)
        return await asyncio.wait_for(self.io.execute(command, read_timeout=limit), limit + 1.0)
      except HoiError as exc:
        raise errors.error_from_hoi(name, exc, self._object_names) from exc
      except asyncio.CancelledError:
        # Interrupted mid-exchange: the session can't be trusted until it is set up again.
        self._uncertain = True
        raise
      except (asyncio.TimeoutError, TimeoutError) as exc:
        self._uncertain = True
        raise MPE2TimeoutError(f"The MPE2 did not answer {name} within {limit:.0f} s.") from exc
      except (ConnectionError, OSError) as exc:
        self._uncertain = True
        if attempt == 2 or not repeatable:
          raise MPE2ConnectionError(f"The link to the MPE2 failed during {name}: {exc}") from exc
        logger.warning("The link to the MPE2 failed during %s (%s); reconnecting.", name, exc)
        try:
          await self._reconnect()
        except Exception as again:
          raise MPE2ConnectionError(f"The MPE2's link could not be restored: {again}") from exc
    raise AssertionError("unreachable")

  async def _query(self, command_class: Type[cmd.MPE2Command], **kwargs) -> Any:
    """Send a query and return its answer."""
    return await self._send(command_class(dest=NO_ADDRESS, **kwargs))

  async def _command(self, command_class: Type[cmd.MPE2Command], **kwargs) -> None:
    """Send a command; the MPE2 accepts it and runs it in the background."""
    await self._send(command_class(dest=NO_ADDRESS, **kwargs))

  async def _run(
    self, command_class: Type[cmd.MPE2Command], timeout: float = PLATE_STEP_TIMEOUT, **kwargs
  ) -> None:
    """Send an SPEControl command, then wait until the MPE2 has finished it, raising its errors.

    At the deadline the command is aborted and ``MPE2TimeoutError`` raised.
    """
    name = command_class.__name__
    if not self._recovering:
      self._require_not_held(name)
    try:
      await self._wait_for(command_class, timeout, kwargs)
    except asyncio.CancelledError:
      # Interrupted (a notebook cell stopped): the step may still run, and a cancelled task can't
      # safely send more. Hold, so nothing moves until someone has looked.
      if not self._recovering:
        self.held_after = MPE2Error(f"{name} was interrupted while the MPE2 may still run it")
        logger.error("MPE2 held: %s; call halt() or recover() once looked at.", self.held_after)
      raise
    except (MPE2TimeoutError, MPE2ConnectionError) as exc:
      # The link failed or went silent during the step: halt and hold, as any timeout. A
      # deadline reached has been handled already.
      if exc.recovery is None:
        await self._after_failure(exc)
      raise

  async def _wait_for(
    self, command_class: Type[cmd.MPE2Command], timeout: float, kwargs: Dict[str, Any]
  ) -> None:
    """Send a command and poll until the MPE2 has finished it, raising its error if it failed."""
    name = command_class.__name__
    await self._command(command_class, **kwargs)
    reconnections = self._reconnections
    deadline = time.monotonic() + timeout
    polls = 0
    while True:
      results: cmd.LastCommandResults = await self._query(cmd.GetLastCommandResults)
      if self._reconnections != reconnections:
        # A new session may not report the old one's running command: whether it still runs
        # isn't known, so hold for someone to look rather than take it as finished.
        raise MPE2ConnectionError(
          f"The link to the MPE2 was set up again while {name} ran; whether it finished is not "
          "known."
        )
      if not results.command_being_processed:
        break
      polls += 1
      if polls % 10 == 0 and command_class in _PROCESS_COMMANDS:
        try:
          logger.info("MPE2 input gas %.1f psi", await self._query(cmd.GetInputPressure))
        except MPE2Error:
          pass
      if time.monotonic() > deadline:
        error = MPE2TimeoutError(f"The MPE2 was still running {name} after {timeout:.0f} s.")
        await self._after_failure(error)
        raise error
      await asyncio.sleep(POLL_INTERVAL)
    for entry in results.results:
      if entry.is_warning:
        logger.warning(
          "%s: the MPE2 warns %s",
          name,
          errors.describe(self._object_names.get(entry.address), entry.result),
        )
    if results.errors:
      failure = errors.error_from_entries(
        name, results.results, self._object_names, results.traversed_states
      )
      await self._after_failure(failure)
      raise failure

  async def _after_failure(self, error: MPE2Error) -> None:
    """What the MPE2 does after a step failed: recover by itself from a pressure error; halt and
    hold after anything else. Inside recovery itself, nothing more."""
    if self._recovering:
      return
    if isinstance(error, _RECOVER_BY_ITSELF) and self.recover_on_error:
      error.recovery = await self.recover(retrieve_plate=True)
      if error.recovery.failed_steps:
        # Recovery itself failed: look first, as after any other error.
        self.held_after = error
        error.recovery.check_by_hand.append(
          f"held after {type(error).__name__}, whose recovery failed: look at the MPE2, then "
          "call recover()"
        )
        logger.error("MPE2 held after %s: its recovery failed (%s).", error, error.recovery)
      return
    report = await self.halt()
    self.held_after = error
    report.check_by_hand.append(
      f"held after {type(error).__name__}: look at the MPE2 (the shuttle's and lifters' path, the "
      "plates), then call recover()"
    )
    error.recovery = report
    logger.error("MPE2 held after %s; nothing that moves is sent until recover().", error)

  def _require_not_held(self, step: str) -> None:
    """Refuse a step while the MPE2 holds after an error."""
    if self.held_after is not None:
      raise MPE2StateError(
        f"{step} refused: the MPE2 holds after {self.held_after}. Look at it, then call recover().",
        command=step,
      )

  async def _state(self) -> cmd.SPEControlState:
    """The plate handling state, as the firmware reports it."""
    return cast(cmd.SPEControlState, await self._query(cmd.GetControlState))

  async def _require_state(self, step: str, *allowed: cmd.SPEControlState) -> cmd.SPEControlState:
    """Refuse a step unless the MPE2 is in one of the states it needs; return the state."""
    state = await self._state()
    if state not in allowed:
      raise MPE2StateError(
        f"{step} needs the MPE2 {' or '.join(state_name(s) for s in allowed)}; it is "
        f"{state_name(state)}.",
        command=step,
      )
    return state

  # ---------------------------------------------------------------------------
  # Status
  # ---------------------------------------------------------------------------

  async def request_status(self) -> MPE2Status:
    """The plate state, initialization, labware, vacuum and waste containers."""
    return MPE2Status(
      state=cast(MPE2State, state_name(await self._state())),
      initialized=await self.request_is_initialized(),
      labware=await self._query(cmd.GetLabwareParameters),
      vacuum_on=await self.vacuum.request_is_on(),
      selected_waste_container=await self.vacuum.request_selected_waste_container(),
      waste={
        1: await self.vacuum.request_waste_status(1),
        2: await self.vacuum.request_waste_status(2),
      },
    )

  async def request_state(self) -> MPE2State:
    """The plate handling state, as a snake_case name ("ready", "filter_plate_clamped")."""
    return cast(MPE2State, state_name(await self._state()))

  async def request_is_initialized(self) -> bool:
    """The firmware's IsInitialized: True only when it is ready (False with a plate placed or
    clamped). Read the state for a lost initialization."""
    return cast(bool, await self._query(cmd.GetIsInitialized))

  async def request_pressures(self) -> PressureReadings:
    """Every pressure sensor, and the flow (Hamilton's GetPressureSensorReadings, and more)."""
    return PressureReadings(
      input_psi=await self._query(cmd.GetInputPressure),
      manifold_psi=await self._query(cmd.GetManifoldPressure),
      high_path_psi=await self._query(cmd.GetHighPressurePathPressure),
      low_path_psi=await self._query(cmd.GetLowPressurePathPressure),
      low_path_flow_slpm=await self._query(cmd.GetLowPressurePathFlow),
      differential_psi=await self._query(cmd.GetDifferentialPressure),
      waste_vacuum_psi=await self._query(cmd.GetWasteVacuum),
    )

  @asynccontextmanager
  async def record_pressures(
    self, *, interval: float = 0.0, high_path: bool = False
  ) -> AsyncIterator[PressureRecording]:
    """Read the gas supply and the manifold pressure over and over while the block runs.

    Each pair of readings takes about 60 ms over USB, so ``interval`` 0 gives about
    15 readings a second, shared with the driver's own polling of a running step. It only reads.
    A reading that fails ends the recording, not the step::

      async with mpe.record_pressures() as recording:
        await mpe.process_filter_to_waste_container([PlateStep("pressure", 30, 5)])
      print(recording)

    Args:
      interval: the pause between readings, in seconds.
      high_path: read the high pressure path too (gas held there between steps is let out as the
        valve opens). A third reading per sample: about 10 samples a second instead of 15.
    """
    recording = PressureRecording()
    done = asyncio.Event()
    start = time.monotonic()

    async def sample() -> None:
      """Read the pressures until the block ends or a reading fails."""
      # Stopped by the event between readings, never cancelled mid-reading: a cancelled exchange
      # would make the driver reconnect.
      while not done.is_set():
        try:
          pressure_in = await self._query(cmd.GetInputPressure)
          manifold = await self._query(cmd.GetManifoldPressure)
          high = await self._query(cmd.GetHighPressurePathPressure) if high_path else None
        except MPE2Error as exc:
          logger.warning("Pressure recording ended: %s", exc)
          return
        recording.samples.append(
          PressureSample(time.monotonic() - start, pressure_in, manifold, high)
        )
        try:
          await asyncio.wait_for(done.wait(), interval if interval > 0 else 0.001)
        except asyncio.TimeoutError:
          pass

    task = asyncio.create_task(sample())
    try:
      yield recording
    finally:
      done.set()
      await task

  async def request_input_pressure_psi(self) -> float:
    """The gas supply's pressure, in psi."""
    return cast(float, await self._query(cmd.GetInputPressure))

  async def request_firmware_version(self) -> str:
    """The main controller's module version."""
    return cast(str, await self._query(cmd.GetModuleVersion))

  async def check_input_pressure_psi(self) -> float:
    """Read the gas supply, warn when it is outside 80 to 120 psi, and refuse only below the
    driver's ``minimum_input_pressure_psi`` when one is set.

    Outside 80 to 120 psi the firmware aborts a process step by itself
    (MinimumInputPressureNotProvided, MaximumInputPressureExceeded); that is a pressure error,
    which recovers by itself.

    Returns:
      The input pressure, in psi.

    Raises:
      MPE2InputPressureError: below ``minimum_input_pressure_psi``, when it is set.
    """
    pressure = await self.request_input_pressure_psi()
    if self.minimum_input_pressure_psi is not None and pressure < self.minimum_input_pressure_psi:
      raise MPE2InputPressureError(
        f"The MPE2's gas supply is at {pressure:.1f} psi, below the "
        f"{self.minimum_input_pressure_psi:.0f}"
        " psi set for this MPE2. Open the regulator or change the cylinder.",
        name="MinimumInputPressureNotProvided",
      )
    if pressure < cmd.MINIMUM_INPUT_PRESSURE:
      logger.warning(
        "The MPE2's gas supply is at %.1f psi; the firmware aborts processing below %.0f psi.",
        pressure,
        cmd.MINIMUM_INPUT_PRESSURE,
      )
    elif pressure > cmd.MAXIMUM_INPUT_PRESSURE:
      logger.warning(
        "The MPE2's gas supply is at %.1f psi; the firmware aborts processing above %.0f psi.",
        pressure,
        cmd.MAXIMUM_INPUT_PRESSURE,
      )
    return pressure

  # ---------------------------------------------------------------------------
  # Initialization
  # ---------------------------------------------------------------------------

  async def initialize(
    self,
    *,
    smart: bool = True,
    waste_container: Union[WasteContainerNumber, Literal["auto"], None] = "auto",
    vacuum_duration: float = 10.0,
    check_vacuum: bool = True,
  ) -> None:
    """Initialize the MPE2 (its motors home), and run the vacuum to a waste container.

    Take any plates off first. With ``smart``, nothing is sent when it is initialized and ready.

    After a process step aborted with the filter plate clamped, the MPE2 is uninitialized and the
    smart initialization refuses (CollectionPlateDetectedDuringInitialization); a full one
    (``smart=False``) brings the plate out to the shuttle, sometimes on the second try, and stops
    on the plate it then sees (FilterPlateDetectedDuringInitialization). Each of these stops is a
    motor error, so the MPE2 holds: call ``recover()`` before the next try. Once the plate is out,
    take it off, then ``recover()`` and initialize again.

    Args:
      smart: initialize only what needs it.
      waste_container: the container to run the vacuum to, 1 or 2; "auto" takes the first in
        place (as HSL's Initialize); None runs no vacuum.
      vacuum_duration: how long the pump runs after reaching vacuum, in seconds (Hamilton's
        default is 10).
      check_vacuum: let the firmware stop the pump when the vacuum gets too strong.
    """
    self._require_not_held("initialize")
    if not vacuum_duration >= 0:
      raise ValueError(f"The vacuum duration can't be negative ({vacuum_duration} s).")
    if waste_container not in (1, 2, None, "auto"):
      raise ValueError(f"The MPE2 has waste containers 1 and 2, not {waste_container}.")
    state = await self._state()
    if smart and state == S.READY and await self.request_is_initialized():
      return
    if state not in (S.UNINITIALIZED, S.READY, S.INITIALIZING):
      raise MPE2StateError(
        f"Initialize with plates on the MPE2 ({state_name(state)}): take them off first.",
        command="initialize",
      )
    container: Optional[int] = None
    if waste_container == "auto":
      for candidate in (1, 2):
        if (await self.vacuum.request_waste_status(cast(WasteContainerNumber, candidate))).present:
          container = candidate
          break
    else:
      container = waste_container
    if container is None:
      await self._run(cmd.Initialize, timeout=INITIALIZE_TIMEOUT, smart=smart)
      return
    try:
      await self._run(
        cmd.InitializeWithParameters,
        timeout=INITIALIZE_TIMEOUT + vacuum_duration,
        smart=smart,
        vacuum_run_time_ms=round(vacuum_duration * 1000),
        waste_container=WASTE[container],
        disable_upper_limit_check=not check_vacuum,
      )
    except BaseException:
      await stop_quietly(self.vacuum, "a failed initialize")
      raise

  # ---------------------------------------------------------------------------
  # Plates
  # ---------------------------------------------------------------------------

  async def filter_plate_placed(self, height: float, nozzle_height: float) -> None:
    """Tell the MPE2 a filter plate is on its shuttle.

    Nothing is sent when one with the same heights is already placed.

    Args:
      height: from the plate's top to where it sits on the filter plate seat, in mm.
      nozzle_height: from the plate's top to the bottom of its nozzles, in mm.
    """
    self._require_not_held("filter_plate_placed")
    if height <= 0 or nozzle_height <= 0:
      raise ValueError("A filter plate's heights are positive, in mm.")
    if nozzle_height < height:
      raise ValueError(
        f"The nozzles end above the plate's seat: the nozzle height ({nozzle_height} mm) is at "
        f"least the plate height ({height} mm)."
      )
    if height > self.maximum_combined_height:
      raise ValueError(
        f"The filter plate ({height} mm) is higher than the MPE2 takes "
        f"({self.maximum_combined_height} mm)."
      )
    state = await self._state()
    if state != S.READY:
      if state != S.UNINITIALIZED:
        labware: cmd.LabwareParameters = await self._query(cmd.GetLabwareParameters)
        if (
          abs(labware.filter_plate_height - height) < 0.05
          and abs(labware.filter_plate_nozzle_height - nozzle_height) < 0.05
        ):
          return
      raise MPE2StateError(
        f"A filter plate can be placed only when the MPE2 is ready; it is {state_name(state)}.",
        command="filter_plate_placed",
      )
    await self._run(cmd.FilterPlatePlaced, filter_height=height, nozzle_height=nozzle_height)

  async def filter_plate_removed(self) -> None:
    """Tell the MPE2 the filter plate is off; nothing is sent when it is ready already."""
    self._require_not_held("filter_plate_removed")
    state = await self._state()
    if state == S.READY:
      return
    if state != S.FILTER_PLATE_PLACED:
      raise MPE2StateError(
        "The filter plate can come off only at the integration area, without a collection "
        f"plate; the MPE2 is {state_name(state)}.",
        command="filter_plate_removed",
      )
    await self._run(cmd.FilterPlateRemoved)

  async def clamp_filter_plate(self) -> None:
    """Move the filter plate under the manifold and clamp it. This moves.

    With the stack of filter and collection plate at the integration area (after
    ``process_filter_to_collection_plate`` brought it back), the filter plate is clamped up off the
    collection plate: the firmware takes the collection plate off only then, as Hamilton's software
    does it. Nothing is sent when it is clamped already.
    """
    self._require_not_held("clamp_filter_plate")
    state = await self._state()
    if state in (S.FILTER_PLATE_CLAMPED, S.COLLECTION_PLATE_PLACED):
      return
    if state not in (S.FILTER_PLATE_PLACED, S.INTEGRATION_AREA_PLATE_STACK):
      raise MPE2StateError(
        "Clamping needs a filter plate placed, alone or on a collection plate; the MPE2 is "
        f"{state_name(state)}.",
        command="clamp_filter_plate",
      )
    if state == S.FILTER_PLATE_PLACED:  # Lifting off a stack uses no gas.
      await self.check_input_pressure_psi()
    await self._run(cmd.ClampFilterPlate)

  async def retrieve_filter_plate(self) -> None:
    """Unclamp the filter plate and bring it back to the integration area. This moves.

    With a collection plate placed, the filter plate is lowered onto it and the two come out as a
    stack, for pipetting into; ``process_filter_to_collection_plate`` clamps the stack again.

    Nothing is sent when it is at the integration area already.
    """
    self._require_not_held("retrieve_filter_plate")
    state = await self._state()
    if state == S.UNINITIALIZED:
      raise MPE2StateError(
        "The MPE2 is uninitialized: where the plate is isn't known to it. Look, then "
        "initialize(smart=False), which brings a clamped plate out.",
        command="retrieve_filter_plate",
      )
    if state not in CLAMPED:
      return
    await self._run(cmd.RetrieveFilterPlate)

  async def collection_plate_placed(
    self, height: float, *, offset_from_nozzles: float = 0.0
  ) -> None:
    """Tell the MPE2 a collection plate is on its shuttle, under the clamped filter plate.

    The order Hamilton's software uses: the filter plate clamped, the collection plate put on
    the shuttle and this called (nothing moves: 0.9 s on the MPE2), then ``retrieve_filter_plate``
    lowers the filter plate onto it and brings the plate stack to the integration area.

    Nothing is sent when one with the same height and offset is placed already.

    Args:
      height: the collection plate's height, in mm.
      offset_from_nozzles: from the filter plate's nozzles to the collection plate's top, in mm;
        negative puts the nozzles inside the wells.
    """
    self._require_not_held("collection_plate_placed")
    if height <= 0:
      raise ValueError("A collection plate's height is positive, in mm.")
    labware: cmd.LabwareParameters = await self._query(cmd.GetLabwareParameters)
    stack = height + labware.filter_plate_height
    if stack > self.maximum_combined_height:
      raise ValueError(
        f"The filter and collection plates stand {stack:.1f} mm together; the MPE2 takes "
        f"{self.maximum_combined_height} mm."
      )
    state = await self._state()
    if state in WITH_COLLECTION_PLATE:
      if (
        abs(labware.collection_plate_height - height) < 0.05
        and abs(labware.offset - offset_from_nozzles) < 0.05
      ):
        return
      raise MPE2StateError(
        "Another collection plate is placed; remove it first.", command="collection_plate_placed"
      )
    if state != S.FILTER_PLATE_CLAMPED:
      # Only this order is known to work.
      raise MPE2StateError(
        "A collection plate goes under the filter plate once that is clamped "
        f"(clamp_filter_plate()); the MPE2 is {state_name(state)}.",
        command="collection_plate_placed",
      )
    await self._run(
      cmd.CollectionPlatePlaced,
      collection_plate_height=height,
      offset_from_nozzles=offset_from_nozzles,
    )

  async def collection_plate_removed(self) -> None:
    """Tell the MPE2 the collection plate is off; nothing is sent when there is none.

    The firmware takes it only with the filter plate clamped up off it (state
    "collection_plate_placed"); with the two plates stacked at the integration area, call
    ``clamp_filter_plate()`` first. After it the filter plate is still clamped:
    ``retrieve_filter_plate()``, then ``filter_plate_removed()``.
    """
    self._require_not_held("collection_plate_removed")
    state = await self._state()
    if state in (S.READY, S.FILTER_PLATE_PLACED, S.FILTER_PLATE_CLAMPED):
      return  # no collection plate
    if state == S.INTEGRATION_AREA_PLATE_STACK:
      raise MPE2StateError(
        "The collection plate comes off with the filter plate clamped up off it: call "
        "clamp_filter_plate() first (the firmware refuses it with the plates stacked).",
        command="collection_plate_removed",
      )
    if state != S.COLLECTION_PLATE_PLACED:
      raise MPE2StateError(
        f"The collection plate can't come off now ({state_name(state)}): with the plates in the "
        "process area, retrieve_filter_plate() brings them out and clamp_filter_plate() lifts the "
        "filter plate off it.",
        command="collection_plate_removed",
      )
    await self._run(cmd.CollectionPlateRemoved)

  # ---------------------------------------------------------------------------
  # Processing
  # ---------------------------------------------------------------------------

  def _control_points(
    self, steps: Sequence[PlateStep]
  ) -> Tuple[Tuple[cmd.ControlPoint, ...], float]:
    """The plate steps as the firmware's per-well control points, checked against its ranges."""
    if self.manifold is None or self.control_point_ranges is None:
      raise RuntimeError("The MPE2 is not set up: call setup() first.")
    if isinstance(steps, str) or not all(isinstance(s, PlateStep) for s in steps):
      raise TypeError(
        "Control points are PlateSteps; parse_control_points() reads Hamilton's text."
      )
    if not steps:
      raise ValueError("At least one control point is needed.")
    plate_steps = list(steps)
    points = to_firmware(plate_steps, self.manifold.number_of_ports, self.control_point_ranges)
    return points, total_duration(plate_steps)

  async def process_filter_to_waste_container(
    self,
    steps: Sequence[PlateStep],
    *,
    waste_container: WasteContainerNumber = 1,
    return_plate: bool = True,
    check_vacuum: bool = True,
  ) -> None:
    """Push the filter plate's contents through into a waste container. This moves.

    It clamps the plate if it isn't clamped. Each call moves liquid, so it is not idempotent. The
    gas supply and the waste container are checked first; on a failure, ``recover()`` runs and the
    error is raised with its report.

    Args:
      steps: the control points for the whole plate (``ramp()`` makes a rising series;
        ``parse_control_points()`` reads Hamilton's text).
      waste_container: the waste container the vacuum draws into, 1 or 2.
      return_plate: bring the plate back to the integration area after.
      check_vacuum: let the firmware stop when the vacuum gets too strong.
    """
    if waste_container not in (1, 2):
      raise ValueError(f"The MPE2 has waste containers 1 and 2, not {waste_container}.")
    points, duration = self._control_points(steps)
    self._require_not_held("process_filter_to_waste_container")
    await self._require_state(
      "process_filter_to_waste_container", S.FILTER_PLATE_PLACED, S.FILTER_PLATE_CLAMPED
    )
    status = await self.vacuum.request_waste_status(waste_container)
    if not status.present or status.full:
      raise MPE2WasteError(
        f"Waste container {waste_container} is "
        + ("full: empty it first." if status.present else "not in place."),
        command="process_filter_to_waste_container",
      )
    await self.check_input_pressure_psi()
    await self._process(
      cmd.ProcessFilterPlateToWaste,
      PLATE_STEP_TIMEOUT + duration,
      control_points=points,
      return_plate_to_integration_area=return_plate,
      waste_container=WASTE[waste_container],
      disable_upper_limit_check=not check_vacuum,
    )

  async def process_filter_to_collection_plate(
    self, steps: Sequence[PlateStep], *, return_plate: bool = True
  ) -> None:
    """Push the filter plate's contents through into the collection plate under it. This moves.

    Not idempotent, as ``process_filter_to_waste_container``; the gas is checked first, and a
    failure recovers.

    Args:
      steps: the control points for the whole plate, as in ``process_filter_to_waste_container``.
      return_plate: bring the plates back to the integration area after.
    """
    points, duration = self._control_points(steps)
    self._require_not_held("process_filter_to_collection_plate")
    await self._require_state(
      "process_filter_to_collection_plate",
      S.COLLECTION_PLATE_PLACED,
      S.INTEGRATION_AREA_PLATE_STACK,
    )
    await self.check_input_pressure_psi()
    await self._process(
      cmd.ProcessFilterPlateToCollection,
      PLATE_STEP_TIMEOUT + duration,
      control_points=points,
      return_plate_to_integration_area=return_plate,
    )

  async def _process(self, command_class: Type[cmd.MPE2Command], timeout: float, **kwargs) -> None:
    """Run a process step; if it failed and did not recover, stop what still runs."""
    try:
      await self._run(command_class, timeout=timeout, **kwargs)
    except MPE2Error as exc:
      if exc.recovery is None:  # Not recovered by itself (recover_on_error off): still halt.
        exc.recovery = await self.halt()
      raise

  async def abort(self) -> bool:
    """Stop what the MPE2 is processing.

    The abort is always sent, without asking first whether something runs (a failed question
    must not keep it from going out). With nothing running the MPE2 takes it and does nothing;
    a refusal is taken as nothing running.

    Returns:
      Whether the MPE2 took the abort.
    """
    try:
      await self._command(cmd.Abort)
    except MPE2StateError:
      return False
    return True

  # ---------------------------------------------------------------------------
  # Recovery
  # ---------------------------------------------------------------------------

  async def halt(self) -> RecoveryReport:
    """Stop everything that runs, and move nothing: abort a running command, stop the vacuum pump.
    A clamped plate stays clamped. Safe to call at any time."""
    report = RecoveryReport()
    self._recovering = True
    try:
      return await self._recover(report, retrieve_plate=False)
    finally:
      self._recovering = False

  async def recover(self, *, retrieve_plate: bool = True) -> RecoveryReport:
    """Take the MPE2 to a safe, idle state after something went wrong, and say what to check.

    It halts (as ``halt()``) and, with ``retrieve_plate``, unclamps the filter plate and brings it
    back to the integration area (this moves). After an intrusion or a motor error, call it once
    someone has looked at the MPE2: it ends the hold. It never initializes by itself. A step that
    fails is noted and the next one still runs. Safe to call again.
    """
    report = RecoveryReport()
    self._recovering = True
    try:
      report = await self._recover(report, retrieve_plate)
    finally:
      self._recovering = False
    if not report.failed_steps:
      self.held_after = None
    return report

  async def _recover(self, report: RecoveryReport, retrieve_plate: bool) -> RecoveryReport:
    """Bring the MPE2 back to a known state step by step, writing each step into the report."""
    failed = set()

    async def step(description: str, action: Callable[[], Awaitable[bool]]) -> None:
      """Run one recovery step, noting whether it did something or failed."""
      try:
        if await action():
          report.steps.append(description)
      except Exception as exc:  # noqa: BLE001 - recovery goes on
        failed.add(action)
        report.failed_steps.append(f"{description}: {exc}")

    async def restore_link() -> bool:
      """Set the connection up again if an exchange was left uncertain."""
      if not self._uncertain:
        return False
      await self._reconnect()
      return True

    async def abort_running() -> bool:
      """Abort a command that may still run."""
      try:
        busy: Optional[bool] = (
          await self._query(cmd.GetLastCommandResults)
        ).command_being_processed
      except MPE2Error:
        busy = None  # Unknown: abort anyway.
      if busy is False:
        return False
      if not await self.abort():
        return False
      deadline = time.monotonic() + ABORT_TIMEOUT
      while (await self._query(cmd.GetLastCommandResults)).command_being_processed:
        if time.monotonic() > deadline:
          raise MPE2TimeoutError(f"still processing {ABORT_TIMEOUT:.0f} s after the abort")
        await asyncio.sleep(POLL_INTERVAL)
      return True

    async def stop_vacuum() -> bool:
      """Stop the vacuum pump; say whether it may have been running."""
      try:
        was_on: Optional[bool] = await self.vacuum.request_is_on()
      except MPE2Error:
        was_on = None
      await self.vacuum.stop()  # always sent: a faulted waste system can't be relied on to answer
      return was_on is not False

    async def retrieve() -> bool:
      """Bring a clamped filter plate back out, when nothing may still run."""
      if not retrieve_plate or abort_running in failed:
        return False  # Don't move the plate while something may still run.
      state = await self._state()
      if state in RETRIEVING:
        return False  # An interrupted retrieve: not resent (never tried on an MPE2); said below.
      if state not in CLAMPED:
        return False
      await self._run(cmd.RetrieveFilterPlate)
      return True

    await step("link restored", restore_link)
    await step("running command aborted", abort_running)
    await step("vacuum stopped", stop_vacuum)
    await step("filter plate retrieved to the integration area", retrieve)

    try:
      report.final_state = state_name(await self._state())
    except Exception as exc:  # noqa: BLE001
      report.failed_steps.append(f"reading the state: {exc}")
    try:
      report.input_pressure_psi = await self.request_input_pressure_psi()
      if report.input_pressure_psi < cmd.MINIMUM_INPUT_PRESSURE:
        report.check_by_hand.append(
          f"input gas {report.input_pressure_psi:.0f} psi: open the regulator or change the cylinder"
        )
      elif report.input_pressure_psi > cmd.MAXIMUM_INPUT_PRESSURE:
        report.check_by_hand.append(
          f"input gas {report.input_pressure_psi:.0f} psi: turn the regulator down"
        )
    except Exception as exc:  # noqa: BLE001
      report.failed_steps.append(f"reading the input pressure: {exc}")
    if report.final_state == "uninitialized":
      # The firmware drops to uninitialized after some aborts (MaximumInputPressureExceeded):
      # where the plate is isn't known to it, so nothing is moved here.
      report.check_by_hand.append(
        "the MPE2 lost its initialization (the plate may still be clamped): look at it, take the "
        "plates off if it lets you, then initialize(smart=False); a plate it still finds stops it "
        "(CollectionPlate- or FilterPlateDetectedDuringInitialization): recover(), and again"
      )
    elif report.final_state in {state_name(s) for s in RETRIEVING}:
      report.check_by_hand.append(
        "a retrieve was interrupted: look at the plate, then retrieve_filter_plate() or "
        "initialize(smart=False)"
      )
    elif report.final_state in {state_name(s) for s in CLAMPED}:
      report.check_by_hand.append(
        "the filter plate is still clamped: check the shuttle's path is clear, then "
        "retrieve_filter_plate()"
      )
    if report.failed_steps:
      report.check_by_hand.append("look at the MPE2 before going on: a recovery step failed")
    if report.steps or report.failed_steps:
      logger.warning("MPE2 recovery: %s", report)
    else:
      logger.info("MPE2 halted, nothing was running: %s", report)
    return report
