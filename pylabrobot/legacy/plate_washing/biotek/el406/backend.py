"""BioTek EL406 plate washer backend.

This module provides the backend implementation for the BioTek EL406
plate washer, communicating via FTDI USB serial interface.

Protocol Details:
- Serial: 38400 baud, 8 data bits, 2 stop bits, no parity
- Flow control: disabled (no flow control)
- ACK byte: 0x06
- Commands are binary with little-endian encoding
- Read timeout: 15000ms, Write timeout: 5000ms
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from pylabrobot.agilent.biotek.el406.driver import EL406Driver
from pylabrobot.agilent.biotek.el406.peristaltic_dispensing_backend import (
  Cassette,
  EL406PeristalticDispensingBackend,
  PeristalticFlowRate,
)
from pylabrobot.agilent.biotek.el406.plate_washing_backend import EL406PlateWashingBackend
from pylabrobot.agilent.biotek.el406.shaking_backend import EL406ShakingBackend
from pylabrobot.agilent.biotek.el406.syringe_dispensing_backend import (
  EL406SyringeDispensingBackend,
  Syringe,
)
from pylabrobot.legacy.machines.backend import MachineBackend
from pylabrobot.resources import Plate

from .helpers import plate_to_wire_byte

logger = logging.getLogger(__name__)


class ExperimentalBioTekEL406Backend(
  MachineBackend,
):
  """Backend for BioTek EL406 plate washer.

  Communicates with the EL406 via FTDI USB interface.

  Attributes:
    timeout: Default timeout for operations in seconds.

  Example:
    >>> backend = BioTekEL406Backend()
    >>> await backend.setup()
    >>> await backend.peristaltic_prime(plate, volume=300.0, flow_rate="High")
    >>> await backend.manifold_wash(plate, cycles=3)
  """

  def __init__(
    self,
    timeout: float = 15.0,
    device_id: str | None = None,
  ) -> None:
    """Initialize the EL406 backend.

    Args:
      timeout: Default timeout for operations in seconds.
      device_id: FTDI device serial number for explicit connection.
    """
    super().__init__()
    self._device_id = device_id
    self._command_lock: asyncio.Lock | None = None
    self._in_batch: bool = False

    # New architecture: driver + capability backends
    self._new_driver = EL406Driver(timeout=timeout, device_id=device_id)

    self._plate_washing = EL406PlateWashingBackend(self._new_driver)
    self._shaking = EL406ShakingBackend(self._new_driver)
    self._syringe = EL406SyringeDispensingBackend(self._new_driver)
    self._peristaltic = EL406PeristalticDispensingBackend(self._new_driver)

  @property
  def io(self):
    return self._new_driver.io

  @io.setter
  def io(self, value):
    self._new_driver.io = value

  @property
  def timeout(self) -> float:
    return self._new_driver.timeout

  @timeout.setter
  def timeout(self, value: float) -> None:
    self._new_driver.timeout = value

  async def setup(
    self,
    skip_reset: bool = False,
  ) -> None:
    """Set up communication with the EL406.

    Args:
      skip_reset: If True, skip the instrument reset step.
    """
    # If io was injected (e.g. mock for testing), pass it to the driver
    if self.io is not None:
      self._new_driver.io = self.io
    from pylabrobot.agilent.biotek.el406.driver import EL406Driver
    await self._new_driver.setup(backend_params=EL406Driver.SetupParams(skip_reset=skip_reset))
    # Sync back so legacy code can access io/lock
    self.io = self._new_driver.io
    self._command_lock = self._new_driver._command_lock

    logger.info("BioTekEL406Backend setup complete")

  async def stop(self) -> None:
    """Stop communication with the EL406."""
    await self._new_driver.stop()
    self.io = None

  @asynccontextmanager
  async def batch(self, plate: Plate) -> AsyncIterator[None]:
    """Context manager for batching step commands."""
    if self._in_batch:
      yield
      return

    self._new_driver._cached_plate = plate
    self._in_batch = True
    self._new_driver._in_batch = True
    try:
      await self._new_driver.start_batch(plate_to_wire_byte(plate))
      yield
    finally:
      try:
        await self._new_driver.cleanup_after_protocol()
      finally:
        self._in_batch = False
        self._new_driver._in_batch = False

  # Query mixin needs these — delegate to driver
  async def _send_framed_query(self, command, data=b"", timeout=None):
    return await self._new_driver._send_framed_query(command, data=data, timeout=timeout)

  async def _send_framed_command(self, framed_message, timeout=None):
    return await self._new_driver._send_framed_command(framed_message, timeout=timeout)

  async def _test_communication(self):
    return await self._new_driver._test_communication()

  # ---------------------------------------------------------------------------
  # Queries — delegate to driver
  # ---------------------------------------------------------------------------

  async def request_washer_manifold(self):
    return await self._new_driver.request_washer_manifold()

  async def request_syringe_manifold(self):
    return await self._new_driver.request_syringe_manifold()

  async def request_serial_number(self):
    return await self._new_driver.request_serial_number()

  async def request_sensor_enabled(self, sensor):
    return await self._new_driver.request_sensor_enabled(sensor)

  async def request_syringe_box_info(self):
    return await self._new_driver.request_syringe_box_info()

  async def request_peristaltic_installed(self, selector):
    return await self._new_driver.request_peristaltic_installed(selector)

  async def request_instrument_settings(self):
    return await self._new_driver.request_instrument_settings()

  async def run_self_check(self):
    return await self._new_driver.run_self_check()

  # ---------------------------------------------------------------------------
  # Device-level operations — delegate to driver
  # ---------------------------------------------------------------------------

  async def abort(self, step_type=None):
    await self._new_driver.abort(step_type=step_type)

  async def pause(self):
    await self._new_driver.pause()

  async def resume(self):
    await self._new_driver.resume()

  async def reset(self):
    await self._new_driver.reset()

  async def home_motors(self, home_type, motor=None):
    await self._new_driver.home_motors(home_type, motor=motor)

  async def set_washer_manifold(self, manifold):
    await self._new_driver.set_washer_manifold(manifold)

  # ---------------------------------------------------------------------------
  # Manifold methods — delegate to new EL406PlateWashingBackend
  # ---------------------------------------------------------------------------

  async def manifold_aspirate(self, plate, **kwargs):
    async with self.batch(plate):
      await self._plate_washing.manifold_aspirate(plate, **kwargs)

  async def manifold_dispense(self, plate, volume, **kwargs):
    async with self.batch(plate):
      await self._plate_washing.manifold_dispense(plate, volume=volume, **kwargs)

  async def manifold_wash(self, plate, **kwargs):
    async with self.batch(plate):
      await self._plate_washing.manifold_wash(plate, **kwargs)

  async def manifold_prime(self, plate, volume, **kwargs):
    async with self.batch(plate):
      await self._plate_washing.manifold_prime(plate, volume=volume, **kwargs)

  async def manifold_auto_clean(self, plate, **kwargs):
    async with self.batch(plate):
      await self._plate_washing.manifold_auto_clean(plate, **kwargs)

  # ---------------------------------------------------------------------------
  # Shake — delegate to new EL406ShakingBackend
  # ---------------------------------------------------------------------------

  async def shake(self, plate, **kwargs):
    async with self.batch(plate):
      params = EL406ShakingBackend.ShakeParams(
        intensity=kwargs.pop("intensity", "Medium"),
        soak_duration=kwargs.pop("soak_duration", 0),
        move_home_first=kwargs.pop("move_home_first", True),
      )
      await self._shaking.shake(
        speed=0,
        duration=kwargs.pop("duration", 0),
        backend_params=params,
      )

  # ---------------------------------------------------------------------------
  # Syringe — delegate to new EL406SyringeDispensingBackend
  # ---------------------------------------------------------------------------

  async def syringe_dispense(
    self,
    plate: Plate,
    volume: float,
    syringe: Syringe = "A",
    flow_rate: int = 2,
    offset_x: int = 0,
    offset_y: int = 0,
    offset_z: int = 336,
    pump_delay: float = 0.0,
    pre_dispense: bool = False,
    pre_dispense_volume: float = 0.0,
    num_pre_dispenses: int = 2,
    columns: list[int] | None = None,
  ) -> None:
    async with self.batch(plate):
      params = EL406SyringeDispensingBackend.DispenseParams(
        syringe=syringe,
        flow_rate=flow_rate,
        offset_x=offset_x / 10,  # legacy 0.1mm → mm
        offset_y=offset_y / 10,
        offset_z=offset_z / 10,
        pump_delay=pump_delay,
        pre_dispense=pre_dispense,
        pre_dispense_volume=pre_dispense_volume,
        num_pre_dispenses=num_pre_dispenses,
      )
      await self._syringe._syringe_dispense(plate, volume=volume, columns=columns, params=params)

  async def syringe_prime(
    self,
    plate: Plate,
    syringe: Literal["A", "B"] = "A",
    volume: float = 5000.0,
    flow_rate: int = 5,
    refills: int = 2,
    pump_delay: float = 0.0,
    submerge_tips: bool = True,
    submerge_duration: float = 0.0,
  ) -> None:
    async with self.batch(plate):
      params = EL406SyringeDispensingBackend.PrimeParams(
        syringe=syringe,
        flow_rate=flow_rate,
        refills=refills,
        pump_delay=pump_delay,
        submerge_tips=submerge_tips,
        submerge_duration=submerge_duration,
      )
      await self._syringe._syringe_prime(plate, volume=volume, params=params)

  # ---------------------------------------------------------------------------
  # Peristaltic — delegate to new EL406PeristalticDispensingBackend
  # ---------------------------------------------------------------------------

  async def peristaltic_prime(
    self,
    plate: Plate,
    volume: float | None = None,
    duration: int | None = None,
    flow_rate: PeristalticFlowRate = "High",
    cassette: Cassette = "Any",
  ) -> None:
    async with self.batch(plate):
      params = EL406PeristalticDispensingBackend.PrimeParams(
        flow_rate=flow_rate,
        cassette=cassette,
      )
      await self._peristaltic._peristaltic_prime(
        plate,
        volume=volume,
        duration=duration,
        params=params,
      )

  async def peristaltic_dispense(
    self,
    plate: Plate,
    volume: float,
    flow_rate: PeristalticFlowRate = "High",
    offset_x: int = 0,
    offset_y: int = 0,
    offset_z: int | None = None,
    pre_dispense_volume: float = 10.0,
    num_pre_dispenses: int = 2,
    cassette: Cassette = "Any",
    columns: list[int] | None = None,
    rows: list[int] | None = None,
  ) -> None:
    async with self.batch(plate):
      params = EL406PeristalticDispensingBackend.DispenseParams(
        flow_rate=flow_rate,
        offset_x=offset_x / 10 if offset_x is not None else 0.0,  # legacy 0.1mm → mm
        offset_y=offset_y / 10 if offset_y is not None else 0.0,
        offset_z=offset_z / 10 if offset_z is not None else None,
        pre_dispense_volume=pre_dispense_volume,
        num_pre_dispenses=num_pre_dispenses,
        cassette=cassette,
        columns=columns,
        rows=rows,
      )
      await self._peristaltic._peristaltic_dispense(plate, volume=volume, params=params)

  async def peristaltic_purge(
    self,
    plate: Plate,
    volume: float | None = None,
    duration: int | None = None,
    flow_rate: PeristalticFlowRate = "High",
    cassette: Cassette = "Any",
  ) -> None:
    async with self.batch(plate):
      params = EL406PeristalticDispensingBackend.PrimeParams(
        flow_rate=flow_rate,
        cassette=cassette,
      )
      await self._peristaltic._peristaltic_purge(
        plate,
        volume=volume,
        duration=duration,
        params=params,
      )

  def serialize(self) -> dict:
    """Serialize backend configuration."""
    return {
      **super().serialize(),
      "timeout": self.timeout,
      "device_id": self._device_id,
    }
