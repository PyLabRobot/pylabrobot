# pylint: skip-file

from typing import Dict, Tuple, Optional

from pylabrobot.resources.liquid import Liquid
from pylabrobot.liquid_handling.liquid_classes.hamilton.base import HamiltonLiquidClass


star_mapping: Dict[Tuple[int, bool, bool, bool, Liquid, bool, bool], HamiltonLiquidClass] = {}

def get_star_liquid_class(
  tip_volume: float,
  is_core: bool,
  is_tip: bool,
  has_filter: bool,
  liquid: Liquid,
  jet: bool,
  blow_out: bool
) -> Optional[HamiltonLiquidClass]:
  """ Get the Hamilton STAR liquid class for the given parameters.

  Args:
    tip_volume: The volume of the tip in microliters.
    is_core: Whether the tip is a core tip.
    is_tip: Whether the tip is a tip tip or a needle.
    has_filter: Whether the tip has a filter.
    liquid: The liquid to be dispensed.
    jet: Whether the liquid is dispensed using a jet.
    blow_out: This is called "empty" in the Hamilton Liquid editor and liquid class names, but
      "blow out" in the firmware documentation. "Empty" in the firmware documentation means fully
      emptying the tip, which is the terminology PyLabRobot adopts. Blow_out is the opposite of
      partial dispense.
  """

  # Tip volumes from resources (mostly where they have filters) are slightly different from the ones
  # in the liquid class mapping, so we need to map them here. If no mapping is found, we use the
  # given maximal volume of the tip.
  tip_volume = int({
    360.0: 300.0,
    1065.0: 1000.0,
    1250.0: 1000.0,
    4367.0: 4000.0,
    5420.0: 5000.0,
  }.get(tip_volume, tip_volume))

  return star_mapping.get((tip_volume, is_core, is_tip, has_filter, liquid, jet, blow_out), None)


star_mapping[(1000, False, False, False, Liquid.WATER, True, True)] = \
_1000ulNeedleCRWater_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 520.0, 50.0: 61.2, 0.0: 0.0, 20.0: 22.5, 100.0: 113.0, 10.0: 11.1, 200.0: 214.0, 1000.0: 1032.0},
  aspiration_flow_rate=500.0,
  aspiration_mix_flow_rate=500.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=500.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, False, False, Liquid.WATER, True, False)] = \
_1000ulNeedleCRWater_DispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 520.0, 50.0: 62.2, 0.0: 0.0, 20.0: 32.0, 100.0: 115.5, 1000.0: 1032.0},
  aspiration_flow_rate=500.0,
  aspiration_mix_flow_rate=500.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=500.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=10.0
)


#
star_mapping[(1000, False, False, False, Liquid.WATER, False, True)] = \
_1000ulNeedleCRWater_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={50.0: 59.0, 0.0: 0.0, 20.0: 25.9, 10.0: 12.9, 1000.0: 1000.0},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=50.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=50.0,
  dispense_air_transport_volume=1.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


#
star_mapping[(1000, False, False, False, Liquid.WATER, False, False)] = \
_1000ulNeedleCRWater_DispenseSurface_Part = HamiltonLiquidClass(
  curve={50.0: 55.0, 0.0: 0.0, 20.0: 25.9, 10.0: 12.9, 1000.0: 1000.0},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=50.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=1.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth Asp. 0.5mm, without pre-rinsing
# - Disp.: jet mode empty tip
# - Pipetting-Volumes jet-dispense  between 20 - 1000µl
#
#
#
# Typical performance data under laboratory conditions:
# Volume µl            Precision %        Trueness %
#       20                       7.15                 - 5.36
#       50                       2.81                 - 1.49
#     100                       2.48                 - 1.94
#     200                       1.25                 - 0.51
#     500                       0.91                   0.02
#   1000                       0.66                 - 0.46
star_mapping[(1000, False, False, False, Liquid.WATER, True, False)] = \
_1000ulNeedle_Water_DispenseJet = HamiltonLiquidClass(
  curve={500.0: 530.0, 50.0: 56.0, 0.0: 0.0, 100.0: 110.0, 20.0: 22.5, 1000.0: 1055.0, 200.0: 214.0},
  aspiration_flow_rate=500.0,
  aspiration_mix_flow_rate=500.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=10.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=500.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=500.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=0.4,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
#                              Disp. 0.5mm
# - without pre-rinsing
# - dispense mode: surface empty tip
# - Pipetting-Volumes surface-dispense  between 20 - 50µl
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                     10.12                 - 4.66
#       50                       3.79                 - 1.18
#
star_mapping[(1000, False, False, False, Liquid.WATER, False, False)] = \
_1000ulNeedle_Water_DispenseSurface = HamiltonLiquidClass(
  curve={50.0: 59.0, 0.0: 0.0, 20.0: 25.9, 1000.0: 1000.0},
  aspiration_flow_rate=500.0,
  aspiration_mix_flow_rate=500.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=10.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=500.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=500.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=0.4,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, False, False, Liquid.WATER, False, True)] = \
_10ulNeedleCRWater_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.7, 0.5: 0.5, 0.0: 0.0, 1.0: 1.2, 2.0: 2.4, 10.0: 11.4},
  aspiration_flow_rate=60.0,
  aspiration_mix_flow_rate=60.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=60.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=60.0,
  dispense_air_transport_volume=1.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, False, False, Liquid.WATER, False, False)] = \
_10ulNeedleCRWater_DispenseSurface_Part = HamiltonLiquidClass(
  curve={5.0: 5.7, 0.5: 0.5, 0.0: 0.0, 1.0: 1.2, 2.0: 2.4, 10.0: 11.4},
  aspiration_flow_rate=60.0,
  aspiration_mix_flow_rate=60.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=60.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=1.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, True, Liquid.DMSO, True, False)] = \
_150ul_Piercing_Tip_Filter_DMSO_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={150.0: 150.0, 0.0: 0.0, 20.0: 20.0, 10.0: 10.0},
  aspiration_flow_rate=180.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=1.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, True, Liquid.DMSO, True, True)] = \
_150ul_Piercing_Tip_Filter_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={150.0: 154.0, 50.0: 52.9, 0.0: 0.0, 20.0: 21.8},
  aspiration_flow_rate=180.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=1.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, True, Liquid.DMSO, False, True)] = \
_150ul_Piercing_Tip_Filter_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={3.0: 4.5, 5.0: 6.5, 150.0: 155.0, 50.0: 53.7, 0.0: 0.0, 10.0: 12.0, 2.0: 3.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=1.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


# - Volume 20 - 300ul
# - submerge depth: Asp.  0.5mm
#                              Disp. 0.5mm
# - without pre-rinsing, in case of drops pre-rinsing 3x  with Aspiratevolume,
#   ( >100ul perhaps 2x or set mix speed to 100ul/s)
# - dispense mode jet empty tip
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       6.68                  -2.95
#       50                       1.71                   1.93
#     100                       1.67                  -0.35
#     300                       0.46                  -0.61
#
star_mapping[(50, False, True, True, Liquid.ETHANOL, True, True)] = \
_150ul_Piercing_Tip_Filter_Ethanol_DispenseJet_Empty = HamiltonLiquidClass(
  curve={150.0: 166.0, 50.0: 58.3, 0.0: 0.0, 20.0: 25.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=7.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=7.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


# - Volume 5 - 50ul
# - submerge depth: Asp.  0.5mm
#                              Disp. 0.5mm
# - without pre-rinsing, in case of drops pre-rinsing 3x  with Aspiratevolume,
#   ( >100ul perhaps 2x or set mix speed to 100ul/s)
# - dispense mode surface empty tip
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         5                       7.96                  -0.03
#       10                       7.99                   5.88
#       20                       0.95                   2.97
#       50                       0.31                  -0.10
#
star_mapping[(50, False, True, True, Liquid.ETHANOL, False, True)] = \
_150ul_Piercing_Tip_Filter_Ethanol_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={3.0: 5.0, 5.0: 7.6, 150.0: 165.0, 50.0: 56.9, 0.0: 0.0, 10.0: 13.2, 2.0: 3.3},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=7.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=150.0,
  dispense_air_transport_volume=7.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
#                              Disp.  0.5mm
# - without pre-rinsing
# - dispense mode surface empty tip
# - Pipetting-Volumes jet-dispense  between 5 - 300µl
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         5                       3.28                   0.86
#       10                       4.88                  -0.29
#       20                       2.92                   2.68
#       50                       2.44                   1.18
#     100                       1.33                   1.29
#     300                       1.08                  -0.87
#
#
star_mapping[(50, False, True, True, Liquid.GLYCERIN80, False, True)] = \
_150ul_Piercing_Tip_Filter_Glycerin80_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={3.0: 4.5, 5.0: 7.2, 150.0: 167.5, 50.0: 60.0, 0.0: 0.0, 1.0: 2.7, 10.0: 13.0, 2.0: 2.5},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=5.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=10.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=50.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=5.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
# - without pre-rinsing
# - dispense mode jet empty tip
# - Pipetting-Volumes jet-dispense  between 20 - 300µl
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       2.78                  -0.05
#       50                       0.89                   1.06
#     100                       0.81                   0.99
#     300                       1.00                   0.65
#
star_mapping[(50, False, True, True, Liquid.SERUM, True, True)] = \
_150ul_Piercing_Tip_Filter_Serum_DispenseJet_Empty = HamiltonLiquidClass(
  curve={150.0: 162.0, 50.0: 55.9, 0.0: 0.0, 20.0: 23.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
#                              Disp.  0.5mm
# - without pre-rinsing
# - dispense mode surface empty tip
# - Pipetting-Volumes surface-dispense  between 1 - 50µl
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         1                     17.32                   3.68
#         2                     16.68                   0.24
#         5                       6.30                   1.37
#       10                       2.03                   5.71
#       20                       1.72                   3.91
#       50                       1.39                  -0.12
#
#
star_mapping[(50, False, True, True, Liquid.SERUM, False, True)] = \
_150ul_Piercing_Tip_Filter_Serum_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={3.0: 3.4, 5.0: 5.9, 150.0: 161.5, 50.0: 56.2, 0.0: 0.0, 10.0: 11.6, 2.0: 2.2},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=150.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, True, Liquid.WATER, True, False)] = \
_150ul_Piercing_Tip_Filter_Water_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={150.0: 150.0, 0.0: 0.0, 20.0: 20.0, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(50, False, True, True, Liquid.WATER, True, True)] = \
_150ul_Piercing_Tip_Filter_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={5.0: 6.6, 150.0: 159.1, 50.0: 55.0, 0.0: 0.0, 100.0: 107.0, 1.0: 1.6, 20.0: 22.9, 10.0: 12.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=1.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, True, Liquid.WATER, False, True)] = \
_150ul_Piercing_Tip_Filter_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={3.0: 3.5, 5.0: 6.5, 150.0: 158.1, 50.0: 54.5, 0.0: 0.0, 1.0: 1.6, 10.0: 11.9, 2.0: 2.8},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=1.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, False, Liquid.DMSO, True, True)] = \
_250ul_Piercing_Tip_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={250.0: 255.5, 50.0: 52.9, 0.0: 0.0, 20.0: 21.8},
  aspiration_flow_rate=180.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=1.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, False, Liquid.DMSO, False, True)] = \
_250ul_Piercing_Tip_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={3.0: 4.2, 5.0: 6.5, 250.0: 256.0, 50.0: 53.7, 0.0: 0.0, 10.0: 12.0, 2.0: 3.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=1.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


# - Volume 20 - 300ul
# - submerge depth: Asp.  0.5mm
#                              Disp. 0.5mm
# - without pre-rinsing, in case of drops pre-rinsing 3x  with Aspiratevolume,
#   ( >100ul perhaps 2x or set mix speed to 100ul/s)
# - dispense mode jet empty tip
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       6.68                  -2.95
#       50                       1.71                   1.93
#     100                       1.67                  -0.35
#     300                       0.46                  -0.61
#
star_mapping[(50, False, True, False, Liquid.ETHANOL, True, True)] = \
_250ul_Piercing_Tip_Ethanol_DispenseJet_Empty = HamiltonLiquidClass(
  curve={250.0: 270.2, 50.0: 59.2, 0.0: 0.0, 20.0: 27.3},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=15.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


# - Volume 5 - 50ul
# - submerge depth: Asp.  0.5mm
#                              Disp. 0.5mm
# - without pre-rinsing, in case of drops pre-rinsing 3x  with Aspiratevolume,
#   ( >100ul perhaps 2x or set mix speed to 100ul/s)
# - dispense mode surface empty tip
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         5                       7.96                  -0.03
#       10                       7.99                   5.88
#       20                       0.95                   2.97
#       50                       0.31                  -0.10
#
star_mapping[(50, False, True, False, Liquid.ETHANOL, False, True)] = \
_250ul_Piercing_Tip_Ethanol_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={3.0: 5.0, 5.0: 9.6, 250.0: 270.5, 50.0: 58.0, 0.0: 0.0, 10.0: 14.8},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=150.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
#                              Disp.  0.5mm
# - without pre-rinsing
# - dispense mode surface empty tip
# - Pipetting-Volumes jet-dispense  between 5 - 300µl
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         5                       3.28                   0.86
#       10                       4.88                  -0.29
#       20                       2.92                   2.68
#       50                       2.44                   1.18
#     100                       1.33                   1.29
#     300                       1.08                  -0.87
#
#
star_mapping[(50, False, True, False, Liquid.GLYCERIN80, False, True)] = \
_250ul_Piercing_Tip_Glycerin80_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={3.0: 4.5, 5.0: 7.2, 250.0: 289.0, 50.0: 65.0, 0.0: 0.0, 1.0: 2.7, 10.0: 13.9},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=5.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=10.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=50.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=5.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
# - without pre-rinsing
# - dispense mode jet empty tip
# - Pipetting-Volumes jet-dispense  between 20 - 300µl
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       2.78                  -0.05
#       50                       0.89                   1.06
#     100                       0.81                   0.99
#     300                       1.00                   0.65
#
star_mapping[(50, False, True, False, Liquid.SERUM, True, True)] = \
_250ul_Piercing_Tip_Serum_DispenseJet_Empty = HamiltonLiquidClass(
  curve={250.0: 265.0, 50.0: 56.4, 0.0: 0.0, 20.0: 23.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
#                              Disp.  0.5mm
# - without pre-rinsing
# - dispense mode surface empty tip
# - Pipetting-Volumes surface-dispense  between 1 - 50µl
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         1                     17.32                   3.68
#         2                     16.68                   0.24
#         5                       6.30                   1.37
#       10                       2.03                   5.71
#       20                       1.72                   3.91
#       50                       1.39                  -0.12
#
#
star_mapping[(50, False, True, False, Liquid.SERUM, False, True)] = \
_250ul_Piercing_Tip_Serum_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={3.0: 3.4, 5.0: 5.9, 250.0: 264.2, 50.0: 56.2, 0.0: 0.0, 10.0: 11.6},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=150.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, False, Liquid.WATER, True, True)] = \
_250ul_Piercing_Tip_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={5.0: 6.6, 250.0: 260.0, 50.0: 55.0, 0.0: 0.0, 100.0: 107.0, 1.0: 1.6, 20.0: 22.5, 10.0: 12.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=1.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, False, Liquid.WATER, False, True)] = \
_250ul_Piercing_Tip_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={3.0: 4.0, 5.0: 6.5, 250.0: 259.0, 50.0: 55.1, 0.0: 0.0, 1.0: 1.6, 10.0: 12.6, 2.0: 2.8},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=1.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


# - Volume 10 - 300ul
# - submerge depth: Asp.  0.5mm
# - without pre-rinsing, in case of drops pre-rinsing 1-3x  with Aspiratevolume,
#   ( >100ul perhaps less than 2x or set mix speed to 100ul/s)
# - dispense mode jet empty tip
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       10                       7.29                   0.79
#       20                       5.85                  -0.66
#       50                       2.57                   0.82
#     100                       1.04                   0.05
#     300                       0.63                  -0.07
#
star_mapping[(300, False, False, False, Liquid.ACETONITRIL80WATER20, True, False)] = \
_300ulNeedleAcetonitril80Water20DispenseJet = HamiltonLiquidClass(
  curve={300.0: 310.0, 50.0: 57.8, 0.0: 0.0, 100.0: 106.5, 20.0: 26.8, 10.0: 16.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=15.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=0.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, False, False, Liquid.WATER, True, True)] = \
_300ulNeedleCRWater_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 313.0, 50.0: 53.5, 0.0: 0.0, 100.0: 104.0, 20.0: 22.3},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, False, False, Liquid.WATER, True, False)] = \
_300ulNeedleCRWater_DispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 313.0, 50.0: 59.5, 0.0: 0.0, 100.0: 109.0, 20.0: 29.3},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, False, False, Liquid.WATER, False, True)] = \
_300ulNeedleCRWater_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 308.4, 5.0: 6.8, 50.0: 52.3, 0.0: 0.0, 100.0: 102.9, 20.0: 22.3, 1.0: 2.3, 200.0: 205.8, 10.0: 11.7, 2.0: 3.0},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=50.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=50.0,
  dispense_air_transport_volume=1.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, False, False, Liquid.WATER, False, False)] = \
