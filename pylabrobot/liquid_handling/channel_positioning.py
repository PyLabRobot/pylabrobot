import logging
import math
import warnings
from typing import List, Optional, Tuple

from pylabrobot.liquid_handling.errors import ChannelsDoNotFitError
from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

logger = logging.getLogger(__name__)

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
      logger.info(
        "Compartment Y=[%.1f, %.1f] (width=%.1fmm) is smaller than "
        "2 * edge_clearance (%.1fmm). Automatic channel positioning will "
        "skip this compartment. Ensure the attached tip physically fits in the container.",
        lo,
        hi,
        raw_width,
        2 * edge_clearance,
      )
  return usable


def _resolve_channel_spacings(
  num_channels: int,
  channel_spacings: Optional[List[float]] = None,
) -> List[float]:
  """Resolve channel_spacings to a validated per-channel list.

  Args:
    num_channels: Number of channels.
    channel_spacings: Per-channel occupancy diameters (length = num_channels).
      Each value is the physical space the channel occupies.
      If None, defaults to GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS for all.

  Returns:
    List of per-channel spacings, length = num_channels.
  """
  if channel_spacings is None:
    return [GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS] * num_channels
  if num_channels <= 1:
    return channel_spacings[:num_channels]
  if len(channel_spacings) != num_channels:
    raise ValueError(
      f"channel_spacings has {len(channel_spacings)} entries, "
      f"expected {num_channels} (one per channel)."
    )
  return channel_spacings


def required_spacing_between(channel_spacings: List[float], i: int, j: int) -> float:
  """Compute the required center-to-center distance between channels i and j.

  Each channel's spacing value is its diameter - the minimum distance its center must maintain
  from any neighbor. For adjacent channels, the required distance is the sum of both channels'
  radii (half-spacings), ceiling-rounded to 0.1mm for safety. For non-adjacent channels, the
  distance is the sum of all intermediate adjacent-pair required spacings.

  Args:
    channel_spacings: Per-channel spacing values (one per channel). Each value is the channel's
      spacing diameter.
    i: Index of the first channel.
    j: Index of the second channel.

  Returns:
    Required center-to-center distance in mm, ceiling-rounded to 0.1mm.
  """
  lo, hi = min(i, j), max(i, j)
  if hi - lo == 1:
    return math.ceil((channel_spacings[lo] / 2 + channel_spacings[hi] / 2) * 10) / 10
  return sum(required_spacing_between(channel_spacings, k, k + 1) for k in range(lo, hi))


def _position_channels_wide(
  resource_size: float,
  channel_spacings: List[float],
) -> List[float]:
  """Compute channel Y centers spread wide across a single region.

  Distributes channels as far apart as possible while respecting per-channel spacing constraints.
  Edge clearance = each edge channel's radius (half its occupancy diameter).
  Returns centers in front-to-back order (ascending Y).
  """
  num_channels = len(channel_spacings)
  if num_channels == 1:
    return [resource_size / 2]

  gaps = [
    required_spacing_between(channel_spacings, i, i + 1) for i in range(len(channel_spacings) - 1)
  ]
  needed = sum(gaps)
  first_radius = channel_spacings[0] / 2
  last_radius = channel_spacings[-1] / 2
  usable = resource_size - first_radius - last_radius

  if usable < needed:
    raise ValueError("Resource is too small to space channels.")

  max_gap = max(gaps)
  classic_gap = resource_size / (num_channels + 1)
  if classic_gap >= max_gap:
    return [(i + 1) * resource_size / (num_channels + 1) for i in range(num_channels)]

  # Can't achieve equal spacing; center block like tight
  surplus = usable - needed
  start = first_radius + surplus / 2
  centers = [start]
  for g in gaps:
    centers.append(centers[-1] + g)
  return centers


