"""PLR labware conversion and bindings scoped to one Opentrons run."""

import math
import uuid
from dataclasses import dataclass, replace
from typing import Any, Dict, Optional, Tuple, cast

from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.opentrons.types import LabwareIdentity
from pylabrobot.resources import (
  Container,
  Coordinate,
  CrossSectionType,
  Plate,
  Resource,
  Tip,
  TipRack,
  Trough,
  TroughBottomType,
  Well,
  WellBottomType,
)
from pylabrobot.utils import reshape_2d

_NAMESPACE = "pylabrobot"
_VERSION = 1
_SCHEMA_VERSION = 2

_WELL_BOTTOM_SHAPES = {
  WellBottomType.FLAT: "flat",
  WellBottomType.U: "u",
  WellBottomType.V: "v",
  WellBottomType.UNKNOWN: "flat",
}

# The robot-server validates wellBottomShape as Literal["flat", "u", "v"],
# so the enum's own "U"/"V"/"unknown" values cannot be passed through.
_TROUGH_BOTTOM_SHAPES = {
  TroughBottomType.FLAT: "flat",
  TroughBottomType.U: "u",
  TroughBottomType.V: "v",
  TroughBottomType.UNKNOWN: "flat",
}

_OFFICIAL_TIP_RACKS = {
  "Opentrons Flex 96 Tip Rack 50 µL": "opentrons_flex_96_tiprack_50ul",
  "Opentrons Flex 96 Filter Tip Rack 50 µL": "opentrons_flex_96_filtertiprack_50ul",
  "Opentrons Flex 96 Tip Rack 200 µL": "opentrons_flex_96_tiprack_200ul",
  "Opentrons Flex 96 Tip Rack 1000 µL": "opentrons_flex_96_tiprack_1000ul",
  "Opentrons OT-2 96 Filter Tip Rack 10 µL": "opentrons_96_filtertiprack_10ul",
  "Opentrons OT-2 96 Filter Tip Rack 20 µL": "opentrons_96_filtertiprack_20ul",
  "Opentrons OT-2 96 Filter Tip Rack 200 µL": "opentrons_96_filtertiprack_200ul",
  "Opentrons OT-2 96 Filter Tip Rack 1000 µL": "opentrons_96_filtertiprack_1000ul",
  "Opentrons OT-2 96 Tip Rack 10 µL": "opentrons_96_tiprack_10ul",
  "Opentrons OT-2 96 Tip Rack 20 µL": "opentrons_96_tiprack_20ul",
  "Opentrons OT-2 96 Tip Rack 300 µL": "opentrons_96_tiprack_300ul",
  "Opentrons OT-2 96 Tip Rack 1000 µL": "opentrons_96_tiprack_1000ul",
}


def official_tip_rack_identity(tip_rack: TipRack) -> Optional[LabwareIdentity]:
  """Look up a rack's official definition identity for its calibration data."""
  load_name = _OFFICIAL_TIP_RACKS.get(tip_rack.model or "")
  return LabwareIdentity("opentrons", load_name, 1) if load_name is not None else None