_300ulNeedleCRWater_DispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 308.4, 5.0: 6.8, 50.0: 52.3, 0.0: 0.0, 100.0: 102.9, 20.0: 22.3, 1.0: 2.3, 200.0: 205.8, 10.0: 11.7, 2.0: 3.0},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=50.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=1.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
# - without pre-rinsing
# - dispense mode jet empty tip
# - Pipetting-Volumes jet-dispense  between 20 - 300µl
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       2.21                   0.57
#       50                       1.53                   0.23
#     100                       0.55                  -0.01
#     300                       0.71                   0.39
#
star_mapping[(300, False, False, False, Liquid.DIMETHYLSULFOXID, True, False)] = \
_300ulNeedleDMSODispenseJet = HamiltonLiquidClass(
  curve={300.0: 317.0, 50.0: 53.5, 0.0: 0.0, 100.0: 106.5, 20.0: 21.3},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
#                              Disp.  0.5mm
# - without pre-rinsing
# - dispense mode surface empty tip
# - Pipetting-Volumes surface-dispense  between 1 - 50µl
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         5                       5.97                   1.26
#       10                       2.53                   1.22
#       20                       3.67                   2.60
#       50                       1.32                  -1.05
#
#
star_mapping[(300, False, False, False, Liquid.DIMETHYLSULFOXID, False, False)] = \
_300ulNeedleDMSODispenseSurface = HamiltonLiquidClass(
  curve={5.0: 6.0, 50.0: 52.3, 0.0: 0.0, 20.0: 22.3, 10.0: 11.4, 2.0: 2.5},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=150.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# - Volume 20 - 300ul
# - submerge depth: Asp.  0.5mm
#                              Disp. 0.5mm
# - without pre-rinsing, in case of drops pre-rinsing 3x  with Aspiratevolume,
#   ( >100ul perhaps 2x or set mix speed to 100ul/s)
# - dispense mode jet empty tip
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       6.68                  -2.95
#       50                       1.71                   1.93
#     100                       1.67                  -0.35
#     300                       0.46                  -0.61
#
star_mapping[(300, False, False, False, Liquid.ETHANOL, True, False)] = \
_300ulNeedleEtOHDispenseJet = HamiltonLiquidClass(
  curve={300.0: 317.0, 50.0: 57.8, 0.0: 0.0, 100.0: 109.0, 20.0: 25.3},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=15.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


# - Volume 5 - 50ul
# - submerge depth: Asp.  0.5mm
#                              Disp. 0.5mm
# - without pre-rinsing, in case of drops pre-rinsing 3x  with Aspiratevolume,
#   ( >100ul perhaps 2x or set mix speed to 100ul/s)
# - dispense mode surface empty tip
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         5                       7.96                  -0.03
#       10                       7.99                   5.88
#       20                       0.95                   2.97
#       50                       0.31                  -0.10
#
star_mapping[(300, False, False, False, Liquid.ETHANOL, False, False)] = \
_300ulNeedleEtOHDispenseSurface = HamiltonLiquidClass(
  curve={5.0: 7.2, 50.0: 55.0, 0.0: 0.0, 20.0: 24.5, 10.0: 13.1},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=150.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
#                              Disp.  0.5mm
# - without pre-rinsing
# - dispense mode surface empty tip
# - Pipetting-Volumes jet-dispense  between 5 - 300µl
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         5                       3.28                   0.86
#       10                       4.88                  -0.29
#       20                       2.92                   2.68
#       50                       2.44                   1.18
#     100                       1.33                   1.29
#     300                       1.08                  -0.87
#
#
star_mapping[(300, False, False, False, Liquid.GLYCERIN80, False, False)] = \
_300ulNeedleGlycerin80DispenseSurface = HamiltonLiquidClass(
  curve={300.0: 325.0, 5.0: 8.0, 50.0: 61.3, 0.0: 0.0, 100.0: 117.0, 20.0: 26.0, 1.0: 2.7, 10.0: 13.9, 2.0: 4.2},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=50.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=50.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
# - without pre-rinsing
# - dispense mode jet empty tip
# - Pipetting-Volumes jet-dispense  between 20 - 300µl
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       2.78                  -0.05
#       50                       0.89                   1.06
#     100                       0.81                   0.99
#     300                       1.00                   0.65
#
star_mapping[(300, False, False, False, Liquid.SERUM, True, False)] = \
_300ulNeedleSerumDispenseJet = HamiltonLiquidClass(
  curve={300.0: 313.0, 50.0: 53.5, 0.0: 0.0, 100.0: 105.0, 20.0: 21.3},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
#                              Disp.  0.5mm
# - without pre-rinsing
# - dispense mode surface empty tip
# - Pipetting-Volumes surface-dispense  between 1 - 50µl
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         1                     17.32                   3.68
#         2                     16.68                   0.24
#         5                       6.30                   1.37
#       10                       2.03                   5.71
#       20                       1.72                   3.91
#       50                       1.39                  -0.12
#
#
star_mapping[(300, False, False, False, Liquid.SERUM, False, False)] = \
_300ulNeedleSerumDispenseSurface = HamiltonLiquidClass(
  curve={5.0: 6.0, 50.0: 52.3, 0.0: 0.0, 20.0: 22.3, 1.0: 2.2, 10.0: 11.9, 2.0: 3.2},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=150.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
# - without pre-rinsing
# - dispense mode jet empty tip
# - Pipetting-Volumes jet-dispense  between 20 - 300µl
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       2.78                  -0.05
#       50                       0.89                   1.06
#     100                       0.81                   0.99
#     300                       1.00                   0.65
#
star_mapping[(300, False, False, False, Liquid.SERUM, True, False)] = \
_300ulNeedle_Serum_DispenseJet = HamiltonLiquidClass(
  curve={300.0: 313.0, 50.0: 53.5, 0.0: 0.0, 100.0: 105.0, 20.0: 21.3},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
#                              Disp.  0.5mm
# - without pre-rinsing
# - dispense mode surface empty tip
# - Pipetting-Volumes surface-dispense  between 1 - 50µl
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         1                     17.32                   3.68
#         2                     16.68                   0.24
#         5                       6.30                   1.37
#       10                       2.03                   5.71
#       20                       1.72                   3.91
#       50                       1.39                  -0.12
#
#
star_mapping[(300, False, False, False, Liquid.SERUM, False, False)] = \
_300ulNeedle_Serum_DispenseSurface = HamiltonLiquidClass(
  curve={300.0: 350.0, 5.0: 6.0, 50.0: 52.3, 0.0: 0.0, 20.0: 22.3, 1.0: 2.2, 10.0: 11.9, 2.0: 3.2},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=150.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
# - without pre-rinsing
# - dispense mode jet empty tip
# - Pipetting-Volumes jet-dispense  between 20 - 300µl
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       0.50                   2.26
#       50                       0.30                   0.65
#     100                       0.22                   1.15
#     200                       0.16                   0.55
#     300                       0.17                   0.35
#
star_mapping[(300, False, False, False, Liquid.WATER, True, False)] = \
_300ulNeedle_Water_DispenseJet = HamiltonLiquidClass(
  curve={300.0: 313.0, 50.0: 53.5, 0.0: 0.0, 100.0: 105.0, 20.0: 22.3},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  0.5mm
#                              Disp.  0.5mm
# - without pre-rinsing
# - dispense mode surface empty tip
# - Pipetting-Volumes jet-dispense  between 1 - 20µl
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         1                     11.17                 - 6.64
#         2                       4.50                   1.95
#         5                       0.38                   0.50
#       10                       0.94                   0.73
#       20                       0.63                   0.73
#
#
star_mapping[(300, False, False, False, Liquid.WATER, False, False)] = \
_300ulNeedle_Water_DispenseSurface = HamiltonLiquidClass(
  curve={300.0: 308.4, 5.0: 6.5, 50.0: 52.3, 0.0: 0.0, 100.0: 102.9, 20.0: 22.3, 1.0: 1.1, 200.0: 205.8, 10.0: 12.0, 2.0: 2.1},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=3.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=150.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=0.4,
  dispense_stop_back_volume=0.0
)


# Liquid class for washing rocket tips with CO-RE 384 head in 96 DC wash station.
star_mapping[(300, True, True, False, Liquid.WATER, False, False)] = \
_300ul_RocketTip_384COREHead_96Washer_DispenseSurface = HamiltonLiquidClass(
  curve={300.0: 330.0, 5.0: 6.3, 0.5: 0.9, 50.0: 55.1, 0.0: 0.0, 1.0: 1.6, 20.0: 23.2, 100.0: 107.2, 2.0: 2.8, 10.0: 11.9, 200.0: 211.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=150.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=150.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=5.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# Evaluation
star_mapping[(300, True, True, False, Liquid.DMSO, True, False)] = \
_300ul_RocketTip_384COREHead_DMSO_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 150.0: 150.0, 50.0: 50.0, 0.0: 0.0, 100.0: 100.0, 20.0: 20.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=7.5,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=120.0,
  dispense_stop_back_volume=10.0
)


# Evaluation
star_mapping[(300, True, True, False, Liquid.DMSO, True, True)] = \
_300ul_RocketTip_384COREHead_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 303.5, 0.0: 0.0, 100.0: 105.8, 200.0: 209.5, 10.0: 11.4},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


# Evaluation
star_mapping[(300, True, True, False, Liquid.DMSO, False, True)] = \
_300ul_RocketTip_384COREHead_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 308.0, 0.0: 0.0, 100.0: 105.5, 200.0: 209.0, 10.0: 12.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=80.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=80.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# Evaluation
star_mapping[(300, True, True, False, Liquid.WATER, True, False)] = \
_300ul_RocketTip_384COREHead_Water_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 309.0, 0.0: 0.0, 100.0: 106.5, 20.0: 22.3, 200.0: 207.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=7.5,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=20.0
)


# Evaluation
star_mapping[(300, True, True, False, Liquid.WATER, True, True)] = \
_300ul_RocketTip_384COREHead_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 309.0, 0.0: 0.0, 100.0: 106.5, 20.0: 22.3, 200.0: 207.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


# Evaluation
star_mapping[(300, True, True, False, Liquid.WATER, False, True)] = \
_300ul_RocketTip_384COREHead_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 314.3, 0.0: 0.0, 100.0: 109.0, 200.0: 214.7, 10.0: 12.7},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=160.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(30, True, True, False, Liquid.DMSO, True, True)] = \
_30ulTip_384COREHead_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={5.0: 5.0, 15.0: 15.3, 30.0: 30.7, 0.0: 0.0, 1.0: 1.0},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=3.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(30, True, True, False, Liquid.DMSO, False, True)] = \
_30ulTip_384COREHead_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 4.9, 15.0: 15.1, 30.0: 30.0, 0.0: 0.0, 1.0: 0.9},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=50.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(30, True, True, False, Liquid.ETHANOL, True, True)] = \
_30ulTip_384COREHead_EtOH_DispenseJet_Empty = HamiltonLiquidClass(
  curve={5.0: 6.54, 15.0: 18.36, 30.0: 33.8, 0.0: 0.0, 1.0: 1.8},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=3.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=3.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(30, True, True, False, Liquid.ETHANOL, False, True)] = \
_30ulTip_384COREHead_EtOH_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 6.2, 15.0: 16.9, 30.0: 33.1, 0.0: 0.0, 1.0: 1.5},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=50.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(30, True, True, False, Liquid.GLYCERIN80, False, True)] = \
_30ulTip_384COREHead_Glyzerin80_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 6.3, 0.5: 0.9, 40.0: 44.0, 0.0: 0.0, 20.0: 22.2, 1.0: 1.6, 10.0: 11.9, 2.0: 2.8},
  aspiration_flow_rate=150.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=2.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=100.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=2.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(30, True, True, False, Liquid.WATER, True, True)] = \
_30ulTip_384COREHead_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={5.0: 6.0, 15.0: 16.5, 30.0: 32.3, 0.0: 0.0, 1.0: 1.6},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=3.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(30, True, True, False, Liquid.WATER, False, True)] = \
_30ulTip_384COREHead_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.6, 15.0: 15.9, 30.0: 31.3, 0.0: 0.0, 1.0: 1.2},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=50.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(30, True, True, False, Liquid.WATER, False, False)] = \
_30ulTip_384COREWasher_DispenseSurface = HamiltonLiquidClass(
  curve={5.0: 6.3, 0.5: 0.9, 40.0: 44.0, 0.0: 0.0, 1.0: 1.6, 20.0: 22.2, 2.0: 2.8, 10.0: 11.9},
  aspiration_flow_rate=10.0,
  aspiration_mix_flow_rate=30.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=15.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=12.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=30.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=15.0,
  dispense_swap_speed=100.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(4000, False, True, False, Liquid.DMSO, True, False)] = \
_4mlTF_DMSO_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={3500.0: 3715.0, 500.0: 631.0, 2500.0: 2691.0, 1500.0: 1667.0, 4000.0: 4224.0, 3000.0: 3202.0, 0.0: 0.0, 2000.0: 2179.0, 100.0: 211.0, 1000.0: 1151.0},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=500.0,
  aspiration_air_transport_volume=20.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=1000.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=20.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=400.0,
  dispense_stop_back_volume=20.0
)


star_mapping[(4000, False, True, False, Liquid.DMSO, True, True)] = \
_4mlTF_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 540.0, 50.0: 61.5, 4000.0: 4102.0, 3000.0: 3083.0, 0.0: 0.0, 2000.0: 2070.0, 100.0: 116.5, 1000.0: 1060.0},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=500.0,
  aspiration_air_transport_volume=20.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=1000.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=20.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=400.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(4000, False, True, False, Liquid.DMSO, False, True)] = \
_4mlTF_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 536.5, 50.0: 62.3, 4000.0: 4128.0, 3000.0: 3109.0, 0.0: 0.0, 2000.0: 2069.0, 100.0: 116.6, 1000.0: 1054.0, 10.0: 15.5},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=500.0,
  aspiration_air_transport_volume=20.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=500.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=500.0,
  dispense_air_transport_volume=20.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=5.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=500.0,
  dispense_stop_back_volume=0.0
)


# First two times mixing with max volume.
star_mapping[(4000, False, True, False, Liquid.ETHANOL, True, False)] = \
_4mlTF_EtOH_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 3500.0: 3500.0, 500.0: 500.0, 2500.0: 2500.0, 1500.0: 1500.0, 4000.0: 4000.0, 3000.0: 3000.0, 0.0: 0.0, 2000.0: 2000.0, 100.0: 100.0, 1000.0: 1000.0},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=2000.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=1000.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(4000, False, True, False, Liquid.ETHANOL, True, True)] = \
_4mlTF_EtOH_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 563.0, 50.0: 72.0, 4000.0: 4215.0, 3000.0: 3190.0, 0.0: 0.0, 2000.0: 2178.0, 100.0: 127.5, 1000.0: 1095.0},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=30.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=30.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=1000.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=30.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=30.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(4000, False, True, False, Liquid.ETHANOL, False, True)] = \
_4mlTF_EtOH_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 555.0, 50.0: 68.0, 4000.0: 4177.0, 3000.0: 3174.0, 0.0: 0.0, 2000.0: 2151.0, 100.0: 123.5, 1000.0: 1085.0, 10.0: 18.6},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=30.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=30.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=1000.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=30.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=30.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=30.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(4000, False, True, False, Liquid.GLYCERIN80, True, True)] = \
_4mlTF_Glycerin80_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 599.0, 50.0: 89.0, 4000.0: 4223.0, 3000.0: 3211.0, 0.0: 0.0, 2000.0: 2195.0, 100.0: 140.0, 1000.0: 1159.0},
  aspiration_flow_rate=1200.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=30.0,
  aspiration_blow_out_volume=100.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=500.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=50.0,
  dispense_blow_out_volume=100.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(4000, False, True, False, Liquid.GLYCERIN80, False, True)] = \
_4mlTF_Glycerin80_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 555.0, 50.0: 71.0, 4000.0: 4135.0, 3000.0: 3122.0, 0.0: 0.0, 2000.0: 2101.0, 100.0: 129.0, 1000.0: 1083.0, 10.0: 16.0},
  aspiration_flow_rate=1000.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=70.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=50.0,
  dispense_blow_out_volume=70.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(4000, False, True, False, Liquid.WATER, True, False)] = \
_4mlTF_Water_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={4000.0: 4160.0, 3000.0: 3160.0, 0.0: 0.0, 2000.0: 2160.0, 100.0: 214.0, 1000.0: 1148.0},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=500.0,
  aspiration_air_transport_volume=20.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=1000.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=20.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=400.0,
  dispense_stop_back_volume=20.0
)


star_mapping[(4000, False, True, False, Liquid.WATER, True, True)] = \
_4mlTF_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 551.8, 50.0: 66.4, 4000.0: 4165.0, 3000.0: 3148.0, 0.0: 0.0, 2000.0: 2128.0, 100.0: 122.7, 1000.0: 1082.0},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=500.0,
  aspiration_air_transport_volume=20.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=1000.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=20.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=400.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(4000, False, True, False, Liquid.WATER, False, True)] = \
_4mlTF_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 547.0, 50.0: 65.5, 4000.0: 4145.0, 3000.0: 3135.0, 0.0: 0.0, 2000.0: 2125.0, 100.0: 120.9, 1000.0: 1075.0, 10.0: 14.5},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=500.0,
  aspiration_air_transport_volume=20.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=500.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=500.0,
  dispense_air_transport_volume=20.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=5.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=500.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.DMSO, True, True)] = \
_50ulTip_384COREHead_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 52.0, 0.0: 0.0, 20.0: 21.1, 10.0: 10.5},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=3.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.DMSO, False, True)] = \
_50ulTip_384COREHead_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.0, 50.0: 51.1, 30.0: 30.7, 0.0: 0.0, 1.0: 0.9, 10.0: 10.1},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=50.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.ETHANOL, True, True)] = \
_50ulTip_384COREHead_EtOH_DispenseJet_Empty = HamiltonLiquidClass(
  curve={5.0: 6.54, 15.0: 18.36, 50.0: 53.0, 30.0: 33.8, 0.0: 0.0, 1.0: 1.8},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=3.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=3.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.ETHANOL, False, True)] = \
_50ulTip_384COREHead_EtOH_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 6.2, 15.0: 16.9, 0.5: 1.0, 50.0: 54.0, 30.0: 33.1, 0.0: 0.0, 1.0: 1.5},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=2.0,
  aspiration_swap_speed=6.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=50.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=2.0,
  dispense_swap_speed=6.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.GLYCERIN80, False, True)] = \
_50ulTip_384COREHead_Glycerin80_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.6, 0.5: 0.65, 50.0: 55.0, 0.0: 0.0, 30.0: 31.5, 1.0: 1.2, 10.0: 10.9},
  aspiration_flow_rate=30.0,
  aspiration_mix_flow_rate=30.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=20.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=20.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.WATER, True, True)] = \
_50ulTip_384COREHead_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 53.6, 0.0: 0.0, 20.0: 22.4, 10.0: 11.9},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.WATER, False, True)] = \
_50ulTip_384COREHead_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.5, 50.0: 52.2, 30.0: 31.5, 0.0: 0.0, 1.0: 1.2, 10.0: 11.3},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=2.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=20.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=2.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.WATER, False, False)] = \
_50ulTip_384COREWasher_DispenseSurface = HamiltonLiquidClass(
  curve={5.0: 6.3, 0.5: 0.9, 50.0: 55.0, 40.0: 44.0, 0.0: 0.0, 20.0: 22.2, 1.0: 1.6, 10.0: 11.9, 2.0: 2.8},
  aspiration_flow_rate=20.0,
  aspiration_mix_flow_rate=30.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=15.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=25.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=30.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=15.0,
  dispense_swap_speed=100.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.DMSO, True, True)] = \
_50ulTip_conductive_384COREHead_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={5.0: 5.2, 50.0: 50.6, 30.0: 30.4, 0.0: 0.0, 1.0: 0.9, 20.0: 21.1, 10.0: 9.3},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=3.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.DMSO, True, True)] = \
_50ulTip_conductive_384COREHead_DMSO_DispenseJet_Empty_below5ul = HamiltonLiquidClass(
  curve={5.0: 5.2, 50.0: 50.6, 30.0: 30.4, 0.0: 0.0, 1.0: 0.9, 20.0: 21.1, 10.0: 9.3},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=5.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=240.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=5.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.DMSO, True, False)] = \
_50ulTip_conductive_384COREHead_DMSO_DispenseJet_Part = HamiltonLiquidClass(
  curve={50.0: 50.0, 0.0: 0.0, 10.0: 10.0},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=3.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=5.0
)


star_mapping[(50, True, True, False, Liquid.DMSO, False, True)] = \
_50ulTip_conductive_384COREHead_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={0.1: 0.05, 0.25: 0.1, 5.0: 4.95, 0.5: 0.22, 50.0: 50.0, 30.0: 30.6, 0.0: 0.0, 1.0: 0.74, 10.0: 9.95},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=50.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.ETHANOL, True, True)] = \
_50ulTip_conductive_384COREHead_EtOH_DispenseJet_Empty = HamiltonLiquidClass(
  curve={5.0: 6.85, 15.0: 18.36, 50.0: 54.3, 30.0: 33.6, 0.0: 0.0, 1.0: 1.5, 10.0: 12.1},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=3.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=3.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.ETHANOL, True, True)] = \
_50ulTip_conductive_384COREHead_EtOH_DispenseJet_Empty_below5ul = HamiltonLiquidClass(
  curve={5.0: 6.85, 15.0: 18.36, 50.0: 54.3, 30.0: 33.6, 0.0: 0.0, 1.0: 1.5, 10.0: 12.1},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=5.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=240.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=3.0,
  dispense_blow_out_volume=5.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.ETHANOL, True, False)] = \
_50ulTip_conductive_384COREHead_EtOH_DispenseJet_Part = HamiltonLiquidClass(
  curve={50.0: 50.0, 0.0: 0.0, 10.0: 10.0},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=3.0,
  aspiration_blow_out_volume=3.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=3.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=2.0
)


star_mapping[(50, True, True, False, Liquid.ETHANOL, False, True)] = \
_50ulTip_conductive_384COREHead_EtOH_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={0.25: 0.3, 5.0: 6.1, 0.5: 0.65, 15.0: 16.9, 50.0: 52.7, 30.0: 32.1, 0.0: 0.0, 1.0: 1.35, 10.0: 11.3},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=2.0,
  aspiration_swap_speed=6.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=50.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=2.0,
  dispense_swap_speed=6.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.GLYCERIN80, False, True)] = \
_50ulTip_conductive_384COREHead_Glycerin80_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={0.25: 0.05, 5.0: 5.5, 0.5: 0.3, 50.0: 51.9, 30.0: 31.8, 0.0: 0.0, 1.0: 1.0, 10.0: 10.9},
  aspiration_flow_rate=30.0,
  aspiration_mix_flow_rate=30.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=20.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=20.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.WATER, True, True)] = \
_50ulTip_conductive_384COREHead_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={5.0: 5.67, 0.5: 0.27, 50.0: 51.9, 30.0: 31.5, 0.0: 0.0, 1.0: 1.06, 20.0: 20.0, 10.0: 10.9},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=2.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=2.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.WATER, True, True)] = \
_50ulTip_conductive_384COREHead_Water_DispenseJet_Empty_below5ul = HamiltonLiquidClass(
  curve={5.0: 5.67, 0.5: 0.27, 50.0: 51.9, 30.0: 31.5, 0.0: 0.0, 1.0: 1.06, 20.0: 20.0, 10.0: 10.9},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=5.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=240.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=5.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.WATER, True, False)] = \
_50ulTip_conductive_384COREHead_Water_DispenseJet_Part = HamiltonLiquidClass(
  curve={50.0: 50.0, 0.0: 0.0, 10.0: 10.0},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=2.0
)


star_mapping[(50, True, True, False, Liquid.WATER, False, True)] = \
_50ulTip_conductive_384COREHead_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={0.1: 0.1, 0.25: 0.15, 5.0: 5.6, 0.5: 0.45, 50.0: 51.0, 30.0: 31.0, 0.0: 0.0, 1.0: 0.98, 10.0: 10.7},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=2.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=20.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=2.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.WATER, False, False)] = \
_50ulTip_conductive_384COREWasher_DispenseSurface = HamiltonLiquidClass(
  curve={5.0: 6.3, 0.5: 0.9, 50.0: 55.0, 40.0: 44.0, 0.0: 0.0, 1.0: 1.6, 20.0: 22.2, 65.0: 65.0, 10.0: 11.9, 2.0: 2.8},
  aspiration_flow_rate=20.0,
  aspiration_mix_flow_rate=30.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=15.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=25.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=30.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=15.0,
  dispense_swap_speed=100.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(5000, False, True, False, Liquid.DMSO, True, False)] = \
_5mlT_DMSO_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={4500.0: 4606.0, 3500.0: 3591.0, 500.0: 525.0, 2500.0: 2576.0, 1500.0: 1559.0, 5000.0: 5114.0, 4000.0: 4099.0, 3000.0: 3083.0, 0.0: 0.0, 2000.0: 2068.0, 100.0: 105.0, 1000.0: 1044.0},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=20.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=1000.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=20.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=400.0,
  dispense_stop_back_volume=20.0
)


