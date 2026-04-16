"""Curated tests for Hamilton TCP protocol implementation.

Focused on high-value invariants:
- packet/frame wire shape and round-trip parsing
- DataFragment encode/decode and parser behavior
- warning/exception payload semantics
- command response auto-decode contract
"""

from __future__ import annotations

import struct
import unittest
import asyncio
from dataclasses import dataclass
from typing import Annotated

from pylabrobot.hamilton.tcp.client import HamiltonTCPClient
from pylabrobot.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.hamilton.tcp.introspection import HamiltonIntrospection, ObjectInfo, ObjectRegistry
from pylabrobot.hamilton.tcp.messages import (
  CommandMessage,
  CommandResponse,
  HoiParams,
  HoiParamsParser,
  InitMessage,
  InitResponse,
  RegistrationMessage,
  RegistrationResponse,
  parse_hamilton_error_entries,
  parse_hamilton_error_entry,
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
  Str,
  StrArray,
  U16,
  Bool,
  BoolArray,
  CountedFlatArray,
  HamiltonDataType,
  HcResultEntry,
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
    original = IpPacket(protocol=6, payload=b"\xAA\xBB", options=b"\x10\x20")
    packed = original.pack()
    unpacked = IpPacket.unpack(packed)
    self.assertEqual(unpacked.protocol, 6)
    self.assertEqual(unpacked.options, b"\x10\x20")
    self.assertEqual(unpacked.payload, b"\xAA\xBB")

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


class TestHamiltonCommandBehavior(unittest.TestCase):
  def test_build_requires_source_address(self):
    class Cmd(HamiltonCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 0
      command_id = 1

    with self.assertRaises(ValueError):
      Cmd(Address(1, 1, 257)).build()

  def test_interpret_response_auto_decodes_nested_response(self):
    class Cmd(HamiltonCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 1
      command_id = 0

      @dataclass(frozen=True)
      class Response:
        value: I64

    cmd = Cmd(Address(0, 0, 0))
    params = HoiParams().add(42, I64).build()
    hoi = HoiPacket(interface_id=1, action_code=Hoi2Action.COMMAND_RESPONSE, action_id=0, params=params)
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
    got = asyncio.run(client.resolve_target("pipettor_service", aliases={"pipettor_service": "Root.Child"}))
    self.assertEqual(got, Address(1, 1, 999))

  def test_send_command_return_raw_returns_hoi_payload_tuple(self):
    class Cmd(HamiltonCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 0
      command_id = 1

    class FakeClient(HamiltonTCPClient):
      async def write(self, data: bytes, timeout=None):  # type: ignore[override]
        del data, timeout

      async def _read_one_message(self):  # type: ignore[override]
        payload = HoiParams().add(123, I32).build()
        hoi = HoiPacket(interface_id=0, action_code=Hoi2Action.COMMAND_RESPONSE, action_id=1, params=payload)
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
    raw = asyncio.run(client.send_command(Cmd(Address(1, 1, 257)), return_raw=True))
    assert raw is not None
    self.assertIsInstance(raw, tuple)
    self.assertEqual(raw[0], HoiParams().add(123, I32).build())

  def test_get_firmware_tree_uses_cache_and_refresh(self):
    class Backend:
      def __init__(self):
        self._registry = ObjectRegistry()
        self._registry.set_root_addresses([Address(1, 1, 100)])
        self._discovered_objects = {"root": [Address(1, 1, 100)]}

    backend = Backend()
    intro = HamiltonIntrospection(backend)
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

    intro.get_object = fake_get_object  # type: ignore[method-assign]
    intro.get_supported_interface0_method_ids = fake_get_supported  # type: ignore[method-assign]
    intro.get_subobject_address = fake_get_subobject_address  # type: ignore[method-assign]

    t1 = asyncio.run(intro.get_firmware_tree())
    t2 = asyncio.run(intro.get_firmware_tree())
    t3 = asyncio.run(intro.get_firmware_tree(refresh=True))

    self.assertIs(t1, t2)
    self.assertIsNot(t1, t3)
    self.assertEqual(len(t1.roots), 1)
    self.assertEqual(t1.roots[0].path, "Root")
    self.assertEqual(len(t1.roots[0].children), 1)
    self.assertIn("Root.Child", str(t1))
    self.assertGreaterEqual(counts["obj"], 4)  # built twice (initial + refresh)
    self.assertGreaterEqual(counts["sub"], 2)


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
    return summary + entries_frag + tail

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


if __name__ == "__main__":
  unittest.main()
