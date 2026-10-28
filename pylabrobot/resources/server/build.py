"""Definitions written in Python, for what has no labware factory: a device's own parts, say.

One builder per kind, each taking its kind's spec as plain keyword arguments:

  well = build.well("well", Coordinate(8, 8, 10), max_volume=360)
  plate = build.plate(
    "plate",
    Coordinate(127.76, 85.48, 14.2),
    plate_type="skirted",
    num_items_x=12,
    num_items_y=8,
    item=well,
    item_offset=Coordinate(10, 7, 1),
    item_dx=9,
    item_dy=9,
  )
  channel = build.pipette_channel(
    "channel_0",
    Coordinate(9, 9, 100),
    reference_point=Coordinate(4.5, 4.5, 0),
    location=Coordinate(10, 400, 20),
    children=[shaft],
  )

Every builder takes a name and a size, and, by keyword, what any resource has (`Common`): its
location in its parent, rotation, category, model, identifier, metadata, children and initial
state. `resource` builds any kind, its spec given as the `ResourceAttributes` field it is.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence, Tuple, TypedDict, Union

from google.protobuf.struct_pb2 import Struct
from typing_extensions import Unpack

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.server.tree import to_rotation_message, to_vec3
from pylabrobot.resources.server.v1 import types_pb2 as pb

K = pb.ResourceKind


def vec3(coordinate: Coordinate) -> pb.Vec3:
  return to_vec3(coordinate)


class Common(TypedDict, total=False):
  """What any resource's builder takes besides its name and size.

  Attributes:
    location: Where it is in its parent; unset on a root.
    rotation: How it is turned in its parent.
    category: PLR's category, "plate_holder" say.
    model: The model it is.
    identifier: Its identifier in an itemized parent, "A1".
    metadata: Anything else about it.
    children: What hangs from it, in order.
    state: What it starts with: a volume, tracking, a jaw width.
  """

  location: Coordinate
  rotation: Rotation
  category: str
  model: str
  identifier: str
  metadata: Mapping[str, Any]
  children: Sequence[pb.ResourceDefinition]
  state: pb.ResourceState


def resource(
  name: str,
  size: Coordinate,
  kind: pb.ResourceKind = pb.RESOURCE_KIND_RESOURCE,
  location: Optional[Coordinate] = None,
  rotation: Optional[Rotation] = None,
  category: str = "",
  model: str = "",
  identifier: str = "",
  metadata: Optional[Mapping[str, Any]] = None,
  children: Sequence[pb.ResourceDefinition] = (),
  state: Optional[pb.ResourceState] = None,
  **specs: Any,
) -> pb.ResourceDefinition:
  """A resource of any kind and what hangs from it.

  Args:
    name: Its name; in a model, `{name}` in it becomes the instance's.
    size: Its size along its own axes.
    kind: What it is.
    location: Where it is in its parent; unset on a root.
    rotation: How it is turned in its parent.
    category: PLR's category, "plate_holder" say.
    model: The model it is.
    identifier: Its identifier in an itemized parent, "A1".
    metadata: Anything else about it.
    children: What hangs from it, in order.
    state: What it starts with: a volume, tracking, a jaw width.
    **specs: Its kind's spec, by the `ResourceAttributes` field name: `holder=pb.HolderSpec(...)`.
  """
  attributes = pb.ResourceAttributes(
    name=name,
    kind=kind,
    model=model,
    category=category,
    size=to_vec3(size),
    identifier=identifier,
    rotation=to_rotation_message(rotation or Rotation()),
    **specs,
  )
  if location is not None:
    attributes.location.CopyFrom(to_vec3(location))
  if metadata:
    struct = Struct()
    struct.update(metadata)
    attributes.metadata.CopyFrom(struct)
  definition = pb.ResourceDefinition(attributes=attributes, children=children)
  if state is not None:
    definition.initial_state.CopyFrom(state)
  return definition


def _optional_vec3(coordinate: Optional[Coordinate]) -> Optional[pb.Vec3]:
  return None if coordinate is None else to_vec3(coordinate)


# -- the frame things stand on ---------------------------------------------------------------------


def deck(
  name: str,
  size: Coordinate,
  num_tracks: Optional[int] = None,
  track_origin: Optional[Coordinate] = None,
  track_pitch: float = 0,
  **common: Unpack[Common],
) -> pb.ResourceDefinition:
  """A deck.

  Args:
    name: Its name.
    size: Its size.
    num_tracks: A Hamilton deck's tracks; unset on any other deck.
    track_origin: Where a Hamilton deck's track 1 is.
    track_pitch: How far apart a Hamilton deck's tracks are, in x.
    **common: See `Common`.
  """
  spec = pb.DeckSpec()
  if num_tracks is not None:
    spec.hamilton.CopyFrom(
      pb.HamiltonDeckSpec(
        num_tracks=num_tracks,
        track_origin=to_vec3(track_origin or Coordinate(0, 0, 0)),
        track_pitch=track_pitch,
      )
    )
  return resource(name, size, kind=K.RESOURCE_KIND_DECK, deck=spec, **common)


def carrier(name: str, size: Coordinate, **common: Unpack[Common]) -> pb.ResourceDefinition:
  """A carrier: its sites are its children, holders say.

  Args:
    name: Its name.
    size: Its size.
    **common: See `Common`.
  """
  return resource(name, size, kind=K.RESOURCE_KIND_CARRIER, **common)


class HolderFields(Common, total=False):
  """What a holder's builder takes: `Common`, and its `HolderSpec`.

  Attributes:
    child_location: Where what it holds stands, in its own frame.
    pedestal_size_z: A plate holder's pedestal height.
    accepts: The kinds it takes; empty takes any.
    capacity: How many children it takes at once; 0 is no limit.
    channel_y_centers: A tool holder's: where along its y each channel takes its tool, back first.
  """

  child_location: Coordinate
  pedestal_size_z: float
  accepts: Sequence[pb.ResourceKind]
  capacity: int
  channel_y_centers: Sequence[float]


def _holder(
  name: str,
  size: Coordinate,
  kind: pb.ResourceKind,
  child_location: Optional[Coordinate] = None,
  pedestal_size_z: Optional[float] = None,
  accepts: Sequence[pb.ResourceKind] = (),
  capacity: int = 0,
  channel_y_centers: Sequence[float] = (),
  **common: Unpack[Common],
) -> pb.ResourceDefinition:
  spec = pb.HolderSpec(
    child_location=to_vec3(child_location or Coordinate(0, 0, 0)),
    pedestal_size_z=pedestal_size_z,
    accepts=accepts,
    capacity=capacity,
    channel_y_centers=channel_y_centers,
  )
  return resource(name, size, kind=kind, holder=spec, **common)


def holder(name: str, size: Coordinate, **fields: Unpack[HolderFields]) -> pb.ResourceDefinition:
  """A resource holder: one site.

  Args:
    name: Its name.
    size: Its size.
    **fields: See `HolderFields`.
  """
  return _holder(name, size, K.RESOURCE_KIND_RESOURCE_HOLDER, **fields)


def plate_holder(
  name: str, size: Coordinate, **fields: Unpack[HolderFields]
) -> pb.ResourceDefinition:
  """A plate holder: a site that takes skirted plates.

  Args:
    name: Its name.
    size: Its size.
    **fields: See `HolderFields`.
  """
  return _holder(name, size, K.RESOURCE_KIND_PLATE_HOLDER, **fields)


def embedded_tip_rack_holder(
  name: str, size: Coordinate, **fields: Unpack[HolderFields]
) -> pb.ResourceDefinition:
  """A holder an embedded tip rack sits in.

  Args:
    name: Its name.
    size: Its size.
    **fields: See `HolderFields`.
  """
  return _holder(name, size, K.RESOURCE_KIND_EMBEDDED_TIP_RACK_HOLDER, **fields)


def petri_dish_holder(
  name: str, size: Coordinate, **fields: Unpack[HolderFields]
) -> pb.ResourceDefinition:
  """A holder a petri dish sits in.

  Args:
    name: Its name.
    size: Its size.
    **fields: See `HolderFields`.
  """
  return _holder(name, size, K.RESOURCE_KIND_PETRI_DISH_HOLDER, **fields)


def resource_stack(
  name: str,
  size: Coordinate,
  direction: pb.StackDirection = pb.STACK_DIRECTION_Z,
  **common: Unpack[Common],
) -> pb.ResourceDefinition:
  """A stack: its children laid one after another along `direction`.

  Args:
    name: Its name.
    size: Its size.
    direction: The axis the stack grows along.
    **common: See `Common`.
  """
  spec = pb.ResourceStackSpec(direction=direction)
  return resource(name, size, kind=K.RESOURCE_KIND_RESOURCE_STACK, stack=spec, **common)


def plate_adapter(
  name: str,
  size: Coordinate,
  dx: float,
  dy: float,
  dz: float,
  adapter_hole_size_x: float,
  adapter_hole_size_y: float,
  adapter_hole_size_z: float,
  adapter_hole_dx: float,
  adapter_hole_dy: float,
  plate_z_offset: float,
  **common: Unpack[Common],
) -> pb.ResourceDefinition:
  """A plate adapter: a grid of holes a plate's wells sink into.

  Args:
    name: Its name.
    size: Its size.
    dx: Where the hole grid starts, in x.
    dy: Where the hole grid starts, in y.
    dz: Where the hole grid starts, in z.
    adapter_hole_size_x: A hole's size in x.
    adapter_hole_size_y: A hole's size in y.
    adapter_hole_size_z: A hole's depth.
    adapter_hole_dx: The holes' pitch in x.
    adapter_hole_dy: The holes' pitch in y.
    plate_z_offset: How far a plate on it sinks, in z.
    **common: See `Common`.
  """
  spec = pb.PlateAdapterSpec(
    dx=dx,
    dy=dy,
    dz=dz,
    adapter_hole_size_x=adapter_hole_size_x,
    adapter_hole_size_y=adapter_hole_size_y,
    adapter_hole_size_z=adapter_hole_size_z,
    adapter_hole_dx=adapter_hole_dx,
    adapter_hole_dy=adapter_hole_dy,
    plate_z_offset=plate_z_offset,
  )
  return resource(name, size, kind=K.RESOURCE_KIND_PLATE_ADAPTER, plate_adapter=spec, **common)


# -- containers ------------------------------------------------------------------------------------


class ContainerFields(Common, total=False):
  """What a container's builder takes: `Common`, and its `ContainerSpec`.

  Attributes:
    max_volume: How much it holds, in uL.
    material_z_thickness: From its outer bottom to its cavity bottom.
    height_volume_data: (height, volume) points it converts between, piecewise linearly.
    no_go_zones: (min, max) corners of boxes a tip must not enter, in its own frame.
    bottom_type: Its bottom's shape.
    cross_section_type: Its cross-section's shape.
    through_base_to_container_base: A trough's: from its base to its container's base.
  """

  max_volume: float
  material_z_thickness: float
  height_volume_data: Sequence[Tuple[float, float]]
  no_go_zones: Sequence[Tuple[Coordinate, Coordinate]]
  bottom_type: pb.WellBottomType
  cross_section_type: pb.CrossSectionType
  through_base_to_container_base: float


def _container(
  name: str,
  size: Coordinate,
  kind: pb.ResourceKind,
  max_volume: Optional[float] = None,
  material_z_thickness: Optional[float] = None,
  height_volume_data: Sequence[Tuple[float, float]] = (),
  no_go_zones: Sequence[Tuple[Coordinate, Coordinate]] = (),
  bottom_type: pb.WellBottomType = pb.WELL_BOTTOM_TYPE_UNSPECIFIED,
  cross_section_type: pb.CrossSectionType = pb.CROSS_SECTION_TYPE_UNSPECIFIED,
  through_base_to_container_base: Optional[float] = None,
  **common: Unpack[Common],
) -> pb.ResourceDefinition:
  spec = pb.ContainerSpec(
    max_volume=max_volume,
    material_z_thickness=material_z_thickness,
    height_volume_data=[
      pb.HeightVolumePoint(height=height, volume=volume) for height, volume in height_volume_data
    ],
    no_go_zones=[pb.Box(min=to_vec3(low), max=to_vec3(high)) for low, high in no_go_zones],
    bottom_type=bottom_type,
    cross_section_type=cross_section_type,
    through_base_to_container_base=through_base_to_container_base,
  )
  return resource(name, size, kind=kind, container=spec, **common)


def container(
  name: str, size: Coordinate, **fields: Unpack[ContainerFields]
) -> pb.ResourceDefinition:
  """A container that is none of the kinds below.

  Args:
    name: Its name.
    size: Its size.
    **fields: See `ContainerFields`.
  """
  return _container(name, size, K.RESOURCE_KIND_CONTAINER, **fields)


def well(name: str, size: Coordinate, **fields: Unpack[ContainerFields]) -> pb.ResourceDefinition:
  """A plate's well.

  Args:
    name: Its name.
    size: Its size.
    **fields: See `ContainerFields`.
  """
  return _container(name, size, K.RESOURCE_KIND_WELL, **fields)


def trough(name: str, size: Coordinate, **fields: Unpack[ContainerFields]) -> pb.ResourceDefinition:
  """A trough.

  Args:
    name: Its name.
    size: Its size.
    **fields: See `ContainerFields`.
  """
  return _container(name, size, K.RESOURCE_KIND_TROUGH, **fields)


def tube(name: str, size: Coordinate, **fields: Unpack[ContainerFields]) -> pb.ResourceDefinition:
  """A tube.

  Args:
    name: Its name.
    size: Its size.
    **fields: See `ContainerFields`.
  """
  return _container(name, size, K.RESOURCE_KIND_TUBE, **fields)


def petri_dish(
  name: str, size: Coordinate, **fields: Unpack[ContainerFields]
) -> pb.ResourceDefinition:
  """A petri dish.

  Args:
    name: Its name.
    size: Its size.
    **fields: See `ContainerFields`.
  """
  return _container(name, size, K.RESOURCE_KIND_PETRI_DISH, **fields)


def trash(name: str, size: Coordinate, **fields: Unpack[ContainerFields]) -> pb.ResourceDefinition:
  """A trash: what is dropped in it is kept.

  Args:
    name: Its name.
    size: Its size.
    **fields: See `ContainerFields`.
  """
  return _container(name, size, K.RESOURCE_KIND_TRASH, **fields)


# -- itemized labware ------------------------------------------------------------------------------


class ItemizedFields(Common, total=False):
  """What an itemized resource's builder takes: `Common`, and its `ItemizedSpec`.

  Its items are either listed in `children`, each with its identifier, or, alike but for where
  they are, given once as `item` and laid out by the store as `create_ordered_items_2d` lays them
  out: the item in column `i` and row `j` (row 0 is "A", at the back) at
  `item_offset + (i * item_dx, (num_items_y - 1 - j) * item_dy, 0)`. `children` then lists only
  what is not an item, a lid say.

  Attributes:
    num_items_x: Its columns.
    num_items_y: Its rows.
    item: The item every grid position holds, without a location or an identifier.
    item_offset: Where the item in the left column and front row is.
    item_dx: The items' pitch in x.
    item_dy: The items' pitch in y.
  """

  num_items_x: int
  num_items_y: int
  item: pb.ResourceDefinition
  item_offset: Coordinate
  item_dx: float
  item_dy: float


def _itemized(
  num_items_x: int = 0,
  num_items_y: int = 0,
  item: Optional[pb.ResourceDefinition] = None,
  item_offset: Optional[Coordinate] = None,
  item_dx: float = 0,
  item_dy: float = 0,
) -> pb.ItemizedSpec:
  spec = pb.ItemizedSpec(num_items_x=num_items_x, num_items_y=num_items_y)
  if item is not None:
    spec.grid.CopyFrom(
      pb.ItemGrid(
        item=item,
        offset=to_vec3(item_offset or Coordinate(0, 0, 0)),
        item_dx=item_dx,
        item_dy=item_dy,
      )
    )
  return spec


def _split(fields: ItemizedFields) -> Tuple[pb.ItemizedSpec, Common]:
  """An itemized builder's fields: its `ItemizedSpec`, and the rest."""
  return (
    _itemized(
      num_items_x=fields.pop("num_items_x", 0),
      num_items_y=fields.pop("num_items_y", 0),
      item=fields.pop("item", None),
      item_offset=fields.pop("item_offset", None),
      item_dx=fields.pop("item_dx", 0),
      item_dy=fields.pop("item_dy", 0),
    ),
    Common(**fields),  # type: ignore[typeddict-item]
  )