star_mapping[(5000, False, True, False, Liquid.DMSO, True, True)] = \
_5mlT_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 540.0, 50.0: 62.0, 5000.0: 5095.0, 4000.0: 4075.0, 0.0: 0.0, 3000.0: 3065.0, 100.0: 117.0, 2000.0: 2060.0, 1000.0: 1060.0},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=500.0,
  aspiration_air_transport_volume=20.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=1000.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=20.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=400.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(5000, False, True, False, Liquid.DMSO, False, True)] = \
_5mlT_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 535.0, 50.0: 60.3, 5000.0: 5090.0, 4000.0: 4078.0, 0.0: 0.0, 3000.0: 3066.0, 100.0: 115.0, 2000.0: 2057.0, 10.0: 12.5, 1000.0: 1054.0},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=500.0,
  aspiration_air_transport_volume=20.0,
  aspiration_blow_out_volume=20.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=500.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=500.0,
  dispense_air_transport_volume=20.0,
  dispense_blow_out_volume=20.0,
  dispense_swap_speed=5.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=500.0,
  dispense_stop_back_volume=0.0
)


# First two times mixing with max volume.
star_mapping[(5000, False, True, False, Liquid.ETHANOL, True, False)] = \
_5mlT_EtOH_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 312.0, 4500.0: 4573.0, 3500.0: 3560.0, 500.0: 519.0, 2500.0: 2551.0, 1500.0: 1542.0, 5000.0: 5081.0, 4000.0: 4066.0, 3000.0: 3056.0, 0.0: 0.0, 2000.0: 2047.0, 100.0: 104.0, 1000.0: 1033.0},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=2000.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=1000.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(5000, False, True, False, Liquid.ETHANOL, True, True)] = \
_5mlT_EtOH_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 563.0, 50.0: 72.0, 5000.0: 5230.0, 4000.0: 4215.0, 0.0: 0.0, 3000.0: 3190.0, 100.0: 129.5, 2000.0: 2166.0, 1000.0: 1095.0},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=30.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=30.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=1000.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=30.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=30.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(5000, False, True, False, Liquid.ETHANOL, False, True)] = \
_5mlT_EtOH_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 555.0, 50.0: 68.0, 5000.0: 5204.0, 4000.0: 4200.0, 0.0: 0.0, 3000.0: 3180.0, 100.0: 123.5, 2000.0: 2160.0, 10.0: 22.0, 1000.0: 1085.0},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=30.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=30.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=1000.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=30.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=30.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=30.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(5000, False, True, False, Liquid.GLYCERIN80, True, True)] = \
_5mlT_Glycerin80_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 597.0, 50.0: 89.0, 5000.0: 5240.0, 4000.0: 4220.0, 0.0: 0.0, 3000.0: 3203.0, 100.0: 138.0, 2000.0: 2195.0, 1000.0: 1166.0},
  aspiration_flow_rate=1200.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=30.0,
  aspiration_blow_out_volume=100.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=500.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=50.0,
  dispense_blow_out_volume=100.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(5000, False, True, False, Liquid.GLYCERIN80, False, True)] = \
_5mlT_Glycerin80_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 555.0, 50.0: 71.0, 5000.0: 5135.0, 4000.0: 4115.0, 0.0: 0.0, 3000.0: 3127.0, 100.0: 127.0, 2000.0: 2115.0, 10.0: 15.5, 1000.0: 1075.0},
  aspiration_flow_rate=1000.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=70.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=50.0,
  dispense_blow_out_volume=70.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(5000, False, True, False, Liquid.WATER, True, False)] = \
_5mlT_Water_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={5000.0: 5030.0, 4000.0: 4040.0, 0.0: 0.0, 3000.0: 3050.0, 100.0: 104.0, 2000.0: 2050.0, 1000.0: 1040.0},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=500.0,
  aspiration_air_transport_volume=20.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=1000.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=20.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=400.0,
  dispense_stop_back_volume=20.0
)


star_mapping[(5000, False, True, False, Liquid.WATER, True, True)] = \
_5mlT_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 551.8, 50.0: 66.4, 5000.0: 5180.0, 4000.0: 4165.0, 0.0: 0.0, 3000.0: 3148.0, 100.0: 122.7, 2000.0: 2128.0, 1000.0: 1082.0},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=500.0,
  aspiration_air_transport_volume=20.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=1000.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=20.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=400.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(5000, False, True, False, Liquid.WATER, False, True)] = \
_5mlT_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 547.0, 50.0: 65.5, 5000.0: 5145.0, 4000.0: 4145.0, 0.0: 0.0, 3000.0: 3130.0, 100.0: 120.9, 2000.0: 2125.0, 10.0: 15.1, 1000.0: 1075.0},
  aspiration_flow_rate=2000.0,
  aspiration_mix_flow_rate=500.0,
  aspiration_air_transport_volume=20.0,
  aspiration_blow_out_volume=20.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=500.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=500.0,
  dispense_air_transport_volume=20.0,
  dispense_blow_out_volume=20.0,
  dispense_swap_speed=5.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=500.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 250
star_mapping[(1000, False, False, False, Liquid.WATER, True, False)] = \
HighNeedle_Water_DispenseJet = HamiltonLiquidClass(
  curve={500.0: 527.3, 50.0: 56.8, 0.0: 0.0, 100.0: 110.4, 20.0: 24.7, 1000.0: 1046.5, 200.0: 214.6, 10.0: 13.2},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=500.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=350.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, False, False, Liquid.WATER, True, True)] = \
HighNeedle_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 527.3, 50.0: 56.8, 0.0: 0.0, 100.0: 110.4, 20.0: 24.7, 1000.0: 1046.5, 200.0: 214.6, 10.0: 13.2},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=500.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=350.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, False, False, Liquid.WATER, True, False)] = \
HighNeedle_Water_DispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 527.3, 50.0: 56.8, 0.0: 0.0, 100.0: 110.4, 20.0: 24.7, 1000.0: 1046.5, 200.0: 214.6, 10.0: 13.2},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=500.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=350.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 120
star_mapping[(1000, False, False, False, Liquid.WATER, False, False)] = \
HighNeedle_Water_DispenseSurface = HamiltonLiquidClass(
  curve={50.0: 53.1, 0.0: 0.0, 20.0: 22.3, 1000.0: 1000.0, 10.0: 10.8},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=20.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=20.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, False, False, Liquid.WATER, False, True)] = \
HighNeedle_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={50.0: 53.1, 0.0: 0.0, 20.0: 22.3, 1000.0: 1000.0, 10.0: 10.8},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=20.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=20.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, False, False, Liquid.WATER, False, False)] = \
HighNeedle_Water_DispenseSurface_Part = HamiltonLiquidClass(
  curve={50.0: 53.1, 0.0: 0.0, 20.0: 22.3, 1000.0: 1000.0, 10.0: 10.8},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=20.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth Asp. 0.5mm
# - without pre-rinsing
# - Dispense: jet mode empty tip
# - Pipetting-Volumes jet-dispense  between 20-1000µl
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       0.57                   2.84
#       50                       0.30                   0.27
#     100                       0.32                   0.54
#     500                       0.13                  -0.06
#   1000                       0.11                   0.17
star_mapping[(1000, False, True, False, Liquid.ACETONITRIL80WATER20, True, False)] = \
HighVolumeAcetonitril80Water20DispenseJet = HamiltonLiquidClass(
  curve={500.0: 514.5, 50.0: 57.5, 0.0: 0.0, 20.0: 25.0, 100.0: 110.5, 1000.0: 1020.8},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=30.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=100.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth Asp. 2mm, without pre-rinsing
# - Disp.: jet mode empty tip
# - Pipetting-Volumes jet-dispense  between 20-1000µl
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       1.04                 - 2.68
#       50                       0.66                   1.53
#     100                       0.20                   0.09
#     200                       0.22                   0.71
#     500                       0.14                   0.01
#   1000                       0.17                   0.02
star_mapping[(1000, False, True, False, Liquid.ACETONITRILE, True, False)] = \
HighVolumeAcetonitrilDispenseJet = HamiltonLiquidClass(
  curve={500.0: 526.5, 250.0: 269.0, 50.0: 60.5, 0.0: 0.0, 20.0: 25.5, 100.0: 112.7, 1000.0: 1045.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=30.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=100.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.ACETONITRILE, True, True)] = \
HighVolumeAcetonitrilDispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 526.5, 250.0: 269.0, 50.0: 60.5, 0.0: 0.0, 100.0: 112.7, 20.0: 25.5, 1000.0: 1045.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=30.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.ACETONITRILE, True, False)] = \
HighVolumeAcetonitrilDispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 526.5, 250.0: 269.0, 50.0: 60.5, 0.0: 0.0, 100.0: 112.7, 20.0: 25.5, 1000.0: 1045.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=30.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=10.0
)


# - submerge depth: Asp.  2mm
#                              Disp. 2mm
# - without pre-rinsing
# - dispense mode surface empty tip
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       10                       2.06                   0.63
#       20                       0.59                   1.63
#       50                       0.41                   2.27
#     100                       0.25                   0.40
#     200                       0.18                   0.69
#     500                       0.23                   0.04
#   1000                       0.22                   0.05
star_mapping[(1000, False, True, False, Liquid.ACETONITRILE, False, False)] = \
HighVolumeAcetonitrilDispenseSurface = HamiltonLiquidClass(
  curve={500.0: 525.4, 250.0: 267.0, 50.0: 57.6, 0.0: 0.0, 20.0: 23.8, 100.0: 111.2, 10.0: 12.1, 1000.0: 1048.8},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=0.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.ACETONITRILE, False, True)] = \
HighVolumeAcetonitrilDispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 525.4, 250.0: 267.0, 50.0: 57.6, 0.0: 0.0, 100.0: 111.2, 20.0: 23.8, 1000.0: 1048.8, 10.0: 12.1},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=0.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.ACETONITRILE, False, False)] = \
HighVolumeAcetonitrilDispenseSurface_Part = HamiltonLiquidClass(
  curve={500.0: 525.4, 250.0: 267.0, 50.0: 57.6, 0.0: 0.0, 100.0: 111.2, 20.0: 23.8, 1000.0: 1048.8},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=0.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# -  Submerge depth: Aspiration 2.0mm
#    (bei Schaumbildung durch mischen/vorbenetzen evtl.5mm, LLD-Erkennung)
# -  Mischen 3-5 x 950µl, mix position 0.5mm, je nach Volumen im Tube
star_mapping[(1000, False, True, False, Liquid.BLOOD, True, False)] = \
HighVolumeBloodDispenseJet = HamiltonLiquidClass(
  curve={500.0: 536.3, 250.0: 275.6, 50.0: 59.8, 0.0: 0.0, 20.0: 26.2, 100.0: 115.3, 10.0: 12.2, 1000.0: 1061.6},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=300.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth Asp. 5mm, (build airbubbles with mix)
# - 5 x pre-rinsing/mix, with 1000ul, mix position 1mm
# - Disp. mode jet empty tip
# - Pipettingvolume jet-dispense from 10µl - 200µl
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       10                       2.95                   0.35
#       20                       0.69                   0.07
#       50                       0.40                   0.46
#     100                       0.23                   0.93
#     200                       0.15                   0.41
#
star_mapping[(1000, False, True, False, Liquid.BRAINHOMOGENATE, True, False)] = \
HighVolumeBrainHomogenateDispenseJet = HamiltonLiquidClass(
  curve={50.0: 57.9, 0.0: 0.0, 20.0: 25.3, 100.0: 111.3, 10.0: 14.2, 200.0: 214.5, 1000.0: 1038.6},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=500.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth Asp. 1mm, pLLD very high
# - 3 x pre-rinsing, with probevolume or 1 x pre-rinsing with 1000ul,
#   mix position 1mm (mix flow rate is intentional low)
# - Disp. mode jet empty tip
# - Pipettingvolume jet-dispense from 400µl - 1000µl, small volumes 20-100ul drops faster out,
#   because the channel is not enough saturated
# - To protect, the distance from Asp. to Disp. should be as short as possible,
#   because Chloroform could be drop out in a long way!
# - a break time after dispense with about 10s time counter, makes shure the drop which  residue
#   after dispense drops back into the probetube
# - some droplets on tip after dispense are also with more air transport volume not avoidable
# - sometimes it helpes using Filtertips
# - Correction Curve is taken from MeOH Liqiudclass
#
#
#
star_mapping[(1000, False, True, False, Liquid.CHLOROFORM, True, False)] = \
HighVolumeChloroformDispenseJet = HamiltonLiquidClass(
  curve={500.0: 520.5, 250.0: 269.0, 50.0: 62.9, 0.0: 0.0, 100.0: 116.3, 1000.0: 1030.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=30.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=100.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


# -  ohne vorbenetzen, gleicher Tip
# -  Aspiration submerge depth  1.0mm
# -  Prealiquot equal to Aliquotvolume,  jet mode part volume
# -  Aliquot, jet mode part volume
# -  Postaliquot equal to Aliquotvolume,  jet mode empty tip
#
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl                     Precision %        Trueness %
#       50  (12 Aliquots)          0.22                  -4.84
#     100  (  9 Aliquots)          0.25                  -4.81
#
#
star_mapping[(1000, False, True, False, Liquid.DMSO, True, False)] = \
HighVolumeDMSOAliquotJet = HamiltonLiquidClass(
  curve={500.0: 500.0, 250.0: 250.0, 0.0: 0.0, 30.0: 30.0, 20.0: 20.0, 100.0: 100.0, 10.0: 10.0, 750.0: 750.0, 1000.0: 1000.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(1000, True, True, True, Liquid.DMSO, True, True)] = \
HighVolumeFilter_96COREHead1000ul_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 508.2, 0.0: 0.0, 20.0: 21.7, 100.0: 101.7, 1000.0: 1017.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, True, True, True, Liquid.DMSO, False, True)] = \
HighVolumeFilter_96COREHead1000ul_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 512.5, 0.0: 0.0, 100.0: 105.8, 10.0: 12.7, 1000.0: 1024.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, True, True, True, Liquid.WATER, True, True)] = \
HighVolumeFilter_96COREHead1000ul_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 524.0, 0.0: 0.0, 20.0: 24.0, 100.0: 109.2, 1000.0: 1040.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, True, True, True, Liquid.WATER, False, True)] = \
HighVolumeFilter_96COREHead1000ul_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 522.0, 0.0: 0.0, 100.0: 108.3, 1000.0: 1034.0, 10.0: 12.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# -  ohne vorbenetzen, gleicher Tip
# -  Aspiration submerge depth  1.0mm
# -  Prealiquot equal to Aliquotvolume,  jet mode part volume
# -  Aliquot, jet mode part volume
# -  Postaliquot equal to Aliquotvolume,  jet mode empty tip
#
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl                     Precision %        Trueness %
#       50  (12 Aliquots)          0.22                  -4.84
#     100  (  9 Aliquots)          0.25                  -4.81
#
#
star_mapping[(1000, False, True, True, Liquid.DMSO, True, False)] = \
HighVolumeFilter_DMSO_AliquotDispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 500.0, 250.0: 250.0, 30.0: 30.0, 0.0: 0.0, 100.0: 100.0, 20.0: 20.0, 1000.0: 1000.0, 750.0: 750.0, 10.0: 10.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 250
star_mapping[(1000, False, True, True, Liquid.DMSO, True, False)] = \
HighVolumeFilter_DMSO_DispenseJet = HamiltonLiquidClass(
  curve={5.0: 5.1, 500.0: 511.2, 250.0: 256.2, 50.0: 52.2, 0.0: 0.0, 20.0: 21.3, 100.0: 103.4, 10.0: 10.7, 1000.0: 1021.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.DMSO, True, True)] = \
HighVolumeFilter_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 511.2, 5.0: 5.1, 250.0: 256.2, 50.0: 52.2, 0.0: 0.0, 100.0: 103.4, 20.0: 21.3, 1000.0: 1021.0, 10.0: 10.7},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.DMSO, True, False)] = \
HighVolumeFilter_DMSO_DispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 517.2, 0.0: 0.0, 100.0: 109.5, 20.0: 27.0, 1000.0: 1027.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 120
star_mapping[(1000, False, True, True, Liquid.DMSO, False, False)] = \
HighVolumeFilter_DMSO_DispenseSurface = HamiltonLiquidClass(
  curve={500.0: 514.3, 250.0: 259.0, 50.0: 54.4, 0.0: 0.0, 20.0: 22.8, 100.0: 105.8, 10.0: 12.1, 1000.0: 1024.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.DMSO, False, True)] = \
HighVolumeFilter_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 514.3, 250.0: 259.0, 50.0: 54.4, 0.0: 0.0, 100.0: 105.8, 20.0: 22.8, 1000.0: 1024.5, 10.0: 12.1},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


#
star_mapping[(1000, False, True, True, Liquid.DMSO, False, False)] = \
HighVolumeFilter_DMSO_DispenseSurface_Part = HamiltonLiquidClass(
  curve={500.0: 514.3, 250.0: 259.0, 50.0: 54.4, 0.0: 0.0, 100.0: 105.8, 20.0: 22.8, 1000.0: 1024.5, 10.0: 12.1},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 250, Stop back volume = 0
star_mapping[(1000, False, True, True, Liquid.ETHANOL, True, False)] = \
HighVolumeFilter_EtOH_DispenseJet = HamiltonLiquidClass(
  curve={500.0: 534.8, 250.0: 273.0, 50.0: 62.9, 0.0: 0.0, 20.0: 27.8, 100.0: 116.3, 10.0: 15.8, 1000.0: 1053.9},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.ETHANOL, True, True)] = \
HighVolumeFilter_EtOH_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 534.8, 250.0: 273.0, 50.0: 62.9, 0.0: 0.0, 100.0: 116.3, 20.0: 27.8, 1000.0: 1053.9, 10.0: 15.8},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.ETHANOL, True, False)] = \
HighVolumeFilter_EtOH_DispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 534.8, 250.0: 273.0, 50.0: 62.9, 0.0: 0.0, 100.0: 116.3, 20.0: 27.8, 1000.0: 1053.9},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=5.0
)


# V1.1: Set mix flow rate to 120
star_mapping[(1000, False, True, True, Liquid.ETHANOL, False, False)] = \
HighVolumeFilter_EtOH_DispenseSurface = HamiltonLiquidClass(
  curve={500.0: 528.4, 250.0: 269.2, 50.0: 61.2, 0.0: 0.0, 20.0: 27.6, 100.0: 114.0, 10.0: 15.7, 1000.0: 1044.3},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.ETHANOL, False, True)] = \
HighVolumeFilter_EtOH_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 528.4, 250.0: 269.2, 50.0: 61.2, 0.0: 0.0, 100.0: 114.0, 20.0: 27.6, 1000.0: 1044.3, 10.0: 15.7},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.ETHANOL, False, False)] = \
HighVolumeFilter_EtOH_DispenseSurface_Part = HamiltonLiquidClass(
  curve={500.0: 528.4, 250.0: 269.2, 50.0: 61.2, 0.0: 0.0, 100.0: 114.0, 20.0: 27.6, 1000.0: 1044.3, 10.0: 15.7},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 200
star_mapping[(1000, False, True, True, Liquid.GLYCERIN80, True, False)] = \
HighVolumeFilter_Glycerin80_DispenseJet = HamiltonLiquidClass(
  curve={500.0: 537.8, 250.0: 277.0, 50.0: 63.3, 0.0: 0.0, 20.0: 28.0, 100.0: 118.8, 10.0: 15.2, 1000.0: 1060.0},
  aspiration_flow_rate=200.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 200
star_mapping[(1000, False, True, True, Liquid.GLYCERIN80, True, True)] = \
HighVolumeFilter_Glycerin80_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 537.8, 250.0: 277.0, 50.0: 63.3, 0.0: 0.0, 100.0: 118.8, 20.0: 28.0, 1000.0: 1060.0, 10.0: 15.2},
  aspiration_flow_rate=200.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 120
star_mapping[(1000, False, True, True, Liquid.GLYCERIN80, False, False)] = \
HighVolumeFilter_Glycerin80_DispenseSurface = HamiltonLiquidClass(
  curve={500.0: 513.5, 250.0: 257.2, 50.0: 55.0, 0.0: 0.0, 20.0: 22.7, 100.0: 105.5, 10.0: 12.2, 1000.0: 1027.2},
  aspiration_flow_rate=150.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.5,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.GLYCERIN80, False, True)] = \
HighVolumeFilter_Glycerin80_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 513.5, 250.0: 257.2, 50.0: 55.0, 0.0: 0.0, 100.0: 105.5, 20.0: 22.7, 1000.0: 1027.2, 10.0: 12.2},
  aspiration_flow_rate=150.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.5,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.GLYCERIN80, False, False)] = \
