import dataclasses
import socket
from typing import Any, List, Optional, Sequence, Tuple
from unittest.mock import patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.hamilton.transport.text_tcp import discovery
from pylabrobot.hamilton.transport.text_tcp.discovery import (
  BROADCAST_MESSAGE,
  HamiltonInstrumentInfo,
  discover_instruments,
  parse_discovery_reply,
  resolve_host,
)
from pylabrobot.testing.concurrency import AnyioTestBase

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


class TestTheBroadcastMessage:
  """What goes out on the wire."""

  def test_the_message_and_the_port(self) -> None:
    assert BROADCAST_MESSAGE == b"HAMILTON_BROADCAST bdc_headerid 2271560481 \r\n"
    assert discovery.BROADCAST_PORT == 34569


class TestParsingAReply:
  """A reply repeats the broadcast header, then gives `bdc_<key> <value>` pairs."""

  def test_a_reply_is_read_with_its_quotes_removed(self) -> None:
    info = parse_discovery_reply(REPLY, sender="203.0.113.1")
    assert info is not None
    assert info.ip_address == "192.0.2.10"
    assert info.system_id == "HAMILTON XRP 0000"
    assert info.module_name == "XRPPipettor"
    assert info.module_description == "LIQUID HANDLING DEVICE"
    assert info.mac_address == "02:00:00:00:00:01"

  def test_text_in_quotes_keeps_its_spaces_and_a_bare_number_stays_a_token(self) -> None:
    info = parse_discovery_reply(ARM_REPLY, sender="203.0.113.1")
    assert info is not None
    assert info.system_id == "magArm 000000"
    assert info.module_description == "Arm controller"
    assert info.raw["modulemode"] == "2"
    assert info.raw["hamsmartport"] == "0"

  def test_a_mac_address_is_kept_as_the_machine_wrote_it(self) -> None:
    with_colons = parse_discovery_reply(REPLY, sender="203.0.113.1")
    without = parse_discovery_reply(ARM_REPLY, sender="203.0.113.1")
    assert with_colons is not None and without is not None
    assert with_colons.mac_address == "02:00:00:00:00:01"
    assert without.mac_address == "020000000002"

  def test_every_key_is_kept_in_raw_without_its_prefix(self) -> None:
    info = parse_discovery_reply(REPLY, sender="203.0.113.1")
    assert info is not None
    assert info.raw == {
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
    }

  def test_the_order_of_the_keys_does_not_matter(self) -> None:
    info = parse_discovery_reply(
      b'bdc_macaddress "02:00:00:00:00:01" bdc_systemid "A B" bdc_ipaddress "192.0.2.10"',
      sender="203.0.113.1",
    )
    assert info is not None
    assert info.system_id == "A B"
    assert info.ip_address == "192.0.2.10"

  def test_a_bare_value_is_read_as_well_as_a_quoted_one(self) -> None:
    info = parse_discovery_reply(b"bdc_systemid ABC bdc_ipaddress 192.0.2.10", sender="203.0.113.1")
    assert info is not None
    assert info.system_id == "ABC"
    assert info.ip_address == "192.0.2.10"

  def test_an_empty_quoted_value_is_no_value(self) -> None:
    info = parse_discovery_reply(b'bdc_systemid "" bdc_modulename "X"', sender="203.0.113.1")
    assert info is not None
    assert info.system_id is None
    assert info.raw["systemid"] == ""
    assert info.module_name == "X"

  def test_trailing_whitespace_and_nul_bytes_are_ignored(self) -> None:
    info = parse_discovery_reply(b'bdc_systemid "ABC" \r\n\x00\x00', sender="203.0.113.1")
    assert info is not None
    assert info.system_id == "ABC"

  def test_without_an_address_in_the_reply_the_sender_is_the_address(self) -> None:
    info = parse_discovery_reply(b'bdc_systemid "ABC"', sender="203.0.113.1")
    assert info is not None
    assert info.ip_address == "203.0.113.1"
    assert info.module_name is None
    assert info.module_description is None
    assert info.mac_address is None

  def test_an_address_that_is_not_an_ipv4_address_is_replaced_by_the_sender(self) -> None:
    for given in ("not an address", "0.0.0.256", "", "fe80::1"):
      info = parse_discovery_reply(
        b'bdc_ipaddress "' + given.encode() + b'" bdc_systemid "ABC"', sender="203.0.113.1"
      )
      assert info is not None
      assert info.ip_address == "203.0.113.1", given

  def test_our_own_broadcast_heard_back_is_not_a_reply(self) -> None:
    assert parse_discovery_reply(BROADCAST_MESSAGE, sender="203.0.113.1") is None

  def test_other_datagrams_are_not_replies(self) -> None:
    for data in (b"", b"\r\n", b"hello", b"\xff\xfe\x00", b"bdc_headerid 1", b'bdc_counter "3"'):
      assert parse_discovery_reply(data, sender="203.0.113.1") is None, data

  def test_a_reply_with_bytes_that_are_not_ascii_is_still_read(self) -> None:
    info = parse_discovery_reply(b'bdc_systemid "VANTAGE-\xe9TEST"', sender="203.0.113.1")
    assert info is not None and info.system_id is not None
    assert info.system_id.startswith("VANTAGE-")

  def test_the_info_cannot_be_changed_and_can_be_hashed_and_compared(self) -> None:
    a = parse_discovery_reply(REPLY, sender="203.0.113.1")
    b = parse_discovery_reply(REPLY, sender="203.0.113.2")
    assert a is not None and b is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
      a.ip_address = "192.0.2.99"  # type: ignore[misc]
    assert a == b
    assert len({a, b}) == 1


