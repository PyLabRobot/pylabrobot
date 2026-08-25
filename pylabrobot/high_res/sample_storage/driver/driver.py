import asyncio
import logging
import random
from typing import Dict, List, Literal, Optional, Tuple, Union, cast

from pylabrobot.events import event_operation, resource_reference
from pylabrobot.io.socket import Socket
from pylabrobot.resources import (
  Plate,
  PlateCarrier,
  PlateHolder,
  Resource,
  ResourceNotFoundError,
  Rotation,
)

from .environment import EnvironmentControl
from .errors import (
  HighResSampleStorageAbortedError,
  HighResSampleStorageError,
  HighResSampleStorageFault,
  HighResSampleStorageProtocolError,
  NoFreeSiteError,
  PlateNotFoundError,
  left_unsafe,
)
from .models import ModelInfo, get_model_info
from .protocol import (
  ACK_TOKEN,
  COMPLETION_ABORTED,
  COMPLETION_ERROR,
  COMPLETION_OK,
  COMPLETION_TOKENS,
  parse_kv,
)
from .settings import HighResSampleStorageSettings
from .types import (
  DOOR_STATES,
  NEST_STATES,
  DoorState,
  EnvironmentParameter,
  NestState,
  StackerDimensions,
  VersionInfo,
)

logger = logging.getLogger(__name__)


