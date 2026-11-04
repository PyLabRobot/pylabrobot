"""Offline protocol regressions; no reader connection is opened."""

from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Optional
from unittest.mock import call, create_autospec

import anyio
import pytest

from pylabrobot.agilent.biotek.plate_reader_protocol import (
  encode_absorbance,
  parse_assay_response,
  parse_response,
)
from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.testing.concurrency import AnyioTestBase

TEST_DATA = Path(__file__).parent / "test_data"
PACKET_VECTORS = json.loads((TEST_DATA / "absorbance_packets.json").read_text())["vectors"]
LEGACY_CAPTURE = json.loads((TEST_DATA / "synergy_ht_identity.json").read_text())
SUPPORTED = b"10000000000000000"
UNSUPPORTED = b"00000000000000000"
ASSAY_REPLY = b"0350000000000000010000000000490300000\x03"


@pytest.mark.parametrize("vector", PACKET_VECTORS)
def test_absorbance_packets(vector: dict) -> None:
  """Match independent serializer vectors across all four capability combinations."""
  assert (
    encode_absorbance(
      bounds=tuple(vector["bounds"]),
      wavelength=vector["wavelength"],
      extended_optics=vector["cap2064"],
      extra_read_field=vector["cap2056"],
    )
    == vector["packet"]
  )


def test_assay_reply() -> None:
  """Accept the recorded D reply used by the existing reader tests."""
  assert parse_assay_response(ASSAY_REPLY) == b"000000000000001000000000049"


@pytest.mark.parametrize(
  "reply",
  [
    b"999" + ASSAY_REPLY[3:],
    ASSAY_REPLY[:10] + b"1" + ASSAY_REPLY[11:],
    ASSAY_REPLY[:-8] + b"999" + ASSAY_REPLY[-5:],
    ASSAY_REPLY[:-1],
    b"0000\x03",
    b"\x03",
    None,
  ],
)
def test_invalid_assay_reply(reply: Optional[bytes]) -> None:
  """Reject corruption and incomplete replies before starting a measurement."""
  with pytest.raises(ValueError):
    parse_assay_response(reply)


def test_instrument_status() -> None:
  """Expose the instrument's hexadecimal error instead of reporting success."""
  with pytest.raises(RuntimeError, match="0x0A01"):
    parse_assay_response(b"0A01\x03")
  with pytest.raises(ValueError, match="invalid status"):
    parse_response(b"GGGG\x03", "z")