def _position_channels_tight(
  resource_size: float,
  channel_spacings: List[float],
) -> List[float]:
  """Compute channel Y centers packed tight in the center of a single region.

  Channels are placed at minimum gap distances, centered in the region.
  Edge clearance = each edge channel's radius (half its occupancy diameter).
  Returns centers in front-to-back order (ascending Y).
  """
  num_channels = len(channel_spacings)
  if num_channels == 1:
    return [resource_size / 2]

  gaps = [
    required_spacing_between(channel_spacings, i, i + 1) for i in range(len(channel_spacings) - 1)
  ]
  needed = sum(gaps)
  first_radius = channel_spacings[0] / 2
  last_radius = channel_spacings[-1] / 2
  usable = resource_size - first_radius - last_radius

  if usable < needed:
    raise ValueError("Resource is too small to space channels.")

  surplus = usable - needed
  start = first_radius + surplus / 2
  centers = [start]
  for g in gaps:
    centers.append(centers[-1] + g)
  return centers


def _centers_to_offsets(centers: List[float], resource: Resource) -> List[Coordinate]:
  """Convert absolute Y centers to offsets relative to the resource center, sorted back-to-front."""
  center_y = resource.center().rotated(resource.get_absolute_rotation()).y
  offsets = [Coordinate(x=0, y=c - center_y, z=0) for c in centers]
  offsets.sort(key=lambda o: o.y, reverse=True)
  return offsets


def _space_needed(spacings: List[float]) -> float:
  """Compute the minimum space needed for a contiguous group of channels."""
  if len(spacings) <= 1:
    return 0.0
  return sum(required_spacing_between(spacings, i, i + 1) for i in range(len(spacings) - 1))