def plate(
  name: str,
  size: Coordinate,
  plate_type: str = "skirted",
  stacking_z_height: Optional[float] = None,
  **fields: Unpack[ItemizedFields],
) -> pb.ResourceDefinition:
  """A plate: wells in a grid.

  Args:
    name: Its name.
    size: Its size.
    plate_type: "skirted", "semi-skirted" or "non-skirted".
    stacking_z_height: How far another plate stacked on it sits above its bottom.
    **fields: See `ItemizedFields`.
  """
  itemized, common = _split(fields)
  spec = pb.PlateSpec(plate_type=plate_type, stacking_z_height=stacking_z_height)
  return resource(name, size, kind=K.RESOURCE_KIND_PLATE, itemized=itemized, plate=spec, **common)


def tip_rack(
  name: str,
  size: Coordinate,
  style: pb.TipRackStyle = pb.TIP_RACK_STYLE_UNSPECIFIED,
  frame_height: Optional[float] = None,
  sinking_depth: Optional[float] = None,
  stacking_z_height: Optional[float] = None,
  **fields: Unpack[ItemizedFields],
) -> pb.ResourceDefinition:
  """A tip rack: tip spots in a grid.

  Args:
    name: Its name.
    size: Its size.
    style: Plain, embedded, standing or nested.
    frame_height: An embedded rack's frame height.
    sinking_depth: How far an embedded rack sinks into its holder.
    stacking_z_height: How far another rack stacked on it sits above its bottom.
    **fields: See `ItemizedFields`.
  """
  itemized, common = _split(fields)
  spec = pb.TipRackSpec(
    style=style,
    frame_height=frame_height,
    sinking_depth=sinking_depth,
    stacking_z_height=stacking_z_height,
  )
  return resource(
    name, size, kind=K.RESOURCE_KIND_TIP_RACK, itemized=itemized, tip_rack=spec, **common
  )


