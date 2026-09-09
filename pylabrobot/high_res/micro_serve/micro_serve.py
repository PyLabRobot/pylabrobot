"""HighRes MicroServe control over TCP (MicroServe API, revision 756)."""

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple

from pylabrobot.io import Socket

logger = logging.getLogger(__name__)


class MicroServeError(Exception):
  """A completed command reported an error, abort, or warning.

  ``lines`` preserves the controller's diagnostic text. Error numbers in that
  text are sequential log entry numbers, not stable error codes.
  """

  def __init__(self, command: str, command_id: int, status: str, lines: Tuple[str, ...]) -> None:
    """Record the command identity, completion status, and diagnostic lines."""
    self.command = command
    self.command_id = command_id
    self.status = status
    self.lines = lines
    super().__init__(f"{status} {command} {command_id}: " + "\n".join(lines))


class MicroServeProtocolError(ConnectionError):
  """The reply cannot be associated with the outstanding command."""


@dataclass(frozen=True)
class MicroServePlateDimensions:
  """Plate geometry in millimeters.

  ``height`` is bottom to top; ``stack_height`` is the bottom-to-bottom pitch
  of stacked plates; ``thickness`` is the distance from the plate top to the
  underside of the wells where the spatula supports the plate.
  """

  height: float
  stack_height: float
  thickness: float

  def __post_init__(self) -> None:
    """Reject dimensions that cannot be represented as positive micrometers."""
    for value in (self.height, self.stack_height, self.thickness):
      if not math.isfinite(value) or value <= 0 or round(value * 1000) < 1:
        raise ValueError("Plate dimensions must be finite and at least 0.001 mm")
    if self.thickness > self.height:
      raise ValueError("Plate thickness cannot exceed plate height")

  def _wire(self) -> str:
    """Encode dimensions in the order and units required by the controller."""
    return " ".join(str(round(v * 1000)) for v in (self.height, self.stack_height, self.thickness))


@dataclass(frozen=True)
class MicroServeStatus:
  """Controller state; ``raw`` retains device-native axis positions.

  ``plate_sensor_blocked`` is the detection beam signal, not an inventory or
  transfer-position occupancy guarantee. The loader moves plates relative to
  this beam to determine their height.

  ``loader`` retains the firmware's loaderStatus field, which can remain
  extended after retraction. Use ``loader_retracted`` to check the loader's
  home sensors and the released stacker lock.
  """

  homed: bool
  stacker: Optional[int]
  busy: bool
  loader: Literal["extended", "retracted"]
  mode: str
  plate_sensor_blocked: bool
  flags: Dict[str, bool]
  raw: str

  @property
  def loader_retracted(self) -> bool:
    """Both loader axes are at their home sensors and the stacker lock is released."""
    return (
      self.flags["spatulaHome"] and self.flags["effectuatorHome"] and not self.flags["lockSensor"]
    )

  @property
  def loader_extended(self) -> bool:
    """Firmware reports extension with both axes away from home and the lock engaged."""
    return (
      self.loader == "extended"
      and not self.flags["spatulaHome"]
      and not self.flags["effectuatorHome"]
      and self.flags["lockSensor"]
    )


@dataclass(frozen=True)
class MicroServeCommandStatus:
  """A command's firmware record, retained until the controller restarts."""

  command_id: int
  state: Literal["ok", "running", "fail", "warning", "aborted"]
  command: str


@dataclass(frozen=True)
class MicroServeMotorInfo:
  """Motor-controller serial number and configured name, read without motion."""

  serial_number: str
  name: str


@dataclass(frozen=True)
class MicroServeUnresolvedPreparation:
  """A load/unload whose completion or final position could not be confirmed."""

  direction: Literal["load", "unload"]
  stacker: int
  command_id: Optional[int]


