"""In-process mock of the HighRes MicroSpin TCP/1000 command server.

This is a faithful-enough re-implementation of the MicroSpin's remote-control
protocol (manual \u00a76.6) to drive :class:`MicroSpinBackend` end-to-end without
a real device. It is intended for:

* CI / local integration tests that want to exercise the full asyncio socket
  path of the backend (not just stubs that replay canned bytes).
* Hand-driving via ``nc`` / ``telnet`` while developing or debugging.
* Reproducing tricky firmware behaviours -- e.g. the "``status`` blocks until
  the spindle has truly stopped" gate, the ``ABORTED!`` terminator the
  device emits for commands cancelled by an ``abort``, and the low-G
  spin-down-detection hang.

The mock implements **only** the commands the real device's ``list``
enumerates (manual \u00a76.7's public set):

    clearbuttonabort, cba
    commandstat,      cstat
    disconnect,       d
    errors,           e
    help
    info,             ??
    list,             ?
    logcommands
    version,          v
    whoami
    abort,            a
    home
    homedstatus,      hss
    open
    spin,             sp
    status,           s

Maintenance commands (``od``, ``cd``, ``lockdoor``, ``unlockdoor``,
``locknest``, ``unlocknest``, ``r``, ``copleyget``, ``copleyset``, ``ddio``,
etc.) are deliberately not modelled. Sending one to the mock will produce
the same ``Command "<name>" not recognized!`` response the real device gives
to unknown commands.

Response text -- including the layout of ``list``/``info``/``help``, the
"Issue the 'clearbuttonabort' (cba) command to re-enable the machine" line
emitted by ``abort``, and the use of the ``ABORTED!`` terminator -- was
captured verbatim from real-device netcat sessions.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Internal handler-control exceptions
# ============================================================================


class _MockError(Exception):
  """Raised inside a command handler to produce an ``ERROR!`` terminator.

  ``error_lines`` are written as data lines before the terminator, just
  like the firmware emits ``Error N: ...`` entries before ``ERROR!``.
  """

  def __init__(self, error_lines: List[str]):
    self.error_lines = list(error_lines)


class _MockAborted(Exception):
  """Raised inside a command handler to produce an ``ABORTED!`` terminator.

  The real device emits ``ABORTED!`` (not ``ERROR!``) when a motion command
  is cancelled by an ``abort``, or when a motion command is issued while the
  abort latch is set (i.e. before ``clearbuttonabort``).
  """


# ============================================================================
# Mutable mock device state
# ============================================================================


@dataclass
class MockState:
  """In-memory model of the MicroSpin's relevant firmware state."""

  homed: bool = False
  spindle_position: int = 0
  door_position: int = -258  # CENTRIFUGE_DOOR_CLOSED from real settings dump
  at_bucket: Optional[int] = None  # 1, 2, or None
  abort_latched: bool = False
  spinning: bool = False  # True while a spin task is active
  current_motion: Optional[asyncio.Task] = None
  errors: List[str] = field(default_factory=list)
  next_command_id: int = 1
  # The mock's analogue of the real device's spindle-stopped sensor. False
  # while motion is in progress; set back to True when motion settles --
  # *unless* `simulate_low_g_hang` is True, in which case the sensor stays
  # False and any subsequent `status` waits forever, reproducing the
  # firmware bug we warn about in :meth:`MicroSpinBackend.spin`.
  spindle_settled: asyncio.Event = field(default_factory=asyncio.Event)
  #: When True, motion handlers refuse to mark the spindle as settled.
  simulate_low_g_hang: bool = False

  def __post_init__(self):
    self.spindle_settled.set()  # idle to start

  def push_error(self, code: int, message: str) -> None:
    """Append an entry to the simulated error stack in the firmware's format."""
    ts = time.strftime("%H:%M:%S", time.gmtime())
    self.errors.append(f"Error {len(self.errors) + 1}: ({ts}) {code}: {message}")


# ============================================================================
# Command specification table -- single source of truth for list/info/help
# ============================================================================


