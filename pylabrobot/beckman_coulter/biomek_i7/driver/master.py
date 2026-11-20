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
from typing import Awaitable, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

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
from pylabrobot.beckman_coulter.biomek_i7.driver.features.pod import Pod
from pylabrobot.io.usb import USB

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
        (`simulator.replay_boards`). With them, the checks that tie the configuration to a real
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
    self._given_boards = list(boards) if boards is not None else None
    self.boards: List[AceBoard] = []
    self._connected = False
    self._armed = False  # this driver armed the interlock, and has not tripped it since
    self._tripping = False  # this driver is tripping it: the chassis's trip event is our own
    self._paused = False  # paused (`pause`), waiting for `resume`
    # Set while the interlock is tripped as far as this driver knows: from the start, when the
    # chassis reports a trip, or when this driver trips it; cleared when it arms it.
    self.interlock_tripped = asyncio.Event()
    self.interlock_tripped.set()
    self._unwatch: List[Callable[[], None]] = []
    self._background: Set["asyncio.Task[None]"] = set()
    self.chassis: Optional[Chassis] = None
    # The pods, named as STAR names its X-arms: each fills an X-arm's slot (design.md, Feature map).
    self.left_x_arm: Optional[Pod] = None
    self.right_x_arm: Optional[Pod] = None

  # -- lifecycle -------------------------------------------------------------

  async def setup(
    self,
    skip_device_initialization: bool = False,
    *,
    skip_left_x_arm: bool = False,
    skip_right_x_arm: bool = False,
  ) -> None:
    """Connect to the instrument, find out what it is, and bring it up.

    `discover` is the read-only part; then the motor tables are loaded where a board has lost them,
    the interlock is armed and every axis enabled. Nothing moves: the drives hold where they stand.
    With every skip below set, setup only reads. Repeatable: the boards stay open and discovery
    reads them again. A setup that fails part way releases the boards.

    Args:
      skip_device_initialization: do not load motor tables, even into a board that has lost them.
        A pod with an axis not configured is then left as it is.
      skip_left_x_arm: leave the left pod as it is: not configured or enabled.
      skip_right_x_arm: leave the right pod as it is.

    Raises:
      RuntimeError: if no chassis is found, a board cannot be placed (`discover`), or the
        interlock or a drive supply does not come up.
      ValueError: for a declared configuration that is not this instrument's (`discover`).
      AceError: if an axis cannot be enabled; the interlock is then tripped and ERROR shown.
    """
    logger.warning(
      "The Biomek i7 driver's discovery, configuration and interlock have run on one instrument; "
      "they are untested on other instruments. Please report what works and what does not."
    )
    if not self._connected:
      await self._open()
    try:
      await self.discover()
      await self.initialize(
        skip_device_initialization=skip_device_initialization,
        skip_left_x_arm=skip_left_x_arm,
        skip_right_x_arm=skip_right_x_arm,
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
    place, no chassis, or a pod type other than the declared one is refused
    (`_check_declared_against`).

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
    self._watch_faults()
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
    pod's module is stopped (Stop Module Execution), then the interlock is tripped and READY shown.
    Otherwise nothing is written. What the boards do on their own when the host goes away is not
    known.
    """
    if self._armed:
      for pod in self.arms:
        try:
          await asyncio.wait_for(pod._unchecked_fw_stop_module_execution(), REST_TIMEOUT)
        except BaseException:
          logger.exception("could not stop %s", pod)
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

  @property
  def front_cover(self) -> Optional[Door]:
    """The door, by STAR's name for it, once discovery has found the chassis."""
    return None if self.chassis is None else self.chassis.front_cover

  async def request_axis_states(self) -> Dict[str, Dict[str, p.AxisStateName]]:
    """Every axis's state, by pod side then axis name. Moves nothing."""
    return {pod.side: await pod.request_axis_states() for pod in self.arms}

  # -- configuration and interlock ------------------------------------------------
  # These write to the boards. Run them only on a clear deck, with someone at the emergency stop.

  def _require_chassis(self) -> Chassis:
    if self.chassis is None:
      raise RuntimeError("no chassis; call `setup()` first")
    return self.chassis

  def skipped_sides(
    self, *, skip_left_x_arm: bool = False, skip_right_x_arm: bool = False
  ) -> Set[str]:
    """The sides (`"left"`, `"right"`) a setup with these skips leaves as they are."""
    return {s for s, skip in (("left", skip_left_x_arm), ("right", skip_right_x_arm)) if skip}

  async def initialize(
    self,
    skip_device_initialization: bool = False,
    *,
    skip_left_x_arm: bool = False,
    skip_right_x_arm: bool = False,
  ) -> None:
    """Bring the instrument up: what `setup` does after discovery.

    Reads every axis's state first. A pod with an axis not configured (the board was powered up, or
    a drive lost its parameters) has its motor tables loaded; one fully configured does not, since
    loading them again would un-home it. Then the interlock is armed and every axis enabled
    (`arm_interlock`). The arguments are those of `setup`.

    An axis in fault is not refused: enabling it is its fault reset, as when Biomek re-arms and
    enables after a fault, and calling this is the person's go-ahead that Biomek waits for. A CiA
    402 drive in fault with its motor supply off is the instrument at rest and is only noted; any
    other fault is warned about, since under power it usually means a following error or a
    collision. A fault that does not clear fails the enable, and the instrument is returned to rest.

    Raises:
      RuntimeError: if the interlock or a drive supply does not come up when armed.
      AceError: if an axis cannot be enabled; the interlock is then tripped and ERROR shown.
    """
    skipped = self.skipped_sides(skip_left_x_arm=skip_left_x_arm, skip_right_x_arm=skip_right_x_arm)
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
    if unconfigured:
      await self.configure(unconfigured)
    if pods:
      try:
        await self.arm_interlock(pods)
      except BaseException:
        await self._return_to_rest(error=True)
        raise

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
  # Biomek's whole-system pause, as its logs show it: a deliberate interlock trip with PAUSED shown;
  # resuming re-arms and enables every axis. There is no mute: the light curtain may be broken only
  # because the drives have no power.

  @property
  def paused(self) -> bool:
    """Whether the instrument is paused (`pause`), waiting for `resume`."""
    return self._paused

  async def pause(self) -> None:
    """Pause for access: cut the pods' drive power and show PAUSED, so the light curtain may be
    broken without an error. Call it between operations, as Biomek's Pause step runs between
    steps.

    Raises:
      RuntimeError: if the interlock is not armed by this driver (nothing to pause), or the
        instrument is already paused.
    """
    if self._paused:
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
    self._paused = True
    logger.info("paused: the interlock is tripped; the light curtain may be broken")

  async def resume(self) -> None:
    """Resume after `pause`: re-arm the interlock and enable every axis. Nothing is homed.

    Raises:
      RuntimeError: if not paused, or if the interlock does not arm (the light curtain still
        broken): the instrument stays paused, so clear it and call `resume` again.
    """
    if not self._paused:
      raise RuntimeError("the instrument is not paused")
    try:
      await self.arm_interlock()
    except BaseException:
      self._armed = False
      self.interlock_tripped.set()
      raise
    self._paused = False
    logger.info("resumed: the interlock is armed")

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
    self._in_background(self._trip(error=True))

  def _interlock_tripped(self, reason: str) -> None:
    """What the Master Controller does when the interlock trips under a running instrument: waits
    end, and ERROR is shown. A trip this driver made itself (at rest, or after a failure) only marks
    it tripped."""
    ours = self._tripping or not self._armed
    self.interlock_tripped.set()
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
