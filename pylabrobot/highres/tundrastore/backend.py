import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from pylabrobot.capabilities.automated_retrieval.backend import AutomatedRetrievalBackend
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.humidity_controlling.backend import HumidityControllerBackend
from pylabrobot.capabilities.temperature_controlling.backend import TemperatureControllerBackend
from pylabrobot.device import Driver
from pylabrobot.io.socket import Socket
from pylabrobot.resources import Plate, PlateHolder

from .constants import (
  ACK_TOKEN,
  COMPLETION_ABORTED,
  COMPLETION_ERROR,
  COMPLETION_OK,
  COMPLETION_TOKENS,
  DoorState,
  NestState,
)
from .errors import (
  PlateNotFoundError,
  TundraStoreAbortedError,
  TundraStoreError,
  TundraStoreFault,
  left_unsafe,
)
from .settings import TundraStoreSettings
from .standard import (
  DoorStatus,
  EnvironmentParameter,
  NestStatus,
  StackerDimensions,
  VersionInfo,
)

logger = logging.getLogger(__name__)


class TundraStoreBackend(
  AutomatedRetrievalBackend,
  TemperatureControllerBackend,
  HumidityControllerBackend,
  Driver,
):
  """Backend for the HighRes Biosolutions TundraStore automated plate store.

  The TundraStore (also sold/branded as "SteriStore") exposes a text-based
  remote-control server over TCP, port 1000. Commands are case-sensitive,
  space-separated, terminated with ``\\r\\n``. Each command is answered with an
  ``ACK!`` echo, optional data lines, then exactly one completion line
  (``OK!`` / ``ABORTED!`` / ``ERROR!``). See the TundraStore User Manual,
  section "Message Formatting".

  Plates are stored in a refrigerated carousel of *stackers*, each holding a
  number of *slots*. An external robot hands plates to/from one of the device's
  *nests* (transfer stations); the internal spatula moves plates between a nest
  and a (stacker, slot). The low-level :meth:`pick` / :meth:`place` take those
  three indices directly.

  The TundraStore has two nests. They are exposed through the multi-tray
  :class:`AutomatedRetrieval` capability: its ``tray`` argument is a 0-based nest
  index (tray ``i`` -> device nest ``i + 1``), and ``tray=None`` selects
  :attr:`loading_tray_nest`. :meth:`pick` / :meth:`place` address a nest by its
  1-based device number directly.
  """

  @dataclass
  class SetupParams(BackendParams):
    """Optional parameters for :meth:`setup`."""

    home_on_setup: bool = False

  def __init__(
    self,
    host: str,
    port: int = 1000,
    read_timeout: float = 30.0,
    motion_timeout: float = 240.0,
    loading_tray_nest: int = 1,
  ):
    """
    Args:
      host: IP address of the TundraStore. The factory default is
        ``192.168.127.60``; all HighRes devices also answer on the backdoor
        ``10.253.253.253``.
      port: Remote-control server port (always 1000).
      read_timeout: Timeout (s) for query/status commands.
      motion_timeout: Timeout (s) for long-running motion commands
        (``home``, ``pick``, ``place``, door moves).
      loading_tray_nest: Which nest the :class:`AutomatedRetrieval` capability
        uses as its loading tray (1 or 2).
    """
    super().__init__()
    self.io = Socket(
      human_readable_device_name="HighRes TundraStore",
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=read_timeout,
    )
    self._read_timeout = read_timeout
    self._motion_timeout = motion_timeout
    self.loading_tray_nest = loading_tray_nest
    self.num_nests = 2
    # Slide (Y) below this is "retracted"; a spatula stuck in a stacker sits at
    # the ~256mm slide-in depth, home is 0. Used by is_parked()/recover().
    self._retracted_y_max = 50.0
    self._command_lock = asyncio.Lock()
    # stacker/slot lookup for the AutomatedRetrieval capability, built from racks.
    self._site_locations: Dict[str, Tuple[int, int]] = {}

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "host": self.io._host,
      "port": self.io._port,
      "read_timeout": self._read_timeout,
      "motion_timeout": self._motion_timeout,
      "loading_tray_nest": self.loading_tray_nest,
    }

  # --- lifecycle ------------------------------------------------------------

  async def setup(self, backend_params: Optional[BackendParams] = None):
    if backend_params is None:
      backend_params = TundraStoreBackend.SetupParams()
    if not isinstance(backend_params, TundraStoreBackend.SetupParams):
      raise TypeError(f"backend_params must be {TundraStoreBackend.SetupParams}")

    await self.io.setup()
    version = await self.request_version()
    logger.info(
      "Connected to %s (serial %s, firmware %s)",
      version.product_name,
      version.serial_number,
      version.firmware_version,
    )
    if backend_params.home_on_setup:
      await self.home()

  async def stop(self):
    await self.io.stop()

  # --- transport ------------------------------------------------------------

  async def _readline(self, timeout: Optional[float]) -> str:
    raw = await self.io.readuntil(b"\n", timeout=timeout)
    return raw.decode("ascii", errors="replace").rstrip("\r\n")

  async def send_command(self, command: str, timeout: Optional[float] = None) -> List[str]:
    """Send a command and return its data lines (those between the ``ACK!`` echo
    and the completion line).

    Raises:
      TundraStoreError: if the device replies ``ERROR!``.
      TundraStoreAbortedError: if the device replies ``ABORTED!``.
    """
    if timeout is None:
      timeout = self._read_timeout
    async with self._command_lock:
      await self.io.write(command.encode("ascii") + b"\r\n")

      data_lines: List[str] = []
      completion: Optional[str] = None
      seen_ack = False
      while completion is None:
        line = await self._readline(timeout)
        if line.startswith(ACK_TOKEN) and not seen_ack:
          seen_ack = True
          continue
        if line.startswith(COMPLETION_TOKENS):
          completion = line
          break
        data_lines.append(line)

    if completion.startswith(COMPLETION_ERROR):
      # Firmware 3.0.x emits the ``Error <n>: ...`` stack as data lines *before*
      # the ERROR! completion, so they are already collected in data_lines.
      error_lines = [ln for ln in data_lines if ln.startswith("Error")] or data_lines
      raise TundraStoreError(command, error_lines)
    if completion.startswith(COMPLETION_ABORTED):
      raise TundraStoreAbortedError(command)
    assert completion.startswith(COMPLETION_OK)
    return data_lines

  @staticmethod
  def _parse_kv(lines: List[str]) -> Dict[str, str]:
    """Parse ``Key: value`` lines into a dict (first colon splits)."""
    out: Dict[str, str] = {}
    for line in lines:
      if ":" in line:
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out

  # --- queries (verified against firmware 3.0.0.119) ------------------------

  async def request_version(self) -> VersionInfo:
    raw = self._parse_kv(await self.send_command("version"))
    return VersionInfo(
      product_name=raw.get("Product Name"),
      serial_number=raw.get("Serial Number"),
      firmware_version=raw.get("Firmware Version"),
      firmware_build=raw.get("Firmware Build"),
      raw=raw,
    )

  async def request_axis_positions(self) -> Dict[str, float]:
    """Return the ``status`` report: carousel/theta/Y/Z positions."""
    out: Dict[str, float] = {}
    for key, value in self._parse_kv(await self.send_command("status")).items():
      try:
        out[key] = float(value)
      except ValueError:
        continue
    return out

  async def is_homed(self) -> bool:
    lines = await self.send_command("homedstatus")
    return any(line.strip().lower() == "homed" for line in lines)

  async def request_door_status(self) -> DoorStatus:
    doors: Dict[str, DoorState] = {}
    for name, value in self._parse_kv(await self.send_command("doorstatus")).items():
      try:
        doors[name] = DoorState(value)
      except ValueError:
        doors[name] = DoorState.UNKNOWN
    return DoorStatus(doors=doors)

  async def request_nest_status(self) -> NestStatus:
    nests: Dict[int, NestState] = {}
    for key, value in self._parse_kv(await self.send_command("neststatus")).items():
      try:
        nest = int(key)
      except ValueError:
        continue
      try:
        nests[nest] = NestState(value)
      except ValueError:
        nests[nest] = NestState.UNKNOWN
    return NestStatus(nests=nests)

  async def spatula_request_is_holding(self) -> bool:
    """Whether a plate is currently held on the spatula (``platestatus``)."""
    lines = await self.send_command("platestatus")
    return not any("NO_PLATE" in line for line in lines)

  async def nest_request_is_holding(self, nest: int) -> bool:
    """Whether a plate is present on ``nest`` (per its plate sensor).

    Note: this unit reports an occupied nest as ``UNKNOWN`` rather than
    ``OCCUPIED``; anything other than ``CLEAR`` counts as holding.
    """
    state = (await self.request_nest_status()).nests.get(nest)
    return state is not None and state is not NestState.CLEAR

  async def probe_presence(self, stacker: int, slot: int, to_nest: int = 1) -> bool:
    """Probe whether a plate is present in ``(stacker, slot)`` by attempting a
    pick. Returns ``True`` if a plate was there, ``False`` if the slot is empty.

    SIDE EFFECT: a plate that is found is moved to ``to_nest`` (the only way to
    sense a stacker slot is to pick it). Only safe for non-top slots, where an
    empty pick is graceful; the top slot (24) faults when empty — see
    :meth:`pick`. For nests, use :meth:`nest_request_is_holding` instead (a
    non-destructive sensor read).
    """
    try:
      await self.pick(stacker, slot, to_nest)
      return True
    except PlateNotFoundError:
      return False

  async def request_environment(self) -> Dict[str, EnvironmentParameter]:
    """Parse ``environmentstatus`` into ``{name: EnvironmentParameter}``.

    Each channel reports ``NAME:current/setpoint/limit``; sensor-only channels
    (e.g. the gas tank pressures) report only a current value.
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

      def _opt(i: int) -> Optional[float]:
        try:
          return float(parts[i])
        except (ValueError, IndexError):
          return None

      out[name.strip()] = EnvironmentParameter(
        name=name.strip(), current=current, setpoint=_opt(1), limit=_opt(2)
      )
    return out

  async def get_stacker_dimensions(self) -> List[StackerDimensions]:
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

  async def request_settings(self, search: str = "") -> TundraStoreSettings:
    """Read the device's settings file (``NAME = value`` pairs) into a
    :class:`TundraStoreSettings`. Pass ``search`` to filter by substring."""
    command = "settings" + (f" {search}" if search else "")
    lines = await self.send_command(command, timeout=self._read_timeout)
    version = await self.request_version()
    return TundraStoreSettings.from_lines(
      lines, serial=version.serial_number, firmware=version.firmware_version
    )

  async def scan_stacker_barcodes(self, stacker, slot: Optional[int] = None) -> List[str]:
    """Scan a stacker (or a single slot) for barcodes.

    Args:
      stacker: Stacker number, or the string ``"all"`` to scan the whole
        inventory.
      slot: Optional single slot to scan.
    """
    command = f"barcode {stacker}"
    if slot is not None:
      command += f" {slot}"
    return await self.send_command(command, timeout=self._motion_timeout)

  # --- motion ---------------------------------------------------------------

  async def home(self):
    """Home the system. The first step closes all doors, which requires the
    pneumatic supply (clean dry air >80 psi); without it this raises
    :class:`TundraStoreError` ("Unable to close all doors")."""
    await self.send_command("home", timeout=self._motion_timeout)

  async def is_parked(self) -> bool:
    """Whether the machine is genuinely safe to move: homed AND the spatula
    retracted out of the carousel.

    Prefer this over :meth:`is_homed`. ``homedstatus`` reports homed even while
    the spatula is stuck extended in a stacker after a faulted top-slot pick, so
    it alone is not a safe-state check; this also verifies the slide (Y) axis is
    near its home position (a stuck spatula sits at the ~256mm slide-in depth).
    """
    if not await self.is_homed():
      return False
    y = (await self.request_axis_positions()).get("Y axis")
    return y is not None and abs(y) < self._retracted_y_max

  async def recover(self) -> bool:
    """Retract the spatula and re-home after a motion fault.

    A faulted command (e.g. an empty-slot ``pick`` in the top few slots) can
    leave the spatula extended. This ALWAYS issues the retract (``spatulaout``)
    + ``home`` — it does not trust ``homedstatus`` to decide whether recovery is
    needed, because that reports homed even while the spatula is stuck extended.
    Retries a few times. Returns ``True`` once :meth:`is_parked`.
    """
    for _ in range(3):
      for command in ("enable", "spatulaout"):
        try:
          await self.send_command(command, timeout=self._motion_timeout)
        except TundraStoreError:
          pass
      try:
        await self.send_command("home", timeout=self._motion_timeout)
      except TundraStoreError:
        pass
      if await self.is_parked():
        return True
    return False

  async def pick(self, stacker: int, slot: int, nest: int, close_door: bool = True):
    """Retrieve a plate from ``(stacker, slot)`` to ``nest``.

    ``close_door=False`` re-opens the doors after the transfer (see :meth:`place`).

    On failure the error is classified; no automatic motion is performed:

    - :class:`PlateNotFoundError` — the slot was empty ("No plate detected")
      and the store retracted cleanly; the machine is safe to keep using.
    - :class:`TundraStoreFault` — the machine was left unsafe (spatula extended
      / unhomed), e.g. an empty *top* slot where the firmware can't complete its
      safe-travel retract. Call :meth:`recover` before any further motion.

    Note: ``homedstatus`` reports homed even when the spatula is stuck extended
    at a top slot, so the firmware's own "unsafe for rotation" signal is used
    (not just :meth:`is_homed`) to detect that case.
    """
    command = f"pick {stacker} {slot} {nest}"
    try:
      await self.send_command(command, timeout=self._motion_timeout)
    except TundraStoreError as exc:
      if left_unsafe(exc.error_lines) or not await self.is_homed():
        raise TundraStoreFault(command, exc.error_lines) from exc
      if any("no plate detected" in line.lower() for line in exc.error_lines):
        raise PlateNotFoundError(command, exc.error_lines) from exc
      raise
    if not close_door:
      await self.open_all_doors()

  async def place(self, stacker: int, slot: int, nest: int, close_door: bool = True):
    """Place the plate at ``nest`` into ``(stacker, slot)``.

    The store re-seals its doors as part of every transfer, so ``close_door``
    controls only the *end* state: with ``close_door=False`` the doors are
    re-opened after the place, leaving the carousel accessible for a following
    operation (handy when the cold environment doesn't matter). The default
    leaves it sealed.
    """
    await self.send_command(f"place {stacker} {slot} {nest}", timeout=self._motion_timeout)
    if not close_door:
      await self.open_all_doors()

  async def open_all_doors(self):
    await self.send_command("openalldoors", timeout=self._motion_timeout)

  async def close_all_doors(self):
    await self.send_command("closealldoors", timeout=self._motion_timeout)

  async def abort(self):
    """Stop current machine operations. ``clear_abort`` is required afterward."""
    await self.send_command("abort")

  async def clear_abort(self):
    await self.send_command("clearabort")

  # --- AutomatedRetrieval capability ----------------------------------------

  async def set_racks(self, racks):
    """Register the storage racks so the capability can resolve a plate/site to
    a ``(stacker, slot)``. Rack *i* (0-based) maps to stacker ``i + 1``; site
    *j* within a rack maps to slot ``j + 1``."""
    self._site_locations = {}
    for rack_index, rack in enumerate(racks):
      for slot_index, site in enumerate(rack.sites.values()):
        self._site_locations[site.name] = (rack_index + 1, slot_index + 1)

  def _locate(self, site: PlateHolder) -> Tuple[int, int]:
    if site.name not in self._site_locations:
      raise ValueError(f"Site '{site.name}' is not a known stacker slot; call set_racks() first.")
    return self._site_locations[site.name]

  def _nest_for_tray(self, tray: Optional[int]) -> int:
    """Map a 0-based capability tray index to a 1-based device nest number.

    ``None`` selects :attr:`loading_tray_nest` (the configured default nest)."""
    if tray is None:
      return self.loading_tray_nest
    if not 0 <= tray < self.num_nests:
      raise ValueError(f"TundraStore has trays 0..{self.num_nests - 1}; got tray={tray}.")
    return tray + 1

  async def fetch_plate_to_loading_tray(self, plate: Plate, tray: Optional[int] = None):
    site = plate.parent
    if not isinstance(site, PlateHolder):
      raise ValueError(f"Plate '{plate.name}' is not in a stacker slot.")
    stacker, slot = self._locate(site)
    await self.pick(stacker, slot, self._nest_for_tray(tray))

  async def store_plate(self, plate: Plate, site: PlateHolder, tray: Optional[int] = None):
    stacker, slot = self._locate(site)
    await self.place(stacker, slot, self._nest_for_tray(tray))

  # --- TemperatureController capability -------------------------------------

  @property
  def supports_active_cooling(self) -> bool:
    return True  # refrigerated store (-20 to 4 C)

  async def request_current_temperature(self) -> float:
    env = await self.request_environment()
    if "TEMP" not in env:
      raise TundraStoreError("environmentstatus", ["no TEMP channel reported"])
    return env["TEMP"].current

  async def set_temperature(self, temperature: float):
    await self.send_command(f"environmentset TEMP {temperature}")

  async def deactivate(self):
    await self.send_command("environment TEMP off")

  # --- HumidityController capability (read-only monitoring) -----------------

  @property
  def supports_humidity_control(self) -> bool:
    return False

  async def request_current_humidity(self) -> float:
    env = await self.request_environment()
    if "RH" not in env:
      raise TundraStoreError("environmentstatus", ["no RH channel reported"])
    return env["RH"].current / 100.0

  async def set_humidity(self, humidity: float):
    raise NotImplementedError("The TundraStore does not support active humidity control.")
