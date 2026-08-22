"""Builders for custom Opentrons labware definitions from PLR resource geometry.

Labware without an official Opentrons definition (third-party plates, troughs,
lids) cannot ``loadLabware`` by name. The pure functions here (no I/O) build a
robot-server labware definition dict from the PLR resource's own geometry;
:class:`~pylabrobot.opentrons.flex.OpentronsFlex` uploads the dict to
``POST /runs/{run_id}/labware_definitions`` and then loads the labware by the
uploaded definition's ``namespace``/``loadName``/``version``.

Frame note: PLR and Opentrons schema-2 definitions both anchor labware at the
front-left-bottom corner of the slot (every shipped Opentrons definition
carries ``cornerOffsetFromSlot`` of zero), so PLR positions carry over
directly -- for UNROTATED labware. Schema 2 has no way to express a rotated
labware, so the plate and tip-rack builders refuse one rather than upload a
definition whose wells and footprint disagree.

Height note: an Opentrons well's ``z`` is the CAVITY floor ("center-bottom of
well" in the schema), while a PLR well's/container's own origin is its OUTER
bottom. The two differ by the labware's wall thickness
(``Container.material_z_thickness``), so the pipettable builders add it --
without that, the default 1 mm bottom clearance aims the tip INTO the plastic.
(The movable stub is exempt: its one fake well is somewhere to grip, not to
pipette, which is why pipetting ops refuse stub-loaded labware outright.)

The builders are pure: they raise ``ValueError`` for geometry no Opentrons
definition can describe, and :class:`~pylabrobot.opentrons.flex.OpentronsFlex`
turns that into an ``OpentronsError`` before any wire command.
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
  Tip,
  TipRack,
  TipSpot,
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


def container_footprint(container: Container) -> Tuple[float, float]:
  """The container's x/y bounding box in the deck frame, as the robot sees it.

  This is the OUTER footprint, an upper bound on the cavity: PLR containers
  carry their overall size, not their cavity's, so a trough's real cavity is
  narrower than what this returns by the wall thickness on each side. Ops that
  reason about whether a nozzle array fits, and the uploaded definition's own
  well, both read this so they can never disagree.

  Rotation-aware: a rotated container (or one under a rotated parent) presents
  its bounding box to the robot, which has no notion of PLR's rotation, so
  this must be read rather than the container's own
  ``get_size_x``/``get_size_y``.
  """
  return container.get_absolute_size_x(), container.get_absolute_size_y()


def _cavity_floor_z(container: Container) -> float:
  """The height of the cavity floor above the container's own bottom.

  ``Container.material_z_thickness`` is optional in PLR and raises when it was
  never declared. There is no safe default: falling back to zero would put the
  Opentrons well floor at the labware's outer base and aim every default
  aspirate a wall thickness INTO the plastic, so an undeclared thickness is
  refused instead.
  """
  try:
    return container.material_z_thickness
  except NotImplementedError as e:
    raise ValueError(
      f"'{container.name}' does not declare material_z_thickness, so the height of its "
      "cavity floor above its base is unknown. An Opentrons definition anchors liquid "
      "ops at that floor, so building one would aim them at the labware's outer base "
      "instead. Give the resource a material_z_thickness, or an official Opentrons "
      "load name."
    ) from e


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
    "z": location.z + _cavity_floor_z(well),
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
    ValueError: If the plate (or a well) is rotated, or a well does not
      declare the wall thickness its cavity floor is measured from.
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


def _tip_rack_well(tip_rack: TipRack, spot: TipSpot, tip: Tip) -> dict:
  """One ``wells`` entry describing the tip seated in ``spot``.

  A ``TipSpot`` has no height of its own (PLR leaves its size_z at zero), so
  the depth comes from the prototype tip the rack's ``tipLength`` already
  reports; using the spot's box would send a zero-depth well and collapse the
  pickup target onto the rack's base.
  """
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
  return {
    "depth": tip.total_tip_length,
    "x": location.x + spot.get_size_x() / 2,
    "y": location.y + spot.get_size_y() / 2,
    "z": location.z,
    # Tip-rack wells stay circular regardless of the PLR cross-section:
    # the engine rejects non-circular tip-rack wells (LabwareIsNotTipRackError).
    "shape": "circular",
    "diameter": spot.get_size_x(),
    "totalLiquidVolume": tip.maximal_volume,
  }


def build_tip_rack_definition(
  tip_rack: TipRack, grip_distance_from_top: Optional[float] = None
) -> dict:
  """Build a robot-server tipRack definition from a PLR tip rack's geometry.

  Tip length and overlap come from the rack's A1 prototype tip, so the robot
  computes the same pickup z the PLR tip model implies. A tip-rack well
  describes the seated TIP, not the spot's own box: ``z`` is where the tip
  end sits and ``depth`` is the tip's length, so ``z + depth`` is the tip's
  top -- the height a ``pickUpTip`` (top origin) descends to.

  Raises:
    ValueError: If the rack (or a spot) is rotated, or its tips reach below
      the rack's own base, which schema 2 cannot express.
  """
  _require_unrotated(tip_rack)
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
      tip_rack.get_child_identifier(spot): _tip_rack_well(tip_rack, spot, tip)
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

  The cavity floor sits the container's wall thickness above its base, and the
  cavity depth shrinks by the same amount, so the well's top stays at the
  container's real rim (``z + depth == zDimension``, as on every shipped
  Opentrons reservoir). The well's x/y footprint is the container's OUTER one
  (see ``container_footprint``): PLR carries no cavity x/y to narrow it with.

  Raises:
    ValueError: If the container does not declare the wall thickness its
      cavity floor is measured from.
  """
  size_x, size_y = container_footprint(container)
  size_z = container.get_absolute_size_z()
  floor_z = _cavity_floor_z(container)
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
        "depth": size_z - floor_z,
        "x": size_x / 2,
        "y": size_y / 2,
        "z": floor_z,
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
