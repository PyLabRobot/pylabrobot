"""Curated tests for Hamilton TCP protocol implementation.

Focused on high-value invariants:
- packet/frame wire shape and round-trip parsing
- DataFragment encode/decode and parser behavior
- warning/exception payload semantics
- command response auto-decode contract
"""

from __future__ import annotations

import asyncio
import struct
import unittest
from dataclasses import dataclass
from typing import Annotated, cast
from unittest.mock import AsyncMock

import pylabrobot.hamilton.tcp.introspection as introspection_mod
from pylabrobot.capabilities.liquid_handling.errors import ChannelizedError
from pylabrobot.hamilton.tcp.client import HamiltonTCPClient
from pylabrobot.hamilton.tcp.commands import TCPCommand
from pylabrobot.hamilton.tcp.error_tables import NIMBUS_ERROR_CODES
from pylabrobot.hamilton.tcp.hoi_error import (
  HoiError,
  parse_hamilton_error_entries,
  parse_hamilton_error_entry,
)
from pylabrobot.hamilton.tcp.introspection import (
  EnumInfo,
  FirmwareTree,
  FirmwareTreeNode,
  GlobalTypePool,
  HamiltonIntrospection,
  InterfaceInfo,
  MethodInfo,
  ObjectInfo,
  ObjectRegistry,
  StructInfo,
  TypeRegistry,
  flatten_firmware_tree,
)
from pylabrobot.hamilton.tcp.messages import (
  CommandMessage,
  CommandResponse,
  HoiParams,
  HoiParamsParser,
  InitMessage,
  InitResponse,
  RegistrationMessage,
  RegistrationResponse,
  parse_into_struct,
  split_hoi_params_after_warning_prefix,
)
from pylabrobot.hamilton.tcp.packets import (
  Address,
  HarpPacket,
  HoiPacket,
  IpPacket,
  RegistrationPacket,
  decode_version_byte,
  encode_version_byte,
)
from pylabrobot.hamilton.tcp.protocol import (
  HamiltonProtocol,
  Hoi2Action,
  RegistrationActionCode,
  RegistrationOptionType,
)
from pylabrobot.hamilton.tcp.wire_types import (
  I32,
  I64,
  U16,
  Bool,
  BoolArray,
  CountedFlatArray,
  HamiltonDataType,
  HcResultEntry,
  Str,
  StrArray,
  decode_fragment,
)


@dataclass
class _EnumValueWire:
  name: Str
  value: I64


@dataclass
class _EnumWire:
  enum_id: I64
  name: Str
  values: Annotated[list[_EnumValueWire], CountedFlatArray()]


@dataclass
class _GetEnumsResponse:
  enums: Annotated[list[_EnumWire], CountedFlatArray()]


class TestVersionByte(unittest.TestCase):
  def test_encode_decode_roundtrip(self):
    for major in range(16):
      for minor in range(16):
        encoded = encode_version_byte(major, minor)
        got_major, got_minor = decode_version_byte(encoded)
        self.assertEqual((got_major, got_minor), (major, minor))

  def test_encode_version_byte_invalid(self):
    with self.assertRaises(ValueError):
      encode_version_byte(16, 0)
    with self.assertRaises(ValueError):
      encode_version_byte(0, 16)


class TestPacketWireShape(unittest.TestCase):
  def test_ip_packet_roundtrip(self):
    original = IpPacket(protocol=6, payload=b"\xaa\xbb", options=b"\x10\x20")
    packed = original.pack()
    unpacked = IpPacket.unpack(packed)
    self.assertEqual(unpacked.protocol, 6)
    self.assertEqual(unpacked.options, b"\x10\x20")
    self.assertEqual(unpacked.payload, b"\xaa\xbb")

  def test_harp_action_bit_and_roundtrip(self):
    original = HarpPacket(
      src=Address(2, 1, 65535),
      dst=Address(1, 1, 257),
      seq=7,
      protocol=2,
      action_code=3,
      payload=b"\x01",
      response_required=True,
    )
    self.assertEqual(original.action, 0x13)
    unpacked = HarpPacket.unpack(original.pack())
    self.assertEqual(unpacked.action_code, 3)
    self.assertTrue(unpacked.response_required)

  def test_hoi_fragment_count_reflects_fragmented_params(self):
    frag1 = b"\x03\x00\x04\x00" + b"\x01\x02\x03\x04"
    frag2 = b"\x04\x00\x01\x00" + b"\x05"
    packet = HoiPacket(interface_id=1, action_code=3, action_id=9, params=frag1 + frag2)
    packed = packet.pack()
    self.assertEqual(packed[5], 2)

  def test_registration_packet_roundtrip(self):
    original = RegistrationPacket(
      action_code=RegistrationActionCode.HARP_PROTOCOL_REQUEST,
      response_code=0,
      req_address=Address(2, 5, 65535),
      res_address=Address(0, 0, 0),
      options=b"\x05\x02\x02\x01",
    )
    unpacked = RegistrationPacket.unpack(original.pack())
    self.assertEqual(unpacked.action_code, original.action_code)
    self.assertEqual(unpacked.req_address, original.req_address)
    self.assertEqual(unpacked.options, original.options)


