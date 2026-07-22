import asyncio
import logging
from typing import Literal, Optional, Tuple

from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)

__all__ = [
  "CurioxHT2000",
  "CurioxHT2000Error",
  "HT2000Status",
  "HT2000Mode",
  "TrayPosition",
  "PrimeMode",
  "StatusReport",
]


# Frame markers for the binary command envelope.
PREAMBLE = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x01])
TRAILER = 0xFF

# Fixed reply lengths, in bytes.
PING_REPLY_LENGTH = 15
ACK_REPLY_LENGTH = 11
REPORT_REPLY_LENGTH = 42

# Byte offsets of the status digit within each reply.
PING_STATUS_OFFSET = 9
ACK_STATUS_OFFSET = 7
REPORT_STATUS_OFFSET = 9

# Trailer appended to a wash-parameter upload after the caller-set fields. These
# are fixed values the firmware expects for the remaining wash parameters.
WASH_PARAM_TRAILER = "025060090000012"

MIN_WASH_NUMBER = 1
MAX_WASH_NUMBER = 19
MIN_INITIAL_VOLUME = 0
MAX_INITIAL_VOLUME = 99
MIN_FLOW_RATE = 2
MAX_FLOW_RATE = 20

# Error codes returned in the status reply (two ASCII digits).
DEVICE_ERRORS = {
  "01": "No plate.",
  "02": "Cannot retract.",
  "11": "Head home error.",
  "12": "Upper-right home error.",
  "13": "Upper-left home error.",
  "14": "Lower-right home error.",
  "15": "Lower-left home error.",
  "16": "Plate-feeder home error.",
  "21": "Load-plate error.",
  "22": "Head home-signal error.",
  "23": "Plate-feeder home error.",
  "24": "Upper-block home error.",
  "25": "Lower-block home error.",
  "31": "Plate-feeder home error.",
  "41": "Back-lash limit error.",
  "42": "Back-lash limit error.",
  "43": "Back-lash limit error.",
  "44": "Back-lash limit error.",
}


# Instrument status, decoded from the status digit of a reply.
HT2000Status = Literal["ready", "busy", "error", "stopped"]
STATUS_BY_DIGIT: Tuple[HT2000Status, ...] = ("ready", "busy", "error", "stopped")

# Operating mode of the instrument.
HT2000Mode = Literal["operation", "service"]

# Plate-tray position.
TrayPosition = Literal["in", "out"]


# Fluidics prime routine, mapped to its command code.
PrimeMode = Literal["standard", "head", "pump", "short"]
PRIME_COMMANDS = {"standard": "221", "head": "222", "pump": "223", "short": "224"}


class Command:
  """ASCII command payloads."""

  PING = "0"
  ENQUIRE_REPORT = "4"
  RECOVER_HOME = "201"
  SWITCH_MODE = "202"
  STANDARD_WASH = "210"
  TOGGLE_TRAY = "211"
  DRAIN_ASPIRATOR = "231"
  DRAIN_SPILL_TRAY = "232"


class StatusReport:
  """Decoded fields of the enquire-report reply."""

  def __init__(
    self,
    mode: HT2000Mode,
    status: HT2000Status,
    error: Optional[str],
    tray_position: TrayPosition,
    spill_tray_active: bool,
    tray_loaded: bool,
  ) -> None:
    self.mode = mode
    self.status = status
    self.error = error
    self.tray_position = tray_position
    self.spill_tray_active = spill_tray_active
    self.tray_loaded = tray_loaded

  def __repr__(self) -> str:
    return (
      f"StatusReport(mode={self.mode!r}, status={self.status!r}, "
      f"error={self.error!r}, tray_position={self.tray_position!r}, "
      f"spill_tray_active={self.spill_tray_active}, tray_loaded={self.tray_loaded})"
    )


class CurioxHT2000Error(Exception):
  """Exceptions raised by a Curiox HT2000."""

  def __init__(self, title: str, message: Optional[str] = None) -> None:
    self.title = title
    self.message = message

  def __str__(self) -> str:
    return f"{self.title}: {self.message}" if self.message else self.title


