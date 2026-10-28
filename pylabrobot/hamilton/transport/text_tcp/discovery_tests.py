import asyncio
import dataclasses
import socket
import time
import unittest
from typing import List, Optional, Tuple
from unittest.mock import AsyncMock, patch

from pylabrobot.hamilton.transport.text_tcp import discovery
from pylabrobot.hamilton.transport.text_tcp.discovery import (
  BROADCAST_MESSAGE,
  HamiltonInstrumentInfo,
  discover_instruments,
  parse_discovery_reply,
  resolve_host,
)

# Replies as machines send them, with every value replaced: documentation-range addresses, locally
# administered MAC addresses and made-up serial numbers, so no real machine is named. A reply
# repeats the broadcast header, writes text in double quotes (with spaces inside) and numbers bare.
REPLY = (
  b"HAMILTON_BROADCAST bdc_headerid 2271560481 bdc_counter 1 bdc_ipstate 2 "
  b'bdc_ipaddress "192.0.2.10" bdc_macaddress "02:00:00:00:00:01" '
  b'bdc_systemid "HAMILTON XRP 0000" bdc_modulename "XRPPipettor" '
  b'bdc_moduledesc "LIQUID HANDLING DEVICE" bdc_modulenumber "0000" bdc_modulemode 2 '
  b"bdc_hamsmartport 34567\r\n"
)
# A second kind of machine, whose MAC address has no colons and whose name has a space.
ARM_REPLY = (
  b"HAMILTON_BROADCAST bdc_headerid 2271560481 bdc_counter 2 bdc_ipstate 2 "
  b'bdc_ipaddress "192.0.2.20" bdc_macaddress "020000000002" '
  b'bdc_systemid "magArm 000000" bdc_modulename "magArm 000000" '
  b'bdc_moduledesc "Arm controller" bdc_modulenumber "000000" bdc_modulemode 2 '
  b"bdc_hamsmartport 0\r\n"
)


def instrument(
  ip: str, system_id: Optional[str], module_name: Optional[str]
) -> HamiltonInstrumentInfo:
  return HamiltonInstrumentInfo(ip_address=ip, system_id=system_id, module_name=module_name)


class TestTheBroadcastMessage(unittest.TestCase):
  """What goes out on the wire."""

  def test_the_message_and_the_port(self):
    self.assertEqual(BROADCAST_MESSAGE, b"HAMILTON_BROADCAST bdc_headerid 2271560481 \r\n")
    self.assertEqual(discovery.BROADCAST_PORT, 34569)


