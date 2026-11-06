"""The OT-2's own modules: the Temperature Module and the Magnetic Module, GEN1 and GEN2.

The robot runs these modules itself: they are plugged into the OT-2, and their commands go into the
robot's run beside the pipettes'. Each module is a resource in a deck slot, holding one labware
where Opentrons' module definition puts it, so the pipettes reach that labware where it stands::

  cooler = OT2TemperatureModule("cooler")
  ot2.deck.assign_child_at_slot(cooler, 3)
  cooler.assign_child_resource(plate)
  await ot2.load_module(cooler)
  await cooler.set_temperature(4)
  await cooler.wait_for_temperature()
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Literal, Optional, Tuple

from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.opentrons.types import ModuleInfo
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource_holder import ResourceHolder

if TYPE_CHECKING:
  from pylabrobot.opentrons.ot2.ot2 import OT2

logger = logging.getLogger(__name__)

TemperatureModuleModel = Literal["temperatureModuleV1", "temperatureModuleV2"]
MagneticModuleModel = Literal["magneticModuleV1", "magneticModuleV2"]
ModuleModel = Literal[
  "temperatureModuleV1", "temperatureModuleV2", "magneticModuleV1", "magneticModuleV2"
]

# Where a module's labware corner stands from its slot's corner, in mm: the ``labwareOffset`` of
# Opentrons' module definitions. GEN1 and GEN2 alike in height, 1.3 mm apart in x.
_LABWARE_OFFSETS: Dict[str, Coordinate] = {
  "temperatureModuleV1": Coordinate(-0.15, -0.15, 80.09),
  "temperatureModuleV2": Coordinate(-1.45, -0.15, 80.09),
  "magneticModuleV1": Coordinate(0.125, -0.125, 82.25),
  "magneticModuleV2": Coordinate(-1.175, -0.125, 82.25),
}

# How high each Magnetic Module's magnets go above the labware's base, in mm: the GEN2's
# ``maxEngageHeight``; the GEN1 counts half millimetres, at most 45 of them with 5 under the base.
_MAXIMUM_ENGAGE_HEIGHTS: Dict[str, float] = {"magneticModuleV1": 20.0, "magneticModuleV2": 25.0}

# A module is drawn as the footprint of the slot it stands in, in mm.
_SLOT_SIZE_X = 128.0
_SLOT_SIZE_Y = 86.0


@dataclass(frozen=True)
class TemperatureModuleStatus:
  """A Temperature Module's state as the robot reports it.

  Attributes:
    temperature: the block's temperature, in °C.
    target: the temperature it is set to, in °C; None when it is off.
    status: the robot's word for its state, such as "idle" or "holding at target".
  """

  temperature: Optional[float]
  target: Optional[float]
  status: Optional[str]


@dataclass(frozen=True)
class MagneticModuleStatus:
  """A Magnetic Module's state as the robot reports it.

  Attributes:
    engaged: whether the magnets are up.
    height: how high they are, in mm, as ``OT2MagneticModule.engage`` takes it.
    status: the robot's word for its state, such as "engaged" or "disengaged".
  """

  engaged: Optional[bool]
  height: Optional[float]
  status: Optional[str]


def _optional_float(data: Dict[str, Any], key: str) -> Optional[float]:
  """Read a number the robot may leave out or report as null."""
  value = data.get(key)
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return None
  return float(value)


class OT2ModuleMismatchError(RuntimeError):
  """The robot loaded another module of the model than the one a module was given.

  Attributes:
    module: the module that was to be loaded.
    slot: the deck slot it stands in.
    serial_number: the serial number of the module the robot loaded instead.
  """

  def __init__(self, module: "OT2Module", slot: int, serial_number: str) -> None:
    super().__init__(
      f"The OT-2 loaded {module.model} {serial_number} in slot {slot}, not {module.serial_number}"
    )
    self.module = module
    self.slot = slot
    self.serial_number = serial_number


class OT2Module(ResourceHolder):
  """A module standing in an OT-2 deck slot, holding one labware.

  ``OT2.load_module`` loads it into the robot's run at its slot; its commands then go through that
  run. Loaded again in a later run, it is bound to that one.
  """

  def __init__(self, name: str, model: ModuleModel, serial_number: Optional[str] = None) -> None:
    """
    Args:
      name: the module's name in the resource tree.
      model: the module's model, as the robot names it.
      serial_number: the serial number of the module standing in this slot, for a robot with two of
        the model connected. None to take the one the robot loads for the slot.

    Raises:
      ValueError: If ``model`` is not an OT-2 module this driver knows.
    """
    if model not in _LABWARE_OFFSETS:
      raise ValueError(f"Unknown OT-2 module model {model!r}")
    offset = _LABWARE_OFFSETS[model]
    super().__init__(
      name=name,
      size_x=_SLOT_SIZE_X,
      size_y=_SLOT_SIZE_Y,
      size_z=offset.z,
      child_location=offset,
      category="ot2_module",
      model=model,
    )
    self.module_model: ModuleModel = model
    self.serial_number = serial_number
    self._robot: Optional[OT2] = None
    self._run: Optional[OpentronsRun] = None
    self._module_id: Optional[str] = None

  @property
  def labware_offset(self) -> Coordinate:
    """Where its labware's corner stands from the slot's corner, in mm: the ``labwareOffset`` of
    Opentrons' module definition."""
    return _LABWARE_OFFSETS[self.module_model]

  @property
  def is_loaded(self) -> bool:
    """Whether the module is loaded into its robot's current run."""
    if self._robot is None or self._run is None:
      return False
    current = self._robot._run
    return current is self._run and current.active

  @property
  def module_id(self) -> Optional[str]:
    """The module's id in the robot's current run, or None when it is not loaded into it."""
    return self._module_id if self.is_loaded else None

  def _bind(
    self, robot: OT2, run: OpentronsRun, module_id: str, serial_number: Optional[str]
  ) -> None:
    """Record that ``robot`` loaded this module into ``run``."""
    self._robot, self._run, self._module_id = robot, run, module_id
    self.serial_number = serial_number

  def _require_loaded(self) -> Tuple[OT2, OpentronsRun, str]:
    """Return the robot, its run and this module's id in it.

    Raises:
      RuntimeError: If the module is not loaded into the robot's current run.
    """
    if not self.is_loaded:
      raise RuntimeError(f"{self.name} is not loaded into the OT-2's run; call load_module() first")
    assert self._robot is not None and self._run is not None and self._module_id is not None
    return self._robot, self._run, self._module_id

  async def _execute(
    self, command_type: str, params: Dict[str, Any], timeout: Optional[float] = None
  ) -> None:
    """Send one module command into the robot's run, under the robot's operation lock."""
    robot, run, module_id = self._require_loaded()
    async with robot._operation_lock:
      await run.execute(command_type, {"moduleId": module_id, **params}, timeout=timeout)

  async def _request_info(self) -> ModuleInfo:
    """Read what the robot reports for this module, found by its serial number.

    Raises:
      RuntimeError: If the module has no serial number yet, or none with it is connected.
    """
    robot, _, _ = self._require_loaded()
    for info in await robot.list_connected_modules():
      if info.serial_number == self.serial_number:
        return info
    raise RuntimeError(f"No {self.model} {self.serial_number} is connected to the OT-2")


