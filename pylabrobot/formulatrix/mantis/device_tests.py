"""Mantis device tests: the parts tree, the chips placed checked against the tags read, a chip
picked up and dropped, shown on the LED bar, primed, and dispensed with into wells."""

import asyncio
import copy
import json
import os
import tempfile
import unittest
from typing import Any, Dict, List, Tuple, Type
from unittest import mock

from pylabrobot.formulatrix.mantis import (
  Mantis,
  MantisDevice,
  mantis_high_volume_hi_fi_chip,
  mantis_low_volume_hi_fi_chip,
)
from pylabrobot.formulatrix.mantis import device as device_module
from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.arm_kinematics import compute_device_position
from pylabrobot.formulatrix.mantis.driver.errors import (
  MantisConfigurationError,
  MantisCoordinateError,
  MantisMotionError,
  MantisPrimeError,
  MantisTimeoutError,
  PlateClampError,
)
from pylabrobot.formulatrix.mantis.driver.features import led_bar
from pylabrobot.formulatrix.mantis.driver.features.led_bar_tests import (
  CYAN,
  GREEN,
  MAGENTA,
  OFF,
  ORANGE,
  RED,
  VIOLET,
  WHITE,
  bar_states,
  color_write,
  led_bar_frames,
  make_led_bar_answers,
)
from pylabrobot.formulatrix.mantis.driver.features.plate_clamp import UNCLAMPING_DONE
from pylabrobot.formulatrix.mantis.driver.features.pump import Pump
from pylabrobot.formulatrix.mantis.driver.features.pump_tests import LOW_VOLUME_HI_FI_SEQUENCES
from pylabrobot.formulatrix.mantis.driver.features.rfid import RFID_READ_TAG_COMMAND
from pylabrobot.formulatrix.mantis.driver.features.rfid_tests import HIGH_VOLUME_HI_FI_TAG_REPLY
from pylabrobot.formulatrix.mantis.driver.master_tests import (
  DriverTestCase,
  FakeLink,
  FakeReader,
  make_answers,
  pass_at_once,
)
from pylabrobot.formulatrix.mantis.driver.oem_config_tests import (
  INPUT_VOLUMES_BLOCK,
  PUMP_SEQUENCE_VOLUMES_BLOCK,
  SYNTHETIC_CHIP_CHANGER_BLOCK,
  SYNTHETIC_IDLE_POSITION_ROUTE,
  SYNTHETIC_STAGE_BLOCK,
  SYNTHETIC_WASTE_ROUTE,
)
from pylabrobot.formulatrix.mantis.driver.simulator import (
  MantisSimulationDriver,
  MantisSimulationDropSensor,
  MantisSimulationRfid,
)
from pylabrobot.formulatrix.mantis.driver.stage_transform import (
  REFERENCE_HEIGHT,
  compute_arm_frame_position,
)
from pylabrobot.formulatrix.mantis.liquid_classes import mantis_mapping
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import Coordinate
from pylabrobot.resources.corning import cor_96_wellplate_360uL_Fb

RESOURCE_MODEL = os.path.join(os.path.dirname(__file__), "resource_model")


class MantisTests(unittest.TestCase):
  def make_device(self) -> MantisDevice:
    return Mantis(io=mock.create_autospec(FTDI, instance=True))

  def test_every_part_has_its_model_file(self):
    device = self.make_device()
    for resource in (device, device.left_chip_rack, device.right_chip_rack):
      with self.subTest(resource=resource.name):
        self.assertTrue(os.path.isfile(os.path.join(RESOURCE_MODEL, f"{resource.model}.glb")))

  def test_simulation_refuses_a_link(self):
    with self.assertRaises(ValueError):
      Mantis(simulation=True, io=mock.create_autospec(FTDI, instance=True))


# Interface A: the tag of a low volume Hi-Fi chip in slot 6, antenna 5, as a Mantis v4's reader sent
# it, chunked as it arrived.
LOW_VOLUME_HI_FI_SLOT_6_TAG_REPLY = [
  b"Request mode.\r\n",
  b"[00000000003532534C32303232202020202020202008000000353230323730313139342E30202034323031"
  b"2E30202020330D000000353731322020372E2020202020202020783C0000312C0000574900006B49000000"
  b"0000000000000000000000]\r\n",
]
SLOT_6_ANTENNA = 5


class ChipCheckTests(unittest.IsolatedAsyncioTestCase):
  async def set_up(self, *, chips: Dict[int, str], tags: Dict[int, List[bytes]]) -> MantisDevice:
    self.reader = FakeReader(tags=tags)
    link = FakeLink(answers=make_answers(), reader=self.reader)
    mantis = Mantis(
      io=link.io, rfid_io=self.reader.io, declared_configuration_json=RECORDING_MANTIS_V4
    )
    factories = {
      "low_volume_hi_fi": mantis_low_volume_hi_fi_chip,
      "high_volume_hi_fi": mantis_high_volume_hi_fi_chip,
    }
    for slot, chip_type in chips.items():
      mantis.chip_spots[slot].assign_child_resource(factories[chip_type](name=f"chip_{slot}"))
    mantis.driver.default_read_timeout = 0.25
    holds = mock.patch.object(Pump, "_sleep", pass_at_once)  # the pump's holds pass at once
    holds.start()
    self.addCleanup(holds.stop)
    self.addAsyncCleanup(mantis.stop)
    return mantis

  def tag_reads(self) -> int:
    return self.reader.written.count(RFID_READ_TAG_COMMAND)

  async def test_a_chip_of_the_type_read_sets_up(self):
    mantis = await self.set_up(
      chips={6: "low_volume_hi_fi"}, tags={SLOT_6_ANTENNA: LOW_VOLUME_HI_FI_SLOT_6_TAG_REPLY}
    )
    await mantis.setup(skip_arm=True, skip_self_test=True)
    self.assertEqual(self.tag_reads(), 6)

  async def test_no_chip_placed_reads_no_tag(self):
    mantis = await self.set_up(chips={}, tags={})
    await mantis.setup(skip_arm=True, skip_self_test=True)
    self.assertEqual(self.tag_reads(), 0)

  async def test_another_type_or_no_chip_refuses_and_stops(self):
    cases = [
      (
        {6: "high_volume_hi_fi"},
        {SLOT_6_ANTENNA: LOW_VOLUME_HI_FI_SLOT_6_TAG_REPLY},
        "of type 'low_volume_hi_fi'",
      ),
      (
        {1: "high_volume_hi_fi", 2: "high_volume_hi_fi"},
        {SLOT_6_ANTENNA: LOW_VOLUME_HI_FI_SLOT_6_TAG_REPLY},
        r"slots \[1, 2\], which hold no chip \(RFID\); chips were read in slots \[6\]",
      ),
    ]
    for chips, tags, message in cases:
      with self.subTest(message=message):
        mantis = await self.set_up(chips=chips, tags=tags)
        with self.assertRaisesRegex(MantisConfigurationError, message):
          await mantis.setup(skip_arm=True, skip_self_test=True)
        self.assertIsNone(mantis.driver.rfid)  # stopped: the links are closed

  async def test_one_chip_placed_in_an_empty_slot_may_be_on_the_head(self):
    mantis = await self.set_up(
      chips={1: "high_volume_hi_fi"}, tags={SLOT_6_ANTENNA: LOW_VOLUME_HI_FI_SLOT_6_TAG_REPLY}
    )
    await mantis.setup(skip_arm=True, skip_self_test=True)
    for move in (mantis.pick_up_chip(slot=1), mantis.drop_chip(slot=1)):
      with self.assertRaisesRegex(MantisMotionError, "slot 1 may be on the head"):
        await move
    reads = self.tag_reads()
    self.assertTrue(await mantis.request_chip_attached())  # slot 1 reads no tag
    self.assertEqual(self.tag_reads(), reads + 1)
    chip = mantis.chip_spots[1].resource
    mantis.confirm_chip_attached(attached=True)
    self.assertIs(mantis.attached_chip, chip)
    self.assertIs(chip.parent, mantis.arm_links["right_long_arm"])  # type: ignore[union-attr]
    self.assertIsNone(mantis.chip_spots[1].resource)
    with self.assertRaisesRegex(ValueError, "no chip may be on the head"):
      mantis.confirm_chip_attached(attached=True)

  async def test_a_chip_on_the_head_needs_its_chip_spot(self):
    mantis = await self.set_up(chips={1: "high_volume_hi_fi"}, tags={})
    await mantis.setup(skip_arm=True, skip_self_test=True)
    mantis.chip_spots[1].resource.unassign()  # type: ignore[union-attr]
    with self.assertRaisesRegex(MantisConfigurationError, r"place the chip on the head in"):
      mantis.confirm_chip_attached(attached=True)
    self.assertIsNone(mantis.attached_chip)
    with self.assertRaisesRegex(MantisMotionError, "slot 1 may be on the head"):
      await mantis.pick_up_chip(slot=1)

  async def test_a_chip_taken_out_by_hand_stays_in_its_spot(self):
    mantis = await self.set_up(chips={1: "high_volume_hi_fi"}, tags={})
    await mantis.setup(skip_arm=True, skip_self_test=True)
    mantis.confirm_chip_attached(attached=False)
    self.assertIsNone(mantis.attached_chip)
    self.assertIsNotNone(mantis.chip_spots[1].resource)
    self.assertFalse(await mantis.request_chip_attached())  # nothing to read

  async def test_a_chip_of_the_type_read_in_slot_2(self):
    mantis = await self.set_up(
      chips={2: "high_volume_hi_fi"}, tags={1: HIGH_VOLUME_HI_FI_TAG_REPLY}
    )
    await mantis.setup(skip_arm=True, skip_self_test=True)
    self.assertEqual(self.tag_reads(), 6)