HighVolumeFilter_Glycerin80_DispenseSurface_Part = HamiltonLiquidClass(
  curve={500.0: 513.5, 250.0: 257.2, 50.0: 55.0, 0.0: 0.0, 100.0: 105.5, 20.0: 22.7, 1000.0: 1027.2, 10.0: 12.2},
  aspiration_flow_rate=150.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.5,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 250
star_mapping[(1000, False, True, True, Liquid.SERUM, True, False)] = \
HighVolumeFilter_Serum_AliquotDispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 500.0, 250.0: 250.0, 30.0: 30.0, 0.0: 0.0, 100.0: 100.0, 20.0: 20.0, 1000.0: 1000.0, 750.0: 750.0, 10.0: 10.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=300.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 250
star_mapping[(1000, False, True, True, Liquid.SERUM, True, False)] = \
HighVolumeFilter_Serum_AliquotJet = HamiltonLiquidClass(
  curve={500.0: 500.0, 250.0: 250.0, 0.0: 0.0, 30.0: 30.0, 20.0: 20.0, 100.0: 100.0, 10.0: 10.0, 750.0: 750.0, 1000.0: 1000.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=300.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 250, Settling time = 0
star_mapping[(1000, False, True, True, Liquid.SERUM, True, False)] = \
HighVolumeFilter_Serum_DispenseJet = HamiltonLiquidClass(
  curve={500.0: 525.3, 250.0: 266.6, 50.0: 57.9, 0.0: 0.0, 20.0: 24.2, 100.0: 111.3, 10.0: 12.2, 1000.0: 1038.6},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.SERUM, True, True)] = \
HighVolumeFilter_Serum_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 525.3, 250.0: 266.6, 50.0: 57.9, 0.0: 0.0, 100.0: 111.3, 20.0: 24.2, 1000.0: 1038.6, 10.0: 12.2},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.SERUM, True, False)] = \
HighVolumeFilter_Serum_DispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 525.3, 0.0: 0.0, 100.0: 111.3, 20.0: 27.3, 1000.0: 1046.6},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 120
star_mapping[(1000, False, True, True, Liquid.SERUM, False, False)] = \
HighVolumeFilter_Serum_DispenseSurface = HamiltonLiquidClass(
  curve={500.0: 517.5, 250.0: 261.9, 50.0: 55.9, 0.0: 0.0, 20.0: 23.2, 100.0: 108.2, 10.0: 11.8, 1000.0: 1026.7},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.SERUM, False, True)] = \
HighVolumeFilter_Serum_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 517.5, 250.0: 261.9, 50.0: 55.9, 0.0: 0.0, 100.0: 108.2, 20.0: 23.2, 1000.0: 1026.7, 10.0: 11.8},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.SERUM, False, False)] = \
HighVolumeFilter_Serum_DispenseSurface_Part = HamiltonLiquidClass(
  curve={500.0: 523.5, 0.0: 0.0, 100.0: 111.2, 20.0: 23.2, 1000.0: 1038.7, 10.0: 11.8},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 250
star_mapping[(1000, False, True, True, Liquid.WATER, True, False)] = \
HighVolumeFilter_Water_AliquotDispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 500.0, 250.0: 250.0, 30.0: 30.0, 0.0: 0.0, 100.0: 100.0, 20.0: 20.0, 1000.0: 1000.0, 750.0: 750.0, 10.0: 10.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 250
star_mapping[(1000, False, True, True, Liquid.WATER, True, False)] = \
HighVolumeFilter_Water_AliquotJet = HamiltonLiquidClass(
  curve={500.0: 500.0, 250.0: 250.0, 0.0: 0.0, 30.0: 30.0, 20.0: 20.0, 100.0: 100.0, 10.0: 10.0, 750.0: 750.0, 1000.0: 1000.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 250
star_mapping[(1000, False, True, True, Liquid.WATER, True, False)] = \
HighVolumeFilter_Water_DispenseJet = HamiltonLiquidClass(
  curve={500.0: 521.7, 50.0: 57.2, 0.0: 0.0, 20.0: 24.6, 100.0: 109.6, 10.0: 13.3, 200.0: 212.9, 1000.0: 1034.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.WATER, True, True)] = \
HighVolumeFilter_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 521.7, 50.0: 57.2, 0.0: 0.0, 100.0: 109.6, 20.0: 24.6, 1000.0: 1034.0, 200.0: 212.9, 10.0: 13.3},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.WATER, True, False)] = \
HighVolumeFilter_Water_DispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 521.7, 50.0: 57.2, 0.0: 0.0, 100.0: 109.6, 20.0: 27.0, 1000.0: 1034.0, 200.0: 212.9},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=20.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 120, Clot retract hight = 0
star_mapping[(1000, False, True, True, Liquid.WATER, False, False)] = \
HighVolumeFilter_Water_DispenseSurface = HamiltonLiquidClass(
  curve={500.0: 518.3, 50.0: 56.3, 0.0: 0.0, 20.0: 23.9, 100.0: 108.3, 10.0: 12.5, 200.0: 211.0, 1000.0: 1028.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.WATER, False, True)] = \
HighVolumeFilter_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 518.3, 50.0: 56.3, 0.0: 0.0, 20.0: 23.9, 100.0: 108.3, 10.0: 12.5, 200.0: 211.0, 1000.0: 1028.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, True, Liquid.WATER, False, False)] = \
HighVolumeFilter_Water_DispenseSurface_Part = HamiltonLiquidClass(
  curve={500.0: 518.3, 50.0: 56.3, 0.0: 0.0, 100.0: 108.3, 20.0: 23.9, 1000.0: 1028.5, 200.0: 211.0, 10.0: 12.7},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=30.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth Asp. 2mm
# - 3 x pre-rinsing, with probevolume, mix position 1mm (mix flow rate is intentional low)
# - Disp. mode jet empty tip
# - Pipettingvolume jet-dispense from 50µl - 1000µl
# - To protect, the distance from Asp. to Disp. should be as short as possible,
#   because MeOH could be drop out in a long way!
# - some droplets on tip after dispense are also with more air transport volume not avoidable
# - sometimes it helpes using Filtertips
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       50                       0.61                 - 1.88
#     100                       1.16                   3.02
#     200                       0.55                   1.87
#     500                       0.49                 - 0.17
#   1000                       0.55                   0.712
#
star_mapping[(1000, False, True, False, Liquid.METHANOL, True, False)] = \
HighVolumeMeOHDispenseJet = HamiltonLiquidClass(
  curve={500.0: 520.5, 250.0: 269.0, 50.0: 62.9, 0.0: 0.0, 100.0: 116.3, 1000.0: 1030.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=30.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=100.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth Asp. 2mm
# - 3 x pre-rinsing, with probevolume, mix position 1mm (mix flow rate is intentional low)
#   200 -1000µl 2x is enough
# - Disp. mode jet empty tip
# - Pipettingvolume jet-dispense from 50µl - 1000µl
# - To protect, the distance from Asp. to Disp. should be as short as possible,
#   because MeOH could be drop out in a long way!
# - some droplets on tip after dispense are also with more air transport volume not avoidable
# - sometimes it helpes using Filtertips
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       10                       3.71                 - 5.23
#       20                       3.12                 - 2.27
#       50                       3.97                   1.85
#     100                       0.54                   1.10
#     200                       0.48                   0.18
#     500                       0.17                   0.22
#   1000                       0.75                   0.29
star_mapping[(1000, False, True, False, Liquid.METHANOL, False, False)] = \
HighVolumeMeOHDispenseSurface = HamiltonLiquidClass(
  curve={500.0: 518.0, 50.0: 61.3, 0.0: 0.0, 20.0: 29.3, 100.0: 111.0, 10.0: 19.3, 200.0: 215.0, 1000.0: 1030.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=0.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=50.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth Asp. 2mm
# - without pre-rinsing
# - Disp. mode jet empty tip
# - Pipettingvolume jet-dispense from 20µl - 1000µl
#
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       1.45                 - 4.76
#       50                       0.59                   0.08
#     100                       0.24                   0.85
#     200                       0.14                   0.06
#     500                       0.12                 - 0.07
#   1000                       0.16                   0.08
star_mapping[(1000, False, True, False, Liquid.METHANOL70WATER030, True, False)] = \
HighVolumeMeOHH2ODispenseJet = HamiltonLiquidClass(
  curve={500.0: 528.5, 250.0: 269.0, 50.0: 60.5, 0.0: 0.0, 100.0: 114.3, 1000.0: 1050.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=50.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=100.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


# - use pLLD
# - submerge depth>: Asp. 0.5 mm
#                                Disp. 1.0 mm (surface)
# - without pre-rinsing
# - dispense mode  jet empty tip
#
#
# Typical performance data under laboratory conditions:
#
# (Liquid adapting with parameters like DMSO, correctioncurve like Glycerin80%)
# tested two volumes
#
# Volume µl            Precision %        Trueness %
#       20                       2.85                   2.92
#     200                       0.14                   0.59
#
star_mapping[(1000, False, True, False, Liquid.OCTANOL, True, False)] = \
HighVolumeOctanol100DispenseJet = HamiltonLiquidClass(
  curve={500.0: 537.8, 250.0: 277.0, 50.0: 63.3, 0.0: 0.0, 20.0: 28.0, 100.0: 118.8, 10.0: 15.2, 1000.0: 1060.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=350.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


# - use pLLD
# - submerge depth>: Asp. 0.5 mm
#                                Disp. 1.0 mm (surface)
# - without pre-rinsing
# - dispense mode surface empty tip
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       10                       2.47                 - 6.09
#       20                       0.90                   1.77
#       50                       0.45                   3.14
#     100                       1.07                   1.23
#     200                       0.30                   1.30
#     500                       0.31                   0.01
#   1000                       0.33                   0.01
star_mapping[(1000, False, True, False, Liquid.OCTANOL, False, False)] = \
HighVolumeOctanol100DispenseSurface = HamiltonLiquidClass(
  curve={500.0: 531.3, 250.0: 265.0, 50.0: 54.4, 0.0: 0.0, 20.0: 23.3, 100.0: 108.8, 10.0: 12.1, 1000.0: 1058.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, True, True, False, Liquid.DMSO, True, False)] = \
HighVolume_96COREHead1000ul_DMSO_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={500.0: 524.0, 0.0: 0.0, 100.0: 107.2, 20.0: 24.0, 1000.0: 1025.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=20.0
)


star_mapping[(1000, True, True, False, Liquid.DMSO, True, True)] = \
HighVolume_96COREHead1000ul_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 508.2, 0.0: 0.0, 100.0: 101.7, 20.0: 21.7, 1000.0: 1017.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, True, True, False, Liquid.DMSO, False, True)] = \
HighVolume_96COREHead1000ul_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 512.5, 0.0: 0.0, 100.0: 105.8, 1000.0: 1024.5, 10.0: 12.7},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# to prevent drop's, mix 2x with e.g. 500ul
star_mapping[(1000, True, True, False, Liquid.ETHANOL, True, False)] = \
HighVolume_96COREHead1000ul_EtOH_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 500.0: 500.0, 0.0: 0.0, 100.0: 100.0, 20.0: 20.0, 1000.0: 1000.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=4.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=400.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(1000, True, True, False, Liquid.ETHANOL, True, True)] = \
HighVolume_96COREHead1000ul_EtOH_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 516.5, 0.0: 0.0, 100.0: 108.3, 20.0: 24.0, 1000.0: 1027.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


# to prevent drop's, mix 2x with e.g. 500ul
star_mapping[(1000, True, True, False, Liquid.ETHANOL, False, True)] = \
HighVolume_96COREHead1000ul_EtOH_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 516.5, 0.0: 0.0, 100.0: 107.0, 1000.0: 1027.0, 10.0: 14.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=150.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=5.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=150.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=5.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, True, True, False, Liquid.GLYCERIN80, False, True)] = \
HighVolume_96COREHead1000ul_Glycerin80_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 522.0, 0.0: 0.0, 100.0: 115.3, 1000.0: 1034.0, 10.0: 12.5},
  aspiration_flow_rate=150.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.5,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, True, True, False, Liquid.WATER, True, False)] = \
HighVolume_96COREHead1000ul_Water_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={500.0: 524.0, 0.0: 0.0, 100.0: 107.2, 20.0: 24.0, 1000.0: 1025.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(1000, True, True, False, Liquid.WATER, True, True)] = \
HighVolume_96COREHead1000ul_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 524.0, 0.0: 0.0, 100.0: 107.2, 20.0: 24.0, 1000.0: 1025.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, True, True, False, Liquid.WATER, False, True)] = \
HighVolume_96COREHead1000ul_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 522.0, 0.0: 0.0, 100.0: 108.3, 1000.0: 1034.0, 10.0: 12.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# Liquid class for wash high volume tips with CO-RE 96 Head in CO-RE 96 Head Washer.
star_mapping[(1000, True, True, False, Liquid.WATER, False, False)] = \
HighVolume_Core96Washer_DispenseSurface = HamiltonLiquidClass(
  curve={500.0: 520.0, 50.0: 56.3, 0.0: 0.0, 100.0: 110.0, 20.0: 23.9, 1000.0: 1050.0, 200.0: 212.0, 10.0: 12.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=220.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=220.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=220.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=5.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# -  ohne vorbenetzen, gleicher Tip
# -  Aspiration submerge depth  1.0mm
# -  Prealiquot equal to Aliquotvolume,  jet mode part volume
# -  Aliquot, jet mode part volume
# -  Postaliquot equal to Aliquotvolume,  jet mode empty tip
#
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl                     Precision %        Trueness %
#       50  (12 Aliquots)          0.22                  -4.84
#     100  (  9 Aliquots)          0.25                  -4.81
#
#
star_mapping[(1000, False, True, False, Liquid.DMSO, True, False)] = \
HighVolume_DMSO_AliquotDispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 500.0, 250.0: 250.0, 30.0: 30.0, 0.0: 0.0, 100.0: 100.0, 20.0: 20.0, 1000.0: 1000.0, 750.0: 750.0, 10.0: 10.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 250
star_mapping[(1000, False, True, False, Liquid.DMSO, True, False)] = \
HighVolume_DMSO_DispenseJet = HamiltonLiquidClass(
  curve={5.0: 5.1, 500.0: 511.2, 250.0: 256.2, 50.0: 52.2, 0.0: 0.0, 20.0: 21.3, 100.0: 103.4, 10.0: 10.7, 1000.0: 1021.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.DMSO, True, True)] = \
HighVolume_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 511.2, 5.0: 5.1, 250.0: 256.2, 50.0: 52.2, 0.0: 0.0, 100.0: 103.4, 20.0: 21.3, 1000.0: 1021.0, 10.0: 10.7},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.DMSO, True, False)] = \
HighVolume_DMSO_DispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 520.2, 0.0: 0.0, 100.0: 112.0, 20.0: 27.0, 1000.0: 1031.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=5.0
)


# V1.1: Set mix flow rate to 120
star_mapping[(1000, False, True, False, Liquid.DMSO, False, False)] = \
HighVolume_DMSO_DispenseSurface = HamiltonLiquidClass(
  curve={500.0: 514.3, 250.0: 259.0, 50.0: 54.4, 0.0: 0.0, 20.0: 22.8, 100.0: 105.8, 10.0: 12.1, 1000.0: 1024.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.DMSO, False, True)] = \
HighVolume_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 514.3, 250.0: 259.0, 50.0: 54.4, 0.0: 0.0, 100.0: 105.8, 20.0: 22.8, 1000.0: 1024.5, 10.0: 12.1},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.DMSO, False, False)] = \
HighVolume_DMSO_DispenseSurface_Part = HamiltonLiquidClass(
  curve={500.0: 514.3, 250.0: 259.0, 50.0: 54.4, 0.0: 0.0, 100.0: 105.8, 20.0: 22.8, 1000.0: 1024.5, 10.0: 12.4},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set Stop back volume to 0
star_mapping[(1000, False, True, False, Liquid.ETHANOL, True, False)] = \
HighVolume_EtOH_DispenseJet = HamiltonLiquidClass(
  curve={500.0: 534.8, 250.0: 273.0, 50.0: 62.9, 0.0: 0.0, 20.0: 27.8, 100.0: 116.3, 10.0: 15.8, 1000.0: 1053.9},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.ETHANOL, True, True)] = \
HighVolume_EtOH_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 534.8, 250.0: 273.0, 50.0: 62.9, 0.0: 0.0, 100.0: 116.3, 20.0: 27.8, 1000.0: 1053.9, 10.0: 15.8},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.ETHANOL, True, False)] = \
HighVolume_EtOH_DispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 529.0, 50.0: 62.9, 0.0: 0.0, 100.0: 114.5, 20.0: 27.8, 1000.0: 1053.9},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=5.0
)


star_mapping[(1000, False, True, False, Liquid.ETHANOL, False, False)] = \
HighVolume_EtOH_DispenseSurface = HamiltonLiquidClass(
  curve={500.0: 528.4, 250.0: 269.2, 50.0: 61.2, 0.0: 0.0, 100.0: 114.0, 20.0: 27.6, 1000.0: 1044.3, 10.0: 15.7},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.ETHANOL, False, True)] = \
HighVolume_EtOH_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 528.4, 250.0: 269.2, 50.0: 61.2, 0.0: 0.0, 20.0: 27.6, 100.0: 114.0, 10.0: 15.7, 1000.0: 1044.3},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.ETHANOL, False, False)] = \
HighVolume_EtOH_DispenseSurface_Part = HamiltonLiquidClass(
  curve={500.0: 528.4, 250.0: 269.2, 50.0: 61.2, 0.0: 0.0, 100.0: 114.0, 20.0: 27.6, 1000.0: 1044.3, 10.0: 14.7},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 200
star_mapping[(1000, False, True, False, Liquid.GLYCERIN80, True, False)] = \
HighVolume_Glycerin80_DispenseJet = HamiltonLiquidClass(
  curve={500.0: 537.8, 250.0: 277.0, 50.0: 63.3, 0.0: 0.0, 20.0: 28.0, 100.0: 118.8, 10.0: 15.2, 1000.0: 1060.0},
  aspiration_flow_rate=200.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.GLYCERIN80, True, True)] = \
HighVolume_Glycerin80_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 537.8, 250.0: 277.0, 50.0: 63.3, 0.0: 0.0, 100.0: 118.8, 20.0: 28.0, 1000.0: 1060.0, 10.0: 15.2},
  aspiration_flow_rate=200.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 120
star_mapping[(1000, False, True, False, Liquid.GLYCERIN80, False, False)] = \
HighVolume_Glycerin80_DispenseSurface = HamiltonLiquidClass(
  curve={500.0: 513.5, 250.0: 257.2, 50.0: 55.0, 0.0: 0.0, 20.0: 22.7, 100.0: 105.5, 10.0: 12.2, 1000.0: 1027.2},
  aspiration_flow_rate=150.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.5,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.GLYCERIN80, False, True)] = \
HighVolume_Glycerin80_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 513.5, 250.0: 257.2, 50.0: 55.0, 0.0: 0.0, 100.0: 105.5, 20.0: 22.7, 1000.0: 1027.2, 10.0: 12.2},
  aspiration_flow_rate=150.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.5,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.GLYCERIN80, False, False)] = \
HighVolume_Glycerin80_DispenseSurface_Part = HamiltonLiquidClass(
  curve={500.0: 513.5, 250.0: 257.2, 50.0: 55.0, 0.0: 0.0, 100.0: 105.5, 20.0: 22.7, 1000.0: 1027.2, 10.0: 12.2},
  aspiration_flow_rate=150.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.5,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 250
star_mapping[(1000, False, True, False, Liquid.SERUM, True, False)] = \
HighVolume_Serum_AliquotDispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 500.0, 250.0: 250.0, 30.0: 30.0, 0.0: 0.0, 100.0: 100.0, 20.0: 20.0, 1000.0: 1000.0, 750.0: 750.0, 10.0: 10.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=300.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 250
star_mapping[(1000, False, True, False, Liquid.SERUM, True, False)] = \
HighVolume_Serum_AliquotJet = HamiltonLiquidClass(
  curve={500.0: 500.0, 250.0: 250.0, 0.0: 0.0, 30.0: 30.0, 20.0: 20.0, 100.0: 100.0, 10.0: 10.0, 750.0: 750.0, 1000.0: 1000.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=300.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 250, settling time = 0
star_mapping[(1000, False, True, False, Liquid.SERUM, True, False)] = \
HighVolume_Serum_DispenseJet = HamiltonLiquidClass(
  curve={500.0: 525.3, 250.0: 266.6, 50.0: 57.9, 0.0: 0.0, 20.0: 24.2, 100.0: 111.3, 10.0: 12.2, 1000.0: 1038.6},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.SERUM, True, True)] = \
HighVolume_Serum_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 525.3, 250.0: 266.6, 50.0: 57.9, 0.0: 0.0, 100.0: 111.3, 20.0: 24.2, 1000.0: 1038.6, 10.0: 12.2},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.SERUM, True, False)] = \
HighVolume_Serum_DispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 525.3, 0.0: 0.0, 100.0: 111.3, 20.0: 27.3, 1000.0: 1046.6},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 120
star_mapping[(1000, False, True, False, Liquid.SERUM, False, False)] = \
HighVolume_Serum_DispenseSurface = HamiltonLiquidClass(
  curve={500.0: 517.5, 250.0: 261.9, 50.0: 55.9, 0.0: 0.0, 20.0: 23.2, 100.0: 108.2, 10.0: 11.8, 1000.0: 1026.7},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.SERUM, False, True)] = \
