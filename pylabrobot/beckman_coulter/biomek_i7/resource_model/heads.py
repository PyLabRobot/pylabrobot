"""The Biomek i7's pipetting heads in the resource tree, as the STAR's are: the Multichannel head an
`NChannelPipette` of 96 `TipMountingShaft`s (category `head96`), each Span-8 probe a channel
(category `pipette_channel`) carrying one shaft. A tip a head carries is a child of its shaft
(`show_tip`), so it rides the head and the viewer draws it.

Sized in centimetres from the instrument file's pod entry (`HeadBodyXSpan`, `HeadHeight`,
`HeadFlangeHeight`, `Head/PodToMandrel1`, `PodToHeadBody*Offset`, `Mandrel1*Offset`, the mandrels'
spacing; `ProbeSize`, `MandrelExtender`, `DefaultTipHubInfo`), else the pod type's values. Biomek's
Z names the bare mandrels' end (design.md I9). The head hangs from the pod by its flange: the body
reaches `HeadHeight` below the pod, and the mandrels end `Head/PodToMandrel1` Z below it, so they
stand `TipSeatingDepth` (the difference) out of the body, and a seated tip's top meets the body. A
Span-8 probe reaches `MandrelExtender` below its channel. A value marked `assumed` has no source
yet.
"""

from typing import Any, Mapping, Optional

from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import PodGeometry
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.n_channel_pipettes import (
  SHAFT_DIAMETER,
  NChannelPipette,
  TipMountingShaft,
)
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.utils import create_ordered_items_2d

from .units import cm

# The Multichannel head. PodTypes/MC: HeadBodyXSpan, HeadBodyYSpan, HeadHeight, HeadFlangeHeight,
# Head/PodToMandrel1 (Z: the mandrels' end below the pod), PodToHeadBodyXOffset,
# PodToHeadBodyYOffset; the pod entry's Mandrel1X/YOffset and spacing.
HEAD_BODY_X_SPAN = 13.0175
HEAD_BODY_Y_SPAN = 7.9375
HEAD_HEIGHT = 9.8171
HEAD_FLANGE_HEIGHT = 1.0795
POD_TO_MANDREL1_Z = -10.5245
POD_TO_HEAD_BODY_X_OFFSET = 2.795
POD_TO_HEAD_BODY_Y_OFFSET = 1.74625
MANDREL1_X_OFFSET = 4.3537
MANDREL1_Y_OFFSET = 2.565
MANDREL_SPACING = 0.9
ROWS, COLUMNS = 8, 12

# A Span-8 probe. ProbeSize (the probes' narrowest spacing), MandrelExtender (the probe below its
# channel), DefaultTipHubInfo MaxRadius (the tip hub at the probe's end); PodXSpan and
# Mandrel1XOffset (the channel's extent in X about its probe, as the pod's).
PROBE_SIZE = 0.9
PROBE_LENGTH = 5.7899
PROBE_HUB_MAX_RADIUS = 0.3429
SPAN8_POD_X_SPAN = 4.0
SPAN8_MANDREL1_X_OFFSET = 1.0
PROBES = 8
# assumed: how tall a probe's channel is drawn above its probe. Not a Biomek value.
SPAN8_CHANNEL_HEIGHT = 3.0
# assumed: the gap left between two channels' bodies at the narrowest spacing, so each reads as
# its own. Not a Biomek value.
SPAN8_CHANNEL_GAP = 0.1

MODEL_HEAD_96 = "beckman_coulter_biomek_i7_head_96"
MODEL_SPAN8_CHANNEL = "beckman_coulter_biomek_i7_span8_channel"


