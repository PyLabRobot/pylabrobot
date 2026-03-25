"""Composed backend for the Thermo Scientific Multidrop Combi."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pylabrobot.bulk_dispensers.backend import BulkDispenserBackend
from pylabrobot.bulk_dispensers.thermo_scientific.multidrop_combi.actions import (
  MultidropCombiActionsMixin,
)
from pylabrobot.bulk_dispensers.thermo_scientific.multidrop_combi.commands import (
  MultidropCombiCommandsMixin,
)
from pylabrobot.bulk_dispensers.thermo_scientific.multidrop_combi.communication import (
  MultidropCombiCommunicationMixin,
)
from pylabrobot.bulk_dispensers.thermo_scientific.multidrop_combi.queries import (
  MultidropCombiQueriesMixin,
)
from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)


class MultidropCombiBackend(
  MultidropCombiCommunicationMixin,
  MultidropCombiQueriesMixin,
  MultidropCombiActionsMixin,
  MultidropCombiCommandsMixin,
  BulkDispenserBackend,
):
  """Backend for the Thermo Scientific Multidrop Combi reagent dispenser.

  Communication is via RS232/USB serial at 9600 baud, 8N1.

  Args:
    port: Serial port (e.g. "COM3", "/dev/ttyUSB0"). If None, auto-detected by VID/PID.
    timeout: Default serial read timeout in seconds.
  """

  def __init__(
    self,
    port: str | None = None,
    timeout: float = 30.0,
  ) -> None:
    super().__init__()
    self._port = port
    self.timeout = timeout
    self.io: Optional[Serial] = None
    self._command_lock: Optional[asyncio.Lock] = None
    self._instrument_name: str = ""
    self._firmware_version: str = ""
    self._serial_number: str = ""

  async def setup(self) -> None:
    self._command_lock = asyncio.Lock()

    # When port is specified, skip VID/PID discovery (the Multidrop is often
    # connected via an RS232-to-USB adapter with a different VID/PID).
    serial_kwargs = dict(
      human_readable_device_name="Multidrop Combi",
      baudrate=9600,
      bytesize=8,
      parity="N",
      stopbits=1,
      timeout=self.timeout,
      write_timeout=5,
    )
    if self._port:
      serial_kwargs["port"] = self._port
    else:
      serial_kwargs["vid"] = 0x0AB6
      serial_kwargs["pid"] = 0x0344

    self.io = Serial(**serial_kwargs)
    await self.io.setup()

    # Enable XON/XOFF flow control on the underlying serial port
    if self.io._ser is not None:
      self.io._ser.xonxoff = True

    await self._drain_stale_data()

    info = await self._enter_remote_mode()
    self._instrument_name = info["instrument_name"]
    self._firmware_version = info["firmware_version"]
    self._serial_number = info["serial_number"]

    logger.info(
      "Connected to %s (FW: %s, SN: %s)",
      self._instrument_name,
      self._firmware_version,
      self._serial_number,
    )

    # Clear any pending errors
    try:
      await self.acknowledge_error()
    except Exception:
      pass

  async def stop(self) -> None:
    await self._exit_remote_mode()
    if self.io is not None:
      await self.io.stop()
      self.io = None
    self._command_lock = None

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "port": self._port,
      "timeout": self.timeout,
    }
