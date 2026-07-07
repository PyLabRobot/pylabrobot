"""BD FACSMelody driver and cell-sorting backend.

The ``FACSMelodyDriver`` is the wire: it owns the control link, loads a decoded
``ProtocolMap``, and sends frames behind two independent safety switches. The
``FACSMelodyCellSorterBackend`` is the protocol: it turns ``CellSorter`` operations
into the frames named in the map.

Safety
------
The Melody is a laser plus pressurized fluidics, so the driver is conservative by
default and mirrors the reverse-engineering toolkit's replay guards:

* Dry-run by default. ``send`` logs the exact bytes and transmits nothing unless
  the driver was constructed with ``armed=True`` and the call passes ``live=True``.
  Two switches, both off by default.
* Commands that move fluid, open a nozzle, or fire a sort additionally require
  ``allow_actuation=True``; otherwise ``send`` raises ``SortActuationError``.
* A live run refuses to start unless every required command in the ProtocolMap is
  decoded, so a half-mapped protocol cannot drive hardware.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.cell_sorting.backend import CellSorterBackend
from pylabrobot.device import Driver

from .constants import ACTUATING_COMMANDS, Transport
from .errors import (
  ProtocolMapIncompleteError,
  SortActuationError,
  SortNotReadyError,
  SortTimeoutError,
)
from .protocol_map import ProtocolMap, seed_required

logger = logging.getLogger(__name__)


class FACSMelodyDriver(Driver):
  """Owns the FACSMelody control link and the guarded frame transport.

  Args:
    protocol_path: Path to a decoded ProtocolMap JSON. Required for a live run;
      omit it to run dry (a required-command map is seeded so orchestration works
      end-to-end without hardware).
    armed: Open the physical link and allow transmission. Off by default.
    allow_actuation: Permit commands that physically actuate the sorter. Off by
      default; a human should be present when this is on.
  """

  def __init__(
    self,
    protocol_path: Optional[str] = None,
    *,
    armed: bool = False,
    allow_actuation: bool = False,
  ):
    super().__init__()
    self.protocol_path = protocol_path
    self.armed = armed
    self.allow_actuation = allow_actuation
    self.pm: Optional[ProtocolMap] = None
    self._conn: Optional[_Connection] = None

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    if self.protocol_path is not None:
      self.pm = ProtocolMap.from_json(self.protocol_path)
    else:
      self.pm = seed_required()

    if not self.armed:
      logger.warning("FACSMelody dry-run (armed=False): no link opened, nothing will transmit.")
      return

    coverage = self.pm.coverage()
    if coverage["missing"]:
      raise ProtocolMapIncompleteError(coverage["missing"])
    if self.pm.endpoint is None:
      raise SortNotReadyError("ProtocolMap has no endpoint; cannot open a live link.")
    self._conn = _open_connection(self.pm.transport, self.pm.endpoint)
    logger.info("FACSMelody link open via %s @ %s", self.pm.transport.value, self.pm.endpoint)

  async def stop(self) -> None:
    if self._conn is not None:
      self._conn.close()
      self._conn = None

  async def send(
    self,
    name: str,
    frame_hex: str,
    *,
    live: bool = False,
    actuating: bool = False,
    expect: Optional[bytes] = None,
  ) -> Optional[bytes]:
    """Send a decoded frame, subject to the safety guards.

    Args:
      name: Logical command name (for logging and the actuation guard).
      frame_hex: The command bytes as a hex string.
      live: Whether this call intends to transmit (still requires ``armed``).
      actuating: Force the actuation guard on regardless of ``name``.
      expect: Optional bytes expected in the response; a mismatch is logged.

    Returns:
      The response bytes, or ``None`` in dry-run.
    """
    will_transmit = self.armed and live
    if will_transmit and (actuating or name in ACTUATING_COMMANDS) and not self.allow_actuation:
      raise SortActuationError(
        f"'{name}' actuates the sorter; construct the driver with "
        "allow_actuation=True and a human present to send it."
      )
    if not will_transmit:
      logger.warning("[dry-run] would send '%s': %s", name, frame_hex or "<no frame>")
      return None
    if self._conn is None:
      raise SortNotReadyError("link is not open; call setup() with armed=True first.")
    data = bytes.fromhex(frame_hex)
    logger.info("SEND '%s': %s", name, frame_hex)
    self._conn.write(data)
    resp = self._conn.read()
    logger.info("RECV: %s", resp.hex() if resp else "<none>")
    if expect is not None and resp is not None and expect not in resp:
      logger.warning("response did not contain expected %s", expect.hex())
    return resp

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "protocol_path": self.protocol_path,
      "armed": self.armed,
      "allow_actuation": self.allow_actuation,
    }


class FACSMelodyCellSorterBackend(CellSorterBackend):
  """Translates ``CellSorter`` operations into FACSMelody frames via the driver."""

  def __init__(self, driver: FACSMelodyDriver):
    self._driver = driver

  def _frame(self, command: str, **params: object) -> str:
    """Build the hex frame for a command, substituting ``{param}`` tokens.

    Returns an empty string when the command is undecoded (dry-run), which the
    driver logs harmlessly. Live runs always have decoded frames because setup
    refuses an incomplete map.
    """
    if self._driver.pm is None:
      raise SortNotReadyError("driver is not set up; call setup() first.")
    cmd = self._driver.pm.commands.get(command)
    template = cmd.frame_template if cmd is not None else None
    if template is None:
      return ""
    for key, value in params.items():
      template = template.replace("{" + key + "}", _encode_param(value))
    return template

  async def get_status(self) -> str:
    resp = await self._driver.send("get_status", self._frame("get_status"), live=True)
    # Response parsing is confirmed during hardware validation; until then a live
    # link reports 'unknown' rather than a fabricated state.
    return "idle" if resp is None else "unknown"

  async def load_template(self, name: str) -> None:
    await self._driver.send("load_template", self._frame("load_template", name=name), live=True)

  async def set_deposition(self, cells_per_well: int, plate_format: str) -> None:
    await self._driver.send(
      "set_deposition",
      self._frame("set_deposition", cells=cells_per_well, plate=plate_format),
      live=True,
    )

  async def prime(self) -> None:
    await self._driver.send("prime", self._frame("prime"), live=True)

  async def start_sort(
    self,
    wells: int,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    await self._driver.send("start_sort", self._frame("start_sort", wells=wells), live=True)

  async def wait_for_completion(self, poll_interval: float, timeout: float) -> None:
    if not self._driver.armed:
      return  # dry-run: nothing physical to wait for
    waited = 0.0
    while waited < timeout:
      if (await self.get_status()) in ("idle", "complete"):
        return
      await asyncio.sleep(poll_interval)
      waited += poll_interval
    raise SortTimeoutError(f"sort did not complete within {timeout}s")

  async def abort(self) -> None:
    await self._driver.send("abort", self._frame("abort"), live=True)

  async def clean(self) -> None:
    await self._driver.send("clean", self._frame("clean"), live=True)


def _encode_param(value: object) -> str:
  """Default parameter encoder, resolved during hardware validation.

  The byte encoding for each parameter is determined during reverse engineering
  (vary one setting, diff the frames) and set per parameter. Until then ints are
  emitted as a single byte and everything else as UTF-8 hex, so dry-runs are
  legible before the per-parameter encoders are confirmed on the instrument.
  """
  if isinstance(value, int):
    return f"{value & 0xFF:02x}"
  return str(value).encode().hex()


class _Connection:
  """Minimal transport interface: write bytes, read a response, close."""

  def write(self, data: bytes) -> None:
    raise NotImplementedError

  def read(self, size: int = 512) -> bytes:
    raise NotImplementedError

  def close(self) -> None:
    raise NotImplementedError


def _open_connection(transport: Transport, endpoint: str, read_timeout: float = 1.0) -> _Connection:
  if transport == Transport.TCP:
    return _TcpConnection(endpoint, read_timeout)
  if transport == Transport.SERIAL:
    return _SerialConnection(endpoint, read_timeout)
  if transport == Transport.USB:
    return _UsbConnection(endpoint, read_timeout)
  raise SortNotReadyError(f"unsupported transport {transport!r}")


class _TcpConnection(_Connection):
  def __init__(self, endpoint: str, read_timeout: float):
    import socket

    host, port = endpoint.rsplit(":", 1)
    self._sock = socket.create_connection((host, int(port)), timeout=read_timeout)

  def write(self, data: bytes) -> None:
    self._sock.sendall(data)

  def read(self, size: int = 512) -> bytes:
    try:
      return self._sock.recv(size)
    except OSError:
      return b""

  def close(self) -> None:
    self._sock.close()


class _SerialConnection(_Connection):
  def __init__(self, endpoint: str, read_timeout: float):
    import serial

    self._port = serial.Serial(endpoint, timeout=read_timeout)

  def write(self, data: bytes) -> None:
    self._port.write(data)

  def read(self, size: int = 512) -> bytes:
    return bytes(self._port.read(size))

  def close(self) -> None:
    self._port.close()


class _UsbConnection(_Connection):
  """PyUSB bulk-endpoint wrapper. Endpoint format: ``usb:0xVID:0xPID``.

  Endpoints are auto-detected as the first bulk OUT/IN pair; override if the
  Melody turns out to use interrupt endpoints.
  """

  def __init__(self, endpoint: str, read_timeout: float):
    import usb.core
    import usb.util

    self._timeout_ms = int(read_timeout * 1000)
    _, vid, pid = endpoint.split(":")
    self._dev = usb.core.find(idVendor=int(vid, 16), idProduct=int(pid, 16))
    if self._dev is None:
      raise SortNotReadyError(f"USB device {endpoint} not found")
    try:
      self._dev.set_configuration()
    except Exception:  # noqa: BLE001 - device may already be configured
      pass
    cfg = self._dev.get_active_configuration()
    intf = cfg[(0, 0)]
    self._ep_out = usb.util.find_descriptor(
      intf,
      custom_match=lambda e: (
        usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
      ),
    )
    self._ep_in = usb.util.find_descriptor(
      intf,
      custom_match=lambda e: (
        usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
      ),
    )

  def write(self, data: bytes) -> None:
    self._ep_out.write(data, timeout=self._timeout_ms)

  def read(self, size: int = 512) -> bytes:
    try:
      return bytes(self._ep_in.read(size, timeout=self._timeout_ms))
    except Exception:  # noqa: BLE001 - a read timeout is a normal empty response
      return b""

  def close(self) -> None:
    import usb.util

    usb.util.dispose_resources(self._dev)
