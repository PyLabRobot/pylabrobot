"""ODTC — v1b1 Device class for the Inheco ODTC thermocycler."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.loading_tray import LoadingTray
from pylabrobot.capabilities.thermocycling.thermocycler import Thermocycler
from pylabrobot.device import Device
from pylabrobot.inheco.scila.inheco_sila_interface import SiLAState
from pylabrobot.resources import Coordinate, Resource

from .backend import ODTCThermocyclerBackend
from .door import ODTCDoorBackend
from .driver import ODTCDriver
from .model import ODTC_DIMENSIONS, ODTCDimensions, ODTCVariant, normalize_variant


class ODTC(Resource, Device):
  """Inheco ODTC thermocycler device.

  Extends both ``Resource`` (physical footprint in the deck resource tree)
  and ``Device`` (driver lifecycle).

  Capabilities:
  - ``odtc.tc``   — ``Thermocycler`` capability for protocol execution and temperature control
  - ``odtc.door`` — ``LoadingTray`` capability for the motorized door (plate access)

  Usage::

    odtc = ODTC(odtc_ip="169.254.x.x", name="odtc")
    await odtc.setup()
    await odtc.door.open()
    # load plate onto odtc.door ...
    await odtc.door.close()
    await odtc.tc.run_protocol(protocol)
    # or with explicit params:
    # await odtc.tc.run_protocol(
    #     protocol,
    #     backend_params=ODTCBackendParams(
    #         fluid_quantity=FluidQuantity.UL_30_TO_74,
    #     ),
    # )
    await odtc.stop()

  Physical dimensions (mm): x=156.5, y=248.0, z=124.3.
  SBS plate footprint on block: 127.76 × 85.48 mm.
  """

  DIMENSIONS: ODTCDimensions = ODTC_DIMENSIONS

  def __init__(
    self,
    odtc_ip: str,
    variant: int = 96,
    name: str = "odtc",
    client_ip: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
  ) -> None:
    driver = ODTCDriver(machine_ip=odtc_ip, client_ip=client_ip, logger=logger)
    variant_normalized: ODTCVariant = normalize_variant(variant)

    Resource.__init__(
      self,
      name=name,
      size_x=ODTC_DIMENSIONS.x,
      size_y=ODTC_DIMENSIONS.y,
      size_z=ODTC_DIMENSIONS.z,
    )
    Device.__init__(self, driver=driver)

    self.driver: ODTCDriver = driver  # typed public reference (Device base stores self.driver)
    self.logger = logger or logging.getLogger(__name__)

    self.tc = Thermocycler(backend=ODTCThermocyclerBackend(driver=driver, variant=variant_normalized))

    self.door = LoadingTray(
      backend=ODTCDoorBackend(driver=driver),
      name=f"{name}_door",
      size_x=127.76,   # SBS 96-well plate footprint
      size_y=85.48,
      size_z=0.0,
      child_location=Coordinate.zero(),
    )
    self.assign_child_resource(self.door, location=Coordinate.zero())

    self._capabilities = [self.tc, self.door]

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Device.serialize(self)}

  # ------------------------------------------------------------------
  # Properties
  # ------------------------------------------------------------------

  @property
  def odtc_ip(self) -> str:
    """IP address of the ODTC device."""
    return self.driver._machine_ip  # type: ignore[attr-defined]

  @property
  def variant(self) -> ODTCVariant:
    """ODTC variant (96 or 384)."""
    return self.tc.backend._variant  # type: ignore[attr-defined]

  # ------------------------------------------------------------------
  # Lifecycle
  # ------------------------------------------------------------------

  async def setup(
    self,
    full: bool = True,
    simulation_mode: bool = False,
    max_attempts: int = 10,
    retry_backoff_base_seconds: float = 1.0,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Set up the ODTC connection.

    Args:
      full: If True (default), runs the full SiLA lifecycle (event receiver,
        Reset, Initialize, verify idle) with retry and exponential backoff.
        If False, starts only the event receiver (reconnect after session loss
        without aborting a running method).
      simulation_mode: When full=True, passes True to the device so methods
        execute in simulation mode (instant completion with estimated duration).
      max_attempts: Number of full-path attempts before giving up.
      retry_backoff_base_seconds: Base delay in seconds for exponential backoff.
    """
    if not full:
      await self.driver.setup(backend_params=backend_params)
      await self.tc._on_setup(backend_params=backend_params)
      await self.door._on_setup(backend_params=backend_params)
      return

    last_error: Optional[Exception] = None
    for attempt in range(max_attempts):
      try:
        await self._setup_full_path(simulation_mode)
        await self.tc._on_setup(backend_params=backend_params)
        await self.door._on_setup(backend_params=backend_params)
        return
      except Exception as e:  # noqa: BLE001
        last_error = e
        if attempt < max_attempts - 1:
          wait_time = retry_backoff_base_seconds * (2 ** attempt)
          self.logger.warning(
            "Setup attempt %s/%s failed: %s. Retrying in %.1fs.",
            attempt + 1, max_attempts, e, wait_time,
          )
          await asyncio.sleep(wait_time)
        else:
          raise last_error from e
    if last_error is not None:
      raise last_error from last_error

  async def _setup_full_path(self, simulation_mode: bool) -> None:
    await self.driver.setup()
    await self.driver.send_command(
      "Reset",
      deviceId="ODTC",
      eventReceiverURI=self.driver.event_receiver_uri,
      simulationMode=simulation_mode,
    )
    self.driver._lock_id = None  # type: ignore[attr-defined]

    status = await self.driver.request_status()
    self.logger.info("GetStatus returned state: %r", status.value)

    if status == SiLAState.STANDBY:
      self.logger.info("Device is in standby, calling Initialize...")
      await self.driver.send_command("Initialize")
      status_after_init = await self.driver.request_status()
      if status_after_init != SiLAState.IDLE:
        raise RuntimeError(
          f"Device not in idle after Initialize. Got {status_after_init.value!r}."
        )
      self.logger.info("Device initialized and idle")
    elif status == SiLAState.IDLE:
      self.logger.info("Device already idle after Reset")
    else:
      raise RuntimeError(
        f"Unexpected device state after Reset: {status.value!r}. "
        f"Expected standby or idle."
      )

  async def stop(self) -> None:
    """Deactivate block, close SiLA connection."""
    await self.tc._on_stop()
    await self.door._on_stop()
    await self.driver.stop()