class SimulatedChipCheckTests(unittest.IsolatedAsyncioTestCase):
  async def test_the_simulated_reader_reads_the_chips_placed(self):
    mantis = Mantis(simulation=True)
    mantis.chip_spots[6].assign_child_resource(mantis_low_volume_hi_fi_chip(name="chip"))
    await mantis.setup()
    self.addAsyncCleanup(mantis.stop)
    assert mantis.rfid is not None
    tags = await mantis.rfid.request_tags()
    self.assertEqual(tags[6]["chip_type"], "low_volume_hi_fi")  # type: ignore[index]
    self.assertEqual(tags[6]["chip_type_code"], 0x08)  # type: ignore[index]
    self.assertEqual([tags[slot] for slot in range(1, 6)], [None] * 5)


class SimulatedChipMoveTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recording = json.load(f)
    path = os.path.join(directory.name, "declared.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump(
        {
          **recording,
          "chip_changer": SYNTHETIC_CHIP_CHANGER_BLOCK,
          "stations": {"idle_position": SYNTHETIC_IDLE_POSITION_ROUTE},
          "chip_sequences": {"low_volume_hi_fi": LOW_VOLUME_HI_FI_SEQUENCES},
        },
        f,
      )
    self.mantis = Mantis(simulation=True, declared_configuration_json=path)
    self.chip = mantis_low_volume_hi_fi_chip(name="chip_1")
    self.mantis.chip_spots[1].assign_child_resource(self.chip)
    await self.mantis.setup()
    self.addAsyncCleanup(self.mantis.stop)

  def get_rfid(self) -> MantisSimulationRfid:
    assert isinstance(self.mantis.rfid, MantisSimulationRfid)
    return self.mantis.rfid

  async def assert_nothing_moves(self, move, error, message: str) -> None:
    driver = self.mantis.driver
    item, positions = driver._next_item, dict(driver.arm._positions)  # type: ignore[union-attr]
    with self.assertRaisesRegex(error, message):
      await move
    self.assertEqual(driver._next_item, item)  # nothing queued, no valve and no move
    self.assertEqual(driver.arm._positions, positions)  # type: ignore[union-attr]

  async def test_a_chip_moves_onto_the_head_and_back(self):
    await self.mantis.pick_up_chip(slot=1)
    self.assertIs(self.mantis.attached_chip, self.chip)
    self.assertIs(self.chip.parent, self.mantis.arm_links["right_long_arm"])
    self.assertIsNone(await self.get_rfid().request_tag(slot=1))
    self.assertTrue(await self.mantis.request_chip_attached())
    assert self.mantis.arm is not None
    self.assertTrue(await self.mantis.arm.request_is_parked())
    await self.mantis.drop_chip(slot=1)
    self.assertIsNone(self.mantis.attached_chip)
    self.assertIs(self.mantis.chip_spots[1].resource, self.chip)
    self.assertFalse(await self.mantis.request_chip_attached())
    self.assertTrue(await self.mantis.arm.request_is_parked())

  async def test_a_pick_up_is_refused_before_anything_moves(self):
    self.mantis.chip_spots[2].assign_child_resource(mantis_low_volume_hi_fi_chip(name="chip_2"))
    self.get_rfid().chip_types = {1: "high_volume_hi_fi"}
    cases = [
      (self.mantis.pick_up_chip(slot=1), MantisConfigurationError, "of type 'high_volume_hi_fi'"),
      (
        self.mantis.pick_up_chip(slot=2),
        MantisConfigurationError,
        r"slot 2 holds no chip \(RFID\)",
      ),
      (self.mantis.pick_up_chip(slot=4), MantisConfigurationError, "no chip is placed in slot 4"),
      (self.mantis.pick_up_chip(slot=7), ValueError, "slot must be one of"),
    ]
    for move, error, message in cases:
      with self.subTest(message=message):
        await self.assert_nothing_moves(move=move, error=error, message=message)
    self.mantis.chip_spots[3].assign_child_resource(mantis_low_volume_hi_fi_chip(name="chip_3"))
    await self.assert_nothing_moves(
      move=self.mantis.pick_up_chip(slot=3), error=ValueError, message="an LC3's positions"
    )

  async def test_a_head_away_from_idle_moves_nothing(self):
    arm = self.mantis.arm
    assert arm is not None
    arm._absolute_positions[0] += 10.0  # type: ignore[attr-defined]
    await self.assert_nothing_moves(
      move=self.mantis.pick_up_chip(slot=1), error=MantisMotionError, message="park"
    )

  async def test_a_drop_is_only_into_the_chips_own_empty_slot(self):
    await self.assert_nothing_moves(
      move=self.mantis.drop_chip(slot=1), error=MantisMotionError, message="no chip is on the head"
    )
    await self.mantis.pick_up_chip(slot=1)
    cases = [
      (self.mantis.drop_chip(slot=2), MantisMotionError, "dropped only there, not into slot 2"),
      (self.mantis.pick_up_chip(slot=1), MantisMotionError, "is on the head; drop_chip"),
    ]
    for move, error, message in cases:
      with self.subTest(message=message):
        await self.assert_nothing_moves(move=move, error=error, message=message)
    self.get_rfid().chip_types = {1: "low_volume_hi_fi"}
    await self.assert_nothing_moves(
      move=self.mantis.drop_chip(slot=1),
      error=MantisConfigurationError,
      message="not dropped there",
    )

  async def test_stop_returns_a_chip_on_the_head(self):
    await self.mantis.pick_up_chip(slot=1)
    await self.mantis.stop()
    self.assertIsNone(self.mantis.attached_chip)
    self.assertIs(self.mantis.chip_spots[1].resource, self.chip)

  async def test_a_failed_move_leaves_the_chip_possibly_on_the_head(self):
    arm = self.mantis.arm
    assert arm is not None
    with mock.patch.object(arm, "_play_queued_move_groups", side_effect=MantisMotionError("fault")):
      with self.assertRaisesRegex(MantisMotionError, "fault"):
        await self.mantis.pick_up_chip(slot=1)
    self.assertIsNone(self.mantis.attached_chip)
    await self.assert_nothing_moves(
      move=self.mantis.pick_up_chip(slot=1), error=MantisMotionError, message="may be on the head"
    )
    self.mantis.confirm_chip_attached(attached=True)
    await self.mantis.drop_chip(slot=1)
    self.assertIs(self.mantis.chip_spots[1].resource, self.chip)

  async def test_a_v3_handles_no_chip(self):
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recording = json.load(f)
    recording["device"]["firmware_versions"]["11"] = None  # a v3: board 11 does not answer
    recording["device"]["generation"] = "v3"
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "recording.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump(recording, f)
      mantis = Mantis(simulation=True, declared_configuration_json=path)
      await mantis.setup()
      self.addAsyncCleanup(mantis.stop)
    with self.assertRaisesRegex(MantisConfigurationError, "a v3's chip changer is not supported"):
      await mantis.pick_up_chip(slot=1)