def build_tip_rack_definition(
  tip_rack: TipRack,
  tip: Optional[Tip] = None,
  load_name: Optional[str] = None,
  *,
  for_flex: bool = False,
  grip_distance_from_top: Optional[float] = None,
) -> Dict[str, Any]:
  """Build a tip-rack definition from PLR geometry.

  Defaults to OT-2's diagonal tip-spot diameter and empty group metadata.
  ``for_flex`` uses the spot width as the diameter and adds group metadata.
  Rotated racks or spots and tips seated below the rack's base are rejected.
  If omitted, the tip comes from A1 and the load name is a random UUID.
  ``grip_distance_from_top`` adds a gripper height to the definition.
  """
  _require_unrotated(tip_rack)
  if tip is None:
    tip = tip_rack.get_item("A1").make_tip()
  if load_name is None:
    load_name = uuid.uuid4().hex
  tip_spots = tip_rack.get_all_items()
  well_names = {spot.name: tip_rack.get_child_identifier(spot) for spot in tip_spots}
  for spot in tip_spots:
    _require_unrotated(spot)
    location = cast(Coordinate, spot.location)
    if location.z < 0:
      raise ValueError(
        f"'{tip_rack.name}' seats its tips {-location.z} mm BELOW its own base (spot "
        f"'{spot.name}'), which an Opentrons definition cannot express -- its well z must "
        "be non-negative, and the robot-server rejects the upload outright. Re-anchor the "
        "rack so its tip seats sit at or above its base, or give it an official Opentrons "
        "load name."
      )
  definition: Dict[str, Any] = {
    "schemaVersion": _SCHEMA_VERSION,
    "version": _VERSION,
    "namespace": _NAMESPACE,
    "metadata": {
      "displayName": tip_rack.name,
      "displayCategory": "tipRack",
      "displayVolumeUnits": "µL",
    },
    "brand": {"brand": "unknown"},
    "parameters": {
      "format": _format_from_grid(tip_rack.num_items_x, tip_rack.num_items_y),
      "isTiprack": True,
      "tipLength": tip.get_size_z(),
      "tipOverlap": tip.fitting_depth,
      "loadName": load_name,
      "isMagneticModuleCompatible": False,
    },
    "ordering": reshape_2d(
      [well_names[tip_spot.name] for tip_spot in tip_spots],
      (tip_rack.num_items_x, tip_rack.num_items_y),
    ),
    "cornerOffsetFromSlot": {
      "x": 0,
      "y": 0,
      "z": 0,
    },
    "dimensions": {
      "xDimension": tip_rack.get_absolute_size_x(),
      "yDimension": tip_rack.get_absolute_size_y(),
      "zDimension": tip_rack.get_absolute_size_z(),
    },
    "wells": {
      well_names[child.name]: {
        "depth": tip.get_size_z(),
        "x": cast(Coordinate, child.location).x + child.get_absolute_size_x() / 2,
        "y": cast(Coordinate, child.location).y + child.get_absolute_size_y() / 2,
        "z": cast(Coordinate, child.location).z,
        "shape": "circular",
        "diameter": child.get_size_x()
        if for_flex
        else math.hypot(
          child.get_absolute_size_x(),
          child.get_absolute_size_y(),
        ),
        "totalLiquidVolume": tip.maximal_volume,
      }
      for child in tip_spots
    },
    "groups": [
      {
        "wells": [well_names[tip_spot.name] for tip_spot in tip_spots],
        "metadata": (
          {"displayName": None, "displayCategory": "tipRack", "wellBottomShape": "flat"}
          if for_flex
          else {}
        ),
      }
    ],
  }
  if grip_distance_from_top is not None:
    definition["gripHeightFromLabwareBottom"] = max(
      0.0, tip_rack.get_absolute_size_z() - grip_distance_from_top
    )
  return definition


@dataclass(frozen=True)
class LabwareBinding:
  """A resource's server identity and location within one run."""

  resource: Resource
  labware_id: str
  slot: str
  identity: LabwareIdentity


