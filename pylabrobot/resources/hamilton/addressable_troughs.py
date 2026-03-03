"""Addressable troughs: one shared volume pool with multiple fixed access locations.

AddressableTrough is a general Container parameterized by a TroughSpec (dimensions,
volume/height curve, spot layout). Use it for Hamilton 60 mL, custom troughs, or other
hardware with the same behavior. The visualizer renders all as Container (one type).
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.serializer import serialize as plr_serialize
from pylabrobot.resources.container import Container
from pylabrobot.resources.resource import Resource
from pylabrobot.utils.interpolation import interpolate_1d
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
  from pylabrobot.resources.liquid import Liquid
  from pylabrobot.resources.volume_tracker import VolumeTracker

# ---------------------------------------------------------------------------
# TroughSpec: parameterization for any addressable trough
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TroughSpec:
  """Spec for an addressable trough: dimensions, volume/height, and spot layout.

  Register with register_trough_spec() so AddressableTrough can deserialize by
  spec_name. Spot positions are (spot_id, x_center_mm, y_center_mm) in local coords.
  """
  name: str  # Key for registry (e.g. \"hamilton_60ml\")
  size_x: float
  size_y: float
  size_z: float
  material_z_thickness: float
  max_volume: float
  spot_size_xy: float
  spots: Tuple[Tuple[str, float, float], ...]  # (spot_id, x_center, y_center)
  compute_volume_from_height: Callable[[float], float]
  compute_height_from_volume: Callable[[float], float]
  model: str = "addressable_trough"
  category: str = "plate"


def register_trough_spec(spec: TroughSpec) -> None:
  _SPEC_REGISTRY[spec.name] = spec


def get_trough_spec(name: str) -> TroughSpec:
  if name not in _SPEC_REGISTRY:
    raise KeyError(f"Unknown trough spec '{name}'; registered: {list(_SPEC_REGISTRY)}")
  return _SPEC_REGISTRY[name]


_SPEC_REGISTRY: Dict[str, TroughSpec] = {}

# ---------------------------------------------------------------------------
# Hamilton 1-trough 60 mL V-bottom (cat. 56694) — one concrete spec
# ---------------------------------------------------------------------------
_HAMILTON_60ML_SIZE_X = 19.0
_HAMILTON_60ML_SIZE_Y = 90.0
_HAMILTON_60ML_SIZE_Z = 65.5
_HAMILTON_60ML_MATERIAL_Z = 1.58
_HAMILTON_60ML_MAX_UL = 60_000
_HAMILTON_60ML_SPOT_SIZE = 9.0

_HEIGHT_TO_VOLUME = {
  0.0: 0.0,
  2.2: 500.0,
  3.5: 1_000.0,
  4.0: 1_500.0,
  4.7: 2_000.0,
  5.2: 2_500.0,
  5.6: 3_000.0,
  6.0: 3_500.0,
  6.3: 4_000.0,
  6.7: 4_500.0,
  6.8: 5_000.0,
  7.2: 5_500.0,
  7.5: 6_000.0,
  8.3: 7_000.0,
  9.0: 8_000.0,
  9.8: 9_000.0,
  10.4: 10_000.0,
  18.0: 20_000.0,
  25.3: 30_000.0,
  35.6: 45_000.0,
  45.7: 60_000.0,
  52.13: 70_000.0,
  58.5: 80_000.0,
}
_VOLUME_TO_HEIGHT = {v: k for k, v in _HEIGHT_TO_VOLUME.items()}


def _hamilton_60ml_volume_from_height(h_mm: float) -> float:
  if h_mm < 0:
    raise ValueError("Height must be ≥ 0 mm.")
  if h_mm > _HAMILTON_60ML_SIZE_Z * 1.05:
    raise ValueError(f"Height {h_mm} is too large for 60 mL trough.")
  return round(
    max(0.0, interpolate_1d(h_mm, _HEIGHT_TO_VOLUME, bounds_handling="error")), 3
  )


def _hamilton_60ml_height_from_volume(volume_ul: float) -> float:
  if volume_ul < 0:
    raise ValueError(f"Volume must be ≥ 0 µL; got {volume_ul} µL")
  return round(
    max(0.0, interpolate_1d(volume_ul, _VOLUME_TO_HEIGHT, bounds_handling="error")), 3
  )


# A1 = back, H1 = front
_Y_CENTERS_60ML = [80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0, 10.0]
_X_CENTER_60ML = _HAMILTON_60ML_SIZE_X / 2
_OFFSET_60ML = _HAMILTON_60ML_SPOT_SIZE / 2
_SPOT_IDS_60ML = ("A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1")
_SPOTS_60ML = tuple(
  (sid, _X_CENTER_60ML - _OFFSET_60ML, _Y_CENTERS_60ML[i] - _OFFSET_60ML)
  for i, sid in enumerate(_SPOT_IDS_60ML)
)

HAMILTON_60ML_TROUGH_SPEC = TroughSpec(
  name="hamilton_60ml",
  size_x=_HAMILTON_60ML_SIZE_X,
  size_y=_HAMILTON_60ML_SIZE_Y,
  size_z=_HAMILTON_60ML_SIZE_Z,
  material_z_thickness=_HAMILTON_60ML_MATERIAL_Z,
  max_volume=_HAMILTON_60ML_MAX_UL,
  spot_size_xy=_HAMILTON_60ML_SPOT_SIZE,
  spots=_SPOTS_60ML,
  compute_volume_from_height=_hamilton_60ml_volume_from_height,
  compute_height_from_volume=_hamilton_60ml_height_from_volume,
  model="hamilton_60ml_trough",
  category="plate",
)
register_trough_spec(HAMILTON_60ML_TROUGH_SPEC)

# ---------------------------------------------------------------------------
# TroughSpot: one access location; delegates volume/height to parent trough
# ---------------------------------------------------------------------------


class TroughSpot(Container):
  """One access location on a trough; volume and height delegate to the parent container."""

  def __init__(
    self,
    name: str,
    trough: Container,
    spot_size_xy: float,
    category: Optional[str] = "trough_spot",
  ) -> None:
    self._trough = trough
    super().__init__(
      name=name,
      size_x=spot_size_xy,
      size_y=spot_size_xy,
      size_z=trough.get_size_z(),
      material_z_thickness=trough.material_z_thickness,
      max_volume=trough.max_volume,
      category=category,
      model=trough.model,
      compute_volume_from_height=lambda h: trough.compute_volume_from_height(h),
      compute_height_from_volume=lambda v: trough.compute_height_from_volume(v),
    )
    self.tracker = DelegatingVolumeTracker(
      thing=f"{self.name}_volume_tracker",
      parent=trough.tracker,
    )

  def serialize(self) -> dict:
    """Serialize without the compute_* callables (they close over the parent and cause
    recursion). Deserialize rebuilds them from trough=."""
    data = Resource.serialize(self)
    data["max_volume"] = plr_serialize(self.max_volume)
    data["material_z_thickness"] = self._material_z_thickness
    data["compute_volume_from_height"] = None
    data["compute_height_from_volume"] = None
    return data

  @classmethod
  def deserialize(
    cls,
    data: dict,
    allow_marshal: bool = False,
    *,
    trough: Optional[Container] = None,
    spot_size_xy: Optional[float] = None,
  ) -> "TroughSpot":
    """Deserialize a TroughSpot. If trough is None, uses a temporary container;
    the parent will replace the tracker and _trough (and fix dimensions) in its callback."""
    data_copy = data.copy()
    for key in ("type", "parent_name", "location"):
      data_copy.pop(key, None)
    name = data_copy.get("name", "spot")
    if trough is None:
      trough = _make_temp_trough_for_deserialize()
    sz = spot_size_xy if spot_size_xy is not None else 9.0
    return cls(name=name, trough=trough, spot_size_xy=sz)


def _make_temp_trough_for_deserialize() -> Container:
  """Minimal container so TroughSpot can be constructed during deserialize."""
  return Container(
    name="__temp_trough__",
    size_x=19.0,
    size_y=90.0,
    size_z=65.5,
    material_z_thickness=1.58,
    max_volume=60_000,
    compute_volume_from_height=_hamilton_60ml_volume_from_height,
    compute_height_from_volume=_hamilton_60ml_height_from_volume,
  )


# ---------------------------------------------------------------------------
# AddressableTrough: spec-driven container with one shared volume, N access spots
# ---------------------------------------------------------------------------


class AddressableTrough(Container):
  """Trough with one shared volume pool and multiple fixed access locations.

  Parameterized by a TroughSpec (dimensions, volume/height, spot layout). Use
  trough[\"A1\"], trough[0], etc. for aspiration; all spots share the same volume.
  Serializes as type \"AddressableTrough\" so the visualizer can render all
  addressable troughs with one mapping (e.g. to Container).
  """

  def __init__(self, name: str, spec: TroughSpec, **kwargs: Any) -> None:
    from_deserialize = "size_x" in kwargs
    if from_deserialize:
      spec_name = kwargs.pop("spec_name", None)
      if spec_name is None:
        raise ValueError("AddressableTrough deserialization requires 'spec_name' in payload")
      spec = get_trough_spec(spec_name)

    kwargs.setdefault("size_x", spec.size_x)
    kwargs.setdefault("size_y", spec.size_y)
    kwargs.setdefault("size_z", spec.size_z)
    kwargs.setdefault("material_z_thickness", spec.material_z_thickness)
    kwargs.setdefault("max_volume", spec.max_volume)
    kwargs.setdefault("category", spec.category)
    kwargs.setdefault("model", spec.model)
    kwargs.setdefault("compute_volume_from_height", spec.compute_volume_from_height)
    kwargs.setdefault("compute_height_from_volume", spec.compute_height_from_volume)

    super().__init__(
      name=name,
      size_x=kwargs.pop("size_x"),
      size_y=kwargs.pop("size_y"),
      size_z=kwargs.pop("size_z"),
      material_z_thickness=kwargs.pop("material_z_thickness"),
      max_volume=kwargs.pop("max_volume"),
      category=kwargs.pop("category"),
      model=kwargs.pop("model"),
      compute_volume_from_height=kwargs.pop("compute_volume_from_height"),
      compute_height_from_volume=kwargs.pop("compute_height_from_volume"),
    )
    self._spec = spec
    self._spots: List[TroughSpot] = []
    self._spot_by_id: Dict[str, TroughSpot] = {}
    spot_ids = [s[0] for s in spec.spots]

    if from_deserialize:
      self.register_did_assign_resource_callback(self._on_spot_assigned_from_deserialize)
      return
    for i, (spot_id, x_center, y_center) in enumerate(spec.spots):
      spot = TroughSpot(
        name=f"{name}_{spot_id}",
        trough=self,
        spot_size_xy=spec.spot_size_xy,
      )
      location = Coordinate(x=x_center, y=y_center, z=0.0)
      self.assign_child_resource(spot, location=location)
      self._spots.append(spot)
      self._spot_by_id[spot_id] = spot

  def _on_spot_assigned_from_deserialize(self, resource: Resource) -> None:
    if not isinstance(resource, TroughSpot) or len(self._spots) >= len(self._spec.spots):
      return
    resource.tracker = DelegatingVolumeTracker(
      thing=f"{resource.name}_volume_tracker",
      parent=self.tracker,
    )
    resource._trough = self
    resource._size_x = self._spec.spot_size_xy
    resource._size_y = self._spec.spot_size_xy
    resource._size_z = self.get_size_z()
    idx = len(self._spots)
    spot_id = self._spec.spots[idx][0]
    self._spots.append(resource)
    self._spot_by_id[spot_id] = resource

  def serialize(self) -> dict:
    data = super().serialize()
    data["spec_name"] = self._spec.name
    return data

  @property
  def num_items(self) -> int:
    return len(self._spots)

  def get_item(
    self,
    identifier: Union[str, int, tuple],
  ) -> TroughSpot:
    if isinstance(identifier, tuple):
      row, col = identifier
      identifier = f"{chr(ord('A') + row)}{col + 1}"
    if isinstance(identifier, int):
      if identifier < 0:
        identifier = identifier + len(self._spots)
      if identifier < 0 or identifier >= len(self._spots):
        raise IndexError(f"Spot index out of range [0, {len(self._spots)}).")
      return self._spots[identifier]
    if isinstance(identifier, str):
      if identifier not in self._spot_by_id:
        raise KeyError(
          f"Unknown spot '{identifier}'; valid ids are {list(self._spot_by_id)}."
        )
      return self._spot_by_id[identifier]
    raise TypeError(f"identifier must be str, int, or tuple; got {type(identifier)}.")

  def get_items(
    self,
    identifiers: Union[str, List[int], List[str]],
  ) -> List[TroughSpot]:
    import pylabrobot.utils
    if isinstance(identifiers, str):
      identifiers = pylabrobot.utils.expand_string_range(identifiers)
    return [self.get_item(i) for i in identifiers]

  def __getitem__(
    self,
    identifier: Union[str, int, slice, range],
  ) -> Union[TroughSpot, List[TroughSpot]]:
    if isinstance(identifier, slice):
      start, stop, step = identifier.indices(len(self._spots))
      return [self._spots[i] for i in range(start, stop, step)]
    if isinstance(identifier, range):
      return [self._spots[i] for i in identifier if 0 <= i < len(self._spots)]
    if isinstance(identifier, str) and ":" in identifier:
      return self.get_items(identifier)
    return self.get_item(identifier)

  def set_volume(self, volume: float) -> None:
    self.tracker.set_volume(volume)


def hamilton_60ml_trough_with_spots(name: str) -> AddressableTrough:
  """60 mL V-bottom trough with 8 access locations (A1–H1). Same footprint as Hamilton 56694.

  Args:
    name: Resource name (shown in visualizer, logs, etc.), e.g.
      res_car[0] = hamilton_60ml_trough_with_spots(\"bead_reservoir\").
  """
  return AddressableTrough(name=name, spec=HAMILTON_60ML_TROUGH_SPEC)


class DelegatingVolumeTracker:
  """Volume tracker that forwards all operations to a parent tracker.

  Used so multiple access "spots" (e.g. on a trough) share a single volume pool
  without changing pylabrobot's liquid handler or backends.
  """

  def __init__(self, thing: str, parent: "VolumeTracker") -> None:
    self.thing = thing
    self._parent = parent

  @property
  def is_disabled(self) -> bool:
    return self._parent.is_disabled

  def disable(self) -> None:
    self._parent.disable()

  def enable(self) -> None:
    self._parent.enable()

  @property
  def max_volume(self) -> float:
    return self._parent.max_volume

  @property
  def volume(self) -> float:
    return self._parent.volume

  @property
  def pending_volume(self) -> float:
    return self._parent.pending_volume

  def set_volume(self, volume: float) -> None:
    self._parent.set_volume(volume)

  def set_liquids(
    self, liquids: List[Tuple[Optional["Liquid"], float]]
  ) -> None:
    self._parent.set_liquids(liquids)

  def remove_liquid(self, volume: float) -> None:
    self._parent.remove_liquid(volume)

  def add_liquid(self, volume: float) -> None:
    self._parent.add_liquid(volume)

  def get_used_volume(self) -> float:
    return self._parent.get_used_volume()

  def get_free_volume(self) -> float:
    return self._parent.get_free_volume()

  def get_liquids(self, top_volume: float) -> List[Tuple[Optional["Liquid"], float]]:
    return self._parent.get_liquids(top_volume)

  def commit(self) -> None:
    self._parent.commit()

  def rollback(self) -> None:
    self._parent.rollback()

  def serialize(self) -> dict:
    return self._parent.serialize()

  def load_state(self, state: dict) -> None:
    self._parent.load_state(state)

  def register_callback(self, callback: object) -> None:
    # Avoid double-firing when multiple spots share the same parent; no-op here.
    pass
