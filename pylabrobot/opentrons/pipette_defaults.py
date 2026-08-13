"""The robot's own default flow rates, per pipette and per tip.

Every Opentrons liquid-handling command carries a required ``flowRate``, and no
robot-server endpoint serves the defaults -- ``GET /instruments`` reports
channels and volume range only. They are read here from the same shared-data
package robot-server itself loads, so a caller who does not name a rate gets
what the robot would have used. The values differ by more than two orders of
magnitude across the Flex range (6 uL/s to 716), so a single constant is wrong
for almost every pipette.
"""

from typing import Dict, NamedTuple, Tuple

from pylabrobot.opentrons.shared_data import require_shared_data


class FlowRates(NamedTuple):
  """Default aspirate, dispense and blow-out rates in uL/s."""

  aspirate: float
  dispense: float
  blow_out: float


_CACHE: Dict[str, Dict[str, FlowRates]] = {}


def _rates_by_tip(pipette_model: str) -> Dict[str, FlowRates]:
  cached = _CACHE.get(pipette_model)
  if cached is not None:
    return cached

  if not pipette_model:
    # An empty model resolves to a p1000 single-channel rather than raising, which
    # would silently pipette a p50 at up to 716 uL/s.
    raise ValueError("No pipette model given, so its default flow rates are unknown.")

  require_shared_data()
  from opentrons_shared_data.pipette.load_data import load_liquid_model
  from opentrons_shared_data.pipette.pipette_load_name_conversions import convert_pipette_model
  from opentrons_shared_data.pipette.types import PipetteModel, PipetteOEMType

  version = convert_pipette_model(PipetteModel(pipette_model))
  liquid_model = load_liquid_model(
    version.pipette_type, version.pipette_channels, version.pipette_version, PipetteOEMType.OT
  )
  rates = {
    tip_type.name: FlowRates(
      aspirate=tip.default_aspirate_flowrate.default,
      dispense=tip.default_dispense_flowrate.default,
      blow_out=tip.default_blowout_flowrate.default,
    )
    for tip_type, tip in liquid_model["default"].supported_tips.items()
  }
  _CACHE[pipette_model] = rates
  return rates


def flow_rates(pipette_model: str, tip_volume: float) -> FlowRates:
  """Look up the defaults for a pipette model ("p50_multi_v3.5") and tip volume.

  Raises:
    ValueError: If the pipette does not support a tip of that volume. There is
      no near-enough tip to fall back to: the p1000 eight-channel alone spans
      478 uL/s on a 50 uL tip and 716 on a 200, so guessing would silently
      pipette at the wrong speed.
  """
  rates = _rates_by_tip(pipette_model)
  tip_name = f"t{int(tip_volume)}"
  if tip_name not in rates:
    supported = ", ".join(sorted(rates))
    raise ValueError(
      f"'{pipette_model}' publishes no default flow rate for a {tip_volume} uL tip "
      f"(it supports {supported}). Pass an explicit flow_rate for this tip."
    )
  return rates[tip_name]


def supported_tip_volumes(pipette_model: str) -> Tuple[float, ...]:
  """The tip volumes this pipette publishes defaults for, ascending."""
  return tuple(sorted(float(name[1:]) for name in _rates_by_tip(pipette_model)))