class LabwareRegistry:
  """Record successful labware loads independently of the device's deck model."""

  def __init__(self, run: OpentronsRun, server_assigned_ids: bool = False) -> None:
    self._run = run
    self._bindings: Dict[int, LabwareBinding] = {}
    self._definitions: Dict[int, Tuple[Resource, LabwareIdentity]] = {}
    self._server_assigned_ids = server_assigned_ids

  def is_loaded(self, resource: Resource) -> bool:
    return id(resource) in self._bindings

  def get(self, resource: Resource) -> LabwareBinding:
    """Look up an existing binding without uploading, loading, or allocating an ID."""
    return self._bindings[id(resource)]

  async def load(
    self,
    resource: Resource,
    slot: str,
    identity: LabwareIdentity,
    definition: Optional[Dict[str, Any]] = None,
  ) -> None:
    """Load a resource and record its binding after confirmed success.

    Repeated loads at the same slot are a no-op. A changed slot is rejected
    until an explicit server-side relocation has been implemented.
    """
    self._run._require_active()
    if self.is_loaded(resource):
      binding = self.get(resource)
      if slot != binding.slot:
        raise ValueError(
          f"Labware {resource.name!r} is loaded in slot {binding.slot}; "
          f"it cannot be used in slot {slot} during the same run"
        )
      if identity != binding.identity:
        raise ValueError(
          f"Labware {resource.name!r} already has a different definition in this run"
        )
      return

    if definition is not None:
      identity = await self.define(resource, definition)
    if self._server_assigned_ids:
      labware_id = await self._run.load_labware(identity, slot, None, resource.name)
    else:
      labware_id = uuid.uuid4().hex
      await self._run.load_labware(identity, slot, labware_id, resource.name)
    self._bindings[id(resource)] = LabwareBinding(resource, labware_id, slot, identity)

  async def define(self, resource: Resource, definition: Dict[str, Any]) -> LabwareIdentity:
    """Upload a resource definition once per run, retaining its server identity."""
    self._run._require_active()
    cached = self.definition(resource)
    if cached is not None:
      return cached
    identity = await self._run.define_labware(definition)
    self._definitions[id(resource)] = (resource, identity)
    return identity

  def definition(self, resource: Resource) -> Optional[LabwareIdentity]:
    """Return a previously uploaded definition without communicating."""
    cached = self._definitions.get(id(resource))
    return cached[1] if cached is not None else None

  def record_location(self, resource: Resource, slot: str) -> None:
    """Update a binding after a confirmed server-side move."""
    self._bindings[id(resource)] = replace(self.get(resource), slot=slot)

  def remove(self, resource: Resource) -> None:
    """Forget a resource after its confirmed departure from the run's deck."""
    self._bindings.pop(id(resource), None)
    self._definitions.pop(id(resource), None)


def _format_from_grid(num_items_x: int, num_items_y: int) -> str:
  """The SBS formats the robot-server recognizes; anything else is irregular."""
  if (num_items_x, num_items_y) == (12, 8):
    return "96Standard"
  if (num_items_x, num_items_y) == (24, 16):
    return "384Standard"
  return "irregular"


def _require_unrotated(resource: Resource) -> None:
  """Refuse a rotated resource: an Opentrons definition cannot describe one.

  A schema-2 definition places its front-left-bottom corner at the slot's, and
  positions every well from that corner, with no rotation anywhere. PLR
  rotates about the resource's own origin without recentering, so baking the
  rotation into the well coordinates would still leave the labware body and
  the slot disagreeing.
  """
  rotation = resource.get_absolute_rotation()
  if (rotation.x, rotation.y, rotation.z) != (0, 0, 0):
    raise ValueError(
      f"'{resource.name}' has absolute rotation {rotation}. An Opentrons labware "
      "definition anchors wells to the slot's front-left corner and cannot express a "
      "rotated labware. Assign it unrotated, or give it an official Opentrons load name."
    )


def _container_bottom_shape(container: Container) -> str:
  """The schema's ``wellBottomShape`` for any container the builders can see.

  Only troughs and wells carry a bottom shape; a plain ``Container``,
  ``PetriDish``, ``Trash`` or ``Tube`` has none, so they get the schema's
  safest default.
  """
  if isinstance(container, Trough):
    return _TROUGH_BOTTOM_SHAPES[container.bottom_type]
  if isinstance(container, Well):
    return _WELL_BOTTOM_SHAPES[container.bottom_type]
  return "flat"


def _well_shape(well: Well) -> dict:
  if well.cross_section_type == CrossSectionType.RECTANGLE:
    return {
      "shape": "rectangular",
      "xDimension": well.get_size_x(),
      "yDimension": well.get_size_y(),
    }
  return {"shape": "circular", "diameter": well.get_size_x()}


