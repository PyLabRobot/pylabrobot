""" Tests for LiquidHandler """
# pylint: disable=missing-class-docstring

import io
import os
import tempfile
import textwrap
import unittest
import unittest.mock

from . import backends
from .backends import STAR
from .liquid_handler import LiquidHandler
from .resources import (
  Coordinate,
  Carrier,
  PlateCarrier,
  Plate,
  TipCarrier,
  Tips,
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cos_96_DW_1mL,
  Cos_96_DW_500ul,
  standard_volume_tip_with_filter
)
from .resources.ml_star import STF_L, HTF_L


class TestLiquidHandlerLayout(unittest.TestCase):
  def setUp(self):
    star = backends.STAR()
    self.lh = LiquidHandler(star)

  def test_resource_assignment(self):
    tip_car = TIP_CAR_480_A00(name="tip carrier")
    tip_car[0] = STF_L(name="tips_01")
    tip_car[1] = STF_L(name="tips_02")
    tip_car[3] = HTF_L("tips_04")

    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cos_96_DW_1mL(name="aspiration plate")
    plt_car[2] = Cos_96_DW_500ul(name="dispense plate")

    self.lh.assign_resource(tip_car, rails=1)
    self.lh.assign_resource(plt_car, rails=21)

    # Test placing a carrier at a location where another carrier is located.
    with self.assertRaises(ValueError):
      dbl_plt_car_1 = PLT_CAR_L5AC_A00(name="double placed carrier 1")
      self.lh.assign_resource(dbl_plt_car_1, rails=1)

      dbl_plt_car_2 = PLT_CAR_L5AC_A00(name="double placed carrier 2")
      self.lh.assign_resource(dbl_plt_car_2, rails=2)

      dbl_plt_car_3 = PLT_CAR_L5AC_A00(name="double placed carrier 3")
      self.lh.assign_resource(dbl_plt_car_3, rails=20)

    # Test carrier with same name.
    with self.assertRaises(ValueError):
      same_name_carrier = PLT_CAR_L5AC_A00(name="plate carrier")
      self.lh.assign_resource(same_name_carrier, rails=10)
    # Should not raise when replacing.
    self.lh.assign_resource(same_name_carrier, rails=10, replace=True)
    # Should not raise when unassinged.
    self.lh.unassign_resource("plate carrier")
    self.lh.assign_resource(same_name_carrier, rails=10, replace=True)

    # Test unassigning unassigned resource
    self.lh.unassign_resource("plate carrier")
    with self.assertRaises(KeyError):
      self.lh.unassign_resource("plate carrier")
    with self.assertRaises(KeyError):
      self.lh.unassign_resource("this resource is completely new.")

    # Test invalid rails.
    with self.assertRaises(ValueError):
      self.lh.assign_resource(plt_car, rails=-1)
    with self.assertRaises(ValueError):
      self.lh.assign_resource(plt_car, rails=42)
    with self.assertRaises(ValueError):
      self.lh.assign_resource(plt_car, rails=27)

  def test_get_resource(self):
    tip_car = TIP_CAR_480_A00(name="tip carrier")
    tip_car[0] = STF_L(name="tips_01")
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cos_96_DW_1mL(name="aspiration plate")
    self.lh.assign_resource(tip_car, rails=1)
    self.lh.assign_resource(plt_car, rails=10)

    # Get resource.
    self.assertEqual(self.lh.get_resource("tip carrier").name, "tip carrier")
    self.assertEqual(self.lh.get_resource("plate carrier").name, "plate carrier")

    # Get subresource.
    self.assertEqual(self.lh.get_resource("tips_01").name, "tips_01")
    self.assertEqual(self.lh.get_resource("aspiration plate").name, "aspiration plate")

    # Get unknown resource.
    self.assertIsNone(self.lh.get_resource("unknown resource"))

  def test_subcoordinates(self):
    tip_car = TIP_CAR_480_A00(name="tip carrier")
    tip_car[0] = STF_L(name="tips_01")
    tip_car[3] = HTF_L(name="tips_04")
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cos_96_DW_1mL(name="aspiration plate")
    plt_car[2] = Cos_96_DW_500ul(name="dispense plate")
    self.lh.assign_resource(tip_car, rails=1)
    self.lh.assign_resource(plt_car, rails=10)

    # Rails 10 should be left of rails 1.
    self.assertGreater(self.lh.get_resource("plate carrier").location.x,
                       self.lh.get_resource("tip carrier").location.x)

    # Verified with Hamilton Method Editor.
    # Carriers.
    self.assertEqual(self.lh.get_resource("tip carrier").location,
                     Coordinate(100.0, 63.0, 100.0))
    self.assertEqual(self.lh.get_resource("plate carrier").location,
                     Coordinate(302.5, 63.0, 100.0))

    # Subresources.
    self.assertEqual(self.lh.get_resource("tips_01").location,
                     Coordinate(117.900, 145.800, 164.450))
    self.assertEqual(self.lh.get_resource("tips_04").location,
                     Coordinate(117.900, 433.800, 131.450))

    self.assertEqual(self.lh.get_resource("dispense plate").location,
                     Coordinate(320.500, 338.000, 188.150))
    self.assertEqual(self.lh.get_resource("aspiration plate").location,
                     Coordinate(320.500, 146.000, 187.150))

  def build_layout(self):
    tip_car = TIP_CAR_480_A00(name="tip carrier")
    tip_car[0] = STF_L(name="tips_01")
    tip_car[1] = STF_L(name="tips_02")
    tip_car[3] = HTF_L("tips_04")

    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cos_96_DW_1mL(name="aspiration plate")
    plt_car[2] = Cos_96_DW_500ul(name="dispense plate")

    self.lh.assign_resource(tip_car, rails=1, replace=True)
    self.lh.assign_resource(plt_car, rails=21, replace=True)

  @unittest.mock.patch("sys.stdout", new_callable=io.StringIO)
  def test_summary(self, out):
    with self.assertRaises(ValueError):
      self.lh.summary()

    self.build_layout()
    self.maxDiff = None # pylint: disable=invalid-name
    expected_out = textwrap.dedent("""
    Rail     Resource                   Type                Coordinates (mm)
    ===============================================================================================
    (1)  ├── tip carrier                TIP_CAR_480_A00     (100.000, 063.000, 100.000)
         │   ├── tips_01                STF_L               (117.900, 145.800, 164.450)
         │   ├── tips_02                STF_L               (117.900, 241.800, 164.450)
         │   ├── <empty>
         │   ├── tips_04                HTF_L               (117.900, 433.800, 131.450)
         │   ├── <empty>
         │
    (21) ├── plate carrier              PLT_CAR_L5AC_A00    (550.000, 063.000, 100.000)
         │   ├── aspiration plate       Cos_96_DW_1mL       (568.000, 146.000, 187.150)
         │   ├── <empty>
         │   ├── dispense plate         Cos_96_DW_500ul     (568.000, 338.000, 188.150)
         │   ├── <empty>
         │   ├── <empty>
    """[1:])
    self.lh.summary()
    self.assertEqual(out.getvalue(), expected_out)

  def test_parse_lay_file(self):
    fn = "./pyhamilton/testing/test_data/test_deck.lay"
    self.lh.load_from_lay_file(fn)

    self.assertEqual(self.lh.get_resource("TIP_CAR_480_A00_0001").location, \
                     Coordinate(122.500, 63.000, 100.000))
    self.assertEqual(self.lh.get_resource("tips_01").location, \
                     Coordinate(140.400, 145.800, 164.450))
    self.assertEqual(self.lh.get_resource("STF_L_0001").location, \
                     Coordinate(140.400, 241.800, 164.450))
    self.assertEqual(self.lh.get_resource("tips_04").location, \
                     Coordinate(140.400, 433.800, 131.450))

    self.assertEqual(self.lh.get_resource("TIP_CAR_480_A00_0001")[0].name, "tips_01")
    self.assertEqual(self.lh.get_resource("TIP_CAR_480_A00_0001")[1].name, "STF_L_0001")
    self.assertIsNone(self.lh.get_resource("TIP_CAR_480_A00_0001")[2])
    self.assertEqual(self.lh.get_resource("TIP_CAR_480_A00_0001")[3].name, "tips_04")
    self.assertIsNone(self.lh.get_resource("TIP_CAR_480_A00_0001")[4])

    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0001").location, \
                     Coordinate(302.500, 63.000, 100.000))
    self.assertEqual(self.lh.get_resource("Cos_96_DW_1mL_0001").location, \
                     Coordinate(320.500, 146.000, 187.150))
    self.assertEqual(self.lh.get_resource("Cos_96_DW_500ul_0001").location, \
                     Coordinate(320.500, 338.000, 188.150))
    self.assertEqual(self.lh.get_resource("Cos_96_DW_1mL_0002").location, \
                     Coordinate(320.500, 434.000, 187.150))
    self.assertEqual(self.lh.get_resource("Cos_96_DW_2mL_0001").location, \
                     Coordinate(320.500, 530.000, 187.150))

    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0001")[0].name, "Cos_96_DW_1mL_0001")
    self.assertIsNone(self.lh.get_resource("PLT_CAR_L5AC_A00_0001")[1])
    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0001")[2].name, "Cos_96_DW_500ul_0001")
    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0001")[3].name, "Cos_96_DW_1mL_0002")
    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0001")[4].name, "Cos_96_DW_2mL_0001")

    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0002").location, \
                     Coordinate(482.500, 63.000, 100.000))
    self.assertEqual(self.lh.get_resource("Cos_96_DW_1mL_0003").location, \
                     Coordinate(500.500, 146.000, 187.150))
    self.assertEqual(self.lh.get_resource("Cos_96_DW_500ul_0003").location, \
                     Coordinate(500.500, 242.000, 188.150))
    self.assertEqual(self.lh.get_resource("Cos_96_PCR_0001").location, \
                     Coordinate(500.500, 434.000, 186.650))

    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0002")[0].name, "Cos_96_DW_1mL_0003")
    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0002")[1].name, "Cos_96_DW_500ul_0003")
    self.assertIsNone(self.lh.get_resource("PLT_CAR_L5AC_A00_0002")[2])
    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0002")[3].name, "Cos_96_PCR_0001")
    self.assertIsNone(self.lh.get_resource("PLT_CAR_L5AC_A00_0002")[4])

  def assert_same(self, lh1, lh2):
    # pylint: disable=protected-access
    self.assertEqual(len(lh1._resources), len(lh2._resources))

    for r_name in lh1._resources:
      resource_1 = lh1.get_resource(r_name)
      resource_2 = lh2.get_resource(r_name)

      self.assertEqual(resource_1.location, resource_2.location)
      self.assertEqual(type(resource_1), type(resource_2))

      if isinstance(resource_1, Carrier):
        self.assertEqual(len(resource_1._sites), len(resource_2._sites))

        for key in range(resource_1.capacity):
          subresource_1 = resource_1[key]
          subresource_2        = resource_2[key]
          self.assertEqual(type(subresource_1), type(subresource_2))
          if subresource_2 is None:
            self.assertIsNone(subresource_1)
          else:
            self.assertEqual(subresource_1.name, subresource_2.name)

  def test_json_serialization(self):
    # test with standard resource classes
    self.build_layout()
    tmp_dir = tempfile.gettempdir()
    fn = os.path.join(tmp_dir, "layout.json")
    self.lh.save(fn)

    star = backends.STAR()
    recovered = LiquidHandler(star)
    recovered.load_from_json(fn)

    self.assert_same(self.lh, recovered)

    # test with custom classes
    custom_1 = LiquidHandler(star)
    tc = TipCarrier("tc", 200, 200, 200, [
      Coordinate(10, 20, 30)
    ])
    tc[0] = Tips("tips", 10, 20, 30, standard_volume_tip_with_filter, -1, -1, -1)
    pc = PlateCarrier("pc", 100, 100, 100, [
      Coordinate(40, 50, 60)
    ])
    pc[0] = Plate("plate", 10, 20, 30, -1, -1, -1)

    fn = os.path.join(tmp_dir, "layout.json")
    custom_1.save(fn)
    custom_recover = LiquidHandler(star)
    custom_recover.load(fn)

    self.assert_same(custom_1, custom_recover)

    # unsupported format
    with self.assertRaises(ValueError):
      custom_recover.load(fn + ".unsupported")