def head96(
  name: str,
  settings: Optional[Mapping[str, Any]] = None,
  geometry: Optional[PodGeometry] = None,
  pod_y_span: Optional[float] = None,
) -> NChannelPipette:
  """The Multichannel head: its flange and body, with a shaft at each mandrel standing out of the
  body's bottom (`mandrel_protrusion`), A1 at the back left (`reference_point`, at the shafts' end:
  the head's bottom). Its origin is the body's front left corner at the mandrels' end; it hangs
  `PodToHeadBody*Offset` in from the carriage's front left corner (`head96_location`).

  Args:
    name: what to call it; its shafts are `<name>_tipmountingshaft_A1` .. `_H12`.
    settings: the pod's settings (the instrument file's pod entry); the pod type's values for those
      missing.
    geometry: the pod's geometry, for the mandrels' offsets and spacing.
    pod_y_span: the carriage's depth (cm), from which mandrel A1 is `Mandrel1YOffset` forward.
  """
  s = dict(settings or {})
  body_x = float(s.get("HeadBodyXSpan", HEAD_BODY_X_SPAN))
  body_y = float(s.get("HeadBodyYSpan", HEAD_BODY_Y_SPAN))
  height = head96_height(s)
  protrusion = mandrel_protrusion(s)
  to_x = float(s.get("PodToHeadBodyXOffset", POD_TO_HEAD_BODY_X_OFFSET))
  to_y = float(s.get("PodToHeadBodyYOffset", POD_TO_HEAD_BODY_Y_OFFSET))
  m1x = geometry.mandrel1_x_offset if geometry is not None else MANDREL1_X_OFFSET
  m1y = geometry.mandrel1_y_offset if geometry is not None else MANDREL1_Y_OFFSET
  pitch_x = geometry.mandrel_x_spacing if geometry is not None else MANDREL_SPACING
  pitch_y = geometry.mandrel_y_spacing if geometry is not None else MANDREL_SPACING
  depth = pod_y_span if pod_y_span is not None else (geometry.pod_y_span if geometry else 11.43)
  # Mandrel A1 in the body's frame: X from the carriage's left edge, Y back from its front edge
  # (the deck's y grows to the back, Biomek's to the front).
  a1_x, a1_y = cm(m1x - to_x), cm(depth - m1y - to_y)
  half = SHAFT_DIAMETER / 2
  return NChannelPipette(
    name=name,
    size_x=cm(body_x),
    size_y=cm(body_y),
    size_z=cm(height),
    reference_point=Coordinate(a1_x, a1_y, 0.0),
    ordered_items=create_ordered_items_2d(
      TipMountingShaft,
      name_prefix=name,
      num_items_x=COLUMNS,
      num_items_y=ROWS,
      # A shaft is placed by its corner; its axis is the mandrel's. Row A is the back one.
      dx=a1_x - half,
      dy=a1_y - (ROWS - 1) * cm(pitch_y) - half,
      # The mandrels' ends are the head's bottom: the shafts stand from there to the body.
      dz=0.0,
      item_dx=cm(pitch_x),
      item_dy=cm(pitch_y),
      tip_pickup_mode="friction",
      size_z=cm(protrusion),
    ),
    category="head96",
    model=MODEL_HEAD_96,
  )


def head96_height(settings: Optional[Mapping[str, Any]] = None) -> float:
  """How far the head reaches below its pod, flange to the mandrels' end (cm):
  `Head/PodToMandrel1` Z."""
  s = dict(settings or {})
  to_mandrel1 = s.get("Head/PodToMandrel1") or {}
  return -float(to_mandrel1.get("Z", POD_TO_MANDREL1_Z))


def mandrel_protrusion(settings: Optional[Mapping[str, Any]] = None) -> float:
  """How far the mandrels stand out of the head's body (cm): the head's reach below its pod less
  the body's (`HeadHeight`). Equal to the head's `TipSeatingDepth`: a seated tip meets the body."""
  s = dict(settings or {})
  return head96_height(s) - float(s.get("HeadHeight", HEAD_HEIGHT))


