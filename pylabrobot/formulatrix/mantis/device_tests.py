"""Mantis device tests: the parts tree, the chips placed checked against the tags read, a chip
picked up and dropped, shown on the LED bar, and primed."""

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
from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.errors import (
  MantisConfigurationError,
  MantisMotionError,
  MantisPrimeError,
  MantisTimeoutError,
)
from pylabrobot.formulatrix.mantis.driver.features.led_bar_tests import (
  CYAN,
  MAGENTA,
  ORANGE,
  RED,
  VIOLET,
  bar_states,
  color_write,
  led_bar_frames,
  make_led_bar_answers,
)
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
  SYNTHETIC_WASTE_ROUTE,
)
from pylabrobot.formulatrix.mantis.driver.simulator import (
  MantisSimulationDropSensor,
  MantisSimulationRfid,
)
from pylabrobot.formulatrix.mantis.liquid_classes import mantis_mapping
from pylabrobot.io.ftdi import FTDI

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


class ChipReaderTests(unittest.IsolatedAsyncioTestCase):
  async def test_a_device_without_a_reader_picks_up_nothing(self):
    mantis = Mantis(io=mock.create_autospec(FTDI, instance=True))
    chip = mantis_low_volume_hi_fi_chip(name="chip_6")
    with self.assertRaisesRegex(MantisConfigurationError, "no RFID reader or antennas"):
      await mantis._check_chip_in_slot(slot=6, chip=chip)


if __name__ == "__main__":
  unittest.main()
