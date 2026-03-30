"""Tecan EVO USB driver.

Owns the USB connection and implements the Tecan firmware command protocol.
This is the v1b1 equivalent of the legacy ``TecanLiquidHandler`` class.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Union

from pylabrobot.device import Driver
from pylabrobot.io.usb import USB
from pylabrobot.tecan.evo.errors import error_code_to_exception

logger = logging.getLogger(__name__)


class TecanEVODriver(Driver):
  """Driver for the Tecan Freedom EVO liquid handler.

  Handles USB connection lifecycle and the Tecan firmware command protocol
  (``\\x02{module}{command},{params}\\x00`` framing, response parsing, SET caching).

  Args:
    packet_read_timeout: Timeout in seconds for reading a single USB packet.
    read_timeout: Timeout in seconds for reading a full response.
    write_timeout: Timeout in seconds for writing a command.
  """

  def __init__(
    self,
    packet_read_timeout: int = 12,
    read_timeout: int = 60,
    write_timeout: int = 60,
  ):
    super().__init__()
    self.io = USB(
      human_readable_device_name="Tecan EVO",
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      id_vendor=0x0C47,
      id_product=0x4000,
    )
    self._cache: Dict[str, List[Optional[int]]] = {}

  def _assemble_command(self, module: str, command: str, params: List[Optional[int]]) -> str:
    """Assemble a firmware command string.

    Args:
      module: 2-character module identifier (e.g. ``"C5"`` for LiHa).
      command: Command identifier (e.g. ``"PIA"``, ``"PAA"``).
      params: List of integer parameters (``None`` for empty/placeholder).

    Returns:
      Framed command string: ``\\x02{module}{command},{params}\\x00``.
    """
    cmd = module + command + ",".join(str(a) if a is not None else "" for a in params)
    return f"\02{cmd}\00"

  def parse_response(self, resp: bytes) -> Dict[str, Union[str, int, List[Union[int, str]]]]:
    """Parse a firmware response.

    Args:
      resp: Raw response bytes from the USB device.

    Returns:
      Dict with ``"module"`` (str) and ``"data"`` (list of int/str values).

    Raises:
      TecanError: If the response indicates a non-zero error code.
    """
    s = resp.decode("utf-8", "ignore")
    module = s[1:3]
    ret = int(resp[3]) ^ (1 << 7)
    if ret != 0:
      raise error_code_to_exception(module, ret)

    data: List[Union[int, str]] = []
    for x in s[3:-1].split(","):
      if len(x) == 0:
        continue
      data.append(int(x) if x.lstrip("-").isdigit() else x)

    return {"module": module, "data": data}

  async def send_command(
    self,
    module: str,
    command: str,
    params: Optional[List[Optional[int]]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[Dict[str, Union[str, int, List[Union[int, str]]]]]:
    """Send a firmware command and return the parsed response.

    Caches SET commands (commands starting with ``"S"``) and skips sending
    if the same command with the same parameters was already sent.

    Args:
      module: 2-character module identifier.
      command: Command identifier.
      params: List of integer parameters.
      write_timeout: Override write timeout (seconds).
      read_timeout: Override read timeout (seconds).
      wait: If ``True``, wait for and return the response. If ``False``,
            return ``None`` immediately after sending.

    Returns:
      Parsed response dict, or ``None`` if ``wait=False``.

    Raises:
      TecanError: If the device returns a non-zero error code.
    """
    if command[0] == "S" and params is not None:
      k = module + command
      if k in self._cache and self._cache[k] == params:
        return None
      self._cache[k] = params

    cmd = self._assemble_command(module, command, [] if params is None else params)
    await self.io.write(cmd.encode(), timeout=write_timeout)

    if not wait:
      return None

    resp = await self.io.read(timeout=read_timeout)
    return self.parse_response(resp)

  async def setup(self) -> None:
    """Open USB connection to the Tecan EVO.

    Uses a short packet timeout for buffer drain to avoid long waits
    on first connection.
    """
    logger.info("Opening USB connection to Tecan EVO...")
    saved_prt = self.io.packet_read_timeout
    self.io.packet_read_timeout = 1  # fast buffer drain
    await self.io.setup()
    self.io.packet_read_timeout = saved_prt
    logger.info("USB connected.")

  async def stop(self) -> None:
    """Close USB connection."""
    logger.info("Closing USB connection.")
    await self.io.stop()

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "packet_read_timeout": self.io.packet_read_timeout,
      "read_timeout": self.io.read_timeout,
      "write_timeout": self.io.write_timeout,
    }