def tube_rack(
  name: str, size: Coordinate, **fields: Unpack[ItemizedFields]
) -> pb.ResourceDefinition:
  """A tube rack: tubes, or the holders they stand in, in a grid.

  Args:
    name: Its name.
    size: Its size.
    **fields: See `ItemizedFields`.
  """
  itemized, common = _split(fields)
  return resource(name, size, kind=K.RESOURCE_KIND_TUBE_RACK, itemized=itemized, **common)


def container_rack(
  name: str, size: Coordinate, **fields: Unpack[ItemizedFields]
) -> pb.ResourceDefinition:
  """A rack of containers in a grid.

  Args:
    name: Its name.
    size: Its size.
    **fields: See `ItemizedFields`.
  """
  itemized, common = _split(fields)
  return resource(name, size, kind=K.RESOURCE_KIND_CONTAINER_RACK, itemized=itemized, **common)


def lid(
  name: str, size: Coordinate, nesting_z_height: float = 0, **common: Unpack[Common]
) -> pb.ResourceDefinition:
  """A lid.

  Args:
    name: Its name.
    size: Its size.
    nesting_z_height: How far it sinks over what it covers.
    **common: See `Common`.
  """
  spec = pb.LidSpec(nesting_z_height=nesting_z_height)
  return resource(name, size, kind=K.RESOURCE_KIND_LID, lid=spec, **common)