class ChipLedBarTestCase(DriverTestCase):
  async def asyncSetUp(self):
    self.reader = FakeReader(tags={SLOT_6_ANTENNA: LOW_VOLUME_HI_FI_SLOT_6_TAG_REPLY})
    self.link = FakeLink(answers=make_led_bar_answers(), reader=self.reader)
    self.mantis = Mantis(
      io=self.link.io, rfid_io=self.reader.io, declared_configuration_json=RECORDING_MANTIS_V4
    )
    self.mantis.chip_spots[6].assign_child_resource(mantis_low_volume_hi_fi_chip(name="chip_6"))
    self.mantis.driver.default_read_timeout = 0.25
    holds = mock.patch.object(Pump, "_sleep", pass_at_once)
    holds.start()
    self.addCleanup(holds.stop)
    await self.mantis.setup(skip_arm=True, skip_self_test=True)
    self.addAsyncCleanup(self.mantis.stop)
    arm, pump = self.mantis.driver.arm, self.mantis.driver.pump
    assert arm is not None and pump is not None
    # The moves and the valve sequences are tested with their own replies; here they pass at once.
    self.play_moves = mock.AsyncMock()
    self.play_valves = mock.AsyncMock()
    for target, name, value in (
      (arm, "_get_chip_changer_moves", mock.Mock(return_value=[])),
      (arm, "request_is_parked", mock.AsyncMock(return_value=True)),
      (arm, "_play_queued_move_groups", self.play_moves),
      (pump, "_get_valve_sequence", mock.Mock(return_value=[])),
      (pump, "_play_valve_sequence", self.play_valves),
    ):
      patcher = mock.patch.object(target, name, value)
      patcher.start()
      self.addCleanup(patcher.stop)
    self.sent_before = len(self.link.written)

  def states(self):
    return bar_states(written=self.link.written[self.sent_before :])


class ChipLedBarTests(ChipLedBarTestCase):
  async def test_a_pick_up_and_a_drop_show_their_colours_around_the_valves_and_moves(self):
    shown_at_valves = []
    self.play_valves.side_effect = lambda **_: shown_at_valves.append(self.states()[-1])
    await self.mantis.pick_up_chip(slot=6)
    self.reader.tags = {}  # the slot reads empty with its chip on the head
    await self.mantis.drop_chip(slot=6)
    self.assertEqual(self.states(), [CYAN, "idle", VIOLET, "idle"])
    self.assertEqual(shown_at_valves, [CYAN, VIOLET])
    self.assertEqual(self.play_moves.await_count, 2)

  async def test_a_refused_pick_up_shows_nothing(self):
    self.reader.tags = {}
    with self.assertRaisesRegex(MantisConfigurationError, r"slot 6 holds no chip \(RFID\)"):
      await self.mantis.pick_up_chip(slot=6)
    self.assertEqual(led_bar_frames(written=self.link.written[self.sent_before :]), [])

  async def test_a_failed_pick_up_leaves_red(self):
    self.play_moves.side_effect = MantisMotionError("fault")
    with self.assertRaisesRegex(MantisMotionError, "fault"):
      await self.mantis.pick_up_chip(slot=6)
    self.assertEqual(self.states(), [CYAN, RED])

  async def test_a_led_bar_fault_neither_fails_nor_masks_a_chip_move(self):
    for color in (CYAN, RED):
      del self.link.answers[color_write(colors=[color] * 20)]
    with self.assertLogs("pylabrobot.formulatrix.mantis.driver.features.led_bar", "WARNING"):
      await self.mantis.pick_up_chip(slot=6)
      self.assertIsNotNone(self.mantis.attached_chip)
      self.play_moves.side_effect = MantisMotionError("fault")
      self.reader.tags = {}
      with self.assertRaisesRegex(MantisMotionError, "fault"):
        await self.mantis.drop_chip(slot=6)

  async def test_a_pick_up_and_a_drop_show_their_colour_attributes(self):
    assert self.mantis.led_bar is not None
    self.mantis.led_bar.pick_up_chip_color = "magenta"
    self.mantis.led_bar.drop_chip_color = "#FF4000"
    await self.mantis.pick_up_chip(slot=6)
    self.reader.tags = {}
    await self.mantis.drop_chip(slot=6)
    self.assertEqual(self.states(), [MAGENTA, "idle", ORANGE, "idle"])

  async def test_a_chip_colour_attribute_refuses_what_is_no_colour(self):
    bar = self.mantis.led_bar
    assert bar is not None
    for color in ("purple", "#FF00F", 0x10000):
      with self.subTest(color=color):
        with self.assertRaises(ValueError):
          bar.pick_up_chip_color = color
        with self.assertRaises(ValueError):
          bar.drop_chip_color = color
    self.assertEqual([bar.pick_up_chip_color, bar.drop_chip_color], ["#00FFFF", "#8000FF"])

  async def test_without_a_led_bar_a_pick_up_and_a_drop_show_nothing(self):
    self.mantis.driver.led_bar = None
    await self.mantis.pick_up_chip(slot=6)
    self.reader.tags = {}
    await self.mantis.drop_chip(slot=6)
    self.assertIsNone(self.mantis.attached_chip)
    self.assertEqual(led_bar_frames(written=self.link.written[self.sent_before :]), [])


class SimulatedPrimeTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recording = json.load(f)
    path = os.path.join(directory.name, "declared.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump(
        {
          **recording,
          "chip_changer": SYNTHETIC_CHIP_CHANGER_BLOCK,
          "stations": {
            "idle_position": SYNTHETIC_IDLE_POSITION_ROUTE,
            "waste": SYNTHETIC_WASTE_ROUTE,
          },
          "chip_sequences": {"low_volume_hi_fi": LOW_VOLUME_HI_FI_SEQUENCES},
          "pump_sequence_volumes": PUMP_SEQUENCE_VOLUMES_BLOCK,
          "input_volumes": INPUT_VOLUMES_BLOCK,
        },
        f,
      )
    self.mantis = Mantis(simulation=True, declared_configuration_json=path)
    self.chip = mantis_low_volume_hi_fi_chip(name="chip_1")
    self.mantis.chip_spots[1].assign_child_resource(self.chip)
    await self.mantis.setup()
    self.addAsyncCleanup(self.mantis.stop)
    assert self.mantis.arm is not None and self.mantis.pump is not None
    self.arm, self.pump = self.mantis.arm, self.mantis.pump
    sensor = self.mantis.drop_sensor
    assert isinstance(sensor, MantisSimulationDropSensor)
    self.sensor = sensor

  def spy_drops(self) -> mock.MagicMock:
    spy = mock.patch.object(self.pump, "_play_drops", wraps=self.pump._play_drops)
    played = spy.start()
    self.addCleanup(spy.stop)
    return played

  async def assert_primed_and_back_at_idle(self) -> None:
    self.assertTrue(await self.arm.request_is_parked())
    self.assertFalse(await self.pump.request_pump_on())
    self.assertIs(self.mantis.attached_chip, self.chip)

  async def test_by_volume_small_then_large_drops(self):
    await self.mantis.pick_up_chip(slot=1)
    played = self.spy_drops()
    await self.mantis.prime_chip(drop_detection=False)  # the install's 100.4 uL
    drops = played.call_args_list[0].kwargs["states"]
    small = [tuple(state) for state in LOW_VOLUME_HI_FI_SEQUENCES["dispense_100nl"]]
    large = [tuple(state) for state in LOW_VOLUME_HI_FI_SEQUENCES["dispense_500nl"]]
    self.assertEqual(drops, small * 4 + large * 200)
    self.assertEqual(played.call_args_list[1].kwargs["states"], [])  # its post-prime
    self.assertTrue(self.sensor.drop_detected)
    await self.assert_primed_and_back_at_idle()

  async def test_by_drops_until_enough_in_a_row(self):
    await self.mantis.pick_up_chip(slot=1)
    played = self.spy_drops()
    await self.mantis.prime_chip()  # with a drop sensor and no volume: by drops
    cycles = [call.kwargs["states"] for call in played.call_args_list[:-1]]
    self.assertEqual(cycles, [[(18, 104), (19, 14), (17, 15), (29, 19), (31, 13)]] * 5)
    self.assertEqual(self.sensor.consecutive_drops, self.mantis.default_consecutive_drops)
    await self.assert_primed_and_back_at_idle()

  async def assert_nothing_moves(self, prime, error, message: str) -> None:
    driver = self.mantis.driver
    item, positions = driver._next_item, dict(self.arm._positions)  # type: ignore[attr-defined]
    with self.assertRaisesRegex(error, message):
      await prime
    self.assertEqual(driver._next_item, item)  # nothing queued, no valve and no move
    self.assertEqual(self.arm._positions, positions)  # type: ignore[attr-defined]
    self.assertFalse(await self.pump.request_pump_on())

  async def test_refused_before_anything_moves(self):
    await self.assert_nothing_moves(
      prime=self.mantis.prime_chip(), error=MantisMotionError, message="no chip is on the head"
    )
    await self.mantis.pick_up_chip(slot=1)
    another_chips_class = mantis_mapping[("1 cP", "high_volume_hi_fi")]
    positive_vacuum, negative_pressure = (
      copy.copy(mantis_mapping[("1 cP", "low_volume_hi_fi")]) for _ in range(2)
    )
    positive_vacuum.prime_vacuum = 1.0
    negative_pressure.prime_pressure = -1.0
    cases: List[Tuple[Dict[str, Any], Type[Exception], str]] = [
      ({"volume": 5.0, "drop_detection": True}, ValueError, "takes no volume"),
      ({"input_source": "tube", "drop_detection": True}, ValueError, "and no input_source"),
      ({"drop_detection": False, "max_cycles": 3}, ValueError, "for a prime that stops on drops"),
      ({"max_cycles": 0}, ValueError, "max_cycles must be 1 or more"),
      ({"consecutive_drops": 0}, ValueError, "consecutive_drops must be 1 or more"),
      ({"volume": 0.0}, ValueError, "volume must be above 0"),
      ({"volume": 1.0, "input_source": "bottle"}, ValueError, "input_source must be one of"),
      ({"liquid_class": another_chips_class}, ValueError, "the liquid class is for"),
      ({"liquid_class": positive_vacuum}, ValueError, "prime_vacuum must be 0 or below"),
      ({"liquid_class": negative_pressure}, ValueError, "prime_pressure must be 0 or above"),
      (
        {"drop_detection": False, "input_source": "pipette_tip"},
        MantisConfigurationError,
        "no pipette_tip prime volume",
      ),
    ]
    for kwargs, error, message in cases:
      with self.subTest(message=message):
        await self.assert_nothing_moves(
          prime=self.mantis.prime_chip(**kwargs), error=error, message=message
        )
    self.arm._absolute_positions[0] += 10.0  # type: ignore[attr-defined]
    await self.assert_nothing_moves(
      prime=self.mantis.prime_chip(), error=MantisMotionError, message="park"
    )

  async def test_no_drop_seen_raises_with_the_pump_stopped(self):
    cases: List[Tuple[Dict[str, Any], str]] = [
      ({"volume": 1.0}, "the drop sensor saw none"),
      ({"max_cycles": 3}, "in 3 prime cycles"),
    ]
    for kwargs, message in cases:
      with self.subTest(message=message):
        await self.asyncSetUp()
        await self.mantis.pick_up_chip(slot=1)
        played = self.spy_drops()
        with mock.patch.object(self.sensor, "_update_drop_count"):
          with self.assertRaisesRegex(MantisPrimeError, message):
            await self.mantis.prime_chip(**kwargs)
        self.assertEqual(played.call_count, kwargs.get("max_cycles", 1))  # no post-prime
        self.assertFalse(await self.pump.request_pump_on())
        self.assertFalse(await self.arm.request_is_parked())  # left over the waste station
        self.assertIs(self.mantis.attached_chip, self.chip)

  async def test_a_failure_while_the_drops_play_stops_the_pump(self):
    await self.mantis.pick_up_chip(slot=1)
    with mock.patch.object(self.pump, "_play_drops", side_effect=MantisTimeoutError("lost")):
      with self.assertRaisesRegex(MantisTimeoutError, "lost"):
        await self.mantis.prime_chip(volume=1.0)
    self.assertFalse(await self.pump.request_pump_on())

  async def test_the_default_consecutive_drops_is_checked_when_assigned(self):
    with self.assertRaises(ValueError):
      self.mantis.default_consecutive_drops = 0
    self.assertEqual(self.mantis.default_consecutive_drops, 5)