class OT2TemperatureModule(OT2Module):
  """The Temperature Module, GEN1 or GEN2: a block that heats or cools what stands on it."""

  def __init__(
    self,
    name: str,
    serial_number: Optional[str] = None,
    model: TemperatureModuleModel = "temperatureModuleV2",
  ) -> None:
    """
    Args:
      name: the module's name in the resource tree.
      serial_number: as for ``OT2Module``.
      model: "temperatureModuleV2" for the GEN2, "temperatureModuleV1" for the GEN1.
    """
    if model not in ("temperatureModuleV1", "temperatureModuleV2"):
      raise ValueError(f"{model!r} is not a Temperature Module")
    super().__init__(name, model, serial_number)

  minimum_temperature = 4.0
  """The lowest temperature it holds, in °C."""
  maximum_temperature = 95.0
  """The highest temperature it holds, in °C."""

  async def set_temperature(self, temperature: float) -> None:
    """Heat or cool the block towards ``temperature``, without waiting for it.

    Args:
      temperature: in °C, 4 to 95.

    Raises:
      ValueError: If ``temperature`` is outside what the module holds.
    """
    if not (
      math.isfinite(temperature)
      and self.minimum_temperature <= temperature <= self.maximum_temperature
    ):
      raise ValueError(
        f"temperature must be between {self.minimum_temperature:g} and "
        f"{self.maximum_temperature:g} °C"
      )
    await self._execute("temperatureModule/setTargetTemperature", {"celsius": float(temperature)})
    logger.info("%s: set to %g °C", self.name, temperature)

  async def wait_for_temperature(self, timeout: float = 1800.0) -> None:
    """Wait until the block is at the temperature it is set to. The robot waits.

    Args:
      timeout: how long to wait, in s. Half an hour by default: cooling to 4 °C takes minutes.

    Raises:
      OpentronsCommandError: If no temperature is set.
      OpentronsCommandTimeout: If the block is not there within ``timeout``.
    """
    await self._execute("temperatureModule/waitForTemperature", {}, timeout=timeout)

  async def deactivate(self) -> None:
    """Stop heating or cooling."""
    await self._execute("temperatureModule/deactivate", {})
    logger.info("%s: off", self.name)

  async def request_status(self) -> TemperatureModuleStatus:
    """Read the block's temperature, its target and its state."""
    data = (await self._request_info()).data
    status = data.get("status")
    return TemperatureModuleStatus(
      temperature=_optional_float(data, "currentTemperature"),
      target=_optional_float(data, "targetTemperature"),
      status=status if isinstance(status, str) else None,
    )