HighVolume_Serum_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 517.5, 250.0: 261.9, 50.0: 55.9, 0.0: 0.0, 100.0: 108.2, 20.0: 23.2, 1000.0: 1026.7, 10.0: 11.8},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.SERUM, False, False)] = \
HighVolume_Serum_DispenseSurface_Part = HamiltonLiquidClass(
  curve={50.0: 55.9, 0.0: 0.0, 100.0: 108.2, 20.0: 23.2, 1000.0: 1037.7, 10.0: 11.8},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 250
star_mapping[(1000, False, True, False, Liquid.WATER, True, False)] = \
HighVolume_Water_AliquotDispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 500.0, 250.0: 250.0, 30.0: 30.0, 0.0: 0.0, 100.0: 100.0, 20.0: 20.0, 1000.0: 1000.0, 750.0: 750.0, 10.0: 10.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 250
star_mapping[(1000, False, True, False, Liquid.WATER, True, False)] = \
HighVolume_Water_AliquotJet = HamiltonLiquidClass(
  curve={500.0: 500.0, 250.0: 250.0, 0.0: 0.0, 30.0: 30.0, 20.0: 20.0, 100.0: 100.0, 10.0: 10.0, 750.0: 750.0, 1000.0: 1000.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 250
star_mapping[(1000, False, True, False, Liquid.WATER, True, False)] = \
HighVolume_Water_DispenseJet = HamiltonLiquidClass(
  curve={500.0: 521.7, 50.0: 57.2, 0.0: 0.0, 20.0: 24.6, 100.0: 109.6, 10.0: 13.3, 200.0: 212.9, 1000.0: 1034.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=250.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.WATER, True, True)] = \
HighVolume_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={500.0: 521.7, 50.0: 57.2, 0.0: 0.0, 100.0: 109.6, 20.0: 24.6, 1000.0: 1034.0, 200.0: 212.9, 10.0: 13.3},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=40.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=40.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.WATER, True, False)] = \
HighVolume_Water_DispenseJet_Part = HamiltonLiquidClass(
  curve={500.0: 521.7, 0.0: 0.0, 100.0: 109.6, 20.0: 26.9, 1000.0: 1040.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=300.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=18.0
)


# V1.1: Set mix flow rate to 120, clot retract height = 0
star_mapping[(1000, False, True, False, Liquid.WATER, False, False)] = \
HighVolume_Water_DispenseSurface = HamiltonLiquidClass(
  curve={500.0: 518.3, 50.0: 56.3, 0.0: 0.0, 20.0: 23.9, 100.0: 108.3, 10.0: 12.5, 200.0: 211.0, 1000.0: 1028.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.WATER, False, True)] = \
HighVolume_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={500.0: 518.3, 50.0: 56.3, 0.0: 0.0, 100.0: 108.3, 20.0: 23.9, 1000.0: 1028.5, 200.0: 211.0, 10.0: 12.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=120.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(1000, False, True, False, Liquid.WATER, False, False)] = \
HighVolume_Water_DispenseSurface_Part = HamiltonLiquidClass(
  curve={500.0: 518.3, 50.0: 56.3, 0.0: 0.0, 100.0: 108.3, 20.0: 23.9, 1000.0: 1036.5, 200.0: 211.0, 10.0: 12.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=120.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=50.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# - without pre-rinsing
# - submerge depth Asp. 1mm
# - for Disp. in empty PCR-Plate from 1µl up
# - fix height from bottom between 0.5-0.7mm
# - dispense mode jet empty tip
# - also with higher DNA concentration
star_mapping[(10, False, False, False, Liquid.DNA_TRIS_EDTA, True, False)] = \
LowNeedleDNADispenseJet = HamiltonLiquidClass(
  curve={5.0: 5.7, 0.5: 1.0, 50.0: 53.0, 0.0: 0.0, 20.0: 22.1, 1.0: 1.5, 10.0: 10.8, 2.0: 2.7},
  aspiration_flow_rate=80.0,
  aspiration_mix_flow_rate=80.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=80.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.5
)


# - without pre-rinsing
# - submerge depth Asp. 1mm
# - for Disp. in empty PCR-Plate/on empty Plate from 1µl up
# - fix height from bottom between 0.5-0.7mm
# - also with higher DNA concentration
star_mapping[(10, False, False, False, Liquid.DNA_TRIS_EDTA, False, False)] = \
LowNeedleDNADispenseSurface = HamiltonLiquidClass(
  curve={5.0: 5.7, 0.5: 1.0, 50.0: 53.0, 0.0: 0.0, 20.0: 22.1, 1.0: 1.5, 10.0: 10.8, 2.0: 2.7},
  aspiration_flow_rate=80.0,
  aspiration_mix_flow_rate=80.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=80.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 60
star_mapping[(10, False, False, False, Liquid.WATER, False, False)] = \
LowNeedle_SysFlWater_DispenseSurface = HamiltonLiquidClass(
  curve={35.0: 35.6, 60.0: 62.7, 50.0: 51.3, 40.0: 40.9, 30.0: 30.0, 0.0: 0.0, 31.0: 31.4, 32.0: 32.7},
  aspiration_flow_rate=60.0,
  aspiration_mix_flow_rate=60.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=100.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=60.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, False, False, Liquid.WATER, True, False)] = \
LowNeedle_Water_DispenseJet = HamiltonLiquidClass(
  curve={50.0: 52.7, 30.0: 31.7, 0.0: 0.0, 20.0: 20.5, 10.0: 10.3},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=15.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, False, False, Liquid.WATER, True, True)] = \
LowNeedle_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={70.0: 70.0, 50.0: 52.7, 30.0: 31.7, 0.0: 0.0, 20.0: 20.5, 10.0: 10.3},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=15.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, False, False, Liquid.WATER, True, False)] = \
LowNeedle_Water_DispenseJet_Part = HamiltonLiquidClass(
  curve={70.0: 70.0, 50.0: 52.7, 30.0: 31.7, 0.0: 0.0, 20.0: 20.5, 10.0: 10.3},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=15.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 60
star_mapping[(10, False, False, False, Liquid.WATER, False, False)] = \
LowNeedle_Water_DispenseSurface = HamiltonLiquidClass(
  curve={5.0: 5.0, 0.5: 0.5, 50.0: 50.0, 0.0: 0.0, 20.0: 20.5, 1.0: 1.0, 10.0: 10.0, 2.0: 2.0},
  aspiration_flow_rate=60.0,
  aspiration_mix_flow_rate=60.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=100.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=60.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, False, False, Liquid.WATER, False, True)] = \
LowNeedle_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.0, 0.5: 0.5, 70.0: 70.0, 50.0: 50.0, 0.0: 0.0, 20.0: 20.5, 1.0: 1.0, 10.0: 10.0, 2.0: 2.0},
  aspiration_flow_rate=60.0,
  aspiration_mix_flow_rate=60.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=100.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=60.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, False, False, Liquid.WATER, False, False)] = \
LowNeedle_Water_DispenseSurface_Part = HamiltonLiquidClass(
  curve={5.0: 5.0, 0.5: 0.5, 70.0: 70.0, 50.0: 50.0, 0.0: 0.0, 20.0: 20.5, 1.0: 1.0, 10.0: 10.0, 2.0: 2.0},
  aspiration_flow_rate=60.0,
  aspiration_mix_flow_rate=60.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=100.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, True, True, True, Liquid.DMSO, False, True)] = \
LowVolumeFilter_96COREHead1000ul_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.3, 0.0: 0.0, 1.0: 0.8, 10.0: 10.0},
  aspiration_flow_rate=25.0,
  aspiration_mix_flow_rate=25.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=35.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=35.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=25.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, True, True, True, Liquid.WATER, False, True)] = \
LowVolumeFilter_96COREHead1000ul_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.8, 0.0: 0.0, 1.0: 1.0, 10.0: 10.0},
  aspiration_flow_rate=25.0,
  aspiration_mix_flow_rate=25.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=35.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=35.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=25.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, True, True, True, Liquid.DMSO, False, True)] = \
LowVolumeFilter_96COREHead_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.1, 0.0: 0.0, 1.0: 0.8, 10.0: 10.0},
  aspiration_flow_rate=25.0,
  aspiration_mix_flow_rate=25.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=35.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=35.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=25.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, True, True, True, Liquid.DMSO, False, False)] = \
LowVolumeFilter_96COREHead_DMSO_DispenseSurface_Part = HamiltonLiquidClass(
  curve={5.0: 5.7, 0.0: 0.0, 1.0: 1.5, 10.0: 10.3},
  aspiration_flow_rate=25.0,
  aspiration_mix_flow_rate=25.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=35.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=35.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=25.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, True, True, True, Liquid.WATER, False, True)] = \
LowVolumeFilter_96COREHead_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.6, 0.0: 0.0, 1.0: 1.2, 10.0: 10.0},
  aspiration_flow_rate=25.0,
  aspiration_mix_flow_rate=25.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=35.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=35.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=25.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, True, True, True, Liquid.WATER, False, False)] = \
LowVolumeFilter_96COREHead_Water_DispenseSurface_Part = HamiltonLiquidClass(
  curve={5.0: 5.8, 0.0: 0.0, 1.0: 1.5, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 75
star_mapping[(10, False, True, True, Liquid.DMSO, False, False)] = \
LowVolumeFilter_DMSO_DispenseSurface = HamiltonLiquidClass(
  curve={5.0: 5.9, 0.5: 0.8, 15.0: 16.4, 0.0: 0.0, 1.0: 1.4, 2.0: 2.6, 10.0: 11.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=56.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, True, Liquid.DMSO, False, True)] = \
LowVolumeFilter_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.9, 0.5: 0.8, 0.0: 0.0, 1.0: 1.4, 10.0: 10.0, 2.0: 2.6},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, True, Liquid.DMSO, False, False)] = \
LowVolumeFilter_DMSO_DispenseSurface_Part = HamiltonLiquidClass(
  curve={5.0: 5.9, 0.0: 0.0, 1.0: 1.4, 10.0: 10.0, 2.0: 2.6},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 75
star_mapping[(10, False, True, True, Liquid.ETHANOL, False, False)] = \
LowVolumeFilter_EtOH_DispenseSurface = HamiltonLiquidClass(
  curve={5.0: 8.4, 0.5: 1.9, 0.0: 0.0, 1.0: 2.7, 2.0: 4.1, 10.0: 13.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=3.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, True, Liquid.ETHANOL, False, True)] = \
LowVolumeFilter_EtOH_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 6.6, 0.0: 0.0, 1.0: 1.8, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=3.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, True, Liquid.ETHANOL, False, False)] = \
LowVolumeFilter_EtOH_DispenseSurface_Part = HamiltonLiquidClass(
  curve={5.0: 6.4, 0.0: 0.0, 1.0: 1.8, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 10
star_mapping[(10, False, True, True, Liquid.GLYCERIN, False, False)] = \
LowVolumeFilter_Glycerin_DispenseSurface = HamiltonLiquidClass(
  curve={5.0: 6.5, 0.5: 1.4, 15.0: 17.0, 0.0: 0.0, 1.0: 2.0, 2.0: 3.2, 10.0: 11.8},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=10.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=10.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=10.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=5.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=2.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, True, Liquid.GLYCERIN80, False, True)] = \
LowVolumeFilter_Glycerin_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 6.5, 0.0: 0.0, 1.0: 0.6, 10.0: 10.0},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=10.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=5.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=10.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=10.0,
  dispense_air_transport_volume=1.0,
  dispense_blow_out_volume=5.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=2.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 75
star_mapping[(10, False, True, True, Liquid.WATER, False, False)] = \
LowVolumeFilter_Water_DispenseSurface = HamiltonLiquidClass(
  curve={5.0: 6.0, 0.5: 0.8, 15.0: 16.7, 0.0: 0.0, 1.0: 1.4, 2.0: 2.6, 10.0: 11.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=56.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, True, Liquid.WATER, False, True)] = \
LowVolumeFilter_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 6.0, 0.5: 0.8, 0.0: 0.0, 1.0: 1.4, 10.0: 10.0, 2.0: 2.6},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, True, Liquid.WATER, False, False)] = \
LowVolumeFilter_Water_DispenseSurface_Part = HamiltonLiquidClass(
  curve={5.0: 5.9, 0.0: 0.0, 1.0: 1.2, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


# - Volume 0.5 - 10ul
# - submerge depth: Asp.  0.5mm
#                              Disp. 0.5mm
# - without pre-rinsing
# - dispense mode surface empty tip
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#      0.5                       5.77                 12.44
#      1.0                       3.65                   4.27
#      2.0                       2.18                   2.27
#      5.0                       1.08                  -1.29
#    10.0                       0.62                   0.53
#
star_mapping[(10, False, True, False, Liquid.PLASMA, False, False)] = \
LowVolumePlasmaDispenseSurface = HamiltonLiquidClass(
  curve={5.0: 5.9, 0.5: 0.8, 0.0: 0.0, 1.0: 1.4, 10.0: 11.5, 2.0: 2.6},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.5,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.5,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, False, Liquid.PLASMA, False, True)] = \
LowVolumePlasmaDispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.6, 0.5: 0.2, 0.0: 0.0, 1.0: 0.9, 10.0: 11.3, 2.0: 2.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.5,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.5,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, False, Liquid.PLASMA, False, False)] = \
LowVolumePlasmaDispenseSurface_Part = HamiltonLiquidClass(
  curve={5.0: 5.9, 0.0: 0.0, 1.0: 1.3, 10.0: 11.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# - Volume 0.5 - 10ul
# - submerge depth: Asp.  0.5mm
#                              Disp. 0.5mm
# - without pre-rinsing
# - dispense mode surface empty tip
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#      0.5                       5.77                 12.44
#      1.0                       3.65                   4.27
#      2.0                       2.18                   2.27
#      5.0                       1.08                  -1.29
#    10.0                       0.62                   0.53
#
star_mapping[(10, False, True, False, Liquid.SERUM, False, False)] = \
LowVolumeSerumDispenseSurface = HamiltonLiquidClass(
  curve={5.0: 5.6, 0.5: 0.2, 0.0: 0.0, 1.0: 0.9, 10.0: 11.3, 2.0: 2.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.5,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.5,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, False, Liquid.SERUM, False, True)] = \
LowVolumeSerumDispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.6, 0.5: 0.2, 0.0: 0.0, 1.0: 0.9, 10.0: 11.3, 2.0: 2.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.5,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.5,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, False, Liquid.SERUM, False, False)] = \
LowVolumeSerumDispenseSurface_Part = HamiltonLiquidClass(
  curve={5.0: 5.9, 0.0: 0.0, 1.0: 1.3, 10.0: 11.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, True, True, False, Liquid.DMSO, False, True)] = \
LowVolume_96COREHead1000ul_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.3, 0.0: 0.0, 1.0: 0.8, 10.0: 10.6},
  aspiration_flow_rate=25.0,
  aspiration_mix_flow_rate=25.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=35.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=35.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=25.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, True, True, False, Liquid.WATER, False, True)] = \
LowVolume_96COREHead1000ul_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.8, 0.0: 0.0, 1.0: 1.0, 10.0: 11.2},
  aspiration_flow_rate=25.0,
  aspiration_mix_flow_rate=25.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=35.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=35.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=25.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, True, True, False, Liquid.DMSO, False, True)] = \
LowVolume_96COREHead_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.1, 0.0: 0.0, 1.0: 0.8, 10.0: 10.3},
  aspiration_flow_rate=25.0,
  aspiration_mix_flow_rate=25.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=35.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=35.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=25.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, True, True, False, Liquid.DMSO, False, False)] = \
LowVolume_96COREHead_DMSO_DispenseSurface_Part = HamiltonLiquidClass(
  curve={5.0: 5.9, 0.0: 0.0, 1.0: 1.5, 10.0: 11.0},
  aspiration_flow_rate=25.0,
  aspiration_mix_flow_rate=25.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=35.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=35.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=25.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, True, True, False, Liquid.WATER, False, True)] = \
LowVolume_96COREHead_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.8, 0.0: 0.0, 1.0: 1.3, 10.0: 11.1},
  aspiration_flow_rate=25.0,
  aspiration_mix_flow_rate=25.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=35.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=35.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=25.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, True, True, False, Liquid.WATER, False, False)] = \
LowVolume_96COREHead_Water_DispenseSurface_Part = HamiltonLiquidClass(
  curve={5.0: 5.7, 0.0: 0.0, 1.0: 1.4, 10.0: 10.8},
  aspiration_flow_rate=25.0,
  aspiration_mix_flow_rate=25.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=35.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=25.0,
  dispense_stop_back_volume=0.0
)


# Liquid class for wash low volume tips with CO-RE 96 Head in CO-RE 96 Head Washer.
star_mapping[(10, True, True, False, Liquid.WATER, False, False)] = \
LowVolume_Core96Washer_DispenseSurface = HamiltonLiquidClass(
  curve={5.0: 6.0, 0.5: 0.8, 0.0: 0.0, 1.0: 1.4, 10.0: 15.0, 2.0: 2.6},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=150.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=150.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=5.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=56.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 75
star_mapping[(10, False, True, False, Liquid.DMSO, False, False)] = \
LowVolume_DMSO_DispenseSurface = HamiltonLiquidClass(
  curve={5.0: 5.9, 15.0: 16.4, 0.5: 0.8, 0.0: 0.0, 1.0: 1.4, 10.0: 11.2, 2.0: 2.6},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=56.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, False, Liquid.DMSO, False, True)] = \
LowVolume_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.9, 0.5: 0.8, 0.0: 0.0, 1.0: 1.4, 10.0: 11.2, 2.0: 2.6},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, False, Liquid.DMSO, False, False)] = \
LowVolume_DMSO_DispenseSurface_Part = HamiltonLiquidClass(
  curve={5.0: 5.9, 0.0: 0.0, 1.0: 1.4, 10.0: 11.2, 2.0: 2.6},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 75
star_mapping[(10, False, True, False, Liquid.ETHANOL, False, False)] = \
LowVolume_EtOH_DispenseSurface = HamiltonLiquidClass(
  curve={5.0: 8.4, 0.5: 1.9, 0.0: 0.0, 1.0: 2.7, 10.0: 13.0, 2.0: 4.1},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=3.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, False, Liquid.ETHANOL, False, True)] = \
LowVolume_EtOH_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 7.3, 0.0: 0.0, 1.0: 2.4, 10.0: 13.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=3.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, False, Liquid.ETHANOL, False, False)] = \
LowVolume_EtOH_DispenseSurface_Part = HamiltonLiquidClass(
  curve={5.0: 7.0, 0.0: 0.0, 1.0: 2.4, 10.0: 13.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 10
star_mapping[(10, False, True, False, Liquid.GLYCERIN, False, False)] = \
LowVolume_Glycerin_DispenseSurface = HamiltonLiquidClass(
  curve={5.0: 6.5, 15.0: 17.0, 0.5: 1.4, 0.0: 0.0, 1.0: 2.0, 10.0: 11.8, 2.0: 3.2},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=10.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=10.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=10.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=5.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=2.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 75
star_mapping[(10, False, True, False, Liquid.WATER, False, False)] = \
LowVolume_Water_DispenseSurface = HamiltonLiquidClass(
  curve={5.0: 6.0, 15.0: 16.7, 0.5: 0.8, 0.0: 0.0, 1.0: 1.4, 10.0: 11.5, 2.0: 2.6},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=56.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, True, True, False, Liquid.WATER, False, False)] = \
LowVolume_Water_DispenseSurface96Head = HamiltonLiquidClass(
  curve={5.0: 6.0, 0.0: 0.0, 1.0: 1.0, 10.0: 11.5},
  aspiration_flow_rate=25.0,
  aspiration_mix_flow_rate=25.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=35.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=35.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=25.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, True, True, False, Liquid.WATER, False, True)] = \
LowVolume_Water_DispenseSurfaceEmpty96Head = HamiltonLiquidClass(
  curve={5.0: 6.0, 0.0: 0.0, 1.0: 1.0, 10.0: 10.9},
  aspiration_flow_rate=25.0,
  aspiration_mix_flow_rate=25.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=35.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=35.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=25.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, True, True, False, Liquid.WATER, False, False)] = \
LowVolume_Water_DispenseSurfacePart96Head = HamiltonLiquidClass(
  curve={5.0: 6.0, 0.0: 0.0, 1.0: 1.0, 10.0: 10.9},
  aspiration_flow_rate=25.0,
  aspiration_mix_flow_rate=25.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=35.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=25.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, False, Liquid.WATER, False, True)] = \
LowVolume_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 6.0, 0.5: 0.8, 0.0: 0.0, 1.0: 1.4, 10.0: 11.5, 2.0: 2.6},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(10, False, True, False, Liquid.WATER, False, False)] = \
LowVolume_Water_DispenseSurface_Part = HamiltonLiquidClass(
  curve={5.0: 5.9, 0.0: 0.0, 1.0: 1.2, 10.0: 11.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


# Under laboratory conditions:
#
# Settings for aliquots:
#
# Prealiquot:     Postaliquot:     Aliquots:
# 20ul               20ul                 13 x 20ul
# 50ul               50ul                 4 x 50ul
# 50ul               50ul                 2 x 100 ul
#
# 12 x 20ul =   approximately 19.2 ul
# 4 x 50 ul =    approximately 48.1 ul
# 2 x 100 ul =  approximately 95.3 ul
star_mapping[(300, True, True, True, Liquid.DMSO, True, False)] = \
SlimTipFilter_96COREHead1000ul_DMSO_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 50.0: 50.0, 30.0: 30.0, 0.0: 0.0, 20.0: 20.0},
  aspiration_flow_rate=200.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=18.0
)