# -- tips and tools --------------------------------------------------------------------------------


def head_tool(
  name: str,
  size: Coordinate,
  fitting_depth: float = 0,
  is_conductive: Optional[bool] = None,
  pick_up_location: Optional[Coordinate] = None,
  grip_line_height: Optional[float] = None,
  **common: Unpack[Common],
) -> pb.ResourceDefinition:
  """A tool a head mounts that is not a tip: a CoRe gripper paddle, say.

  Args:
    name: Its name.
    size: Its size.
    fitting_depth: How far the head's shaft goes into it.
    is_conductive: Whether it conducts, for capacitive liquid level detection.
    pick_up_location: Where it is taken from, in its own frame.
    grip_line_height: A gripper tool's: how far the line it grips along stands above its bottom.
    **common: See `Common`.
  """
  spec = pb.HeadToolSpec(
    fitting_depth=fitting_depth,
    is_conductive=is_conductive,
    pick_up_location=_optional_vec3(pick_up_location),
    grip_line_height=grip_line_height,
  )
  return resource(name, size, kind=K.RESOURCE_KIND_HEAD_TOOL, head_tool=spec, **common)


def tip(
  name: str,
  size: Coordinate,
  diameter: float,
  maximal_volume: float,
  fitting_depth: float,
  has_filter: bool = False,
  nominal_volume: Optional[float] = None,
  collar_height: Optional[float] = None,
  is_conductive: Optional[bool] = None,
  hamilton_tip_size: Optional[str] = None,
  hamilton_pickup_method: pb.TipPickupMethod = pb.TIP_PICKUP_METHOD_UNSPECIFIED,
  **common: Unpack[Common],
) -> pb.ResourceDefinition:
  """A tip.

  Args:
    name: Its name.
    size: Its size; its z is its total length.
    diameter: Its diameter.
    maximal_volume: How much it holds, in uL.
    fitting_depth: How far the channel's shaft goes into it.
    has_filter: Whether it has a filter.
    nominal_volume: The volume it is sold as; `maximal_volume` when unset.
    collar_height: Where its collar is, from its top; unset, it rests on its spot's surface.
    is_conductive: Whether it conducts, for capacitive liquid level detection.
    hamilton_tip_size: A Hamilton tip's `TipSize` name; unset on any other tip.
    hamilton_pickup_method: A Hamilton tip's pickup method.
    **common: See `Common`.
  """
  spec = pb.TipSpec(
    diameter=diameter,
    has_filter=has_filter,
    maximal_volume=maximal_volume,
    nominal_volume=maximal_volume if nominal_volume is None else nominal_volume,
    collar_height=collar_height,
  )
  if hamilton_tip_size is not None:
    spec.hamilton.CopyFrom(
      pb.HamiltonTipSpec(tip_size=hamilton_tip_size, pickup_method=hamilton_pickup_method)
    )
  tool = pb.HeadToolSpec(fitting_depth=fitting_depth, is_conductive=is_conductive)
  return resource(name, size, kind=K.RESOURCE_KIND_TIP, tip=spec, head_tool=tool, **common)