@dataclass(frozen=True)
class _CommandSpec:
  """Static metadata about one wire command.

  ``description`` is the single-line summary shown by ``list``.
  ``params_signature`` is the parameter-line ``info``/``help`` print
  immediately under the description (e.g. ``"(No parameters)"`` or
  ``"Parameters(1): <bucket>"``).
  ``params_description`` are additional indented lines printed under the
  signature in ``info``/``help`` (often a description of each parameter).
  """

  name: str
  aliases: tuple  # tuple of alias strings (may be empty)
  description: str
  params_signature: str
  params_description: tuple  # tuple of extra info lines

  @property
  def display_name(self) -> str:
    """e.g. ``"homedstatus, hss"`` or ``"home"`` (used by list/info)."""
    if not self.aliases:
      return self.name
    return f"{self.name}, {', '.join(self.aliases)}"


# Width of the name+alias column in `list`/`info` output (real device uses 32).
_NAME_COLUMN_WIDTH = 32


# This table drives the responses to `list`, `info`, and `help <cmd>`. The
# command order matches the real device's output: client/server-side commands
# first, then machine-side commands.
#
# IMPORTANT: any change here is visible on the wire and affects every test
# that scrapes list/info output.
_COMMAND_TABLE: List[_CommandSpec] = [
  _CommandSpec(
    name="clearbuttonabort",
    aliases=("cba",),
    description="Clear abort state.",
    params_signature="(No parameters)",
    params_description=(),
  ),
  _CommandSpec(
    name="commandstat",
    aliases=("cstat",),
    description="Gets status of a command.",
    params_signature="Parameters(1): <command id>",
    params_description=("",),  # real device emits a trailing blank info line
  ),
  _CommandSpec(
    name="disconnect",
    aliases=("d",),
    description="Close the current client's connection.",
    params_signature="(No parameters)",
    params_description=(),
  ),
  _CommandSpec(
    name="errors",
    aliases=("e",),
    description="Display the top 10 errors on the error stack.",
    params_signature="Parameters(0 - 1): [<n>]",
    params_description=("Optional parameter <n> specifies the max number of errors to display.",),
  ),
  _CommandSpec(
    name="help",
    aliases=(),
    description="Displays the parameter information for a specific command.",
    params_signature="Parameters(1): <command>",
    params_description=("Where <command> is the name of the command to view information about.",),
  ),
  _CommandSpec(
    name="info",
    aliases=("??",),
    description="Displays the list of user commands with parameter information.",
    params_signature="Parameters(0 - 1): [all]",
    params_description=("If 'all' is specified, maintenance commands will be included.",),
  ),
  _CommandSpec(
    name="list",
    aliases=("?",),
    description="Displays the list of user commands that the server recognizes.",
    params_signature="Parameters(0 - 1): [all]",
    params_description=("If 'all' is specified, maintenance commands will be included.",),
  ),
  _CommandSpec(
    name="logcommands",
    aliases=(),
    description="Log all received commands to a file.",
    params_signature="Parameters(1): yes|no",
    params_description=("Yes will enable logging.  No will disable logging.",),
  ),
  _CommandSpec(
    name="version",
    aliases=("v",),
    description="Return the software version report.",
    params_signature="(No parameters)",
    params_description=(),
  ),
  _CommandSpec(
    name="whoami",
    aliases=(),
    description="Get the current client's client number.",
    params_signature="(No parameters)",
    params_description=(),
  ),
  _CommandSpec(
    name="abort",
    aliases=("a",),
    description="Stop current machine operation.",
    params_signature="(No parameters)",
    params_description=(),
  ),
  _CommandSpec(
    name="home",
    aliases=(),
    description="Homes the system",
    params_signature="(No parameters)",
    params_description=(),
  ),
  _CommandSpec(
    name="homedstatus",
    aliases=("hss",),
    description="returns whether the device is in a homed state",
    params_signature="(No parameters)",
    params_description=(),
  ),
  _CommandSpec(
    name="open",
    aliases=(),
    description="Open the door and present the specified bucket.",
    params_signature="Parameters(1): <bucket>",
    params_description=("<bucket> the bucket number to present",),
  ),
  _CommandSpec(
    name="spin",
    aliases=("sp",),
    description="Spin the centrifuge.",
    params_signature="Parameters(4): <G-force> <acceleration> <deceleration> <time>",
    params_description=(
      "<G-force> is the force to spin at.",
      "<acceleration> rate of acceleration 0-100%",
      "<deceleration> rate of deceleration 0-100%",
    ),
  ),
  _CommandSpec(
    name="status",
    aliases=("s",),
    description="Returns the device status report.",
    params_signature="(No parameters)",
    params_description=(),
  ),
]


