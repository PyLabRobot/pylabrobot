"""Opentrons Thermocycler backend and device classes."""

from typing import Optional, cast

from pylabrobot.capabilities.temperature_controlling import (
  TemperatureControlCapability,
  TemperatureControllerBackend,
)
from pylabrobot.capabilities.thermocycling import (
  Protocol,
  ThermocyclingBackend,
  ThermocyclingCapability,
)
from pylabrobot.device import Device, Driver
from pylabrobot.resources import Coordinate, ItemizedResource, ResourceHolder

try:
  from ot_api.modules import (
    list_connected_modules,
    thermocycler_close_lid,
    thermocycler_deactivate_block,
    thermocycler_deactivate_lid,
    thermocycler_open_lid,
    thermocycler_run_profile_no_wait,
    thermocycler_set_block_temperature,
    thermocycler_set_lid_temperature,
  )

  USE_OT = True
except ImportError as e:
  USE_OT = False
  _OT_IMPORT_ERROR = e


# ---------------------------------------------------------------------------
# Driver: single class that talks to the OT HTTP API
# ---------------------------------------------------------------------------


class OpentronsThermocyclerDriver(Driver):
  """Low-level driver for the Opentrons Thermocycler HTTP API.

  All OT API calls live here. Capability backends delegate to this.
  """

  def __init__(self, opentrons_id: str):
    super().__init__()
    if not USE_OT:
      raise RuntimeError(
        "Opentrons is not installed. Please run pip install pylabrobot[opentrons]."
        f" Import error: {_OT_IMPORT_ERROR}."
      )
    self.opentrons_id = opentrons_id

  async def setup(self):
    pass

  async def stop(self):
    thermocycler_deactivate_block(module_id=self.opentrons_id)
    thermocycler_deactivate_lid(module_id=self.opentrons_id)

  def serialize(self) -> dict:
    return {**super().serialize(), "opentrons_id": self.opentrons_id}

  def _find_module(self) -> dict:
    for m in list_connected_modules():
      if m["id"] == self.opentrons_id:
        return cast(dict, m["data"])
    raise RuntimeError(f"Module '{self.opentrons_id}' not found")

  def open_lid(self) -> None:
    thermocycler_open_lid(module_id=self.opentrons_id)

  def close_lid(self) -> None:
    thermocycler_close_lid(module_id=self.opentrons_id)

  def set_block_temperature(self, celsius: float) -> None:
    thermocycler_set_block_temperature(celsius=celsius, module_id=self.opentrons_id)

  def set_lid_temperature(self, celsius: float) -> None:
    thermocycler_set_lid_temperature(celsius=celsius, module_id=self.opentrons_id)

  def deactivate_block(self) -> None:
    thermocycler_deactivate_block(module_id=self.opentrons_id)

  def deactivate_lid(self) -> None:
    thermocycler_deactivate_lid(module_id=self.opentrons_id)

  def run_profile(self, profile: list, block_max_volume: float) -> None:
    thermocycler_run_profile_no_wait(
      profile=profile,
      block_max_volume=block_max_volume,
      module_id=self.opentrons_id,
    )

  def get_block_current_temperature(self) -> float:
    return cast(float, self._find_module()["currentTemperature"])

  def get_block_target_temperature(self) -> Optional[float]:
    return self._find_module().get("targetTemperature")

  def get_lid_current_temperature(self) -> float:
    return cast(float, self._find_module()["lidTemperature"])

  def get_lid_target_temperature(self) -> Optional[float]:
    return self._find_module().get("lidTargetTemperature")

  def get_lid_status_str(self) -> str:
    return cast(str, self._find_module()["lidStatus"])

  def get_lid_temperature_status_str(self) -> str:
    return cast(str, self._find_module().get("lidTemperatureStatus", "idle"))

  def get_block_status_str(self) -> str:
    return cast(str, self._find_module().get("status", "idle"))

  def get_hold_time(self) -> float:
    return cast(float, self._find_module().get("holdTime", 0.0))

  def get_current_step_index(self) -> Optional[int]:
    return self._find_module().get("currentStepIndex")

  def get_total_step_count(self) -> Optional[int]:
    return self._find_module().get("totalStepCount")


# ---------------------------------------------------------------------------
# Capability backends: each takes a driver reference
# ---------------------------------------------------------------------------


class OpentronsBlockBackend(TemperatureControllerBackend):
  """Block temperature controller backed by the OT driver."""

  def __init__(self, driver: OpentronsThermocyclerDriver):
    self._driver = driver

  @property
  def supports_active_cooling(self) -> bool:
    return True

  async def set_temperature(self, temperature: float):
    self._driver.set_block_temperature(temperature)

  async def get_current_temperature(self) -> float:
    return self._driver.get_block_current_temperature()

  async def deactivate(self):
    self._driver.deactivate_block()