def _plate_well(well: Well) -> dict:
  """One ``wells`` entry: the well's cavity floor, depth and footprint.

  ``depth`` is the well's own size_z, which in PLR already spans cavity floor
  to rim, so ``z + depth`` lands on the plate's top the way every shipped
  Opentrons plate definition does.
  """
  _require_unrotated(well)
  location = cast(Coordinate, well.location)
  return {
    "depth": well.get_size_z(),
    "x": location.x + well.get_size_x() / 2,
    "y": location.y + well.get_size_y() / 2,
    "z": location.z + well.material_z_thickness,
    "totalLiquidVolume": well.max_volume,
    **_well_shape(well),
  }


def build_plate_definition(plate: Plate, grip_distance_from_top: Optional[float] = None) -> dict:
  """Build a robot-server wellPlate definition from a PLR plate's geometry.

  Wells carry their real depth, volume, cross-section (circular or
  rectangular) and bottom shape so well-referencing commands (``touchTip``,
  ``liquidProbe``) get the true geometry. Wells are keyed by their PLR child
  identifier ("A1" style), matching the ``wellName`` the pipetting commands
  send. Each well's ``z`` is its CAVITY floor -- its PLR origin plus its wall
  thickness -- which puts the well top (``z + depth``) at the plate's real
  rim. ``gripHeightFromLabwareBottom`` is included only when
  ``grip_distance_from_top`` is given; without it the robot-server grips at
  its default mid-height.

  Raises:
    ValueError: If the plate or a well is rotated.
    NotImplementedError: If a well does not declare the wall thickness its
      cavity floor is measured from.
  """
  _require_unrotated(plate)
  wells = plate.get_all_items()
  well_names = [plate.get_child_identifier(well) for well in wells]
  definition: dict = {
    "schemaVersion": _SCHEMA_VERSION,
    "version": _VERSION,
    "namespace": _NAMESPACE,
    "metadata": {
      "displayName": plate.name,
      "displayCategory": "wellPlate",
      "displayVolumeUnits": "µL",
    },
    "brand": {"brand": "unknown"},
    "parameters": {
      "format": _format_from_grid(plate.num_items_x, plate.num_items_y),
      "isTiprack": False,
      "loadName": uuid.uuid4().hex,
      "isMagneticModuleCompatible": False,
    },
    "ordering": reshape_2d(well_names, (plate.num_items_x, plate.num_items_y)),
    "cornerOffsetFromSlot": {"x": 0, "y": 0, "z": 0},
    "dimensions": {
      "xDimension": plate.get_absolute_size_x(),
      "yDimension": plate.get_absolute_size_y(),
      "zDimension": plate.get_absolute_size_z(),
    },
    "wells": {plate.get_child_identifier(well): _plate_well(well) for well in wells},
    "groups": [
      {
        "wells": well_names,
        "metadata": {"wellBottomShape": _WELL_BOTTOM_SHAPES[wells[0].bottom_type]},
      }
    ],
  }
  if grip_distance_from_top is not None:
    definition["gripHeightFromLabwareBottom"] = max(
      0.0, plate.get_absolute_size_z() - grip_distance_from_top
    )
  return definition