def tip_spot(
  name: str,
  size: Coordinate,
  prototype_tip: Union[pb.ResourceDefinition, pb.ResourceAttributes],
  **common: Unpack[Common],
) -> pb.ResourceDefinition:
  """A tip spot: where a tip rack holds a tip.

  Args:
    name: Its name.
    size: Its size.
    prototype_tip: The tip it makes when one is needed and it is not tracking; `tip` builds one.
    **common: See `Common`.
  """
  attributes = (
    prototype_tip.attributes if isinstance(prototype_tip, pb.ResourceDefinition) else prototype_tip
  )
  spec = pb.TipSpotSpec(prototype_tip=attributes)
  return resource(name, size, kind=K.RESOURCE_KIND_TIP_SPOT, tip_spot=spec, **common)


# -- a device's own parts --------------------------------------------------------------------------


def pipette_channel(
  name: str,
  size: Coordinate,
  reference_point: Coordinate,
  **common: Unpack[Common],
) -> pb.ResourceDefinition:
  """A pipetting channel's body, which a tip mounting shaft hangs from.

  Args:
    name: Its name.
    size: Its size.
    reference_point: Where the drives report, from its left front bottom corner.
    **common: See `Common`.
  """
  spec = pb.PipetteChannelSpec(reference_point=to_vec3(reference_point))
  return resource(name, size, kind=K.RESOURCE_KIND_PIPETTE_CHANNEL, pipette_channel=spec, **common)


