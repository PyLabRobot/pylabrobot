"""Biomek ALPs (Automated Labware Positioners): the accessories pinned onto the Biomek deck.

An ALP is a carrier: a body standing on the deck's pin grid, with one site per labware position.
Every number here is transcribed from the ALP's own definition file, as Biomek states it in
centimetres, and converted once through `cm`. The definitions are Beckman's
`C:\\ProgramData\\Beckman Coulter\\AlpDefinitions\\**\\<Type>.alp` (JSON, `AlpDefinitionV1`).

Biomek's deck Y runs from the back towards the front, PyLabRobot's from the front towards the back,
so an ALP definition's Y offsets, measured from the ALP's back edge, are flipped here. Where
Biomek's fields go:

- `XSpan`, `YSpan`, `ZSpan`: the ALP's size.
- `Pins.ColCount`, `Pins.RowCount`: how many deck pins it covers; `Pins.OffsetX`, `Pins.OffsetY`:
  its back left pin, from its back left corner. The deck places an ALP by its pins.
- `Positions[key].OffsetX`, `.OffsetY`: a site's back left corner, from the ALP's; `.OffsetZ`: how
  far below the ALP's top the site's floor is; `.XSpan`, `.YSpan`: the site's footprint;
  `.BaseName`: the prefix Biomek numbers this kind of position under on a deck (`P`, `TL`, `TR`).
- `Positions[key].AdaptorPlate.Z`: where labware sits from the position's floor (the tip-loading
  ALP's adaptor plate takes it 0.254 cm lower). Biomek's per-class `LabwareOffsets` are not kept:
  on that position every tip box's equals the adaptor plate's.
- `Positions[key].CustomData`'s `TipDisposalOffset*`: where the pods discard tips at a trash.
"""

from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from pylabrobot.resources.carrier import Carrier
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource_holder import ResourceHolder

from .units import cm


class Site(NamedTuple):
  """One labware position on an ALP, in Biomek's centimetres and Biomek's frame.

  Attributes:
    key: Biomek's name for it on the ALP (`A1`, `A2`, ...).
    base_name: what Biomek numbers it under on a deck (`P`, `TL`, `TR`, `W`, ...).
    offset_x: its left edge, from the ALP's left edge.
    offset_y: its back edge, from the ALP's back edge.
    offset_z: its floor, from the ALP's top.
    size_x: its width.
    size_y: its depth.
    adaptor_z: where labware sits from its floor (`AdaptorPlate.Z`).
    tip_disposal: where the pods discard tips, for a trash.
    head96_tip_loading: whether the 96-head may load tips here (the position's characteristic
      "Multichannel Can Load Tips": only the tip-loading ALP's, built to take the press).
  """

  key: str
  base_name: str
  offset_x: float
  offset_y: float
  offset_z: float
  size_x: float
  size_y: float
  adaptor_z: float = 0.0
  tip_disposal: Optional["TipDisposal"] = None
  head96_tip_loading: bool = False


class TipDisposal(NamedTuple):
  """Where the pods discard tips at a trash position, in Biomek's centimetres from the position's
  back left corner (Y forward), from the ALP's `CustomData`. Biomek's tip unload puts the
  96-head's mandrel A1 at (`x`, `y`) with the tips' ends `z` above the
  position, the tips' length being the effective one (`TipHeight` - `TipSeatingDepth`).

  Attributes:
    x: `TipDisposalOffsetX`, the 96-head's mandrel A1.
    y: `TipDisposalOffsetY`.
    z: `TipDisposalOffsetZ`, the tips' ends above the position.
    span8_x: `Span8TipDisposalOffsetX`.
    span8_y: `Span8TipDisposalOffsetY`.
  """

  x: float
  y: float
  z: float
  span8_x: float
  span8_y: float


