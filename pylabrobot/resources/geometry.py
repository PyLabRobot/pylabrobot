from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.well import Well


def generate_geometry_library(root: Resource) -> Dict[str, Any]:
  """Generate a renderer-oriented geometry library entry for a resource tree.

  The library entry is intentionally separate from the package data: call this on a deck
  or labware resource after building a layout, then write the returned dict to JSON
  if a simulator needs it.
  """

  prototypes: Dict[str, Dict[str, Any]] = {}
  instances: Dict[str, Dict[str, Any]] = {}

  for resource in _walk_resources(root):
    prototype = _resource_geometry_prototype(resource)
    prototype_id = _geometry_prototype_id(prototype)
    prototypes.setdefault(prototype_id, prototype)

    instance: Dict[str, Any] = {
      "prototype": prototype_id,
      "parent": resource.parent.name if resource.parent is not None else None,
      "pose": _resource_pose(resource),
      "rotation": _rotation_values(resource.get_absolute_rotation()),
    }
    if len(resource.children) > 0:
      instance["children"] = [child.name for child in resource.children]
    instances[resource.name] = instance

  return {
    "root": root.name,
    "prototypes": prototypes,
    "instances": instances,
  }


def save_geometry_library(
  root: Resource,
  path: Union[str, Path],
  indent: Optional[int] = 2,
) -> None:
  """Generate a geometry library entry and write it to a JSON file."""

  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(generate_geometry_library(root), indent=indent), encoding="utf-8")


def generate_geometry_catalog(root: Resource) -> Dict[str, Any]:
  """Deprecated alias for :func:`generate_geometry_library`."""

  return generate_geometry_library(root)


def save_geometry_catalog(
  root: Resource,
  path: Union[str, Path],
  indent: Optional[int] = 2,
) -> None:
  """Deprecated alias for :func:`save_geometry_library`."""

  save_geometry_library(root, path, indent=indent)


def _walk_resources(root: Resource) -> List[Resource]:
  resources = [root]
  for child in root.children:
    resources.extend(_walk_resources(child))
  return resources


def _resource_geometry_prototype(resource: Resource) -> Dict[str, Any]:
  prototype: Dict[str, Any] = {
    "type": resource.__class__.__name__,
    "category": resource.category,
    "size": _coordinate_values(
      Coordinate(resource.get_size_x(), resource.get_size_y(), resource.get_size_z())
    ),
    "geometry": _resource_geometry_hints(resource),
  }
  if resource.model is not None:
    prototype["model"] = resource.model
  return prototype


def _resource_geometry_hints(resource: Resource) -> Dict[str, Any]:
  if isinstance(resource, Well):
    geometry: Dict[str, Any] = {
      "shape": "well",
      "cross_section": resource.cross_section_type.value,
      "bottom": resource.bottom_type.value,
    }
    if resource._material_z_thickness is not None:
      geometry["material_z_thickness"] = resource._material_z_thickness
    if len(resource.no_go_zones) > 0:
      geometry["no_go_zones"] = _no_go_zones_to_values(resource.no_go_zones)
    return geometry

  if isinstance(resource, TipSpot):
    return {"shape": "tip_spot"}

  if isinstance(resource, Container):
    geometry = {"shape": "container"}
    if resource._material_z_thickness is not None:
      geometry["material_z_thickness"] = resource._material_z_thickness
    if len(resource.no_go_zones) > 0:
      geometry["no_go_zones"] = _no_go_zones_to_values(resource.no_go_zones)
    return geometry

  if resource.category == "deck":
    return {"shape": "deck"}

  return {"shape": "box"}


def _geometry_prototype_id(prototype: Dict[str, Any]) -> str:
  payload = json.dumps(prototype, sort_keys=True, separators=(",", ":"))
  digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
  return f"{prototype['type']}_{digest}"


def _coordinate_or_none(resource: Resource, x: str, y: str, z: str) -> Optional[List[float]]:
  try:
    return _coordinate_values(resource.get_absolute_location(x=x, y=y, z=z))
  except Exception:
    return None


def _resource_pose(resource: Resource) -> Optional[List[float]]:
  pose = _coordinate_or_none(resource, x="l", y="f", z="b")
  if pose is None and resource.parent is None:
    return [0, 0, 0]
  return pose


def _coordinate_values(coordinate: Coordinate) -> List[float]:
  return [coordinate.x, coordinate.y, coordinate.z]


def _rotation_values(rotation: Rotation) -> List[float]:
  return [rotation.x, rotation.y, rotation.z]


def _no_go_zones_to_values(
  no_go_zones: List[tuple[Coordinate, Coordinate]],
) -> List[List[List[float]]]:
  return [
    [_coordinate_values(front_left_bottom), _coordinate_values(back_right_top)]
    for front_left_bottom, back_right_top in no_go_zones
  ]
