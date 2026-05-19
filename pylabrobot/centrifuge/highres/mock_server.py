"""In-process mock of the HighRes MicroSpin TCP/1000 command server.

This is a faithful-enough re-implementation of the MicroSpin's remote-control
protocol (manual \u00a76.6) to drive :class:`MicroSpinBackend` end-to-end without
a real device. It is intended for:

* CI / local integration tests that want to exercise the full asyncio socket
  path of the backend (not just stubs that replay canned bytes).
* Hand-driving via ``nc`` / ``telnet`` while developing or debugging.
* Reproducing tricky firmware behaviours -- e.g. the "``status`` blocks until
  the spindle has truly stopped" gate that :meth:`MicroSpinBackend.reset`
  relies on, or the low-G hang we warn about in :meth:`MicroSpinBackend.spin`.

The server is small and *not* a perfect emulator -- it implements only the
commands pylabrobot uses, plus a few handy ones for diagnostics (``status``,
``hss``, ``errors``, ``version``, ``list``).

Usage from Python::

    async with MicroSpinMockServer() as srv:
        print(f"listening on {srv.host}:{srv.port}")
        backend = MicroSpinBackend(host=srv.host, port=srv.port)
        await backend.setup()
        ...

Or as a script::

    $ python -m pylabrobot.centrifuge.highres.mock_server --port 1000
    # in another shell:
    $ nc 127.0.0.1 1000
    home
    ACK! home 1
    OK! home 1
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class _MockError(Exception):
  """Raised inside a command handler to produce an ``ERROR!`` terminator."""

  def __init__(self, error_lines: List[str]):
    self.error_lines = list(error_lines)


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
  # When True, motion handlers refuse to mark the spindle as settled.
  simulate_low_g_hang: bool = False

  def __post_init__(self):
    self.spindle_settled.set()  # idle to start

  def push_error(self, code: int, message: str) -> None:
    """Append an entry to the simulated error stack in the firmware's format."""
    ts = time.strftime("%H:%M:%S", time.gmtime())
    self.errors.append(f"Error {len(self.errors) + 1}: ({ts}) {code}: {message}")


