import dataclasses
import json
import pathlib
import tempfile
import unittest
from typing import List, Optional, Tuple, cast

from pylabrobot.hamilton.star.device import RECORDING_STAR
from pylabrobot.hamilton.star.driver.configuration import read_configuration
from pylabrobot.hamilton.star.driver.features.head96 import Head96, Head96Configuration
from pylabrobot.hamilton.star.driver.features.x_arm import XArm
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import STARDeck
from pylabrobot.utils.configuration_json import to_jsonable

# The 96-head on the device this package ships a recording of.
RECORDED_HEAD96 = cast(
  Head96Configuration, read_configuration(RECORDING_STAR)["arms"]["left"]["head96"]
)


def declaring(**parts: object) -> str:
  """The shipped recording with parts swapped out, written where it can be read back.

  A declaration is read from a file and nothing else, so a test that needs a device no recording
  describes writes one. Everything not named here stays as the recorded STAR has it.

  Args:
    parts: `device` for the device itself, or a feature name for something the left arm carries.

  Returns:
    The path it was written to.
  """
  tree = json.loads(pathlib.Path(RECORDING_STAR).read_text())
  for name, part in parts.items():
    if name == "device":
      tree["device"] = to_jsonable(part)
    else:
      tree["arms"]["left"][name] = to_jsonable(part)
  written = pathlib.Path(tempfile.mkdtemp()) / "declared.json"
  written.write_text(json.dumps(tree))
  return str(written)


class TestDriveDefaults(unittest.IsolatedAsyncioTestCase):
  """Where the value a move uses when the caller names none comes from: what the head reported,
  falling back to what its firmware documents."""

  async def test_the_defaults_are_what_the_head_reported(self):
    """Discovery reads the four Y and Z drive parameters off the head, and the defaults answer with
    them. Read from a head declaring values its firmware does not, so a default that ignored the
    head and computed the documented one instead could not pass. Four distinct values, so a read
    stored under the wrong name fails this too."""
    declared = dataclasses.replace(
      RECORDED_HEAD96,
      y_drive_speed_firmware_reported=200.0,
      y_drive_acceleration_firmware_reported=300.0,
      z_drive_speed_firmware_reported=50.0,
      z_drive_acceleration_firmware_reported=250.0,
    )
    driver = STARSimulationDriver(
      deck=STARDeck(), declared_configuration_json=declaring(head96=declared)
    )
    await driver.setup()

    c = cast(Head96, driver.x_arm.head96).configuration
    self.assertEqual(
      (
        c.y_drive_speed_default,
        c.y_drive_acceleration_default,
        c.z_drive_speed_default,
        c.z_drive_acceleration_default,
      ),
      (200.0, 300.0, 50.0, 250.0),
    )

  async def test_the_96_head_takes_its_dispensing_and_squeezer_defaults_too(self):
    """`Head96.discover` reads four drive parameters on top of the Y and Z ones every head shares,
    and its defaults answer with what it reported for them. Apart from the test above because it
    covers the override rather than the base: the 384-head adds no reads of its own.

    Compared to a tenth rather than exactly: the drive counts in increments, so a value that does
    not fall on one comes back as the nearest that does - 400.0 mm/s reads back as 400.01. That is
    what a head does, and what the simulated one does now that its answer crosses the link and is
    decoded rather than handed over whole."""
    declared = dataclasses.replace(
      RECORDED_HEAD96,
      dispensing_drive_speed_firmware_reported=400.0,
      dispensing_drive_acceleration_firmware_reported=9000.0,
      squeezer_drive_speed_firmware_reported=12.0,
      squeezer_drive_acceleration_firmware_reported=50.0,
    )
    driver = STARSimulationDriver(
      deck=STARDeck(), declared_configuration_json=declaring(head96=declared)
    )
    await driver.setup()

    c = cast(Head96, driver.x_arm.head96).configuration
    for read, declared_value in zip(
      (
        c.dispensing_drive_speed_default,
        c.dispensing_drive_acceleration_default,
        c.squeezer_drive_speed_default,
        c.squeezer_drive_acceleration_default,
      ),
      (400.0, 9000.0, 12.0, 50.0),
    ):
      self.assertAlmostEqual(read, declared_value, places=1)

  async def test_a_head_that_will_not_say_keeps_what_its_firmware_documents(self):
    """A head that refuses the read leaves discovery with nothing to record, and the defaults fall
    back to the increments its firmware documents rather than the read failing setup. Driven
    through `discover` alone: the rest of setup moves the head, and reads these same parameters to
    do it."""
    driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
    head = cast(Head96, cast(XArm, driver.left_x_arm).head96)

    async def refuse(parameter: str) -> float:
      raise RuntimeError("this head does not answer for its drives")

    head.request_drive_parameter = refuse  # type: ignore[method-assign]
    await head.discover()

    c = head.configuration
    self.assertEqual(
      (
        c.y_drive_speed_firmware_reported,
        c.y_drive_acceleration_firmware_reported,
        c.z_drive_speed_firmware_reported,
        c.z_drive_acceleration_firmware_reported,
      ),
      (None, None, None, None),
    )
    self.assertEqual(
      (
        c.y_drive_speed_default,
        c.y_drive_acceleration_default,
        c.z_drive_speed_default,
        c.z_drive_acceleration_default,
      ),
      (390.62, 546.88, 85.0, 400.0),
    )


