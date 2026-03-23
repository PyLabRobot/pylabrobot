import warnings
from typing import List, Optional, Tuple

from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS = 9
MIN_SPACING_BETWEEN_CHANNELS = GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS
# minimum spacing between the edge of the container and the border of a pipette
MIN_SPACING_EDGE = 2.0


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


def _resolve_channel_spacings(
  num_channels: int,
  channel_spacings: Optional[List[float]] = None,
) -> List[float]:
  """Resolve channel_spacings to a validated list of per-pair spacings."""
  expected = max(num_channels - 1, 0)
  if channel_spacings is None:
    return [GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS] * expected
  if expected == 0:
    return []
  if len(channel_spacings) != expected:
    raise ValueError(
      f"channel_spacings has {len(channel_spacings)} entries, "
      f"expected {expected} (num_channels - 1)."
    )
  return channel_spacings


def _position_channels_wide(
  resource_size: float,
  num_channels: int,
  spacings: List[float],
) -> List[float]:
  """Compute channel Y centers spread wide across a single region.

  Distributes channels as far apart as possible while respecting minimum spacings.
  Surplus space is shared equally across all gaps (n+1 slots: edges + between channels).
  Returns centers in front-to-back order (ascending Y).
  """
  if num_channels == 1:
    return [resource_size / 2]
  min_spacing = min(spacings)
  needed = sum(spacings)
  if resource_size < MIN_SPACING_EDGE * 2 + needed:
    raise ValueError("Resource is too small to space channels.")
  # If there's enough room for equal distribution (margins + gaps), use it
  if resource_size - needed > min_spacing * 2:
    # Evenly distribute across n+1 slots (same as old _get_centers_with_margin)
    return [(i + 1) * resource_size / (num_channels + 1) for i in range(num_channels)]
  # Otherwise, tight distribution with edge margins
  remaining_space = resource_size - needed - MIN_SPACING_EDGE * 2
  return [MIN_SPACING_EDGE + remaining_space / 2 + sum(spacings[:i]) for i in range(num_channels)]


def _position_channels_tight(
  resource_size: float,
  num_channels: int,
  spacings: List[float],
) -> List[float]:
  """Compute channel Y centers packed tight in the center of a single region.

  Channels are placed at minimum spacings, centered in the region.
  Returns centers in front-to-back order (ascending Y).
  """
  if num_channels == 1:
    return [resource_size / 2]
  needed = sum(spacings)
  start = (resource_size - needed) / 2
  if start < MIN_SPACING_EDGE:
    raise ValueError("Resource is too small to space channels.")
  centers = [start]
  for s in spacings:
    centers.append(centers[-1] + s)
  return centers


def _centers_to_offsets(centers: List[float], resource: Resource) -> List[Coordinate]:
  """Convert absolute Y centers to offsets relative to the resource center, sorted back-to-front."""
  center_y = resource.center().rotated(resource.get_absolute_rotation()).y
  offsets = [Coordinate(x=0, y=c - center_y, z=0) for c in centers]
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
      If None, defaults to 9mm for all pairs.

  Returns:
    List of Y offsets relative to the resource center, sorted back-to-front (descending Y).

  Raises:
    ValueError: If channels cannot fit, or if spread is not "wide", "tight", or "custom".
  """
  if spread == "custom":
    return [Coordinate.zero()] * num_channels
  if spread not in ("wide", "tight"):
    raise ValueError(
      f"Invalid value for 'spread': {spread!r}. Must be 'wide', 'tight', or 'custom'."
    )

  spacings = _resolve_channel_spacings(num_channels, channel_spacings)

  # Container with no-go zones: distribute across compartments
  if isinstance(resource, Container) and resource.no_go_zones:
    compartments = _get_compartments(resource)
    if not compartments:
      raise ValueError(
        f"Cannot fit {num_channels} channels into the compartments of "
        f"'{resource.name}' while respecting its no-go zones. "
        f"Use fewer channels or spread='custom' with manual offsets."
      )

    n_comp = len(compartments)
    base = num_channels // n_comp
    remainder = num_channels % n_comp
    center_idx = (n_comp - 1) / 2
    priority = sorted(range(n_comp), key=lambda i: (abs(i - center_idx), -i))
    distribution = [base] * n_comp
    for i in priority[:remainder]:
      distribution[i] += 1

    container_center_y = resource.get_size_y() / 2
    offsets = []
    spacing_idx = 0

    for (comp_lo, comp_hi), n_ch in zip(compartments, distribution):
      if n_ch == 0:
        continue
      comp_width = comp_hi - comp_lo
      group_spacings = spacings[spacing_idx : spacing_idx + n_ch - 1]
      spacing_idx += max(n_ch - 1, 0)
      needed = sum(group_spacings)
      if comp_width < needed:
        raise ValueError(
          f"Cannot fit {num_channels} channels into the compartments of "
          f"'{resource.name}' while respecting its no-go zones. "
          f"Use fewer channels or spread='custom' with manual offsets."
        )

      if n_ch == 1:
        centers = [(comp_lo + comp_hi) / 2]
      elif spread == "wide":
        min_gap = min(group_spacings) if group_spacings else GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS
        even_gap = comp_width / (n_ch + 1)
        if even_gap >= min_gap:
          # Even distribution with equal edge margins
          centers = [comp_lo + (i + 1) * comp_width / (n_ch + 1) for i in range(n_ch)]
        else:
          # Compartment too small for even distribution; use tight (centered) instead
          start = (comp_lo + comp_hi) / 2 - needed / 2
          centers = [start]
          for s in group_spacings:
            centers.append(centers[-1] + s)
      else:
        start = (comp_lo + comp_hi) / 2 - needed / 2
        centers = [start]
        for s in group_spacings:
          centers.append(centers[-1] + s)

      for c in centers:
        offsets.append(Coordinate(0, c - container_center_y, 0))

    offsets.sort(key=lambda o: o.y, reverse=True)
    return offsets

  # Plain resource (no no-go zones): wide or tight across full Y
  resource_size = resource.get_absolute_size_y()
  if spread == "wide":
    centers = _position_channels_wide(resource_size, num_channels, spacings)
  else:
    centers = _position_channels_tight(resource_size, num_channels, spacings)
  return _centers_to_offsets(centers, resource)


# ---------------------------------------------------------------------------
# Deprecated wrappers (remove after 2026-09)
# ---------------------------------------------------------------------------


def get_wide_single_resource_liquid_op_offsets(
  resource: Resource,
  num_channels: int,
  min_spacing: float = GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS,
) -> List[Coordinate]:
  """Deprecated. Use ``compute_channel_offsets(resource, num_channels, spread='wide')`` instead."""
  warnings.warn(
    "get_wide_single_resource_liquid_op_offsets() is deprecated and will be removed after 2026-09. "
    "Use compute_channel_offsets(resource, num_channels, spread='wide') instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  spacings = [min_spacing] * max(num_channels - 1, 0)
  return compute_channel_offsets(resource, num_channels, spread="wide", channel_spacings=spacings)


def get_tight_single_resource_liquid_op_offsets(
  resource: Resource,
  num_channels: int,
  min_spacing: float = GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS,
) -> List[Coordinate]:
  """Deprecated. Use ``compute_channel_offsets(resource, num_channels, spread='tight')`` instead."""
  warnings.warn(
    "get_tight_single_resource_liquid_op_offsets() is deprecated and will be removed after 2026-09. "
    "Use compute_channel_offsets(resource, num_channels, spread='tight') instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  spacings = [min_spacing] * max(num_channels - 1, 0)
  return compute_channel_offsets(resource, num_channels, spread="tight", channel_spacings=spacings)