class BiomekPosition(ResourceHolder):
  """A deck position on an ALP. Biomek centres the labware in it: a position is a little larger
  than the labware (12.819 x 8.59 cm against a tip box's 12.79 x 8.54), and Biomek's targets are the
  position's corner + half the difference + the labware's A1 offset. A labware set down here sits the
  same way, `labware_z` above the position's floor (the TL positions' adaptor plate: 2.54 mm lower).

  Attributes:
    labware_z: where labware sits from the position's floor, mm (`Site.adaptor_z`).
    tip_disposal: for a trash, where the pods discard tips.
    head96_tip_loading: whether the 96-head may load tips here (`Site.head96_tip_loading`).
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    labware_z: float = 0.0,
    tip_disposal: Optional[Sequence[float]] = None,
    head96_tip_loading: bool = False,
    **kwargs: Any,
  ):
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, **kwargs)
    self.head96_tip_loading = head96_tip_loading
    self.labware_z = labware_z
    self.tip_disposal = None if tip_disposal is None else TipDisposal(*tip_disposal)

  def get_default_child_location(self, resource) -> Coordinate:  # type: ignore[no-untyped-def]
    centred = Coordinate(
      (self.get_size_x() - resource.get_size_x()) / 2,
      (self.get_size_y() - resource.get_size_y()) / 2,
      self.labware_z,
    )
    return super().get_default_child_location(resource) + centred

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "labware_z": self.labware_z,
      "tip_disposal": None if self.tip_disposal is None else list(self.tip_disposal),
      "head96_tip_loading": self.head96_tip_loading,
    }


def alp_model(alp_type: str) -> str:
  """The model name of an ALP type: Biomek's name, namespaced, so its mesh file is unambiguous."""
  return "beckman_coulter_biomek_alp_" + alp_type.replace("-", "_")