async def head96_on_a_deck() -> Tuple[Head96, List[str]]:
  """A simulated 96-head, and every tip command sent from here on.

  Returns:
    The head, and the `C0 TT`, `EP` and `ER` commands as sent, without their ids.
  """
  driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
  await driver.setup()
  head = cast(Head96, driver.x_arm.head96)
  sent: List[str] = []
  log = driver._log_exchange  # type: ignore[attr-defined]

  def recorded(written: str, read: Optional[str]) -> None:
    if written[:4] in ("C0TT", "C0EP", "C0ER"):
      sent.append(written)
    log(written, read)

  driver._log_exchange = recorded  # type: ignore[method-assign]
  return head, sent


class TestHead96TipHandling(unittest.IsolatedAsyncioTestCase):
  """A rack of tips moves from its spots onto the head's shafts, and back.

  With tip tracking on, which is what makes a spot give up its tip.
  """

  def setUp(self):
    from pylabrobot.resources import set_tip_tracking

    set_tip_tracking(True)
    self.addCleanup(set_tip_tracking, False)

  async def test_tips_go_onto_the_shaft_above_their_spot_and_back_or_to_the_trash(self):
    from pylabrobot.resources.hamilton import TIP_CAR_480_A00, hamilton_96_tiprack_300uL

    head, _ = await head96_on_a_deck()
    deck = head._driver.deck
    assert deck is not None and head.resource is not None
    carrier = TIP_CAR_480_A00("tip_carrier")
    carrier[0] = rack = hamilton_96_tiprack_300uL("rack")
    deck.assign_child_resource(carrier, track=16)
    h12 = rack.get_item("H12").tip
    assert h12 is not None

    await head.pick_up_tips(rack)
    self.assertIs(head.resource.get_item("H12").tip, h12)
    self.assertFalse(any(spot.has_tip() for spot in rack.get_all_items()))

    await head.drop_tips(rack)
    self.assertIs(rack.get_item("H12").tip, h12)
    self.assertFalse(any(shaft.has_tip() for shaft in head.resource.get_all_items()))

    await head.pick_up_tips(rack)
    await head.drop_tips()
    self.assertIsNone(h12.parent)
    self.assertFalse(any(spot.has_tip() for spot in rack.get_all_items()))