def _list_lines() -> List[str]:
  """The data lines emitted by ``list`` (one per command, no info)."""
  return [
    f"{spec.display_name:<{_NAME_COLUMN_WIDTH}}- {spec.description}" for spec in _COMMAND_TABLE
  ]


def _info_lines() -> List[str]:
  """The data lines emitted by ``info`` (one block per command)."""
  out: List[str] = []
  for spec in _COMMAND_TABLE:
    out.append(f"{spec.display_name:<{_NAME_COLUMN_WIDTH}}- {spec.description}")
    out.append(f"     {spec.params_signature}")
    for extra in spec.params_description:
      out.append(f"     {extra}")
    out.append("")  # blank line between command blocks
  return out


def _help_lines(spec: _CommandSpec) -> List[str]:
  """The data lines emitted by ``help <cmd>`` for one specific command."""
  out = [f"{spec.display_name} - {spec.description}"]
  out.append(f"     {spec.params_signature}")
  for extra in spec.params_description:
    out.append(f"     {extra}")
  out.append("")  # trailing blank line, matching real device
  return out


# Quick lookup: alias -> canonical command name. Built once from the table.
_ALIAS_MAP: Dict[str, str] = {alias: spec.name for spec in _COMMAND_TABLE for alias in spec.aliases}


# ============================================================================
# The mock server itself
# ============================================================================