star_mapping[(300, True, True, True, Liquid.DMSO, True, True)] = \
SlimTipFilter_96COREHead1000ul_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 312.3, 50.0: 55.3, 0.0: 0.0, 100.0: 107.7, 20.0: 22.4, 200.0: 210.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=20.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=20.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.DMSO, False, True)] = \
SlimTipFilter_96COREHead1000ul_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 311.9, 50.0: 54.1, 0.0: 0.0, 100.0: 107.5, 20.0: 22.5, 10.0: 11.1, 200.0: 209.4},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=1.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# Under laboratory conditions:
#
# Settings for aliquots:
#
# Prealiquot:     Postaliquot:     Aliquots:
# 20ul               20ul                 13 x 20ul
# 50ul               50ul                 4 x 50ul
# 50ul               50ul                 2 x 100 ul
#
# 12 x 20ul =   approximately 19.6 ul
# 4 x 50 ul =    approximately 48.9 ul
# 2 x 100 ul =  approximately 97.2 ul
#
star_mapping[(300, True, True, True, Liquid.WATER, True, False)] = \
SlimTipFilter_96COREHead1000ul_Water_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 50.0: 50.0, 30.0: 30.0, 0.0: 0.0, 100.0: 100.0, 20.0: 20.0},
  aspiration_flow_rate=200.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(300, True, True, True, Liquid.WATER, True, True)] = \
SlimTipFilter_96COREHead1000ul_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 317.0, 50.0: 55.8, 0.0: 0.0, 100.0: 109.4, 20.0: 22.7, 200.0: 213.7},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=20.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=230.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=20.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.WATER, False, True)] = \
SlimTipFilter_96COREHead1000ul_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 318.7, 50.0: 54.9, 0.0: 0.0, 100.0: 110.4, 10.0: 11.7, 200.0: 210.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# Under laboratory conditions:
#
# Settings for aliquots:
#
# Prealiquot:     Postaliquot:     Aliquots:
# 20ul               20ul                 13 x 20ul
# 50ul               50ul                 4 x 50ul
# 50ul               50ul                 2 x 100 ul
#
# 12 x 20ul =   approximately 19.1 ul
# 4 x 50 ul =    approximately 48.3 ul
# 2 x 100 ul =  approximately 95.7 ul
#
star_mapping[(300, True, True, True, Liquid.DMSO, True, False)] = \
SlimTipFilter_DMSO_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 50.0: 50.0, 30.0: 30.0, 0.0: 0.0, 20.0: 20.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=18.0
)


star_mapping[(300, True, True, True, Liquid.DMSO, True, True)] = \
SlimTipFilter_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 309.5, 50.0: 54.4, 0.0: 0.0, 100.0: 106.4, 20.0: 22.1, 200.0: 208.2},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.DMSO, False, True)] = \
SlimTipFilter_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 309.7, 5.0: 5.6, 50.0: 53.8, 0.0: 0.0, 100.0: 105.4, 20.0: 22.2, 10.0: 11.3, 200.0: 207.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=1.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# Under laboratory conditions:
#
# Settings for aliquots:
#
# Prealiquot:     Postaliquot:     Aliquots:
# 20ul               20ul                 12 x 20ul
# 50ul               50ul                 4 x 50ul
# 50ul               50ul                 2 x 100 ul
#
# 12 x 20ul =   approximately 21.3 ul
# 4 x 50 ul =    approximately 54.3 ul
# 2 x 100 ul =  approximately 105.2 ul
star_mapping[(300, True, True, True, Liquid.ETHANOL, True, False)] = \
SlimTipFilter_EtOH_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 50.0: 50.0, 0.0: 0.0, 100.0: 100.0, 20.0: 20.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(300, True, True, True, Liquid.ETHANOL, True, True)] = \
SlimTipFilter_EtOH_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 320.4, 50.0: 57.2, 0.0: 0.0, 100.0: 110.5, 20.0: 24.5, 200.0: 215.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.ETHANOL, False, True)] = \
SlimTipFilter_EtOH_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 313.9, 50.0: 55.4, 0.0: 0.0, 100.0: 107.7, 20.0: 23.2, 10.0: 12.4, 200.0: 210.6},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=2.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=2.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.GLYCERIN80, False, True)] = \
SlimTipFilter_Glycerin_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 312.0, 50.0: 55.0, 0.0: 0.0, 100.0: 107.8, 20.0: 22.9, 10.0: 11.8, 200.0: 210.0},
  aspiration_flow_rate=30.0,
  aspiration_mix_flow_rate=30.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=30.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=30.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=2.0,
  dispense_stop_back_volume=0.0
)


# Under laboratory conditions:
#
# Settings for aliquots:
#
# Prealiquot:     Postaliquot:     Aliquots:
# 20ul               20ul                 13 x 20ul
# 50ul               50ul                 4 x 50ul
# 50ul               50ul                 2 x 100 ul
#
# 12 x 20ul =   approximately 19.6 ul
# 4 x 50 ul =    approximately 49.2 ul
# 2 x 100 ul =  approximately 97.5 ul
star_mapping[(300, True, True, True, Liquid.WATER, True, False)] = \
SlimTipFilter_Water_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 50.0: 50.0, 30.0: 30.0, 0.0: 0.0, 100.0: 100.0, 20.0: 20.0},
  aspiration_flow_rate=200.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=3.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=3.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(300, True, True, True, Liquid.WATER, True, True)] = \
SlimTipFilter_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 317.2, 50.0: 55.6, 0.0: 0.0, 100.0: 108.6, 20.0: 22.6, 200.0: 212.8},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.WATER, False, True)] = \
SlimTipFilter_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 314.1, 5.0: 6.2, 50.0: 54.7, 0.0: 0.0, 100.0: 108.0, 20.0: 22.7, 10.0: 11.9, 200.0: 211.3},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# Under laboratory conditions:
#
# Settings for aliquots:
#
# Prealiquot:     Postaliquot:     Aliquots:
# 20ul               20ul                 13 x 20ul
# 50ul               50ul                 4 x 50ul
# 50ul               50ul                 2 x 100 ul
#
# 12 x 20ul =   approximately 19.2 ul
# 4 x 50 ul =    approximately 48.1 ul
# 2 x 100 ul =  approximately 95.3 ul
star_mapping[(300, True, True, False, Liquid.DMSO, True, False)] = \
SlimTip_96COREHead1000ul_DMSO_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 50.0: 50.0, 30.0: 30.0, 0.0: 0.0, 20.0: 20.0},
  aspiration_flow_rate=200.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=18.0
)


star_mapping[(300, True, True, False, Liquid.DMSO, True, True)] = \
SlimTip_96COREHead1000ul_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 313.8, 50.0: 55.8, 0.0: 0.0, 100.0: 109.2, 20.0: 23.1, 200.0: 212.7},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.DMSO, False, True)] = \
SlimTip_96COREHead1000ul_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 312.9, 50.0: 54.1, 0.0: 0.0, 20.0: 22.5, 100.0: 108.8, 200.0: 210.9, 10.0: 11.1},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=1.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# Under laboratory conditions:
#
# Settings for aliquots:
#
# Prealiquot:     Postaliquot:     Aliquots:
# 20ul               20ul                 10 x 20ul
# 50ul               50ul                 4 x 50ul
# 50ul               50ul                 2 x 100 ul
#
# 12 x 20ul =   approximately 21.8 ul
# 4 x 50 ul =    approximately 53.6 ul
# 2 x 100 ul =  approximately 105.2 ul
star_mapping[(300, True, True, False, Liquid.ETHANOL, True, False)] = \
SlimTip_96COREHead1000ul_EtOH_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 50.0: 50.0, 0.0: 0.0, 100.0: 100.0, 20.0: 20.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=80.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(300, True, True, False, Liquid.ETHANOL, True, True)] = \
SlimTip_96COREHead1000ul_EtOH_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 326.2, 50.0: 58.8, 0.0: 0.0, 100.0: 112.7, 20.0: 25.0, 200.0: 218.2},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.ETHANOL, False, True)] = \
SlimTip_96COREHead1000ul_EtOH_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 320.3, 50.0: 56.7, 0.0: 0.0, 100.0: 109.5, 10.0: 12.4, 200.0: 213.9},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=2.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=150.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=2.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.GLYCERIN80, False, True)] = \
SlimTip_96COREHead1000ul_Glycerin80_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 319.3, 50.0: 58.2, 0.0: 0.0, 100.0: 112.1, 20.0: 23.9, 10.0: 12.1, 200.0: 216.9},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=50.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=50.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=2.0,
  dispense_stop_back_volume=0.0
)


# Under laboratory conditions:
#
# Settings for aliquots:
#
# Prealiquot:     Postaliquot:     Aliquots:
# 20ul               20ul                 13 x 20ul
# 50ul               50ul                 4 x 50ul
# 50ul               50ul                 2 x 100 ul
#
# 12 x 20ul =   approximately 19.6 ul
# 4 x 50 ul =    approximately 48.9 ul
# 2 x 100 ul =  approximately 97.2 ul
#
star_mapping[(300, True, True, False, Liquid.WATER, True, False)] = \
SlimTip_96COREHead1000ul_Water_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 50.0: 50.0, 30.0: 30.0, 0.0: 0.0, 100.0: 100.0, 20.0: 20.0},
  aspiration_flow_rate=200.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(300, True, True, False, Liquid.WATER, True, True)] = \
SlimTip_96COREHead1000ul_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 315.0, 50.0: 55.5, 0.0: 0.0, 100.0: 107.2, 20.0: 22.8, 200.0: 211.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.WATER, False, True)] = \
SlimTip_96COREHead1000ul_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 322.7, 50.0: 56.4, 0.0: 0.0, 100.0: 110.4, 10.0: 11.9, 200.0: 215.5},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# Under laboratory conditions:
#
# Settings for aliquots:
#
# Prealiquot:     Postaliquot:     Aliquots:
# 20ul               20ul                 13 x 20ul
# 50ul               50ul                 4 x 50ul
# 50ul               50ul                 2 x 100 ul
#
# 12 x 20ul =   approximately 19.1 ul
# 4 x 50 ul =    approximately 48.3 ul
# 2 x 100 ul =  approximately 95.7 ul
#
star_mapping[(300, True, True, False, Liquid.DMSO, True, False)] = \
SlimTip_DMSO_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 50.0: 50.0, 30.0: 30.0, 0.0: 0.0, 20.0: 20.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=18.0
)


star_mapping[(300, True, True, False, Liquid.DMSO, True, True)] = \
SlimTip_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 309.5, 50.0: 54.7, 0.0: 0.0, 100.0: 107.2, 20.0: 22.5, 200.0: 209.7},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.DMSO, False, True)] = \
SlimTip_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 310.2, 5.0: 5.6, 50.0: 54.1, 0.0: 0.0, 100.0: 106.2, 20.0: 22.5, 10.0: 11.3, 200.0: 208.7},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=1.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# Under laboratory conditions:
#
# Settings for aliquots:
#
# Prealiquot:     Postaliquot:     Aliquots:
# 20ul               20ul                 12 x 20ul
# 50ul               50ul                 4 x 50ul
# 50ul               50ul                 2 x 100 ul
#
# 12 x 20ul =   approximately 21.3 ul
# 4 x 50 ul =    approximately 54.3 ul
# 2 x 100 ul =  approximately 105.2 ul
star_mapping[(300, True, True, False, Liquid.ETHANOL, True, False)] = \
SlimTip_EtOH_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 50.0: 50.0, 0.0: 0.0, 100.0: 100.0, 20.0: 20.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(300, True, True, False, Liquid.ETHANOL, True, True)] = \
SlimTip_EtOH_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 323.4, 50.0: 57.2, 0.0: 0.0, 100.0: 110.5, 20.0: 24.7, 200.0: 211.9},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.ETHANOL, False, True)] = \
SlimTip_EtOH_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 312.9, 5.0: 6.2, 50.0: 55.4, 0.0: 0.0, 100.0: 107.7, 20.0: 23.2, 10.0: 11.9, 200.0: 210.6},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=2.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=2.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.GLYCERIN80, False, True)] = \
SlimTip_Glycerin_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 313.3, 5.0: 6.0, 50.0: 55.7, 0.0: 0.0, 100.0: 107.8, 20.0: 22.9, 10.0: 11.5, 200.0: 210.0},
  aspiration_flow_rate=30.0,
  aspiration_mix_flow_rate=30.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=30.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=30.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=2.0,
  dispense_stop_back_volume=0.0
)


# Under laboratory conditions:
#
# Settings for aliquots:
#
# Prealiquot:     Postaliquot:     Aliquots:
# 20ul               20ul                 13 x 20ul
# 50ul               50ul                 4 x 50ul
# 50ul               50ul                 2 x 100 ul
#
# 12 x 20ul =   approximately 19.6 ul
# 4 x 50 ul =    approximately 50.0 ul
# 2 x 100 ul =  approximately 98.4 ul
star_mapping[(300, True, True, False, Liquid.SERUM, True, False)] = \
SlimTip_Serum_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 50.0: 50.0, 30.0: 30.0, 0.0: 0.0, 20.0: 20.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=3.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(300, True, True, False, Liquid.SERUM, True, True)] = \
SlimTip_Serum_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 321.5, 50.0: 56.0, 0.0: 0.0, 100.0: 109.7, 20.0: 22.8, 200.0: 215.7},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.SERUM, False, True)] = \
SlimTip_Serum_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 320.2, 5.0: 5.5, 50.0: 55.4, 0.0: 0.0, 20.0: 22.6, 100.0: 109.7, 200.0: 214.9, 10.0: 11.3},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=1.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=1.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# Under laboratory conditions:
#
# Settings for aliquots:
#
# Prealiquot:     Postaliquot:     Aliquots:
# 20ul               20ul                 13 x 20ul
# 50ul               50ul                 4 x 50ul
# 50ul               50ul                 2 x 100 ul
#
# 12 x 20ul =   approximately 19.6 ul
# 4 x 50 ul =    approximately 49.2 ul
# 2 x 100 ul =  approximately 97.5 ul
star_mapping[(300, True, True, False, Liquid.WATER, True, False)] = \
SlimTip_Water_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 50.0: 50.0, 30.0: 30.0, 0.0: 0.0, 100.0: 100.0, 20.0: 20.0},
  aspiration_flow_rate=200.0,
  aspiration_mix_flow_rate=200.0,
  aspiration_air_transport_volume=3.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=3.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(300, True, True, False, Liquid.WATER, True, True)] = \
SlimTip_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 317.2, 50.0: 55.6, 0.0: 0.0, 20.0: 22.6, 100.0: 108.6, 200.0: 212.8},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.WATER, False, True)] = \
SlimTip_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 317.1, 5.0: 6.2, 50.0: 55.1, 0.0: 0.0, 100.0: 108.0, 20.0: 22.9, 10.0: 11.9, 200.0: 213.0},
  aspiration_flow_rate=250.0,
  aspiration_mix_flow_rate=250.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=200.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 80
# V1.2: Stop back volume = 0 (previous value: 15)
star_mapping[(300, False, False, False, Liquid.WATER, True, False)] = \
StandardNeedle_Water_DispenseJet = HamiltonLiquidClass(
  curve={300.0: 311.2, 50.0: 51.3, 0.0: 0.0, 100.0: 103.4, 20.0: 19.5},
  aspiration_flow_rate=80.0,
  aspiration_mix_flow_rate=80.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=80.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, False, False, Liquid.WATER, True, True)] = \
StandardNeedle_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 311.2, 50.0: 51.3, 0.0: 0.0, 100.0: 103.4, 20.0: 19.5},
  aspiration_flow_rate=80.0,
  aspiration_mix_flow_rate=80.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, False, False, Liquid.WATER, True, False)] = \
StandardNeedle_Water_DispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 311.2, 50.0: 51.3, 0.0: 0.0, 100.0: 103.4, 20.0: 19.5},
  aspiration_flow_rate=80.0,
  aspiration_mix_flow_rate=80.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, False, False, Liquid.WATER, False, False)] = \
StandardNeedle_Water_DispenseSurface = HamiltonLiquidClass(
  curve={300.0: 308.4, 5.0: 6.5, 50.0: 52.3, 0.0: 0.0, 100.0: 102.9, 20.0: 22.3, 1.0: 1.1, 200.0: 205.8, 10.0: 12.0, 2.0: 2.1},
  aspiration_flow_rate=80.0,
  aspiration_mix_flow_rate=80.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=80.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, False, False, Liquid.WATER, False, True)] = \
StandardNeedle_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 308.4, 5.0: 6.5, 50.0: 52.3, 0.0: 0.0, 100.0: 102.9, 20.0: 22.3, 1.0: 1.1, 200.0: 205.8, 10.0: 12.0, 2.0: 2.1},
  aspiration_flow_rate=80.0,
  aspiration_mix_flow_rate=80.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=80.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, False, False, Liquid.WATER, False, False)] = \
StandardNeedle_Water_DispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 308.4, 5.0: 6.5, 50.0: 52.3, 0.0: 0.0, 100.0: 102.9, 20.0: 22.3, 1.0: 1.1, 200.0: 205.8, 10.0: 12.0, 2.0: 2.1},
  aspiration_flow_rate=80.0,
  aspiration_mix_flow_rate=80.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# - set Air transport volume to 25ul
# - set Correction 200.0, from 220.0 back to 217.0 (V 1.0)
#
# - submerge depth: Asp.  1mm
# - without pre-rinsing
# - dispense mode jet empty tip
#
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       0.50                   2.26
#       50                       0.30                   0.65
#     100                       0.22                   1.15
#     200                       0.16                   0.55
#     300                       0.17                   0.35
#
star_mapping[(300, False, True, False, Liquid.ACETONITRILE, True, False)] = \
StandardVolumeAcetonitrilDispenseJet = HamiltonLiquidClass(
  curve={300.0: 326.2, 50.0: 57.3, 0.0: 0.0, 100.0: 111.5, 20.0: 24.6, 200.0: 217.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=25.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.ACETONITRILE, True, True)] = \
StandardVolumeAcetonitrilDispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 326.2, 50.0: 57.3, 0.0: 0.0, 100.0: 111.5, 20.0: 24.6, 200.0: 217.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=25.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.ACETONITRILE, True, False)] = \
StandardVolumeAcetonitrilDispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 321.2, 50.0: 57.3, 0.0: 0.0, 100.0: 110.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=10.0
)


# - submerge depth: Asp.  2mm
#                              Disp. 2mm
# - without pre-rinsing
# - dispense mode surface empty tip
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         1                     11.17                 - 6.64
#         2                       4.50                   1.95
#         5                       0.38                   0.50
#       10                       0.94                   0.73
#       20                       0.63                   0.73
#       50                       0.39                   1.28
#     100                       0.28                   0.94
#     200                       0.65                   0.65
#     300                       0.21                   0.88
#
star_mapping[(300, False, True, False, Liquid.ACETONITRILE, False, False)] = \
StandardVolumeAcetonitrilDispenseSurface = HamiltonLiquidClass(
  curve={300.0: 328.0, 5.0: 6.8, 50.0: 58.5, 0.0: 0.0, 100.0: 112.7, 20.0: 24.8, 1.0: 1.3, 200.0: 220.0, 10.0: 13.0, 2.0: 3.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=1.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.ACETONITRILE, False, True)] = \
StandardVolumeAcetonitrilDispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 328.0, 5.0: 6.8, 50.0: 58.5, 0.0: 0.0, 100.0: 112.7, 20.0: 24.8, 1.0: 1.3, 200.0: 220.0, 10.0: 13.0, 2.0: 3.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=1.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.ACETONITRILE, False, False)] = \
StandardVolumeAcetonitrilDispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 328.0, 5.0: 7.3, 0.0: 0.0, 100.0: 112.7, 10.0: 13.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=1.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=20.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# -  ohne vorbenetzen, gleicher Tip
# -  Aspiration submerge depth  1.0mm
# -  Prealiquot equal to Aliquotvolume,  jet mode part volume
# -  Aliquot, jet mode part volume
# -  Postaliquot equal to Aliquotvolume,  jet mode empty tip
#
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl                     Precision %        Trueness %
#       20  (12 Aliquots)          2.53                 -2.97
#       50  (  4 Aliquots)          0.84                 -2.57
#
star_mapping[(300, False, True, False, Liquid.DMSO, True, False)] = \
StandardVolumeDMSOAliquotJet = HamiltonLiquidClass(
  curve={350.0: 350.0, 30.0: 30.0, 0.0: 0.0, 20.0: 20.0, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=0.3,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=10.0
)


# - Volume 5 - 300ul
# - submerge depth: Asp.  0.5mm
#                              Disp. 0.5mm
# - pre-rinsing 3x  with Aspiratevolume, ( >100ul perhaps 2x or set mix speed to 100ul/s)
# - dispense mode surface empty tip
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       3.51                   3.16
#       50                       1.19                   1.09
#     100                       0.76                   0.42
#     200                       0.53                   0.08
#     300                       0.54                   0.22
#
star_mapping[(300, False, True, False, Liquid.ETHANOL, False, False)] = \
StandardVolumeEtOHDispenseSurface = HamiltonLiquidClass(
  curve={300.0: 309.2, 50.0: 54.8, 0.0: 0.0, 100.0: 106.5, 20.0: 23.7, 200.0: 208.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=3.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=0.4,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.ETHANOL, False, True)] = \
StandardVolumeEtOHDispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 309.2, 50.0: 54.8, 0.0: 0.0, 100.0: 106.5, 20.0: 23.7, 200.0: 208.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=3.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.ETHANOL, False, False)] = \
StandardVolumeEtOHDispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 315.2, 0.0: 0.0, 100.0: 108.5, 20.0: 23.7},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=3.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.DMSO, True, True)] = \
StandardVolumeFilter_96COREHead1000ul_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 302.5, 0.0: 0.0, 100.0: 101.0, 20.0: 20.4, 200.0: 201.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.DMSO, False, True)] = \
StandardVolumeFilter_96COREHead1000ul_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 306.0, 0.0: 0.0, 100.0: 104.3, 200.0: 205.0, 10.0: 12.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.WATER, True, True)] = \
StandardVolumeFilter_96COREHead1000ul_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 313.5, 0.0: 0.0, 100.0: 107.2, 20.0: 23.2, 200.0: 211.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.WATER, False, True)] = \
StandardVolumeFilter_96COREHead1000ul_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 313.5, 0.0: 0.0, 100.0: 107.2, 200.0: 210.0, 10.0: 11.9},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.DMSO, True, True)] = \
StandardVolumeFilter_96COREHead_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 303.5, 0.0: 0.0, 100.0: 101.8, 10.0: 10.2, 200.0: 200.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.DMSO, True, False)] = \
StandardVolumeFilter_96COREHead_DMSO_DispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 305.0, 0.0: 0.0, 100.0: 103.6, 10.0: 11.5, 200.0: 206.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(300, True, True, True, Liquid.DMSO, False, True)] = \
StandardVolumeFilter_96COREHead_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 303.0, 0.0: 0.0, 100.0: 101.3, 10.0: 10.6, 200.0: 202.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.DMSO, False, False)] = \
StandardVolumeFilter_96COREHead_DMSO_DispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 303.0, 0.0: 0.0, 100.0: 101.3, 10.0: 10.1, 200.0: 202.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.WATER, True, True)] = \
StandardVolumeFilter_96COREHead_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 309.0, 0.0: 0.0, 20.0: 22.3, 100.0: 104.2, 10.0: 11.9, 200.0: 207.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.WATER, True, False)] = \
StandardVolumeFilter_96COREHead_Water_DispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 309.0, 0.0: 0.0, 20.0: 22.3, 100.0: 104.2, 10.0: 11.9, 200.0: 207.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(300, True, True, True, Liquid.WATER, False, True)] = \
StandardVolumeFilter_96COREHead_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 306.3, 0.0: 0.0, 100.0: 104.5, 10.0: 11.9, 200.0: 205.7},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, True, Liquid.WATER, False, False)] = \
StandardVolumeFilter_96COREHead_Water_DispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 304.0, 0.0: 0.0, 100.0: 105.3, 10.0: 11.9, 200.0: 205.7},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# -  ohne vorbenetzen, gleicher Tip
# -  Aspiration submerge depth  1.0mm
# -  Prealiquot equal to Aliquotvolume,  jet mode part volume
# -  Aliquot, jet mode part volume
# -  Postaliquot equal to Aliquotvolume,  jet mode empty tip
#
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl                     Precision %        Trueness %
#       20  (12 Aliquots)          2.53                 -2.97
#       50  (  4 Aliquots)          0.84                 -2.57
#
star_mapping[(300, False, True, True, Liquid.DMSO, True, False)] = \
StandardVolumeFilter_DMSO_AliquotDispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 300.0, 30.0: 30.0, 0.0: 0.0, 20.0: 20.0, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, True, Liquid.DMSO, True, False)] = \
StandardVolumeFilter_DMSO_DispenseJet = HamiltonLiquidClass(
  curve={300.0: 304.6, 50.0: 51.1, 0.0: 0.0, 20.0: 20.7, 100.0: 101.8, 200.0: 203.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, True, Liquid.DMSO, True, True)] = \
StandardVolumeFilter_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 304.6, 50.0: 51.1, 0.0: 0.0, 100.0: 101.8, 20.0: 20.7, 200.0: 203.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, True, Liquid.DMSO, True, False)] = \
StandardVolumeFilter_DMSO_DispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 315.6, 0.0: 0.0, 100.0: 112.8, 20.0: 29.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(300, False, True, True, Liquid.DMSO, False, False)] = \
StandardVolumeFilter_DMSO_DispenseSurface = HamiltonLiquidClass(
  curve={300.0: 308.8, 5.0: 6.6, 50.0: 52.9, 0.0: 0.0, 1.0: 1.8, 20.0: 22.1, 100.0: 103.8, 2.0: 3.0, 10.0: 11.9, 200.0: 205.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, True, Liquid.DMSO, False, True)] = \
StandardVolumeFilter_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 308.8, 5.0: 6.6, 50.0: 52.9, 0.0: 0.0, 1.0: 1.8, 20.0: 22.1, 100.0: 103.8, 2.0: 3.0, 10.0: 11.9, 200.0: 205.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, True, Liquid.DMSO, False, False)] = \
StandardVolumeFilter_DMSO_DispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 306.8, 5.0: 6.4, 50.0: 52.9, 0.0: 0.0, 100.0: 103.8, 20.0: 22.1, 10.0: 11.9},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 100,  Stop back volume=0
star_mapping[(300, False, True, True, Liquid.ETHANOL, True, False)] = \
StandardVolumeFilter_EtOH_DispenseJet = HamiltonLiquidClass(
  curve={300.0: 310.2, 50.0: 55.8, 0.0: 0.0, 20.0: 24.6, 100.0: 107.5, 200.0: 209.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, True, Liquid.ETHANOL, True, True)] = \
StandardVolumeFilter_EtOH_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 310.2, 50.0: 55.8, 0.0: 0.0, 100.0: 107.5, 20.0: 24.6, 200.0: 209.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, True, Liquid.ETHANOL, True, False)] = \
StandardVolumeFilter_EtOH_DispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 317.2, 0.0: 0.0, 100.0: 110.5, 20.0: 25.6},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=5.0
)


# V1.1: Set mix flow rate to 20, dispense settling time=0, Stop back volume=0
star_mapping[(300, False, True, True, Liquid.GLYCERIN, True, False)] = \
StandardVolumeFilter_Glycerin_DispenseJet = HamiltonLiquidClass(
  curve={300.0: 309.0, 50.0: 53.6, 0.0: 0.0, 20.0: 22.3, 100.0: 104.9, 200.0: 207.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=20.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=20.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=20.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 20, dispense settling time=0, Stop back volume=0
star_mapping[(300, False, True, True, Liquid.GLYCERIN80, True, True)] = \
StandardVolumeFilter_Glycerin_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 309.0, 50.0: 53.6, 0.0: 0.0, 100.0: 104.9, 20.0: 22.3, 200.0: 207.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=20.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=20.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 10
star_mapping[(300, False, True, True, Liquid.GLYCERIN, False, False)] = \
StandardVolumeFilter_Glycerin_DispenseSurface = HamiltonLiquidClass(
  curve={300.0: 307.9, 5.0: 6.5, 50.0: 53.6, 0.0: 0.0, 20.0: 22.5, 100.0: 105.7, 2.0: 3.2, 10.0: 12.0, 200.0: 207.0},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=10.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=10.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=10.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=2.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, True, Liquid.GLYCERIN80, False, True)] = \
StandardVolumeFilter_Glycerin_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 307.9, 5.0: 6.5, 50.0: 53.6, 0.0: 0.0, 100.0: 105.7, 20.0: 22.5, 200.0: 207.0, 10.0: 12.0, 2.0: 3.2},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=10.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=10.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=10.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=2.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, True, Liquid.GLYCERIN80, False, False)] = \
StandardVolumeFilter_Glycerin_DispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 307.9, 5.0: 6.1, 0.0: 0.0, 100.0: 104.7, 200.0: 207.0, 10.0: 11.5},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=10.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=10.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=2.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, True, Liquid.SERUM, True, False)] = \
StandardVolumeFilter_Serum_AliquotDispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 300.0, 30.0: 30.0, 0.0: 0.0, 20.0: 20.0, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, True, Liquid.SERUM, True, False)] = \
StandardVolumeFilter_Serum_AliquotJet = HamiltonLiquidClass(
  curve={300.0: 300.0, 0.0: 0.0, 30.0: 30.0, 20.0: 20.0, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, True, Liquid.SERUM, True, False)] = \
StandardVolumeFilter_Serum_DispenseJet = HamiltonLiquidClass(
  curve={300.0: 315.2, 50.0: 55.6, 0.0: 0.0, 20.0: 23.2, 100.0: 108.1, 200.0: 212.1},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, True, Liquid.SERUM, True, True)] = \
StandardVolumeFilter_Serum_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 315.2, 50.0: 55.6, 0.0: 0.0, 100.0: 108.1, 20.0: 23.2, 200.0: 212.1},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, True, Liquid.SERUM, True, False)] = \
StandardVolumeFilter_Serum_DispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 315.2, 0.0: 0.0, 100.0: 111.5, 20.0: 29.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=5.0
)


star_mapping[(300, False, True, True, Liquid.SERUM, False, False)] = \
StandardVolumeFilter_Serum_DispenseSurface = HamiltonLiquidClass(
  curve={300.0: 313.4, 5.0: 6.3, 50.0: 54.9, 0.0: 0.0, 20.0: 23.0, 100.0: 107.1, 2.0: 2.6, 10.0: 12.0, 200.0: 210.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, True, Liquid.SERUM, False, False)] = \
StandardVolumeFilter_Serum_DispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 313.4, 0.0: 0.0, 100.0: 109.1, 10.0: 12.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, True, Liquid.WATER, True, False)] = \
StandardVolumeFilter_Water_AliquotDispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 300.0, 30.0: 30.0, 0.0: 0.0, 20.0: 20.0, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, True, Liquid.WATER, True, False)] = \
StandardVolumeFilter_Water_AliquotJet = HamiltonLiquidClass(
  curve={300.0: 300.0, 0.0: 0.0, 30.0: 30.0, 20.0: 20.0, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=0.3,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, True, Liquid.WATER, True, False)] = \
StandardVolumeFilter_Water_DispenseJet = HamiltonLiquidClass(
  curve={300.0: 313.5, 50.0: 55.1, 0.0: 0.0, 20.0: 23.2, 100.0: 107.2, 200.0: 211.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, True, Liquid.WATER, True, True)] = \
StandardVolumeFilter_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 313.5, 50.0: 55.1, 0.0: 0.0, 100.0: 107.2, 20.0: 23.2, 200.0: 211.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, True, Liquid.WATER, True, False)] = \
StandardVolumeFilter_Water_DispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 313.5, 0.0: 0.0, 100.0: 110.2, 20.0: 27.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, True, Liquid.WATER, False, False)] = \
StandardVolumeFilter_Water_DispenseSurface = HamiltonLiquidClass(
  curve={300.0: 313.5, 5.0: 6.3, 0.5: 0.9, 50.0: 55.1, 0.0: 0.0, 1.0: 1.6, 20.0: 23.2, 100.0: 107.2, 2.0: 2.8, 10.0: 11.9, 200.0: 211.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, True, Liquid.WATER, False, True)] = \
StandardVolumeFilter_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 313.5, 5.0: 6.3, 0.5: 0.9, 50.0: 55.1, 0.0: 0.0, 100.0: 107.2, 20.0: 23.2, 1.0: 1.6, 200.0: 211.0, 10.0: 11.9, 2.0: 2.8},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, True, Liquid.WATER, False, False)] = \
StandardVolumeFilter_Water_DispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 313.5, 5.0: 6.5, 50.0: 55.1, 0.0: 0.0, 20.0: 23.2, 100.0: 107.2, 10.0: 11.9, 200.0: 211.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth Asp. 1mm
# - 3x pre-rinsing with probevolume
#   mix position 0mm (mix flow rate is intentional low)
# - Disp. mode jet empty tip
# - Pipettingvolume jet-dispense from 20µl - 300µl
# - To protect, the distance from Asp. to Disp. should be as short as possible( about 12slot),
#   because MeOH could  drop out in a long way!
# - some droplets on tip after dispense are also with more air transport volume not avoidable
# - sometimes it helpes using Filtertips
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       0.61                   0.57
#       50                       1.21                   0.87
#     100                       0.63                   0.47
#     200                       0.56                   0.07
#     300                       0.54                   1.12
#
star_mapping[(300, False, True, False, Liquid.METHANOL, True, False)] = \
StandardVolumeMeOHDispenseJet = HamiltonLiquidClass(
  curve={300.0: 336.0, 50.0: 63.0, 0.0: 0.0, 100.0: 119.5, 20.0: 28.3, 200.0: 230.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=30.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=0.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=30.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth Asp. 2mm
# - 5x pre-rinsing with probevolume 5-50µl, 3x pre-rinsing with probevolume >100µl,
#   mix position 1mm (mix flow rate is intentional low)
# - Disp. mode jet empty tip
# - Pipettingvolume surface-dispense from 5µl - 300µl
# - To protect, the distance from Asp. to Disp. should be as short as possible( about 12slot),
#   because MeOH could  drop out in a long way!
# - some droplets on tip after dispense are also with more air transport volume not avoidable
# - sometimes it helpes using Filtertips
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         5                     13.22                   5.95
#       10                       2.08                   1.00
#       20                       1.52                   0.58
#       50                       0.63                   0.51
#     100                       0.66                   0.26
#     200                       0.51                   0.59
#     300                       0.81                   0.22
#
star_mapping[(300, False, True, False, Liquid.METHANOL, False, False)] = \
StandardVolumeMeOHDispenseSurface = HamiltonLiquidClass(
  curve={300.0: 310.2, 5.0: 8.0, 50.0: 55.8, 0.0: 0.0, 100.0: 107.5, 20.0: 24.6, 200.0: 209.2, 10.0: 14.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=30.0,
  aspiration_air_transport_volume=10.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=0.1,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=30.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# - use pLLD
# - submerge depth>: Asp. 0.5 mm
#                                Disp. 1.0 mm (surface)
# - without pre-rinsing
# - dispense mode jet empty tip
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#       20                       0.94                   0.94
#       50                       0.74                   1.20
#     100                       1.39                   1.37
#     200                       0.29                   0.17
#     300                       0.16                   0.80
#
star_mapping[(300, False, True, False, Liquid.OCTANOL, True, False)] = \
StandardVolumeOctanol100DispenseJet = HamiltonLiquidClass(
  curve={300.0: 319.3, 50.0: 56.6, 0.0: 0.0, 100.0: 109.9, 20.0: 23.8, 200.0: 216.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.5,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


# - use pLLD
# - submerge depth>: Asp. 0.5 mm
#                                Disp. 1.0 mm (surface)
# - without pre-rinsing
# - dispense mode surface empty tip
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         1                       7.45                   9.13
#         2                       3.99                   1.51
#         5                       1.95                   1.64
#       10                       0.51                   3.81
#       20                       0.34                 - 3.95
#       50                       2.74                   1.38
#     100                       0.29                   1.04
#     200                       0.02                   0.12
#     300                       0.11                   0.29
#
star_mapping[(300, False, True, False, Liquid.OCTANOL, False, False)] = \
StandardVolumeOctanol100DispenseSurface = HamiltonLiquidClass(
  curve={300.0: 315.0, 5.0: 6.6, 50.0: 55.9, 0.0: 0.0, 100.0: 106.8, 20.0: 22.1, 1.0: 0.8, 200.0: 212.0, 10.0: 12.6, 2.0: 3.7},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.5,
  aspiration_over_aspirate_volume=1.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp.  2mm
#                              Disp. 2mm
# - without pre-rinsing
# - dispense mode surface empty tip
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl            Precision %        Trueness %
#         1                       4.67                   0.55
#         5                       3.98                   2.77
#       10                       1.99                   4.39
#
#
star_mapping[(300, False, True, False, Liquid.PBS_BUFFER, False, False)] = \
StandardVolumePBSDispenseSurface = HamiltonLiquidClass(
  curve={300.0: 313.5, 5.0: 7.5, 50.0: 55.1, 0.0: 0.0, 100.0: 107.2, 20.0: 23.2, 1.0: 2.6, 200.0: 211.0, 10.0: 12.8},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=5.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=5.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# - submerge depth: Asp. 0.5 mm
# - without pre-rinsing
# - dispense mode jet empty tip
#
#
#
#
# LC-Plasma is a copy from Serumclass, Plasma has the same Parameters and Correctioncurve!
#
# Typical performance data under laboratory conditions:
# (2 Volumes measured as control)
#
# Volume µl            Precision %        Trueness %
#     100                       0.08                   1.09
#     200                       0.09                   0.91
#
star_mapping[(300, False, True, False, Liquid.PLASMA, True, False)] = \
StandardVolumePlasmaDispenseJet = HamiltonLiquidClass(
  curve={300.0: 315.2, 50.0: 55.6, 0.0: 0.0, 100.0: 108.1, 20.0: 23.2, 200.0: 212.1, 10.0: 12.3},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.PLASMA, True, True)] = \
StandardVolumePlasmaDispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 315.2, 50.0: 55.6, 0.0: 0.0, 20.0: 23.2, 100.0: 108.1, 200.0: 212.1},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.PLASMA, True, False)] = \
StandardVolumePlasmaDispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 315.2, 0.0: 0.0, 20.0: 29.2, 100.0: 111.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=5.0
)


# - submerge depth: Asp.  0.5mm
#                              Disp. 0.5mm
# - without pre-rinsing
# - dispense mode surface empty tip
#
#
# LC-Plasma is a copy from Serumclass, Plasma has the same Parameters and Correctioncurve!
#
# Typical performance data under laboratory conditions:
# (3 Volumes measured as control)
#
# Volume µl            Precision %        Trueness %
#       10                       2.09                   4.37
#       20                       1.16                   3.52
#       60                       0.55                   2.06
#
#
star_mapping[(300, False, True, False, Liquid.PLASMA, False, False)] = \
StandardVolumePlasmaDispenseSurface = HamiltonLiquidClass(
  curve={300.0: 313.4, 5.0: 6.3, 50.0: 54.9, 0.0: 0.0, 100.0: 107.1, 20.0: 23.0, 200.0: 210.5, 10.0: 12.0, 2.0: 2.6},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.PLASMA, False, True)] = \
StandardVolumePlasmaDispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 313.4, 5.0: 6.3, 50.0: 54.9, 0.0: 0.0, 20.0: 23.0, 100.0: 107.1, 2.0: 2.6, 10.0: 12.0, 200.0: 210.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.PLASMA, False, False)] = \
StandardVolumePlasmaDispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 313.4, 5.0: 6.8, 0.0: 0.0, 100.0: 109.1, 10.0: 12.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.DMSO, True, False)] = \
StandardVolume_96COREHead1000ul_DMSO_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 150.0: 150.0, 50.0: 50.0, 0.0: 0.0, 20.0: 20.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=20.0
)