class PrimeLedBarTests(ChipLedBarTestCase):
  async def asyncSetUp(self):
    await super().asyncSetUp()
    await self.mantis.pick_up_chip(slot=6)
    arm, pump = self.mantis.driver.arm, self.mantis.driver.pump
    assert arm is not None and pump is not None
    # The way in, the prime and the way back are tested with their own replies; here they pass.
    self.move_in = mock.AsyncMock()
    self.prime = mock.AsyncMock()
    dispense_sequences = (("dispense_100nl", 0.1), ("dispense_500nl", 0.5))
    for target, name, value in (
      (arm, "move_to_waste_station", self.move_in),
      (arm, "_get_waste_station_return_moves", mock.Mock(return_value=[])),
      (pump, "_get_dispense_sequences", mock.Mock(return_value=dispense_sequences)),
      (pump, "_get_drop_states", mock.Mock(return_value=[])),
      (pump, "_prime", self.prime),
    ):
      patcher = mock.patch.object(target, name, value)
      patcher.start()
      self.addCleanup(patcher.stop)
    self.play_moves.reset_mock()
    self.sent_before = len(self.link.written)

  async def test_a_prime_shows_magenta_around_the_way_in_the_prime_and_the_way_back(self):
    shown = []
    for step in (self.move_in, self.prime, self.play_moves):
      step.side_effect = lambda **_: shown.append(self.states()[-1])
    await self.mantis.prime_chip(volume=1.0)
    self.assertEqual(self.states(), [MAGENTA, "idle"])
    self.assertEqual(shown, [MAGENTA] * 3)

  async def test_a_prime_shows_its_colour_attribute(self):
    bar = self.mantis.led_bar
    assert bar is not None
    for color in ("purple", "#FF00F", 0x10000):
      with self.subTest(color=color):
        with self.assertRaises(ValueError):
          bar.prime_chip_color = color
    self.assertEqual(bar.prime_chip_color, "magenta")
    bar.prime_chip_color = "#FF4000"
    await self.mantis.prime_chip(volume=1.0)
    self.assertEqual(self.states(), [ORANGE, "idle"])

  async def test_a_refused_prime_shows_nothing(self):
    with self.assertRaisesRegex(ValueError, "volume must be above 0"):
      await self.mantis.prime_chip(volume=0.0)
    self.assertEqual(led_bar_frames(written=self.link.written[self.sent_before :]), [])
    self.move_in.assert_not_awaited()

  async def test_a_failed_prime_leaves_red(self):
    self.prime.side_effect = MantisPrimeError("no drop")
    with self.assertRaisesRegex(MantisPrimeError, "no drop"):
      await self.mantis.prime_chip(volume=1.0)
    self.assertEqual(self.states(), [MAGENTA, RED])
    self.play_moves.assert_not_awaited()

  async def test_a_led_bar_fault_neither_fails_nor_masks_a_prime(self):
    for color in (MAGENTA, RED):
      del self.link.answers[color_write(colors=[color] * 20)]
    with self.assertLogs("pylabrobot.formulatrix.mantis.driver.features.led_bar", "WARNING"):
      await self.mantis.prime_chip(volume=1.0)
      self.assertEqual(self.play_moves.await_count, 1)
      self.prime.side_effect = MantisPrimeError("no drop")
      with self.assertRaisesRegex(MantisPrimeError, "no drop"):
        await self.mantis.prime_chip(volume=1.0)

  async def test_without_a_led_bar_a_prime_shows_nothing(self):
    self.mantis.driver.led_bar = None
    await self.mantis.prime_chip(volume=1.0)
    self.prime.assert_awaited_once()
    self.assertEqual(led_bar_frames(written=self.link.written[self.sent_before :]), [])


# The bar half filled after the first of two wells, then full; then the full bar, not sent again,
# blinks 3 times.
HALF_FILL = [GREEN] * 10 + [OFF] * 10
DISPENSE_DONE = [GREEN] + [OFF, GREEN] * 3