class MicroSpinMockServer:
  """A localhost TCP server that speaks the MicroSpin remote-control protocol.

  Multiple clients can connect concurrently (the real firmware allows up to
  10); each gets its own command-id counter is *not* shared across clients,
  which matches the real device's behaviour.
  """

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
      "od": 0.02,
      "cd": 0.02,
      # `spin` computes its own dwell from parameters.
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
    """Shut down the server, cancelling any in-flight client handlers.

    Cancelling per-client tasks is necessary because a handler that is
    waiting on :attr:`MockState.spindle_settled` would otherwise block
    :meth:`asyncio.Server.wait_closed` forever -- this matters in
    particular for the low-G hang simulation.
    """
    # Cancel any in-progress motion task.
    if self.state.current_motion is not None and not self.state.current_motion.done():
      self.state.current_motion.cancel()
    # Cancel all in-flight per-client handler tasks, so handlers that are
    # blocked waiting on `spindle_settled` (or anything else) can exit and
    # let `wait_closed()` complete. Without this, an aborted-but-not-yet-
    # settled handler would deadlock `stop()`.
    for task in list(self._client_tasks):
      if not task.done():
        task.cancel()
    if self._server is not None:
      self._server.close()
      # Give cancelled handlers a chance to exit cleanly.
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

    # Stage 2: ACK
    writer.write(f"ACK! {cmd_line} {cmd_id}\r\n".encode("ascii"))
    await writer.drain()

    parts = cmd_line.split()
    head = parts[0] if parts else ""
    args = parts[1:]

    try:
      data_lines = await self._dispatch(head, args, cmd_id)
      for line in data_lines:
        writer.write((line + "\r\n").encode("ascii"))
      writer.write(f"OK! {cmd_line} {cmd_id}\r\n".encode("ascii"))
    except _MockError as exc:
      for err in exc.error_lines:
        writer.write((err + "\r\n").encode("ascii"))
        # mirror real device: errors also go on the persistent stack
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
    head: str,
    args: List[str],
    cmd_id: int,
  ) -> List[str]:
    aliases = {
      "a": "abort",
      "cba": "clearbuttonabort",
      "d": "disconnect",
      "e": "errors",
      "hi": "history",
      "hss": "homedstatus",
      "s": "status",
      "sp": "spin",
      "v": "version",
      "?": "list",
      "??": "info",
    }
    head_canon = aliases.get(head, head)

    handler = self._handlers.get(head_canon)
    if handler is None:
      raise _MockError([f"Error: Command {head!r} not recognized!"])
    return await handler(self, args, cmd_id)

  # ---------- helpers shared by handlers --------------------------------

  async def _wait_for_spindle_stopped(self) -> None:
    """Block until the spindle-stopped sensor reports settled.

    Reproduces the firmware behaviour where ``status`` (and a handful of
    other commands) queue behind active motion. While
    :attr:`MockState.simulate_low_g_hang` is True, the settle event is
    never set after motion, so this method hangs indefinitely -- the
    real-world failure mode we warn about in
    :meth:`MicroSpinBackend.spin`.
    """
    await self.state.spindle_settled.wait()

  def _begin_motion(self, coro_factory):
    """Start a motion coroutine, clearing/setting the spindle-settled flag.

    `coro_factory` is a no-arg callable returning the awaitable that does
    the actual simulated motion (sleep + state mutations).
    """
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
      raise _MockError(["Error: aborted state latched; call clearbuttonabort"])

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
    return [
      "clearbuttonabort, cba           - Clear abort state.",
      "disconnect, d                   - Close the current client's connection.",
      "errors, e                       - Display the error stack.",
      "help                            - Show parameter info for a command.",
      "info, ??                        - List commands with parameter info.",
      "list, ?                         - List available commands.",
      "version, v                      - Software version report.",
      "whoami                          - Return client number.",
      "abort, a                        - Stop current operation.",
      "home                            - Home the system.",
      "homedstatus, hss                - Whether the device is homed.",
      "open                            - Open the door and present a bucket.",
      "spin, sp                        - Spin the centrifuge.",
      "status, s                       - Return the device status report.",
    ]

  async def _h_info(self, args, cmd_id):
    return await self._h_list(args, cmd_id)  # same surface in the mock

  async def _h_whoami(self, args, cmd_id):
    return [str(cmd_id)]

  async def _h_disconnect(self, args, cmd_id):
    # The real device closes the connection after ACK; we leave that to the
    # caller's stream handling. Just return no data.
    return []

  async def _h_help(self, args, cmd_id):
    if not args:
      raise _MockError(
        ['Error: Abnormal number of parameters (0) for command "help".  Min: 1, Max: 1']
      )
    return [f"{args[0]} -- (mock) no detailed help available"]

  async def _h_errors(self, args, cmd_id):
    n = int(args[0]) if args else 10
    return list(self.state.errors[-n:])

  async def _h_homedstatus(self, args, cmd_id):
    # `hss` does NOT wait for motion to finish in the real device, so we
    # don't either.
    return ["homed" if self.state.homed else "not homed"]

  async def _h_status(self, args, cmd_id):
    # Crucial: status blocks behind active motion. This is the gate that
    # MicroSpinBackend.reset() / wait_for_spindle_stopped() depend on.
    await self._wait_for_spindle_stopped()
    return [
      f"Spindle Position: {self.state.spindle_position}",
      f"Door Position: {self.state.door_position}",
    ]

  async def _h_abort(self, args, cmd_id):
    motion = self.state.current_motion
    if motion is not None and not motion.done():
      motion.cancel()
    self.state.abort_latched = True
    self.state.spinning = False
    return []

  async def _h_clearbuttonabort(self, args, cmd_id):
    self.state.abort_latched = False
    return []

  async def _h_home(self, args, cmd_id):
    self._require_not_aborted()

    async def do_home():
      await asyncio.sleep(self.motion_dwell["home"])
      self.state.spindle_position = 1958  # arbitrary "homed" rest position
      self.state.door_position = -258
      self.state.homed = True
      self.state.at_bucket = None

    task = self._begin_motion(do_home)
    try:
      await task
    except asyncio.CancelledError:
      raise _MockError(["Error: home cancelled by abort"])
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
      raise _MockError(["Error: open cancelled by abort"])
    return []

  async def _h_od(self, args, cmd_id):
    self._require_not_aborted()

    async def do_od():
      await asyncio.sleep(self.motion_dwell["od"])
      self.state.door_position = 19242

    task = self._begin_motion(do_od)
    try:
      await task
    except asyncio.CancelledError:
      raise _MockError(["Error: od cancelled by abort"])
    return []

  async def _h_cd(self, args, cmd_id):
    self._require_not_aborted()

    async def do_cd():
      await asyncio.sleep(self.motion_dwell["cd"])
      self.state.door_position = -258
      self.state.at_bucket = None

    task = self._begin_motion(do_cd)
    try:
      await task
    except asyncio.CancelledError:
      raise _MockError(["Error: cd cancelled by abort"])
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
    if self.state.door_position > 0:  # door not closed
      raise _MockError(["Error: door must be closed before spin"])

    # Compute a simulated dwell that scales with duration but is short by
    # default so tests don't sleep for a minute. Tests can override.
    dwell = self.motion_dwell.get("spin_seconds_per_real_second", 0.005) * duration

    async def do_spin():
      self.state.spinning = True
      try:
        await asyncio.sleep(dwell)
      finally:
        self.state.spinning = False
        self.state.spindle_position = (self.state.spindle_position + g) % 8192

    task = self._begin_motion(do_spin)
    try:
      await task
    except asyncio.CancelledError:
      raise _MockError(["Error: spin cancelled by abort"])
    return []

  _handlers: Dict[str, Callable[["MicroSpinMockServer", List[str], int], Awaitable[List[str]]]]


# Wire up the handler table (after class body so methods are bound names).
MicroSpinMockServer._handlers = {
  "abort": MicroSpinMockServer._h_abort,
  "cd": MicroSpinMockServer._h_cd,
  "clearbuttonabort": MicroSpinMockServer._h_clearbuttonabort,
  "disconnect": MicroSpinMockServer._h_disconnect,
  "errors": MicroSpinMockServer._h_errors,
  "help": MicroSpinMockServer._h_help,
  "home": MicroSpinMockServer._h_home,
  "homedstatus": MicroSpinMockServer._h_homedstatus,
  "info": MicroSpinMockServer._h_info,
  "list": MicroSpinMockServer._h_list,
  "od": MicroSpinMockServer._h_od,
  "open": MicroSpinMockServer._h_open,
  "spin": MicroSpinMockServer._h_spin,
  "status": MicroSpinMockServer._h_status,
  "version": MicroSpinMockServer._h_version,
  "whoami": MicroSpinMockServer._h_whoami,
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
