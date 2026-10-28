"""Definitions from `Resource` objects: what a labware factory builds, as a store creates it."""

from __future__ import annotations

from typing import Optional

from google.protobuf.struct_pb2 import Struct

from pylabrobot.resources.carrier import Carrier, PlateHolder
from pylabrobot.resources.container import Container
from pylabrobot.resources.container_rack import ContainerRack
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.hamilton.core_gripper_tools import HamiltonCoreGripperTool
from pylabrobot.resources.hamilton.core_grippers import HamiltonCoreGrippers
from pylabrobot.resources.hamilton.hamilton_decks import HamiltonDeck
from pylabrobot.resources.hamilton.tip_creators import HamiltonTip
from pylabrobot.resources.head_tool import HeadTool
from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.lid import Lid
from pylabrobot.resources.manipulator import LinkBody
from pylabrobot.resources.n_channel_pipettes import NChannelPipette, TipMountingShaft
from pylabrobot.resources.petri_dish import PetriDish, PetriDishHolder
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.plate_adapter import PlateAdapter
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.resource_stack import ResourceStack
from pylabrobot.resources.server.tree import to_rotation_message, to_vec3
from pylabrobot.resources.server.v1 import types_pb2 as pb
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import (
  EmbeddedTipRack,
  NestedTipRack,
  StandingTipRack,
  TipRack,
  TipSpot,
)
from pylabrobot.resources.tip_rack_holder import EmbeddedTipRackHolder
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.trough import Trough
from pylabrobot.resources.tube import Tube
from pylabrobot.resources.tube_rack import TubeRack
from pylabrobot.resources.volume_tracker import VolumeTracker
from pylabrobot.resources.well import Well

K = pb.ResourceKind

_BOTTOM_TYPES = {
  "flat": pb.WELL_BOTTOM_TYPE_FLAT,
  "U": pb.WELL_BOTTOM_TYPE_U,
  "V": pb.WELL_BOTTOM_TYPE_V,
  "unknown": pb.WELL_BOTTOM_TYPE_UNSPECIFIED,
}
_CROSS_SECTION_TYPES = {
  "circle": pb.CROSS_SECTION_TYPE_CIRCLE,
  "rectangle": pb.CROSS_SECTION_TYPE_RECTANGLE,
}
_STACK_DIRECTIONS = {
  "x": pb.STACK_DIRECTION_X,
  "y": pb.STACK_DIRECTION_Y,
  "z": pb.STACK_DIRECTION_Z,
}
_PICKUP_METHODS = {
  "OUT_OF_RACK": pb.TIP_PICKUP_METHOD_OUT_OF_RACK,
  "OUT_OF_WASH_LIQUID": pb.TIP_PICKUP_METHOD_OUT_OF_WASH_LIQUID,
}


def kind_of(resource: Resource) -> pb.ResourceKind:
  """The kind a resource is stored as: the most specific one its class is."""
  kinds = [
    (TipSpot, K.RESOURCE_KIND_TIP_SPOT),
    (Tip, K.RESOURCE_KIND_TIP),
    (HeadTool, K.RESOURCE_KIND_HEAD_TOOL),
    (Lid, K.RESOURCE_KIND_LID),
    (PlateAdapter, K.RESOURCE_KIND_PLATE_ADAPTER),
    (ResourceStack, K.RESOURCE_KIND_RESOURCE_STACK),
    (Well, K.RESOURCE_KIND_WELL),
    (Trough, K.RESOURCE_KIND_TROUGH),
    (Tube, K.RESOURCE_KIND_TUBE),
    (PetriDish, K.RESOURCE_KIND_PETRI_DISH),
    (Trash, K.RESOURCE_KIND_TRASH),
    (Container, K.RESOURCE_KIND_CONTAINER),
    (PetriDishHolder, K.RESOURCE_KIND_PETRI_DISH_HOLDER),
    (Plate, K.RESOURCE_KIND_PLATE),
    (TipRack, K.RESOURCE_KIND_TIP_RACK),
    (TubeRack, K.RESOURCE_KIND_TUBE_RACK),
    (ContainerRack, K.RESOURCE_KIND_CONTAINER_RACK),
    (EmbeddedTipRackHolder, K.RESOURCE_KIND_EMBEDDED_TIP_RACK_HOLDER),
    (PlateHolder, K.RESOURCE_KIND_PLATE_HOLDER),
    (ResourceHolder, K.RESOURCE_KIND_RESOURCE_HOLDER),
    (Carrier, K.RESOURCE_KIND_CARRIER),
    (Deck, K.RESOURCE_KIND_DECK),
    (TipMountingShaft, K.RESOURCE_KIND_TIP_MOUNTING_SHAFT),
    (NChannelPipette, K.RESOURCE_KIND_N_CHANNEL_PIPETTE),
    (MechanicalGripper, K.RESOURCE_KIND_MECHANICAL_GRIPPER),
    (LinkBody, K.RESOURCE_KIND_LINK_BODY),
  ]
  return next((kind for cls, kind in kinds if isinstance(resource, cls)), K.RESOURCE_KIND_RESOURCE)


