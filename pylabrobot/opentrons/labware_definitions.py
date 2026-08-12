"""Builders for custom Opentrons labware definitions from PLR resource geometry.

Labware without an official Opentrons definition (third-party plates, troughs,
lids) cannot ``loadLabware`` by name. The pure functions here (no I/O) build a
robot-server labware definition dict from the PLR resource's own geometry;
:class:`~pylabrobot.opentrons.flex.OpentronsFlex` uploads the dict to
``POST /runs/{run_id}/labware_definitions`` and then loads the labware by the
uploaded definition's ``namespace``/``loadName``/``version``.

Frame note: PLR and Opentrons schema-2 definitions both anchor labware at the
front-left-bottom corner of the slot (every shipped Opentrons definition
carries ``cornerOffsetFromSlot`` of zero), so PLR geometry carries over
directly -- well positions included.
"""

import hashlib
import re
from typing import Optional, Tuple, cast

from pylabrobot.resources import (
  Container,
  Coordinate,
  CrossSectionType,
  Plate,
  Resource,
  TipRack,
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


def _definition_load_name(resource: Resource) -> str:
  """Sanitized resource name plus a short digest of the raw name.

  Opentrons load names must match ``^[a-z0-9._]+$`` while PLR names are
  unrestricted, so distinct names can sanitize identically; the digest keeps
  their definitions from silently sharing one ``definitionUri``.
  """
  sanitized = re.sub(r"[^a-z0-9._]", "_", resource.name.lower())
  digest = hashlib.sha1(resource.name.encode()).hexdigest()[:6]
  return f"{sanitized}_{digest}"


def _format_from_grid(num_items_x: int, num_items_y: int) -> str:
  """The SBS formats the robot-server recognizes; anything else is irregular."""
  if (num_items_x, num_items_y) == (12, 8):
    return "96Standard"
  if (num_items_x, num_items_y) == (24, 16):
    return "384Standard"
  return "irregular"


def container_cavity_footprint(container: Container) -> Tuple[float, float]:
  """The cavity's x/y footprint in the deck frame, as the robot sees it.

  Rotation-aware: a rotated container (or one under a rotated parent) presents
  its bounding box to the robot, which has no notion of PLR's rotation. Ops
  that reason about whether a nozzle array fits the cavity must read this
  rather than the container's own ``get_size_x``/``get_size_y``, so the
  guard and the uploaded definition below can never disagree.
  """
  return container.get_absolute_size_x(), container.get_absolute_size_y()


def _well_shape(well: Well) -> dict:
  if well.cross_section_type == CrossSectionType.RECTANGLE:
    return {
      "shape": "rectangular",
      "xDimension": well.get_absolute_size_x(),
      "yDimension": well.get_absolute_size_y(),
    }
  return {"shape": "circular", "diameter": well.get_absolute_size_x()}


def build_plate_definition(plate: Plate, grip_distance_from_top: Optional[float] = None) -> dict:
  """Build a robot-server wellPlate definition from a PLR plate's geometry.

  Wells carry their real depth, volume, cross-section (circular or
  rectangular) and bottom shape so well-referencing commands (``touchTip``,
  ``liquidProbe``) get the true geometry. Wells are keyed by their PLR child
  identifier ("A1" style), matching the ``wellName`` the pipetting commands
  send. ``gripHeightFromLabwareBottom`` is included only when
  ``grip_distance_from_top`` is given; without it the robot-server grips at
  its default mid-height.
  """
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
      "loadName": _definition_load_name(plate),
      "isMagneticModuleCompatible": False,
    },
    "ordering": reshape_2d(well_names, (plate.num_items_x, plate.num_items_y)),
    "cornerOffsetFromSlot": {"x": 0, "y": 0, "z": 0},
    "dimensions": {
      "xDimension": plate.get_absolute_size_x(),
      "yDimension": plate.get_absolute_size_y(),
      "zDimension": plate.get_absolute_size_z(),
    },
    "wells": {
      plate.get_child_identifier(well): {
        "depth": well.get_absolute_size_z(),
        "x": cast(Coordinate, well.location).x + well.get_absolute_size_x() / 2,
        "y": cast(Coordinate, well.location).y + well.get_absolute_size_y() / 2,
        "z": cast(Coordinate, well.location).z,
        "totalLiquidVolume": well.max_volume,
        **_well_shape(well),
      }
      for well in wells
    },
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


def build_tip_rack_definition(
  tip_rack: TipRack, grip_distance_from_top: Optional[float] = None
) -> dict:
  """Build a robot-server tipRack definition from a PLR tip rack's geometry.

  Tip length and overlap come from the rack's A1 prototype tip, so the robot
  computes the same pickup z the PLR tip model implies.
  """
  tip = tip_rack.get_item("A1").make_tip()
  spot_names = [tip_rack.get_child_identifier(spot) for spot in tip_rack.get_all_items()]
  definition: dict = {
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
      "tipLength": tip.total_tip_length,
      "tipOverlap": tip.fitting_depth,
      "loadName": _definition_load_name(tip_rack),
      "isMagneticModuleCompatible": False,
    },
    "ordering": reshape_2d(spot_names, (tip_rack.num_items_x, tip_rack.num_items_y)),
    "cornerOffsetFromSlot": {"x": 0, "y": 0, "z": 0},
    "dimensions": {
      "xDimension": tip_rack.get_absolute_size_x(),
      "yDimension": tip_rack.get_absolute_size_y(),
      "zDimension": tip_rack.get_absolute_size_z(),
    },
    "wells": {
      tip_rack.get_child_identifier(spot): {
        "depth": spot.get_absolute_size_z(),
        "x": cast(Coordinate, spot.location).x + spot.get_absolute_size_x() / 2,
        "y": cast(Coordinate, spot.location).y + spot.get_absolute_size_y() / 2,
        "z": cast(Coordinate, spot.location).z,
        # Tip-rack wells stay circular regardless of the PLR cross-section:
        # the engine rejects non-circular tip-rack wells (LabwareIsNotTipRackError).
        "shape": "circular",
        "diameter": spot.get_absolute_size_x(),
        "totalLiquidVolume": tip.maximal_volume,
      }
      for spot in tip_rack.get_all_items()
    },
    "groups": [
      {
        "wells": spot_names,
        "metadata": {
          "displayName": None,
          "displayCategory": "tipRack",
          "wellBottomShape": "flat",  # required even for tip racks
        },
      }
    ],
  }
  if grip_distance_from_top is not None:
    definition["gripHeightFromLabwareBottom"] = max(
      0.0, tip_rack.get_absolute_size_z() - grip_distance_from_top
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
  """
  size_x, size_y = container_cavity_footprint(container)
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
      "loadName": _definition_load_name(container),
      "isMagneticModuleCompatible": False,
    },
    "ordering": [["A1"]],
    "cornerOffsetFromSlot": {"x": 0, "y": 0, "z": 0},
    "dimensions": {"xDimension": size_x, "yDimension": size_y, "zDimension": size_z},
    "wells": {
      "A1": {
        "depth": size_z,
        "x": size_x / 2,
        "y": size_y / 2,
        "z": 0,
        "shape": "rectangular",
        "xDimension": size_x,
        "yDimension": size_y,
        "totalLiquidVolume": container.max_volume,
      }
    },
    "groups": [{"wells": ["A1"], "metadata": {"wellBottomShape": "flat"}}],
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
      "loadName": _definition_load_name(resource),
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