class OpentronsLidBackend(TemperatureControllerBackend):
  """Lid temperature controller backed by the OT driver."""

  def __init__(self, driver: OpentronsThermocyclerDriver):
    self._driver = driver

  @property
  def supports_active_cooling(self) -> bool:
    return False

  async def set_temperature(self, temperature: float):
    self._driver.set_lid_temperature(temperature)

  async def get_current_temperature(self) -> float:
    return self._driver.get_lid_current_temperature()

  async def deactivate(self):
    self._driver.deactivate_lid()


class OpentronsThermocyclingBackend(ThermocyclingBackend):
  """Thermocycling capability backed by the OT driver."""

  def __init__(self, driver: OpentronsThermocyclerDriver):
    self._driver = driver

  async def open_lid(self) -> None:
    self._driver.open_lid()

  async def close_lid(self) -> None:
    self._driver.close_lid()

  async def get_lid_open(self) -> bool:
    return self._driver.get_lid_status_str() == "open"

  async def run_protocol(self, protocol: Protocol, block_max_volume: float) -> None:
    ot_profile = []
    for stage in protocol.stages:
      for _ in range(stage.repeats):
        for step in stage.steps:
          if len(set(step.temperature)) != 1:
            raise ValueError(
              f"Opentrons thermocycler only supports a single unique temperature per step, "
              f"got {set(step.temperature)}"
            )
          ot_profile.append({"celsius": step.temperature[0], "holdSeconds": step.hold_seconds})

    self._driver.run_profile(ot_profile, block_max_volume)

  async def get_hold_time(self) -> float:
    return self._driver.get_hold_time()

  async def get_current_cycle_index(self) -> int:
    raise NotImplementedError('Opentrons "cycle" concept is not understood currently.')

  async def get_total_cycle_count(self) -> int:
    raise NotImplementedError('Opentrons "cycle" concept is not understood currently.')

  async def get_current_step_index(self) -> int:
    step_index = self._driver.get_current_step_index()
    if step_index is None:
      raise RuntimeError("Current step index is not available. Is a profile running?")
    return step_index - 1  # OT is one-based

  async def get_total_step_count(self) -> int:
    total = self._driver.get_total_step_count()
    if total is None:
      raise RuntimeError("Total step count is not available. Is a profile running?")
    return total


# ---------------------------------------------------------------------------
# Device classes
# ---------------------------------------------------------------------------


class OpentronsThermocyclerV1(ResourceHolder, Device):
  """Opentrons Thermocycler GEN1."""

  def __init__(
    self,
    name: str,
    opentrons_id: str,
    child_location: Coordinate = Coordinate.zero(),
    child: Optional[ItemizedResource] = None,
  ):
    self._driver = OpentronsThermocyclerDriver(opentrons_id=opentrons_id)

    ResourceHolder.__init__(
      self,
      name=name,
      size_x=172.0,
      size_y=316.0,
      size_z=154.0,
      child_location=child_location,
      category="thermocycler",
      model="thermocyclerModuleV1",
    )
    Device.__init__(self, backend=self._driver)

    self.block = TemperatureControlCapability(backend=OpentronsBlockBackend(self._driver))
    self.lid = TemperatureControlCapability(backend=OpentronsLidBackend(self._driver))
    self.thermocycling = ThermocyclingCapability(
      backend=OpentronsThermocyclingBackend(self._driver), block=self.block, lid=self.lid
    )
    self._capabilities = [self.block, self.lid, self.thermocycling]

    if child is not None:
      self.assign_child_resource(child, location=child_location)

  def serialize(self) -> dict:
    return {**ResourceHolder.serialize(self), **Device.serialize(self)}


class OpentronsThermocyclerV2(ResourceHolder, Device):
  """Opentrons Thermocycler GEN2."""

  def __init__(
    self,
    name: str,
    opentrons_id: str,
    child_location: Coordinate = Coordinate.zero(),
    child: Optional[ItemizedResource] = None,
  ):
    self._driver = OpentronsThermocyclerDriver(opentrons_id=opentrons_id)

    ResourceHolder.__init__(
      self,
      name=name,
      size_x=172.0,
      size_y=244.95,
      size_z=170.35,
      child_location=child_location,
      category="thermocycler",
      model="thermocyclerModuleV2",
    )
    Device.__init__(self, backend=self._driver)

    self.block = TemperatureControlCapability(backend=OpentronsBlockBackend(self._driver))
    self.lid = TemperatureControlCapability(backend=OpentronsLidBackend(self._driver))
    self.thermocycling = ThermocyclingCapability(
      backend=OpentronsThermocyclingBackend(self._driver), block=self.block, lid=self.lid
    )
    self._capabilities = [self.block, self.lid, self.thermocycling]

    if child is not None:
      self.assign_child_resource(child, location=child_location)

  def serialize(self) -> dict:
    return {**ResourceHolder.serialize(self), **Device.serialize(self)}