def _identifier(resource: Resource) -> str:
  """A resource's identifier in its itemized parent, "A1"; empty when it is not an item."""
  parent = resource.parent
  if not isinstance(parent, ItemizedResource):
    return ""
  try:
    return parent.get_child_identifier(resource)
  except ValueError:
    return ""


def _volume_state(tracker: VolumeTracker) -> pb.VolumeState:
  return pb.VolumeState(
    volume=tracker.volume,
    pending_volume=tracker.pending_volume,
    tracking_enabled=not tracker.is_disabled,
  )


def _container_spec(container: Container) -> pb.ContainerSpec:
  spec = pb.ContainerSpec(max_volume=container.max_volume)
  if container._material_z_thickness is not None:
    spec.material_z_thickness = container._material_z_thickness
  for height, volume in sorted((container.height_volume_data or {}).items()):
    spec.height_volume_data.add(height=height, volume=volume)
  for flb, brt in container.no_go_zones:
    spec.no_go_zones.add(min=to_vec3(flb), max=to_vec3(brt))
  # A tube is given a bottom type, but does not keep it.
  if isinstance(container, (Well, Trough)):
    spec.bottom_type = _BOTTOM_TYPES[container.bottom_type.value]
  if isinstance(container, Well):
    spec.cross_section_type = _CROSS_SECTION_TYPES[container.cross_section_type.value]
  if isinstance(container, Trough):
    spec.through_base_to_container_base = container.through_base_to_container_base
  return spec


def _tip_rack_spec(rack: TipRack) -> pb.TipRackSpec:
  spec = pb.TipRackSpec(style=pb.TIP_RACK_STYLE_UNSPECIFIED)
  if rack._frame_height is not None:
    spec.frame_height = rack._frame_height
  if isinstance(rack, EmbeddedTipRack):
    spec.style = pb.TIP_RACK_STYLE_EMBEDDED
    spec.sinking_depth = rack.sinking_depth
  elif isinstance(rack, StandingTipRack):
    spec.style = (
      pb.TIP_RACK_STYLE_NESTED if isinstance(rack, NestedTipRack) else pb.TIP_RACK_STYLE_STANDING
    )
    if rack.stacking_z_height is not None:
      spec.stacking_z_height = rack.stacking_z_height
  return spec


def _set_tool_specs(attributes: pb.ResourceAttributes, tool: HeadTool) -> None:
  attributes.head_tool.fitting_depth = tool.fitting_depth
  if tool.is_conductive is not None:
    attributes.head_tool.is_conductive = tool.is_conductive
  if tool.pick_up_location is not None:
    attributes.head_tool.pick_up_location.CopyFrom(to_vec3(tool.pick_up_location))
  if isinstance(tool, HamiltonCoreGripperTool):
    attributes.head_tool.grip_line_height = tool.grip_line_height
  if isinstance(tool, Tip):
    attributes.tip.diameter = tool.get_size_x()
    attributes.tip.has_filter = tool.has_filter
    attributes.tip.maximal_volume = tool.maximal_volume
    attributes.tip.nominal_volume = tool.nominal_volume
    if tool.has_collar_height:
      attributes.tip.collar_height = tool.collar_height
    if isinstance(tool, HamiltonTip):
      attributes.tip.hamilton.tip_size = tool.tip_size.name
      attributes.tip.hamilton.pickup_method = _PICKUP_METHODS[tool.pickup_method.name]