def tip_mounting_shaft(
  name: str,
  size: Coordinate,
  tip_pickup_mode: str = "",
  cross_section_type: pb.CrossSectionType = pb.CROSS_SECTION_TYPE_UNSPECIFIED,
  **common: Unpack[Common],
) -> pb.ResourceDefinition:
  """The shaft a channel mounts a tip on.

  Args:
    name: Its name.
    size: Its size.
    tip_pickup_mode: How it takes a tip.
    cross_section_type: Its cross-section's shape.
    **common: See `Common`.
  """
  spec = pb.TipMountingShaftSpec(
    tip_pickup_mode=tip_pickup_mode, cross_section_type=cross_section_type
  )
  return resource(
    name, size, kind=K.RESOURCE_KIND_TIP_MOUNTING_SHAFT, tip_mounting_shaft=spec, **common
  )


def n_channel_pipette(
  name: str,
  size: Coordinate,
  reference_point: Coordinate,
  independent_channel_actuation: bool = False,
  **fields: Unpack[ItemizedFields],
) -> pb.ResourceDefinition:
  """A head of channels in a grid, a 96-head say.

  Args:
    name: Its name.
    size: Its size.
    reference_point: Where the drives report, from its left front bottom corner.
    independent_channel_actuation: Whether its channels move each on its own.
    **fields: See `ItemizedFields`; its items are its channels.
  """
  itemized, common = _split(fields)
  spec = pb.NChannelPipetteSpec(
    reference_point=to_vec3(reference_point),
    independent_channel_actuation=independent_channel_actuation,
  )
  return resource(
    name,
    size,
    kind=K.RESOURCE_KIND_N_CHANNEL_PIPETTE,
    itemized=itemized,
    n_channel_pipette=spec,
    **common,
  )