class TestBroadcastTargets:
  """Where the broadcast is sent."""

  @staticmethod
  def addresses(*addresses: str) -> List[Tuple[Any, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0)) for address in addresses]

  def test_each_network_gets_its_x_y_z_255_and_the_limited_broadcast_comes_last(self) -> None:
    with patch.object(
      socket, "getaddrinfo", return_value=self.addresses("192.0.2.39", "198.51.100.21")
    ):
      assert discovery._broadcast_targets() == ["192.0.2.255", "198.51.100.255", "<broadcast>"]

  def test_loopback_is_left_out_and_a_network_is_listed_once(self) -> None:
    with patch.object(
      socket,
      "getaddrinfo",
      return_value=self.addresses("127.0.1.1", "192.0.2.5", "192.0.2.77", "127.0.0.1"),
    ):
      assert discovery._broadcast_targets() == ["192.0.2.255", "<broadcast>"]

  def test_a_hostname_that_does_not_resolve_leaves_only_the_limited_broadcast(self) -> None:
    with patch.object(socket, "getaddrinfo", side_effect=socket.gaierror("no such host")):
      assert discovery._broadcast_targets() == ["<broadcast>"]


class LocalInstrument:
  """A UDP listener standing in for a machine: it records what it receives and answers."""

  def __init__(self, reply: Optional[bytes], times: int = 1) -> None:
    self.reply = reply
    self.times = times
    self.received: List[bytes] = []

  async def serve(self, sock: anyio.abc.UDPSocket) -> None:
    while True:
      try:
        data, (host, port) = await sock.receive()
      except (OSError, anyio.BrokenResourceError, anyio.ClosedResourceError):
        return
      self.received.append(data)
      if self.reply is not None:
        for _ in range(self.times):
          await sock.sendto(self.reply, host, port)