class TestParsingAReply(unittest.TestCase):
  """A reply repeats the broadcast header, then gives `bdc_<key> <value>` pairs."""

  def test_a_reply_is_read_with_its_quotes_removed(self):
    info = parse_discovery_reply(REPLY, sender="203.0.113.1")
    assert info is not None
    self.assertEqual(info.ip_address, "192.0.2.10")
    self.assertEqual(info.system_id, "HAMILTON XRP 0000")
    self.assertEqual(info.module_name, "XRPPipettor")
    self.assertEqual(info.module_description, "LIQUID HANDLING DEVICE")
    self.assertEqual(info.mac_address, "02:00:00:00:00:01")

  def test_text_in_quotes_keeps_its_spaces_and_a_bare_number_stays_a_token(self):
    info = parse_discovery_reply(ARM_REPLY, sender="203.0.113.1")
    assert info is not None
    self.assertEqual(info.system_id, "magArm 000000")
    self.assertEqual(info.module_description, "Arm controller")
    self.assertEqual(info.raw["modulemode"], "2")
    self.assertEqual(info.raw["hamsmartport"], "0")

  def test_a_mac_address_is_kept_as_the_machine_wrote_it(self):
    with_colons = parse_discovery_reply(REPLY, sender="203.0.113.1")
    without = parse_discovery_reply(ARM_REPLY, sender="203.0.113.1")
    assert with_colons is not None and without is not None
    self.assertEqual(with_colons.mac_address, "02:00:00:00:00:01")
    self.assertEqual(without.mac_address, "020000000002")

  def test_every_key_is_kept_in_raw_without_its_prefix(self):
    info = parse_discovery_reply(REPLY, sender="203.0.113.1")
    assert info is not None
    self.assertEqual(
      info.raw,
      {
        "headerid": "2271560481",
        "counter": "1",
        "ipstate": "2",
        "ipaddress": "192.0.2.10",
        "macaddress": "02:00:00:00:00:01",
        "systemid": "HAMILTON XRP 0000",
        "modulename": "XRPPipettor",
        "moduledesc": "LIQUID HANDLING DEVICE",
        "modulenumber": "0000",
        "modulemode": "2",
        "hamsmartport": "34567",
      },
    )

  def test_the_order_of_the_keys_does_not_matter(self):
    info = parse_discovery_reply(
      b'bdc_macaddress "02:00:00:00:00:01" bdc_systemid "A B" bdc_ipaddress "192.0.2.10"',
      sender="203.0.113.1",
    )
    assert info is not None
    self.assertEqual(info.system_id, "A B")
    self.assertEqual(info.ip_address, "192.0.2.10")

  def test_a_bare_value_is_read_as_well_as_a_quoted_one(self):
    info = parse_discovery_reply(b"bdc_systemid ABC bdc_ipaddress 192.0.2.10", sender="203.0.113.1")
    assert info is not None
    self.assertEqual(info.system_id, "ABC")
    self.assertEqual(info.ip_address, "192.0.2.10")

  def test_an_empty_quoted_value_is_no_value(self):
    info = parse_discovery_reply(b'bdc_systemid "" bdc_modulename "X"', sender="203.0.113.1")
    assert info is not None
    self.assertIsNone(info.system_id)
    self.assertEqual(info.raw["systemid"], "")
    self.assertEqual(info.module_name, "X")

  def test_trailing_whitespace_and_nul_bytes_are_ignored(self):
    info = parse_discovery_reply(b'bdc_systemid "ABC" \r\n\x00\x00', sender="203.0.113.1")
    assert info is not None
    self.assertEqual(info.system_id, "ABC")

  def test_without_an_address_in_the_reply_the_sender_is_the_address(self):
    info = parse_discovery_reply(b'bdc_systemid "ABC"', sender="203.0.113.1")
    assert info is not None
    self.assertEqual(info.ip_address, "203.0.113.1")
    self.assertIsNone(info.module_name)
    self.assertIsNone(info.module_description)
    self.assertIsNone(info.mac_address)

  def test_an_address_that_is_not_an_ipv4_address_is_replaced_by_the_sender(self):
    for given in ("not an address", "0.0.0.256", "", "fe80::1"):
      info = parse_discovery_reply(
        b'bdc_ipaddress "' + given.encode() + b'" bdc_systemid "ABC"', sender="203.0.113.1"
      )
      assert info is not None
      self.assertEqual(info.ip_address, "203.0.113.1", given)

  def test_our_own_broadcast_heard_back_is_not_a_reply(self):
    self.assertIsNone(parse_discovery_reply(BROADCAST_MESSAGE, sender="203.0.113.1"))

  def test_other_datagrams_are_not_replies(self):
    for data in (b"", b"\r\n", b"hello", b"\xff\xfe\x00", b"bdc_headerid 1", b'bdc_counter "3"'):
      self.assertIsNone(parse_discovery_reply(data, sender="203.0.113.1"), data)

  def test_a_reply_with_bytes_that_are_not_ascii_is_still_read(self):
    info = parse_discovery_reply(b'bdc_systemid "VANTAGE-\xe9TEST"', sender="203.0.113.1")
    assert info is not None and info.system_id is not None
    self.assertTrue(info.system_id.startswith("VANTAGE-"))

  def test_the_info_cannot_be_changed_and_can_be_hashed_and_compared(self):
    a = parse_discovery_reply(REPLY, sender="203.0.113.1")
    b = parse_discovery_reply(REPLY, sender="203.0.113.2")
    assert a is not None and b is not None
    with self.assertRaises(dataclasses.FrozenInstanceError):
      a.ip_address = "192.0.2.99"  # type: ignore[misc]
    self.assertEqual(a, b)
    self.assertEqual(len({a, b}), 1)


