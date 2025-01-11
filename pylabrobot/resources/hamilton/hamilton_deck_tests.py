import textwrap
import unittest

from pylabrobot.resources.corning_costar import (
  Cor_96_wellplate_360ul_Fb,
)
from pylabrobot.resources.hamilton import (
  HTF,
  PLT_CAR_L5AC_A00,
  STF,
  TIP_CAR_480_A00,
  STARLetDeck,
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
    tip_car[0] = STF(name="tip_rack_01")
    tip_car[1] = STF(name="tip_rack_02")
    tip_car[3] = HTF(name="tip_rack_04")

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
    Rail  Resource                      Type           Coordinates (mm)
    =================================================================================
    (-6)  ├── trash_core96              Trash          (-58.200, 106.000, 229.000)
          │
    (1)   ├── tip_carrier               TipCarrier     (100.000, 063.000, 100.000)
          │   ├── tip_rack_01           TipRack        (106.200, 073.000, 214.950)
          │   ├── tip_rack_02           TipRack        (106.200, 169.000, 214.950)
          │   ├── <empty>
          │   ├── tip_rack_04           TipRack        (106.200, 361.000, 214.950)
          │   ├── <empty>
          │
    (21)  ├── plate carrier             PlateCarrier   (550.000, 063.000, 100.000)
          │   ├── aspiration plate      Plate          (554.000, 071.500, 183.120)
          │   ├── <empty>
          │   ├── dispense plate        Plate          (554.000, 263.500, 183.120)
          │   ├── <empty>
          │   ├── <empty>
          │
    (31)  ├── teaching_carrier          Resource       (775.000, 051.800, 100.000)
          │   ├── teaching_tip_rack     TipRack        (780.900, 461.100, 100.000)
          │
    (32)  ├── trash                     Trash          (800.000, 190.600, 137.100)
    """[1:]
      ),
    )

  def test_assign_gigantic_resource(self):
    stanley_cup = StanleyCup_QUENCHER_FLOWSTATE_TUMBLER(name="HUGE")
    deck = STARLetDeck()
    with self.assertLogs() as log:
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
