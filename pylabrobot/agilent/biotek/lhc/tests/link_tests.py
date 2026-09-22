"""One framed request and its reply.

What the link owns is the shape of an exchange and nothing else: write the header, write the payload,
read the acknowledgement, read the reply, check it, and turn a status word into the kind of failure
it names. The transport underneath is a fake, so every one of those steps runs for real.
"""

from __future__ import annotations

import importlib.util
import unittest

import pytest

from pylabrobot.agilent.biotek.lhc.comm.connection import transport_for
from pylabrobot.agilent.biotek.lhc.comm.ftdi_transport import FtdiTransport, is_ftdi_port
from pylabrobot.agilent.biotek.lhc.comm.link import ACK, NAK, Link
from pylabrobot.agilent.biotek.lhc.comm.serial_transport import SerialTransport
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.error_handling import LinkError, RejectedError
from pylabrobot.agilent.biotek.lhc.serialization.command import Command
from pylabrobot.agilent.biotek.lhc.serialization.command_numbers import CommandNumber
from pylabrobot.agilent.biotek.lhc.serialization.commands.queries import GetSerialNumber, Ping
from pylabrobot.agilent.biotek.lhc.serialization.frame import HEADER_LENGTH, Header, checksum
from pylabrobot.agilent.biotek.lhc.tests.helpers import FakeInstrument, fake_link

HAS_USB_DRIVER = importlib.util.find_spec("pylibftdi") is not None
"""Whether the driver a USB bridge needs is installed. It is an optional dependency."""


class TestChoosingATransport:
  """Which kind of link a port string names, which is decided in one place."""

  @pytest.mark.parametrize(
    "port, usb",
    [
      ("USB 405 TS/LS sn:13010914", True),
      ("ftdi:FT1ABCDE", True),
      ("/dev/ttyUSB0", False),
      ("/dev/ttyS4", False),
      ("COM3", False),
    ],
  )
  def test_a_port_string_names_its_own_kind(self, port: str, usb: bool):
    """A string carrying a device serial number is a USB bridge; anything else is handed to the
    operating system as it stands, which is why no name pattern is tested for."""
    assert is_ftdi_port(port) is usb

  @pytest.mark.parametrize(
    "port, expected",
    [
      ("USB 405 TS/LS sn:13010914", FtdiTransport),
      ("ftdi:FT1ABCDE", FtdiTransport),
      ("/dev/ttyUSB0", SerialTransport),
      ("/dev/ttyS4", SerialTransport),
      ("COM3", SerialTransport),
    ],
  )
  def test_the_port_string_alone_picks_the_transport(self, port: str, expected: type):
    """Nothing above this knows which kind it got, and nothing chooses one by hand."""
    if expected is FtdiTransport and not HAS_USB_DRIVER:
      pytest.skip("a USB bridge needs pylibftdi, which is an optional dependency")
    assert isinstance(transport_for(port), expected)

  def test_a_serial_port_is_passed_through_unexamined(self):
    """A serial port is whatever the operating system calls one, so nothing here validates it."""
    assert transport_for("/dev/ttyS4").port == "/dev/ttyS4"

  def test_no_port_is_an_error(self):
    """No port is an error."""
    with pytest.raises(ValueError, match="no port"):
      transport_for("")

  def test_a_link_needs_either_a_port_or_a_transport(self):
    """A link needs either a port or a transport."""
    with pytest.raises(ValueError, match="either a port or a transport"):
      Link()


class TestTheFrame:
  """The bytes around a payload, which every command shares."""

  def test_the_checksum_covers_the_header_and_the_payload(self):
    """The checksum covers the header and the payload."""
    header = Header(number=115, payload_length=2)
    assert checksum(header.to_bytes(), b"\x01\x02") != checksum(header.to_bytes(), b"\x01\x03")

  def test_a_header_packs_and_unpacks(self):
    """A header packs and unpacks."""
    header = Header(number=141, payload_length=1, check=0x1234)
    assert len(header.to_bytes()) == HEADER_LENGTH
    assert Header.from_bytes(header.to_bytes()) == header

  def test_a_command_frames_itself_as_a_header_and_its_payload(self):
    """A command frames itself as a header and its payload."""
    command = Command(number=141, payload=b"\x04")
    framed = command.to_bytes()
    assert len(framed) == HEADER_LENGTH + 1
    assert Header.from_bytes(framed).number == 141
    assert framed[HEADER_LENGTH:] == b"\x04"

  def test_a_reply_carries_its_status_before_its_answer(self):
    """A reply carries its status before its answer."""
    command = Command(number=256)
    assert command.parse_reply((0).to_bytes(2, "little") + b"abc") == (0, b"abc")

  def test_a_reply_that_reports_an_error_has_no_answer(self):
    """A caller cannot mistake a failure for a short answer."""
    command = Command(number=256)
    assert command.parse_reply((0x6029).to_bytes(2, "little") + b"abc") == (0x6029, b"")