class TestBroadcastTargets(unittest.TestCase):
  """Where the broadcast is sent."""

  @staticmethod
  def addresses(*addresses: str) -> List[Tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0)) for address in addresses]

  def test_each_network_gets_its_x_y_z_255_and_the_limited_broadcast_comes_last(self):
    with patch.object(
      socket, "getaddrinfo", return_value=self.addresses("192.0.2.39", "198.51.100.21")
    ):
      self.assertEqual(
        discovery._broadcast_targets(), ["192.0.2.255", "198.51.100.255", "<broadcast>"]
      )

  def test_loopback_is_left_out_and_a_network_is_listed_once(self):
    with patch.object(
      socket,
      "getaddrinfo",
      return_value=self.addresses("127.0.1.1", "192.0.2.5", "192.0.2.77", "127.0.0.1"),
    ):
      self.assertEqual(discovery._broadcast_targets(), ["192.0.2.255", "<broadcast>"])

  def test_a_hostname_that_does_not_resolve_leaves_only_the_limited_broadcast(self):
    with patch.object(socket, "getaddrinfo", side_effect=socket.gaierror("no such host")):
      self.assertEqual(discovery._broadcast_targets(), ["<broadcast>"])


class LocalInstrument(asyncio.DatagramProtocol):
  """A UDP listener standing in for a machine: it records what it receives and answers."""

  def __init__(self, reply: Optional[bytes], times: int = 1):
    self.reply = reply
    self.times = times
    self.received: List[bytes] = []
    self.transport: Optional[asyncio.DatagramTransport] = None

  def connection_made(self, transport):
    self.transport = transport

  def datagram_received(self, data, addr):
    self.received.append(data)
    if self.reply is not None and self.transport is not None:
      for _ in range(self.times):
        self.transport.sendto(self.reply, addr)


class TestOverUDP(unittest.IsolatedAsyncioTestCase):
  """The broadcast and its answers over real UDP, on the loopback interface."""

  async def start_instrument(
    self, reply: Optional[bytes], times: int = 1
  ) -> Tuple[LocalInstrument, int]:
    loop = asyncio.get_running_loop()
    protocol = LocalInstrument(reply, times)
    transport, _ = await loop.create_datagram_endpoint(
      lambda: protocol, local_addr=("127.0.0.1", 0)
    )
    self.addCleanup(transport.close)
    return protocol, transport.get_extra_info("sockname")[1]

  async def test_the_exact_message_is_sent_and_the_answer_is_collected(self):
    instrument_, port = await self.start_instrument(REPLY)
    loop = asyncio.get_running_loop()
    replies = await loop.run_in_executor(None, discovery._exchange, ["127.0.0.1"], 0.3, port)
    self.assertEqual(instrument_.received, [BROADCAST_MESSAGE])
    self.assertEqual(replies, [(REPLY, "127.0.0.1")])

  async def test_a_target_that_cannot_be_sent_to_does_not_stop_the_others(self):
    instrument_, port = await self.start_instrument(REPLY)
    loop = asyncio.get_running_loop()
    # An IPv6 address cannot be sent to from an IPv4 socket, so the first send fails, at once and
    # without a name lookup.
    replies = await loop.run_in_executor(None, discovery._exchange, ["::1", "127.0.0.1"], 0.3, port)
    self.assertEqual(replies, [(REPLY, "127.0.0.1")])

  async def test_no_answer_returns_nothing_after_about_the_timeout(self):
    _, port = await self.start_instrument(None)
    loop = asyncio.get_running_loop()
    started = time.monotonic()
    replies = await loop.run_in_executor(None, discovery._exchange, ["127.0.0.1"], 0.2, port)
    self.assertEqual(replies, [])
    self.assertGreaterEqual(time.monotonic() - started, 0.15)

  async def test_discover_instruments_lists_a_machine_once_however_often_it_answers(self):
    _, port = await self.start_instrument(REPLY, times=3)
    with (
      patch.object(discovery, "_broadcast_targets", return_value=["127.0.0.1"]),
      patch.object(discovery, "BROADCAST_PORT", port),
    ):
      found = await discover_instruments(timeout=0.3)
    self.assertEqual(len(found), 1)
    self.assertEqual(found[0].ip_address, "192.0.2.10")
    self.assertEqual(found[0].system_id, "HAMILTON XRP 0000")

  async def test_discover_instruments_ignores_what_is_not_a_machine_reply(self):
    _, port = await self.start_instrument(BROADCAST_MESSAGE)
    with (
      patch.object(discovery, "_broadcast_targets", return_value=["127.0.0.1"]),
      patch.object(discovery, "BROADCAST_PORT", port),
    ):
      self.assertEqual(await discover_instruments(timeout=0.3), [])


