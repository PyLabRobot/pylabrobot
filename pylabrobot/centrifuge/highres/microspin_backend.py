"""Backend for the HighRes Biosolutions MicroSpin automated microplate centrifuge.

The MicroSpin exposes an ASCII command/response protocol over TCP/IP on port
1000 by default. The wire protocol is documented in HighRes Biosolutions
"MicroSpin User Manual" (document 1058675, §6.6). In summary, every command
exchange has the following shape::

    >>> <command> [args...]\\r\\n
    <<< ACK!   <echoed command> <command-id>
    <<< (optional data lines, depending on the command)
    <<< OK!    <command> <command-id>          -- success terminator
        ERROR! <command> <command-id>          -- failure terminator
        Error <n>: <text>                      -- (one or more lines after ERROR!)

This module also exposes a small surface of MicroSpin-specific helpers
(``home``, ``abort``, ``get_status`` ...) in addition to the abstract
:class:`~pylabrobot.centrifuge.backend.CentrifugeBackend` interface.

Safety
------
The MicroSpin is a real piece of mechanical equipment that can develop
3000 ``×g`` (4729 RPM) and weighs 43 kg. Before issuing a ``spin`` command:

* the device MUST be homed (use :meth:`home` if :meth:`is_homed` returns False)
* the shipping tie-wraps holding the payload buckets MUST be removed
* both buckets must be properly seated on the rotor pins and balanced
* the door must be closed (the firmware will refuse to spin otherwise)
* nothing flammable, corrosive, or biohazardous may be in the chamber

Refer to the MicroSpin user manual (HighRes document 1058675) §7 for the
full pre-spin checklist, and to §11 for environmental specifications.
"""

from __future__ import annotations

import asyncio
import logging
import re
import warnings
from typing import Dict, List, Optional

from ..backend import CentrifugeBackend

logger = logging.getLogger(__name__)


_ACK_RE = re.compile(r"^ACK!\s+(?P<echo>.*?)\s+(?P<id>\d+)\s*$")


class MicroSpinError(RuntimeError):
  """Raised when the MicroSpin responds with ``ERROR!`` to a command.

  Attributes:
    command: The command text that was sent.
    command_id: The numeric command id assigned by the device, or -1 if the
      ACK could not be parsed.
    error_lines: The diagnostic lines (``Error <n>: ...``) the device emitted
      before the ``ERROR!`` terminator.
  """

  def __init__(self, command: str, command_id: int, error_lines: List[str]):
    self.command = command
    self.command_id = command_id
    self.error_lines = list(error_lines)
    super().__init__(
      f"MicroSpin returned ERROR! for {command!r} (id={command_id}):\n  "
      + ("\n  ".join(error_lines) if error_lines else "(no error detail)")
    )


class MicroSpinProtocolError(RuntimeError):
  """Raised when the MicroSpin emits a response we cannot parse."""