class TestHoiParamsAndParser(unittest.TestCase):
  def test_bool_array_wire_shape_keeps_padding_semantics(self):
    params = HoiParams().add([True, False, True], BoolArray).build()
    self.assertEqual(params[0], HamiltonDataType.BOOL_ARRAY)
    self.assertEqual(params[1], 0x01)  # padded flag required by protocol
    self.assertEqual(params[2:4], b"\x04\x00")
    self.assertEqual(params[4:], b"\x01\x00\x01\x00")

  def test_string_array_wire_shape(self):
    params = HoiParams().add(["a", "bc"], StrArray).build()
    self.assertEqual(params[0], HamiltonDataType.STRING_ARRAY)
    self.assertEqual(params[2:4], b"\x05\x00")
    self.assertEqual(params[4:], b"a\x00bc\x00")

  def test_parser_roundtrip_mixed_payload(self):
    payload = HoiParams().add(42, I32).add("ok", Str).add(True, Bool).build()
    parser = HoiParamsParser(payload)
    values = [parser.parse_next()[1], parser.parse_next()[1], parser.parse_next()[1]]
    self.assertEqual(values, [42, "ok", True])
    self.assertFalse(parser.has_remaining())

  def test_decode_fragment_structure_array(self):
    p1 = b"a"
    p2 = b"bc"
    inner = (
      bytes([HamiltonDataType.STRUCTURE, 0])
      + struct.pack("<H", len(p1))
      + p1
      + bytes([HamiltonDataType.STRUCTURE, 0])
      + struct.pack("<H", len(p2))
      + p2
    )
    out = decode_fragment(HamiltonDataType.STRUCTURE_ARRAY, inner)
    self.assertEqual(out, [p1, p2])

  def test_decode_fragment_unknown_type_raises(self):
    with self.assertRaises(ValueError):
      decode_fragment(0xFF, b"")


class TestMessageBuildersAndParsers(unittest.TestCase):
  def test_command_message_build_has_expected_protocol_layers(self):
    dest = Address(1, 1, 257)
    packet = CommandMessage(
      dest=dest, interface_id=1, method_id=4, params=HoiParams().add(100, I32)
    ).build(src=Address(2, 1, 65535), seq=5)
    ip = IpPacket.unpack(packet)
    harp = HarpPacket.unpack(ip.payload)
    hoi = HoiPacket.unpack(harp.payload)
    self.assertEqual(ip.protocol, 6)
    self.assertEqual(harp.protocol, 2)
    self.assertEqual(hoi.interface_id, 1)
    self.assertEqual(hoi.action_id, 4)

  def test_registration_message_roundtrip(self):
    msg = RegistrationMessage(
      dest=Address(0, 0, 65534), action_code=RegistrationActionCode.HARP_PROTOCOL_REQUEST
    )
    msg.add_registration_option(
      RegistrationOptionType.HARP_PROTOCOL_REQUEST, protocol=2, request_id=1
    )
    packet = msg.build(
      src=Address(2, 1, 65535),
      req_addr=Address(0, 0, 0),
      res_addr=Address(0, 0, 0),
      seq=1,
    )
    parsed = RegistrationResponse.from_bytes(packet)
    self.assertEqual(parsed.harp.protocol, 3)
    self.assertGreater(len(parsed.registration.options), 0)

  def test_init_response_parsing(self):
    response = (
      b"\x16\x00"
      b"\x07"
      b"\x30"
      b"\x00\x00"
      b"\x00\x00\x03\x00"
      b"\x01\x10\x00\x00\x05\x00"
      b"\x02\x10\x00\x00\x01\x00"
      b"\x04\x10\x00\x00\x1e\x00"
    )
    parsed = InitResponse.from_bytes(response)
    self.assertEqual(parsed.client_id, 5)
    self.assertEqual(parsed.connection_type, 1)
    self.assertEqual(parsed.timeout, 30)

  def test_init_message_is_protocol_7(self):
    packet = InitMessage(timeout=30).build()
    self.assertEqual(packet[2], 7)