class TestDiscoverInstruments(unittest.IsolatedAsyncioTestCase):
  """What comes back is turned into one entry per machine."""

  async def test_a_machine_that_answers_twice_is_listed_once_and_machines_keep_their_order(self):
    # The broadcast reaches a machine through more than one target, so it answers once for each,
    # with a counter that has gone up.
    replies = [
      (ARM_REPLY, "192.0.2.20"),
      (REPLY, "192.0.2.10"),
      (ARM_REPLY.replace(b"bdc_counter 2", b"bdc_counter 3"), "192.0.2.20"),
      (REPLY.replace(b"bdc_counter 1", b"bdc_counter 2"), "192.0.2.10"),
    ]
    with (
      patch.object(discovery, "_broadcast_targets", return_value=["192.0.2.255"]),
      patch.object(discovery, "_exchange", return_value=replies) as exchange,
    ):
      found = await discover_instruments(timeout=0.5)
    exchange.assert_called_once_with(["192.0.2.255"], 0.5, discovery.BROADCAST_PORT)
    self.assertEqual([info.ip_address for info in found], ["192.0.2.20", "192.0.2.10"])
    self.assertEqual([info.raw["counter"] for info in found], ["2", "1"])


class TestTheDefaultWait(unittest.IsolatedAsyncioTestCase):
  """Answers are waited for half a second unless the caller says otherwise."""

  async def test_discover_instruments_waits_half_a_second_by_default(self):
    with (
      patch.object(discovery, "_broadcast_targets", return_value=["192.0.2.255"]),
      patch.object(discovery, "_exchange", return_value=[]) as exchange,
    ):
      await discover_instruments()
    exchange.assert_called_once_with(["192.0.2.255"], 0.5, discovery.BROADCAST_PORT)

  async def test_resolve_host_waits_half_a_second_by_default(self):
    found = [instrument("192.0.2.10", "ABC", None)]
    with patch.object(
      discovery, "discover_instruments", new=AsyncMock(return_value=found)
    ) as discover:
      await resolve_host("ABC")
    discover.assert_awaited_once_with(0.5)