def _parse_status(lines: Tuple[str, ...]) -> MicroServeStatus:
  """Parse status data without confusing its OK! prefix with completion."""
  raw = " ".join(lines)
  fields = re.search(
    r"^OK! status (homed|nothomed), stackerNumber (-?\d+), .*?"
    r"machineState (NotBusy|Busy), loaderStatus (Extended|Retracted), mode (\w+)$",
    raw,
  )
  if fields is None:
    raise MicroServeProtocolError(f"Unrecognized MicroServe status: {raw!r}")
  flags = {name: state == "ON" for name, state in re.findall(r"(\w+) (ON|OFF)\b", raw)}
  if not {"plateSensor", "spatulaHome", "effectuatorHome", "lockSensor"} <= flags.keys():
    raise MicroServeProtocolError(f"Status is missing a loader or plate sensor: {raw!r}")
  stacker = int(fields[2])
  if not -1 <= stacker < 14:
    raise MicroServeProtocolError(f"Invalid stacker in status: {stacker}")
  return MicroServeStatus(
    homed=fields[1] == "homed",
    stacker=None if stacker == -1 else stacker,
    busy=fields[3] == "Busy",
    loader="extended" if fields[4] == "Extended" else "retracted",
    mode=fields[5].lower(),
    plate_sensor_blocked=flags["plateSensor"],
    flags=flags,
    raw=raw,
  )


