"""BiomekI7Driver: finds the instrument's ACE boards, works out what each one is, and owns them.

A Biomek i7 shows up as one USB device per ACE module: the chassis, and one per arm. Which is which
is not fixed by USB order or serial number, so setup opens every ACE device, asks each its module
ID (its type, and an arm's side), and builds the chassis and a pod per arm from the answers. Only one
program can hold an ACE board at a time: Biomek's Master Controller must not be running.
"""

import logging
from typing import Dict, List, Optional, Sequence, Tuple, Union

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
from pylabrobot.beckman_coulter.biomek_i7.driver.features.chassis import Chassis
from pylabrobot.beckman_coulter.biomek_i7.driver.features.pod import Pod
from pylabrobot.io.usb import USB

logger = logging.getLogger(__name__)


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
    self.chassis: Optional[Chassis] = None
    # The pods, named as STAR names its X-arms: each fills an X-arm's slot (design.md, Feature map).
    self.left_x_arm: Optional[Pod] = None
    self.right_x_arm: Optional[Pod] = None

  # -- lifecycle -------------------------------------------------------------

  async def setup(self) -> None:
    """Connect to the instrument and find out what it is. Reads only: nothing is written to a
    board and nothing moves. Repeatable: the boards stay open, and discovery reads them again. A
    setup that fails part way releases the boards.

    Raises:
      RuntimeError: if no chassis is found, or a board cannot be placed (`discover`).
      ValueError: for a declared configuration that is not this instrument's (`discover`).
    """
    logger.warning(
      "The Biomek i7 driver's discovery has run on one instrument; it is untested on other "
      "instruments. Please report what works and what does not."
    )
    if not self._connected:
      await self._open()
    try:
      await self.discover()
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
    """Release every board. Writes nothing and moves nothing."""
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