class OT2MagneticModule(OT2Module):
  """The Magnetic Module, GEN1 or GEN2: magnets that rise under the labware standing on it."""

  def __init__(
    self,
    name: str,
    serial_number: Optional[str] = None,
    model: MagneticModuleModel = "magneticModuleV2",
  ) -> None:
    """
    Args:
      name: the module's name in the resource tree.
      serial_number: as for ``OT2Module``.
      model: "magneticModuleV2" for the GEN2, "magneticModuleV1" for the GEN1.
    """
    if model not in ("magneticModuleV1", "magneticModuleV2"):
      raise ValueError(f"{model!r} is not a Magnetic Module")
    super().__init__(name, model, serial_number)

  @property
  def maximum_engage_height(self) -> float:
    """How high the magnets go, in mm: 25 on the GEN2, 20 on the GEN1."""
    return _MAXIMUM_ENGAGE_HEIGHTS[self.module_model]

  async def engage(self, height: float) -> None:
    """Raise the magnets to ``height``.

    Args:
      height: in mm above the base of the labware, 0 to ``maximum_engage_height``, as the robot's
        engage command takes it.

    Raises:
      ValueError: If ``height`` is outside what the magnets reach.
    """
    if not (math.isfinite(height) and 0 <= height <= self.maximum_engage_height):
      raise ValueError(f"height must be between 0 and {self.maximum_engage_height:g} mm")
    await self._execute("magneticModule/engage", {"height": float(height)})
    logger.info("%s: magnets up to %g mm", self.name, height)

  async def disengage(self) -> None:
    """Lower the magnets."""
    await self._execute("magneticModule/disengage", {})
    logger.info("%s: magnets down", self.name)

  async def request_status(self) -> MagneticModuleStatus:
    """Read whether the magnets are up, how high, and the module's state."""
    data = (await self._request_info()).data
    engaged, status = data.get("engaged"), data.get("status")
    return MagneticModuleStatus(
      engaged=engaged if isinstance(engaged, bool) else None,
      height=_optional_float(data, "height"),
      status=status if isinstance(status, str) else None,
    )
