from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, cast

from pylabrobot.capabilities.automated_retrieval.backend import AutomatedRetrievalBackend
from pylabrobot.resources import Plate, PlateCarrier, PlateHolder

from ..errors import (
  HighResSampleStorageError,
  HighResSampleStorageFault,
  PlateNotFoundError,
  left_unsafe,
)
from ..settings import HighResSampleStorageSettings
from ..types import DOOR_STATES, NEST_STATES, DoorState, NestState, StackerDimensions
from .protocol import parse_kv

if TYPE_CHECKING:
  from .driver import HighResSampleStorageDriver


class HighResSampleStorageAutomatedRetrievalBackend(AutomatedRetrievalBackend):
  """Plate storage/motion (automated retrieval) for a HighRes sample store.

  Plates are stored in a refrigerated carousel of *stackers*, each holding a
  number of *slots*. An external robot hands plates to/from one of the device's
  *nests* (transfer stations); the internal spatula moves plates between a nest
  and a (stacker, slot). The low-level :meth:`pick` / :meth:`place` take those
  three indices directly.

  The store has two nests, exposed through the multi-tray
  :class:`AutomatedRetrieval` capability: its ``tray_index`` argument is a
  0-based nest index (tray ``i`` -> device nest ``i + 1``), and ``tray_index=None``
  selects :attr:`loading_tray_nest`.

  All commands are issued through the owning :class:`HighResSampleStorageDriver`.
  """

  def __init__(
    self,
    driver: "HighResSampleStorageDriver",
    loading_tray_nest: int = 1,
    num_nests: int = 2,
  ):
    super().__init__()
    self._driver = driver
    self.loading_tray_nest = loading_tray_nest
    self.num_nests = num_nests
    # Slide (Y) below this is "retracted"; a spatula stuck in a stacker sits at
    # the ~256mm slide-in depth, home is 0. Used by request_is_parked()/recover().
    self._retracted_y_max = 50.0
    # stacker/slot lookup, built from racks by set_racks().
    self._site_locations: Dict[str, Tuple[int, int]] = {}

  # --- queries (verified against firmware 3.0.0.119) ------------------------

  async def request_axis_positions(self) -> Dict[str, float]:
    """Return the ``status`` report: carousel/theta/Y/Z positions."""
    out: Dict[str, float] = {}
    for key, value in parse_kv(await self._driver.send_command("status")).items():
      try:
        out[key] = float(value)
      except ValueError:
        continue
    return out

  async def request_is_homed(self) -> bool:
    lines = await self._driver.send_command("homedstatus")
    return any(line.strip().lower() == "homed" for line in lines)

  async def request_door_status(self) -> Dict[str, DoorState]:
    """Parsed ``doorstatus`` output, keyed by door name."""
    doors: Dict[str, DoorState] = {}
    for name, value in parse_kv(await self._driver.send_command("doorstatus")).items():
      state = value.lower()
      doors[name] = cast(DoorState, state) if state in DOOR_STATES else "unknown"
    return doors

  async def request_nest_status(self) -> Dict[int, NestState]:
    """Parsed ``neststatus`` output, keyed by nest number."""
    nests: Dict[int, NestState] = {}
    for key, value in parse_kv(await self._driver.send_command("neststatus")).items():
      try:
        nest = int(key)
      except ValueError:
        continue
      state = value.lower()
      nests[nest] = cast(NestState, state) if state in NEST_STATES else "unknown"
    return nests

  async def request_spatula_is_holding(self) -> bool:
    """Whether a plate is currently held on the spatula (``platestatus``)."""
    lines = await self._driver.send_command("platestatus")
    return not any("NO_PLATE" in line for line in lines)

  async def request_nest_is_holding(self, nest: int) -> bool:
    """Whether a plate is present on ``nest`` (per its plate sensor).

    Note: this unit reports an occupied nest as ``UNKNOWN`` rather than
    ``OCCUPIED``; anything other than ``CLEAR`` counts as holding.
    """
    state = (await self.request_nest_status()).get(nest)
    return state is not None and state != "clear"

  async def probe_presence(self, stacker: int, slot: int, to_nest: int = 1) -> bool:
    """Probe whether a plate is present in ``(stacker, slot)`` by attempting a
    pick. Returns ``True`` if a plate was there, ``False`` if the slot is empty.

    SIDE EFFECT: a plate that is found is moved to ``to_nest`` (the only way to
    sense a stacker slot is to pick it). Only safe for non-top slots, where an
    empty pick is graceful; the top slot (24) faults when empty — see
    :meth:`pick`. For nests, use :meth:`request_nest_is_holding` instead (a
    non-destructive sensor read).
    """
    try:
      await self.pick(stacker, slot, to_nest)
      return True
    except PlateNotFoundError:
      return False

  async def request_stacker_dimensions(self) -> List[StackerDimensions]:
    """Parse ``getstackerdimensions`` (``<stacker>: <zero_offset> <slot_height>
    <slot_count>``)."""
    dims: List[StackerDimensions] = []
    for line in await self._driver.send_command("getstackerdimensions"):
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
    lines = await self._driver.send_command("settings", timeout=self._driver.read_timeout)
    return HighResSampleStorageSettings.from_lines(lines)

  async def request_stacker_barcodes(self, stacker, slot: Optional[int] = None) -> List[str]:
    """Scan a stacker (or a single slot) for barcodes.

    Args:
      stacker: Stacker number, or the string ``"all"`` to scan the whole
        inventory.
      slot: Optional single slot to scan.
    """
    command = f"barcode {stacker}"
    if slot is not None:
      command += f" {slot}"
    return await self._driver.send_command(command, timeout=self._driver.motion_timeout)

  # --- motion ---------------------------------------------------------------

  async def home(self):
    """Home the system. The first step closes all doors, which requires the
    pneumatic supply (clean dry air >80 psi); without it this raises
    :class:`HighResSampleStorageError` ("Unable to close all doors")."""
    await self._driver.send_command("home", timeout=self._driver.motion_timeout)

  async def request_is_parked(self) -> bool:
    """Whether the machine is genuinely safe to move: homed AND the spatula
    retracted out of the carousel.

    Prefer this over :meth:`request_is_homed`. ``homedstatus`` reports homed even while
    the spatula is stuck extended in a stacker after a faulted top-slot pick, so
    it alone is not a safe-state check; this also verifies the slide (Y) axis is
    near its home position (a stuck spatula sits at the ~256mm slide-in depth).
    """
    if not await self.request_is_homed():
      return False
    y = (await self.request_axis_positions()).get("Y axis")
    return y is not None and abs(y) < self._retracted_y_max

  async def recover(self) -> bool:
    """Retract the spatula and re-home after a motion fault.

    A faulted command (e.g. an empty-slot ``pick`` in the top few slots) can
    leave the spatula extended. This ALWAYS issues the retract (``spatulaout``)
    + ``home`` — it does not trust ``homedstatus`` to decide whether recovery is
    needed, because that reports homed even while the spatula is stuck extended.
    Retries a few times. Returns ``True`` once :meth:`request_is_parked`.
    """
    for _ in range(3):
      for command in ("enable", "spatulaout"):
        try:
          await self._driver.send_command(command, timeout=self._driver.motion_timeout)
        except HighResSampleStorageError:
          pass
      try:
        await self._driver.send_command("home", timeout=self._driver.motion_timeout)
      except HighResSampleStorageError:
        pass
      if await self.request_is_parked():
        return True
    return False

  async def pick(self, stacker: int, slot: int, nest: int, close_door: bool = True):
    """Retrieve a plate from ``(stacker, slot)`` to ``nest``.

    ``close_door=False`` re-opens the doors after the transfer (see :meth:`place`).

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
    try:
      await self._driver.send_command(command, timeout=self._driver.motion_timeout)
    except HighResSampleStorageError as exc:
      if left_unsafe(exc.error_lines) or not await self.request_is_homed():
        raise HighResSampleStorageFault(command, exc.error_lines) from exc
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
    await self._driver.send_command(
      f"place {stacker} {slot} {nest}", timeout=self._driver.motion_timeout
    )
    if not close_door:
      await self.open_all_doors()

  async def open_all_doors(self):
    await self._driver.send_command("openalldoors", timeout=self._driver.motion_timeout)

  async def close_all_doors(self):
    await self._driver.send_command("closealldoors", timeout=self._driver.motion_timeout)

  async def abort(self):
    """Stop current machine operations. ``clear_abort`` is required afterward."""
    await self._driver.send_command("abort")

  async def clear_abort(self):
    await self._driver.send_command("clearabort")

  # --- AutomatedRetrieval capability ----------------------------------------

  async def set_racks(self, racks: List[PlateCarrier]):
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

  def _nest_for_tray(self, tray_index: Optional[int]) -> int:
    """Map a 0-based capability tray index to a 1-based device nest number.

    ``None`` selects :attr:`loading_tray_nest` (the configured default nest)."""
    if tray_index is None:
      return self.loading_tray_nest
    if not 0 <= tray_index < self.num_nests:
      raise ValueError(
        f"sample store has trays 0..{self.num_nests - 1}; got tray_index={tray_index}."
      )
    return tray_index + 1

  async def fetch_plate_to_loading_tray(self, plate: Plate, tray_index: Optional[int] = None):
    site = plate.parent
    if not isinstance(site, PlateHolder):
      raise ValueError(f"Plate '{plate.name}' is not in a stacker slot.")
    stacker, slot = self._locate(site)
    await self.pick(stacker, slot, self._nest_for_tray(tray_index))

  async def store_plate(self, plate: Plate, site: PlateHolder, tray_index: Optional[int] = None):
    stacker, slot = self._locate(site)
    await self.place(stacker, slot, self._nest_for_tray(tray_index))