class TestOneExchange(unittest.IsolatedAsyncioTestCase):
  """Sending a command over the link and reading what comes back."""

  async def test_a_closed_link_refuses_to_send(self):
    """A closed link refuses to send."""
    link, _ = fake_link()
    with self.assertRaisesRegex(LinkError, "not open"):
      await link.request(Ping())

  async def test_opening_twice_does_nothing(self):
    """Opening twice does nothing."""
    link, io = fake_link()
    await link.setup()
    await link.setup()
    self.assertTrue(link.is_open)
    self.assertTrue(io.is_open)

  async def test_closing_a_closed_link_does_nothing(self):
    """Closing a closed link does nothing."""
    link, _ = fake_link()
    await link.stop()
    self.assertFalse(link.is_open)

  async def test_the_header_and_the_payload_are_written_separately(self):
    """The instrument expects them as two writes, so a command carrying a payload is two."""
    link, io = fake_link()
    await link.setup()
    await link.request(Command(number=int(CommandNumber.INIT_PROTOCOL), payload=b"\x04"))
    self.assertEqual(io.payload_of(CommandNumber.INIT_PROTOCOL), b"\x04")

  async def test_an_answer_comes_back_with_the_status_split_off(self):
    """An answer comes back with the status split off."""
    link, _ = fake_link(answers={CommandNumber.GET_SERIAL_NUMBER: b"SN0001".ljust(24)})
    await link.setup()
    command = GetSerialNumber()
    self.assertEqual(command.parse(await link.request(command)), "SN0001")

  async def test_a_status_the_instrument_reports_is_raised_as_what_failed(self):
    """A status the instrument reports is raised as what failed."""
    link, _ = fake_link(status=0x6029)
    await link.setup()
    with self.assertRaises(RejectedError):
      await link.request(Ping())

  async def test_a_refused_command_is_a_link_failure(self):
    """The instrument answering that it will not take the command at all is different from it
    answering that the command failed."""

    class Refusing(FakeInstrument):
      """A fake instrument that refuses whatever it is sent."""

      def _answer(self, header: Header, payload: bytes) -> None:
        """Answer with a refusal rather than a reply.

        Args:
          header: The request's header.
          payload: The request's payload.
        """
        self.sent.append(header.number)
        self.payloads.append(payload)
        self._out += bytes([NAK])

    io = Refusing()
    link = Link(port="fake", family=InstrumentFamily.EL406, name="fake", timeout=0.05, io=io)
    await link.setup()
    with self.assertRaisesRegex(LinkError, "refused"):
      await link.request(Ping())

  async def test_an_instrument_that_never_acknowledges_times_out(self):
    """An instrument that never acknowledges times out."""

    class Silent(FakeInstrument):
      """A fake instrument that takes a command and says nothing."""

      def _answer(self, header: Header, payload: bytes) -> None:
        """Take the request and answer nothing.

        Args:
          header: The request's header.
          payload: The request's payload.
        """
        self.sent.append(header.number)
        self.payloads.append(payload)

    link = Link(port="fake", family=InstrumentFamily.EL406, name="fake", timeout=0.05, io=Silent())
    await link.setup()
    with self.assertRaisesRegex(LinkError, "did not acknowledge"):
      await link.request(Ping())

  async def test_a_reply_that_stops_part_way_times_out(self):
    """A reply that stops part way times out."""

    class Truncating(FakeInstrument):
      """A fake instrument whose reply stops after the acknowledgement."""

      def _answer(self, header: Header, payload: bytes) -> None:
        """Acknowledge and then send nothing.

        Args:
          header: The request's header.
          payload: The request's payload.
        """
        self.sent.append(header.number)
        self.payloads.append(payload)
        self._out += bytes([ACK])

    link = Link(
      port="fake", family=InstrumentFamily.EL406, name="fake", timeout=0.05, io=Truncating()
    )
    await link.setup()
    # Not a ping: that carries a timeout of its own, and this is about the link's.
    with self.assertRaisesRegex(LinkError, "header bytes"):
      await link.request(Command(number=int(CommandNumber.PING)))

  async def test_a_reply_that_does_not_add_up_is_refused(self):
    """A reply that does not add up is refused."""

    class Corrupting(FakeInstrument):
      """A fake instrument whose replies do not match their own checksum."""

      def _reply(self, number: int) -> bytes:
        """Frame a reply with a checksum that does not cover it.

        Args:
          number: Which command is being answered.

        Returns:
          The reply.
        """
        payload = (0).to_bytes(2, "little")
        header = Header(number=number, payload_length=len(payload), check=0)
        return header.to_bytes() + payload

    link = Link(
      port="fake", family=InstrumentFamily.EL406, name="fake", timeout=0.05, io=Corrupting()
    )
    await link.setup()
    with self.assertRaisesRegex(LinkError, "did not arrive intact"):
      await link.request(Ping())
