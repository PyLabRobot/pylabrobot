import warnings
from typing import List, Optional, Tuple

from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS = 9
MIN_SPACING_BETWEEN_CHANNELS = GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS
# minimum spacing between the edge of the container and the border of a pipette
MIN_SPACING_EDGE = 2.0


def _get_centers_with_margin(dim_size: float, n: int, margin: float, min_spacing: float):
  """Get the centers of the channels with a minimum margin on the edges."""
  if n == 1:
    return [dim_size / 2]
  if dim_size < margin * 2 + (n - 1) * min_spacing:
    raise ValueError("Resource is too small to space channels.")
  if dim_size - (n - 1) * min_spacing <= min_spacing * 2:
    remaining_space = dim_size - (n - 1) * min_spacing - margin * 2
    return [margin + remaining_space / 2 + i * min_spacing for i in range(n)]
  return [(i + 1) * dim_size / (n + 1) for i in range(n)]


def get_wide_single_resource_liquid_op_offsets(
  resource: Resource,
  num_channels: int,
  min_spacing: float = GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS,
) -> List[Coordinate]:
  resource_size = resource.get_absolute_size_y()
  centers = list(
    reversed(
      _get_centers_with_margin(
        dim_size=resource_size,
        n=num_channels,
        margin=MIN_SPACING_EDGE,
        min_spacing=min_spacing,
      )
    )
  )  # reverse because channels are from back to front

  # offsets are relative to the center of the resource, but above we computed them wrt lfb
  # so we need to subtract the center of the resource
  # also, offsets are in absolute space, so we need to rotate the center
  return [
    Coordinate(
      x=0,
      y=c - resource.center().rotated(resource.get_absolute_rotation()).y,
      z=0,
    )
    for c in centers
  ]


def get_tight_single_resource_liquid_op_offsets(
  resource: Resource,
  num_channels: int,
  min_spacing: float = GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS,
) -> List[Coordinate]:
  channel_space = (num_channels - 1) * min_spacing

  min_y = (resource.get_absolute_size_y() - channel_space) / 2
  if min_y < MIN_SPACING_EDGE:
    raise ValueError("Resource is too small to space channels.")

  centers = [min_y + i * min_spacing for i in range(num_channels)][::-1]

  # offsets are relative to the center of the resource, but above we computed them wrt lfb
  # so we need to subtract the center of the resource
  # also, offsets are in absolute space, so we need to rotate the center
  return [
    Coordinate(
      x=0,
      y=c - resource.center().rotated(resource.get_absolute_rotation()).y,
      z=0,
    )
    for c in centers
  ]


def _get_compartments(
  container: Container,
  edge_clearance: float = MIN_SPACING_EDGE,
) -> List[Tuple[float, float]]:
  """Compute the usable Y compartments within a container created by no-go zones.

  Each compartment is the free-space region between no-go zones and/or container walls,
  shrunk by ``edge_clearance`` on each side.

  Args:
    container: The container whose no-go zones define the compartments.
    edge_clearance: Minimum clearance between the pipette border and a compartment
      boundary (container wall or no-go zone) in mm.

  Returns:
    List of (y_min, y_max) tuples representing usable Y ranges for channel centers.
  """
  container_y = container.get_size_y()
  zones = sorted(container.no_go_zones, key=lambda z: z[0].y)

  raw_compartments = []
  prev_end = 0.0
  for flb, brt in zones:
    if flb.y > prev_end:
      raw_compartments.append((prev_end, flb.y))
    prev_end = max(prev_end, brt.y)
  if prev_end < container_y:
    raw_compartments.append((prev_end, container_y))

  usable = []
  for lo, hi in raw_compartments:
    raw_width = hi - lo
    usable_lo = lo + edge_clearance
    usable_hi = hi - edge_clearance
    if usable_hi > usable_lo:
      usable.append((usable_lo, usable_hi))
    elif raw_width > 0:
      warnings.warn(
        f"Compartment Y=[{lo:.1f}, {hi:.1f}] (width={raw_width:.1f}mm) is smaller than "
        f"2 * edge_clearance ({2 * edge_clearance:.1f}mm). Automatic channel positioning will "
        f"skip this compartment. Ensure the attached tip physically fits in the container.",
        stacklevel=3,
      )
  return usable