class HighResSampleStorage(Resource):
  """Base device for HighRes Biosolutions sample stores.

  The TundraStore, SteriStore and AmbiStore are the same machine family behind a
  shared port-1000 API, so all of the implementation lives here and the concrete
  devices are thin subclasses. Each rack is a *stacker* (a vertical column of
  plate slots); plates enter and leave through one of the device's *nests*
  (transfer stations). Fetch and store operations address a particular nest
  with a 0-based ``tray_index``.

  Subclasses set :attr:`_model_name`; callers can override it with ``model``.
  This configured model selects model-specific behavior. The product name
  reported by the device is logged during setup but is not used as configuration.

  The store exposes a text-based remote-control server over TCP, port 1000.
  Commands are case-sensitive, space-separated, terminated with ``\\r\\n``. Each
  command is answered with an ``ACK!`` echo, optional data lines, then exactly
  one completion line (``OK!`` / ``ABORTED!`` / ``ERROR!``). See the User
  Manual, section "Message Formatting".
  """

  _model_name = "HighResSampleStorage"
  _verification_warning: Optional[str] = None

  def __init__(
    self,
    host: str,
    name: str,
    racks: List[PlateCarrier],
    size_x: float = 0,
    size_y: float = 0,
    size_z: float = 0,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = "plate_store",
    model: Optional[str] = None,
    port: int = 1000,
    read_timeout: float = 30.0,
    motion_timeout: float = 240.0,
  ):
    """
    Args:
      host: IP address of the store. The factory default is ``192.168.127.60``;
        all HighRes devices also answer on the backdoor ``10.253.253.253``.
      port: Remote-control server port (always 1000).
      read_timeout: Timeout (s) for query/status commands.
      motion_timeout: Timeout (s) for long-running motion commands
        (``home``, ``pick``, ``place``, door moves).
      model: Model used for model-specific behavior. Defaults to the concrete
        class's model and is never replaced with the device-reported product name.
    """
    configured_model = model if model is not None else self._model_name
    model_info = get_model_info(configured_model)
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=configured_model,
    )

    # The device reports its configured nest numbers during setup. Their
    # robot-facing coordinates are intentionally undefined relative to the
    # store, so the corresponding resources are attached with location=None.
    self.nests: List[PlateHolder] = []
    self._nest_numbers: List[int] = []

    self._racks = racks
    self._site_locations: Dict[int, Tuple[int, int]] = {}
    for rack_index, rack in enumerate(self._racks):
      self.assign_child_resource(rack, location=None)
      for spot, site in rack.sites.items():
        if spot < 0:
          raise ValueError(f"Rack site spot must be non-negative; got {spot} for {site.name!r}.")
        # PLR carrier spots are zero-based; HighRes stacker slots are one-based.
        self._site_locations[id(site)] = (rack_index + 1, spot + 1)

    # Slide (Y) and lift (Z) positions near zero are retracted. Faulted moves
    # can leave either axis extended even when firmware still reports homed.
    self._retracted_y_max = 50.0
    self._retracted_z_max = 50.0

    self._model_info = model_info
    if self._model_info.has_environment_control:
      self.environment = EnvironmentControl(driver=self)

    self.io = Socket(
      human_readable_device_name="HighRes sample store",
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=read_timeout,
    )
    self._read_timeout = read_timeout
    self._motion_timeout = motion_timeout
    self._command_lock = asyncio.Lock()
    # A command lock prevents protocol responses from interleaving, but a plate
    # transfer also includes resource validation and bookkeeping on either side
    # of the hardware command. Keep that entire transaction atomic.
    self._transfer_lock = asyncio.Lock()

  @property
  def read_timeout(self) -> float:
    return self._read_timeout

  @property
  def motion_timeout(self) -> float:
    return self._motion_timeout

  @property
  def model_info(self) -> ModelInfo:
    return self._model_info

  def serialize(self) -> dict:
    raise NotImplementedError("HighRes sample store serialization is not implemented yet.")

  # --- lifecycle ------------------------------------------------------------

  async def setup(self, home: bool = False) -> None:
    if self._verification_warning is not None:
      logger.warning("%s", self._verification_warning)
    await self.io.setup()
    try:
      await self._setup_connected(home=home)
    except BaseException:
      await self.io.stop()
      raise

  async def _setup_connected(self, home: bool) -> None:
    version = await self.request_version()
    logger.info(
      "Connected to %s (serial %s, firmware %s)",
      version.product_name,
      version.serial_number,
      version.firmware_version,
    )

    if self._model_info.has_environment_control:
      await self.environment.refresh()

    nest_status = await self.request_nest_status()
    if not nest_status:
      raise RuntimeError("The sample store did not report any nests.")
    if not self.nests:
      self._nest_numbers = sorted(nest_status)
      for nest_number in self._nest_numbers:
        nest = PlateHolder(
          name=f"{self.name}_nest_{nest_number}",
          size_x=127.76,
          size_y=85.48,
          size_z=0,
          pedestal_size_z=0,
        )
        self.assign_child_resource(nest, location=None)
        self.nests.append(nest)
    if home:
      await self.home()

  async def stop(self):
    logger.info("Stopping %s", self.name)
    await self.io.stop()

  # --- transport ------------------------------------------------------------

  async def _readline(self, timeout: Optional[float]) -> str:
    raw = await self.io.readuntil(b"\n", timeout=timeout)
    return raw.decode("ascii", errors="replace").rstrip("\r\n")

  async def send_command(self, command: str, timeout: Optional[float] = None) -> List[str]:
    """Send a command and return its data lines (those between the ``ACK!`` echo
    and the completion line).

    Raises:
      HighResSampleStorageError: if the device replies ``ERROR!``.
      HighResSampleStorageAbortedError: if the device replies ``ABORTED!``.
      HighResSampleStorageProtocolError: if the acknowledgement or completion
        does not correspond exactly to this command.
    """
    if not command or command != command.strip() or "\r" in command or "\n" in command:
      raise ValueError("command must be a non-empty single line without surrounding whitespace")
    if timeout is None:
      timeout = self._read_timeout
    encoded_command = command.encode("ascii") + b"\r\n"
    async with self._command_lock:
      try:
        await self.io.write(encoded_command)

        data_lines: List[str] = []
        ack = await self._readline(timeout)
        ack_command, command_id = self._parse_envelope(ACK_TOKEN, ack, command)
        if ack_command != command:
          raise HighResSampleStorageProtocolError(
            command, ack, f"ACK echoed command {ack_command!r}"
          )

        while True:
          line = await self._readline(timeout)
          completion_token = next(
            (token for token in COMPLETION_TOKENS if line == token or line.startswith(f"{token} ")),
            None,
          )
          if completion_token is None:
            if line == ACK_TOKEN or line.startswith(f"{ACK_TOKEN} "):
              raise HighResSampleStorageProtocolError(command, line, "received a second ACK")
            data_lines.append(line)
            continue

          completion_command, completion_id = self._parse_envelope(completion_token, line, command)
          if completion_command != command:
            raise HighResSampleStorageProtocolError(
              command, line, f"completion echoed command {completion_command!r}"
            )
          if completion_id != command_id:
            raise HighResSampleStorageProtocolError(
              command,
              line,
              f"completion command ID {completion_id!r} does not match ACK ID {command_id!r}",
            )
          break
      except BaseException:
        # A timeout, cancellation, or malformed envelope can leave unread response
        # lines in the stream. Closing prevents the next command from consuming
        # those stale lines as its own response.
        logger.exception(
          "Invalidating %s connection after incomplete command %r", self.name, command
        )
        try:
          await self.io.stop()
        except BaseException:
          logger.exception("Failed to close invalid %s connection", self.name)
        raise

    if completion_token == COMPLETION_ERROR:
      # Firmware 3.0.x emits the ``Error <n>: ...`` stack as data lines *before*
      # the ERROR! completion, so they are already collected in data_lines.
      error_lines = [ln for ln in data_lines if ln.startswith("Error")] or data_lines
      raise HighResSampleStorageError(command, error_lines)
    if completion_token == COMPLETION_ABORTED:
      raise HighResSampleStorageAbortedError(command)
    if completion_token != COMPLETION_OK:
      raise HighResSampleStorageProtocolError(command, line, "unknown completion status")
    return data_lines

  @staticmethod
  def _parse_envelope(token: str, line: str, command: str) -> Tuple[str, str]:
    prefix = f"{token} "
    if not line.startswith(prefix):
      raise HighResSampleStorageProtocolError(command, line, f"expected {token} envelope")
    echoed_command, separator, command_id = line[len(prefix) :].rpartition(" ")
    if not separator or not echoed_command or not command_id.isdecimal():
      raise HighResSampleStorageProtocolError(
        command, line, f"expected '{token} <command> <numeric command ID>'"
      )
    return echoed_command, command_id

  # --- shared device queries ------------------------------------------------

  async def request_version(self) -> VersionInfo:
    raw = parse_kv(await self.send_command("version"))
    return VersionInfo(
      product_name=raw.get("Product Name"),
      serial_number=raw.get("Serial Number"),
      firmware_version=raw.get("Firmware Version"),
      firmware_build=raw.get("Firmware Build"),
      raw=raw,
    )

  async def request_environment(self) -> Dict[str, EnvironmentParameter]:
    """Parse ``environmentstatus`` into ``{name: EnvironmentParameter}``.

    Each channel reports ``NAME:current/setpoint/limit``; sensor-only channels
    (e.g. the gas tank pressures) report only a current value. Shared by the
    temperature and humidity controls.
    """
    out: Dict[str, EnvironmentParameter] = {}
    for line in await self.send_command("environmentstatus"):
      if ":" not in line:
        continue
      name, _, rest = line.partition(":")
      parts = rest.strip().rstrip(":").split("/")
      try:
        current = float(parts[0])
      except (ValueError, IndexError):
        continue

      def _opt(i: int, parts=parts) -> Optional[float]:
        try:
          return float(parts[i])
        except (ValueError, IndexError):
          return None

      channel = name.strip().upper()
      out[channel] = EnvironmentParameter(
        name=channel, current=current, setpoint=_opt(1), limit=_opt(2)
      )
    return out

  # --- queries --------------------------------------------------------------

  async def request_axis_positions(self) -> Dict[str, float]:
    """Return the ``status`` report: carousel/theta/Y/Z positions."""
    out: Dict[str, float] = {}
    for key, value in parse_kv(await self.send_command("status")).items():
      try:
        out[key] = float(value)
      except ValueError:
        continue
    return out

  async def request_is_homed(self) -> bool:
    lines = await self.send_command("homedstatus")
    return any(line.strip().lower() == "homed" for line in lines)

  async def request_door_status(self) -> Dict[str, DoorState]:
    """Parsed ``doorstatus`` output, keyed by door name."""
    doors: Dict[str, DoorState] = {}
    for name, value in parse_kv(await self.send_command("doorstatus")).items():
      state = value.lower()
      doors[name] = cast(DoorState, state) if state in DOOR_STATES else "unknown"
    return doors

  async def request_nest_status(self) -> Dict[int, NestState]:
    """Parsed ``neststatus`` output, keyed by nest number."""
    nests: Dict[int, NestState] = {}
    for key, value in parse_kv(await self.send_command("neststatus")).items():
      try:
        nest = int(key)
      except ValueError:
        continue
      state = value.lower()
      if state == "plate_available":
        state = "occupied"
      nests[nest] = cast(NestState, state) if state in NEST_STATES else "unknown"
    return nests

  async def request_spatula_is_holding(self) -> bool:
    """Whether a plate is currently held on the spatula (``platestatus``)."""
    lines = await self.send_command("platestatus")
    return not any("NO_PLATE" in line for line in lines)

  async def request_nest_is_holding(self, nest: int) -> bool:
    """Whether a plate is present on ``nest`` (per its plate sensor).

    Firmware ``PLATE_AVAILABLE`` responses are normalized to ``occupied`` by
    :meth:`request_nest_status`; any other non-clear state also counts as holding.
    """
    states = await self.request_nest_status()
    if nest not in states:
      raise ValueError(f"The device did not report nest {nest}.")
    return states[nest] != "clear"

  async def _probe_presence(self, stacker: int, slot: int, to_nest: int = 1) -> bool:
    """Probe whether a plate is present in ``(stacker, slot)`` by attempting a
    pick. Returns ``True`` if a plate was there, ``False`` if the slot is empty.

    SIDE EFFECT: a plate that is found is moved to ``to_nest`` (the only way to
    sense a stacker slot is to pick it). Only safe for non-top slots, where an
    empty pick is graceful; the top slot (24) faults when empty — see
    :meth:`_pick`. For nests, use :meth:`request_nest_is_holding` instead (a
    non-destructive sensor read).
    """
    try:
      await self._pick(stacker, slot, to_nest)
      return True
    except PlateNotFoundError:
      return False

  async def request_stacker_dimensions(self) -> List[StackerDimensions]:
    """Parse ``getstackerdimensions`` (``<stacker>: <zero_offset> <slot_height>
    <slot_count>``)."""
    dims: List[StackerDimensions] = []
    for line in await self.send_command("getstackerdimensions"):
      key, _, rest = line.partition(":")
      try:
        stacker = int(key)
        zero_offset, slot_height, slot_count = rest.split()
        dims.append(
          StackerDimensions(
            stacker=stacker,
            zero_offset=float(zero_offset),
            slot_height=float(slot_height),
            slot_count=int(slot_count),
          )
        )
      except ValueError:
        continue
    return dims

  async def request_settings(self) -> HighResSampleStorageSettings:
    """Read the device's full settings file (``NAME = value`` pairs) into a
    frozen :class:`HighResSampleStorageSettings`."""
    lines = await self.send_command("settings", timeout=self.read_timeout)
    return HighResSampleStorageSettings.from_lines(lines)

  async def request_stacker_barcodes(
    self, stacker: Union[int, Literal["all"]], slot: Optional[int] = None
  ) -> List[str]:
    """Scan a stacker (or a single slot) for barcodes.

    All transfer nests must be clear: firmware 3.0.0.119 otherwise waits for
    an automation door that cannot open and eventually reports a timeout.
    ``EMPTY`` in the returned lines means no readable barcode; it is not a
    plate-presence result.

    Args:
      stacker: Stacker number, or the string ``"all"`` to scan the whole
        inventory.
      slot: Optional single slot to scan.
    """
    if stacker != "all" and (not isinstance(stacker, int) or stacker < 1):
      raise ValueError("stacker must be a positive integer or 'all'.")
    if stacker == "all" and slot is not None:
      raise ValueError("slot cannot be specified when stacker is 'all'.")
    if slot is not None and slot < 1:
      raise ValueError("slot must be a positive integer.")

    occupied_nests = [
      nest for nest, state in (await self.request_nest_status()).items() if state != "clear"
    ]
    if occupied_nests:
      raise RuntimeError(
        "Cannot scan barcodes while plates are present on nests "
        + ", ".join(map(str, occupied_nests))
        + "; clear all nests first."
      )

    command = f"barcode {stacker}"
    if slot is not None:
      command += f" {slot}"
    return await self.send_command(command, timeout=self.motion_timeout)

  # --- motion ---------------------------------------------------------------

  async def home(self):
    """Home the system. The first step closes all doors, which requires the
    pneumatic supply (clean dry air >80 psi); without it this raises
    :class:`HighResSampleStorageError` ("Unable to close all doors")."""
    if await self.request_spatula_is_holding():
      raise RuntimeError("Cannot home while the spatula reports that it is holding a plate.")
    logger.info("Homing %s", self.name)
    await self.send_command("home", timeout=self.motion_timeout)
    logger.info("Homed %s", self.name)

  async def request_is_parked(self) -> bool:
    """Whether the machine is genuinely safe to move: homed AND the spatula
    slide/lift axes are retracted.

    Prefer this over :meth:`request_is_homed`. ``homedstatus`` reports homed even while
    the spatula is stuck extended in a stacker after a faulted top-slot pick, so
    it alone is not a safe-state check; this also verifies the slide (Y) and
    lift (Z) axes are near their home positions.
    """
    if not await self.request_is_homed():
      return False
    positions = await self.request_axis_positions()
    y = positions.get("Y axis")
    z = positions.get("Z axis")
    return (
      y is not None
      and z is not None
      and abs(y) < self._retracted_y_max
      and abs(z) < self._retracted_z_max
    )

  async def recover(self) -> bool:
    """Retract the spatula and re-home after a motion fault.

    A faulted command (e.g. an empty-slot ``pick`` in the top few slots) can
    leave the spatula extended. This ALWAYS issues the retract (``spatulaout``)
    + ``home`` — it does not trust ``homedstatus`` to decide whether recovery is
    needed, because that reports homed even while the spatula is stuck extended.
    Automatic recovery is refused while the spatula plate sensor is active,
    because the plate cannot be relocated safely without physical inspection.
    Retries a few times. Returns ``True`` once :meth:`request_is_parked`.
    """
    if await self.request_spatula_is_holding():
      raise RuntimeError(
        "Cannot recover automatically while the spatula reports that it is holding a plate."
      )

    logger.warning("Starting motion recovery for %s", self.name)
    for attempt in range(1, 4):
      for command in ("enable", "spatulaout"):
        try:
          await self.send_command(command, timeout=self.motion_timeout)
        except HighResSampleStorageError as exc:
          logger.warning(
            "Recovery attempt %d: %s failed on %s: %s",
            attempt,
            command,
            self.name,
            exc,
          )
      try:
        await self.send_command("home", timeout=self.motion_timeout)
      except HighResSampleStorageError as exc:
        logger.warning("Recovery attempt %d: home failed on %s: %s", attempt, self.name, exc)
      if await self.request_is_parked():
        logger.info("Motion recovery completed for %s on attempt %d", self.name, attempt)
        return True
    logger.error("Motion recovery failed for %s after 3 attempts", self.name)
    return False

  async def _pick(self, stacker: int, slot: int, nest: int, close_door: bool = True):
    """Retrieve a plate from ``(stacker, slot)`` to ``nest``.

    ``close_door=False`` re-opens the doors after the transfer (see :meth:`_place`).

    On failure the error is classified; no automatic motion is performed:

    - :class:`PlateNotFoundError` — the slot was empty ("No plate detected")
      and the store retracted cleanly; the machine is safe to keep using.
    - :class:`HighResSampleStorageFault` — the machine was left unsafe (spatula extended
      / unhomed), e.g. an empty *top* slot where the firmware can't complete its
      safe-travel retract. Call :meth:`recover` before any further motion.

    Note: ``homedstatus`` reports homed even when the spatula is stuck extended
    at a top slot, so the firmware's own "unsafe for rotation" signal is used
    (not just :meth:`request_is_homed`) to detect that case.
    """
    command = f"pick {stacker} {slot} {nest}"
    logger.info(
      "Moving a plate in %s from stacker %d slot %d to nest %d",
      self.name,
      stacker,
      slot,
      nest,
    )
    try:
      await self.send_command(command, timeout=self.motion_timeout)
    except HighResSampleStorageError as exc:
      if left_unsafe(exc.error_lines) or not await self.request_is_homed():
        logger.error("Pick left %s unsafe: %s", self.name, exc)
        raise HighResSampleStorageFault(command, exc.error_lines) from exc
      if any("no plate detected" in line.lower() for line in exc.error_lines):
        logger.warning("No plate found in %s stacker %d slot %d", self.name, stacker, slot)
        raise PlateNotFoundError(command, exc.error_lines) from exc
      logger.error("Pick failed on %s: %s", self.name, exc)
      raise
    if not close_door:
      await self.open_all_doors()

  async def _place(self, stacker: int, slot: int, nest: int, close_door: bool = True):
    """Place the plate at ``nest`` into ``(stacker, slot)``.

    The store re-seals its doors as part of every transfer, so ``close_door``
    controls only the *end* state: with ``close_door=False`` the doors are
    re-opened after the place, leaving the carousel accessible for a following
    operation (handy when the cold environment doesn't matter). The default
    leaves it sealed.
    """
    command = f"place {stacker} {slot} {nest}"
    logger.info(
      "Moving a plate in %s from nest %d to stacker %d slot %d",
      self.name,
      nest,
      stacker,
      slot,
    )
    try:
      await self.send_command(command, timeout=self.motion_timeout)
    except HighResSampleStorageError as exc:
      if left_unsafe(exc.error_lines) or not await self.request_is_homed():
        logger.error("Place left %s unsafe: %s", self.name, exc)
        raise HighResSampleStorageFault(command, exc.error_lines) from exc
      logger.error("Place failed on %s: %s", self.name, exc)
      raise
    if not close_door:
      await self.open_all_doors()

  @staticmethod
  def _robot_doors_reached(
    doors: Dict[str, DoorState], acceptable_states: Tuple[DoorState, ...]
  ) -> bool:
    robot_doors = [state for name, state in doors.items() if name.casefold() != "user door"]
    return bool(robot_doors) and all(state in acceptable_states for state in robot_doors)

  async def _wait_for_robot_doors(
    self, target: Literal["open", "closed"], moving: Literal["opening", "closing"]
  ) -> bool:
    """Wait for every robot door to reach ``target`` after a firmware error.

    Returns ``False`` immediately if a door reports a contradictory or unknown
    state, or after the configured motion timeout expires.
    """
    deadline = asyncio.get_running_loop().time() + self.motion_timeout
    while True:
      doors = await self.request_door_status()
      if self._robot_doors_reached(doors, acceptable_states=(target,)):
        return True
      if not self._robot_doors_reached(doors, acceptable_states=(target, moving)):
        return False
      remaining = deadline - asyncio.get_running_loop().time()
      if remaining <= 0:
        return False
      await asyncio.sleep(min(0.1, remaining))

  async def open_all_doors(self) -> None:
    """Open every pneumatic robot door.

    Firmware 3.0.0.119 can return ``ERROR!`` after completing this operation.
    In that case the door sensors are the authoritative postcondition; the
    original command error is re-raised unless every robot door reaches open.
    Transitional states are polled up to :attr:`motion_timeout`. The manual user
    door is intentionally excluded.
    """
    try:
      logger.info("Opening robot doors on %s", self.name)
      await self.send_command("openalldoors", timeout=self.motion_timeout)
    except HighResSampleStorageError:
      logger.warning("Open-all-doors returned an error on %s; checking door sensors", self.name)
      if await self._wait_for_robot_doors(target="open", moving="opening"):
        logger.info("Robot doors on %s reached open despite firmware error", self.name)
        return
      raise

  async def close_all_doors(self) -> None:
    """Close every pneumatic robot door.

    As with :meth:`open_all_doors`, accept an erroneous completion only when
    the live door report reaches the requested final state.
    """
    try:
      logger.info("Closing robot doors on %s", self.name)
      await self.send_command("closealldoors", timeout=self.motion_timeout)
    except HighResSampleStorageError:
      logger.warning("Close-all-doors returned an error on %s; checking door sensors", self.name)
      if await self._wait_for_robot_doors(target="closed", moving="closing"):
        logger.info("Robot doors on %s reached closed despite firmware error", self.name)
        return
      raise

  async def clear_abort(self) -> None:
    """Clear an abort state reported by the device.

    Firmware 3.0.0.119 exposes ``clearabort`` but no command for initiating an
    abort, so this is recovery for device- or externally-initiated aborts.
    """
    logger.info("Clearing abort state on %s", self.name)
    await self.send_command("clearabort")

  # --- plate retrieval -------------------------------------------------------

  @property
  def racks(self) -> List[PlateCarrier]:
    return self._racks

  def get_num_free_sites(self) -> int:
    return sum(len(rack.get_free_sites()) for rack in self._racks)

  def get_site_by_plate_name(self, plate_name: str) -> PlateHolder:
    for rack in self._racks:
      for site in rack.sites.values():
        if site.resource is not None and site.resource.name == plate_name:
          return site
    raise ResourceNotFoundError(f"Plate {plate_name!r} not found in {self.name!r}.")

  def _find_available_sites_sorted(self, plate: Plate) -> List[PlateHolder]:
    plate_height = plate.get_size_z()
    if plate.lid is not None:
      lid_location = plate.get_lid_location(plate.lid)
      plate_height = max(plate_height, lid_location.z + plate.lid.get_size_z())
    available = [
      site
      for rack in self._racks
      for site in rack.get_free_sites()
      if site.get_size_z() >= plate_height
    ]
    if not available:
      raise NoFreeSiteError(
        f"No free site at least {plate_height:g} mm high found for plate {plate.name!r}."
      )
    return sorted(available, key=lambda site: site.get_size_z())

  async def _require_nest_states(self, expected: Dict[int, NestState]) -> None:
    """Require exact live sensor states before issuing a transfer command."""
    actual = await self.request_nest_status()
    for nest, expected_state in expected.items():
      actual_state = actual.get(nest)
      if actual_state != expected_state:
        raise RuntimeError(
          f"Cannot transfer plate: nest {nest} must be {expected_state}, "
          f"but its sensor reports {actual_state or 'missing'}."
        )

  def find_smallest_site_for_plate(self, plate: Plate) -> PlateHolder:
    return self._find_available_sites_sorted(plate)[0]

  def find_random_site(self, plate: Plate) -> PlateHolder:
    return random.choice(self._find_available_sites_sorted(plate))

  def _locate(self, site: PlateHolder) -> Tuple[int, int]:
    if id(site) not in self._site_locations:
      raise ValueError(f"Site '{site.name}' is not a known stacker slot.")
    return self._site_locations[id(site)]

  def _nest_for_tray(self, tray_index: int) -> int:
    """Map a 0-based tray index to a nest number reported by the device."""
    if not self._nest_numbers:
      raise RuntimeError("Nests have not been loaded; call setup() first.")
    if not 0 <= tray_index < len(self._nest_numbers):
      raise ValueError(
        f"sample store has trays 0..{len(self._nest_numbers) - 1}; got tray_index={tray_index}."
      )
    return self._nest_numbers[tray_index]

  async def fetch_plate_to_loading_tray(self, plate: Union[Plate, str], tray_index: int) -> Plate:
    async with self._transfer_lock:
      if isinstance(plate, str):
        stored_site = self.get_site_by_plate_name(plate)
        stored_resource = stored_site.resource
        if not isinstance(stored_resource, Plate):
          raise ResourceNotFoundError(f"Plate {plate!r} not found in {self.name!r}.")
        plate = stored_resource
      parent = plate.parent
      if not isinstance(parent, PlateHolder):
        raise ValueError(f"Plate '{plate.name}' is not in a stacker slot.")
      stacker, slot = self._locate(parent)
      nest_number = self._nest_for_tray(tray_index)
      nest = self.nests[tray_index]
      nest.check_can_drop_resource_here(plate)
      await self._require_nest_states({nest_number: "clear"})

      with event_operation(
        "incubator.fetch_plate",
        device=resource_reference(self),
        resources=[resource_reference(plate)],
        source=resource_reference(parent),
        destination=resource_reference(nest),
      ):
        await self._pick(stacker, slot, nest_number)

        plate.unassign()
        nest.assign_child_resource(plate)
        return plate

  async def take_in_plate(
    self,
    tray_index: int,
    site: Union[PlateHolder, Literal["random", "smallest"]] = "smallest",
  ) -> Plate:
    async with self._transfer_lock:
      self._nest_for_tray(tray_index)
      plate = self.nests[tray_index].resource
      if not isinstance(plate, Plate):
        raise ResourceNotFoundError(f"No plate on tray {tray_index}.")

      if site == "random":
        destination = self.find_random_site(plate)
      elif site == "smallest":
        destination = self.find_smallest_site_for_plate(plate)
      elif isinstance(site, PlateHolder):
        if site not in self._find_available_sites_sorted(plate):
          raise ValueError(f"Site {site.name!r} is not available for plate {plate.name!r}.")
        destination = site
      else:
        raise ValueError(f"Invalid site: {site!r}")

      nest = self.nests[tray_index]
      with event_operation(
        "incubator.take_in_plate",
        device=resource_reference(self),
        resources=[resource_reference(plate)],
        source=resource_reference(nest),
        destination=resource_reference(destination),
      ):
        await self._store_plate(plate, destination, tray_index)
        return plate

  async def store_plate(self, plate: Plate, site: PlateHolder, tray_index: int) -> None:
    async with self._transfer_lock:
      self._nest_for_tray(tray_index)
      nest = self.nests[tray_index]
      with event_operation(
        "incubator.take_in_plate",
        device=resource_reference(self),
        resources=[resource_reference(plate)],
        source=resource_reference(nest),
        destination=resource_reference(site),
      ):
        await self._store_plate(plate, site, tray_index)

  async def transfer_plate_between_nests(
    self, source_tray_index: int, destination_tray_index: int
  ) -> Plate:
    """Move a plate between two transfer nests.

    Tray indices are zero-based and map to the sorted nest numbers reported by
    the device during :meth:`setup`.
    """
    async with self._transfer_lock:
      if source_tray_index == destination_tray_index:
        raise ValueError("Source and destination tray indices must be different.")
      source_number = self._nest_for_tray(source_tray_index)
      destination_number = self._nest_for_tray(destination_tray_index)
      source = self.nests[source_tray_index]
      destination = self.nests[destination_tray_index]
      plate = source.resource
      if not isinstance(plate, Plate):
        raise ResourceNotFoundError(f"No plate on tray {source_tray_index}.")
      destination.check_can_drop_resource_here(plate)
      await self._require_nest_states({source_number: "occupied", destination_number: "clear"})

      with event_operation(
        "incubator.transfer_plate",
        device=resource_reference(self),
        resources=[resource_reference(plate)],
        source=resource_reference(source),
        destination=resource_reference(destination),
      ):
        logger.info(
          "Moving plate %s in %s from nest %d to nest %d",
          plate.name,
          self.name,
          source_number,
          destination_number,
        )
        await self.send_command(
          f"nesttransfer {source_number} {destination_number}", timeout=self.motion_timeout
        )
        plate.unassign()
        destination.assign_child_resource(plate)
        return plate

  async def _store_plate(self, plate: Plate, site: PlateHolder, tray_index: int) -> None:
    stacker, slot = self._locate(site)
    nest_number = self._nest_for_tray(tray_index)
    nest = self.nests[tray_index]
    if plate.parent is not nest:
      raise ValueError(f"Plate '{plate.name}' is not on tray {tray_index}.")
    site.check_can_drop_resource_here(plate)
    await self._require_nest_states({nest_number: "occupied"})

    await self._place(stacker, slot, nest_number)

    plate.unassign()
    site.assign_child_resource(plate)