def _distribute_channels(
  compartments: List[Tuple[float, float]],
  num_channels: int,
  spacings: List[float],
) -> List[int]:
  """Distribute channels across compartments proportionally to compartment width.

  Uses largest-remainder rounding to convert fractional per-compartment ideal_channel_counts into integers,
  then iteratively shifts channels from overloaded compartments to neighbors until all groups
  fit. Detects cycles to avoid infinite loops.

  Args:
    compartments: List of (y_min, y_max) usable compartment ranges.
    num_channels: Total number of channels to distribute.
    spacings: Per-channel spacing values (length = num_channels).

  Returns:
    List of channel counts per compartment.

  Raises:
    ChannelsDoNotFitError: If no valid distribution exists.
  """
  n_comp = len(compartments)
  widths = [hi - lo for lo, hi in compartments]
  total_width = sum(widths)

  # Proportional ideal_channel_counts (largest-remainder rounding)
  ideal_channel_counts = [w / total_width * num_channels for w in widths]
  channel_distribution = [int(t) for t in ideal_channel_counts]
  remainders = [t - int(t) for t in ideal_channel_counts]
  deficit = num_channels - sum(channel_distribution)
  # Give extra channels to compartments with largest remainders.
  # Break ties by preferring back compartments (higher index = further back).
  for _ in range(deficit):
    idx = max(range(n_comp), key=lambda i: (remainders[i], i))
    channel_distribution[idx] += 1
    remainders[idx] = -1  # don't pick again

  # Validate: check each group fits
  def _fits(dist: List[int]) -> bool:
    idx = 0
    for comp_idx, n_ch in enumerate(dist):
      if n_ch == 0:
        idx += 0
        continue
      group = spacings[idx : idx + n_ch]
      needed = _space_needed(group)
      if needed > widths[comp_idx]:
        return False
      idx += n_ch
    return True

  # Shift channels from overloaded compartments to neighbors until everything fits.
  seen = set()
  while not _fits(channel_distribution):
    key = tuple(channel_distribution)
    if key in seen:
      raise ChannelsDoNotFitError(
        "Cannot distribute channels across compartments while respecting spacing constraints."
      )
    seen.add(key)

    improved = False
    idx = 0
    for comp_idx in range(n_comp):
      n_ch = channel_distribution[comp_idx]
      if n_ch == 0:
        continue
      group = spacings[idx : idx + n_ch]
      needed = _space_needed(group)
      if needed > widths[comp_idx] and n_ch > 1:
        if comp_idx + 1 < n_comp:
          channel_distribution[comp_idx] -= 1
          channel_distribution[comp_idx + 1] += 1
          improved = True
          break
        if comp_idx - 1 >= 0:
          channel_distribution[comp_idx] -= 1
          channel_distribution[comp_idx - 1] += 1
          improved = True
          break
      idx += n_ch
    if not improved:
      raise ChannelsDoNotFitError(
        "Cannot distribute channels across compartments while respecting spacing constraints."
      )

  return channel_distribution


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
    channel_spacings: Per-channel occupancy diameters in mm (length = num_channels).
      Each value is the physical space the channel occupies. The required gap between
      channels i and i+1 = spacing[i]/2 + spacing[i+1]/2 (sum of radii).
      If None, defaults to 9mm for all channels.

  Returns:
    List of Y offsets relative to the resource center, sorted back-to-front (descending Y).

  Raises:
    ChannelsDoNotFitError: If channels cannot fit within the resource's compartments.
    ValueError: If spread is not "wide", "tight", or "custom".
  """
  if spread == "custom":
    return [Coordinate.zero()] * num_channels
  if spread not in ("wide", "tight"):
    raise ValueError(
      f"Invalid value for 'spread': {spread!r}. Must be 'wide', 'tight', or 'custom'."
    )

  spacings = _resolve_channel_spacings(num_channels, channel_spacings)

  # channel_spacings are back-to-front (ch0=backmost first).
  # Compartments are processed front-to-back (ascending Y).
  # Reverse for compartment assignment so spacings[0] = frontmost channel.
  spacings_front_to_back = list(reversed(spacings))

  # Container with no-go zones: distribute across compartments
  if isinstance(resource, Container) and len(resource.no_go_zones) > 0:
    compartments = _get_compartments(resource)
    if not compartments:
      raise ChannelsDoNotFitError(
        f"Cannot fit {num_channels} channels into the compartments of "
        f"'{resource.name}' while respecting its no-go zones. "
        f"Use fewer channels or spread='custom' with manual offsets."
      )

    distribution = _distribute_channels(compartments, num_channels, spacings_front_to_back)

    container_center_y = resource.get_size_y() / 2
    offsets = []
    spacing_idx = 0

    for (comp_lo, comp_hi), n_ch in zip(compartments, distribution):
      if n_ch == 0:
        continue
      comp_width = comp_hi - comp_lo

      # Slice per-channel spacings for this group (front-to-back order)
      group_spacings = spacings_front_to_back[spacing_idx : spacing_idx + n_ch]
      spacing_idx += n_ch

      if n_ch == 1:
        if comp_width <= 0:
          raise ChannelsDoNotFitError(
            f"Cannot fit {num_channels} channels into the compartments of "
            f"'{resource.name}' while respecting its no-go zones. "
            f"Use fewer channels or spread='custom' with manual offsets."
          )
        centers = [(comp_lo + comp_hi) / 2]
      else:
        group_gaps = [
          required_spacing_between(group_spacings, i, i + 1) for i in range(len(group_spacings) - 1)
        ]
        needed = sum(group_gaps)
        # Edge clearance: first and last channel's radius must not extend past compartment
        first_radius = group_spacings[0] / 2
        last_radius = group_spacings[-1] / 2
        usable = comp_width - first_radius - last_radius

        if usable < needed:
          raise ChannelsDoNotFitError(
            f"Cannot fit {num_channels} channels into the compartments of "
            f"'{resource.name}' while respecting its no-go zones. "
            f"Use fewer channels or spread='custom' with manual offsets."
          )

        if spread == "wide":
          max_gap = max(group_gaps)
          classic_gap = comp_width / (n_ch + 1)
          if classic_gap >= max_gap:
            centers = [comp_lo + (i + 1) * comp_width / (n_ch + 1) for i in range(n_ch)]
          else:
            # Can't achieve equal spacing; center block like tight
            surplus = usable - needed
            start = comp_lo + first_radius + surplus / 2
            centers = [start]
            for g in group_gaps:
              centers.append(centers[-1] + g)
        else:
          # Tight: minimum gaps, centered within usable space
          surplus = usable - needed
          start = comp_lo + first_radius + surplus / 2
          centers = [start]
          for g in group_gaps:
            centers.append(centers[-1] + g)

      for c in centers:
        offsets.append(Coordinate(0, c - container_center_y, 0))

    # Validate cross-compartment gaps: channels at adjacent compartment boundaries
    # must respect their required spacing across the no-go zone.
    all_centers = sorted([container_center_y + o.y for o in offsets])
    ch_idx = 0
    for comp_i in range(len(distribution) - 1):
      n_a = distribution[comp_i]
      n_b = distribution[comp_i + 1]
      if n_a == 0 or n_b == 0:
        ch_idx += n_a
        continue
      last_in_a = all_centers[ch_idx + n_a - 1]
      first_in_b = all_centers[ch_idx + n_a]
      # Spacing indices: last channel of group A and first channel of group B
      spacing_idx_a = sum(distribution[: comp_i + 1]) - 1
      spacing_idx_b = spacing_idx_a + 1
      required = required_spacing_between(spacings_front_to_back, spacing_idx_a, spacing_idx_b)
      actual = first_in_b - last_in_a
      if actual < required - 0.05:
        raise ChannelsDoNotFitError(
          f"Cannot fit {num_channels} channels into the compartments of "
          f"'{resource.name}' while respecting spacing constraints across no-go zones "
          f"(gap {actual:.1f}mm < required {required:.1f}mm between compartments "
          f"{comp_i} and {comp_i + 1}). "
          f"Use fewer channels or spread='custom' with manual offsets."
        )
      ch_idx += n_a

    offsets.sort(key=lambda o: o.y, reverse=True)
    return offsets

  # Plain resource (no no-go zones): wide or tight across full Y
  resource_size = resource.get_absolute_size_y()
  if spread == "wide":
    centers = _position_channels_wide(resource_size, spacings_front_to_back)
  else:
    centers = _position_channels_tight(resource_size, spacings_front_to_back)
  return _centers_to_offsets(centers, resource)


# ---------------------------------------------------------------------------
# Deprecated wrappers (remove in v1)
# ---------------------------------------------------------------------------


def get_wide_single_resource_liquid_op_offsets(
  resource: Resource,
  num_channels: int,
  min_spacing: float = GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS,
) -> List[Coordinate]:
  """Deprecated. Use ``compute_channel_offsets(resource, num_channels, spread='wide')`` instead."""
  warnings.warn(
    "get_wide_single_resource_liquid_op_offsets() is deprecated and will be removed in v1. "
    "Use compute_channel_offsets(resource, num_channels, spread='wide') instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  per_channel = [min_spacing] * num_channels
  return compute_channel_offsets(
    resource, num_channels, spread="wide", channel_spacings=per_channel
  )


def get_tight_single_resource_liquid_op_offsets(
  resource: Resource,
  num_channels: int,
  min_spacing: float = GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS,
) -> List[Coordinate]:
  """Deprecated. Use ``compute_channel_offsets(resource, num_channels, spread='tight')`` instead."""
  warnings.warn(
    "get_tight_single_resource_liquid_op_offsets() is deprecated and will be removed in v1. "
    "Use compute_channel_offsets(resource, num_channels, spread='tight') instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  per_channel = [min_spacing] * num_channels
  return compute_channel_offsets(
    resource, num_channels, spread="tight", channel_spacings=per_channel
  )