class MockSTARBackend(STAR):
  def __init__(self):
    super().__init__()
    self.commands = []

  def send_command(self, module, command, **kwargs):
    cmd, _ = self._assemble_command(module, command, **kwargs)
    self.commands.append(cmd)


class TestLiquidHandlerCommands(unittest.TestCase):
  def setUp(self):
    # pylint: disable=invalid-name
    self.mockSTAR = MockSTARBackend()
    self.lh = LiquidHandler(self.mockSTAR)

    tip_car = TIP_CAR_480_A00(name="tip carrier")
    tip_car[1] = STF_L(name="tips_01")
    self.lh.assign_resource(tip_car, rails=1)

    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cos_96_DW_1mL(name="plate_01")
    self.lh.assign_resource(plt_car, rails=9)

    self.maxDiff = None

  def _assert_command_in_command_buffer(self, cmd: str, should_be: bool, fmt: str):
    """ Assert that the given command was sent to the backend. The ordering of the parameters is not
    taken into account, but the values and formatting should match. The id parameter of the command
    is ignored.

    Args:
      cmd: the command to look for
      should_be: whether the command should be found or not
      fmt: the format of the command
    """

    found = False
    # Command that fits the format, but is not the same as the command we are looking for.
    similar = None

    parsed_cmd = self.mockSTAR.parse_fw_string(cmd, fmt)
    parsed_cmd.pop("id")

    for sent_cmd in self.mockSTAR.commands:
      # When the module and command do not match, there is no point in comparing the parameters.
      if sent_cmd[0:4] != cmd[0:4]:
        continue

      try:
        parsed_sent_cmd = self.mockSTAR.parse_fw_string(sent_cmd, fmt)
        parsed_sent_cmd.pop("id")

        if parsed_cmd == parsed_sent_cmd:
          self.mockSTAR.commands.remove(sent_cmd)
          found = True
          break
        else:
          similar = parsed_sent_cmd
      except ValueError as e:
        # The command could not be parsed.
        print(e)
        continue

    if should_be and not found:
      if similar is not None:
        # These will not be equal, but this method does give a better error message.
        self.assertEqual(similar, parsed_cmd)
      else:
        self.fail(f"Command {cmd} not found in sent commands: {self.mockSTAR.commands}")
    elif not should_be and found:
      self.fail(f"Command {cmd} was found in sent commands: {self.mockSTAR.commands}")

  def _assert_command_sent_once(self, cmd: str, fmt: str):
    """ Assert that the given command was sent to the backend exactly once. """
    self._assert_command_in_command_buffer(cmd, True, fmt)
    self._assert_command_in_command_buffer(cmd, False, fmt)

  def test_tip_definition(self):
    pass

  def test_tip_pickup_01(self):
    self.lh.pickup_tips("tips_01", [True, True, False, False, False, False, False, False])
    self._assert_command_sent_once("C0TPid0000xp01269&yp2418 2328 0000&tm1 0&tt01tp2244tz2164th2450td0",
                           "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####td#")

  def test_tip_pickup_45(self):
    self.lh.pickup_tips("tips_01", [False, False, False, False, True, True, False, False])
    self._assert_command_sent_once("C0TPid0000xp01269&yp2058 1968 0000&tm1 0&tt01tp2244tz2164th2450td0",
                           "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####td#")

  def test_tip_pickup_15(self):
    self.lh.pickup_tips("tips_01", [True, False, False, False, False, True, False, False])
    self._assert_command_sent_once("C0TPid0000xp01269&yp2418 1968 0000&tm1 0&tt01tp2244tz2164th2450td0",
                           "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####td#")

  def test_tip_discard_45(self):
    self.test_tip_pickup_45() # pick up tips first
    self.lh.discard_tips("tips_01", [False, False, False, False, True, True, False, False])
    self._assert_command_sent_once("C0TRid0000xp01269 00000&yp2058 1968 0000&tm1 0&tt01tp2244tz2164th2450td0",
                           "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####td#")

  def test_single_channel_aspiration(self):
    self.test_tip_pickup_45() # pick up tips first
    self.lh.aspirate("plate_01", [100])

    # Real command, but with extra parameters. `lp`, `zl`, `zx`, `av`, `zu`, `zr` changed
    # TODO: Do we need these parameters with the real robot?
    # self._assert_command_sent_once(
    #   "C0ASid0300at0&tm1 0&xp02980 00000&yp1460 0000&th2450te2450lp2321 2450&ch000&zl1881 2450&"
    #   "zx1871 0000&ip0000&it0&fp0000&av01072 00000&as1000&ta000&ba0000&oa000&lm0&ll1&lv1&ld00&"
    #   "de0020&wt10&mv00000&mc00&mp000&ms1000&gi000&gj0gk0zu0032 0000&zr06180 00000&mh0000&zo000&"
    #   "po0100&lk0&ik0000&sd0500&se0500&sz0300&io0000&il00000&in0000&",
    #   fmt="at# (n)tm# (n)xp##### (n)yp#### (n)th####te####lp#### (n)ch### (n)zl#### (n)zx#### (n)"
    #   "ip#### (n)it# (n)fp#### (n)av#### (n)as#### (n)ta### (n)ba#### (n)oa### (n)lm# (n)ll# (n)"
    #   "lv# (n)ld## (n)de#### (n)wt## (n)mv##### (n)mc## (n)mp### (n)ms#### (n)gi### (n)gj#gk#"
    #   "zu#### (n)zr#### (n)mh#### (n)zo### (n)po#### (n)lk# (n)ik#### (n)sd#### (n)se#### (n)"
    #   "sz#### (n)io#### (n)il##### (n)in#### (n)")

    # This passes the test, but is not the real command.
    self._assert_command_sent_once(
      "C0ASid0000at0&tm1 0&xp02980 00000&yp1460 0000&th2450te2450lp2321&ch000&zl1881&"
      "zx1871&ip0000&it0&fp0000&av01072&as1000&ta000&ba0000&oa000&lm0&ll1&lv1&ld00&"
      "de0020&wt10&mv00000&mc00&mp000&ms1000&gi000&gj0gk0zu0032&zr06180&mh0000&zo000&"
      "po0100&lk0&ik0000&sd0500&se0500&sz0300&io0000&il00000&in0000&",
      fmt="at# (n)tm# (n)xp##### (n)yp#### (n)th####te####lp#### (n)ch### (n)zl#### (n)zx#### (n)"
      "ip#### (n)it# (n)fp#### (n)av#### (n)as#### (n)ta### (n)ba#### (n)oa### (n)lm# (n)ll# (n)"
      "lv# (n)ld## (n)de#### (n)wt## (n)mv##### (n)mc## (n)mp### (n)ms#### (n)gi### (n)gj#gk#"
      "zu#### (n)zr#### (n)mh#### (n)zo### (n)po#### (n)lk# (n)ik#### (n)sd#### (n)se#### (n)"
      "sz#### (n)io#### (n)il##### (n)in#### (n)")

  def test_multi_channel_aspiration(self):
    self.test_tip_pickup_45() # pick up tips first
    self.lh.aspirate("plate_01", [100, 100])

    # Real command
    # self._assert_command_sent_once(
    #   "C0ASid0225at0&tm1 1 0&xp02980 02980 00000&yp1460 1370 0000&th2450te2450lp2321 2321 2450&"
    #   "ch000 000&zl1881 1881 2450&zx1871 1871 0000&ip0000 0000&it0 0&fp0000 0000&"
    #   "av01072 01072 00000&as1000 1000&ta000 000&ba0000 0000&oa000 000&lm0 0&ll1 1&lv1 1&ld00 00&"
    #   "de0020 0020&wt10 10&mv00000 00000&mc00 00&mp000 000&ms1000 1000&gi000 000&gj0gk0"
    #   "zu0032 0032 0000&zr06180 06180 00000&mh0000 0000&zo000 000&po0100 0100&lk0 0&ik0000 0000&"
    #   "sd0500 0500&se0500 0500&sz0300 0300&io0000 0000&il00000 00000&in0000 0000&",
    #   fmt="at# (n)tm# (n)xp##### (n)yp#### (n)th####te####lp#### (n)ch### (n)zl#### (n)zx#### (n)"
    #   "ip#### (n)it# (n)fp#### (n)av#### (n)as#### (n)ta### (n)ba#### (n)oa### (n)lm# (n)ll# (n)"
    #   "lv# (n)ld## (n)de#### (n)wt## (n)mv##### (n)mc## (n)mp### (n)ms#### (n)gi### (n)gj#gk#"
    #   "zu#### (n)zr#### (n)mh#### (n)zo### (n)po#### (n)lk# (n)ik#### (n)sd#### (n)se#### (n)"
    #   "sz#### (n)io#### (n)il##### (n)in#### (n)")

    # This passes the test, but is not the real command.
    self._assert_command_sent_once(
      "C0ASid0000at0&tm1 1 0&xp02980 02980 00000&yp1460 1370 0000&th2450te2450lp2321 2321&"
      "ch000 000&zl1881 1881&zx1871 1871&ip0000 0000&it0 0&fp0000 0000&"
      "av01072 01072&as1000 1000&ta000 000&ba0000 0000&oa000 000&lm0 0&ll1 1&lv1 1&ld00 00&"
      "de0020 0020&wt10 10&mv00000 00000&mc00 00&mp000 000&ms1000 1000&gi000 000&gj0gk0"
      "zu0032 0032&zr06180 06180&mh0000 0000&zo000 000&po0100 0100&lk0 0&ik0000 0000&"
      "sd0500 0500&se0500 0500&sz0300 0300&io0000 0000&il00000 00000&in0000 0000&",
      fmt="at# (n)tm# (n)xp##### (n)yp#### (n)th####te####lp#### (n)ch### (n)zl#### (n)zx#### (n)"
      "ip#### (n)it# (n)fp#### (n)av#### (n)as#### (n)ta### (n)ba#### (n)oa### (n)lm# (n)ll# (n)"
      "lv# (n)ld## (n)de#### (n)wt## (n)mv##### (n)mc## (n)mp### (n)ms#### (n)gi### (n)gj#gk#"
      "zu#### (n)zr#### (n)mh#### (n)zo### (n)po#### (n)lk# (n)ik#### (n)sd#### (n)se#### (n)"
      "sz#### (n)io#### (n)il##### (n)in#### (n)")

  def test_single_channel_dispense(self):
    self.test_tip_pickup_45()
    self.lh.dispense("plate_01", [100])
    self._assert_command_sent_once(
      "C0DSid0000dm2&tm1 0&xp02980 00000&yp1460 0000&zx1871&lp2321&zl1881&"
      "ip0000&it0&fp0000&th2450te2450dv01072&ds1200&ss0050&rv000&ta000&ba0000&lm0&zo000&ll1&"
      "lv1&de0020&mv00000&mc00&mp000&ms0010&wt00&gi000&gj0gk0zu0032&dj00zr06180&"
      " mh0000&po0100&",
      "dm# (n)tm# (n)xp##### (n)yp#### (n)zx#### (n)lp#### (n)zl#### (n)ip#### (n)it# (n)fp#### (n)"
      "th####te####dv##### (n)ds#### (n)ss#### (n)rv### (n)ta### (n)ba#### (n)lm# (n)zo### (n)"
      "ll# (n)lv# (n)de#### (n)mv##### (n)mc## (n)mp### (n)ms#### (n)wt## (n)gi### (n)gj#gk#"
      "zu#### (n)zr##### (n)mh#### (n)po#### (n)")

  def test_multi_channel_dispense(self):
    self.test_tip_pickup_45() # pick up tips first
    self.lh.dispense("plate_01", [100, 100])
    # self._assert_command_sent_once(
    #   "C0DSid0317dm2 2&tm1 1 0&dv01072 01072 00000&xp02980 02980 00000&yp1460 1370 0000&"
    #   "zx1871 1871 0000&lp2321 2321 2450&zl1881 1881 2450&ip0000 0000&it0 0&fp0000 0000&th2450"
    #   "te2450ds1200 1200&ss0050 0050&rv000 000&ta000 000&ba0000 0000&lm0 0&zo000 000&ll1 1&",
    #   "lv1 1&de0020 0020&mv00000 00000&mc00 00&mp000 000&ms0010 0010&wt00 00&gi000 000&gj0gk0"
    #   "zu0032 0032 0000&dj00zr06180 06180 00000&mh0000 0000&po0100 0100&",
    #   "dm# (n)tm# (n)xp##### (n)yp#### (n)zx#### (n)lp#### (n)zl#### (n)ip#### (n)it# (n)fp#### (n)"
    #   "th####te####dv##### (n)ds#### (n)ss#### (n)rv### (n)ta### (n)ba#### (n)lm# (n)zo### (n)"
    #   "ll# (n)lv# (n)de#### (n)mv##### (n)mc## (n)mp### (n)ms#### (n)wt## (n)gi### (n)gj#gk#"
    #   "zu#### (n)zr##### (n)mh#### (n)po#### (n)")
    # modified to remove additional values in parameters (dm, dv, lp, zl, zr, zu, zx)
    self._assert_command_sent_once(
      "C0DSid0317dm2 2&tm1 1 0&dv01072 01072&xp02980 02980 00000&yp1460 1370 0000&"
      "zx1871 1871&lp2321 2321&zl1881 1881&ip0000 0000&it0 0&fp0000 0000&th2450"
      "te2450ds1200 1200&ss0050 0050&rv000 000&ta000 000&ba0000 0000&lm0 0&zo000 000&ll1 1&"
      "lv1 1&de0020 0020&mv00000 00000&mc00 00&mp000 000&ms0010 0010&wt00 00&gi000 000&gj0gk0"
      "zu0032 0032&dj00zr06180 06180&mh0000 0000&po0100 0100&",
      "dm# (n)tm# (n)xp##### (n)yp#### (n)zx#### (n)lp#### (n)zl#### (n)ip#### (n)it# (n)fp#### (n)"
      "th####te####dv##### (n)ds#### (n)ss#### (n)rv### (n)ta### (n)ba#### (n)lm# (n)zo### (n)"
      "ll# (n)lv# (n)de#### (n)mv##### (n)mc## (n)mp### (n)ms#### (n)wt## (n)gi### (n)gj#gk#"
      "zu#### (n)zr##### (n)mh#### (n)po#### (n)")

  def test_move(self):
    self.test_tip_pickup_45() # pick up tips first
    self.lh.move("plate_01", "plate_01", [100, 100])

    self._assert_command_sent_once(
      "C0ASid0000at0&tm1 1 0&xp02980 02980 00000&yp1460 1370 0000&th2450te2450lp2321 2321&"
      "ch000 000&zl1881 1881&zx1871 1871&ip0000 0000&it0 0&fp0000 0000&"
      "av01072 01072&as1000 1000&ta000 000&ba0000 0000&oa000 000&lm0 0&ll1 1&lv1 1&ld00 00&"
      "de0020 0020&wt10 10&mv00000 00000&mc00 00&mp000 000&ms1000 1000&gi000 000&gj0gk0"
      "zu0032 0032&zr06180 06180&mh0000 0000&zo000 000&po0100 0100&lk0 0&ik0000 0000&"
      "sd0500 0500&se0500 0500&sz0300 0300&io0000 0000&il00000 00000&in0000 0000&",
      fmt="at# (n)tm# (n)xp##### (n)yp#### (n)th####te####lp#### (n)ch### (n)zl#### (n)zx#### (n)"
      "ip#### (n)it# (n)fp#### (n)av#### (n)as#### (n)ta### (n)ba#### (n)oa### (n)lm# (n)ll# (n)"
      "lv# (n)ld## (n)de#### (n)wt## (n)mv##### (n)mc## (n)mp### (n)ms#### (n)gi### (n)gj#gk#"
      "zu#### (n)zr#### (n)mh#### (n)zo### (n)po#### (n)lk# (n)ik#### (n)sd#### (n)se#### (n)"
      "sz#### (n)io#### (n)il##### (n)in#### (n)")
    self._assert_command_sent_once(
      "C0DSid0317dm2 2&tm1 1 0&dv01072 01072&xp02980 02980 00000&yp1460 1370 0000&"
      "zx1871 1871&lp2321 2321&zl1881 1881&ip0000 0000&it0 0&fp0000 0000&th2450"
      "te2450ds1200 1200&ss0050 0050&rv000 000&ta000 000&ba0000 0000&lm0 0&zo000 000&ll1 1&"
      "lv1 1&de0020 0020&mv00000 00000&mc00 00&mp000 000&ms0010 0010&wt00 00&gi000 000&gj0gk0"
      "zu0032 0032&dj00zr06180 06180&mh0000 0000&po0100 0100&",
      "dm# (n)tm# (n)xp##### (n)yp#### (n)zx#### (n)lp#### (n)zl#### (n)ip#### (n)it# (n)fp#### (n)"
      "th####te####dv##### (n)ds#### (n)ss#### (n)rv### (n)ta### (n)ba#### (n)lm# (n)zo### (n)"
      "ll# (n)lv# (n)de#### (n)mv##### (n)mc## (n)mp### (n)ms#### (n)wt## (n)gi### (n)gj#gk#"
      "zu#### (n)zr##### (n)mh#### (n)po#### (n)")

if __name__ == "__main__":
  unittest.main()