star_mapping[(300, True, True, False, Liquid.DMSO, True, True)] = \
StandardVolume_96COREHead1000ul_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 302.5, 0.0: 0.0, 100.0: 101.0, 20.0: 20.4, 200.0: 201.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.DMSO, False, True)] = \
StandardVolume_96COREHead1000ul_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 306.0, 0.0: 0.0, 100.0: 104.3, 200.0: 205.0, 10.0: 12.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.WATER, True, False)] = \
StandardVolume_96COREHead1000ul_Water_DispenseJet_Aliquot = HamiltonLiquidClass(
  curve={300.0: 300.0, 150.0: 150.0, 50.0: 50.0, 0.0: 0.0, 20.0: 20.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(300, True, True, False, Liquid.WATER, True, True)] = \
StandardVolume_96COREHead1000ul_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 313.5, 0.0: 0.0, 100.0: 107.2, 20.0: 23.2, 200.0: 207.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.WATER, False, True)] = \
StandardVolume_96COREHead1000ul_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 313.5, 0.0: 0.0, 100.0: 107.2, 200.0: 210.0, 10.0: 11.9},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.DMSO, True, True)] = \
StandardVolume_96COREHead_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 303.5, 0.0: 0.0, 100.0: 101.8, 10.0: 10.2, 200.0: 200.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.DMSO, True, False)] = \
StandardVolume_96COREHead_DMSO_DispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 306.0, 0.0: 0.0, 100.0: 105.6, 10.0: 12.2, 200.0: 207.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(300, True, True, False, Liquid.DMSO, False, True)] = \
StandardVolume_96COREHead_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 303.0, 0.0: 0.0, 100.0: 101.3, 10.0: 10.6, 200.0: 202.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.DMSO, False, False)] = \
StandardVolume_96COREHead_DMSO_DispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 303.0, 0.0: 0.0, 100.0: 101.3, 10.0: 10.1, 200.0: 202.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.WATER, True, True)] = \
StandardVolume_96COREHead_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 309.0, 0.0: 0.0, 20.0: 22.3, 100.0: 104.2, 10.0: 11.9, 200.0: 207.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.WATER, True, False)] = \
StandardVolume_96COREHead_Water_DispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 309.0, 0.0: 0.0, 20.0: 22.3, 100.0: 104.2, 10.0: 11.9},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(300, True, True, False, Liquid.WATER, False, True)] = \
StandardVolume_96COREHead_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 306.3, 0.0: 0.0, 100.0: 104.5, 10.0: 11.9, 200.0: 205.7},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.WATER, False, False)] = \
StandardVolume_96COREHead_Water_DispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 304.0, 0.0: 0.0, 100.0: 105.3, 10.0: 11.9, 200.0: 205.7},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# Liquid class for wash standard volume tips with CO-RE 96 Head in CO-RE 96 Head Washer.
star_mapping[(300, True, True, False, Liquid.WATER, False, False)] = \
StandardVolume_Core96Washer_DispenseSurface = HamiltonLiquidClass(
  curve={300.0: 330.0, 5.0: 6.3, 0.5: 0.9, 50.0: 55.1, 0.0: 0.0, 100.0: 107.2, 20.0: 23.2, 1.0: 1.6, 200.0: 211.0, 10.0: 11.9, 2.0: 2.8},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=150.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=100.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=150.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=5.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


# -  ohne vorbenetzen, gleicher Tip
# -  Aspiration submerge depth  1.0mm
# -  Prealiquot equal to Aliquotvolume,  jet mode part volume
# -  Aliquot, jet mode part volume
# -  Postaliquot equal to Aliquotvolume,  jet mode empty tip
#
#
#
#
#
# Typical performance data under laboratory conditions:
#
# Volume µl                     Precision %        Trueness %
#       20  (12 Aliquots)          2.53                 -2.97
#       50  (  4 Aliquots)          0.84                 -2.57
#
star_mapping[(300, False, True, False, Liquid.DMSO, True, False)] = \
StandardVolume_DMSO_AliquotDispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 300.0, 30.0: 30.0, 0.0: 0.0, 20.0: 20.0, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=250.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, False, Liquid.DMSO, True, False)] = \
StandardVolume_DMSO_DispenseJet = HamiltonLiquidClass(
  curve={300.0: 304.6, 350.0: 355.2, 50.0: 51.1, 0.0: 0.0, 100.0: 101.8, 20.0: 20.7, 200.0: 203.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.DMSO, True, True)] = \
StandardVolume_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 304.6, 50.0: 51.1, 0.0: 0.0, 20.0: 20.7, 100.0: 101.8, 200.0: 203.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.DMSO, True, False)] = \
StandardVolume_DMSO_DispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 320.0, 0.0: 0.0, 20.0: 30.5, 100.0: 116.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=10.0
)


star_mapping[(300, False, True, False, Liquid.DMSO, False, False)] = \
StandardVolume_DMSO_DispenseSurface = HamiltonLiquidClass(
  curve={300.0: 308.8, 5.0: 6.6, 50.0: 52.9, 350.0: 360.5, 0.0: 0.0, 1.0: 1.8, 20.0: 22.1, 100.0: 103.8, 2.0: 3.0, 10.0: 11.9, 200.0: 205.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.DMSO, False, True)] = \
StandardVolume_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 308.8, 5.0: 6.6, 50.0: 52.9, 0.0: 0.0, 1.0: 1.8, 20.0: 22.1, 100.0: 103.8, 2.0: 3.0, 10.0: 11.9, 200.0: 205.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.DMSO, False, False)] = \
StandardVolume_DMSO_DispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 308.8, 5.0: 6.4, 50.0: 52.9, 0.0: 0.0, 20.0: 22.1, 100.0: 103.8, 10.0: 11.9, 200.0: 205.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 100, stop back volume = 0
star_mapping[(300, False, True, False, Liquid.ETHANOL, True, False)] = \
StandardVolume_EtOH_DispenseJet = HamiltonLiquidClass(
  curve={300.0: 310.2, 350.0: 360.5, 50.0: 55.8, 0.0: 0.0, 100.0: 107.5, 20.0: 24.6, 200.0: 209.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.ETHANOL, True, True)] = \
StandardVolume_EtOH_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 310.2, 50.0: 55.8, 0.0: 0.0, 20.0: 24.6, 100.0: 107.5, 200.0: 209.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.ETHANOL, True, False)] = \
StandardVolume_EtOH_DispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 317.2, 0.0: 0.0, 20.0: 25.6, 100.0: 110.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=5.0
)


# V1.1: Set mix flow rate to 20, dispense settling time=0, stop back volume = 0
star_mapping[(300, False, True, False, Liquid.GLYCERIN, True, False)] = \
StandardVolume_Glycerin_DispenseJet = HamiltonLiquidClass(
  curve={300.0: 309.0, 350.0: 360.0, 50.0: 53.6, 0.0: 0.0, 100.0: 104.9, 20.0: 22.3, 200.0: 207.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=20.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=20.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=20.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 20, dispense settling time=0, stop back volume = 0
star_mapping[(300, False, True, False, Liquid.GLYCERIN80, True, True)] = \
StandardVolume_Glycerin_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 309.0, 50.0: 53.6, 0.0: 0.0, 100.0: 104.9, 20.0: 22.3, 200.0: 207.2},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=20.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=20.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=20.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 10
star_mapping[(300, False, True, False, Liquid.GLYCERIN, False, False)] = \
StandardVolume_Glycerin_DispenseSurface = HamiltonLiquidClass(
  curve={300.0: 307.9, 5.0: 6.5, 350.0: 358.4, 50.0: 53.6, 0.0: 0.0, 100.0: 105.7, 20.0: 22.5, 200.0: 207.0, 10.0: 12.0, 2.0: 3.2},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=10.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=10.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=10.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=2.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.GLYCERIN80, False, True)] = \
StandardVolume_Glycerin_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 307.9, 5.0: 6.5, 50.0: 53.6, 0.0: 0.0, 100.0: 105.7, 20.0: 22.5, 200.0: 207.0, 10.0: 12.0, 2.0: 3.2},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=10.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=10.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=10.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=2.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.GLYCERIN80, False, False)] = \
StandardVolume_Glycerin_DispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 307.9, 5.0: 6.2, 50.0: 53.6, 0.0: 0.0, 100.0: 105.7, 20.0: 22.5, 200.0: 207.0, 10.0: 12.0},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=10.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=2.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=10.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=2.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, False, Liquid.SERUM, True, False)] = \
StandardVolume_Serum_AliquotDispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 300.0, 30.0: 30.0, 0.0: 0.0, 20.0: 20.0, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, False, Liquid.SERUM, True, False)] = \
StandardVolume_Serum_AliquotJet = HamiltonLiquidClass(
  curve={350.0: 350.0, 30.0: 30.0, 0.0: 0.0, 20.0: 20.0, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=250.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, False, Liquid.SERUM, True, False)] = \
StandardVolume_Serum_DispenseJet = HamiltonLiquidClass(
  curve={300.0: 315.2, 50.0: 55.6, 0.0: 0.0, 100.0: 108.1, 20.0: 23.2, 200.0: 212.1},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.SERUM, True, True)] = \
StandardVolume_Serum_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 315.2, 50.0: 55.6, 0.0: 0.0, 20.0: 23.2, 100.0: 108.1, 200.0: 212.1},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.SERUM, True, False)] = \
StandardVolume_Serum_DispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 315.2, 0.0: 0.0, 20.0: 29.2, 100.0: 111.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=10.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=5.0
)


star_mapping[(300, False, True, False, Liquid.SERUM, False, False)] = \
StandardVolume_Serum_DispenseSurface = HamiltonLiquidClass(
  curve={300.0: 313.4, 5.0: 6.3, 50.0: 54.9, 0.0: 0.0, 20.0: 23.0, 100.0: 107.1, 2.0: 2.6, 10.0: 12.0, 200.0: 210.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.SERUM, False, True)] = \
StandardVolume_Serum_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 313.4, 5.0: 6.3, 50.0: 54.9, 0.0: 0.0, 20.0: 23.0, 100.0: 107.1, 2.0: 2.6, 10.0: 12.0, 200.0: 210.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.SERUM, False, False)] = \
StandardVolume_Serum_DispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 313.4, 5.0: 6.8, 0.0: 0.0, 100.0: 109.1, 10.0: 12.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=15.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=10.0,
  dispense_stop_back_volume=0.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, False, Liquid.WATER, True, False)] = \
StandardVolume_Water_AliquotDispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 300.0, 30.0: 30.0, 0.0: 0.0, 20.0: 20.0, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, False, Liquid.WATER, True, False)] = \
StandardVolume_Water_AliquotJet = HamiltonLiquidClass(
  curve={350.0: 350.0, 30.0: 30.0, 0.0: 0.0, 20.0: 20.0, 10.0: 10.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=200.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=0.3,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, False, Liquid.WATER, True, False)] = \
StandardVolume_Water_DispenseJet = HamiltonLiquidClass(
  curve={300.0: 313.5, 350.0: 364.3, 50.0: 55.1, 0.0: 0.0, 100.0: 107.2, 20.0: 23.2, 200.0: 211.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=0.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.WATER, True, True)] = \
StandardVolume_Water_DispenseJetEmpty96Head = HamiltonLiquidClass(
  curve={300.0: 313.5, 0.0: 0.0, 100.0: 107.2, 200.0: 205.3, 10.0: 11.9},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.WATER, True, False)] = \
StandardVolume_Water_DispenseJetPart96Head = HamiltonLiquidClass(
  curve={300.0: 313.5, 0.0: 0.0, 100.0: 107.2, 200.0: 205.3, 10.0: 11.9},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.WATER, True, True)] = \
StandardVolume_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={300.0: 313.5, 50.0: 55.1, 0.0: 0.0, 20.0: 23.2, 100.0: 107.2, 200.0: 211.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.WATER, True, False)] = \
StandardVolume_Water_DispenseJet_Part = HamiltonLiquidClass(
  curve={300.0: 313.5, 0.0: 0.0, 20.0: 28.2, 100.0: 111.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=2.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=150.0,
  dispense_stop_back_volume=10.0
)


# V1.1: Set mix flow rate to 100
star_mapping[(300, False, True, False, Liquid.WATER, False, False)] = \
StandardVolume_Water_DispenseSurface = HamiltonLiquidClass(
  curve={300.0: 313.5, 5.0: 6.3, 0.5: 0.9, 350.0: 364.3, 50.0: 55.1, 0.0: 0.0, 100.0: 107.2, 20.0: 23.2, 1.0: 1.6, 200.0: 211.0, 10.0: 11.9, 2.0: 2.8},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.WATER, False, False)] = \
StandardVolume_Water_DispenseSurface96Head = HamiltonLiquidClass(
  curve={300.0: 313.5, 0.0: 0.0, 100.0: 107.2, 10.0: 11.9},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=1.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.WATER, False, True)] = \
StandardVolume_Water_DispenseSurfaceEmpty96Head = HamiltonLiquidClass(
  curve={300.0: 313.5, 0.0: 0.0, 100.0: 107.2, 200.0: 205.7, 10.0: 11.9},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, True, True, False, Liquid.WATER, False, False)] = \
StandardVolume_Water_DispenseSurfacePart96Head = HamiltonLiquidClass(
  curve={300.0: 313.5, 0.0: 0.0, 100.0: 107.2, 200.0: 205.7, 10.0: 11.9},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.WATER, False, True)] = \
StandardVolume_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={300.0: 313.5, 5.0: 6.3, 0.5: 0.9, 50.0: 55.1, 0.0: 0.0, 1.0: 1.6, 20.0: 23.2, 100.0: 107.2, 2.0: 2.8, 10.0: 11.9, 200.0: 211.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=100.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(300, False, True, False, Liquid.WATER, False, False)] = \
StandardVolume_Water_DispenseSurface_Part = HamiltonLiquidClass(
  curve={300.0: 313.5, 5.0: 6.8, 50.0: 55.1, 0.0: 0.0, 20.0: 23.2, 100.0: 107.2, 10.0: 12.3, 200.0: 211.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=5.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=4.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=1.0,
  dispense_stop_flow_rate=5.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, True, Liquid.DMSO, True, True)] = \
Tip_50ulFilter_96COREHead1000ul_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 52.1, 30.0: 31.7, 0.0: 0.0, 20.0: 21.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=1.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, True, Liquid.DMSO, False, True)] = \
Tip_50ulFilter_96COREHead1000ul_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.4, 50.0: 52.1, 30.0: 31.5, 0.0: 0.0, 1.0: 0.7, 10.0: 10.8},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, True, Liquid.WATER, True, True)] = \
Tip_50ulFilter_96COREHead1000ul_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 54.2, 30.0: 33.2, 0.0: 0.0, 20.0: 22.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, True, Liquid.WATER, False, True)] = \
Tip_50ulFilter_96COREHead1000ul_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.6, 50.0: 53.6, 30.0: 32.6, 0.0: 0.0, 1.0: 0.8, 10.0: 11.3},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, True, Liquid.DMSO, True, True)] = \
Tip_50ulFilter_96COREHead_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 51.4, 0.0: 0.0, 30.0: 31.3, 20.0: 21.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, True, Liquid.DMSO, False, True)] = \
Tip_50ulFilter_96COREHead_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.6, 50.0: 51.1, 0.0: 0.0, 30.0: 31.0, 1.0: 0.8, 10.0: 10.7},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, True, Liquid.WATER, True, True)] = \
Tip_50ulFilter_96COREHead_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 54.0, 30.0: 33.0, 0.0: 0.0, 20.0: 22.4},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, True, Liquid.WATER, False, True)] = \
Tip_50ulFilter_96COREHead_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.6, 50.0: 53.5, 30.0: 32.9, 0.0: 0.0, 1.0: 0.8, 10.0: 11.4},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, True, Liquid.DMSO, True, True)] = \
Tip_50ulFilter_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 52.5, 0.0: 0.0, 30.0: 31.4, 20.0: 21.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, True, Liquid.DMSO, False, True)] = \
Tip_50ulFilter_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.5, 50.0: 52.6, 0.0: 0.0, 30.0: 32.0, 1.0: 0.7, 10.0: 11.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, True, Liquid.ETHANOL, True, True)] = \
Tip_50ulFilter_EtOH_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 57.5, 0.0: 0.0, 30.0: 35.8, 20.0: 24.4},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=3.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, True, Liquid.ETHANOL, False, True)] = \
Tip_50ulFilter_EtOH_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 6.5, 50.0: 54.1, 0.0: 0.0, 30.0: 33.8, 1.0: 1.9, 10.0: 12.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=2.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=2.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, True, Liquid.GLYCERIN80, False, True)] = \
Tip_50ulFilter_Glycerin80_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.5, 50.0: 57.0, 0.0: 0.0, 30.0: 35.9, 1.0: 0.6, 10.0: 12.0},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=3.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=50.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=10.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=3.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, True, Liquid.SERUM, True, True)] = \
Tip_50ulFilter_Serum_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 54.6, 30.0: 33.5, 0.0: 0.0, 20.0: 22.6},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, True, Liquid.SERUM, False, True)] = \
Tip_50ulFilter_Serum_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.7, 50.0: 54.9, 30.0: 33.0, 0.0: 0.0, 1.0: 0.7, 10.0: 11.3},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=100.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, True, Liquid.WATER, True, True)] = \
Tip_50ulFilter_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 54.0, 30.0: 33.6, 0.0: 0.0, 20.0: 22.7},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, True, Liquid.WATER, False, True)] = \
Tip_50ulFilter_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.7, 50.0: 54.2, 30.0: 33.1, 0.0: 0.0, 1.0: 0.65, 10.0: 11.4},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.DMSO, True, True)] = \
Tip_50ul_96COREHead1000ul_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 52.1, 30.0: 31.7, 0.0: 0.0, 20.0: 21.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=1.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.DMSO, False, True)] = \
Tip_50ul_96COREHead1000ul_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.4, 50.0: 52.1, 30.0: 31.5, 0.0: 0.0, 1.0: 0.7, 10.0: 10.8},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.WATER, True, True)] = \
Tip_50ul_96COREHead1000ul_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 52.8, 0.0: 0.0, 30.0: 33.2, 20.0: 22.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.WATER, False, True)] = \
Tip_50ul_96COREHead1000ul_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.8, 50.0: 53.6, 30.0: 32.6, 0.0: 0.0, 1.0: 0.8, 10.0: 11.3},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=5.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=5.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=3.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.DMSO, True, True)] = \
Tip_50ul_96COREHead_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 51.4, 30.0: 31.3, 0.0: 0.0, 20.0: 21.1},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.DMSO, False, True)] = \
Tip_50ul_96COREHead_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.6, 50.0: 52.1, 30.0: 31.6, 0.0: 0.0, 1.0: 0.8, 10.0: 11.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.WATER, True, True)] = \
Tip_50ul_96COREHead_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 54.1, 0.0: 0.0, 30.0: 33.0, 20.0: 22.4},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, True, True, False, Liquid.WATER, False, True)] = \
Tip_50ul_96COREHead_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.6, 50.0: 53.6, 30.0: 32.9, 0.0: 0.0, 1.0: 0.7, 10.0: 11.4},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


# Liquid class for wash 50ul tips with CO-RE 96 Head in CO-RE 96 Head Washer.
star_mapping[(50, True, True, False, Liquid.WATER, False, False)] = \
Tip_50ul_Core96Washer_DispenseSurface = HamiltonLiquidClass(
  curve={5.0: 5.7, 50.0: 54.2, 30.0: 33.2, 0.0: 0.0, 1.0: 0.5, 10.0: 11.4},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=0.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=0.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, False, Liquid.DMSO, True, True)] = \
Tip_50ul_DMSO_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 52.5, 30.0: 32.2, 0.0: 0.0, 20.0: 21.4},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=10.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=1.0,
  dispense_blow_out_volume=10.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=200.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, False, Liquid.DMSO, False, True)] = \
Tip_50ul_DMSO_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.6, 50.0: 52.6, 30.0: 32.1, 0.0: 0.0, 1.0: 0.7, 10.0: 11.0},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, False, Liquid.ETHANOL, True, True)] = \
Tip_50ul_EtOH_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 58.4, 0.0: 0.0, 30.0: 36.0, 20.0: 24.2},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=50.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=400.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=3.0,
  dispense_blow_out_volume=50.0,
  dispense_swap_speed=4.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, False, Liquid.ETHANOL, False, True)] = \
Tip_50ul_EtOH_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 6.7, 50.0: 54.1, 0.0: 0.0, 30.0: 33.7, 1.0: 2.1, 10.0: 12.1},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=2.0,
  aspiration_blow_out_volume=2.0,
  aspiration_swap_speed=50.0,
  aspiration_settling_time=0.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=75.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=2.0,
  dispense_blow_out_volume=2.0,
  dispense_swap_speed=50.0,
  dispense_settling_time=0.5,
  dispense_stop_flow_rate=50.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, False, Liquid.GLYCERIN80, False, True)] = \
Tip_50ul_Glycerin80_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.7, 50.0: 59.4, 0.0: 0.0, 30.0: 36.0, 1.0: 0.3, 10.0: 11.8},
  aspiration_flow_rate=50.0,
  aspiration_mix_flow_rate=50.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=2.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=50.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=10.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=2.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=2.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, False, Liquid.SERUM, True, True)] = \
Tip_50ul_Serum_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 54.6, 30.0: 33.5, 0.0: 0.0, 20.0: 22.6},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=150.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, False, Liquid.SERUM, False, True)] = \
Tip_50ul_Serum_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.7, 50.0: 54.9, 30.0: 33.0, 0.0: 0.0, 1.0: 0.7, 10.0: 11.3},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=100.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, False, Liquid.WATER, True, True)] = \
Tip_50ul_Water_DispenseJet_Empty = HamiltonLiquidClass(
  curve={50.0: 54.0, 30.0: 33.5, 0.0: 0.0, 20.0: 22.5},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=100.0,
  aspiration_air_transport_volume=5.0,
  aspiration_blow_out_volume=30.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=0.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=180.0,
  dispense_mode=3.0,
  dispense_mix_flow_rate=1.0,
  dispense_air_transport_volume=5.0,
  dispense_blow_out_volume=30.0,
  dispense_swap_speed=1.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=100.0,
  dispense_stop_back_volume=0.0
)


star_mapping[(50, False, True, False, Liquid.WATER, False, True)] = \
Tip_50ul_Water_DispenseSurface_Empty = HamiltonLiquidClass(
  curve={5.0: 5.7, 50.0: 54.2, 30.0: 33.2, 0.0: 0.0, 1.0: 0.7, 10.0: 11.4},
  aspiration_flow_rate=100.0,
  aspiration_mix_flow_rate=75.0,
  aspiration_air_transport_volume=0.0,
  aspiration_blow_out_volume=1.0,
  aspiration_swap_speed=2.0,
  aspiration_settling_time=1.0,
  aspiration_over_aspirate_volume=2.0,
  aspiration_clot_retract_height=0.0,
  dispense_flow_rate=120.0,
  dispense_mode=5.0,
  dispense_mix_flow_rate=75.0,
  dispense_air_transport_volume=0.0,
  dispense_blow_out_volume=1.0,
  dispense_swap_speed=2.0,
  dispense_settling_time=0.0,
  dispense_stop_flow_rate=1.0,
  dispense_stop_back_volume=0.0
)