class MicroSpinBackend(CentrifugeBackend):
  """Asynchronous backend for the HighRes Biosolutions MicroSpin centrifuge.

  Communicates over a single persistent TCP connection. Commands are serialised
  with an :class:`asyncio.Lock` so the front-end can safely interleave callers.
  """

  #: Factory-default TCP port for the MicroSpin's remote-control server
  #: (manual §6.3). The port is configurable per-device on the
  #: ``/network.html`` web page (the ``SERVER_PORT`` setting); if your unit
  #: has been reconfigured, pass the right port to the constructor or to
  #: :func:`~pylabrobot.centrifuge.highres.microspin.MicroSpin`.
  DEFAULT_PORT: int = 1000
  #: Spec sheet maximum (manual §11). The firmware will reject larger values.
  MAX_G_FORCE = 3000
  #: Empirically observed minimum below which the firmware sometimes fails to
  #: detect spin-down. Spinning below this triggers a :class:`UserWarning`;
  #: see :meth:`spin` for the failure mode.
  LOW_G_WARNING_THRESHOLD = 30

  def __init__(
    self,
    host: str,
    port: int = DEFAULT_PORT,
    timeout: float = 30.0,
  ):
    """
    Args:
      host: IP address or DNS name of the MicroSpin's Ethernet interface.
      port: TCP port for the remote-control server. Defaults to
        :attr:`DEFAULT_PORT` (1000, the factory default). Override this if
        your device has been reconfigured via its ``/network.html`` web UI
        to listen on a different port.
      timeout: Default per-command timeout in seconds. Long-running commands
        (``spin``, ``home``) automatically extend this internally.
    """
    self.host = host
    self.port = port
    self.timeout = timeout

    self._reader: Optional[asyncio.StreamReader] = None
    self._writer: Optional[asyncio.StreamWriter] = None
    self._lock = asyncio.Lock()

  # ------------------------------------------------------------------ lifecycle

  async def setup(self) -> None:
    """Open the TCP connection to the MicroSpin's remote-control server."""
    logger.debug("[microspin] connecting to %s:%d", self.host, self.port)
    self._reader, self._writer = await asyncio.open_connection(self.host, self.port)

  async def stop(self) -> None:
    """Close the TCP connection. Safe to call even if never set up."""
    if self._writer is not None:
      try:
        self._writer.close()
        await self._writer.wait_closed()
      except Exception:  # noqa: BLE001 -- best-effort close
        logger.debug("[microspin] error while closing connection", exc_info=True)
    self._reader = None
    self._writer = None

  def serialize(self) -> dict:
    """Return a JSON-serialisable view of this backend's construction args."""
    return {
      **super().serialize(),
      "host": self.host,
      "port": self.port,
      "timeout": self.timeout,
    }

  # ------------------------------------------------------------------ wire IO

  async def _readline(self) -> str:
    assert self._reader is not None, "not connected"
    raw = await self._reader.readline()
    if not raw:
      raise ConnectionError("MicroSpin closed the connection")
    return raw.rstrip(b"\r\n").decode("ascii", errors="replace")

  async def send_command(
    self,
    command: str,
    *,
    timeout: Optional[float] = None,
  ) -> List[str]:
    """Send a single command and return any data lines emitted by the device.

    Args:
      command: The full command line *without* CR/LF.
      timeout: Override the default per-command timeout (seconds).

    Returns:
      A list of the data lines emitted between ``ACK!`` and ``OK!``. The list
      is empty for commands that report only status (e.g. ``home``).

    Raises:
      MicroSpinError: If the device terminates with ``ERROR!``.
      MicroSpinProtocolError: If the ACK or terminator cannot be parsed.
      asyncio.TimeoutError: If the timeout elapses.
    """
    if self._writer is None or self._reader is None:
      raise RuntimeError("MicroSpinBackend is not set up. Call `await backend.setup()` first.")

    effective_timeout = self.timeout if timeout is None else timeout

    async with self._lock:
      return await asyncio.wait_for(
        self._send_command_no_lock(command),
        timeout=effective_timeout,
      )

  async def _send_command_no_lock(self, command: str) -> List[str]:
    assert self._writer is not None
    logger.debug("[microspin] >>> %s", command)
    self._writer.write((command + "\r\n").encode("ascii"))
    await self._writer.drain()

    # Stage 2: ACK! <echo> <id>
    ack = await self._readline()
    m = _ACK_RE.match(ack)
    if not m:
      raise MicroSpinProtocolError(f"Expected ACK!, got {ack!r}")
    command_id = int(m.group("id"))
    logger.debug("[microspin] <<< ACK id=%d", command_id)

    # Stages 3+4: data lines until OK!/ERROR! <cmd> <id>
    end_re = re.compile(rf"^(?P<status>OK!|ERROR!)\s+.*\s+{command_id}\s*$")
    data: List[str] = []
    while True:
      line = await self._readline()
      end = end_re.match(line)
      if end:
        if end.group("status") == "OK!":
          logger.debug("[microspin] <<< OK (%d data lines)", len(data))
          return data
        raise MicroSpinError(command, command_id, data)
      data.append(line)

  # ------------------------------ CentrifugeBackend abstract methods --------

  async def open_door(self) -> None:
    """Open the plate-loading door. Sends the firmware ``od`` command."""
    await self.send_command("od")

  async def close_door(self) -> None:
    """Close the plate-loading door. Sends the firmware ``cd`` command."""
    await self.send_command("cd")

  async def go_to_bucket1(self) -> None:
    """Present bucket 1 at the load position.

    Note that on the MicroSpin the ``open <bucket>`` command also *opens the
    door* as a side effect; this is the only way to position a bucket for
    loading.
    """
    await self.send_command("open 1", timeout=max(self.timeout, 60.0))

  async def go_to_bucket2(self) -> None:
    """Present bucket 2 at the load position (also opens the door)."""
    await self.send_command("open 2", timeout=max(self.timeout, 60.0))

  # The four lock/unlock primitives below are declared abstract by
  # CentrifugeBackend, but on the MicroSpin they are firmware-internal
  # maintenance commands (see manual §6.7) that the higher-level commands
  # (``open <bucket>``, ``spin``, ``home``) already handle automatically. We
  # deliberately do NOT forward them to the wire so callers can't put the
  # device into a half-managed state by issuing them out-of-band. If you
  # really need to drive the underlying ``lockdoor`` / ``unlockdoor`` /
  # ``locknest`` / ``unlocknest`` commands (e.g. for service), use
  # :meth:`send_command` directly.

  async def lock_door(self) -> None:  # pragma: no cover -- always raises
    """Not supported on the MicroSpin: door locking is firmware-managed.

    Always raises :class:`NotImplementedError`. The MicroSpin firmware locks
    the door automatically as part of ``spin``; the underlying ``lockdoor``
    wire command is documented as maintenance-only in manual §6.7 and is
    deliberately not exposed here. If you really need to drive it, use
    ``backend.send_command("lockdoor")`` directly.
    """
    raise NotImplementedError(
      "Door locking is handled automatically by the MicroSpin firmware as "
      "part of `spin`; the standalone `lockdoor` command is a maintenance "
      "primitive (manual §6.7) and is not exposed through pylabrobot. If "
      "you really need it, use `backend.send_command('lockdoor')`."
    )

  async def unlock_door(self) -> None:  # pragma: no cover -- always raises
    """Not supported on the MicroSpin: door unlocking is firmware-managed.

    Always raises :class:`NotImplementedError`. See :meth:`lock_door`.
    """
    raise NotImplementedError(
      "Door unlocking is handled automatically by the MicroSpin firmware "
      "as part of `open <bucket>`; the standalone `unlockdoor` command is "
      "a maintenance primitive (manual §6.7) and is not exposed through "
      "pylabrobot. If you really need it, use "
      "`backend.send_command('unlockdoor')`."
    )

  async def lock_bucket(self) -> None:  # pragma: no cover -- always raises
    """Not supported on the MicroSpin: nest locking is firmware-managed.

    Always raises :class:`NotImplementedError`. See :meth:`lock_door`.
    """
    raise NotImplementedError(
      "Nest locking is handled automatically by the MicroSpin firmware as "
      "part of `open <bucket>`; the standalone `locknest` command is a "
      "maintenance primitive (manual §6.7) and is not exposed through "
      "pylabrobot. If you really need it, use "
      "`backend.send_command('locknest')`."
    )

  async def unlock_bucket(self) -> None:  # pragma: no cover -- always raises
    """Not supported on the MicroSpin: nest unlocking is firmware-managed.

    Always raises :class:`NotImplementedError`. See :meth:`lock_door`.
    """
    raise NotImplementedError(
      "Nest unlocking is handled automatically by the MicroSpin firmware "
      "as part of `spin`; the standalone `unlocknest` command is a "
      "maintenance primitive (manual §6.7) and is not exposed through "
      "pylabrobot. If you really need it, use "
      "`backend.send_command('unlocknest')`."
    )

  async def spin(
    self,
    g: float,
    duration: float,
    acceleration: float = 0.5,
    deceleration: float = 0.5,
  ) -> None:
    """Start a spin cycle on the MicroSpin.

    Args:
      g: Relative centrifugal force in ``×g``. Must be in ``[1, 3000]``
        (3000 ``×g`` is the spec maximum per manual §11).
      duration: Time at speed in seconds. Must be ``>= 1``.
      acceleration: Acceleration ramp as a fraction of the machine maximum,
        in ``(0, 1]``. Internally converted to the integer percent the
        firmware expects.
      deceleration: Deceleration ramp as a fraction of the machine maximum,
        in ``(0, 1]``. The MicroSpin firmware uses a fast decel curve above
        ``CENTRIFUGE_DECEL_THRESHOLD_G`` (default 300 ``×g``) and a slow one
        below, so the *effective* decel rate may vary across the run.

    Raises:
      ValueError: If any argument is out of range.
      MicroSpinError: If the device rejects the spin (e.g. door not closed,
        device not homed, imbalance trip).

    Warns:
      UserWarning: If ``g`` is below
        :attr:`LOW_G_WARNING_THRESHOLD` (30 ×g by default). At very low
        G-forces the spindle's "stopped" sensor sometimes fails to latch at
        the end of the spin, so the firmware never emits the final ``OK!``
        completion line. From the client's point of view the command
        appears to hang indefinitely; subsequent commands will time out
        because the firmware still considers a spin in progress. If you
        hit this, the recovery path is :meth:`abort` followed by
        :meth:`clear_button_abort` (and possibly a power-cycle).

    Safety:
      See the module-level docstring for the pre-spin checklist. This method
      does NOT verify physical conditions; it only validates argument ranges.
    """
    if not 1 <= g <= self.MAX_G_FORCE:
      raise ValueError(f"g must be in [1, {self.MAX_G_FORCE}] ×g, got {g}")
    if duration < 1:
      raise ValueError(f"duration must be at least 1 second, got {duration}")
    if not 0 < acceleration <= 1:
      raise ValueError(f"acceleration must be in (0, 1], got {acceleration}")
    if not 0 < deceleration <= 1:
      raise ValueError(f"deceleration must be in (0, 1], got {deceleration}")

    if g < self.LOW_G_WARNING_THRESHOLD:
      warnings.warn(
        f"Spinning the MicroSpin at g={g} (<{self.LOW_G_WARNING_THRESHOLD} ×g) "
        "is known to occasionally hang the firmware: the spindle-stopped "
        "sensor may fail to latch, so no `OK!` is emitted and subsequent "
        "commands will time out. If this happens, call `abort()` followed "
        "by `clear_button_abort()`, and power-cycle if the device stays stuck.",
        UserWarning,
        stacklevel=2,
      )

    g_int = int(round(g))
    duration_int = int(round(duration))
    accel_pct = max(1, min(100, int(round(acceleration * 100))))
    decel_pct = max(1, min(100, int(round(deceleration * 100))))

    # The spin command completes when the rotor has fully decelerated and the
    # "spindle stopped" sensor latches. Generous padding on top of the user's
    # `duration` is needed to cover both ramps plus the sensor settle window.
    spin_timeout = max(self.timeout, duration + 180.0)
    await self.send_command(
      f"spin {g_int} {accel_pct} {decel_pct} {duration_int}",
      timeout=spin_timeout,
    )

  # ------------------------------ MicroSpin-specific helpers ----------------

  async def home(self) -> None:
    """Home both axes (door and spindle).

    The MicroSpin needs to be homed once after every power-cycle before any
    spin or bucket-positioning command is accepted.
    """
    await self.send_command("home", timeout=max(self.timeout, 120.0))

  async def is_homed(self) -> bool:
    """Return ``True`` if the device reports ``homed`` to ``hss``."""
    data = await self.send_command("hss")
    return bool(data) and data[0].strip().lower() == "homed"

  async def abort(self, *, timeout: Optional[float] = None) -> None:
    """Decelerate the rotor and stop the current operation.

    After ``abort``, the firmware enters an aborted state that blocks further
    motion commands until you call :meth:`clear_button_abort`.

    Args:
      timeout: Override the per-command timeout. A full decel from 3000 ×g on
        the slow-decel curve can take well over a minute, so by default this
        uses ``max(self.timeout, 180s)``.
    """
    effective = max(self.timeout, 180.0) if timeout is None else timeout
    await self.send_command("abort", timeout=effective)

  async def clear_button_abort(self) -> None:
    """Clear the abort state (resets the latch set by ``abort`` or the front
    panel button)."""
    await self.send_command("clearbuttonabort")

  async def reset(
    self,
    *,
    abort_timeout: Optional[float] = None,
    settle_timeout: Optional[float] = None,
    swallow_abort_errors: bool = True,
    wait_for_settle: bool = True,
  ) -> Optional[Dict[str, str]]:
    """Bring the device back to a clean, ready-to-command state.

    Issues the canonical recovery sequence:

    1. :meth:`abort` -- request a decel + stop.
    2. :meth:`clear_button_abort` -- release the latched abort state so that
       subsequent motion commands (``home``, ``open``, ``spin``, ...) are
       accepted again.
    3. :meth:`wait_for_spindle_stopped` -- a single ``status`` call that the
       firmware will not answer until the rotor is genuinely stopped.

    Steps 1 and 2 both return ``OK!`` *immediately* on the wire -- they are
    just acknowledgements of the request, not confirmation that motion has
    ceased. Step 3 is the real "we are stopped" gate, because the firmware
    queues a ``status`` request behind any active motion and only answers
    once that motion completes.

    Args:
      abort_timeout: Override the timeout for the ``abort`` step. Pass a
        generous value if you've configured a very tight backend timeout.
        Defaults to :meth:`abort`'s own default.
      settle_timeout: Override the timeout for the final ``status`` poll
        that waits for the spindle to actually spin down. Defaults to
        :meth:`wait_for_spindle_stopped`'s own default (``max(self.timeout,
        300s)``).
      swallow_abort_errors: If ``True`` (the default), errors raised by the
        ``abort`` step are logged and ignored so that
        :meth:`clear_button_abort` is still attempted. This is usually what
        you want for a recovery routine -- e.g. ``abort`` can legitimately
        fail with "nothing to abort" depending on firmware state. Set this
        to ``False`` if you want any abort failure to propagate.
      wait_for_settle: If ``True`` (the default), block on step 3 and
        return the final status dict. If ``False``, skip step 3 and return
        ``None`` immediately after :meth:`clear_button_abort` -- useful
        when you only want to clear the firmware's abort latch and don't
        care whether the rotor has stopped yet.

    Returns:
      The parsed status report from step 3 (a ``{field: value}`` dict), or
      ``None`` if ``wait_for_settle=False``.

    Raises:
      MicroSpinError: If :meth:`clear_button_abort` fails, or if the final
        ``status`` poll returns ``ERROR!``. Either case indicates the
        device is in a state that the normal recovery sequence can't get
        out of -- a power-cycle is usually required at that point.
      asyncio.TimeoutError: If the rotor doesn't stop within
        ``settle_timeout`` (i.e. the spindle is genuinely stuck).

    Note:
      ``reset`` does NOT verify that the resulting state is *homed*. Follow
      up with :meth:`is_homed` (and re-:meth:`home` if needed) before
      issuing the next motion command. The MicroSpin firmware also keeps a
      persistent error stack which is unaffected by reset; use
      :meth:`get_errors` to inspect it.
    """
    try:
      await self.abort(timeout=abort_timeout)
    except MicroSpinError as exc:
      if not swallow_abort_errors:
        raise
      logger.info(
        "[microspin] abort during reset() returned ERROR! (swallowed): %s",
        exc,
      )
    await self.clear_button_abort()
    if not wait_for_settle:
      return None
    return await self.wait_for_spindle_stopped(timeout=settle_timeout)

  async def get_status(self, *, timeout: Optional[float] = None) -> Dict[str, str]:
    """Return the device's status report as a ``{field: value}`` dict.

    Typical fields include ``Spindle Position`` and ``Door Position``.

    Note:
      When a spin or decel is in progress, the MicroSpin firmware will not
      respond to ``status`` until the rotor is fully stopped. ``status`` is
      therefore a convenient synchronous "are we really stopped yet?" gate;
      see :meth:`wait_for_spindle_stopped` and :meth:`reset` for callers
      that exploit this.

    Args:
      timeout: Override the per-command timeout. The default (None) uses
        ``self.timeout`` which may be too short if a spin-down is in
        progress -- pass a generous value (e.g. 300 s) in that case.
    """
    data = await self.send_command("status", timeout=timeout)
    return _parse_kv_lines(data)

  async def wait_for_spindle_stopped(
    self,
    *,
    timeout: Optional[float] = None,
  ) -> Dict[str, str]:
    """Block until the firmware confirms the rotor is fully stopped.

    Sends a single ``status`` command, which the firmware queues behind any
    in-progress motion. When that motion completes, the firmware emits the
    status report and ``OK!`` -- at which point we know the rotor is
    mechanically stopped from the controller's point of view.

    Args:
      timeout: Override the per-command timeout. Defaults to
        ``max(self.timeout, 300s)`` which covers a full decel from 3000 ×g
        on the slow-decel curve.

    Returns:
      The parsed status report ({key: value} dict, same as
      :meth:`get_status`).
    """
    effective = max(self.timeout, 300.0) if timeout is None else timeout
    return await self.get_status(timeout=effective)

  async def get_version(self) -> Dict[str, str]:
    """Return the firmware/library version report as a ``{field: value}`` dict."""
    data = await self.send_command("version")
    return _parse_kv_lines(data)

  async def get_errors(self, n: int = 10) -> List[str]:
    """Return the top ``n`` entries from the device's error stack."""
    return await self.send_command(f"errors {int(n)}")


def _parse_kv_lines(lines: List[str]) -> Dict[str, str]:
  """Parse ``key: value`` style report lines into a dict."""
  out: Dict[str, str] = {}
  for line in lines:
    if ":" in line:
      key, _, value = line.partition(":")
      out[key.strip()] = value.strip()
  return out