class MicroSpinMockServer:
  """A localhost TCP server that speaks the MicroSpin remote-control protocol."""

  def __init__(
    self,
    host: str = "127.0.0.1",
    port: int = 0,
    state: Optional[MockState] = None,
  ) -> None:
    self.host = host
    self.port = port  # 0 = pick a free port; actual port set in start()
    self.state = state or MockState()
    self._server: Optional[asyncio.AbstractServer] = None
    self._client_tasks: "set[asyncio.Task]" = set()
    # Maps motion commands to their simulated dwell time. Tests can override
    # this map to make every motion instantaneous, or use the real ramps.
    self.motion_dwell: Dict[str, float] = {
      "home": 0.05,
      "open": 0.05,
      "close_door_during_spin": 0.02,
      # `spin` computes its own dwell from parameters; see _h_spin.
    }

  # ---- lifecycle --------------------------------------------------------

  async def start(self) -> "MicroSpinMockServer":
    """Bind the listening socket and begin serving clients.

    If ``self.port`` was ``0`` (the default), the OS picks a free port and
    ``self.port`` is updated in-place so callers can read it back.
    """
    self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
    sock = self._server.sockets[0]
    self.host, self.port = sock.getsockname()[:2]
    logger.debug("[mock] listening on %s:%d", self.host, self.port)
    return self

  async def stop(self) -> None:
    """Shut down the server, cancelling any in-flight client handlers."""
    if self.state.current_motion is not None and not self.state.current_motion.done():
      self.state.current_motion.cancel()
    for task in list(self._client_tasks):
      if not task.done():
        task.cancel()
    if self._server is not None:
      self._server.close()
      if self._client_tasks:
        await asyncio.gather(*self._client_tasks, return_exceptions=True)
      await self._server.wait_closed()
      self._server = None
    self._client_tasks.clear()

  async def __aenter__(self) -> "MicroSpinMockServer":
    return await self.start()

  async def __aexit__(self, *exc) -> None:
    await self.stop()

  # ---- per-client loop --------------------------------------------------

  async def _handle_client(
    self,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
  ) -> None:
    addr = writer.get_extra_info("peername")
    logger.debug("[mock] client connected: %s", addr)
    task = asyncio.current_task()
    if task is not None:
      self._client_tasks.add(task)
    try:
      while True:
        raw = await reader.readline()
        if not raw:
          break
        cmd_line = raw.rstrip(b"\r\n").decode("ascii", errors="replace").strip()
        if not cmd_line:
          continue
        await self._serve_command(cmd_line, writer)
    except (ConnectionError, asyncio.CancelledError):
      pass
    finally:
      try:
        writer.close()
        await writer.wait_closed()
      except Exception:
        pass
      if task is not None:
        self._client_tasks.discard(task)
      logger.debug("[mock] client disconnected: %s", addr)

  async def _serve_command(self, cmd_line: str, writer: asyncio.StreamWriter) -> None:
    cmd_id = self.state.next_command_id
    self.state.next_command_id += 1

    # Stage 2: ACK -- the echo is exactly the bytes the client sent (the
    # real device does the same; it doesn't normalise aliases here).
    writer.write(f"ACK! {cmd_line} {cmd_id}\r\n".encode("ascii"))
    await writer.drain()

    parts = cmd_line.split()
    head = parts[0] if parts else ""
    args = parts[1:]
    # Resolve aliases to the canonical command name for dispatch.
    canonical = _ALIAS_MAP.get(head, head)

    try:
      data_lines = await self._dispatch(canonical, args, cmd_id)
      for line in data_lines:
        writer.write((line + "\r\n").encode("ascii"))
      writer.write(f"OK! {cmd_line} {cmd_id}\r\n".encode("ascii"))
    except _MockAborted:
      writer.write(f"ABORTED! {cmd_line} {cmd_id}\r\n".encode("ascii"))
    except _MockError as exc:
      for err in exc.error_lines:
        writer.write((err + "\r\n").encode("ascii"))
        self.state.errors.append(err)
      writer.write(f"ERROR! {cmd_line} {cmd_id}\r\n".encode("ascii"))
    except asyncio.CancelledError:
      raise
    except Exception as exc:  # noqa: BLE001
      logger.exception("[mock] handler crashed for %r", cmd_line)
      writer.write(f"Error: internal mock crash: {exc}\r\n".encode("ascii"))
      writer.write(f"ERROR! {cmd_line} {cmd_id}\r\n".encode("ascii"))
    await writer.drain()

  # ---- command dispatch -------------------------------------------------

  async def _dispatch(
    self,
    canonical: str,
    args: List[str],
    cmd_id: int,
  ) -> List[str]:
    handler = self._handlers.get(canonical)
    if handler is None:
      raise _MockError([f'Error: Command "{canonical}" not recognized!'])
    return await handler(self, args, cmd_id)

  # ---------- helpers shared by handlers --------------------------------

  def _begin_motion(self, coro_factory):
    """Start a motion coroutine, clearing/setting the spindle-settled flag."""
    self.state.spindle_settled.clear()

    async def runner():
      try:
        await coro_factory()
      finally:
        if not self.state.simulate_low_g_hang:
          self.state.spindle_settled.set()

    self.state.current_motion = asyncio.create_task(runner())
    return self.state.current_motion

  def _require_homed(self) -> None:
    if not self.state.homed:
      raise _MockError(["Error: device is not homed"])

  def _require_not_aborted(self) -> None:
    if self.state.abort_latched:
      # Real-device behaviour: motion commands issued while the abort latch
      # is set return ACK! -> ABORTED!, with no Error data lines.
      raise _MockAborted()

  # ---------- individual command handlers -------------------------------

  async def _h_version(self, args, cmd_id):
    return [
      "Product Name: RandomServe",
      "Serial Number: HRB-MOCK-0000001",
      "libcommon Revision:    4289",
      "libsettings Revision:  2830",
      "libsqlite Revision:    3973",
      "libts7500dio Revision: 3973",
      "Firmware Revision:     4290",
      "Version:               MS-1.3.3-mock",
      "Firmware Build: MOCK1234",
    ]

  async def _h_list(self, args, cmd_id):
    return _list_lines()

  async def _h_info(self, args, cmd_id):
    return _info_lines()

  async def _h_whoami(self, args, cmd_id):
    return [str(cmd_id)]

  async def _h_disconnect(self, args, cmd_id):
    # The real device closes the connection after the ACK/OK. We leave the
    # actual close to the per-client read loop (it sees EOF on the next
    # readline if the client disconnects); here we just emit no data.
    return []

  async def _h_help(self, args, cmd_id):
    if len(args) != 1:
      raise _MockError(
        [f'Error: Abnormal number of parameters ({len(args)}) for command "help".  Min: 1, Max: 1']
      )
    target = args[0]
    canonical = _ALIAS_MAP.get(target, target)
    for spec in _COMMAND_TABLE:
      if spec.name == canonical:
        return _help_lines(spec)
    raise _MockError([f"Error: Can't provide information on unrecognized command '{target}'"])

  async def _h_errors(self, args, cmd_id):
    n = int(args[0]) if args else 10
    return list(self.state.errors[-n:])

  async def _h_homedstatus(self, args, cmd_id):
    # `hss` does NOT wait for motion to finish on the real device.
    return ["homed" if self.state.homed else "not homed"]

  async def _h_status(self, args, cmd_id):
    # Crucial firmware behaviour: `status` blocks behind active motion --
    # the device only answers once the spindle-stopped sensor latches.
    # While `state.simulate_low_g_hang` is True, the settle event is never
    # set after motion, so this `wait()` hangs forever -- reproducing the
    # real-world firmware quirk we warn about in `MicroSpinBackend.spin`.
    await self.state.spindle_settled.wait()
    return [
      f"Spindle Position: {self.state.spindle_position}",
      f"Door Position: {self.state.door_position}",
    ]

  async def _h_abort(self, args, cmd_id):
    motion = self.state.current_motion
    if motion is not None and not motion.done():
      motion.cancel()
      # Wait for the cancelled motion handler to finish writing its
      # ABORTED! terminator before we send our own OK! and the
      # "Issue the cba ..." data line.
      try:
        await motion
      except (asyncio.CancelledError, _MockAborted, _MockError):
        pass
    self.state.abort_latched = True
    self.state.spinning = False
    # The real device prints this between abort's ACK and OK as a data line.
    return ['Issue the "clearbuttonabort" (cba) command to re-enable the machine']

  async def _h_clearbuttonabort(self, args, cmd_id):
    self.state.abort_latched = False
    return []

  async def _h_logcommands(self, args, cmd_id):
    if len(args) != 1 or args[0].lower() not in ("yes", "no"):
      raise _MockError(
        [
          f"Error: Abnormal number of parameters ({len(args)}) for command "
          f'"logcommands".  Min: 1, Max: 1'
        ]
      )
    # The mock doesn't actually log; just accept the toggle.
    return []

  async def _h_commandstat(self, args, cmd_id):
    if len(args) != 1:
      raise _MockError(
        [f'Error: Abnormal number of parameters ({len(args)}) for command "cstat".  Min: 1, Max: 1']
      )
    try:
      target_id = int(args[0])
    except ValueError:
      target_id = 0
    # The mock doesn't track command history, so every lookup misses.
    raise _MockError(
      [
        f"Error {len(self.state.errors) + 1}: ({time.strftime('%H:%M:%S', time.gmtime())}) "
        f"{cmd_id}: Command id {target_id} not in recorded history."
      ]
    )

  async def _h_home(self, args, cmd_id):
    self._require_not_aborted()

    async def do_home():
      await asyncio.sleep(self.motion_dwell["home"])
      self.state.spindle_position = 1958
      self.state.door_position = -258
      self.state.homed = True
      self.state.at_bucket = None

    task = self._begin_motion(do_home)
    try:
      await task
    except asyncio.CancelledError:
      # Cancelled by abort -> ABORTED! terminator
      raise _MockAborted()
    return []

  async def _h_open(self, args, cmd_id):
    self._require_not_aborted()
    if not args:
      raise _MockError(
        ['Error: Abnormal number of parameters (0) for command "open".  Min: 1, Max: 1']
      )
    try:
      bucket = int(args[0])
    except ValueError:
      raise _MockError([f"Error: bad bucket: {args[0]!r}"])
    if bucket not in (1, 2):
      raise _MockError([f"Error: bucket must be 1 or 2, got {bucket}"])
    self._require_homed()

    async def do_open():
      await asyncio.sleep(self.motion_dwell["open"])
      self.state.at_bucket = bucket
      self.state.door_position = 19242  # CENTRIFUGE_DOOR_OPEN-ish

    task = self._begin_motion(do_open)
    try:
      await task
    except asyncio.CancelledError:
      raise _MockAborted()
    return []

  async def _h_spin(self, args, cmd_id):
    if len(args) != 4:
      raise _MockError(
        [f'Error: Abnormal number of parameters ({len(args)}) for command "spin".  Min: 4, Max: 4']
      )
    try:
      g, accel, decel, duration = (int(x) for x in args)
    except ValueError:
      raise _MockError([f"Error: spin params must be integers, got {args}"])
    if not (1 <= g <= 3000):
      raise _MockError([f"Error: g out of range: {g}"])
    if duration < 1:
      raise _MockError([f"Error: duration too short: {duration}"])
    self._require_homed()
    self._require_not_aborted()

    door_was_open = self.state.door_position > 0
    dwell = self.motion_dwell.get("spin_seconds_per_real_second", 0.005) * duration

    async def do_spin():
      self.state.spinning = True
      try:
        if door_was_open:
          await asyncio.sleep(self.motion_dwell["close_door_during_spin"])
          self.state.door_position = -258
          self.state.at_bucket = None
        await asyncio.sleep(dwell)
      finally:
        self.state.spinning = False
        self.state.spindle_position = (self.state.spindle_position + g) % 8192

    task = self._begin_motion(do_spin)
    try:
      await task
    except asyncio.CancelledError:
      raise _MockAborted()
    return []

  _handlers: Dict[str, Callable[["MicroSpinMockServer", List[str], int], Awaitable[List[str]]]]