def build_container_definition(
  container: Container, grip_distance_from_top: Optional[float] = None
) -> dict:
  """Build a robot-server reservoir definition from a PLR container's geometry.

  A container (e.g. a trough) is a single cavity, so the definition has one
  well "A1" whose rectangular footprint spans the whole container, with depth
  and volume from the container's geometry. The ``centerMultichannelOnWells``
  quirk matches every shipped Opentrons 1-well reservoir: the engine centers
  a multi-channel nozzle array on the cavity itself, so ops send no manual
  centering offsets.

  The cavity floor sits the container's wall thickness above its base, and the
  cavity depth shrinks by the same amount, so the well's top stays at the
  container's real rim (``z + depth == zDimension``, as on every shipped
  Opentrons reservoir). The well's x/y footprint is the container's outer
  bounding box in the deck frame; PLR carries no cavity x/y to narrow it with.

  Raises:
    NotImplementedError: If the container does not declare the wall thickness its
      cavity floor is measured from.
  """
  size_x = container.get_absolute_size_x()
  size_y = container.get_absolute_size_y()
  size_z = container.get_absolute_size_z()
  definition: dict = {
    "schemaVersion": _SCHEMA_VERSION,
    "version": _VERSION,
    "namespace": _NAMESPACE,
    "metadata": {
      "displayName": container.name,
      "displayCategory": "reservoir",
      "displayVolumeUnits": "µL",
    },
    "brand": {"brand": "unknown"},
    "parameters": {
      "format": "irregular",
      "quirks": ["centerMultichannelOnWells"],
      "isTiprack": False,
      "loadName": uuid.uuid4().hex,
      "isMagneticModuleCompatible": False,
    },
    "ordering": [["A1"]],
    "cornerOffsetFromSlot": {"x": 0, "y": 0, "z": 0},
    "dimensions": {"xDimension": size_x, "yDimension": size_y, "zDimension": size_z},
    "wells": {
      "A1": {
        "depth": size_z - container.material_z_thickness,
        "x": size_x / 2,
        "y": size_y / 2,
        "z": container.material_z_thickness,
        "shape": "rectangular",
        "xDimension": size_x,
        "yDimension": size_y,
        "totalLiquidVolume": container.max_volume,
      }
    },
    "groups": [
      {"wells": ["A1"], "metadata": {"wellBottomShape": _container_bottom_shape(container)}}
    ],
  }
  if grip_distance_from_top is not None:
    definition["gripHeightFromLabwareBottom"] = max(0.0, size_z - grip_distance_from_top)
  return definition


def build_movable_labware_definition(
  resource: Resource, grip_distance_from_top: Optional[float] = None
) -> dict:
  """Build a minimal single-well stub definition for gripper moves of any resource.

  The stub is not pipettable (one fake well, zero depth and volume); it exists
  so the robot-server can gripper-move labware it has no real definition for.
  ``gripHeightFromLabwareBottom`` is included only when
  ``grip_distance_from_top`` is given; without it the robot-server grips at
  its default mid-height.
  """
  size_x = resource.get_absolute_size_x()
  size_y = resource.get_absolute_size_y()
  size_z = resource.get_absolute_size_z()
  definition: dict = {
    "schemaVersion": _SCHEMA_VERSION,
    "version": _VERSION,
    "namespace": _NAMESPACE,
    "metadata": {
      "displayName": resource.name,
      "displayCategory": "wellPlate",
      "displayVolumeUnits": "µL",
    },
    "brand": {"brand": "unknown"},
    "parameters": {
      "format": "irregular",
      "isTiprack": False,
      "loadName": uuid.uuid4().hex,
      "isMagneticModuleCompatible": False,
    },
    "ordering": [["A1"]],
    "cornerOffsetFromSlot": {"x": 0, "y": 0, "z": 0},
    "dimensions": {"xDimension": size_x, "yDimension": size_y, "zDimension": size_z},
    "wells": {
      "A1": {
        "depth": 0,
        "x": size_x / 2,
        "y": size_y / 2,
        "z": 0,
        "shape": "circular",
        "diameter": 5,
        "totalLiquidVolume": 0,
      }
    },
    "groups": [{"wells": ["A1"], "metadata": {"wellBottomShape": "flat"}}],
    "gripperOffsets": {
      "default": {
        "pickUpOffset": {"x": 0, "y": 0, "z": 0},
        "dropOffset": {"x": 0, "y": 0, "z": 0},
      }
    },
  }
  if grip_distance_from_top is not None:
    definition["gripHeightFromLabwareBottom"] = max(0.0, size_z - grip_distance_from_top)
  return definition