class HighResMicroServe:
  """MicroServe with fourteen zero-based stackers and a shared transfer position.

  Connect to TCP port 1000. On a dedicated Ethernet link the controller also
  answers at ``10.253.253.253``. ``setup()`` only opens the connection; homing
  and all other motion are explicit operations. Use ``stackers[0]`` through
  ``stackers[13]`` to prepare plates for a separate robot to place or pick.

  Only one operation runs at a time. Commands are never retried automatically.
  After a timeout, cancellation, or broken reply, the connection is closed:
  call ``setup()`` and inspect the machine before deciding how to recover.
  Closing a connection does not abort an operation already executing.

  An incomplete load/unload remains in ``unresolved_preparation``. It cannot
  be repeated until its result is reconciled or the handoff explicitly ended
  by retraction. The plate-detection beam is not used to infer occupancy.

  Communication, homing, all carousel positions, and empty receiving-position
  preparation/retraction were checked on firmware 2.7.0.756. Transfers with
  plates, barcode scanning, and physical fault recovery remain unverified.
  """

  def __init__(
    self,
    host: str,
    port: int = 1000,
    ack_timeout: float = 10.0,
    command_timeout: float = 120.0,
    scan_timeout: float = 300.0,
  ) -> None:
    """Configure the connection and timeouts in seconds without opening it."""
    for timeout in (ack_timeout, command_timeout, scan_timeout):
      if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeouts must be finite and positive")
    self.ack_timeout = ack_timeout
    self.command_timeout = command_timeout
    self.scan_timeout = scan_timeout
    self.io = Socket(
      human_readable_device_name="HighRes MicroServe",
      host=host,
      port=port,
      read_timeout=ack_timeout,
      write_timeout=ack_timeout,
    )
    self._lock = asyncio.Lock()
    self._connected = False
    self._last_command_id: Optional[int] = None
    self._prepared: Optional[Tuple[str, int]] = None
    self._unresolved_preparation: Optional[MicroServeUnresolvedPreparation] = None
    self.stackers = tuple(MicroServeStacker(self, index) for index in range(14))

  @property
  def last_command_id(self) -> Optional[int]:
    """ID from the latest ACK, or None if the latest exchange received no ACK."""
    return self._last_command_id

  @property
  def unresolved_preparation(self) -> Optional[MicroServeUnresolvedPreparation]:
    """The load/unload that requires reconciliation before another preparation."""
    return self._unresolved_preparation

  async def setup(self) -> None:
    """Connect without homing, clearing faults, or changing settings."""
    async with self._lock:
      if self._connected:
        return
      try:
        await asyncio.wait_for(self.io.setup(), timeout=self.ack_timeout)
      except BaseException:
        await self._close()
        raise
      self._connected = True
      logger.info("Connected to MicroServe")
      logger.warning(
        "MicroServe transfers with plates, barcode scanning, and physical fault recovery "
        "are unverified; validate before use"
      )

  async def stop(self) -> None:
    """Close the connection without moving the device or clearing its state."""
    async with self._lock:
      await self._close()

  async def _close(self) -> None:
    """Invalidate the session and close its transport."""
    self._connected = False
    self._prepared = None
    await self.io.stop()

  async def _command(self, command: str, timeout: Optional[float] = None) -> Tuple[str, ...]:
    """Exchange one command while the caller holds the operation lock.

    Completion must echo the entire command and its ACK identifier. In
    particular, ``OK! status homed, ...`` is data, not a completion frame.
    """
    if not self._connected:
      raise RuntimeError("MicroServe is not connected; call setup() first")
    if not command or any(c in command for c in "\r\n\x00"):
      raise ValueError("A command must be one nonempty ASCII line")
    payload = command.encode("ascii") + b"\n"
    self._last_command_id = None
    completion_timeout = self.command_timeout if timeout is None else timeout
    loop = asyncio.get_running_loop()
    lines: List[str] = []
    command_id: Optional[int] = None
    deadline = loop.time() + self.ack_timeout
    size = 0
    try:
      await self.io.write(payload, timeout=self.ack_timeout)
      while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
          raise TimeoutError(f"Timed out waiting for {command!r} (id {command_id})")
        data = await self.io.readline(timeout=remaining)
        if not data:
          raise ConnectionError(f"MicroServe disconnected during {command!r}")
        size += len(data)
        if not data.endswith(b"\n") or size > 4 * 1024 * 1024:
          raise MicroServeProtocolError("Incomplete or oversized MicroServe reply")
        line = data.decode("ascii").rstrip("\r\n")
        if not line:
          continue
        if command_id is None:
          ack = re.fullmatch(r"ACK! " + re.escape(command) + r" (\d+)", line)
          if ack is None:
            raise MicroServeProtocolError(f"Expected ACK for {command!r}, received {line!r}")
          command_id = int(ack[1])
          self._last_command_id = command_id
          deadline = loop.time() + completion_timeout
          continue
        frame = re.fullmatch(r"(OK|ERROR|ABORTED|WARNING|ACK)! (.+) (\d+)", line)
        if frame is not None:
          if frame[2] != command or int(frame[3]) != command_id or frame[1] == "ACK":
            raise MicroServeProtocolError(f"Unexpected command frame: {line!r}")
          if frame[1] != "OK":
            raise MicroServeError(command, command_id, frame[1], tuple(lines))
          return tuple(lines)
        lines.append(line)
    except MicroServeError:
      logger.error("MicroServe command failed: %s", command, exc_info=True)
      raise
    except BaseException:
      # Cancellation or a partial exchange leaves command ownership uncertain.
      logger.warning("Closing MicroServe connection after incomplete command %s", command)
      try:
        await self._close()
      except BaseException:
        logger.exception("Failed to close invalid MicroServe connection")
      raise

  async def _status(self) -> MicroServeStatus:
    """Read the full status while the caller holds the operation lock."""
    return _parse_status(await self._command("status"))

  async def request_status(self) -> MicroServeStatus:
    """Read homing, carousel, loader, and sensor state without motion."""
    async with self._lock:
      return await self._status()

  async def request_version(self) -> str:
    """Return the human-readable controller identification report."""
    async with self._lock:
      return "\n".join(await self._command("version"))

  async def request_firmware_version(self) -> str:
    """Return the machine-readable firmware version."""
    async with self._lock:
      return "\n".join(await self._command("firmwareversion"))

  async def request_detailed_version(self) -> str:
    """Read firmware checksums and versions of the individual motor controllers."""
    async with self._lock:
      return "\n".join(await self._command("detailedversion"))

  async def request_motor_information(self) -> Dict[str, MicroServeMotorInfo]:
    """Read the carousel, spatula, effectuator, and barcode motor identities."""
    async with self._lock:
      motors = {}
      for line in await self._command("copleyserial"):
        match = re.fullmatch(r"([^:]+):\s*(\d+)\s*:\s*(.+)", line)
        if match is None or match[1].strip() in motors:
          raise MicroServeProtocolError(f"Invalid motor identity: {line!r}")
        motors[match[1].strip()] = MicroServeMotorInfo(match[2], match[3].strip())
      return motors

  async def _command_status(self, command_id: int) -> MicroServeCommandStatus:
    """Read one command record while holding the operation lock."""
    lines = await self._command(f"commandstat {command_id}")
    if len(lines) != 1:
      raise MicroServeProtocolError(f"Invalid command status: {lines!r}")
    match = re.fullmatch(r"\s*(\d+) (OK|RUNNING|FAIL|WARNING|ABORTED)!\s+- (.+)", lines[0])
    if match is None or int(match[1]) != command_id:
      raise MicroServeProtocolError(f"Invalid command status: {lines[0]!r}")
    states: Dict[str, Literal["ok", "running", "fail", "warning", "aborted"]] = {
      "OK": "ok",
      "RUNNING": "running",
      "FAIL": "fail",
      "WARNING": "warning",
      "ABORTED": "aborted",
    }
    return MicroServeCommandStatus(command_id, states[match[2]], match[3])

  async def request_command_status(self, command_id: int) -> MicroServeCommandStatus:
    """Query a command's completion; this does not repeat the command.

    IDs are only meaningful within the current controller boot. Do not use a
    saved identifier to infer a result after the MicroServe has restarted.
    """
    self._validate_count(command_id, "command_id")
    async with self._lock:
      return await self._command_status(command_id)

  @staticmethod
  def _validate_count(value: int, name: str) -> None:
    """Require an actual positive integer for query counts and identifiers."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
      raise ValueError(f"{name} must be a positive integer")

  async def request_history(self, count: int = 10) -> Tuple[str, ...]:
    """Read recent command history as human-readable lines."""
    self._validate_count(count, "count")
    async with self._lock:
      return await self._command(f"history {count}")

  async def request_errors(self, count: int = 10) -> Tuple[str, ...]:
    """Read up to ``count`` error log entries without clearing them."""
    self._validate_count(count, "count")
    async with self._lock:
      return await self._command(f"errors {count}")

  async def request_settings(self, search: Optional[str] = None) -> Dict[str, str]:
    """Read in-memory settings, optionally filtering names with ``search``.

    Network settings can differ from the web configuration. This query neither
    changes settings nor applies defaults from the web settings descriptions.
    """
    if search is not None and re.fullmatch(r"[A-Za-z0-9_]+", search) is None:
      raise ValueError("search must contain only ASCII letters, numbers, and underscores")
    async with self._lock:
      lines = await self._command("settings" if search is None else f"settings {search}")
      settings = {}
      for line in lines:
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
          raise MicroServeProtocolError(f"Invalid setting: {line!r}")
        settings[key.strip()] = value.strip()
      return settings

  async def is_ready(self) -> bool:
    """Report readiness to present a plate to the robot, not general machine idleness.

    Firmware returns false for an idle retracted loader and true at a prepared
    transfer position. The API also describes this flag for E-stop recovery.
    """
    async with self._lock:
      reply = await self._command("microserveready")
      if reply not in (("0",), ("1",)):
        raise MicroServeProtocolError(f"Invalid readiness reply: {reply!r}")
      return reply == ("1",)

  async def request_plate_counts(self) -> Dict[int, int]:
    """Read cached approximate counts; this does not physically count plates."""
    async with self._lock:
      counts = {}
      for line in await self._command("getplatecounts"):
        match = re.fullmatch(r"(\d+): (\d+)", line)
        if match is None or int(match[1]) in counts or not 0 <= int(match[1]) < 14:
          raise MicroServeProtocolError(f"Invalid plate count: {line!r}")
        counts[int(match[1])] = int(match[2])
      if set(counts) != set(range(14)):
        raise MicroServeProtocolError("Plate count reply must contain all fourteen stackers")
      return counts

  async def _dimensions(self) -> Dict[int, MicroServePlateDimensions]:
    """Read dimensions while the caller holds the operation lock."""
    dimensions = {}
    for line in await self._command("dimstatus"):
      match = re.fullmatch(
        r"Stacker (\d+): PlateHeight (\d+), StackHeight (\d+), PlateThickness (\d+)", line
      )
      if match is None or int(match[1]) in dimensions or not 0 <= int(match[1]) < 14:
        raise MicroServeProtocolError(f"Invalid dimension reply: {line!r}")
      dimensions[int(match[1])] = MicroServePlateDimensions(
        int(match[2]) / 1000, int(match[3]) / 1000, int(match[4]) / 1000
      )
    if set(dimensions) != set(range(14)):
      raise MicroServeProtocolError("Dimension reply must contain all fourteen stackers")
    return dimensions

  async def request_dimensions(self) -> Dict[int, MicroServePlateDimensions]:
    """Read the configured plate dimensions in millimeters for every stacker."""
    async with self._lock:
      return await self._dimensions()

  @staticmethod
  def _require_idle(status: MicroServeStatus, homed: bool = True) -> None:
    """Reject motion in an unknown, busy, or inappropriate operating state."""
    if status.busy or status.mode != "ready" or (homed and not status.homed):
      raise RuntimeError(f"MicroServe is not ready for this operation: {status.raw}")

  def _require_resolved_preparation(self) -> None:
    """Block new motion when the previous transfer preparation is uncertain."""
    if self._unresolved_preparation is not None:
      raise RuntimeError(
        "A transfer preparation is unresolved. Inspect the machine and call "
        "reconcile_preparation(), or explicitly retract to end the handoff."
      )

  async def reconcile_preparation(self) -> None:
    """Confirm an interrupted preparation using read-only command and status queries.

    Call only if the controller has not rebooted and no other client has moved
    it. A successful command record plus the expected extended stacker restores
    the prepared state. Failed, running, or unidentifiable commands remain
    unresolved. This method never retries a command or moves the machine.
    """
    async with self._lock:
      pending = self._unresolved_preparation
      if pending is None:
        return
      if pending.command_id is None:
        raise RuntimeError("No ACK identifier was received; inspect the machine before recovery")
      record = await self._command_status(pending.command_id)
      if record.command != f"{pending.direction} {pending.stacker}" or record.state != "ok":
        raise RuntimeError(f"Preparation is not confirmed complete: {record}")
      status = await self._status()
      self._require_idle(status)
      if status.stacker != pending.stacker or not status.loader_extended:
        raise RuntimeError(f"Prepared transfer position cannot be confirmed: {status.raw}")
      self._prepared = (pending.direction, pending.stacker)
      self._unresolved_preparation = None
      logger.info("Reconciled MicroServe preparation %s", record.command)

  async def home(self) -> None:
    """Home the machine if needed, then verify it is homed and idle.

    Remove plates from the transfer position and keep the robot clear first.
    """
    async with self._lock:
      self._require_resolved_preparation()
      status = await self._status()
      if status.busy or status.mode not in ("ready", "manual"):
        raise RuntimeError(f"MicroServe cannot home in this state: {status.raw}")
      if status.homed and status.mode == "ready":
        return
      if status.plate_sensor_blocked:
        raise RuntimeError("Clear the plate-detection beam before homing")
      logger.info("Homing MicroServe")
      await self._command("home")
      self._require_idle(await self._status())
      self._prepared = None

  async def retract(self) -> None:
    """Retract the spatula and effectuator, then verify the loader is retracted.

    Call after the robot finishes its transfer and clears the device. Retraction
    can move a plate placed by the robot; the caller must finish the handoff first.
    Successful retraction ends any recorded or unresolved preparation.
    """
    async with self._lock:
      status = await self._status()
      self._require_idle(status)
      if status.loader_retracted:
        self._prepared = None
        self._unresolved_preparation = None
        return
      logger.info("Retracting MicroServe loader")
      await self._command("retract")
      status = await self._status()
      self._require_idle(status)
      if not status.loader_retracted:
        raise RuntimeError(f"Loader did not retract: {status.raw}")
      self._prepared = None
      self._unresolved_preparation = None


class MicroServeStacker:
  """One of the fourteen stackers, accessed through ``device.stackers[index]``.

  Stacker indices are zero-based. Preparation methods position a plate or an
  empty receiving location for an external robot; they do not operate the robot.
  """

  def __init__(self, device: HighResMicroServe, index: int) -> None:
    """Associate this stacker with its controller and zero-based index."""
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < 14:
      raise ValueError("Stacker index must be an integer from 0 through 13")
    self._device = device
    self._index = index

  @property
  def index(self) -> int:
    """This stacker's fixed zero-based controller index."""
    return self._index

  async def request_plate_count(self) -> int:
    """Read this stacker's cached approximate count without physical measurement."""
    async with self._device._lock:
      lines = await self._device._command(f"getplatecounts {self.index}")
      if len(lines) != 1:
        raise MicroServeProtocolError(f"Invalid plate count reply: {lines!r}")
      match = re.fullmatch(rf"{self.index}: (\d+)", lines[0])
      if match is None:
        raise MicroServeProtocolError(f"Invalid plate count reply: {lines!r}")
      return int(match[1])

  async def _set_dimensions(self, dimensions: MicroServePlateDimensions) -> None:
    """Apply and verify geometry while holding the device's operation lock."""
    await self._device._command(f"setstackerdimensions {self.index} {dimensions._wire()}")
    actual = (await self._device._dimensions())[self.index]
    if actual._wire() != dimensions._wire():
      raise MicroServeProtocolError(f"Stacker {self.index} did not accept the plate dimensions")

  async def set_dimensions(self, dimensions: MicroServePlateDimensions) -> None:
    """Set this stacker's plate geometry in millimeters, then read it back."""
    async with self._device._lock:
      self._device._require_resolved_preparation()
      status = await self._device._status()
      self._device._require_idle(status, homed=False)
      if not status.loader_retracted:
        raise RuntimeError("Retract before changing plate geometry")
      await self._set_dimensions(dimensions)

  async def move_to(self) -> None:
    """Rotate the retracted carousel to this stacker, if it is not already there."""
    async with self._device._lock:
      self._device._require_resolved_preparation()
      status = await self._device._status()
      self._device._require_idle(status)
      if not status.loader_retracted or status.plate_sensor_blocked:
        raise RuntimeError("Clear the transfer position and retract before rotating the carousel")
      if status.stacker == self.index:
        return
      logger.info("Rotating MicroServe to stacker %d", self.index)
      await self._device._command(f"spin {self.index}")
      status = await self._device._status()
      self._device._require_idle(status)
      if status.stacker != self.index or not status.loader_retracted:
        raise RuntimeError(f"Carousel did not reach stacker {self.index}: {status.raw}")

  async def _prepare(
    self, direction: Literal["load", "unload"], dimensions: MicroServePlateDimensions
  ) -> None:
    """Apply geometry and reach the requested transfer state without blind retries."""
    async with self._device._lock:
      self._device._require_resolved_preparation()
      status = await self._device._status()
      self._device._require_idle(status)
      at_target = status.stacker == self.index and status.loader_extended
      if at_target and self._device._prepared == (direction, self.index):
        if (await self._device._dimensions())[self.index]._wire() != dimensions._wire():
          raise RuntimeError("Retract the loader before changing geometry at the transfer position")
        return
      if status.plate_sensor_blocked or not status.loader_retracted:
        raise RuntimeError("Finish the current robot handoff and retract before preparing another")
      await self._set_dimensions(dimensions)
      logger.info("Preparing MicroServe stacker %d for %s", self.index, direction)
      command_id = None
      self._device._prepared = None
      try:
        await self._device._command(f"{direction} {self.index}")
        command_id = self._device.last_command_id
        status = await self._device._status()
        self._device._require_idle(status)
        if status.stacker != self.index or not status.loader_extended:
          raise RuntimeError(f"Requested transfer state was not reached: {status.raw}")
      except BaseException:
        self._device._unresolved_preparation = MicroServeUnresolvedPreparation(
          direction,
          self.index,
          command_id if command_id is not None else self._device.last_command_id,
        )
        raise
      self._device._prepared = (direction, self.index)

  async def prepare_for_load(self, dimensions: MicroServePlateDimensions) -> None:
    """Present an empty receiving position for the robot to place a plate.

    Repeating this call in the same verified transfer state does not move the
    device again. After placing the plate, clear the robot and call ``retract()``.
    """
    await self._prepare("load", dimensions)

  async def prepare_for_unload(self, dimensions: MicroServePlateDimensions) -> None:
    """Present a plate from this stacker for the robot to pick.

    A confirmed preparation is not repeated before retraction, even if the
    robot has already removed the plate. After the robot clears the device,
    call ``retract()`` to end the handoff.
    """
    await self._prepare("unload", dimensions)

  async def scan_barcodes(self, dimensions: MicroServePlateDimensions) -> Tuple[str, ...]:
    """Move the barcode reader and return the controller's unmodified data lines.

    Barcode output formatting is firmware-dependent. Geometry is applied before
    scanning; the robot must be clear and the loader retracted. This operation
    moves hardware even though its purpose is reading barcodes.
    """
    async with self._device._lock:
      self._device._require_resolved_preparation()
      status = await self._device._status()
      self._device._require_idle(status)
      if not status.loader_retracted or status.plate_sensor_blocked:
        raise RuntimeError("Clear the transfer position and retract before scanning barcodes")
      await self._set_dimensions(dimensions)
      logger.info("Scanning MicroServe stacker %d", self.index)
      return await self._device._command(
        f"readbarcodestacker {self.index}", timeout=self._device.scan_timeout
      )