class TestTCPCommandBehavior(unittest.TestCase):
  def test_build_requires_source_address(self):
    class Cmd(TCPCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 0
      command_id = 1

    with self.assertRaises(ValueError):
      Cmd(Address(1, 1, 257)).build()

  def test_interpret_response_auto_decodes_nested_response(self):
    class Cmd(TCPCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 1
      command_id = 0

      @dataclass(frozen=True)
      class Response:
        value: I64

    cmd = Cmd(Address(0, 0, 0))
    params = HoiParams().add(42, I64).build()
    hoi = HoiPacket(
      interface_id=1, action_code=Hoi2Action.COMMAND_RESPONSE, action_id=0, params=params
    )
    harp = HarpPacket(
      src=Address(0, 0, 0),
      dst=Address(0, 0, 0),
      seq=0,
      protocol=2,
      action_code=4,
      payload=hoi.pack(),
    )
    response = CommandResponse.from_bytes(IpPacket(protocol=6, payload=harp.pack()).pack())
    result = cmd.interpret_response(response)
    self.assertIsInstance(result, Cmd.Response)
    self.assertEqual(result.value, 42)


class TestTransportApiAlignment(unittest.TestCase):
  def test_resolve_target_accepts_address_passthrough(self):
    client = HamiltonTCPClient(host="127.0.0.1", port=0)
    addr = Address(1, 1, 257)
    got = asyncio.run(client.resolve_target(addr))
    self.assertEqual(got, addr)

  def test_resolve_target_applies_aliases(self):
    client = HamiltonTCPClient(host="127.0.0.1", port=0)

    async def _fake_resolve_path(path: str) -> Address:
      self.assertEqual(path, "Root.Child")
      return Address(1, 1, 999)

    client.resolve_path = _fake_resolve_path  # type: ignore[method-assign]
    got = asyncio.run(
      client.resolve_target("pipettor_service", aliases={"pipettor_service": "Root.Child"})
    )
    self.assertEqual(got, Address(1, 1, 999))

  def test_send_query_returns_hoi_payload_tuple(self):
    class Cmd(TCPCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 0
      command_id = 1

    class FakeClient(HamiltonTCPClient):
      async def write(self, data: bytes, timeout=None):  # type: ignore[override]
        del data, timeout

      async def _read_one_message(self, timeout=None):  # type: ignore[override]
        del timeout
        payload = HoiParams().add(123, I32).build()
        hoi = HoiPacket(
          interface_id=0, action_code=Hoi2Action.COMMAND_RESPONSE, action_id=1, params=payload
        )
        harp = HarpPacket(
          src=Address(1, 1, 257),
          dst=Address(2, 1, 65535),
          seq=1,
          protocol=2,
          action_code=4,
          payload=hoi.pack(),
        )
        return CommandResponse.from_bytes(IpPacket(protocol=6, payload=harp.pack()).pack())

    client = FakeClient(host="127.0.0.1", port=0)
    client.client_address = Address(2, 1, 65535)
    raw = asyncio.run(client.send_query(Cmd(Address(1, 1, 257))))
    assert raw is not None
    self.assertIsInstance(raw, tuple)
    self.assertEqual(raw[0], HoiParams().add(123, I32).build())

  def test_get_firmware_tree_uses_cache_and_refresh(self):
    registry = ObjectRegistry()
    registry.set_root_address(Address(1, 1, 100))

    async def _unused(*a, **k):
      raise RuntimeError("unused in this test")

    intro = HamiltonIntrospection(
      registry=registry,
      global_object_addresses=[],
      send_discovery_command=_unused,
      send_query=_unused,
    )
    counts = {"obj": 0, "sub": 0}
    root = Address(1, 1, 100)
    child = Address(1, 1, 101)

    async def fake_get_object(addr: Address) -> ObjectInfo:
      counts["obj"] += 1
      if addr == root:
        return ObjectInfo("Root", "", method_count=2, subobject_count=1, address=addr)
      return ObjectInfo("Child", "", method_count=1, subobject_count=0, address=addr)

    async def fake_get_supported(addr: Address):
      return {1, 3} if addr == root else {1}

    async def fake_get_subobject_address(_addr: Address, idx: int) -> Address:
      counts["sub"] += 1
      self.assertEqual(idx, 0)
      return child

    intro.get_object = fake_get_object  # type: ignore[method-assign, assignment]
    intro.get_supported_interface0_method_ids = fake_get_supported  # type: ignore[method-assign, assignment]
    intro.get_subobject_address = fake_get_subobject_address  # type: ignore[method-assign, assignment]

    t1 = asyncio.run(intro.get_firmware_tree())
    t2 = asyncio.run(intro.get_firmware_tree())
    t3 = asyncio.run(intro.get_firmware_tree(refresh=True))

    self.assertIs(t1, t2)
    self.assertIsNot(t1, t3)
    self.assertEqual(t1.root.path, "Root")
    self.assertEqual(len(t1.root.children), 1)
    self.assertIn("Root.Child", str(t1))
    self.assertGreaterEqual(counts["obj"], 4)  # built twice (initial + refresh)
    self.assertGreaterEqual(counts["sub"], 2)

  def test_flatten_firmware_tree_preorder(self):
    a0 = Address(1, 1, 10)
    a1 = Address(1, 1, 11)
    a2 = Address(1, 1, 12)
    o0 = ObjectInfo(name="root", version="v", method_count=1, subobject_count=2, address=a0)
    o1 = ObjectInfo(name="child", version="v", method_count=1, subobject_count=0, address=a1)
    o2 = ObjectInfo(name="other", version="v", method_count=1, subobject_count=0, address=a2)
    c1 = FirmwareTreeNode(path="R.child", address=a1, object_info=o1, children=[])
    c2 = FirmwareTreeNode(path="R.other", address=a2, object_info=o2, children=[])
    root = FirmwareTreeNode(path="R", address=a0, object_info=o0, children=[c1, c2])
    tree = FirmwareTree(root=root)
    flat = flatten_firmware_tree(tree)
    self.assertEqual([p for p, _, _ in flat], ["R", "R.child", "R.other"])

  def test_get_firmware_tree_flat_delegates_to_flatten(self):
    client = HamiltonTCPClient(host="127.0.0.1", port=0)
    a0 = Address(1, 1, 20)
    o0 = ObjectInfo(name="only", version="v", method_count=0, subobject_count=0, address=a0)
    root = FirmwareTreeNode(path="Only", address=a0, object_info=o0, children=[])
    tree = FirmwareTree(root=root)

    async def fake_get_firmware_tree(refresh: bool = False):
      del refresh
      return tree

    client.introspection.get_firmware_tree = fake_get_firmware_tree  # type: ignore[method-assign]
    got = asyncio.run(client.introspection.get_firmware_tree_flat())
    self.assertEqual(len(got), 1)
    self.assertEqual(got[0][0], "Only")
    self.assertEqual(got[0][1], a0)
    self.assertIs(got[0][2], o0)


class TestHcResultHelperUsesIntrospection(unittest.IsolatedAsyncioTestCase):
  async def test_describe_entry_routes_to_introspection(self):
    client = HamiltonTCPClient(host="127.0.0.1", port=0)
    entry = HcResultEntry(1, 1, 257, 1, 6, 0xF08)
    client.introspection.get_interface_name = AsyncMock(return_value="ITest")  # type: ignore[method-assign]
    client.introspection.get_hc_result_text = AsyncMock(  # type: ignore[method-assign]
      return_value="Simulated"
    )

    iface_name, desc = await client._describe_entry(entry)
    self.assertEqual(iface_name, "ITest")
    self.assertEqual(desc, "Simulated")

  async def test_format_entry_context_uses_method_lookup_from_introspection(self):
    client = HamiltonTCPClient(host="127.0.0.1", port=0)
    addr = Address(1, 1, 257)
    client.registry.register(
      "Root.Channel",
      ObjectInfo(name="Channel", version="", method_count=0, subobject_count=0, address=addr),
    )
    method = MethodInfo(interface_id=1, call_type=0, method_id=6, name="DoThing")
    client.introspection.get_method_by_id = AsyncMock(return_value=method)  # type: ignore[method-assign]
    entry = HcResultEntry(1, 1, 257, 1, 6, 0xF08)

    context = await client._format_entry_context(entry)
    assert context is not None
    self.assertIn("path=Root.Channel", context)
    self.assertIn("DoThing(void) -> void", context)


class TestWarningAndExceptionSemantics(unittest.TestCase):
  @staticmethod
  def _format_entry(entry: HcResultEntry) -> str:
    return (
      f"0x{entry.module_id:04X}.0x{entry.node_id:04X}.0x{entry.object_id:04X}:"
      f"0x{entry.interface_id:02X},0x{entry.action_id:04X},0x{entry.result:04X}"
    )

  @classmethod
  def _build_warning_params(cls, entries: list[HcResultEntry], tail: bytes = b"") -> bytes:
    summary = HoiParams().add(len(entries), U16).build()
    entries_frag = HoiParams().add(";".join(cls._format_entry(e) for e in entries), Str).build()
    return cast(bytes, summary + entries_frag + tail)

  def test_non_warning_action_does_not_strip(self):
    payload = HoiParams().add(True, Bool).build()
    rest, entries = split_hoi_params_after_warning_prefix(Hoi2Action.COMMAND_RESPONSE, payload)
    self.assertEqual(rest, payload)
    self.assertEqual(entries, [])

  def test_warning_prefix_strip_and_parse_entries(self):
    entries = [HcResultEntry(1, 1, 257, 1, 6, 0x8001)]
    tail = HoiParams().add(99, I32).build()
    params = self._build_warning_params(entries, tail=tail)
    rest, parsed = split_hoi_params_after_warning_prefix(Hoi2Action.COMMAND_WARNING, params)
    self.assertEqual(rest, tail)
    self.assertEqual(len(parsed), 1)
    self.assertEqual(parsed[0].result, 0x8001)
    self.assertTrue(parsed[0].is_warning)

  def test_parse_hamilton_error_entry_and_entries(self):
    e1 = HcResultEntry(1, 1, 257, 1, 6, 0x0F08)
    e2 = HcResultEntry(1, 1, 257, 1, 6, 0x0F09)

    one = HoiParams().add(self._format_entry(e1), Str).build()
    got_one = parse_hamilton_error_entry(one)
    assert got_one is not None
    self.assertEqual(got_one.result, 0x0F08)

    two = HoiParams().add(self._format_entry(e1), Str).add(self._format_entry(e2), Str).build()
    got_two = parse_hamilton_error_entries(two)
    self.assertEqual([e.result for e in got_two], [0x0F08, 0x0F09])


class TestErrorEntryChannelDetection(unittest.TestCase):
  @dataclass
  class _Ap:
    channel: int

  @dataclass
  class _CmdPrep(TCPCommand):
    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 1
    dest: Address
    aspirate_parameters: list

    def __post_init__(self):
      super().__init__(self.dest)

  def test_true_when_struct_array_has_channel(self):
    c = TestErrorEntryChannelDetection._CmdPrep(
      Address(1, 1, 1), aspirate_parameters=[TestErrorEntryChannelDetection._Ap(0)]
    )
    self.assertTrue(c.error_entries_use_physical_channels())

  @dataclass
  class _CmdVoid(TCPCommand):
    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 35
    dest: Address

    def __post_init__(self):
      super().__init__(self.dest)

  def test_false_for_void_command(self):
    c = TestErrorEntryChannelDetection._CmdVoid(Address(1, 1, 1))
    self.assertFalse(c.error_entries_use_physical_channels())

  @dataclass
  class _CmdNimbus(TCPCommand):
    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 4
    dest: Address
    channels_involved: tuple

    def __post_init__(self):
      super().__init__(self.dest)

  def test_true_when_channels_involved_present(self):
    c = TestErrorEntryChannelDetection._CmdNimbus(Address(1, 1, 1), (1, 0))
    self.assertTrue(c.error_entries_use_physical_channels())


class TestSendCommandStatusException(unittest.IsolatedAsyncioTestCase):
  @staticmethod
  def _format_wire_entry(entry: HcResultEntry) -> str:
    return (
      f"0x{entry.module_id:04X}.0x{entry.node_id:04X}.0x{entry.object_id:04X}:"
      f"0x{entry.interface_id:02X},0x{entry.action_id:04X},0x{entry.result:04X}"
    )

  async def test_void_command_raises_hoi_error(self):
    entry = HcResultEntry(1, 1, 5376, 1, 35, 0x0206)
    err_params = HoiParams().add(self._format_wire_entry(entry), Str).build()

    @dataclass
    class CmdVoid(TCPCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 1
      command_id = 35
      dest: Address

      def __post_init__(self):
        super().__init__(self.dest)

    class FakeClient(HamiltonTCPClient):
      async def write(self, data: bytes, timeout=None):  # type: ignore[override]
        del data, timeout

      async def _read_one_message(self, timeout=None):  # type: ignore[override]
        del timeout
        hoi = HoiPacket(
          interface_id=1,
          action_code=Hoi2Action.STATUS_EXCEPTION,
          action_id=0,
          params=err_params,
        )
        harp = HarpPacket(
          src=Address(1, 1, 5376),
          dst=Address(2, 1, 65535),
          seq=1,
          protocol=2,
          action_code=4,
          payload=hoi.pack(),
        )
        return CommandResponse.from_bytes(IpPacket(protocol=6, payload=harp.pack()).pack())

    client = FakeClient(host="127.0.0.1", port=0)
    client.client_address = Address(2, 1, 65535)
    client.introspection.get_interface_name = AsyncMock(return_value="MLPrep")  # type: ignore[method-assign]
    client.introspection.get_hc_result_text = AsyncMock(return_value=None)  # type: ignore[method-assign]

    cmd = CmdVoid(Address(1, 1, 5376))
    with self.assertRaises(HoiError) as ctx:
      await client.send_command(cmd)
    self.assertIn(0, ctx.exception.exceptions)
    self.assertEqual(ctx.exception.entries[0].result, 0x0206)

  async def test_channels_involved_raises_channelized_error(self):
    entry = HcResultEntry(1, 1, 257, 1, 6, 0x0F08)
    err_params = HoiParams().add(self._format_wire_entry(entry), Str).build()

    @dataclass
    class CmdPick(TCPCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 1
      command_id = 4
      dest: Address
      channels_involved: tuple

      def __post_init__(self):
        super().__init__(self.dest)

    class FakeClient(HamiltonTCPClient):
      async def write(self, data: bytes, timeout=None):  # type: ignore[override]
        del data, timeout

      async def _read_one_message(self, timeout=None):  # type: ignore[override]
        del timeout
        hoi = HoiPacket(
          interface_id=1,
          action_code=Hoi2Action.STATUS_EXCEPTION,
          action_id=0,
          params=err_params,
        )
        harp = HarpPacket(
          src=Address(1, 1, 257),
          dst=Address(2, 1, 65535),
          seq=1,
          protocol=2,
          action_code=4,
          payload=hoi.pack(),
        )
        return CommandResponse.from_bytes(IpPacket(protocol=6, payload=harp.pack()).pack())

    client = FakeClient(host="127.0.0.1", port=0)
    client.client_address = Address(2, 1, 65535)
    client.introspection.get_interface_name = AsyncMock(return_value="Pipette")  # type: ignore[method-assign]
    client.introspection.get_hc_result_text = AsyncMock(return_value=None)  # type: ignore[method-assign]

    cmd = CmdPick(Address(1, 1, 257), (1, 0))
    with self.assertRaises(ChannelizedError) as ctx:
      await client.send_command(cmd)
    self.assertIn(0, ctx.exception.errors)
    self.assertEqual(len(ctx.exception.kwargs["hoi_entries"]), 1)
    self.assertIn(0, ctx.exception.kwargs["hoi_exceptions"])


class TestHcResultDescriptionNimbusTable(unittest.IsolatedAsyncioTestCase):
  """NIMBUS_ERROR_CODES keys use interface_id in the 4th slot; describe_entry must match that."""

  async def test_lookup_uses_interface_id_not_method_id(self):
    class _NimbusClient(HamiltonTCPClient):
      _ERROR_CODES = NIMBUS_ERROR_CODES

    client = _NimbusClient(host="127.0.0.1", port=0)
    client.introspection.get_interface_name = AsyncMock(return_value="Pipette")  # type: ignore[method-assign]
    client.introspection.get_hc_result_text = AsyncMock(return_value=None)  # type: ignore[method-assign]
    entry = HcResultEntry(0x0001, 0x0001, 0x0110, 1, 6, 0x0F4E)
    _iface, desc = await client._describe_entry(entry)
    self.assertIn("Tip Detected Not Correct Tip", desc)
    entry_b = HcResultEntry(0x0001, 0x0001, 0x0110, 1, 6, 0x0F4B)
    _iface_b, desc_b = await client._describe_entry(entry_b)
    self.assertIn("No Tip Picked Up", desc_b)


class TestCountedFlatArrayDecode(unittest.TestCase):
  def test_counted_flat_array_nested_decode(self):
    data = (
      HoiParams()
      .add(1, I64)  # enum_count
      .add(1, I64)  # enum_id
      .add("E1", Str)
      .add(2, I64)  # value_count
      .add("v1", Str)
      .add(10, I64)
      .add("v2", Str)
      .add(20, I64)
      .build()
    )

    parsed = parse_into_struct(HoiParamsParser(data), _GetEnumsResponse)
    self.assertEqual(len(parsed.enums), 1)
    self.assertEqual(parsed.enums[0].name, "E1")
    self.assertEqual([v.name for v in parsed.enums[0].values], ["v1", "v2"])
    self.assertEqual([v.value for v in parsed.enums[0].values], [10, 20])

  def test_i16_array_roundtrip_decode_fragment(self):
    payload = struct.pack("<hhh", 1, 2, 3)
    self.assertEqual(decode_fragment(HamiltonDataType.I16_ARRAY, payload), [1, 2, 3])


class TestIntrospectionTypeGridInvariants(unittest.TestCase):
  """Canonical integrity guard for HOI type table edits."""

  def test_grid_shape_and_padding_row_contract(self):
    rows = introspection_mod._HOI_TYPE_ROWS
    self.assertEqual(len(rows), 31)
    self.assertTrue(all(len(row.ids) == 4 for row in rows))
    for idx, row in enumerate(rows):
      if idx == len(rows) - 1:
        self.assertEqual(row.ids, (0, 0, 0, 0))
      else:
        self.assertTrue(all(tid != 0 for tid in row.ids), msg=f"unexpected zero at row {idx}")

  def test_nonzero_ids_are_unique(self):
    all_nonzero = [tid for row in introspection_mod._HOI_TYPE_ROWS for tid in row.ids if tid != 0]
    self.assertEqual(len(all_nonzero), len(set(all_nonzero)))

  def test_empirical_id_113_overridden_to_direction_in(self):
    wire_type, direction = introspection_mod._HOI_ID_TO_WIRE[113]
    self.assertEqual(wire_type, HamiltonDataType.F32_ARRAY)
    self.assertEqual(direction, introspection_mod.Direction.In)

  def test_grid_directions_match_column_order(self):
    for row in introspection_mod._HOI_TYPE_ROWS:
      if row.ids == (0, 0, 0, 0):
        continue
      in_id, out_id, inout_id, retval_id = row.ids
      # empirical override for 113 — skip the RetVal column check for that row
      if 113 not in row.ids:
        _, d = introspection_mod._HOI_ID_TO_WIRE[retval_id]
        self.assertEqual(d, introspection_mod.Direction.RetVal)
      _, d_in = introspection_mod._HOI_ID_TO_WIRE[in_id]
      _, d_out = introspection_mod._HOI_ID_TO_WIRE[out_id]
      _, d_inout = introspection_mod._HOI_ID_TO_WIRE[inout_id]
      self.assertEqual(d_in, introspection_mod.Direction.In)
      self.assertEqual(d_out, introspection_mod.Direction.Out)
      self.assertEqual(d_inout, introspection_mod.Direction.InOut)


class TestIntrospectionTypeSetsAndClassification(unittest.TestCase):
  @staticmethod
  def _ids_for_flag(flag: str) -> tuple[int, ...]:
    """Collect all IDs from rows matching a boolean flag (is_struct_kind, is_enum_kind, etc.)."""
    ids: list[int] = []
    for row in introspection_mod._HOI_TYPE_ROWS:
      if getattr(row, flag):
        ids.extend(tid for tid in row.ids if tid != 0)
    return tuple(ids)

  def test_complex_method_and_struct_sets_are_disjoint(self):
    self.assertTrue(
      introspection_mod._COMPLEX_METHOD_TYPE_IDS.isdisjoint(
        introspection_mod._COMPLEX_STRUCT_TYPE_IDS
      )
    )

  def test_method_param_struct_and_enum_ref_types_are_disjoint(self):
    struct_wire = {HamiltonDataType.STRUCTURE, HamiltonDataType.STRUCTURE_ARRAY}
    enum_wire = {HamiltonDataType.ENUM, HamiltonDataType.ENUM_ARRAY}
    self.assertTrue(struct_wire.isdisjoint(enum_wire))

  def test_method_param_type_struct_refs_cover_all_directions(self):
    for row in introspection_mod._HOI_TYPE_ROWS:
      if not row.is_struct_kind:
        continue
      for direction, tid in zip(introspection_mod.Direction, row.ids):
        pt = introspection_mod.MethodParamType(row.wire_type, direction, source_id=2, ref_id=1)
        self.assertTrue(pt.is_struct_ref)
        self.assertFalse(pt.is_enum_ref)

  def test_struct_field_type_struct_refs_cover_wire_sentinels(self):
    for wire_type in (HamiltonDataType.STRUCTURE, HamiltonDataType.STRUCTURE_ARRAY):
      sft = introspection_mod.StructFieldType(wire_type, source_id=2, ref_id=1)
      self.assertTrue(sft.is_complex)
      self.assertTrue(sft.is_struct_ref)
      self.assertFalse(sft.is_enum_ref)

  def test_method_param_type_enum_refs_cover_all_directions(self):
    for row in introspection_mod._HOI_TYPE_ROWS:
      if not row.is_enum_kind:
        continue
      for direction, tid in zip(introspection_mod.Direction, row.ids):
        pt = introspection_mod.MethodParamType(row.wire_type, direction, source_id=2, ref_id=1)
        self.assertTrue(pt.is_enum_ref)
        self.assertFalse(pt.is_struct_ref)

  def test_struct_field_type_enum_refs_cover_wire_sentinels(self):
    for wire_type in (HamiltonDataType.ENUM, HamiltonDataType.ENUM_ARRAY):
      sft = introspection_mod.StructFieldType(wire_type, source_id=2, ref_id=1)
      self.assertTrue(sft.is_complex)
      self.assertTrue(sft.is_enum_ref)
      self.assertFalse(sft.is_struct_ref)

  def test_scalar_method_param_type_is_not_a_reference(self):
    row = next(r for r in introspection_mod._HOI_TYPE_ROWS if r.display_name == "i32")
    pt = introspection_mod.MethodParamType(row.wire_type, introspection_mod.Direction.In)
    self.assertFalse(pt.is_struct_ref)
    self.assertFalse(pt.is_enum_ref)

  def test_scalar_struct_field_type_is_not_complex_or_reference(self):
    sft = introspection_mod.StructFieldType(HamiltonDataType.F32)
    self.assertFalse(sft.is_complex)
    self.assertFalse(sft.is_struct_ref)
    self.assertFalse(sft.is_enum_ref)


class TestIntrospectionTypeParsers(unittest.TestCase):
  def test_parse_method_param_types_supports_simple_ref_and_node_global(self):
    # [i8 In] + [struct In source=2 id=1] + [struct In source=4 id=9 "01" ]
    raw = [1, 57, 2, 1, 57, 4, 9, 0x22, 0x30, 0x31, 0x22, 0x20]
    parsed = introspection_mod._parse_method_param_types(raw)
    self.assertEqual(len(parsed), 3)
    self.assertEqual([pt.wire_type for pt in parsed], [HamiltonDataType.I8, HamiltonDataType.STRUCTURE, HamiltonDataType.STRUCTURE])
    self.assertEqual([pt.direction for pt in parsed], [introspection_mod.Direction.In, introspection_mod.Direction.In, introspection_mod.Direction.In])
    self.assertEqual([pt._byte_width for pt in parsed], [1, 3, 8])
    self.assertEqual((parsed[1].source_id, parsed[1].ref_id), (2, 1))
    self.assertEqual((parsed[2].source_id, parsed[2].ref_id), (4, 9))

  def test_parse_struct_field_types_supports_simple_ref_and_node_global(self):
    # [F32 simple] + [STRUCT source=2 id=3] + [STRUCT source=4 id=7 ModHi ModLo NodeHi NodeLo]
    raw = [40, 30, 2, 3, 30, 4, 7, 0x00, 0x01, 0x00, 0x02]
    parsed = introspection_mod._parse_struct_field_types(raw)
    self.assertEqual(len(parsed), 3)
    self.assertEqual([pt.type_id for pt in parsed], [HamiltonDataType.F32, HamiltonDataType.STRUCTURE, HamiltonDataType.STRUCTURE])
    self.assertEqual([pt._byte_width for pt in parsed], [1, 3, 7])
    self.assertEqual((parsed[1].source_id, parsed[1].ref_id), (2, 3))
    self.assertEqual((parsed[2].source_id, parsed[2].ref_id), (4, 7))

  def test_struct_parser_byte_width_sum_matches_cursor_advance(self):
    raw = [40, 30, 2, 3, 30, 4, 7, 0x00, 0x01, 0x00, 0x02]
    parsed = introspection_mod._parse_struct_field_types(raw)
    bytes_used = sum(pt._byte_width for pt in parsed[:3])
    self.assertEqual(bytes_used, len(raw))


class TestHamiltonIntrospectionLazyCaches(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.addr = Address(1, 1, 99)

    async def _should_not_be_called(*a, **k):
      raise AssertionError("transport should be patched out in introspection cache tests")

    self.intro = HamiltonIntrospection(
      registry=ObjectRegistry(),
      global_object_addresses=[],
      send_discovery_command=_should_not_be_called,
      send_query=_should_not_be_called,
    )

  async def test_second_ensure_method_table_skips_get_method(self):
    info = ObjectInfo(name="O", version="", method_count=2, subobject_count=0, address=self.addr)
    self.intro.get_object = AsyncMock(return_value=info)  # type: ignore[method-assign]
    self.intro.get_supported_interface0_method_ids = AsyncMock(  # type: ignore[method-assign]
      return_value={1, 2, 4, 5, 6}
    )
    gm = AsyncMock(
      side_effect=[
        MethodInfo(1, 0, 0, "a", [], [], [], []),
        MethodInfo(1, 0, 1, "b", [], [], [], []),
      ]
    )
    self.intro.get_method = gm  # type: ignore[method-assign]
    r1 = await self.intro.ensure_method_table(self.addr)
    self.assertEqual(len(r1), 2)
    self.assertEqual(gm.call_count, 2)
    r2 = await self.intro.methods_for_interface(self.addr, 1)
    self.assertEqual(len(r2), 2)
    self.assertEqual(gm.call_count, 2)
    r3 = await self.intro.ensure_method_table(self.addr)
    self.assertIs(r1, r3)

  async def test_lazy_signature_loads_only_referenced_iface(self):
    st = StructInfo(struct_id=0, name="TipParams", fields={}, interface_id=1)
    pt = introspection_mod.MethodParamType(HamiltonDataType.STRUCTURE, introspection_mod.Direction.In, source_id=2, ref_id=1)
    m = MethodInfo(1, 0, 3, "Foo", [pt], ["p"], [], [])
    info = ObjectInfo(name="O", version="", method_count=1, subobject_count=0, address=self.addr)
    self.intro.get_object = AsyncMock(return_value=info)  # type: ignore[method-assign]
    self.intro.get_supported_interface0_method_ids = AsyncMock(  # type: ignore[method-assign]
      return_value={1, 2, 4, 5, 6}
    )
    self.intro.get_method = AsyncMock(return_value=m)  # type: ignore[method-assign]
    self.intro.ensure_global_type_pool = AsyncMock(  # type: ignore[method-assign]
      return_value=GlobalTypePool()
    )
    touched: list[int] = []

    async def fake_ensure(addr, iface_id):
      touched.append(iface_id)
      key = (addr, iface_id)
      self.intro._iface_types[key] = ({0: st}, {})

    self.intro.ensure_structs_enums = fake_ensure  # type: ignore[method-assign]

    sig = await self.intro.resolve_signature(self.addr, 1, 3)
    self.assertIn("TipParams", sig)
    self.assertEqual(touched, [1])

  async def test_lazy_signature_matches_full_registry_for_local_struct(self):
    st = StructInfo(struct_id=0, name="TipParams", fields={}, interface_id=1)
    pt = introspection_mod.MethodParamType(HamiltonDataType.STRUCTURE, introspection_mod.Direction.In, source_id=2, ref_id=1)
    m = MethodInfo(1, 0, 3, "Foo", [pt], ["p"], [], [])
    info = ObjectInfo(name="O", version="", method_count=1, subobject_count=0, address=self.addr)
    self.intro.get_object = AsyncMock(return_value=info)  # type: ignore[method-assign]
    self.intro.get_supported_interface0_method_ids = AsyncMock(  # type: ignore[method-assign]
      return_value={1, 2, 4, 5, 6}
    )
    self.intro.get_method = AsyncMock(return_value=m)  # type: ignore[method-assign]
    self.intro.get_structs = AsyncMock(return_value=[st])  # type: ignore[method-assign]
    self.intro.get_enums = AsyncMock(return_value=[])  # type: ignore[method-assign]
    self.intro.ensure_global_type_pool = AsyncMock(  # type: ignore[method-assign]
      return_value=GlobalTypePool()
    )

    lazy_sig = await self.intro.resolve_signature(self.addr, 1, 3)

    full = TypeRegistry(address=self.addr, global_pool=GlobalTypePool())
    full.methods = [m]
    full.structs[1] = {0: st}
    full_sig = m.get_signature_string(full)
    self.assertEqual(lazy_sig, full_sig)

  async def test_interface_name_and_hc_result_text_use_introspection_session_cache(self):
    self.intro.get_interfaces = AsyncMock(  # type: ignore[method-assign]
      return_value=[InterfaceInfo(interface_id=1, name="ITest", version="")]
    )
    name1 = await self.intro.get_interface_name(self.addr, 1)
    name2 = await self.intro.get_interface_name(self.addr, 1)
    self.assertEqual(name1, "ITest")
    self.assertEqual(name2, "ITest")
    self.assertEqual(self.intro.get_interfaces.call_count, 1)

    self.intro.get_supported_interface0_method_ids = AsyncMock(return_value={5, 6})  # type: ignore[method-assign]
    self.intro.get_structs = AsyncMock(return_value=[])  # type: ignore[method-assign]
    self.intro.get_enums = AsyncMock(  # type: ignore[method-assign]
      return_value=[
        EnumInfo(
          enum_id=0,
          name="HcResult",
          values={"OK": 0, "SomethingFailed": 0xF08},
        )
      ]
    )
    text1 = await self.intro.get_hc_result_text(self.addr, 1, 0xF08)
    text2 = await self.intro.get_hc_result_text(self.addr, 1, 0xF08)
    self.assertEqual(text1, "SomethingFailed")
    self.assertEqual(text2, "SomethingFailed")
    self.assertEqual(self.intro.get_enums.call_count, 1)


if __name__ == "__main__":
  unittest.main()