def link_body(
  name: str,
  size: Coordinate,
  proximal_joint: Coordinate,
  distal_joint: Optional[Coordinate] = None,
  **common: Unpack[Common],
) -> pb.ResourceDefinition:
  """A link of an arm, between two joints.

  Args:
    name: Its name.
    size: Its size.
    proximal_joint: The joint it turns about, in its own frame.
    distal_joint: The joint the next link turns about; unset on the last.
    **common: See `Common`.
  """
  spec = pb.LinkBodySpec(
    proximal_joint=to_vec3(proximal_joint), distal_joint=_optional_vec3(distal_joint)
  )
  return resource(name, size, kind=K.RESOURCE_KIND_LINK_BODY, link_body=spec, **common)


def mechanical_gripper(
  name: str,
  size: Coordinate,
  proximal_joint: Coordinate,
  tool_center_point: Coordinate,
  jaw_width_min: float,
  jaw_width_max: float,
  body: str = "",
  fingers: Sequence[str] = (),
  pads: Sequence[str] = (),
  jaws: Sequence[str] = (),
  **common: Unpack[Common],
) -> pb.ResourceDefinition:
  """A gripper: its own parts are the children named here, anything else under it is held.

  Args:
    name: Its name.
    size: Its size.
    proximal_joint: The joint it turns about, in its own frame.
    tool_center_point: Where its fingers close on, in its own frame.
    jaw_width_min: Its jaws' narrowest opening.
    jaw_width_max: Its jaws' widest opening.
    body: Its body's name.
    fingers: Its fingers' names.
    pads: Its pads' names.
    jaws: Its jaws' names.
    **common: See `Common`.
  """
  spec = pb.MechanicalGripperSpec(
    proximal_joint=to_vec3(proximal_joint),
    tool_center_point=to_vec3(tool_center_point),
    jaw_width_min=jaw_width_min,
    jaw_width_max=jaw_width_max,
    body=body,
    fingers=fingers,
    pads=pads,
    jaws=jaws,
  )
  return resource(
    name, size, kind=K.RESOURCE_KIND_MECHANICAL_GRIPPER, mechanical_gripper=spec, **common
  )
