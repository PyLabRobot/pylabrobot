"""Hamilton STAR's rigid heads: grids of pipetting channels that move as one."""

from typing import Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.n_channel_pipettes import (
  SHAFT_DIAMETER,
  SHAFT_LENGTH,
  NChannelPipette,
  TipMountingShaft,
)
from pylabrobot.resources.utils import create_ordered_items_2d


def _rigid_head(
  name: str,
  columns: int,
  rows: int,
  pitch: float,
  model: str,
  size_x: Optional[float],
  size_y: Optional[float],
  size_z: float,
  dx: Optional[float],
  dy: Optional[float],
  dz: float,
) -> NChannelPipette:
  """One of the rigid heads: a grid the device knows, in a body it does not.

  What the device tells us is the grid - how many channels, at what pitch. The body around it
  is a measurement of a particular head, never derived from the pitch. Left unmeasured, the resource
  spans the array and nothing more.

  Args:
    name: what to call this one.
    columns: how many channels across.
    rows: how many channels deep.
    pitch: their centre-to-centre spacing, in mm.
    model: which head this is.
    size_x: how wide the head's body is, in mm. None spans the channel array instead.
    size_y: how deep the body is, in mm. None spans the channel array instead.
    size_z: how tall it is, in mm. Zero models it as its channel plane.
    dx: how far channel A1 sits from the body's left edge, in mm. None centres the array.
    dy: how far channel A1 sits from its front edge, in mm. None centres the array.
    dz: how far channel A1 sits above its bottom, in mm.

  Returns:
    The pipette.
  """
  array_x, array_y = (columns - 1) * pitch, (rows - 1) * pitch
  size_x = array_x if size_x is None else size_x
  size_y = array_y if size_y is None else size_y
  # Centred in the body unless placed explicitly. A1 is the back row, so it sits a full array
  # depth behind the front margin.
  dx = (size_x - array_x) / 2 if dx is None else dx
  dy = (size_y - array_y) / 2 + array_y if dy is None else dy
  return NChannelPipette(
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    # Where a tip is picked up: the axis of shaft A1, at the end of it. That end is a shaft's length
    # below the plane the body starts at, which is what the shafts hang from.
    reference_point=Coordinate(dx, dy, dz - SHAFT_LENGTH),
    ordered_items=create_ordered_items_2d(
      TipMountingShaft,
      name_prefix=name,
      num_items_x=columns,
      num_items_y=rows,
      # A channel position is an axis, and these place a corner, so each shaft is set back by its
      # own radius to leave its axis where the grid says the channel is.
      dx=dx - SHAFT_DIAMETER / 2,
      dy=dy - array_y - SHAFT_DIAMETER / 2,
      # The shafts are what reaches lowest, so they hang below the plane the body starts at rather
      # than standing on it: their own length below `dz`, which puts their ends at the bottom of
      # everything and the body a shaft's length clear of it.
      dz=dz - SHAFT_LENGTH,
      item_dx=pitch,
      item_dy=pitch,
      tip_pickup_mode="core",
    ),
    category=model,
    model=model,
  )


def head96(
  name: str,
  size_x: Optional[float] = 160.0,
  size_y: Optional[float] = 120.0,
  size_z: float = 0.0,
  dx: Optional[float] = None,
  dy: Optional[float] = None,
  dz: float = 0.0,
) -> NChannelPipette:
  """The 96-head: 96 channels on a 12 by 8 grid at 9 mm, moving as one.

  The drives report channel A1, so that is what `reference_point` is. The body is measured, and the
  channel array sits centred in it - which is where the margins on every side come from.

  Args:
    name: what to call this one.
    size_x: how wide the head's body is, in mm. None spans the channel array instead.
    size_y: how deep the body is, in mm. None spans the channel array instead.
    size_z: how tall it is, from its stop disc to its top, in mm. Zero leaves it unmodelled.
    dx: how far channel A1 sits from the body's left edge, in mm. None centres the array.
    dy: how far channel A1 sits from its front edge, in mm. None centres the array.
    dz: how far channel A1 sits above its bottom, in mm.

  Returns:
    The pipette.
  """
  return _rigid_head(name, 12, 8, 9.0, "head96", size_x, size_y, size_z, dx, dy, dz)


def head384(
  name: str,
  size_x: Optional[float] = 160.0,
  size_y: Optional[float] = 120.0,
  size_z: float = 0.0,
  dx: Optional[float] = None,
  dy: Optional[float] = None,
  dz: float = 0.0,
) -> NChannelPipette:
  """The 384-head: 384 channels on a 24 by 16 grid at 4.5 mm, moving as one.

  Measured from channel A1, and sharing the 96-head's body, which the two are built around. Its
  array is denser, so it leaves wider margins in the same envelope.

  Args:
    name: what to call this one.
    size_x: how wide the head's body is, in mm. None spans the channel array instead.
    size_y: how deep the body is, in mm. None spans the channel array instead.
    size_z: how tall it is, from its collar bearing to its top, in mm. Zero leaves it unmodelled.
    dx: how far channel A1 sits from the body's left edge, in mm. None centres the array.
    dy: how far channel A1 sits from its front edge, in mm. None centres the array.
    dz: how far channel A1 sits above its bottom, in mm.

  Returns:
    The pipette.
  """
  return _rigid_head(name, 24, 16, 4.5, "head384", size_x, size_y, size_z, dx, dy, dz)
