"""Repeated position grids a resource lays things out on, described as data.

A grid is where the first position is, how far apart they are, how many there are and how they
are labelled. It is read off the resource where it declares one, and derived from its public
API otherwise.
"""

import logging
from typing import Any, Dict, List, Optional

from pylabrobot.resources.resource import Resource

logger = logging.getLogger(__name__)

# How close a child's own y has to be to the rail line to count as standing on it, in mm.
SEATED_TOLERANCE = 0.5

# How often a rail gets a number. Legacy labels the first and then every fifth, which is dense
# enough to count from and sparse enough to read.
DEFAULT_LABEL_EVERY = 5


def describe_bands(resource: Resource) -> Optional[List[Dict[str, Any]]]:
  """The `access_bands` a resource declares, or None.

  Each band is `{"label": str, "from": float, "to": float}` in the resource's own frame, with
  optional `x_from` and `x_to` bounding it along the other axis. A malformed band is logged and
  skipped.
  """
  bands = getattr(resource, "access_bands", None)
  if not bands:
    return None
  described: List[Dict[str, Any]] = []
  for band in bands:
    try:
      described.append(
        {
          "label": str(band["label"]),
          "from": float(band["from"]),
          "to": float(band["to"]),
          # Optional: how far the band runs along the other axis. Absent means the whole resource.
          **({"x_from": float(band["x_from"])} if "x_from" in band else {}),
          **({"x_to": float(band["x_to"])} if "x_to" in band else {}),
        }
      )
    except (KeyError, TypeError, ValueError):
      logger.warning("ignoring malformed access band on %s: %r", resource.name, band)
  return described or None


def describe_grid(resource: Resource) -> Optional[Dict[str, Any]]:
  """The repeated grid this resource lays positions on, or None if it lays none.

  A declared `position_grid` is returned as is; otherwise it is derived from `track_to_location`
  or `rail_to_location` and the matching count.

  Returns:
    A dict in the resource's own frame: `axis`, which way the grid runs; `count`; `spacing` in mm;
    `origin`, the first position as [x, y, z] in mm; `extent`, how far a mark runs across the
    resource in mm; `label_every`, label the first and then every nth; `label`, what one
    position is called.
  """
  # A resource may state its grid, as it states its access bands: a loading tray's markings line
  # up with the deck it feeds, so nothing about itself could derive them.
  declared = getattr(resource, "position_grid", None)
  if declared:
    return dict(declared)

  # A deck says where its positions are under whatever it calls them: `track` or `rail`, and the
  # marks are labelled under the name it uses.
  named = next(
    (
      (word, getattr(resource, f"{word}_to_location"))
      for word in ("track", "rail")
      if callable(getattr(resource, f"{word}_to_location", None))
    ),
    None,
  )
  if named is None:
    return None
  word, locate = named
  count = next(
    (
      value
      for attribute in (f"num_{word}s", f"num_{word}es")
      if isinstance(value := getattr(resource, attribute, None), int)
    ),
    None,
  )
  if not isinstance(count, int) or count < 2:
    return None

  try:
    first, second = locate(1), locate(2)
  except Exception as e:
    logger.debug("%s declined to locate its rails: %s", getattr(resource, "name", resource), e)
    return None

  spacing = second.x - first.x
  if spacing <= 0:
    return None

  # A mark runs to the back of what the positions carry, not of the resource: the deepest child
  # seated on the line (sharing its y), or the resource's own depth when nothing stands on it.
  try:
    depth = resource.get_absolute_size_y()
  except Exception:
    return None

  seated: List[float] = []
  for child in getattr(resource, "children", []):
    location = getattr(child, "location", None)
    if location is None or abs(location.y - first.y) > SEATED_TOLERANCE:
      continue
    try:
      seated.append(child.get_absolute_size_y())
    except Exception:
      continue

  return {
    "axis": "x",
    "count": count,
    "spacing": round(spacing, 4),
    "origin": [round(first.x, 4), round(first.y, 4), round(first.z, 4)],
    "extent": round(max(seated) if seated else max(depth - first.y, 0.0), 4),
    "label_every": DEFAULT_LABEL_EVERY,
    "label": word,
  }
