"""BiomekI7Driver: finds the instrument's ACE boards, works out what each one is, and owns them.

A Biomek i7 shows up as one USB device per ACE module: the chassis, and one per arm. Which is which
is not fixed by USB order or serial number, so setup opens every ACE device, asks each its module
ID (its type, and an arm's side), and builds the chassis and a pod per arm from the answers. Only one
program can hold an ACE board at a time: Biomek's Master Controller must not be running.

At rest the interlock is tripped: the arms' drives have no motor power, and every CiA 402 drive
reads FAULT. That is how the Master Controller leaves the instrument (its drive supply polls read
off over 90% of the time), so it is not an error here. Bringing the instrument up arms the
interlock and enables every axis, which is each faulted drive's reset, as Biomek does; a fault
that does not clear stops it before anything moves. When anything fails once the interlock is
armed, the driver shows ERROR and trips the interlock; `stop` trips it too, as the Master
Controller's logs show it doing.
"""

import asyncio
import logging
from typing import (
  TYPE_CHECKING,
  Awaitable,
  Callable,
  Dict,
  Iterable,
  List,
  Mapping,
  Optional,
  Sequence,
  Set,
  Tuple,
  Union,
)

from pylabrobot.beckman_coulter.biomek_i7.driver import motion
from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.board import (
  ACE_PRODUCT_ID,
  ACE_VENDOR_ID,
  AceBoard,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import (
  DeviceConfiguration,
  ModuleConfiguration,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.errors import INTERLOCK_TRIPPED_CODE, ace_error
from pylabrobot.beckman_coulter.biomek_i7.driver.features.chassis import Chassis
from pylabrobot.beckman_coulter.biomek_i7.driver.features.door import Door
from pylabrobot.beckman_coulter.biomek_i7.driver.features.head96 import Head96
from pylabrobot.beckman_coulter.biomek_i7.driver.features.pipettes import Pipettes
from pylabrobot.beckman_coulter.biomek_i7.driver.features.pod import MoveResult, Pod
from pylabrobot.beckman_coulter.biomek_i7.driver.x_rail import XRail
from pylabrobot.io.usb import USB

if TYPE_CHECKING:
  from pylabrobot.beckman_coulter.biomek_i7.deck import BiomekI7Deck

logger = logging.getLogger(__name__)

# A pod's drive supply ("+36V Interlock") reads about 35-36 V with the interlock armed and 0.1-0.16
# V tripped (Master Controller polls, 2026-07 to 10). Above this it counts as on.
DRIVE_SUPPLY_ON_VOLTS = 30.0

# How long returning to rest may take, in seconds: two requests to the chassis.
REST_TIMEOUT = 5.0


async def _together(aws: Iterable[Awaitable[object]]) -> None:
  """Run the pods' work at once. If one fails, the others are cancelled and waited for before the
  error is raised, so nothing is left sending to a board behind the caller's back."""
  tasks = [asyncio.ensure_future(a) for a in aws]
  try:
    await asyncio.gather(*tasks)
  except BaseException:
    for task in tasks:
      task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    raise


def find_ace_boards() -> Dict[str, int]:
  """Every ACE board attached: its USB serial number and current USB address.

  Reading a serial number opens the device; the handle is released straight after, because a
  WinUSB device admits one open handle at a time.
  """
  import usb.util  # optional dependency, as in pylabrobot.io.usb

  boards: Dict[str, int] = {}
  for device in USB(ACE_VENDOR_ID, ACE_PRODUCT_ID, "ACE board").get_available_devices():
    try:
      boards[str(device.serial_number)] = device.address
    finally:
      usb.util.dispose_resources(device)
  return boards


class BiomekI7Driver:
  """The Biomek i7's ACE boards, as a chassis and up to two pods."""

  def __init__(
    self,
    serial_numbers: Optional[Sequence[str]] = None,
    declared_configuration_json: Optional[str] = None,
    boards: Optional[Sequence[AceBoard]] = None,
  ):
    """
    Args:
      serial_numbers: the ACE boards' USB serial numbers. Defaults to every ACE board attached.
      declared_configuration_json: path of the instrument's declared configuration (motor tables,
        and per pod its conversions, limits, geometry and calibration), as
        `DeviceConfiguration.from_biomek` makes it from the instrument's Biomek files. Required:
        the driver never falls back to another, the package's generic one included.
      boards: boards to use instead of opening USB devices: a simulated instrument
        (`simulator.simulated_boards`). With them, the checks that tie the configuration to a real
        instrument (`discover`) are not made.

    Raises:
      ValueError: with no declared configuration, or one changed since it was imported
        (`DeviceConfiguration.load`).
    """
    if declared_configuration_json is None:
      raise ValueError(
        "no declared configuration: pass `declared_configuration_json`, the instrument's own, made "
        "by `DeviceConfiguration.from_biomek` from its Biomek files"
      )
    self._serial_numbers = list(serial_numbers) if serial_numbers is not None else None
    self.declared_configuration_json = declared_configuration_json
    self.declared = DeviceConfiguration.load(declared_configuration_json)
    logger.info(
      "declared configuration %s: chassis %s%s; %s",
      declared_configuration_json,
      self.declared.chassis_serial_number or "none",
      " (generic)" if self.declared.generic else "",
      "; ".join(
        f"{name} {s.get('kind', '')} {s.get('path', '')} read {s.get('read_at', '?')} "
        f"{s.get('fingerprint', '')[:19]}".replace("  ", " ")
        for name, s in self.declared.sources.items()
      ),
    )
    self.motor_tables = self.declared.motor_tables
    # Called when a head's tips change; the device saves its heads' tips on it (`state_json`).
    self.on_tips_changed: Callable[[], None] = lambda: None
    self._given_boards = list(boards) if boards is not None else None
    self.boards: List[AceBoard] = []
    self._connected = False
    self._armed = False  # this driver armed the interlock, and has not tripped it since
    self._tripping = False  # this driver is tripping it: the chassis's trip event is our own
    # While paused (`pause`): each pod's positions when the pause began, by side.
    self._paused: Optional[Dict[str, Dict[str, int]]] = None
    # Set while the interlock is tripped as far as this driver knows: from the start, when the
    # chassis reports a trip, or when this driver trips it; cleared when it arms it. A pod's
    # waits (the `LT` settle) end early when it is set, as the Master Controller's do.
    self.interlock_tripped = asyncio.Event()
    self.interlock_tripped.set()
    self._unwatch: List[Callable[[], None]] = []
    self._background: Set["asyncio.Task[None]"] = set()
    self.chassis: Optional[Chassis] = None
    # The shared X rail (design.md I7), built at discovery from the declared pod geometry.
    self.rail: Optional[XRail] = None
    # The pods, named as STAR names its X-arms: each fills an X-arm's slot (design.md, The pods).
    self.left_x_arm: Optional[Pod] = None
    self.right_x_arm: Optional[Pod] = None
    # The deck the operations work on (STAR's `driver.deck`), set by `BiomekI7`; None without one.
    # Moves, homing and the rail guard never need it.
    self.deck: Optional["BiomekI7Deck"] = None

  # -- lifecycle -------------------------------------------------------------

  async def setup(
    self,
    skip_device_initialization: bool = False,
    skip_pipettes: bool = False,
    *,
    skip_head96: bool = False,
    skip_left_x_arm: bool = False,
    skip_right_x_arm: bool = False,
    force_initialize: bool = False,
  ) -> None:
    """Connect to the instrument, find out what it is, and bring it up.

    This moves the arms: every pod is homed. `discover` is the read-only part; with every skip
    below set, setup only reads. Repeatable: the boards stay open, discovery reads them again, and
    initializing an instrument that is up re-homes nothing (unless `force_initialize`). A setup that
    fails part way releases the boards.

    Args:
      skip_device_initialization: do not load motor tables, even into a board that has lost them.
        A pod with an axis not configured is then left as it is, and cannot move.
      skip_pipettes: leave the pod carrying the Span-8 as it is (STAR's per-feature skip). A pod's
        axes come up together, so this skips the whole pod, its gripper included.
      skip_head96: leave the pod carrying the 96-head as it is; as `skip_pipettes`.
      skip_left_x_arm: leave the left pod as it is: not configured, enabled or moved.
      skip_right_x_arm: leave the right pod as it is.
      force_initialize: home again axes that are already homed, as Biomek's Home All does.

    Raises:
      RuntimeError: if no chassis is found, two modules claim the same place, an axis reports a
        fault, or a pod would move on the X rail beside a skipped pod whose X is not homed.
    """
    logger.warning(
      "The Biomek i7 driver has run on one instrument (Home All, prime, tips and transfers on "
      "both heads, park); its commands were compared with Biomek's logs where Biomek ran the "
      "same operation; it is untested on other instruments. Please report what works and what "
      "does not."
    )
    if not self._connected:
      await self._open()
    try:
      await self.discover()
      await self.initialize(
        skip_device_initialization=skip_device_initialization,
        skip_pipettes=skip_pipettes,
        skip_head96=skip_head96,
        skip_left_x_arm=skip_left_x_arm,
        skip_right_x_arm=skip_right_x_arm,
        force_initialize=force_initialize,
      )
    except BaseException:
      await self.stop()
      raise

  async def _open(self) -> None:
    """Open every ACE board and start reading it."""
    if self._given_boards is not None:
      self.boards = list(self._given_boards)
    else:
      found = find_ace_boards()
      if not found:
        raise RuntimeError("no ACE boards (USB 0AB5:ACE0) found")
      serials = self._serial_numbers or sorted(found)
      missing = [s for s in serials if s not in found]
      if missing:
        raise RuntimeError(f"ACE boards {missing} not found; attached: {sorted(found)}")
      # Opened by the address just enumerated: matching on serial number would open each device
      # again to read its descriptor.
      self.boards = [AceBoard(device_address=found[s], name=f"ACE {s}") for s in serials]
      for board, serial in zip(self.boards, serials):
        board.usb_serial_number = serial
    self._connected = True
    for board in self.boards:
      await board.setup()

  async def discover(self) -> None:
    """Work out which board is which, then read each module, chassis first. Moves nothing, arms
    nothing and writes nothing: it opens the boards if `setup` has not, and can be called on its
    own.

    Each board says what it is: Get Module ID answers a module type (`protocol.MODULE_TYPES`:
    chassis, Multichannel, Span-8) and a side (left, right). That, not its USB serial number or
    order, places it. Seen on one instrument; an unknown type or side, two boards claiming one
    place, no chassis, or a pod type other than the declared one is refused (`_check_declared_against`).

    On the instrument (no `boards` given), two checks follow, each refusing:
    - the declared configuration must be this instrument's: its chassis serial number and each
      declared pod's module serial number as the modules report them, and not the generic one
      (`DeviceConfiguration.generic`);
    - it must still match the Biomek files it was imported from, where they can be found
      (`DeviceConfiguration.check_sources`): a re-framed deck or re-measured motors there make it
      stale. Where they cannot be found (another computer), a warning says so.

    Raises:
      RuntimeError: for a board this driver cannot place, or no chassis.
      ValueError: for a declared configuration that is not this instrument's, or is stale.
    """
    if not self._connected:
      await self._open()
    places: Dict[str, AceBoard] = {}
    types: Dict[str, str] = {}
    for board in self.boards:
      module = p.parse_module_id((await board.send(p.get_module_id())).payload)
      if module.type is None:
        raise RuntimeError(
          f"{board.name}: module type 0x{module.type_code:02X} is not one this driver knows"
        )
      place = "chassis" if module.type == "chassis" else str(module.side)
      if place in places:
        raise RuntimeError(
          f"{board.name} and {places[place].name} both report themselves as {place}"
        )
      if place not in ("chassis", "left", "right"):
        raise RuntimeError(
          f"{board.name}: {module.type} module reports side 0x{module.side_code:02X}"
        )
      places[place] = board
      types[place] = module.type
    if "chassis" not in places:
      raise RuntimeError("no chassis module among the ACE boards")
    self._check_declared_against(types)
    self.chassis = Chassis(places["chassis"])
    self.left_x_arm = (
      Pod(places["left"], "left", types["left"], self.motor_tables, self.declared.pods.get("left"))
      if "left" in places
      else None
    )
    self.right_x_arm = (
      Pod(
        places["right"], "right", types["right"], self.motor_tables, self.declared.pods.get("right")
      )
      if "right" in places
      else None
    )
    self.rail = XRail({pod.side: pod.declared for pod in self.arms})
    self._watch_faults()
    for pod in self.arms:
      pod.driver = self
      pod.rail = self.rail
      pod.interlock_tripped = self.interlock_tripped
      self.rail.readers[pod.side] = pod.request_rail_pose
    modules: List[Tuple[str, ModuleConfiguration]] = [("chassis", await self.chassis.discover())]
    for pod in self.arms:
      modules.append((str(pod), await pod.discover()))
      logger.info(
        "found %s, module %s", pod, pod.configuration.serial_number if pod.configuration else "?"
      )
    for name, found in modules:
      untested = found.untested_firmware()
      if untested:
        logger.warning(
          "%s runs firmware this driver has not been run against: %s", name, "; ".join(untested)
        )
    if self._given_boards is not None:
      return  # simulated: no instrument to tie the configuration to
    mismatches = self._serial_number_mismatches()
    if mismatches:
      raise ValueError(
        "the declared configuration is not this instrument's: "
        + "; ".join(mismatches)
        + ". Its motor tables, conversions, geometry and calibration may not fit. Pass this "
        "instrument's own `declared_configuration_json`, made by "
        "`DeviceConfiguration.from_biomek` from its Biomek files."
      )
    sources = self.declared.check_sources()
    if sources.changed:
      shown = sources.changed[:20]
      more = len(sources.changed) - len(shown)
      raise ValueError(
        "the declared configuration no longer matches the Biomek files it was imported from ("
        + "; ".join(shown)
        + (f"; and {more} more" if more else "")
        + "): the instrument was re-calibrated or re-configured since. Import it again: "
        "`DeviceConfiguration.from_biomek(...).save(path)`."
      )
    if sources.unavailable:
      logger.warning(
        "the declared configuration's sources cannot be read here (%s): whether the instrument "
        "was re-calibrated since %s cannot be checked",
        "; ".join(sources.unavailable),
        self.declared.sources.get("instrument_file", {}).get("read_at", "it was imported"),
      )
    logger.info(
      "the declared configuration is this instrument's: serial numbers match the modules; "
      "sources %s",
      "unchanged since import" if not sources.unavailable else "partly not checked (above)",
    )

  async def request_serial_numbers(self) -> Dict[str, Optional[str]]:
    """The serial numbers the modules report: `chassis`, and `left` / `right` for each pod found.
    Moves nothing.

    Raises:
      RuntimeError: before discovery (`discover` or `setup`).
    """
    return self._serial_numbers_found()

  def _serial_number_mismatches(self) -> List[str]:
    """How the declared configuration's serial numbers differ from the modules' (read at discovery),
    each as a sentence; empty when they are this instrument's."""
    found = self._serial_numbers_found()
    d = self.declared
    problems = (
      ["it is the generic configuration, which describes no instrument"] if d.generic else []
    )
    if d.chassis_serial_number != found["chassis"]:
      problems.append(
        f"chassis {d.chassis_serial_number or 'not recorded'}, found {found['chassis']}"
      )
    for side, pod in d.pods.items():
      if side in found and pod.module_serial_number != found[side]:
        problems.append(
          f"{side} pod module {pod.module_serial_number or 'not recorded'}, found {found[side]}"
        )
    return problems

  def _check_declared_against(self, types: Dict[str, str]) -> None:
    """Refuse an instrument whose pods are not the ones declared: their motor tables and geometry
    would be another arm's. A pod declared but not found, or found but not declared, is only
    logged (a single-pod instrument, or one being configured).

    Raises:
      ValueError: if a pod is declared on a side where the boards report another arm type.
    """
    for side in ("left", "right"):
      declared = self.declared.pods.get(side)
      found = types.get(side)
      if declared is not None and found is not None and declared.module_type != found:
        raise ValueError(
          f"the declared configuration has a {declared.module_type} pod on the {side}, but the "
          f"boards report a {found}; pass this instrument's own `declared_configuration_json`"
        )
      if (declared is None) != (found is None):
        logger.warning(
          "%s pod: declared %s, found %s",
          side,
          declared.module_type if declared else "nothing",
          found or "nothing",
        )

  def save_configuration(self, path: str, indent: Optional[int] = 2) -> None:
    """Write the declared configuration to `path`, as loaded, for `declared_configuration_json` to
    read back. A configuration is made only from the instrument's Biomek files
    (`DeviceConfiguration.from_biomek`), so nothing found at discovery is written into it.

    Args:
      path: where to write it.
      indent: how far to indent the JSON, or None to write it on one line.
    """
    self.declared.save(path, indent=indent)

  def _serial_numbers_found(self) -> Dict[str, Optional[str]]:
    if self.chassis is None or self.chassis.configuration is None:
      raise RuntimeError("the modules have not been read; call `discover()` or `setup()` first")
    found: Dict[str, Optional[str]] = {"chassis": self.chassis.configuration.serial_number}
    for pod in self.arms:
      found[pod.side] = pod.configuration.serial_number if pod.configuration else None
    return found

  async def stop(self) -> None:
    """Return the instrument to rest and release every board. Moves nothing.

    If this driver armed the interlock, as the Master Controller does when its host goes away: each
    pod's module is stopped (Stop Module Execution) and its positions forgotten, then the
    interlock is tripped and READY shown. Otherwise nothing is written. What the boards do on their
    own when the host goes away is not known.
    """
    # As the Master Controller does when its host goes away: stop each pod's
    # module, forget its positions, then trip the interlock. Only while this driver has the
    # interlock armed: otherwise the drives have no power, and a read-only session writes nothing.
    if self._armed:
      for pod in self.arms:
        try:
          await asyncio.wait_for(pod._unchecked_fw_stop_module_execution(), REST_TIMEOUT)
        except BaseException:
          logger.exception("could not stop %s", pod)
        pod._forget_positions()
    await self._return_to_rest()
    for unwatch in self._unwatch:
      unwatch()
    self._unwatch = []
    for board in self.boards:
      try:
        await board.stop()
      except Exception:
        logger.exception("failed to release %s", board.name)
    self._connected = False

  # -- what the instrument has ------------------------------------------------

  @property
  def arms(self) -> List[Pod]:
    """The pods this instrument has, left first (STAR's `arms`)."""
    return [pod for pod in (self.left_x_arm, self.right_x_arm) if pod is not None]

  @property
  def x_arm(self) -> Pod:
    """The pod, on an instrument with one (STAR's `x_arm`).

    Raises:
      RuntimeError: before discovery, when which pods are fitted is not yet known.
      ValueError: with no pod, or two; reach two as `left_x_arm` and `right_x_arm`.
    """
    if self.chassis is None:
      raise RuntimeError("the instrument has not been read; call `discover()` or `setup()` first")
    arms = self.arms
    if not arms:
      raise ValueError("this instrument has no pod")
    if len(arms) > 1:
      raise ValueError(
        "this instrument has two pods, so `x_arm` is ambiguous - reach it through "
        "`left_x_arm` or `right_x_arm`."
      )
    return arms[0]

  def arm(self, name: Union[str, int]) -> Pod:
    """A pod by any name it goes by: its side (`"left"`), Biomek's number (`1`, `"Pod1"`), or
    Biomek's component name in its logs (`"MC_1"`). For porting Biomek methods and logs, which
    name pods by number; the driver itself names them by side.

    Raises:
      KeyError: if no pod goes by that name.
    """
    for pod in self.arms:
      if name in (pod.side, pod.pod_number, f"Pod{pod.pod_number}", pod.component):
        return pod
    raise KeyError(f"no pod {name!r}; have {[(p.side, p.component) for p in self.arms]}")

  def _one_with(self, feature: str) -> Optional[Pod]:
    """The one pod carrying `feature`, None if none does.

    Raises:
      ValueError: if more than one pod carries it; reach it through the pod instead.
    """
    carriers = [pod for pod in self.arms if getattr(pod, feature) is not None]
    if len(carriers) > 1:
      raise ValueError(
        f"both pods have a {feature}, so `{feature}` is ambiguous - reach it through "
        f"`left_x_arm.{feature}` or `right_x_arm.{feature}`."
      )
    return carriers[0] if carriers else None

  @property
  def front_cover(self) -> Optional[Door]:
    """The door, by STAR's name for it, once discovery has found the chassis."""
    return None if self.chassis is None else self.chassis.front_cover

  @property
  def head96(self) -> Optional[Head96]:
    """The 96-channel head, on an instrument where one pod has it."""
    pod = self._one_with("head96")
    return pod.head96 if pod is not None else None

  @property
  def pipettes(self) -> Optional[Pipettes]:
    """The Span-8 channels, on an instrument where one pod has them."""
    pod = self._one_with("pipettes")
    return pod.pipettes if pod is not None else None

  async def request_axis_states(self) -> Dict[str, Dict[str, p.AxisStateName]]:
    """Every axis's state, by pod side then axis name. Moves nothing."""
    return {pod.side: await pod.request_axis_states() for pod in self.arms}

  # -- configuration, interlock and homing ---------------------------------------
  # These write to the boards and move the arms. Run them only on a clear deck, with someone at
  # the emergency stop.

  def _require_chassis(self) -> Chassis:
    if self.chassis is None:
      raise RuntimeError("no chassis; call `setup()` first")
    return self.chassis

  def skipped_sides(
    self,
    *,
    skip_pipettes: bool = False,
    skip_head96: bool = False,
    skip_left_x_arm: bool = False,
    skip_right_x_arm: bool = False,
  ) -> Set[str]:
    """The sides (`"left"`, `"right"`) a setup with these skips leaves as they are: the sides
    named, and the pod carrying each feature named (STAR's per-feature skips; a feature this
    instrument does not have skips nothing). Known after discovery."""
    sides = {s for s, skip in (("left", skip_left_x_arm), ("right", skip_right_x_arm)) if skip}
    for pod in self.arms:
      if (skip_pipettes and pod.pipettes is not None) or (skip_head96 and pod.head96 is not None):
        sides.add(pod.side)
    return sides

  async def initialize(
    self,
    skip_device_initialization: bool = False,
    skip_pipettes: bool = False,
    *,
    skip_head96: bool = False,
    skip_left_x_arm: bool = False,
    skip_right_x_arm: bool = False,
    force_initialize: bool = False,
  ) -> None:
    """Bring the instrument up: what `setup` does after discovery, and what Home All is later.

    Reads every axis's state first. A pod with an axis not configured (the board was powered up, or
    a drive lost its parameters) has its motor tables loaded; one fully configured does not, since
    loading them again would un-home it. Then the pods are homed together (`home`). The arguments
    are those of `setup`.

    An axis in fault is not refused: enabling it is its fault reset, as when Biomek re-arms and
    enables after a fault, and calling this is the person's go-ahead that Biomek waits for. A CiA
    402 drive in fault with its motor supply off is the instrument at rest and is only noted; any
    other fault is warned about, since under power it usually means a following error or a
    collision. A fault that does not clear fails the enable, and `home` returns the instrument to
    rest before anything moves.

    Raises:
      RuntimeError: if a pod would move on the X rail beside a skipped pod whose X is not homed
        (nothing has been written), or the drive supply does not come up when armed.
      AceError: if an axis cannot be enabled, or a move or home fails; the interlock is then
        tripped and ERROR shown.
    """
    skipped = self.skipped_sides(
      skip_pipettes=skip_pipettes, skip_head96=skip_head96,
      skip_left_x_arm=skip_left_x_arm, skip_right_x_arm=skip_right_x_arm,
    )  # fmt: skip
    pods = [pod for pod in self.arms if pod.side not in skipped]
    if skip_device_initialization and not pods:
      return

    states = await self.request_axis_states()
    for pod in pods:
      faulted = [name for name, state in states[pod.side].items() if state == "fault"]
      if not faulted:
        continue
      supply = await pod.request_drive_supply()
      at_rest = supply < DRIVE_SUPPLY_ON_VOLTS and all(
        pod.axis(name).drive == "cia402" for name in faulted
      )
      if at_rest:
        logger.info("%s: %s in fault with the drive supply off (%.1f V), as at rest", pod,
                    ", ".join(faulted), supply)  # fmt: skip
      else:
        positions = await pod.request_positions()
        logger.warning(
          "%s: %s in fault with the drive supply at %.1f V; enabling will reset them. A fault under "
          "power usually means a following error or a collision: check the deck. Positions: %s",
          pod,
          ", ".join(faulted),
          supply,
          {name: positions[name] for name in faulted},
        )

    unconfigured = [pod for pod in pods if "not_configured" in states[pod.side].values()]
    if unconfigured and skip_device_initialization:
      logger.warning(
        "%s not configured, and configuring was skipped; left as they are, and they cannot move",
        ", ".join(str(pod) for pod in unconfigured),
      )
      pods = [pod for pod in pods if pod not in unconfigured]
      unconfigured = []
    if pods:
      await self._check_shared_x_rail(pods)
    if unconfigured:
      await self.configure(unconfigured)
    if pods:
      await self.home(pods, force=force_initialize)

    # A pod that answered every request is not a pod that came up, so each is asked again.
    states = await self.request_axis_states()
    down = [
      f"{pod.side} {name} ({state})"
      for pod in pods
      for name, state in states[pod.side].items()
      if state != "enabled"
    ]
    if down:
      logger.warning("initialize finished with these axes not enabled: %s", ", ".join(down))

  async def _check_shared_x_rail(self, moving: Sequence[Pod]) -> None:
    """Refuse to move pods on the X rail beside a pod left out whose X is not homed: where that pod
    stands is not known. Reads only.

    Raises:
      RuntimeError: if a pod left out has its X not homed.
    """
    for pod in self.arms:
      if pod in moving:
        continue
      others = ", ".join(str(m) for m in moving)
      if await pod.request_homed_state("X") != "homed":
        raise RuntimeError(
          f"the {pod} is left as it is and its X is not homed, so where it stands on the shared X "
          f"rail is not known; the {others} cannot move beside it. Bring up both pods. Nothing was "
          "moved"
        )
      logger.warning(
        "the %s is left as it is; the %s's X moves are not checked against it", pod, others
      )

  async def configure(self, pods: Optional[Sequence[Pod]] = None) -> None:
    """Load the motor tables into pods' boards (every pod by default), as after a power-up. Moves
    nothing.

    A board keeps its tables until it loses power. Loading them into a configured board is
    accepted, and leaves every axis disabled and not homed, so this un-homes
    the pods; `initialize` loads them only where an axis is not configured.
    """
    chassis = self._require_chassis()
    await chassis.prepare_interlock()
    for pod in self.arms if pods is None else pods:
      await pod.configure()
    await chassis._io_box_write_after_configure()

  async def arm_interlock(self, pods: Optional[Sequence[Pod]] = None) -> None:
    """Arm the interlock, check the pods' drive supply came up, then enable every axis of pods
    (every pod by default). Enabling resets a drive's fault. Moves nothing: the drives hold where
    they stand.

    The door does not take part: Biomek arms with it open, and opening it does not trip the
    interlock (Biomek's logs); only its state is logged.

    Raises:
      RuntimeError: if the interlock does not arm (the light curtain broken), or a pod's drive
        supply stays off.
      AceError: if an axis cannot be enabled.
    """
    targets = self.arms if pods is None else list(pods)
    chassis = self._require_chassis()
    if await chassis.front_cover.request_state() == "open":
      logger.info("arming the interlock with the door open (the door does not trip it)")
    self._armed = True  # before the request: a re-arm that times out may still have armed it
    self.interlock_tripped.clear()
    await chassis.arm_interlock()
    state = await chassis.request_interlock_state()
    if state != "armed":
      raise RuntimeError(f"the interlock reads {state} after re-arming")
    for pod in targets:
      supply = await pod.request_drive_supply()
      if supply < DRIVE_SUPPLY_ON_VOLTS:
        raise RuntimeError(f"{pod}: the drive supply reads {supply:.1f} V with the interlock armed")
    await _together(pod.enable_axes() for pod in targets)

  async def home(self, pods: Optional[Sequence[Pod]] = None, force: bool = False) -> None:
    """Home pods (every pod by default) at once, the Master Controller's Home All without its
    Span-8 priming.

    Arms the interlock, then homes the pods together, as Biomek does: each runs its `HM` recipe,
    and a Multichannel pod then homes its plunger (`HD`). The Span-8's syringe pumps are left for
    priming. Needs the motor tables loaded (`initialize` sees to it).

    If anything fails once the interlock is armed, ERROR is shown and the interlock tripped, which
    cuts the drives' power, before the error is raised; nothing is retried. Biomek instead leaves
    the drives powered while its dialog waits for the person; here no one may be waiting, so the
    instrument goes back to rest.

    Args:
      pods: the pods to home.
      force: home again axes already homed, as Biomek does. Without it they are only moved to
        where a Home leaves them.
    """
    targets = self.arms if pods is None else list(pods)

    async def home_pod(pod: Pod) -> None:
      await pod.home(force=force)
      if pod.head96 is not None:
        await pod.head96.home_plunger(force=force)

    try:
      await self.arm_interlock(targets)
      await _together(home_pod(pod) for pod in targets)
    except BaseException:
      await self._return_to_rest(error=True)
      raise

  async def move_axes(
    self, targets: Mapping[str, Mapping[str, int]], speed: int = 1000
  ) -> Dict[str, MoveResult]:
    """Move axes of both pods at once: each pod's `AM` (`Pod.move_axes`), started together, as
    Biomek runs the two pods' steps. The shared X rail keeps them apart: a move into the other
    pod's range is refused, and one crossing only the other's path waits for it.

    Args:
      targets: per pod side (`left`, `right`), its axes' targets in counts.
      speed: 1..1000, Biomek's speed field, for both.

    Returns:
      Each pod's result, by side.

    Raises:
      ValueError: for a side with no pod. Otherwise as `Pod.move_axes`; if one pod's move fails,
        the other's is cancelled and waited for first. Nothing returns the instrument to rest:
        whether to is the caller's (design.md I5).
    """
    pods: Dict[str, Pod] = {pod.side: pod for pod in self.arms}
    missing = sorted(set(targets) - set(pods))
    if missing:
      raise ValueError(f"no pod on the {', '.join(missing)}; have {sorted(pods)}")
    results: Dict[str, MoveResult] = {}

    async def move(side: str) -> None:
      results[side] = await pods[side].move_axes(targets[side], speed)

    await _together(move(side) for side in targets)
    return results

  async def _return_to_rest(self, error: bool = False) -> None:
    """Trip the interlock, if this driver armed it, and show READY (ERROR with `error`). Never
    raises: it runs on the way out of a failure."""
    if not self._armed:
      return
    await self._trip(error)

  async def _trip(self, error: bool) -> None:
    """Trip the interlock and show READY or ERROR. Never raises."""
    if self.chassis is None:
      return
    self._tripping = True
    try:
      await asyncio.wait_for(self.chassis.trip_interlock(error=error), REST_TIMEOUT)
      self._armed = False
      self.interlock_tripped.set()
    except BaseException:
      logger.exception(
        "could not trip the interlock; the drives may still be powered: use the E-stop"
      )
    finally:
      self._tripping = False

  # -- pause for access --------------------------------------------------------------
  # Biomek's whole-system pause, as its logs show it: a deliberate interlock trip with PAUSED shown
  # and the pods' positions saved; resuming re-arms, enables every axis and moves the pods back.
  # There is no mute: the light curtain may be broken only because the drives have no power.

  @property
  def paused(self) -> bool:
    """Whether the instrument is paused (`pause`), waiting for `resume`."""
    return self._paused is not None

  async def pause(self) -> None:
    """Pause for access: cut the pods' drive power and show PAUSED, so the light curtain may be
    broken without an error. Call it between operations, as Biomek's Pause step runs between
    steps: a move still running is stopped where it is. Moving the pods out of the way first is
    the caller's. Each pod's positions are read and kept for `resume`.

    Raises:
      RuntimeError: if the interlock is not armed by this driver (nothing to pause), or the
        instrument is already paused.
    """
    if self._paused is not None:
      raise RuntimeError("the instrument is already paused")
    if not self._armed:
      raise RuntimeError("the interlock is not armed: nothing to pause")
    chassis = self._require_chassis()
    self._tripping = True
    try:
      await chassis._unchecked_fw_trip_interlock()
      self._armed = False
      self.interlock_tripped.set()
    finally:
      self._tripping = False
    await chassis._unchecked_fw_set_status_display("paused")
    # Biomek reads the positions after the trip; the drives answer without power. Kept as the
    # adjusted positions moves take (a Span-8 Z channel's hysteresis), as the driver keeps them.
    saved: Dict[str, Dict[str, int]] = {}
    for pod in self.arms:
      read = await pod.request_positions()
      saved[pod.side] = {
        name: motion.update_cpos(pod.cpositions[name], counts, pod.axis(name).hysteresis)[1]
        for name, counts in read.items()
      }
      pod._forget_positions()
    self._paused = saved
    logger.info("paused: the interlock is tripped; the light curtain may be broken")

  async def resume(self, speed: int = 200) -> None:
    """Resume after `pause`: re-arm the interlock, enable every axis, then move each pod back to
    where it was when paused (an axis pushed by hand is returned; the plunger and syringe pumps
    are not, as Biomek does not, nor is a gripper holding labware opened or closed). Nothing is
    homed.

    Args:
      speed: 1..1000, Biomek's speed field, for the moves back.

    Raises:
      RuntimeError: if not paused, or if the interlock does not arm (the light curtain still
        broken): the instrument stays paused, so clear it and call `resume` again.
    """
    saved = self._paused
    if saved is None:
      raise RuntimeError("the instrument is not paused")
    try:
      await self.arm_interlock()
    except BaseException:
      self._armed = False
      self.interlock_tripped.set()
      raise
    self._paused = None
    logger.info("resumed: the interlock is armed")
    for pod in self.arms:
      keep = {"D"} | ({"GG"} if pod._held_labware is not None else set())
      back = {
        name: counts
        for name, counts in saved.get(pod.side, {}).items()
        if name not in keep and not pod.axis(name).is_pump
      }
      if any(pod.cpositions[name][1] != counts for name, counts in back.items()):
        logger.info("%s moving back to where it was paused", pod.component)
        await pod.move_axes(back, speed)

  # -- faults the boards report unasked ------------------------------------------
  # As the Master Controller handles them, as its logs show it.

  def _watch_faults(self) -> None:
    """Listen for the chassis reporting the interlock tripped and the pods reporting a motor that
    failed on its own (both arrive unasked, sequence 0)."""
    for unwatch in self._unwatch:
      unwatch()
    self._unwatch = []
    if self.chassis is not None:
      self._unwatch.append(self.chassis.board.subscribe(self._on_chassis_message))
    for pod in self.arms:
      self._unwatch.append(pod.board.subscribe(self._pod_watcher(pod)))

  def _pod_watcher(self, pod: Pod) -> Callable[[p.Reply], None]:
    return lambda reply: self._on_pod_message(pod, reply)

  def _on_chassis_message(self, reply: p.Reply) -> None:
    """The interlock tripped (Notify On Interlock Trip): the light curtain broken, or a trip by
    the host."""
    if reply.status == p.Status.EVENT and (reply.device, reply.opcode) == p.INTERLOCK_TRIPPED:
      self._interlock_tripped("A light curtain violation has occurred.")

  def _on_pod_message(self, pod: Pod, reply: p.Reply) -> None:
    """A motor's Move All ended in an error nobody was waiting for: the Master Controller trips
    the interlock with "The <side> <motor> motor failed: <error>." (an interlock trip itself is
    reported by the chassis instead)."""
    if not (
      reply.opcode == 0x50
      and reply.status == p.Status.NOTICE
      and reply.device in (p.Device.MOTOR, p.Device.PUMP)
      and reply.error_code is not None
      and reply.error_code != INTERLOCK_TRIPPED_CODE
    ):
      return
    names = {(a.device, a.index): a.name for a in pod.axes}
    axis = names.get((reply.device, reply.index), f"device 0x{reply.device:02X} {reply.index}")
    error = ace_error(reply.error_code, reply.device_error, f"{pod} {axis}")
    logger.error("The %s %s motor failed: %s.", pod.side, axis, error)
    self.interlock_tripped.set()
    for each in self.arms:
      each._forget_positions()
    self._in_background(self._trip(error=True))

  def _interlock_tripped(self, reason: str) -> None:
    """What the Master Controller does when the interlock trips under a running instrument: every
    position is forgotten (read again after re-arming), waits end, and ERROR is shown. A trip this
    driver made itself (at rest, or after a failure) only marks it tripped."""
    ours = self._tripping or not self._armed
    self.interlock_tripped.set()
    for pod in self.arms:
      pod._forget_positions()
    if ours:
      return
    logger.error("the interlock tripped: %s", reason)
    self._armed = False
    if self.chassis is not None:
      self._in_background(self.chassis._unchecked_fw_set_status_display("error"))

  def _in_background(self, coroutine: Awaitable[None]) -> None:
    """Run a board write from a message handler, which cannot wait; its failure is logged."""

    async def run() -> None:
      try:
        await coroutine
      except Exception:
        logger.exception("after a fault the boards reported")

    task = asyncio.get_running_loop().create_task(run())
    self._background.add(task)
    task.add_done_callback(self._background.discard)