class TestHead96TipsGroundTruth(unittest.IsolatedAsyncioTestCase):
  """The 96-head's tip commands are what Hamilton's own software sends, for each rack on the holders
  it was recorded on."""

  def setUp(self):
    from pylabrobot.resources import set_tip_tracking

    set_tip_tracking(True)
    self.addCleanup(set_tip_tracking, False)

  async def assert_sent(
    self, head: Head96, sent: List[str], rack, definition: str, xs: str, yh: int, za: str
  ):
    sent.clear()
    await head.pick_up_tips(rack)
    await head.drop_tips(rack)
    tt = sent[0][4:8]
    self.assertEqual(
      sent,
      [
        f"C0TT{tt}{definition}",
        f"C0EPxs{xs}xd0yh{yh}{tt}wu0za{za}zh2450ze2450",
        f"C0ERxs{xs}xd0yh{yh}za{za}zh2450ze2450",
      ],
    )

  async def test_nested_tip_racks_ground_truth(self):
    from pylabrobot.resources.hamilton import (
      hamilton_96_tiprack_10uL_NTR,
      hamilton_96_tiprack_50uL_NTR,
      hamilton_96_tiprack_300uL_NTR,
      hamilton_mfx_carrier_L5_base,
      hamilton_mfx_module_tiprackholder_ntr,
      hamilton_tip_carrier_L5_ntr_a00,
    )

    # rack, tip definition (without its index), NTR site
    tips = [
      (hamilton_96_tiprack_10uL_NTR, "tf0tl0219tv00150tg1tu0", 0),
      (hamilton_96_tiprack_50uL_NTR, "tf0tl0424tv00650tg2tu0", 1),
      (hamilton_96_tiprack_300uL_NTR, "tf0tl0519tv04000tg2tu0", 2),
    ]
    for holder_name in ("ntr4_module", "ntr_carrier"):
      for rack_fn, definition, site in tips:
        with self.subTest(holder=holder_name, tip=rack_fn.__name__):
          head, sent = await head96_on_a_deck()
          deck = head._driver.deck
          assert deck is not None
          rack = rack_fn(rack_fn.__name__)
          if holder_name == "ntr4_module":
            module = hamilton_mfx_module_tiprackholder_ntr("ntr4_module")
            deck.assign_child_resource(
              hamilton_mfx_carrier_L5_base("mfx_carrier", modules={3: module}),
              location=Coordinate(932.5, 63, 100),
            )
            module.assign_child_resource(rack)
            xs, yh = "09505", 4340
          else:
            carrier = hamilton_tip_carrier_L5_ntr_a00("ntr_carrier")
            deck.assign_child_resource(carrier, location=Coordinate(752.5, 63, 100))
            carrier.sites[site].assign_child_resource(rack)
            xs, yh = "07704", 1458 + 960 * site
          await self.assert_sent(head, sent, rack, definition, xs, yh, "1840")

  async def test_framed_tip_racks_on_tip_carrier_ground_truth(self):
    from pylabrobot.resources.hamilton import (
      TIP_CAR_480BC_A00,
      hamilton_96_tiprack_10uL,
      hamilton_96_tiprack_50uL,
      hamilton_96_tiprack_300uL,
      hamilton_96_tiprack_300uL_filter_slim,
      hamilton_96_tiprack_1000uL_filter,
    )

    # rack, carrier x, site, tip definition (without its index)
    tips = [
      (hamilton_96_tiprack_10uL, 437.5, 0, "tf0tl0219tv00150tg1tu0"),
      (hamilton_96_tiprack_50uL, 437.5, 1, "tf0tl0424tv00650tg2tu0"),
      (hamilton_96_tiprack_300uL, 572.5, 0, "tf0tl0519tv04000tg2tu0"),
      (hamilton_96_tiprack_1000uL_filter, 572.5, 1, "tf1tl0871tv10650tg3tu0"),
      (hamilton_96_tiprack_300uL_filter_slim, 572.5, 2, "tf1tl0870tv03450tg3tu0"),
    ]
    for rack_fn, carrier_x, site, definition in tips:
      with self.subTest(tip=rack_fn.__name__):
        head, sent = await head96_on_a_deck()
        deck = head._driver.deck
        assert deck is not None
        carrier = TIP_CAR_480BC_A00("tip_carrier")
        deck.assign_child_resource(carrier, location=Coordinate(carrier_x, 63, 100))
        carrier[site] = rack = rack_fn(rack_fn.__name__)
        # A1 17.9 mm right of the carrier, 145.8 mm back on site 0, sites 96 mm apart (0.1 mm)
        xs, yh = f"{round(carrier_x * 10) + 179:05}", 1458 + 960 * site
        await self.assert_sent(head, sent, rack, definition, xs, yh, "2164")

  async def test_framed_tip_racks_on_mfx_tip_module_ground_truth(self):
    from pylabrobot.resources.hamilton import (
      hamilton_96_tiprack_10uL,
      hamilton_96_tiprack_50uL,
      hamilton_96_tiprack_300uL,
      hamilton_96_tiprack_300uL_filter_slim,
      hamilton_96_tiprack_1000uL_filter,
      hamilton_mfx_carrier_L5_base,
      hamilton_mfx_module_tiprackholder_standard,
    )

    # rack, MFX slot, tip definition (without its index)
    tips = [
      (hamilton_96_tiprack_10uL, 0, "tf0tl0219tv00150tg1tu0"),
      (hamilton_96_tiprack_50uL, 1, "tf0tl0424tv00650tg2tu0"),
      (hamilton_96_tiprack_300uL, 4, "tf0tl0519tv04000tg2tu0"),
      (hamilton_96_tiprack_1000uL_filter, 0, "tf1tl0871tv10650tg3tu0"),
      (hamilton_96_tiprack_300uL_filter_slim, 1, "tf1tl0870tv03450tg3tu0"),
    ]
    for rack_fn, slot, definition in tips:
      with self.subTest(tip=rack_fn.__name__):
        head, sent = await head96_on_a_deck()
        deck = head._driver.deck
        assert deck is not None
        module = hamilton_mfx_module_tiprackholder_standard("tip_module")
        deck.assign_child_resource(
          hamilton_mfx_carrier_L5_base("mfx_carrier", modules={slot: module}),
          location=Coordinate(752.5, 63, 100),
        )
        rack = rack_fn(rack_fn.__name__)
        module.assign_child_resource(rack)
        # A1 146.0 mm back on slot 0, slots 96 mm apart (0.1 mm)
        await self.assert_sent(head, sent, rack, definition, "07705", 1460 + 960 * slot, "2162")