class BiomekALP(Carrier[ResourceHolder]):
  """An ALP: a carrier that stands on the Biomek deck's pin grid.

  `site_keys` lists Biomek's key for each site in index order and `base_names` what Biomek numbers
  each under on a deck.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    pin_count: Tuple[int, int],
    pin_offset: Tuple[float, float],
    sites: Optional[Dict[int, ResourceHolder]] = None,
    site_keys: Optional[List[str]] = None,
    base_names: Optional[Dict[str, str]] = None,
    alp_type: Optional[str] = None,
    **kwargs: Any,
  ):
    """
    Args:
      name: what to call this one.
      size_x: its width, in mm.
      size_y: its depth, in mm.
      size_z: its height, in mm.
      pin_count: how many deck pins it covers, as (columns, rows).
      pin_offset: its back left pin, from its back left corner, as (right, forward) in mm.
      sites: its labware positions, by index.
      site_keys: Biomek's key for each site, in index order.
      base_names: what Biomek numbers each site under on a deck, by key.
      alp_type: the ALP type, as Biomek names it (`Static1x1`). The model is this, namespaced.
      kwargs: what `serialize` writes besides these (category, model), taken so a saved deck loads;
        a loaded ALP's sites come back as its children.
    """
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      sites=sites,
      category="alp",
      model=None if alp_type is None else alp_model(alp_type),
    )
    self.alp_type = alp_type
    self.pin_count = pin_count
    self.pin_offset = pin_offset
    self.site_keys = site_keys or []
    self.base_names = base_names or {}

  def get_site(self, key: str) -> ResourceHolder:
    """The site Biomek calls `key` on this ALP (`A1`)."""
    return self.sites[self.site_keys.index(key)]

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "alp_type": self.alp_type,
      "pin_count": list(self.pin_count),
      "pin_offset": list(self.pin_offset),
      "site_keys": self.site_keys,
      "base_names": self.base_names,
    }


def _alp(
  name: str,
  model: str,
  size: Tuple[float, float, float],
  pin_count: Tuple[int, int],
  pin_offset: Tuple[float, float],
  sites: List[Site],
  position_names: Optional[Sequence[str]],
) -> BiomekALP:
  """An ALP from its definition, every length in Biomek's centimetres.

  Args:
    position_names: what each site is called, in site order; `<name>_<key>` when not given. A deck
      layout passes the names Biomek gives the positions on the deck (`P1`, `TL1`, ...).
  """
  if position_names is None:
    position_names = [f"{name}_{site.key}" for site in sites]
  if len(position_names) != len(sites):
    raise ValueError(f"{model} has {len(sites)} positions, {len(position_names)} names were given")
  size_x, size_y, size_z = (cm(v) for v in size)
  holders: Dict[int, ResourceHolder] = {}
  for i, (site, position_name) in enumerate(zip(sites, position_names)):
    holder = BiomekPosition(
      name=position_name,
      size_x=cm(site.size_x),
      size_y=cm(site.size_y),
      size_z=0,
      labware_z=cm(site.adaptor_z),
      tip_disposal=site.tip_disposal,
      head96_tip_loading=site.head96_tip_loading,
    )
    # Carrier places a site at its own `location`. Biomek measures from the back edge.
    holder.location = Coordinate(
      cm(site.offset_x),
      size_y - cm(site.offset_y) - cm(site.size_y),
      size_z + cm(site.offset_z),
    )
    holders[i] = holder
  return BiomekALP(
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    pin_count=pin_count,
    pin_offset=(cm(pin_offset[0]), cm(pin_offset[1])),
    sites=holders,
    site_keys=[site.key for site in sites],
    base_names={site.key: site.base_name for site in sites},
    alp_type=model,
  )


# A standard titer-plate site on the static ALPs: 12.819 x 8.59 cm, 0.441 cm in from the left.
# Alps - Common\Static1x*.alp, TipLoad1x1.alp: Positions[*].XSpan, YSpan, OffsetX
_PLATE_SITE = (12.819, 8.59)
_STATIC_SITE_X = 0.441
# Positions[A1..A5].OffsetY on Static1x5.alp (Static1x1 and Static1x3 share the first ones).
_STATIC_SITE_Y = (0.441, 10.855, 21.269, 31.683, 42.097)

Names = Optional[Sequence[str]]


def _static(name: str, model: str, size_y: float, rows: int, sites: int, names: Names) -> BiomekALP:
  return _alp(
    name=name,
    model=model,
    size=(13.701, size_y, 15.875),  # XSpan, ZSpan as on every static ALP
    pin_count=(7, rows),
    pin_offset=(0.602, 0.57),  # Pins.OffsetX, OffsetY
    sites=[
      Site(f"A{i + 1}", "P", _STATIC_SITE_X, _STATIC_SITE_Y[i], 0.0, *_PLATE_SITE)
      for i in range(sites)
    ],
    position_names=names,
  )


def biomek_static_1x1(name: str, position_names: Names = None) -> BiomekALP:
  """Static 1x1 ALP: one titer-plate position. Alps - Common\\Static1x1.alp"""
  return _static(name, "Static1x1", size_y=9.472, rows=5, sites=1, names=position_names)


def biomek_static_1x3(name: str, position_names: Names = None) -> BiomekALP:
  """Static 1x3 ALP: three titer-plate positions, A1 at the back. Alps - Common\\Static1x3.alp"""
  return _static(name, "Static1x3", size_y=30.3, rows=15, sites=3, names=position_names)


def biomek_static_1x5(name: str, position_names: Names = None) -> BiomekALP:
  """Static 1x5 ALP: five titer-plate positions, A1 at the back. Alps - Common\\Static1x5.alp"""
  return _static(name, "Static1x5", size_y=51.128, rows=25, sites=5, names=position_names)


def biomek_tip_load_1x1(name: str, position_names: Names = None) -> BiomekALP:
  """Tip-loading ALP for the 96-head: one tip box, on an adaptor plate that takes it 0.254 cm lower
  than a static position would (`AdaptorPlate.Z`). Alps - Common\\TipLoad1x1.alp"""
  return _alp(
    name=name,
    model="TipLoad1x1",
    size=(13.701, 9.472, 16.129),  # XSpan, YSpan, ZSpan
    pin_count=(7, 5),
    pin_offset=(0.602, 0.57),
    sites=[
      Site(
        "A1",
        "TL",
        0.441,
        0.441,
        0.0,
        *_PLATE_SITE,
        adaptor_z=-0.254,
        head96_tip_loading=True,
      )
    ],
    position_names=position_names,
  )


def _trash(
  name: str,
  model: str,
  pin_offset_x: float,
  site_x: float,
  tip_disposal: TipDisposal,
  names: Names,
) -> BiomekALP:
  return _alp(
    name=name,
    model=model,
    size=(32.446, 14.418, 27.508),  # XSpan, YSpan, ZSpan
    pin_count=(7, 7),
    pin_offset=(pin_offset_x, 0.961),
    # The chute's opening sits 6.896 cm below the top.
    sites=[Site("A1", "TR", site_x, 2.9351, -6.896, *_PLATE_SITE, tip_disposal=tip_disposal)],
    position_names=names,
  )


def biomek_trash_left_bin(name: str, position_names: Names = None) -> BiomekALP:
  """Trash ALP hanging off the deck's left edge, into a bin. Alps - Common\\TrashLeftBin.alp"""
  return _trash(
    name,
    "TrashLeftBin",
    pin_offset_x=18.997,
    site_x=18.527,
    tip_disposal=TipDisposal(0.0, 1.124, 5.896, span8_x=-4.2186, span8_y=0.0),  # CustomData
    names=position_names,
  )


def biomek_trash_right_bin(name: str, position_names: Names = None) -> BiomekALP:
  """Trash ALP hanging off the deck's right edge, into a bin. Alps - Common\\TrashRightBin.alp"""
  return _trash(
    name,
    "TrashRightBin",
    pin_offset_x=0.953,
    site_x=1.1,
    tip_disposal=TipDisposal(5.9995, 1.124, 5.896, span8_x=4.561, span8_y=0.0),  # CustomData
    names=position_names,
  )


