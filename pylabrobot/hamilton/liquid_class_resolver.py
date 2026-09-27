"""Resolve Hamilton liquid classes and corrected volumes for pipetting channels.

Automatic lookup defaults to
:func:`~pylabrobot.hamilton.star.liquid_classes.get_star_liquid_class`
(STAR calibration tables); pass ``lookup=`` for instrument-specific tables.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pylabrobot.hamilton.liquid_classes import HamiltonLiquidClass
from pylabrobot.hamilton.star.liquid_classes.mapping import get_star_liquid_class
from pylabrobot.resources.container import Container
from pylabrobot.resources.liquid import Liquid
from pylabrobot.resources.tip import Tip

# Finds a class, with `get_star_liquid_class`'s keywords.
LiquidClassLookup = Callable[..., Optional[HamiltonLiquidClass]]

# What an aspirate argument takes from a class when it is not given.
ASPIRATE_CLASS_ATTRIBUTES: Dict[str, Callable[[HamiltonLiquidClass], float]] = {
  "flow_rates": lambda hlc: hlc.aspiration_flow_rate,
  "clot_detection_heights": lambda hlc: hlc.aspiration_clot_retract_height,
  "blow_out_air_volumes": lambda hlc: hlc.aspiration_blow_out_volume,
  "pre_wetting_volumes": lambda hlc: hlc.aspiration_over_aspirate_volume,
  "settling_times": lambda hlc: hlc.aspiration_settling_time,
  "swap_speeds": lambda hlc: hlc.aspiration_swap_speed,
  "transport_air_volumes": lambda hlc: hlc.aspiration_air_transport_volume,
}

# What a dispense argument takes from a class when it is not given.
DISPENSE_CLASS_ATTRIBUTES: Dict[str, Callable[[HamiltonLiquidClass], float]] = {
  "flow_rates": lambda hlc: hlc.dispense_flow_rate,
  "transport_air_volumes": lambda hlc: hlc.dispense_air_transport_volume,
  "stop_back_volumes": lambda hlc: hlc.dispense_stop_back_volume,
  "blow_out_air_volumes": lambda hlc: hlc.dispense_blow_out_volume,
  "settling_times": lambda hlc: hlc.dispense_settling_time,
  "swap_speeds": lambda hlc: hlc.dispense_swap_speed,
}


def per_container(name: str, given: Optional[Sequence[Any]], n: int) -> Optional[List[Any]]:
  """`given` as a list of one entry per container; None stays None.

  Raises:
    ValueError: Not one entry per container.
  """
  if given is None:
    return None
  if len(given) != n:
    raise ValueError(f"{name} must have one entry per container, {n}, has {len(given)}")
  return list(given)


def check_volume_arguments(
  volumes: Optional[Sequence[float]],
  piston_volumes: Optional[Sequence[float]],
  hamilton_liquid_classes: Optional[Sequence[Optional[HamiltonLiquidClass]]],
  how_moved: str,
) -> None:
  """Raise unless exactly one of `volumes` and `piston_volumes` is given, without a class beside
  `piston_volumes`.

  Args:
    volumes: liquid per container, corrected by a class; or None.
    piston_volumes: piston travel per container, as given; or None.
    hamilton_liquid_classes: the classes given, if any.
    how_moved: "drawn" or "pushed out", for the refusals.
  """
  if (volumes is None) == (piston_volumes is None):
    raise ValueError(
      f"give volumes, which a liquid class corrects, or piston_volumes, {how_moved} as given; "
      "not both and not neither"
    )
  if piston_volumes is not None and hamilton_liquid_classes is not None:
    raise ValueError(f"piston_volumes are {how_moved} as given; a liquid class would correct them")


def get_volumes_and_classes(
  containers: Sequence[Container],
  channel_of: Sequence[int],
  tips: Sequence[Tip],
  volumes: Optional[Sequence[float]],
  piston_volumes: Optional[Sequence[float]],
  hamilton_liquid_classes: Optional[Sequence[HamiltonLiquidClass]],
  jets: Sequence[bool],
  blow_outs: Sequence[bool],
  lookup: LiquidClassLookup = get_star_liquid_class,
) -> Tuple[List[float], List[float], Optional[List[HamiltonLiquidClass]]]:
  """The liquid asked per container, the piston volume that moves it, and the classes used.

  Args:
    containers: per job.
    channel_of: the channel of each container, per job.
    tips: per job, the tip on its channel.
    volumes: liquid per container, corrected by a class; or None.
    piston_volumes: piston travel per container, as given, the liquid counting the same; or None.
    hamilton_liquid_classes: one per container; looked up for the tip, water, `jets` and
      `blow_outs` when None.
    jets: per job, for the lookup.
    blow_outs: per job, for the lookup.
    lookup: finds a class, with `get_star_liquid_class`'s keywords.

  Returns:
    The liquid per job, the piston volume per job, rounded to 0.01 uL, and the classes, None
    with `piston_volumes`.

  Raises:
    ValueError: Lists not one per container, or no class known for a channel's tip.
  """
  n = len(containers)
  if volumes is None:
    assert piston_volumes is not None
    piston = per_container("piston_volumes", piston_volumes, n) or []
    return list(piston), piston, None
  liquid = per_container("volumes", volumes, n)
  assert liquid is not None
  classes = per_container("hamilton_liquid_classes", hamilton_liquid_classes, n)
  if classes is None:
    classes = []
    for job, tip in enumerate(tips):
      keys = dict(
        tip_volume=tip.maximal_volume,
        is_core=False,
        is_tip=True,
        has_filter=tip.has_filter,
        liquid=Liquid.WATER,
        jet=jets[job],
      )
      found = lookup(**keys, blow_out=blow_outs[job])
      if found is None:
        other = not blow_outs[job] and lookup(**keys, blow_out=True) is not None
        raise ValueError(
          f"no liquid class is known for channel {channel_of[job]}'s tip on "
          f"{containers[job].name}: {tip.maximal_volume} uL, "
          f"{'with' if tip.has_filter else 'without'} filter, water, jet={jets[job]}, "
          f"blow_out={blow_outs[job]}. Give hamilton_liquid_classes, "
          f"{'blow_out=True, which has one, ' if other else ''}or piston_volumes"
        )
      classes.append(found)
  piston = [round(hlc.compute_corrected_volume(v), 2) for hlc, v in zip(classes, liquid)]
  return liquid, piston, classes


def from_class(
  name: str,
  given: Optional[Sequence[Any]],
  n: int,
  classes: Optional[Sequence[HamiltonLiquidClass]],
  attributes: Dict[str, Callable[[HamiltonLiquidClass], float]],
) -> Optional[List[Any]]:
  """What is given, else what the liquid classes say, else None: the driver's own default. A None
  entry of `given` takes its class's value.

  Args:
    name: the argument, a key of `attributes`.
    given: one entry per container, or None.
    n: how many containers.
    classes: one per container, or None with piston volumes.
    attributes: `ASPIRATE_CLASS_ATTRIBUTES` or `DISPENSE_CLASS_ATTRIBUTES`.
  """
  values = per_container(name, given, n)
  if classes is None:
    return values
  if values is None:
    return [attributes[name](hlc) for hlc in classes]
  return [attributes[name](hlc) if v is None else v for v, hlc in zip(values, classes)]
