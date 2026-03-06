import textwrap
import unittest

from pylabrobot.resources.corning import (
  Cor_96_wellplate_360ul_Fb,
)
from pylabrobot.resources.hamilton import (
  PLT_CAR_L5AC_A00,
  TIP_CAR_480_A00,
  STARLetDeck,
  hamilton_96_tiprack_300uL_filter,
  hamilton_96_tiprack_1000uL_filter,
)
from pylabrobot.resources.stanley.cups import (
  StanleyCup_QUENCHER_FLOWSTATE_TUMBLER,
)


class HamiltonDeckTests(unittest.TestCase):
  """Tests for the HamiltonDeck class."""

  def build_layout(self):
    """Build a deck layout for testing"""
    deck = STARLetDeck()

    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = hamilton_96_tiprack_300uL_filter(name="tip_rack_01")
    tip_car[1] = hamilton_96_tiprack_300uL_filter(name="tip_rack_02")
    tip_car[3] = hamilton_96_tiprack_1000uL_filter(name="tip_rack_04")

    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cor_96_wellplate_360ul_Fb(name="aspiration plate")
    plt_car[2] = Cor_96_wellplate_360ul_Fb(name="dispense plate")

    deck.assign_child_resource(tip_car, rails=1)
    deck.assign_child_resource(plt_car, rails=21)

    return deck

  def test_summary(self):
    self.maxDiff = None
    deck = self.build_layout()
    self.assertEqual(
      deck.summary(),
      textwrap.dedent(
        """
    Rail  Resource                      Type                 Coordinates (mm)
    =======================================================================================
    (-6)  ├── trash_core96              Trash                (-58.200, 106.000, 216.400)
          │
    (1)   ├── tip_carrier               TipCarrier           (100.000, 063.000, 100.000)
          │   ├── tip_rack_01           TipRack              (106.200, 073.000, 214.950)
          │   ├── tip_rack_02           TipRack              (106.200, 169.000, 214.950)
          │   ├── <empty>
          │   ├── tip_rack_04           TipRack              (106.200, 361.000, 214.950)
          │   ├── <empty>
          │
    (21)  ├── plate carrier             PlateCarrier         (550.000, 063.000, 100.000)
          │   ├── aspiration plate      Plate                (554.000, 071.500, 183.120)
          │   ├── <empty>
          │   ├── dispense plate        Plate                (554.000, 263.500, 183.120)
          │   ├── <empty>
          │   ├── <empty>
          │
    (31)  ├── waste_block               Resource             (775.000, 115.000, 100.000)
          │   ├── teaching_tip_rack     TipRack              (780.900, 461.100, 100.000)
          │   ├── waste_position_1      Trash                (800.000, 405.000, 187.000)
          │   ├── waste_position_2      Trash                (800.000, 392.500, 187.000)
          │   ├── waste_position_3      Trash                (800.000, 380.000, 187.000)
          │   ├── waste_position_4      Trash                (800.000, 367.500, 187.000)
          │   ├── waste_position_5      Trash                (800.000, 355.000, 187.000)
          │   ├── waste_position_6      Trash                (800.000, 342.500, 187.000)
          │   ├── waste_position_7      Trash                (800.000, 330.000, 187.000)
          │   ├── waste_position_8      Trash                (800.000, 317.500, 187.000)
          │   ├── waste_position_9      Trash                (800.000, 305.000, 187.000)
          │   ├── waste_position_10     Trash                (800.000, 292.500, 187.000)
          │   ├── waste_position_11     Trash                (800.000, 280.000, 187.000)
          │   ├── waste_position_12     Trash                (800.000, 267.500, 187.000)
          │   ├── waste_position_13     Trash                (800.000, 255.000, 187.000)
          │   ├── waste_position_14     Trash                (800.000, 242.500, 187.000)
          │   ├── waste_position_15     Trash                (800.000, 230.000, 187.000)
          │   ├── waste_position_16     Trash                (800.000, 217.500, 187.000)
          │   ├── core_grippers         HamiltonCoreGrippers (797.500, 085.500, 205.000)
          │
    (32)  ├── trash                     Trash                (800.000, 190.600, 137.100)
    """[1:]
      ),
    )

  def test_get_waste_positions_default_returns_16(self):
    """Default STARLetDeck has 16 addressable waste positions."""
    deck = STARLetDeck()
    positions = deck.get_waste_positions()
    self.assertEqual(len(positions), 16)
    self.assertEqual([p.name for p in positions], [f"waste_position_{i}" for i in range(1, 17)])

  def test_get_waste_positions_none_returns_single_trash(self):
    """STARLetDeck(waste_positions=None) returns single trash (same as get_trash_area)."""
    deck = STARLetDeck(waste_positions=None)
    positions = deck.get_waste_positions()
    self.assertEqual(len(positions), 1)
    self.assertIs(positions[0], deck.get_trash_area())

  def test_assign_gigantic_resource(self):
    stanley_cup = StanleyCup_QUENCHER_FLOWSTATE_TUMBLER(name="HUGE")
    deck = STARLetDeck()
    with self.assertLogs("pylabrobot") as log:
      deck.assign_child_resource(stanley_cup, rails=1)
    self.assertEqual(
      log.output,
      [
        "WARNING:pylabrobot:Resource 'HUGE' is very high on the deck: 412.42 mm. Be "
        "careful when traversing the deck.",
        "WARNING:pylabrobot:Resource 'HUGE' is very high on the deck: 412.42 mm. Be "
        "careful when grabbing this resource.",
      ],
    )