# Wire up the handler table from the command-spec table. We intentionally
# include EXACTLY the commands `_COMMAND_TABLE` lists -- no more, no less --
# so that the mock's command surface matches what the real device's `list`
# enumerates. Maintenance commands (od/cd/lockdoor/locknest/etc.) are not
# modelled and will produce the same "command not recognized" error the
# real device gives for unknown commands.
MicroSpinMockServer._handlers = {
  "clearbuttonabort": MicroSpinMockServer._h_clearbuttonabort,
  "commandstat": MicroSpinMockServer._h_commandstat,
  "disconnect": MicroSpinMockServer._h_disconnect,
  "errors": MicroSpinMockServer._h_errors,
  "help": MicroSpinMockServer._h_help,
  "info": MicroSpinMockServer._h_info,
  "list": MicroSpinMockServer._h_list,
  "logcommands": MicroSpinMockServer._h_logcommands,
  "version": MicroSpinMockServer._h_version,
  "whoami": MicroSpinMockServer._h_whoami,
  "abort": MicroSpinMockServer._h_abort,
  "home": MicroSpinMockServer._h_home,
  "homedstatus": MicroSpinMockServer._h_homedstatus,
  "open": MicroSpinMockServer._h_open,
  "spin": MicroSpinMockServer._h_spin,
  "status": MicroSpinMockServer._h_status,
}


# --- CLI entry point ------------------------------------------------------


async def _run_forever(host: str, port: int) -> None:
  async with MicroSpinMockServer(host=host, port=port) as srv:
    print(f"MicroSpin mock listening on {srv.host}:{srv.port} (Ctrl-C to stop)")
    await asyncio.Event().wait()


def main() -> None:
  """CLI entry point: parse args and run the mock server until interrupted."""
  parser = argparse.ArgumentParser(description="Run the MicroSpin mock server.")
  parser.add_argument("--host", default="127.0.0.1")
  parser.add_argument("--port", type=int, default=1000)
  parser.add_argument("--verbose", "-v", action="store_true")
  ns = parser.parse_args()
  logging.basicConfig(level=logging.DEBUG if ns.verbose else logging.INFO)
  try:
    asyncio.run(_run_forever(ns.host, ns.port))
  except KeyboardInterrupt:
    pass


if __name__ == "__main__":
  main()
