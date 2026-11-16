"""RFID reader tests: the antenna switches and reader commands sent, against real replies."""

from typing import List
from unittest import mock

import pytest

from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.errors import MantisConfigurationError
from pylabrobot.formulatrix.mantis.driver.features import rfid
from pylabrobot.formulatrix.mantis.driver.framing import parse_frame
from pylabrobot.formulatrix.mantis.driver.master import MantisDriver
from pylabrobot.formulatrix.mantis.driver.master_tests import (
  DriverTestBase,
  FakeLink,
  FakeReader,
  make_answers,
)

# Interface A: the tags of a low-volume Hi-Fi and a high-volume Hi-Fi chip, as a Mantis v4's
# reader sent them, chunked as they arrived.
LOW_VOLUME_HI_FI_TAG_REPLY = [
  b"Reque",
  b"st mode.\r\n",
  b"[00000000003532534C3230323220202",
  b"0202020202008000000353230323730313139342E302020343230312E3020202033030000002E313231303"
  b"03030303030303130303090050000C60400004E08000054080000000000000000000000000000]\r\n",
]
HIGH_VOLUME_HI_FI_TAG_REPLY = [
  b"Request mode.\r\n",
  b"[00000000003532534838393130202020202020202007000",
  b"000353230323032353037302E352020313431302E31202020370100000038383632202020202020202020"
  b"202020DC010000D80200006E0400006F040000000000000000000000000000]\r\n",
]


def compute_tag(*, reply: List[bytes]) -> bytes:
  """The tag a reply carries, hex-decoded."""
  return bytes.fromhex(b"".join(reply).split(b"[")[1].split(b"]")[0].decode())


class TestRfid(DriverTestBase):
  async def set_up_rfid(self, *, reader: FakeReader) -> MantisDriver:
    self.link = FakeLink(answers=make_answers(), reader=reader)
    driver = await self.set_up_driver(
      link=self.link, declared_configuration_json=RECORDING_MANTIS_V4
    )
    self.sent_before = len(self.link.written)
    return driver

  async def test_setup_sets_up_the_reader(self) -> None:
    reader = FakeReader()
    await self.set_up_rfid(reader=reader)
    assert reader.written == [
      rfid.RFID_PING_DEVICE_COMMAND,
      rfid.RFID_SET_ANTENNA_MODE_COMMAND,
      rfid.RFID_SET_UP_ISO15693_PROTOCOL_COMMAND,
    ]

  async def test_tags_switch_each_antenna_then_read(self) -> None:
    reader = FakeReader(tags={0: LOW_VOLUME_HI_FI_TAG_REPLY, 1: HIGH_VOLUME_HI_FI_TAG_REPLY})
    driver = await self.set_up_rfid(reader=reader)
    assert driver.rfid is not None
    tags = await driver.rfid.request_tags()
    assert [parse_frame(frame=frame)[1:] for frame in self.link.written[self.sent_before :]] == [
      (0, 41, bytes.fromhex(f"78000100{antenna:02x}00")) for antenna in (0, 1, 4, 6, 7, 5)
    ]
    assert reader.written[3:] == [rfid.RFID_READ_TAG_COMMAND] * 6
    slot_1, slot_2 = tags[1], tags[2]
    assert slot_1 is not None and slot_2 is not None
    assert (slot_1["serial"], slot_1["chip_type"], slot_1["valid"]) == (
      "LS252202",
      "low_volume_hi_fi",
      True,
    )
    assert (slot_2["serial"], slot_2["chip_type"], slot_2["valid"]) == (
      "HS250198",
      "high_volume_hi_fi",
      True,
    )
    assert [tags[slot] for slot in (3, 4, 5, 6)] == [None] * 4

  async def test_short_or_late_tag_is_no_chip(self) -> None:
    short = [
      b"Request mode.\r\n",
      b"[" + compute_tag(reply=LOW_VOLUME_HI_FI_TAG_REPLY)[:-4].hex().encode() + b"]\r\n",
    ]
    for reply in (short, [b"Request mode.\r\n"]):
      driver = await self.set_up_rfid(reader=FakeReader(tags={0: reply}))
      assert driver.rfid is not None
      with mock.patch.object(rfid, "RFID_REPLY_TIMEOUT", 0.05):
        assert await driver.rfid.request_tag(slot=1) is None

  async def test_no_antennas_an_unknown_slot_or_a_delay_send_nothing(self) -> None:
    driver = await self.set_up_rfid(reader=FakeReader())
    assert driver.rfid is not None
    with pytest.raises(ValueError, match="slot must be one of"):
      await driver.rfid.request_tag(slot=7)
    driver.rfid.multiplexer_delay_raw = 5.0
    with pytest.raises(MantisConfigurationError, match="unconfirmed unit"):
      await driver.rfid.request_tag(slot=1)
    driver.rfid.slot_to_antenna = None  # a unit whose folder, declared file and recording have none
    driver.rfid.multiplexer_delay_raw = 0.0
    with pytest.raises(MantisConfigurationError, match="no RFID antennas"):
      await driver.rfid.request_tags()
    assert len(self.link.written) == self.sent_before

  async def test_built_only_when_the_reader_answers(self) -> None:
    with mock.patch.object(rfid, "RFID_REPLY_TIMEOUT", 0.05):
      driver = await self.set_up_rfid(reader=FakeReader(answers_ping=False))
    assert driver.rfid is None

  async def test_a_reader_link_that_does_not_open_leaves_setup_working(self) -> None:
    reader = FakeReader()
    reader.io.entry_error = OSError("interface A busy")
    driver = await self.set_up_rfid(reader=reader)
    assert driver.rfid is None
    assert driver.plate_clamp is not None

  async def test_the_reader_is_set_up_again_after_the_link_reopens(self) -> None:
    reader = FakeReader()
    link = FakeLink(answers=make_answers(), reader=reader)
    driver = self.make_driver(link=link, declared_configuration_json=RECORDING_MANTIS_V4)
    async with driver.lifespan(skip_arm=True):
      assert driver.rfid is not None
    assert driver.rfid is None
    async with driver.lifespan(skip_arm=True):
      assert driver.rfid is not None
    assert reader.written.count(rfid.RFID_PING_DEVICE_COMMAND) == 2

  def test_tag_validity(self) -> None:
    tag = bytearray(compute_tag(reply=LOW_VOLUME_HI_FI_TAG_REPLY))
    assert rfid.compute_tag_valid(tag=bytes(tag))
    tag[25:33] = b"52020231"  # date 20251320: no month 13
    assert not rfid.compute_tag_valid(tag=bytes(tag))