class CurioxHT2000:
  """Curiox Laminar Wash HT2000 system.

  A benchtop Laminar Wash station for Curiox DropArray plates. Commands are
  ASCII payloads wrapped in a fixed binary frame; replies are fixed-length and
  carry a status digit (0 ready, 1 busy, 2 error, 3 stopped).

  Serial settings:
    115200 baud, 8 data bits, no parity, 1 stop bit, no handshake.

  Frame layout of a command:
    FF FF FF FF FF 01 | <len> | <payload ASCII> | <checksum hi> <checksum lo> | FF
  where <len> is the payload length and the checksum is the low 16 bits of
  (sum of payload bytes + length), sent high byte first.

  Commands:
    0            ping / status (15-byte reply)
    4            enquire report (42-byte reply)
    201          recover home
    202          switch from service to operation mode
    210          standard wash
    211          toggle tray
    221/222/223/224  prime: standard / head / pump / short
    231          drain aspirator
    232          drain spill tray
  Every action other than ping and enquire-report returns an 11-byte acknowledgement.

  Not verified: has NOT been tested against hardware in PyLabRobot. A warning is
  emitted at setup.
  """

  def __init__(
    self,
    port: str,
    timeout: float = 10.0,
    wash_start_settle: float = 8.0,
    prime_settle: float = 8.0,
    mode_switch_settle: float = 3.0,
    set_settle: float = 2.0,
    poll_interval: float = 3.0,
  ) -> None:
    self.wash_start_settle = wash_start_settle
    self.prime_settle = prime_settle
    self.mode_switch_settle = mode_switch_settle
    self.set_settle = set_settle
    self.poll_interval = poll_interval
    self.io = Serial(
      human_readable_device_name="Curiox HT2000",
      port=port,
      baudrate=115200,
      bytesize=8,
      parity="N",
      stopbits=1,
      timeout=timeout,
      write_timeout=timeout,
    )

  async def setup(self) -> None:
    logger.warning(
      "CurioxHT2000 has NOT been tested against hardware in PyLabRobot. "
      "Please make a PR to remove this message if you have verified it on your hardware."
    )
    await self.io.setup()
    status, _mode, error = await self.ping()
    if status == "error":
      raise CurioxHT2000Error(title="HT2000 reported an error on connect", message=error)
    logger.info("[HT2000 %s] connected: status=%s", self.io.port, status)

  async def stop(self) -> None:
    """Close the serial connection."""
    await self.io.stop()

  # === Frame layer ===

  @staticmethod
  def _build_frame(payload: str) -> bytes:
    body = payload.encode("ascii")
    checksum = (sum(body) + len(body)) & 0xFFFF
    return bytes([*PREAMBLE, len(body), *body, (checksum >> 8) & 0xFF, checksum & 0xFF, TRAILER])

  async def _send_command(self, payload: str) -> None:
    await self.io.write(self._build_frame(payload))
    logger.debug("[HT2000] send: %s", payload)

  async def _read_exact(self, length: int) -> bytes:
    """Read exactly ``length`` bytes, or raise if the device falls short."""
    buf = bytearray()
    while len(buf) < length:
      chunk = await self.io.read(length - len(buf))
      if not chunk:
        break
      buf += chunk
    if len(buf) != length:
      raise CurioxHT2000Error(
        title="No acknowledgement from HT2000",
        message=f"expected {length} bytes, got {len(buf)}",
      )
    return bytes(buf)

  @staticmethod
  def _decode_status(digit: int) -> HT2000Status:
    return STATUS_BY_DIGIT[digit - ord("0")]

  @staticmethod
  def _decode_mode(byte: int) -> HT2000Mode:
    return "operation" if byte == ord("0") else "service"

  async def _send_and_ack(self, payload: str) -> None:
    """Send a command and consume its acknowledgement, raising on an error status."""
    await self._send_command(payload)
    reply = await self._read_exact(ACK_REPLY_LENGTH)
    status = self._decode_status(reply[ACK_STATUS_OFFSET])
    if status == "error":
      raise CurioxHT2000Error(title=f"Command {payload} was rejected")

  # === Status ===

  async def ping(self) -> tuple:
    """Poll status. Returns ``(status, mode, error)``; error is a code string or None."""
    await self._send_command(Command.PING)
    reply = await self._read_exact(PING_REPLY_LENGTH)
    mode = self._decode_mode(reply[8])
    status = self._decode_status(reply[PING_STATUS_OFFSET])
    error = None
    if status == "error":
      code = reply[10:12].decode("ascii", errors="replace")
      error = DEVICE_ERRORS.get(code, code)
    return status, mode, error

  async def enquire_report(self) -> StatusReport:
    """Read the full status report (mode, status, tray state)."""
    await self._send_command(Command.ENQUIRE_REPORT)
    reply = await self._read_exact(REPORT_REPLY_LENGTH)
    mode = self._decode_mode(reply[8])
    status = self._decode_status(reply[REPORT_STATUS_OFFSET])
    error = None
    if status == "error":
      code = reply[10:12].decode("ascii", errors="replace")
      error = DEVICE_ERRORS.get(code, code)
    return StatusReport(
      mode=mode,
      status=status,
      error=error,
      tray_position="in" if reply[12] == ord("0") else "out",
      spill_tray_active=reply[13] != ord("0"),
      tray_loaded=reply[14] != ord("0"),
    )

  # === Operations ===

  async def home(self) -> None:
    """Recover home."""
    await self._send_and_ack(Command.RECOVER_HOME)
    logger.info("[HT2000 %s] homed", self.io.port)

  async def move_tray_out(self) -> None:
    """Move the tray out. Idempotent: a no-op if it is already out."""
    await self._move_tray("out")

  async def move_tray_in(self) -> None:
    """Move the tray in. Idempotent: a no-op if it is already in."""
    await self._move_tray("in")

  async def _toggle_tray(self) -> None:
    """Move the tray to the opposite position (the raw, non-idempotent primitive)."""
    await self._send_and_ack(Command.TOGGLE_TRAY)

  async def _move_tray(self, target: TrayPosition) -> None:
    if (await self.enquire_report()).tray_position == target:
      return
    await self._toggle_tray()
    reached = (await self.enquire_report()).tray_position
    if reached != target:
      raise CurioxHT2000Error(
        title="Tray did not reach the target position",
        message=f"wanted {target}, got {reached}",
      )

  async def drain_aspirator(self) -> None:
    """Drain the aspirator."""
    await self._send_and_ack(Command.DRAIN_ASPIRATOR)

  async def drain_spill_tray(self) -> None:
    """Drain the spill tray."""
    await self._send_and_ack(Command.DRAIN_SPILL_TRAY)

  async def prime(self, mode: PrimeMode = "standard") -> None:
    """Run a fluidics prime routine and wait for it to settle.

    Args:
      mode: prime routine, one of "standard", "head", "pump", or "short".
    """
    if mode not in PRIME_COMMANDS:
      raise ValueError(f"mode must be one of {sorted(PRIME_COMMANDS)}")
    await self._send_and_ack(PRIME_COMMANDS[mode])
    await asyncio.sleep(self.prime_settle)
    logger.info("[HT2000 %s] primed (%s)", self.io.port, mode)

  async def _set_wash_parameters(
    self, wash_number: int, initial_volume: int, flow_rate: int, channel: int
  ) -> None:
    if not MIN_WASH_NUMBER <= wash_number <= MAX_WASH_NUMBER:
      raise ValueError(f"wash_number must be {MIN_WASH_NUMBER}..{MAX_WASH_NUMBER}")
    if not MIN_INITIAL_VOLUME <= initial_volume <= MAX_INITIAL_VOLUME:
      raise ValueError(f"initial_volume must be {MIN_INITIAL_VOLUME}..{MAX_INITIAL_VOLUME}")
    if not MIN_FLOW_RATE <= flow_rate <= MAX_FLOW_RATE:
      raise ValueError(f"flow_rate must be {MIN_FLOW_RATE}..{MAX_FLOW_RATE}")
    if not (0 <= channel <= 5 or 11 <= channel <= 15):
      raise ValueError("channel must be 0..5 or 11..15")
    payload = (
      "1"
      + f"{wash_number:02d}"
      + f"{initial_volume:03d}"
      + f"{flow_rate:02d}"
      + f"{channel:02d}"
      + WASH_PARAM_TRAILER
    )
    await self._send_and_ack(payload)

  async def wash(
    self,
    wash_number: int = 3,
    initial_volume: int = 50,
    flow_rate: int = 5,
    channel: int = 0,
    max_operation_duration: float = 300.0,
  ) -> None:
    """Upload wash parameters and run a wash cycle.

    Switches to operation mode if needed, applies the parameters, starts the
    wash, then polls until the wash cycle completes (the instrument finishes
    washing and returns to ready).

    Args:
      wash_number: number of wash repeats (1..19).
      initial_volume: initial volume in device units (0..99).
      flow_rate: flow rate in device units (2..20).
      channel: fluid channel to use (0..5 or 11..15).
      max_operation_duration: seconds to wait for the wash to complete before
        raising a timeout.
    """
    _status, mode, _error = await self.ping()
    if mode == "service":
      await self._send_and_ack(Command.SWITCH_MODE)
      await asyncio.sleep(self.mode_switch_settle)

    await self._set_wash_parameters(wash_number, initial_volume, flow_rate, channel)
    await asyncio.sleep(self.set_settle)
    await self._send_and_ack(Command.STANDARD_WASH)
    await asyncio.sleep(self.wash_start_settle)
    logger.info("[HT2000 %s] washing (n=%d)", self.io.port, wash_number)
    await self._wait_for_wash(max_operation_duration)

  async def _wait_for_wash(self, max_operation_duration: float) -> None:
    """Poll the report until the wash cycle completes, or raise on error/timeout.

    Waits for the instrument to reach the running state (operation mode, busy,
    tray out, plate loaded) and then for it to return to ready, which marks the
    end of the cycle.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_operation_duration
    started = False
    while loop.time() < deadline:
      report = await self.enquire_report()
      if report.status == "stopped":
        raise CurioxHT2000Error(title="HT2000 stopped during wash")
      if not report.tray_loaded:
        raise CurioxHT2000Error(title="No plate on the tray")
      if report.status == "error":
        raise CurioxHT2000Error(title="HT2000 error during wash", message=report.error)
      running = (
        report.mode == "operation" and report.status == "busy" and report.tray_position == "out"
      )
      if running:
        started = True
      elif started and report.status == "ready":
        return
      await asyncio.sleep(self.poll_interval)
    raise CurioxHT2000Error(title="Timed out waiting for the wash to complete")