class TestOverUDP(AnyioTestBase):
  """The broadcast and its answers over real UDP, on the loopback interface."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self._stack = stack

  async def start_instrument(
    self, reply: Optional[bytes], times: int = 1
  ) -> Tuple[LocalInstrument, int]:
    sock = await anyio.create_udp_socket(local_host="127.0.0.1")
    await self._stack.enter_async_context(sock)
    tg = await self._stack.enter_task_group()
    self._stack.callback(tg.cancel_scope.cancel)
    inst = LocalInstrument(reply, times)
    tg.start_soon(inst.serve, sock)
    port = int(sock.extra(anyio.abc.SocketAttribute.local_address)[1])
    return inst, port

  async def test_the_exact_message_is_sent_and_the_answer_is_collected(self) -> None:
    instrument_, port = await self.start_instrument(REPLY)
    replies = await discovery._exchange(["127.0.0.1"], 0.3, port)
    assert instrument_.received == [BROADCAST_MESSAGE]
    assert replies == [(REPLY, "127.0.0.1")]

  async def test_a_target_that_cannot_be_sent_to_does_not_stop_the_others(self) -> None:
    _, port = await self.start_instrument(REPLY)
    # An IPv6 address cannot be sent to from an IPv4 socket, so the first send fails, at once and
    # without a name lookup.
    replies = await discovery._exchange(["::1", "127.0.0.1"], 0.3, port)
    assert replies == [(REPLY, "127.0.0.1")]

  async def test_no_answer_returns_nothing_after_about_the_timeout(self) -> None:
    _, port = await self.start_instrument(None)
    started = anyio.current_time()
    replies = await discovery._exchange(["127.0.0.1"], 0.2, port)
    assert replies == []
    assert anyio.current_time() - started >= 0.15

  async def test_discover_instruments_lists_a_machine_once_however_often_it_answers(self) -> None:
    _, port = await self.start_instrument(REPLY, times=3)
    with (
      patch.object(discovery, "_broadcast_targets", return_value=["127.0.0.1"]),
      patch.object(discovery, "BROADCAST_PORT", port),
    ):
      found = await discover_instruments(timeout=0.3)
    assert len(found) == 1
    assert found[0].ip_address == "192.0.2.10"
    assert found[0].system_id == "HAMILTON XRP 0000"

  async def test_discover_instruments_ignores_what_is_not_a_machine_reply(self) -> None:
    _, port = await self.start_instrument(BROADCAST_MESSAGE)
    with (
      patch.object(discovery, "_broadcast_targets", return_value=["127.0.0.1"]),
      patch.object(discovery, "BROADCAST_PORT", port),
    ):
      assert await discover_instruments(timeout=0.3) == []


class TestDiscoverInstruments(AnyioTestBase):
  """What comes back is turned into one entry per machine."""

  async def test_a_machine_that_answers_twice_is_listed_once_and_machines_keep_their_order(
    self,
  ) -> None:
    # The broadcast reaches a machine through more than one target, so it answers once for each,
    # with a counter that has gone up.
    replies = [
      (ARM_REPLY, "192.0.2.20"),
      (REPLY, "192.0.2.10"),
      (ARM_REPLY.replace(b"bdc_counter 2", b"bdc_counter 3"), "192.0.2.20"),
      (REPLY.replace(b"bdc_counter 1", b"bdc_counter 2"), "192.0.2.10"),
    ]
    calls: List[Tuple[Sequence[str], float, int]] = []

    async def fake_exchange(
      targets: Sequence[str], timeout: float, port: int
    ) -> List[Tuple[bytes, str]]:
      calls.append((targets, timeout, port))
      return replies

    with (
      patch.object(discovery, "_broadcast_targets", return_value=["192.0.2.255"]),
      patch.object(discovery, "_exchange", new=fake_exchange),
    ):
      found = await discover_instruments(timeout=0.5)
    assert calls == [(["192.0.2.255"], 0.5, discovery.BROADCAST_PORT)]
    assert [info.ip_address for info in found] == ["192.0.2.20", "192.0.2.10"]
    assert [info.raw["counter"] for info in found] == ["2", "1"]


class TestTheDefaultWait(AnyioTestBase):
  """Answers are waited for half a second unless the caller says otherwise."""

  async def test_discover_instruments_waits_half_a_second_by_default(self) -> None:
    calls: List[Tuple[Sequence[str], float, int]] = []

    async def fake_exchange(
      targets: Sequence[str], timeout: float, port: int
    ) -> List[Tuple[bytes, str]]:
      calls.append((targets, timeout, port))
      return []

    with (
      patch.object(discovery, "_broadcast_targets", return_value=["192.0.2.255"]),
      patch.object(discovery, "_exchange", new=fake_exchange),
    ):
      await discover_instruments()
    assert calls == [(["192.0.2.255"], 0.5, discovery.BROADCAST_PORT)]

  async def test_resolve_host_waits_half_a_second_by_default(self) -> None:
    found = [instrument("192.0.2.10", "ABC", None)]
    calls: List[float] = []

    async def fake_discover(
      timeout: float = discovery.DEFAULT_TIMEOUT,
    ) -> List[HamiltonInstrumentInfo]:
      calls.append(timeout)
      return found

    with patch.object(discovery, "discover_instruments", new=fake_discover):
      await resolve_host("ABC")
    assert calls == [0.5]


class TestResolveHost(AnyioTestBase):
  """An address stays an address, and a name is looked up."""

  async def test_an_ipv4_address_is_returned_as_it_is_without_discovery(self) -> None:
    called = False

    async def fake_discover(
      timeout: float = discovery.DEFAULT_TIMEOUT,
    ) -> List[HamiltonInstrumentInfo]:
      nonlocal called
      called = True
      return []

    with patch.object(discovery, "discover_instruments", new=fake_discover):
      assert await resolve_host("192.0.2.123") == "192.0.2.123"
    assert not called

  async def test_localhost_is_resolved_by_the_system(self) -> None:
    answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
    called = False

    async def fake_discover(
      timeout: float = discovery.DEFAULT_TIMEOUT,
    ) -> List[HamiltonInstrumentInfo]:
      nonlocal called
      called = True
      return []

    with (
      patch.object(socket, "getaddrinfo", return_value=answer) as resolver,
      patch.object(discovery, "discover_instruments", new=fake_discover),
    ):
      assert await resolve_host("localhost") == "127.0.0.1"
      assert resolver.call_args.args[0] == "localhost"
    assert not called

  async def test_a_dot_local_name_is_resolved_by_the_system(self) -> None:
    dns_calls: List[str] = []
    discover_called = False

    async def fake_dns(name: str) -> str:
      dns_calls.append(name)
      return "192.0.2.7"

    async def fake_discover(
      timeout: float = discovery.DEFAULT_TIMEOUT,
    ) -> List[HamiltonInstrumentInfo]:
      nonlocal discover_called
      discover_called = True
      return []

    with (
      patch.object(discovery, "_lookup_dns", new=fake_dns),
      patch.object(discovery, "discover_instruments", new=fake_discover),
    ):
      assert await resolve_host("Vantage.LOCAL") == "192.0.2.7"
    assert dns_calls == ["Vantage.LOCAL"]
    assert not discover_called

  async def test_a_name_that_the_system_cannot_resolve_raises_value_error(self) -> None:
    discover_called = False

    async def fake_discover(
      timeout: float = discovery.DEFAULT_TIMEOUT,
    ) -> List[HamiltonInstrumentInfo]:
      nonlocal discover_called
      discover_called = True
      return []

    with (
      patch.object(socket, "getaddrinfo", side_effect=socket.gaierror("no such host")),
      patch.object(discovery, "discover_instruments", new=fake_discover),
    ):
      with pytest.raises(ValueError, match=r"no-such-machine\.local"):
        await resolve_host("no-such-machine.local")
    assert not discover_called

  async def test_a_name_is_matched_against_system_id_and_module_name_ignoring_case(self) -> None:
    found = [
      instrument("192.0.2.10", "VANTAGE-TEST", "Vantage Test Lab"),
      instrument("192.0.2.11", "OTHER", "Other Machine"),
    ]

    async def fake_discover(
      timeout: float = discovery.DEFAULT_TIMEOUT,
    ) -> List[HamiltonInstrumentInfo]:
      return found

    with patch.object(discovery, "discover_instruments", new=fake_discover):
      assert await resolve_host("vantage-test") == "192.0.2.10"
      assert await resolve_host("VANTAGE TEST LAB") == "192.0.2.10"
      assert await resolve_host("other") == "192.0.2.11"
      assert await resolve_host("Other Machine") == "192.0.2.11"

  async def test_names_as_machines_write_them_resolve_to_their_addresses(self) -> None:
    found = [
      info
      for info in (
        parse_discovery_reply(REPLY, sender="192.0.2.10"),
        parse_discovery_reply(ARM_REPLY, sender="192.0.2.20"),
      )
      if info is not None
    ]

    async def fake_discover(
      timeout: float = discovery.DEFAULT_TIMEOUT,
    ) -> List[HamiltonInstrumentInfo]:
      return found

    with patch.object(discovery, "discover_instruments", new=fake_discover):
      assert await resolve_host("HAMILTON XRP 0000") == "192.0.2.10"
      assert await resolve_host("xrppipettor") == "192.0.2.10"
      assert await resolve_host("MAGARM 000000") == "192.0.2.20"

  async def test_the_timeout_goes_to_the_discovery(self) -> None:
    found = [instrument("192.0.2.10", "VANTAGE-TEST", None)]
    calls: List[float] = []

    async def fake_discover(
      timeout: float = discovery.DEFAULT_TIMEOUT,
    ) -> List[HamiltonInstrumentInfo]:
      calls.append(timeout)
      return found

    with patch.object(discovery, "discover_instruments", new=fake_discover):
      await resolve_host("VANTAGE-TEST", timeout=1.5)
    assert calls == [1.5]

  async def test_a_name_nobody_answered_to_raises_value_error_naming_those_that_did(self) -> None:
    found = [instrument("192.0.2.10", "VANTAGE-TEST", None)]

    async def fake_discover(
      timeout: float = discovery.DEFAULT_TIMEOUT,
    ) -> List[HamiltonInstrumentInfo]:
      return found

    with patch.object(discovery, "discover_instruments", new=fake_discover):
      with pytest.raises(ValueError) as caught:
        await resolve_host("nothing-like-it")
    assert "nothing-like-it" in str(caught.value)
    assert "VANTAGE-TEST (192.0.2.10)" in str(caught.value)

  async def test_no_answer_at_all_raises_value_error(self) -> None:
    async def fake_discover(
      timeout: float = discovery.DEFAULT_TIMEOUT,
    ) -> List[HamiltonInstrumentInfo]:
      return []

    with patch.object(discovery, "discover_instruments", new=fake_discover):
      with pytest.raises(ValueError, match="none"):
        await resolve_host("anything")

  async def test_a_name_that_matches_two_machines_raises_value_error(self) -> None:
    found = [
      instrument("192.0.2.10", "LAB", None),
      instrument("192.0.2.11", None, "lab"),
    ]

    async def fake_discover(
      timeout: float = discovery.DEFAULT_TIMEOUT,
    ) -> List[HamiltonInstrumentInfo]:
      return found

    with patch.object(discovery, "discover_instruments", new=fake_discover):
      with pytest.raises(ValueError) as caught:
        await resolve_host("Lab")
    assert "192.0.2.10" in str(caught.value)
    assert "192.0.2.11" in str(caught.value)

  async def test_a_string_that_is_not_a_valid_address_is_treated_as_a_name(self) -> None:
    calls = 0

    async def fake_discover(
      timeout: float = discovery.DEFAULT_TIMEOUT,
    ) -> List[HamiltonInstrumentInfo]:
      nonlocal calls
      calls += 1
      return []

    with patch.object(discovery, "discover_instruments", new=fake_discover):
      with pytest.raises(ValueError):
        await resolve_host("999.1.1.1")
    assert calls == 1