def biomek_wash_station_span8(name: str, position_names: Names = None) -> BiomekALP:
  """Span-8 wash station: a narrow trough the 8 probes dip into. Alps - Common\\WashStationSpan8.alp"""
  return _alp(
    name=name,
    model="WashStationSpan8",
    size=(5.715, 9.9568, 22.1361),
    pin_count=(2, 5),
    pin_offset=(2.9464, 0.8128),
    sites=[Site("A1", "W", 0.4953, 0.4788, 0.0, 3.175, 8.9992)],
    position_names=position_names,
  )


def biomek_orbital_shaker(name: str, position_names: Names = None) -> BiomekALP:
  """Orbital shaker ALP. Alps - Common\\OrbitalShaker.alp"""
  return _alp(
    name=name,
    model="OrbitalShaker",
    size=(14.802, 10.16, 16.134),
    pin_count=(7, 5),
    pin_offset=(1.118, 0.914),
    sites=[Site("A1", "Orbital", 0.815, 0.851, 0.0, *_PLATE_SITE)],
    position_names=position_names,
  )


def biomek_bioshake(name: str, position_names: Names = None) -> BiomekALP:
  """Q.Instruments BioShake ALP. Devices\\Q Instruments\\BioShake.alp"""
  return _alp(
    name=name,
    model="BioShake",
    size=(14.21, 10.0, 17.13),
    pin_count=(5, 5),
    pin_offset=(2.934, 0.834),
    sites=[Site("A1", "BS", 0.696, 0.705, 0.0, *_PLATE_SITE)],
    position_names=position_names,
  )


def biomek_cold_plate(name: str, position_names: Names = None) -> BiomekALP:
  """Q.Instruments ColdPlate ALP. Devices\\Q Instruments\\ColdPlate.alp"""
  return _alp(
    name=name,
    model="ColdPlate",
    size=(14.21, 10.0, 16.057),
    pin_count=(5, 5),
    pin_offset=(2.934, 0.834),
    sites=[Site("A1", "CP", 0.696, 0.705, 0.0, *_PLATE_SITE)],
    position_names=position_names,
  )


def biomek_static_peltier_96(name: str, position_names: Names = None) -> BiomekALP:
  """Static Peltier ALP with a 96-well block. Peltiers\\StaticPeltier_96.alp"""
  return _alp(
    name=name,
    model="StaticPeltier_96",
    size=(15.24, 11.113, 16.5275),
    pin_count=(7, 5),
    pin_offset=(1.384, 1.391),
    sites=[Site("A1", "Pelt", 1.173, 1.224, 0.0, 12.894, 8.662)],
    position_names=position_names,
  )


def biomek_tilting_alp(name: str, position_names: Names = None) -> BiomekALP:
  """Tilting ALP (site-defined). Custom\\Tilting-ALP.alp"""
  return _alp(
    name=name,
    model="Tilting-ALP",
    size=(15.2, 10.1, 15.87),
    pin_count=(7, 5),
    pin_offset=(1.35, 1.09),
    sites=[Site("A1", "TA", 1.19, 0.755, 0.0, *_PLATE_SITE)],
    position_names=position_names,
  )


def biomek_elx405dw(name: str, position_names: Names = None) -> BiomekALP:
  """BioTek ELx405 deep-well washer, at the back of the deck. Devices\\ELx405DW.alp

  Drawn as its envelope: the definition gives the space it takes and its plate position, nothing
  of its shape.
  """
  return _alp(
    name=name,
    model="ELx405DW",
    size=(41.1, 42.6, 10.6),
    pin_count=(11, 2),
    pin_offset=(10.713, 38.375),
    sites=[Site("A1", "W", 23.77, 28.76, 0.0, *_PLATE_SITE)],
    position_names=position_names,
  )


# Each ALP above by the type Biomek names it by (an instrument file's `Decks/<deck>/Groups/<ALP>`
# `Type`), for building a deck from a declared layout (`BiomekI7Deck.from_configuration`).
ALP_TYPES = {
  "Static1x1": biomek_static_1x1,
  "Static1x3": biomek_static_1x3,
  "Static1x5": biomek_static_1x5,
  "TipLoad1x1": biomek_tip_load_1x1,
  "TrashLeftBin": biomek_trash_left_bin,
  "TrashRightBin": biomek_trash_right_bin,
  "WashStationSpan8": biomek_wash_station_span8,
  "OrbitalShaker": biomek_orbital_shaker,
  "BioShake": biomek_bioshake,
  "ColdPlate": biomek_cold_plate,
  "StaticPeltier_96": biomek_static_peltier_96,
  "Tilting-ALP": biomek_tilting_alp,
  "ELx405DW": biomek_elx405dw,
}