def head96_location(settings: Optional[Mapping[str, Any]] = None) -> Coordinate:
  """Where the head's body sits in its carriage, x and y (mm); z is where the head is."""
  s = dict(settings or {})
  return Coordinate(
    cm(float(s.get("PodToHeadBodyXOffset", POD_TO_HEAD_BODY_X_OFFSET))),
    cm(float(s.get("PodToHeadBodyYOffset", POD_TO_HEAD_BODY_Y_OFFSET))),
    0.0,
  )


def span8_channel(
  name: str,
  settings: Optional[Mapping[str, Any]] = None,
  mandrel1_x_offset: float = SPAN8_MANDREL1_X_OFFSET,
  pod_x_span: float = SPAN8_POD_X_SPAN,
) -> Resource:
  """A Span-8 probe's channel, with its probe a shaft hanging below it, the channel measured from
  the probe's end (`reference_point`), as the STAR's channels are (`add_tip_mounting_shaft`).

  The channel spans the pod in X (`pod_x_span`, cm), its probe `mandrel1_x_offset` (cm) in from its
  left edge, and is `ProbeSize` deep less `SPAN8_CHANNEL_GAP`, so eight at their narrowest spacing
  stand side by side as eight. The probe is `MandrelExtender` long and as wide as the tip hub at
  its end (`DefaultTipHubInfo` MaxRadius)."""
  s = dict(settings or {})
  depth = cm(float(s.get("ProbeSize", PROBE_SIZE)) - SPAN8_CHANNEL_GAP)
  width = cm(pod_x_span)
  axis_x = cm(mandrel1_x_offset)
  length = cm(float(s.get("MandrelExtender", PROBE_LENGTH)))
  hub = s.get("DefaultTipHubInfo") or {}
  diameter = cm(2 * float(hub.get("MaxRadius", PROBE_HUB_MAX_RADIUS)))
  channel = Resource(
    name=name,
    size_x=width,
    size_y=depth,
    size_z=cm(SPAN8_CHANNEL_HEIGHT),
    category="pipette_channel",
    model=MODEL_SPAN8_CHANNEL,
  )
  shaft = TipMountingShaft(
    name=f"{name}_tip_mounting_shaft",
    tip_pickup_mode="friction",
    size_x=diameter,
    size_y=diameter,
    size_z=length,
  )
  channel.assign_child_resource(
    shaft, location=Coordinate(axis_x - diameter / 2, (depth - diameter) / 2, -length)
  )
  # Stated on a plain `Resource`, as the STAR states it on its channels.
  channel.reference_point = Coordinate(axis_x, depth / 2, -length)  # type: ignore[attr-defined]
  return channel


def shaft_of(channel: Resource) -> TipMountingShaft:
  """A Span-8 channel's shaft."""
  for child in channel.children:
    if isinstance(child, TipMountingShaft):
      return child
  raise ValueError(f"{channel.name} carries no tip mounting shaft")


def show_tip(shaft: TipMountingShaft, tip: Optional[Tip]) -> None:
  """Put `tip` on `shaft` in the tree, or take whatever it carries off (None).

  A tip already on the shaft stays. A tip that cannot go in the tree as it is - one held on
  another shaft, or whose name the tree already has - is shown by a copy named after the
  shaft."""
  if shaft.tip is tip and tip is not None:
    return
  if shaft.has_tip():
    shaft.release_tip()
  if tip is None:
    return
  if isinstance(tip.parent, TipMountingShaft) or _name_taken(shaft, tip):
    tip = Tip.deserialize({**tip.serialize(), "name": f"{shaft.name}_tip"})
    assert isinstance(tip, Tip)
  shaft.mount_tip(tip)


def _name_taken(shaft: Resource, tip: Tip) -> bool:
  root = shaft.get_root()
  if tip.parent is not None and tip.get_root() is root:
    return False  # it is the one by that name, and moves rather than copies
  try:
    root.get_resource(tip.name)
  except Exception:
    return False
  return True