class TestResolveHost(unittest.IsolatedAsyncioTestCase):
  """An address stays an address, and a name is looked up."""

  async def test_an_ipv4_address_is_returned_as_it_is_without_discovery(self):
    with patch.object(discovery, "discover_instruments", new=AsyncMock()) as discover:
      self.assertEqual(await resolve_host("192.0.2.123"), "192.0.2.123")
      discover.assert_not_called()

  async def test_localhost_is_resolved_by_the_system(self):
    answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
    with (
      patch.object(socket, "getaddrinfo", return_value=answer) as resolver,
      patch.object(discovery, "discover_instruments", new=AsyncMock()) as discover,
    ):
      self.assertEqual(await resolve_host("localhost"), "127.0.0.1")
      self.assertEqual(resolver.call_args.args[0], "localhost")
      discover.assert_not_called()

  async def test_a_dot_local_name_is_resolved_by_the_system(self):
    with (
      patch.object(discovery, "_lookup_dns", new=AsyncMock(return_value="192.0.2.7")) as dns,
      patch.object(discovery, "discover_instruments", new=AsyncMock()) as discover,
    ):
      self.assertEqual(await resolve_host("Vantage.LOCAL"), "192.0.2.7")
      dns.assert_awaited_once_with("Vantage.LOCAL")
      discover.assert_not_called()

  async def test_a_name_that_the_system_cannot_resolve_raises_value_error(self):
    with (
      patch.object(socket, "getaddrinfo", side_effect=socket.gaierror("no such host")),
      patch.object(discovery, "discover_instruments", new=AsyncMock()) as discover,
    ):
      with self.assertRaises(ValueError) as caught:
        await resolve_host("no-such-machine.local")
      discover.assert_not_called()
    self.assertIn("no-such-machine.local", str(caught.exception))

  async def test_a_name_is_matched_against_system_id_and_module_name_ignoring_case(self):
    found = [
      instrument("192.0.2.10", "VANTAGE-TEST", "Vantage Test Lab"),
      instrument("192.0.2.11", "OTHER", "Other Machine"),
    ]
    with patch.object(discovery, "discover_instruments", new=AsyncMock(return_value=found)):
      self.assertEqual(await resolve_host("vantage-test"), "192.0.2.10")
      self.assertEqual(await resolve_host("VANTAGE TEST LAB"), "192.0.2.10")
      self.assertEqual(await resolve_host("other"), "192.0.2.11")
      self.assertEqual(await resolve_host("Other Machine"), "192.0.2.11")

  async def test_names_as_machines_write_them_resolve_to_their_addresses(self):
    found = [
      info
      for info in (
        parse_discovery_reply(REPLY, sender="192.0.2.10"),
        parse_discovery_reply(ARM_REPLY, sender="192.0.2.20"),
      )
      if info is not None
    ]
    with patch.object(discovery, "discover_instruments", new=AsyncMock(return_value=found)):
      self.assertEqual(await resolve_host("HAMILTON XRP 0000"), "192.0.2.10")
      self.assertEqual(await resolve_host("xrppipettor"), "192.0.2.10")
      self.assertEqual(await resolve_host("MAGARM 000000"), "192.0.2.20")

  async def test_the_timeout_goes_to_the_discovery(self):
    found = [instrument("192.0.2.10", "VANTAGE-TEST", None)]
    with patch.object(
      discovery, "discover_instruments", new=AsyncMock(return_value=found)
    ) as discover:
      await resolve_host("VANTAGE-TEST", timeout=1.5)
      discover.assert_awaited_once_with(1.5)

  async def test_a_name_nobody_answered_to_raises_value_error_naming_those_that_did(self):
    found = [instrument("192.0.2.10", "VANTAGE-TEST", None)]
    with patch.object(discovery, "discover_instruments", new=AsyncMock(return_value=found)):
      with self.assertRaises(ValueError) as caught:
        await resolve_host("nothing-like-it")
    self.assertIn("nothing-like-it", str(caught.exception))
    self.assertIn("VANTAGE-TEST (192.0.2.10)", str(caught.exception))

  async def test_no_answer_at_all_raises_value_error(self):
    with patch.object(discovery, "discover_instruments", new=AsyncMock(return_value=[])):
      with self.assertRaises(ValueError) as caught:
        await resolve_host("anything")
    self.assertIn("none", str(caught.exception))

  async def test_a_name_that_matches_two_machines_raises_value_error(self):
    found = [
      instrument("192.0.2.10", "LAB", None),
      instrument("192.0.2.11", None, "lab"),
    ]
    with patch.object(discovery, "discover_instruments", new=AsyncMock(return_value=found)):
      with self.assertRaises(ValueError) as caught:
        await resolve_host("Lab")
    self.assertIn("192.0.2.10", str(caught.exception))
    self.assertIn("192.0.2.11", str(caught.exception))

  async def test_a_string_that_is_not_a_valid_address_is_treated_as_a_name(self):
    with patch.object(
      discovery, "discover_instruments", new=AsyncMock(return_value=[])
    ) as discover:
      with self.assertRaises(ValueError):
        await resolve_host("999.1.1.1")
      discover.assert_awaited_once()


if __name__ == "__main__":
  unittest.main()