def _attributes(resource: Resource) -> pb.ResourceAttributes:
  """What a resource is and where it is in its parent, with its kind's spec."""
  kind = kind_of(resource)
  attributes = pb.ResourceAttributes(
    name=resource.name,
    kind=kind,
    model=resource.model or "",
    category=resource.category or "",
    identifier=_identifier(resource),
    rotation=to_rotation_message(resource.rotation),
  )
  if kind != K.RESOURCE_KIND_RESOURCE_STACK:
    attributes.size.CopyFrom(
      pb.Vec3(x=resource.get_size_x(), y=resource.get_size_y(), z=resource.get_size_z())
    )
  if resource.location is not None:
    attributes.location.CopyFrom(to_vec3(resource.location))
  if resource.preferred_pickup_location is not None:
    attributes.preferred_pickup_location.CopyFrom(to_vec3(resource.preferred_pickup_location))
  if resource.barcode is not None:
    attributes.barcode.CopyFrom(
      pb.Barcode(
        data=resource.barcode.data,
        symbology=resource.barcode.symbology,
        position_on_resource=resource.barcode.position_on_resource,
      )
    )
  if resource.metadata:
    metadata = Struct()
    metadata.update(resource.metadata)
    attributes.metadata.CopyFrom(metadata)

  if isinstance(resource, Container):
    attributes.container.CopyFrom(_container_spec(resource))
  if isinstance(resource, ItemizedResource):
    try:
      attributes.itemized.CopyFrom(
        pb.ItemizedSpec(num_items_x=resource.num_items_x, num_items_y=resource.num_items_y)
      )
    except ValueError:
      pass  # Its items are not a full grid, so it has no grid size; its items still say where.
  if isinstance(resource, Plate):
    attributes.plate.plate_type = resource.plate_type
    if resource.stacking_z_height is not None:
      attributes.plate.stacking_z_height = resource.stacking_z_height
  if isinstance(resource, TipRack):
    attributes.tip_rack.CopyFrom(_tip_rack_spec(resource))
  if isinstance(resource, HeadTool):
    _set_tool_specs(attributes, resource)
  if isinstance(resource, TipSpot):
    attributes.tip_spot.prototype_tip.CopyFrom(_attributes(resource._make_tip_func(resource.name)))
  if isinstance(resource, Lid):
    attributes.lid.nesting_z_height = resource.nesting_z_height
  if isinstance(resource, ResourceHolder):
    attributes.holder.child_location.CopyFrom(to_vec3(resource.child_location))
    attributes.holder.capacity = 1
    if isinstance(resource, PlateHolder):
      attributes.holder.pedestal_size_z = resource.pedestal_size_z
  if isinstance(resource, HamiltonCoreGrippers):
    attributes.holder.channel_y_centers.extend(
      [resource.back_channel_y_center, resource.front_channel_y_center]
    )
  if isinstance(resource, ResourceStack):
    attributes.stack.direction = _STACK_DIRECTIONS[resource.direction]
  if isinstance(resource, PlateAdapter):
    attributes.plate_adapter.CopyFrom(
      pb.PlateAdapterSpec(
        dx=resource.dx,
        dy=resource.dy,
        dz=resource.dz,
        adapter_hole_size_x=resource.adapter_hole_size_x,
        adapter_hole_size_y=resource.adapter_hole_size_y,
        adapter_hole_size_z=resource.adapter_hole_size_z,
        adapter_hole_dx=resource.adapter_hole_dx,
        adapter_hole_dy=resource.adapter_hole_dy,
        plate_z_offset=resource.plate_z_offset,
      )
    )
  if isinstance(resource, TipMountingShaft):
    attributes.tip_mounting_shaft.tip_pickup_mode = resource.tip_pickup_mode
    attributes.tip_mounting_shaft.cross_section_type = pb.CROSS_SECTION_TYPE_CIRCLE
  if isinstance(resource, NChannelPipette):
    attributes.n_channel_pipette.reference_point.CopyFrom(to_vec3(resource.reference_point))
    attributes.n_channel_pipette.independent_channel_actuation = (
      resource.independent_channel_actuation
    )
  if isinstance(resource, MechanicalGripper):
    low, high = resource.jaw_range
    attributes.mechanical_gripper.CopyFrom(
      pb.MechanicalGripperSpec(
        proximal_joint=to_vec3(resource.proximal_joint),
        tool_center_point=to_vec3(resource._tool_center_point),
        jaw_width_min=low,
        jaw_width_max=high,
        body=resource.body.name,
        fingers=[finger.name for finger in resource.fingers],
        pads=[pad.name for pad in resource.pads],
        jaws=[jaw.name for jaw in resource.jaws],
      )
    )
  elif isinstance(resource, LinkBody):
    attributes.link_body.proximal_joint.CopyFrom(to_vec3(resource.proximal_joint))
    if resource.distal_joint is not None:
      attributes.link_body.distal_joint.CopyFrom(to_vec3(resource.distal_joint))
  if isinstance(resource, HamiltonDeck):
    first, second = resource.track_to_location(1), resource.track_to_location(2)
    attributes.deck.hamilton.CopyFrom(
      pb.HamiltonDeckSpec(
        num_tracks=resource.num_tracks,
        track_origin=to_vec3(first),
        track_pitch=round(second.x - first.x, 4),
      )
    )
  return attributes


def _state(resource: Resource) -> Optional[pb.ResourceState]:
  """A resource's state, or None when it has none."""
  if isinstance(resource, (Container, Tip)):
    return pb.ResourceState(volume=_volume_state(resource.tracker))
  if isinstance(resource, TipSpot):
    return pb.ResourceState(
      tip_tracking_enabled=resource.tip_tracking_enabled,
      tips_made=resource._tip_counter,
    )
  if isinstance(resource, MechanicalGripper):
    return pb.ResourceState(jaw_width=resource.jaw_width)
  return None


def definition_from_resource(resource: Resource) -> pb.ResourceDefinition:
  """The definition of a resource and everything under it, with its state, as it is now.

  A container whose volume and height convert only through Python functions is defined without
  them: a store converts through `height_volume_data` alone.
  """
  definition = pb.ResourceDefinition(attributes=_attributes(resource))
  state = _state(resource)
  if state is not None:
    definition.initial_state.CopyFrom(state)
  definition.children.extend(definition_from_resource(child) for child in resource.children)
  return definition