def center_channels_in_compartments(
  container: Container,
  num_channels: int,
  channel_spacings: Optional[List[float]] = None,
  edge_clearance: float = MIN_SPACING_EDGE,
  spread: str = "tight",
) -> Optional[List[Coordinate]]:
  """Distribute channels across compartments created by no-go zones.

  Divides the channels by the number of compartments, then positions each group within its
  compartment according to the spread mode. Channels are distributed center-out, then back-first.

  Args:
    container: The container with no-go zones that define compartments.
    num_channels: Number of channels to distribute.
    channel_spacings: Per-adjacent-pair minimum spacings in mm. Length must be
      ``num_channels - 1``. If None, uses ``GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS`` (9mm)
      for all pairs.
    edge_clearance: Minimum clearance between the edge of a pipette and a compartment
      boundary (container wall or no-go zone) in mm.
    spread: How to position channels within each compartment:
      - "wide": spread channels as far apart as possible within the compartment
      - "tight": pack channels at minimum spacing, centered in the compartment

  Returns:
    List of Y offsets (relative to container center) for each channel, sorted back-to-front
    (descending Y), or None if the channels cannot fit.
  """
  if spread not in ("wide", "tight"):
    raise ValueError(f"Invalid value for 'spread': {spread!r}. Must be 'wide' or 'tight'.")

  if not container.no_go_zones:
    return None

  if channel_spacings is None:
    channel_spacings = [GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS] * max(num_channels - 1, 0)
  elif len(channel_spacings) != max(num_channels - 1, 0):
    raise ValueError(
      f"channel_spacings has {len(channel_spacings)} entries, "
      f"expected {max(num_channels - 1, 0)} (num_channels - 1)."
    )

  compartments = _get_compartments(container, edge_clearance)
  if not compartments:
    return None

  n_comp = len(compartments)
  base = num_channels // n_comp
  remainder = num_channels % n_comp
  # distribute remainder center-out, then back-first:
  # rank compartments by distance from center (ascending), break ties back-first (descending index)
  center_idx = (n_comp - 1) / 2
  priority = sorted(range(n_comp), key=lambda i: (abs(i - center_idx), -i))
  distribution = [base] * n_comp
  for i in priority[:remainder]:
    distribution[i] += 1

  container_center_y = container.get_size_y() / 2
  offsets = []
  spacing_idx = 0  # tracks which pair spacings to consume

  for (comp_lo, comp_hi), n_ch in zip(compartments, distribution):
    if n_ch == 0:
      continue
    comp_width = comp_hi - comp_lo
    # get the spacings for channels assigned to this compartment
    group_spacings = channel_spacings[spacing_idx : spacing_idx + n_ch - 1]
    spacing_idx += max(n_ch - 1, 0)
    needed = sum(group_spacings)
    if comp_width < needed:
      return None

    if n_ch == 1:
      centers = [(comp_lo + comp_hi) / 2]
    elif spread == "wide":
      # spread channels as far apart as possible within the compartment,
      # distributing surplus space evenly across all gaps
      surplus = comp_width - needed
      gap_surplus = surplus / max(n_ch - 1, 1)
      wide_spacings = [s + gap_surplus for s in group_spacings]
      total = sum(wide_spacings)
      start = (comp_lo + comp_hi) / 2 - total / 2
      centers = [start]
      for s in wide_spacings:
        centers.append(centers[-1] + s)
    else:
      # tight: pack channels at minimum spacing, centered in the compartment
      start = (comp_lo + comp_hi) / 2 - needed / 2
      centers = [start]
      for s in group_spacings:
        centers.append(centers[-1] + s)

    for c in centers:
      offsets.append(Coordinate(0, c - container_center_y, 0))

  offsets.sort(key=lambda o: o.y, reverse=True)
  return offsets


def compute_channel_offsets(
  resource: Resource,
  num_channels: int,
  spread: str = "wide",
  channel_spacings: Optional[List[float]] = None,
) -> List[Coordinate]:
  """Compute Y offsets for positioning pipette channels in a resource.

  Single entry point for all channel positioning logic. Handles containers with no-go zones
  (distributing channels across compartments) and plain resources (wide/tight spread).

  Args:
    resource: The target resource (Container, Trough, Well, etc.).
    num_channels: Number of channels to position.
    spread: Positioning strategy:
      - "wide": spread channels as far apart as possible (respects no-go zones if present)
      - "tight": pack channels at minimum spacing (respects no-go zones if present)
      - "custom": return zero offsets (caller controls positioning)
    channel_spacings: Per-adjacent-pair minimum spacings in mm (length = num_channels - 1).
      Only used when the resource has no-go zones. If None, defaults to 9mm for all pairs.

  Returns:
    List of Y offsets relative to the resource center, sorted back-to-front (descending Y).

  Raises:
    ValueError: If channels cannot fit into the compartments of a container with no-go zones,
      or if spread is not one of "wide", "tight", or "custom".
  """
  if spread == "custom":
    return [Coordinate.zero()] * num_channels

  if num_channels > 1 and isinstance(resource, Container) and resource.no_go_zones:
    compartment_offsets = center_channels_in_compartments(
      resource, num_channels, channel_spacings=channel_spacings, spread=spread
    )
    if compartment_offsets is not None:
      return compartment_offsets
    raise ValueError(
      f"Cannot fit {num_channels} channels into the compartments of "
      f"'{resource.name}' while respecting its no-go zones. "
      f"Use fewer channels or spread='custom' with manual offsets."
    )

  if spread == "tight":
    return get_tight_single_resource_liquid_op_offsets(resource=resource, num_channels=num_channels)
  if spread == "wide":
    return get_wide_single_resource_liquid_op_offsets(resource=resource, num_channels=num_channels)
  raise ValueError(f"Invalid value for 'spread': {spread!r}. Must be 'tight', 'wide', or 'custom'.")
