from __future__ import annotations

import warnings
from collections import OrderedDict
from typing import (
  TYPE_CHECKING,
  Dict,
  List,
  Literal,
  Optional,
  Sequence,
  Tuple,
  Union,
  cast,
)

from pylabrobot.resources.liquid import Liquid

from .itemized_resource import ItemizedResource
from .lid import Lid, Liddable
from .resource import Resource

if TYPE_CHECKING:
  from .well import Well


class Plate(Liddable, ItemizedResource["Well"]):
  """Base class for Plate resources."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    ordered_items: Optional[Dict[str, "Well"]] = None,
    ordering: Optional[OrderedDict[str, str]] = None,
    category: str = "plate",
    lid: Optional[Lid] = None,
    model: Optional[str] = None,
    plate_type: Literal["skirted", "semi-skirted", "non-skirted"] = "skirted",
    stacking_z_height: Optional[float] = None,
  ):
    """Initialize a Plate resource.

    Args:
      well_size_x: Size of the wells in the x direction.
      well_size_y: Size of the wells in the y direction.
      lid: Immediately assign a lid to the plate.
      plate_type: Type of the plate. One of "skirted", "semi-skirted", or "non-skirted". A
        more complete description is still pending.
      stacking_z_height: The vertical pitch in mm between two identical plates stacked directly on
        top of each other (i.e. the height a plate adds to a stack, equal to ``size_z`` minus the
        overlap with the plate below). Required by some stacker devices (e.g. the Agilent BenchCel)
        and left as ``None`` when unknown.
    """

    super().__init__(
      name,
      size_x,
      size_y,
      size_z,
      ordered_items=ordered_items,
      ordering=ordering,
      category=category,
      model=model,
    )
    self.plate_type = plate_type
    self.stacking_z_height = stacking_z_height

    if lid is not None:
      self.assign_child_resource(lid)

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "plate_type": self.plate_type,
      "stacking_z_height": self.stacking_z_height,
    }

  def __eq__(self, other) -> bool:
    # `stacking_z_height` is a physical dimension (the pitch a plate adds to a stack), so plates
    # that differ in it are different labware and must not compare equal.
    return (
      super().__eq__(other)
      and isinstance(other, Plate)
      and self.stacking_z_height == other.stacking_z_height
    )

  # Defining `__eq__` sets `__hash__` to None; restore the `Resource` (repr-based) hash. Equal
  # plates share the same repr, so the hash/eq invariant holds.
  __hash__ = Resource.__hash__

  def __repr__(self) -> str:
    return (
      f"{self.__class__.__name__}(name={self.name!r}, size_x={self._size_x}, "
      f"size_y={self._size_y}, size_z={self._size_z}, "
      f"stacking_z_height={self.stacking_z_height}, location={self.location})"
    )

  def get_well(self, identifier: Union[str, int, Tuple[int, int]]) -> "Well":
    """Get the item with the given identifier.

    See :meth:`~.get_item` for more information.
    """

    return super().get_item(identifier)

  def get_wells(self, identifier: Union[str, Sequence[int]]) -> List["Well"]:
    """Get the wells with the given identifier.

    See :meth:`~.get_items` for more information.
    """

    return super().get_items(identifier)

  def set_well_volumes(
    self,
    volumes: List[float],
  ) -> None:
    """Fill all wells in the plate with a given volume.

    Args:
      volumes: The volume to fill each well with, in uL.
    """

    if not len(volumes) == self.num_items:
      raise ValueError(
        f"Length of volumes ({len(volumes)}) does not match number of wells ({self.num_items})."
      )

    for well, volume in zip(self.get_all_items(), volumes):
      well.set_volume(volume)

  def set_well_liquids(
    self,
    liquids: Union[
      List[List[Tuple[Optional["Liquid"], Union[int, float]]]],
      List[Tuple[Optional["Liquid"], Union[int, float]]],
      Tuple[Optional["Liquid"], Union[int, float]],
    ],
  ):
    """Deprecated: Use `set_well_volumes` instead."""
    warnings.warn(
      "set_well_liquids is deprecated and will be removed in a future version. "
      "Use set_well_volumes instead.",
      FutureWarning,
    )
    if isinstance(liquids, tuple):
      liquids = [liquids] * self.num_items
    elif isinstance(liquids, list) and all(isinstance(column, list) for column in liquids):
      # mypy doesn't know that all() checks the type
      liquids = cast(List[List[Tuple[Optional["Liquid"], float]]], liquids)
      liquids = [list(column) for column in zip(*liquids)]  # transpose the list of lists
      liquids = [volume for column in liquids for volume in column]  # flatten the list of lists

    self.set_well_volumes([volume for _, volume in liquids])  # type: ignore

  def disable_volume_trackers(self) -> None:
    """Disable volume tracking for all wells in the plate."""

    for well in self.get_all_items():
      well.tracker.disable()

  def enable_volume_trackers(self) -> None:
    """Enable volume tracking for all wells in the plate."""

    for well in self.get_all_items():
      well.tracker.enable()

  def get_quadrant(
    self,
    quadrant: Literal[
      "tl", "top_left", "tr", "top_right", "bl", "bottom_left", "br", "bottom_right"
    ],
    quadrant_type: Literal["block", "checkerboard"] = "checkerboard",
    quadrant_internal_fill_order: Literal["column-major", "row-major"] = "column-major",
  ) -> List["Well"]:
    """
    Get wells from a specified quadrant.

    Args:
      quadrant: The desired quadrant ("tl" / "top_left", "tr" / "top_right",
        "bl" / "bottom_left", "br" / "bottom_right").
      quadrant_type: Either "block" (divides plate into 4 sections) or
        "checkerboard" (alternating well pattern).
      quadrant_internal_fill_order: Whether to return wells in "column-major"
        or "row-major" order.

    Returns:
      List of wells in the specified quadrant.

    Raises:
      ValueError: If an invalid quadrant or configuration is specified.
    """

    # Ensure plate dimensions are even for valid quadrant selection
    if self.num_items_x % 2 != 0 or self.num_items_y % 2 != 0:
      raise ValueError(
        "Both num_items_x and num_items_y must be even for quadrant selection,"
        f"\nare {self.num_items_x=}, {self.num_items_y=}"
      )
    assert quadrant_internal_fill_order in ["column-major", "row-major"], (
      f"Invalid quadrant_internal_fill_order: {quadrant_internal_fill_order},"
      "\nquadrant_internal_fill_order must be either 'column-major' or 'row-major',"
    )

    # Determine row and column start indices
    if quadrant.lower() in ["tl", "top_left"]:
      row_start, col_start = 0, 0
    elif quadrant.lower() in ["tr", "top_right"]:
      row_start, col_start = 0, 1
    elif quadrant.lower() in ["bl", "bottom_left"]:
      row_start, col_start = 1, 0
    elif quadrant.lower() in ["br", "bottom_right"]:
      row_start, col_start = 1, 1
    else:
      raise ValueError(
        f"Invalid quadrant: {quadrant}. Quadrant must be in  ['tl', 'tr', 'bl', 'br']"
      )

    wells = []

    if quadrant_type == "checkerboard":
      # Checkerboard pattern: Every other well
      for row in range(row_start, self.num_items_y, 2):
        for col in range(col_start, self.num_items_x, 2):
          wells.append(self.get_well((row, col)))

    elif quadrant_type == "block":
      # Block pattern: Ensure plate can be evenly divided into 4 quadrants
      row_half = self.num_items_y // 2
      col_half = self.num_items_x // 2

      row_range = range(row_start * row_half, (row_start + 1) * row_half)
      col_range = range(col_start * col_half, (col_start + 1) * col_half)

      for row in row_range:
        for col in col_range:
          wells.append(self.get_well((row, col)))

    else:
      raise ValueError(
        f"Invalid quadrant_type: {quadrant_type}. "
        "quadrant_type must be either 'checkerboard' (default) or 'block'"
      )

    # Apply internal fill order
    assert all(well.location is not None for well in wells)
    if quadrant_internal_fill_order == "row-major":
      wells.sort(key=lambda well: (-well.location.y, well.location.x))  # type: ignore
    else:
      wells.sort(key=lambda well: (well.location.x, -well.location.y))  # type: ignore

    return wells

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    if not isinstance(resource, Lid):
      raise RuntimeError(f"Can only drop Lid resources onto Plate '{self.name}'.")
