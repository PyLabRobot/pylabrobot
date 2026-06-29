import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver
from pylabrobot.io.socket import Socket

from ..errors import HighResSampleStorageAbortedError, HighResSampleStorageError
from ..types import EnvironmentParameter, VersionInfo
from .automated_retrieval import HighResSampleStorageAutomatedRetrievalBackend
from .humidity import HighResSampleStorageHumidityControllerBackend
from .protocol import (
  ACK_TOKEN,
  COMPLETION_ABORTED,
  COMPLETION_ERROR,
  COMPLETION_OK,
  COMPLETION_TOKENS,
  parse_kv,
)
from .temperature import HighResSampleStorageTemperatureControllerBackend

logger = logging.getLogger(__name__)


class HighResSampleStorageDriver(Driver):
  """Transport for HighRes Biosolutions sample stores (TundraStore / SteriStore
  / AmbiStore).

  The store exposes a text-based remote-control server over TCP, port 1000.
  Commands are case-sensitive, space-separated, terminated with ``\\r\\n``. Each
  command is answered with an ``ACK!`` echo, optional data lines, then exactly
  one completion line (``OK!`` / ``ABORTED!`` / ``ERROR!``). See the User
  Manual, section "Message Formatting".

  :meth:`send_command` is the shared primitive. The driver owns the per-capability
  backends (:attr:`automated_retrieval`, :attr:`temperature`, :attr:`humidity`),
  which build their commands on top of it.
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
    num_nests: int = 2,
  ):
    """
    Args:
      host: IP address of the store. The factory default is ``192.168.127.60``;
        all HighRes devices also answer on the backdoor ``10.253.253.253``.
      port: Remote-control server port (always 1000).
      read_timeout: Timeout (s) for query/status commands.
      motion_timeout: Timeout (s) for long-running motion commands
        (``home``, ``pick``, ``place``, door moves).
      loading_tray_nest: Which nest the :class:`AutomatedRetrieval` capability
        uses as its default loading tray (1 or 2).
    """
    super().__init__()
    self.io = Socket(
      human_readable_device_name="HighRes sample store",
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=read_timeout,
    )
    self._read_timeout = read_timeout
    self._motion_timeout = motion_timeout
    self._command_lock = asyncio.Lock()

    self.automated_retrieval = HighResSampleStorageAutomatedRetrievalBackend(
      self, loading_tray_nest=loading_tray_nest, num_nests=num_nests
    )
    self.temperature = HighResSampleStorageTemperatureControllerBackend(self)
    self.humidity = HighResSampleStorageHumidityControllerBackend(self)

  @property
  def read_timeout(self) -> float:
    return self._read_timeout

  @property
  def motion_timeout(self) -> float:
    return self._motion_timeout

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "host": self.io._host,
      "port": self.io._port,
      "read_timeout": self._read_timeout,
      "motion_timeout": self._motion_timeout,
      "loading_tray_nest": self.automated_retrieval.loading_tray_nest,
    }

  # --- lifecycle ------------------------------------------------------------

  async def setup(self, backend_params: Optional[BackendParams] = None):
    if backend_params is None:
      backend_params = HighResSampleStorageDriver.SetupParams()
    if not isinstance(backend_params, HighResSampleStorageDriver.SetupParams):
      raise TypeError(f"backend_params must be {HighResSampleStorageDriver.SetupParams}")

    await self.io.setup()
    version = await self.request_version()
    logger.info(
      "Connected to %s (serial %s, firmware %s)",
      version.product_name,
      version.serial_number,
      version.firmware_version,
    )
    if backend_params.home_on_setup:
      await self.automated_retrieval.home()

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
      HighResSampleStorageError: if the device replies ``ERROR!``.
      HighResSampleStorageAbortedError: if the device replies ``ABORTED!``.
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
      raise HighResSampleStorageError(command, error_lines)
    if completion.startswith(COMPLETION_ABORTED):
      raise HighResSampleStorageAbortedError(command)
    assert completion.startswith(COMPLETION_OK)
    return data_lines

  # --- shared device queries ------------------------------------------------

  async def request_version(self) -> VersionInfo:
    raw = parse_kv(await self.send_command("version"))
    return VersionInfo(
      product_name=raw.get("Product Name"),
      serial_number=raw.get("Serial Number"),
      firmware_version=raw.get("Firmware Version"),
      firmware_build=raw.get("Firmware Build"),
      raw=raw,
    )

  async def request_environment(self) -> Dict[str, EnvironmentParameter]:
    """Parse ``environmentstatus`` into ``{name: EnvironmentParameter}``.

    Each channel reports ``NAME:current/setpoint/limit``; sensor-only channels
    (e.g. the gas tank pressures) report only a current value. Shared by the
    temperature and humidity capability backends.
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

      def _opt(i: int, parts=parts) -> Optional[float]:
        try:
          return float(parts[i])
        except (ValueError, IndexError):
          return None

      out[name.strip()] = EnvironmentParameter(
        name=name.strip(), current=current, setpoint=_opt(1), limit=_opt(2)
      )
    return out