class DispenseLedBarTests(ChipLedBarTestCase):
  async def asyncSetUp(self):
    await super().asyncSetUp()
    await self.mantis.pick_up_chip(slot=6)
    self.mantis._primed_chip = self.mantis.attached_chip
    self.wellplate = cor_96_wellplate_360uL_Fb(name="wellplate")
    self.mantis.plate_holder.assign_child_resource(self.wellplate)
    driver, arm, pump = self.mantis.driver, self.mantis.driver.arm, self.mantis.driver.pump
    assert arm is not None and pump is not None and driver.plate_clamp is not None
    # The clamp, the pump, the moves and the drops are tested with their own replies; here they
    # pass. Each well's last item is reported played as it is queued.
    self.unlock, self.stop_pump = mock.AsyncMock(), mock.AsyncMock()
    self.play_wells = mock.AsyncMock(side_effect=self.report_wells_played)
    self.shown_at_wells: List[Any] = []
    dispense_sequences = (("dispense_100nl", 0.1), ("dispense_500nl", 0.5))
    for target, name, value in (
      (driver, "_unchecked_fw_clear_sequencer", mock.AsyncMock()),
      (driver, "_unchecked_fw_start_sequencer", mock.AsyncMock()),
      (driver.plate_clamp, "lock", mock.AsyncMock()),
      (driver.plate_clamp, "unlock", self.unlock),
      (pump, "start_pump", mock.AsyncMock()),
      (pump, "stop_pump", self.stop_pump),
      (pump, "_prepare_pressures", mock.AsyncMock()),
      (pump, "_prime", mock.AsyncMock()),
      (pump, "_get_dispense_sequences", mock.Mock(return_value=dispense_sequences)),
      (pump, "_get_drop_states", mock.Mock(return_value=[(26, 1)])),
      (arm, "move_to_waste_station", mock.AsyncMock()),
      (arm, "_get_waste_station_return_moves", mock.Mock(return_value=[])),
      (arm, "_get_arm_frame_location", mock.Mock(return_value=Coordinate.zero())),
      (arm, "_get_dispense_moves", mock.Mock(side_effect=lambda **kw: [[]] * len(kw["locations"]))),
      (arm, "_get_dispense_end_moves", mock.Mock(return_value=([], []))),
      (self.mantis, "_play_wells", self.play_wells),
      (led_bar, "FILL_INTERVAL", 0.01),
    ):
      patcher = mock.patch.object(target, name, value)
      patcher.start()
      self.addCleanup(patcher.stop)
    self.sent_before = len(self.link.written)

  async def report_wells_played(self, *, wells, well_ends, **_):
    self.shown_at_wells.append(self.states()[-1] if self.states() else None)
    for item, _ in enumerate(wells, start=1):
      well_ends.append(item)
      self.mantis.driver._sequence_progress = item
      await asyncio.sleep(0.05)  # the bar looks every 0.01 s here

  async def dispense(self) -> None:
    await self.mantis.dispense(
      resource=self.wellplate["A1:A2"], volume=1.0, pre_dispense_volume=0.0
    )

  async def test_a_dispense_shows_white_fills_as_the_wells_play_then_blinks(self):
    await self.dispense()
    self.assertEqual(self.shown_at_wells, [WHITE])
    self.assertEqual(self.states(), [WHITE, HALF_FILL, *DISPENSE_DONE, "idle"])
    self.unlock.assert_awaited_once()

  async def test_a_chip_not_primed_shows_magenta_inside_white(self):
    self.mantis._primed_chip = None
    await self.mantis.dispense(
      resource=self.wellplate["A1:A2"], volume=1.0, prime_volume=1.0, pre_dispense_volume=0.0
    )
    self.assertEqual(self.states(), [WHITE, MAGENTA, WHITE, HALF_FILL, *DISPENSE_DONE, "idle"])

  async def test_a_dispense_shows_its_colour_attributes(self):
    bar = self.mantis.led_bar
    assert bar is not None
    for color in ("purple", "#FF00F", 0x10000):
      with self.subTest(color=color):
        with self.assertRaises(ValueError):
          bar.dispense_color = color
    self.assertEqual(bar.dispense_color, "white")
    bar.dispense_color = "#FF4000"
    await self.dispense()
    self.assertEqual(self.states(), [ORANGE, HALF_FILL, *DISPENSE_DONE, "idle"])

  async def test_a_refused_dispense_shows_nothing(self):
    with self.assertRaisesRegex(ValueError, "volumes must be 0 or above"):
      await self.mantis.dispense(resource=self.wellplate["A1:A2"], volume=-1.0)
    self.assertEqual(led_bar_frames(written=self.link.written[self.sent_before :]), [])

  async def test_a_failed_dispense_leaves_red_and_stops_the_fill(self):
    async def fail(*, well_ends, **_):
      well_ends.append(1)
      self.mantis.driver._sequence_progress = 1
      await asyncio.sleep(0.05)
      raise MantisMotionError("fault")

    self.play_wells.side_effect = fail
    with self.assertRaisesRegex(MantisMotionError, "fault"):
      await self.dispense()
    self.assertEqual(self.states(), [WHITE, HALF_FILL, RED])
    self.unlock.assert_awaited_once()
    self.stop_pump.assert_awaited_once()
    self.mantis.driver._sequence_progress = 2
    await asyncio.sleep(0.05)
    self.assertEqual(self.states(), [WHITE, HALF_FILL, RED])

  async def test_a_led_bar_fault_neither_fails_nor_masks_a_dispense(self):
    for colors in ([WHITE] * 20, HALF_FILL, [GREEN] * 20):
      del self.link.answers[color_write(colors=colors)]
    with self.assertLogs("pylabrobot.formulatrix.mantis.driver.features.led_bar", "WARNING"):
      await self.dispense()
      self.unlock.assert_awaited_once()
      self.play_wells.side_effect = MantisMotionError("fault")
      with self.assertRaisesRegex(MantisMotionError, "fault"):
        await self.dispense()

  async def test_without_a_led_bar_a_dispense_shows_nothing(self):
    self.mantis.driver.led_bar = None
    await self.dispense()
    self.play_wells.assert_awaited_once()
    self.assertEqual(led_bar_frames(written=self.link.written[self.sent_before :]), [])

  async def test_without_show_actions_a_dispense_shows_nothing(self):
    assert self.mantis.led_bar is not None
    self.mantis.led_bar.show_actions = False
    await self.dispense()
    self.play_wells.assert_awaited_once()
    self.assertEqual(led_bar_frames(written=self.link.written[self.sent_before :]), [])


# A low volume Hi-Fi chip's drops and safe state as a dispense plays them with the 1 cP class.
DISPENSE_SEQUENCES: Dict[str, List[Tuple[int, int]]] = {
  "small": [(30, 1), (22, 38), (23, 14), (21, 15), (29, 21), (31, 13)],
  "large": [(26, 104), (27, 1), (25, 12), (29, 19), (31, 10)],
  "prime_pump": [(18, 104), (19, 14), (17, 15), (29, 19), (31, 13)],
  "safe_state": [(17, 104)],
}


def name_drops(states: List[Tuple[int, int]]) -> List[str]:
  """Name each drop in a run of queued valve states, in order."""
  names, start = [], 0
  while start < len(states):
    for name, sequence in DISPENSE_SEQUENCES.items():
      if states[start : start + len(sequence)] == sequence:
        names.append(name)
        start += len(sequence)
        break
    else:
      raise AssertionError(f"no drop starts at {states[start : start + 6]}")
  return names


def get_moving_axes(move: List[Tuple[float, float, float]]) -> Tuple[bool, bool, bool]:
  """Which motors a queued move moves: those sent a speed."""
  return tuple(speed != 0 for _, speed, _ in move)  # type: ignore[return-value]


HOLD, ARMS, Z, ALL = (False, False, False), (True, True, False), (False, False, True), (True,) * 3
WELLPLATE_TOP = 14.2  # the wellplate's frame, mm above its base


class SimulatedDispenseTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recording = json.load(f)
    path = os.path.join(directory.name, "declared.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump(
        {
          **recording,
          "chip_changer": SYNTHETIC_CHIP_CHANGER_BLOCK,
          "stations": {
            "idle_position": SYNTHETIC_IDLE_POSITION_ROUTE,
            "waste": SYNTHETIC_WASTE_ROUTE,
          },
          "stage": SYNTHETIC_STAGE_BLOCK,
          "chip_sequences": {"low_volume_hi_fi": LOW_VOLUME_HI_FI_SEQUENCES},
          "pump_sequence_volumes": PUMP_SEQUENCE_VOLUMES_BLOCK,
          "input_volumes": INPUT_VOLUMES_BLOCK,
        },
        f,
      )
    self.mantis = Mantis(simulation=True, declared_configuration_json=path)
    self.chip = mantis_low_volume_hi_fi_chip(name="chip_1")
    self.mantis.chip_spots[1].assign_child_resource(self.chip)
    self.wellplate = cor_96_wellplate_360uL_Fb(name="wellplate")
    self.mantis.plate_holder.assign_child_resource(self.wellplate)
    await self.mantis.setup()
    self.addAsyncCleanup(self.mantis.stop)
    assert self.mantis.arm is not None and self.mantis.pump is not None
    self.arm, self.pump = self.mantis.arm, self.mantis.pump
    assert self.mantis.plate_clamp is not None
    self.plate_clamp = self.mantis.plate_clamp
    assert isinstance(self.mantis.driver, MantisSimulationDriver)
    self.driver = self.mantis.driver
    await self.mantis.pick_up_chip(slot=1)

  def record_queue(self) -> List[Tuple[str, Any]]:
    """Record each item queued from now: ("state", (state, duration)) or ("move", targets)."""
    queued: List[Tuple[str, Any]] = []
    write, move = (
      self.driver._unchecked_fw_queue_write_ppi,
      self.driver._unchecked_fw_queue_move_item,
    )

    async def record_write(**kwargs):
      queued.append(("state", (kwargs["values"][0], kwargs["duration"])))
      return await write(**kwargs)

    async def record_move(**kwargs):
      queued.append(("move", kwargs["targets"]))
      return await move(**kwargs)

    for name, side_effect in (
      ("_unchecked_fw_queue_write_ppi", record_write),
      ("_unchecked_fw_queue_move_item", record_move),
    ):
      patcher = mock.patch.object(self.driver, name, side_effect=side_effect)
      patcher.start()
      self.addCleanup(patcher.stop)
    return queued

  @staticmethod
  def get_drops(queued: List[Tuple[str, Any]]) -> List[str]:
    return name_drops([value for kind, value in queued if kind == "state"])

  async def assert_dispensed_and_back_at_idle(self) -> None:
    self.assertTrue(await self.arm.request_is_parked())
    self.assertFalse(await self.pump.request_pump_on())
    self.assertEqual(await self.plate_clamp.request_state(), UNCLAMPING_DONE)
    self.assertIs(self.mantis.attached_chip, self.chip)

  async def test_each_well_small_then_large_drops_then_the_safe_state(self):
    await self.mantis.prime_chip(volume=1.0)
    queued = self.record_queue()
    wells = self.wellplate["A1:A2"]
    await self.mantis.dispense(resource=wells, volume=(2.5, 0.5), minimum_small_diaphragm_pumps=5)
    # The install's pre-dispense, 0.6 uL, large drops first; then 5 small and 4 large, 5 small.
    self.assertEqual(
      self.get_drops(queued),
      ["large", "small"] + ["small"] * 5 + ["large"] * 4 + ["small"] * 5 + ["safe_state"],
    )
    await self.assert_dispensed_and_back_at_idle()

  async def test_precise_small_drops_then_large_drops(self):
    await self.mantis.prime_chip(volume=1.0)
    queued = self.record_queue()
    wells = self.wellplate["A1:A2"]
    await self.mantis.dispense(
      resource=wells, volume=[2.5, 0.5], minimum_small_diaphragm_pumps=5, precise=True
    )
    # 0.8 uL first, every well's small drops, 2 large at the waste station, the large drops.
    self.assertEqual(
      self.get_drops(queued),
      ["large"] + ["small"] * 3 + ["small"] * 10 + ["large"] * 2 + ["large"] * 4 + ["safe_state"],
    )
    await self.assert_dispensed_and_back_at_idle()

  async def test_a_chip_not_primed_is_primed_first(self):
    patcher = mock.patch.object(self.pump, "_prepare_pressures", wraps=self.pump._prepare_pressures)
    prepare = patcher.start()
    self.addCleanup(patcher.stop)
    queued = self.record_queue()
    await self.mantis.dispense(resource=self.wellplate["A1"], volume=1.0, pre_dispense_volume=0.0)
    # By drops, the drop sensor seeing one per cycle; then the well's 2 large drops.
    drops = self.get_drops(queued)
    self.assertEqual(drops, ["prime_pump"] * 5 + ["large"] * 2 + ["safe_state"])
    self.assertEqual(prepare.call_count, 1)  # the prime's pressures are the dispense's
    queued.clear()
    await self.mantis.dispense(resource=self.wellplate["A1"], volume=1.0, pre_dispense_volume=0.0)
    self.assertEqual(self.get_drops(queued), ["large"] * 2 + ["safe_state"])
    self.assertEqual(prepare.call_count, 2)
    await self.assert_dispensed_and_back_at_idle()

  async def get_moves(self, **kwargs) -> List[Tuple[bool, bool, bool]]:
    """The moving axes of each move a dispense into A1 and A2 queues, in order, primed first."""
    await self.mantis.prime_chip(volume=1.0)
    queued = self.record_queue()
    await self.mantis.dispense(
      resource=self.wellplate["A1:A2"], volume=1.0, pre_dispense_volume=0.0, **kwargs
    )
    await self.assert_dispensed_and_back_at_idle()
    return [get_moving_axes(value) for kind, value in queued if kind == "move"]

  async def test_the_head_moves_as_a_v4_dispense(self):
    # The hold and the idle position with Z held, Z alone, the arms over A1; A2 in one move; the
    # idle position route, then to idle Z first.
    to_idle_position, to_idle = [HOLD, ARMS, Z, ALL], [Z, ARMS]
    self.assertEqual(
      await self.get_moves(), [HOLD, ARMS, Z, ARMS, ALL, *to_idle_position, *to_idle]
    )

  async def assert_moves_to_idle_after(self, moves: List[Tuple[bool, bool, bool]], **kwargs):
    to_idle_position, to_idle = [HOLD, ARMS, Z, ALL], [Z, ARMS]
    self.assertEqual(await self.get_moves(**kwargs), [*moves, *to_idle_position, *to_idle])

  async def test_the_start_traverse_height(self):
    await self.assert_moves_to_idle_after(
      moves=[HOLD, ARMS, Z, ARMS, Z, ALL], minimum_traverse_height_start=WELLPLATE_TOP + 10
    )

  async def test_the_traverse_height_during(self):
    await self.assert_moves_to_idle_after(
      moves=[HOLD, ARMS, Z, ARMS, Z, ARMS, Z], minimum_traverse_height_during=WELLPLATE_TOP + 6
    )

  async def test_the_end_traverse_height(self):
    await self.assert_moves_to_idle_after(
      moves=[HOLD, ARMS, Z, ARMS, ALL, Z], minimum_traverse_height_end=WELLPLATE_TOP + 10
    )

  async def test_the_nozzle_stands_its_height_above_the_wellplate(self):
    await self.mantis.prime_chip(volume=1.0)
    queued = self.record_queue()
    await self.mantis.dispense(resource=self.wellplate["A1"], volume=1.0, pre_dispense_volume=0.0)
    # A1's centre is 14.3 mm right of and 11.28 mm in front of the back-left corner; 2.5 mm above
    # the 14.2 mm frame. The arms reach it with Z held at that height.
    _, _, z = compute_arm_frame_position(
      position=(14.3, 11.28, REFERENCE_HEIGHT - 16.7),
      origin=(-50.0, 40.0, 51.0),
      transformation_matrix=SYNTHETIC_STAGE_BLOCK["transformation_matrix"],
      offset_x=(0.0, 0.0, -0.0015),
      offset_y=(0.0, 0.0, 0.0025),
      arm_type_rotation_degree=0.0,
      arm_type_translation=(0.0, 0.0, 0.0),
    )
    moves = [value for kind, value in queued if kind == "move"]
    # Within 0.01 device units, a quarter of a µm: the well's centre is given to 0.01 mm here.
    expected = compute_device_position(motor=2, position=z)
    self.assertAlmostEqual(moves[2][2][0], expected, delta=0.01)

  async def assert_nothing_moves(self, dispense, error, message: str) -> None:
    item = self.driver._next_item
    positions = dict(self.arm._positions)  # type: ignore[attr-defined]
    clamp = await self.plate_clamp.request_state()
    with self.assertRaisesRegex(error, message):
      await dispense
    self.assertEqual(self.driver._next_item, item)  # nothing queued, no valve and no move
    self.assertEqual(self.arm._positions, positions)  # type: ignore[attr-defined]
    self.assertEqual(await self.plate_clamp.request_state(), clamp)
    self.assertFalse(await self.pump.request_pump_on())

  async def test_refused_before_anything_moves(self):
    wells = self.wellplate["A1:A2"]
    another_chips_class = mantis_mapping[("1 cP", "high_volume_hi_fi")]
    positive_vacuum = copy.copy(mantis_mapping[("1 cP", "low_volume_hi_fi")])
    positive_vacuum.vacuum = 1.0
    cases: List[Tuple[Dict[str, Any], Type[Exception], str]] = [
      ({"resource": [], "volume": 1.0}, ValueError, "no well"),
      ({"resource": wells, "volume": [1.0]}, ValueError, "2 wells but 1 volumes"),
      ({"resource": wells, "volume": -1.0}, ValueError, "volumes must be 0 or above"),
      (
        {"resource": wells, "volume": 1.0, "minimum_small_diaphragm_pumps": -1},
        ValueError,
        "minimum_small_diaphragm_pumps",
      ),
      ({"resource": wells, "volume": 1.0, "pre_dispense_volume": -1.0}, ValueError, "pre_dis"),
      (
        {"resource": wells, "volume": 1.0, "dispense_height_above_well": 0.0},
        ValueError,
        "dispense_height_above_well must be above 0",
      ),
      (
        {"resource": wells, "volume": 1.0, "minimum_traverse_height_during": 10.0},
        ValueError,
        "below wellplate's tallest point",
      ),
      (
        {"resource": wells, "volume": 1.0, "liquid_class": another_chips_class},
        ValueError,
        "the liquid class is for",
      ),
      (
        {"resource": wells, "volume": 1.0, "liquid_class": positive_vacuum},
        ValueError,
        "vacuum must be 0 or below",
      ),
      (
        {"resource": wells, "volume": 1.0, "offset": Coordinate(z=-30.0)},
        ValueError,
        "outside its limits",
      ),
    ]
    for kwargs, error, message in cases:
      with self.subTest(message=message):
        await self.assert_nothing_moves(
          dispense=self.mantis.dispense(**kwargs), error=error, message=message
        )
    with mock.patch.object(device_module, "PRECISE_PRE_DISPENSE_VOLUMES", {}):
      await self.assert_nothing_moves(
        dispense=self.mantis.dispense(resource=wells, volume=1.0, precise=True),
        error=MantisConfigurationError,
        message="no precise pre-dispense volume",
      )
    self.arm._absolute_positions[0] += 10.0  # type: ignore[attr-defined]
    await self.assert_nothing_moves(
      dispense=self.mantis.dispense(resource=wells, volume=1.0),
      error=MantisMotionError,
      message="park",
    )

  async def test_without_a_chip_or_on_another_wellplate_nothing_moves(self):
    await self.mantis.drop_chip(slot=1)
    await self.assert_nothing_moves(
      dispense=self.mantis.dispense(resource=self.wellplate["A1"], volume=1.0),
      error=MantisMotionError,
      message="no chip is on the head",
    )
    await self.mantis.pick_up_chip(slot=1)
    other = cor_96_wellplate_360uL_Fb(name="other")
    await self.assert_nothing_moves(
      dispense=self.mantis.dispense(
        resource=[self.wellplate.get_item("A1"), other.get_item("A1")], volume=1.0
      ),
      error=ValueError,
      message="more than one wellplate",
    )
    await self.assert_nothing_moves(
      dispense=self.mantis.dispense(resource=other["A1"], volume=1.0),
      error=MantisCoordinateError,
      message="is not in Mantis_plate_holder",
    )
    turned = cor_96_wellplate_360uL_Fb(name="turned")
    turned.rotation.z = 180
    await self.assert_nothing_moves(
      dispense=self.mantis.dispense(resource=turned["A1"], volume=1.0),
      error=MantisCoordinateError,
      message="is turned on its holder",
    )

  async def test_a_volume_below_half_a_small_drop_dispenses_none(self):
    item = self.driver._next_item
    with self.assertLogs(device_module.logger, level="WARNING") as logs:
      await self.mantis.dispense(resource=self.wellplate["A1:A2"], volume=0.04)
    self.assertEqual(self.driver._next_item, item)
    self.assertTrue(any("nothing to dispense" in line for line in logs.output))

  async def test_a_failure_over_the_wells_releases_the_clamp_and_stops_the_pump(self):
    await self.mantis.prime_chip(volume=1.0)
    with mock.patch.object(self.arm, "_queue_moves", side_effect=MantisMotionError("fault")):
      with self.assertRaisesRegex(MantisMotionError, "fault"):
        await self.mantis.dispense(
          resource=self.wellplate["A1"], volume=1.0, pre_dispense_volume=0.0
        )
    self.assertFalse(self.driver._sequencer_started)  # aborted
    self.assertFalse(await self.pump.request_pump_on())
    self.assertEqual(await self.plate_clamp.request_state(), UNCLAMPING_DONE)
    self.assertIs(self.mantis.attached_chip, self.chip)

  async def test_a_clamp_that_does_not_release_raises_with_the_pump_stopped(self):
    await self.mantis.prime_chip(volume=1.0)
    with mock.patch.object(
      self.plate_clamp, "unlock", side_effect=PlateClampError("stuck", state=7)
    ):
      with self.assertRaisesRegex(PlateClampError, "stuck"):
        await self.mantis.dispense(
          resource=self.wellplate["A1"], volume=1.0, pre_dispense_volume=0.0
        )
    self.assertFalse(await self.pump.request_pump_on())
    self.assertTrue(await self.arm.request_is_parked())

  async def test_an_interrupt_while_the_clamp_releases_stops_the_pump(self):
    await self.mantis.prime_chip(volume=1.0)
    with mock.patch.object(self.plate_clamp, "unlock", side_effect=asyncio.CancelledError):
      with self.assertRaises(asyncio.CancelledError):
        await self.mantis.dispense(
          resource=self.wellplate["A1"], volume=1.0, pre_dispense_volume=0.0
        )
    self.assertFalse(await self.pump.request_pump_on())
    self.assertTrue(await self.arm.request_is_parked())

  async def test_a_clamp_that_does_not_lock_starts_no_pump(self):
    await self.mantis.prime_chip(volume=1.0)
    item = self.driver._next_item
    with mock.patch.object(
      self.plate_clamp, "lock", side_effect=PlateClampError("timeout", state=4)
    ):
      with self.assertRaisesRegex(PlateClampError, "timeout"):
        await self.mantis.dispense(resource=self.wellplate["A1"], volume=1.0)
    self.assertEqual(self.driver._next_item, item)
    self.assertFalse(await self.pump.request_pump_on())
    self.assertEqual(await self.plate_clamp.request_state(), UNCLAMPING_DONE)

  async def test_the_defaults_are_checked_when_assigned(self):
    with self.assertRaises(ValueError):
      self.mantis.default_dispense_height_above_well = 0.0
    with self.assertRaises(ValueError):
      self.mantis.default_nozzle_clearance_above_wellplate = 0.0
    self.assertEqual(self.mantis.default_dispense_height_above_well, 2.5)
    self.assertEqual(self.mantis.default_nozzle_clearance_above_wellplate, 2.5)


class ChipReaderTests(unittest.IsolatedAsyncioTestCase):
  async def test_a_device_without_a_reader_picks_up_nothing(self):
    mantis = Mantis(io=mock.create_autospec(FTDI, instance=True))
    chip = mantis_low_volume_hi_fi_chip(name="chip_6")
    with self.assertRaisesRegex(MantisConfigurationError, "no RFID reader or antennas"):
      await mantis._check_chip_in_slot(slot=6, chip=chip)


if __name__ == "__main__":
  unittest.main()
