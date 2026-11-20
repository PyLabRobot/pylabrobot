"""Beckman Coulter Biomek tips, from Biomek's tip classes (lengths in cm there, mm here).

- `tipheight`: the tip's length; `capacity`: its volume in uL; `filtered`: a filter tip.
- `spanseatingdepth`: how far a mandrel goes into the tip.
- `maxtipradius`: the tip's radius where it seats on the mandrel.
- `mintipradius`, `coniclength`: its radius at the end, and the length over which it widens from
  there to `maxtipradius` (the cone).
- `lls`: whether the tip is conductive, for liquid-level sensing.
"""

from .biomek_resource import BiomekTip


def beckman_coulter_biomek_tip_50uL_filter(name: str) -> BiomekTip:
  """Biomek T50F tip, 50 uL, filter: tip class T50F."""
  return BiomekTip(
    name=name,
    model=beckman_coulter_biomek_tip_50uL_filter.__name__,
    diameter=5.182,  # maxtipradius 0.2591 cm
    size_z=51.359,  # tipheight 5.1359 cm
    has_filter=True,
    maximal_volume=50.0,  # capacity
    nominal_volume=50.0,
    fitting_depth=7.75,  # spanseatingdepth 0.775 cm
    end_diameter=0.812,  # mintipradius 0.0406 cm
    cone_length=38.227,  # coniclength 3.8227 cm
    is_conductive=False,  # lls
  )


def beckman_coulter_biomek_tip_190uL_filter(name: str) -> BiomekTip:
  """Biomek T190F tip, 190 uL, filter: tip class T190F."""
  return BiomekTip(
    name=name,
    model=beckman_coulter_biomek_tip_190uL_filter.__name__,
    diameter=4.826,  # maxtipradius 0.2413 cm
    size_z=51.435,  # tipheight 5.1435 cm
    has_filter=True,
    maximal_volume=190.0,  # capacity
    nominal_volume=190.0,
    fitting_depth=7.75,  # spanseatingdepth 0.775 cm
    end_diameter=1.27,  # mintipradius 0.0635 cm
    cone_length=28.194,  # coniclength 2.8194 cm
    is_conductive=False,  # lls
  )


def beckman_coulter_biomek_tip_230uL(name: str) -> BiomekTip:
  """Biomek T230 tip, 230 uL, no filter: tip class T230."""
  return BiomekTip(
    name=name,
    model=beckman_coulter_biomek_tip_230uL.__name__,
    diameter=4.826,  # maxtipradius 0.2413 cm
    size_z=51.435,  # tipheight 5.1435 cm
    has_filter=False,
    maximal_volume=230.0,  # capacity
    nominal_volume=230.0,
    fitting_depth=7.75,  # spanseatingdepth 0.775 cm
    end_diameter=1.27,  # mintipradius 0.0635 cm
    cone_length=28.194,  # coniclength 2.8194 cm
    is_conductive=False,  # lls
  )


def beckman_coulter_biomek_tip_1025uL_filter(name: str) -> BiomekTip:
  """Biomek T1025F tip, 1025 uL, filter: tip class T1025F."""
  return BiomekTip(
    name=name,
    model=beckman_coulter_biomek_tip_1025uL_filter.__name__,
    diameter=4.826,  # maxtipradius 0.2413 cm
    size_z=109.855,  # tipheight 10.9855 cm
    has_filter=True,
    maximal_volume=1025.0,  # capacity
    nominal_volume=1025.0,
    fitting_depth=7.75,  # spanseatingdepth 0.775 cm
    end_diameter=1.27,  # mintipradius 0.0635 cm
    cone_length=19.8374,  # coniclength 1.98374 cm
    is_conductive=False,  # lls
  )


def beckman_coulter_biomek_tip_1070uL(name: str) -> BiomekTip:
  """Biomek T1070 tip, 1070 uL, no filter: tip class T1070."""
  return BiomekTip(
    name=name,
    model=beckman_coulter_biomek_tip_1070uL.__name__,
    diameter=4.826,  # maxtipradius 0.2413 cm
    size_z=109.855,  # tipheight 10.9855 cm
    has_filter=False,
    maximal_volume=1070.0,  # capacity
    nominal_volume=1070.0,
    fitting_depth=7.75,  # spanseatingdepth 0.775 cm
    end_diameter=1.27,  # mintipradius 0.0635 cm
    cone_length=19.8374,  # coniclength 1.98374 cm
    is_conductive=False,  # lls
  )