class TestReaderNegotiation(AnyioTestBase):
  """Exercise command sequencing with simulated FTDI reads and writes."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Replace the transport before any setup or device discovery."""
    await super()._enter_lifespan(stack)
    pytest.importorskip("pylibftdi")
    from pylabrobot.agilent.biotek.plate_reader_base import BioTekPlateReaderDriver
    from pylabrobot.io.ftdi import FTDI
    from pylabrobot.resources import cellvis_96_wellplate_350uL_Fb

    self.reader = BioTekPlateReaderDriver()
    self.io = create_autospec(FTDI, instance=True)
    self.io.write.side_effect = len
    self.io._device_id = "mock-biotek"
    self.io._close = self.io.stop

    @asynccontextmanager
    async def io_lifespan():
      await self.io.setup()
      try:
        yield self.io
      finally:
        with anyio.CancelScope(shield=True):
          await self.io.stop()

    self.io.lifespan = io_lifespan
    self.reader.io = self.io
    self.plate = cellvis_96_wellplate_350uL_Fb("plate")

  async def test_captured_legacy_reader(self) -> None:
    """A real firmware-2.24 reader reports no RDOP22; never send it D/O or y."""
    self.io.read.side_effect = [
      bytes.fromhex(record["hex"])
      for record in LEGACY_CAPTURE["records"]
      if record["direction"] == "rx"
    ]
    async with self.reader:
      assert self.reader.version == "2.24"
      with pytest.raises(NotImplementedError, match="Legacy BioTek V/S"):
        await self.reader.read_absorbance(self.plate, self.plate.get_all_items(), 600)
    assert self.io.write.call_args_list == [call(b"e"), call(b"z"), call(b"2000\x03")]

  async def test_older_capability_layouts(self) -> None:
    """Simulated 1.12 replies select the negotiated layout, independently of version."""
    for vector in PACKET_VECTORS:
      if vector["wavelength"] != 580 or vector["bounds"] != [1, 1, 1, 1]:
        continue
      self.io.reset_mock()
      cap2056 = SUPPORTED if vector["cap2056"] else UNSUPPORTED
      cap2064 = SUPPORTED if vector["cap2064"] else UNSUPPORTED
      self.io.read.side_effect = [
        b"\x060000000  Version 1.12   0000\x03",  # Synthetic identity
        b"\x06",
        SUPPORTED + b"0000\x03",
        b"\x06",
        cap2056 + b"0000\x03",
        b"\x06",
        cap2064 + b"0000\x03",
        b"\x06",
        b"0000\x03",  # Geometry
        b"\x06",
        ASSAY_REPLY,
        b"\x060000\x03" + b"01,1,\r000:00:00.0,228,01,01,+0.1917\r\n228\x1a088\x1a0000\x03",
        b"\x062360000\x03",
      ]
      async with self.reader:
        result = await self.reader.read_absorbance(self.plate, self.plate["A1"], 580)
        assert self.reader.version == "1.12"
        assert result[0].data[0][0] == 0.1917
        assert result[0].data[0][1] is None
      self.io.write.assert_any_call(vector["packet"].encode())
      assert self.io.write.call_args_list.count(call(b"2000\x03")) == 1
      assert self.io.write.call_args_list.count(call(b"2056\x03")) == 1
      assert self.io.write.call_args_list.count(call(b"2064\x03")) == 1

  async def test_assay_error_does_not_start_read(self) -> None:
    """An invalid D response must not be followed by O."""
    for bad_reply in (ASSAY_REPLY[:10] + b"1" + ASSAY_REPLY[11:], b"0A01\x03"):
      self.reader._capabilities.clear()
      self.reader.clear_plate()
      self.io.reset_mock()
      self.io.read.side_effect = [
        b"\x06",
        SUPPORTED + b"0000\x03",
        b"\x06",
        UNSUPPORTED + b"0000\x03",
        b"\x06",
        UNSUPPORTED + b"0000\x03",
        b"\x06",
        b"0000\x03",
        b"\x06",
        bad_reply,
      ]
      with pytest.raises((ValueError, RuntimeError)):
        await self.reader.read_absorbance(self.plate, self.plate["A1"], 580)
      assert call(b"O") not in self.io.write.call_args_list

  async def test_capability_cache_resets_on_connection(self) -> None:
    """A reconnect may have a different capability set."""
    self.io.read.side_effect = [
      b"\x06",
      SUPPORTED + b"0000\x03",
      b"\x060000000  Version 1.12   0000\x03",
      b"\x06",
      UNSUPPORTED + b"0000\x03",
    ]
    assert await self.reader.query_capability(2064) == SUPPORTED
    assert await self.reader.query_capability(2064) == SUPPORTED
    async with self.reader:
      assert await self.reader.query_capability(2064) == UNSUPPORTED
    assert self.io.write.call_args_list.count(call(b"2064\x03")) == 2

  async def test_read_start_requires_success_and_ack(self) -> None:
    """Reject an O error or malformed acknowledgement before reading well data."""
    for reply in (b"\x060A01\x03", b"0000\x03", b"\x15" + b"0000\x03"):
      self.reader._capabilities.clear()
      self.reader.clear_plate()
      self.io.reset_mock()
      self.io.read.side_effect = [
        b"\x06",
        SUPPORTED + b"0000\x03",
        b"\x06",
        UNSUPPORTED + b"0000\x03",
        b"\x06",
        UNSUPPORTED + b"0000\x03",
        b"\x06",
        b"0000\x03",
        b"\x06",
        ASSAY_REPLY,
        reply,
      ]
      with pytest.raises((ValueError, RuntimeError)):
        await self.reader.read_absorbance(self.plate, self.plate["A1"], 580)
      assert self.io.write.call_args_list[-1] == call(b"O")

  async def test_bad_capability_reply_is_not_cached(self) -> None:
    """Instrument errors and malformed data are distinct from unsupported features."""
    for reply in (b"0A01\x03", b"10000\x03", b"2" + b"0" * 20 + b"\x03"):
      self.io.read.side_effect = [b"\x06", reply]
      with pytest.raises((ValueError, RuntimeError)):
        await self.reader.query_capability(2064)
      assert 2064 not in self.reader._capabilities

  async def test_unsupported_carrier_speed(self) -> None:
    """Default tray movement still works when speed selection is unsupported."""
    self.io.read.side_effect = [b"\x06", UNSUPPORTED + b"0000\x03", b"\x060000\x03"]
    await self.reader.open()
    assert self.io.write.call_args_list == [call(b"z"), call(b"2138\x03"), call(b"J")]
    with pytest.raises(NotImplementedError, match="carrier speed"):
      await self.reader.open(slow=True)
    assert self.io.write.call_count == 3

  async def test_rejected_geometry_is_not_cached(self) -> None:
    """A geometry error must not mark the plate as configured."""
    self.io.read.side_effect = [b"\x06", SUPPORTED + b"0000\x03", b"\x06", b"0A01\x03"]
    with pytest.raises(RuntimeError, match="BioTek y"):
      await self.reader.set_plate(self.plate)
    assert self.reader._plate is None

  async def test_rejected_command_does_not_send_parameters(self) -> None:
    """A NAK fails immediately rather than waiting for an ACK or writing parameters."""
    self.io.read.side_effect = [b"\x15"]
    with pytest.raises(RuntimeError, match="expected ACK"):
      await self.reader.query_capability(2000)
    self.io.write.assert_called_once_with(b"z")

  async def test_synergy_chunk_preserves_following_frame(self) -> None:
    """A chunk containing O's reply and measurement bytes retains both frames."""
    from pylabrobot.agilent.biotek.synergy.synergy_h1 import SynergyH1

    reader = SynergyH1("reader")
    reader.io = self.io
    self.io.read.side_effect = [b"\x060000\x03measurement\x03"]
    assert await reader.send_command("O") == b"\x060000\x03"
    assert await reader._read_until(b"\x03") == b"measurement\x03"
    self.io.read.assert_awaited_once()

  async def test_synergy_chunk_preserves_parameter_response(self) -> None:
    """Fragmented capability data are assembled correctly by the chunked transport."""
    from pylabrobot.agilent.biotek.synergy.synergy_h1 import SynergyH1

    reader = SynergyH1("reader")
    reader.io = self.io
    self.io.read.side_effect = [b"\x06", SUPPORTED[:5], SUPPORTED[5:] + b"0000", b"\x03"]
    assert await reader.query_capability(2000) == SUPPORTED
