"""Tests for Hamilton TCP protocol implementation.

This module tests the packet structures, message builders, parameter encoding,
and command classes in the Hamilton TCP protocol stack.
"""

import struct
import unittest
from dataclasses import dataclass
from typing import Annotated
from unittest.mock import AsyncMock

from pylabrobot.liquid_handling.backends.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import (
  CommandMessage,
  CommandResponse,
  HoiParams,
  HoiParamsParser,
  InitMessage,
  InitResponse,
  RegistrationMessage,
  RegistrationResponse,
  parse_into_struct,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp.packets import (
  Address,
  HarpPacket,
  HoiPacket,
  IpPacket,
  RegistrationPacket,
  decode_version_byte,
  encode_version_byte,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp.protocol import (
  HamiltonProtocol,
  Hoi2Action,
  RegistrationActionCode,
  RegistrationOptionType,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp.wire_types import (
  Bool,
  BoolArray,
  CountedFlatArray,
  F32,
  F32Array,
  F64,
  HamiltonDataType,
  I8,
  I16,
  I32,
  I32Array,
  I64,
  Str,
  StrArray,
  U8,
  U16,
  U16Array,
  U32,
  U64,
  decode_fragment,
)


class TestVersionByte(unittest.TestCase):
  """Tests for version byte encoding/decoding."""

  def test_encode_version_byte(self):
    # Test standard version 3.0
    version = encode_version_byte(3, 0)
    self.assertEqual(version, 0x30)

  def test_encode_version_byte_zero(self):
    version = encode_version_byte(0, 0)
    self.assertEqual(version, 0x00)

  def test_encode_version_byte_max(self):
    version = encode_version_byte(15, 15)
    self.assertEqual(version, 0xFF)

  def test_encode_version_byte_mixed(self):
    version = encode_version_byte(2, 5)
    self.assertEqual(version, 0x25)

  def test_encode_version_byte_invalid_major(self):
    with self.assertRaises(ValueError):
      encode_version_byte(16, 0)

  def test_encode_version_byte_invalid_minor(self):
    with self.assertRaises(ValueError):
      encode_version_byte(0, 16)

  def test_encode_version_byte_negative(self):
    with self.assertRaises(ValueError):
      encode_version_byte(-1, 0)

  def test_decode_version_byte(self):
    major, minor = decode_version_byte(0x30)
    self.assertEqual(major, 3)
    self.assertEqual(minor, 0)

  def test_decode_version_byte_zero(self):
    major, minor = decode_version_byte(0x00)
    self.assertEqual(major, 0)
    self.assertEqual(minor, 0)

  def test_decode_version_byte_max(self):
    major, minor = decode_version_byte(0xFF)
    self.assertEqual(major, 15)
    self.assertEqual(minor, 15)

  def test_encode_decode_roundtrip(self):
    for major in range(16):
      for minor in range(16):
        encoded = encode_version_byte(major, minor)
        decoded_major, decoded_minor = decode_version_byte(encoded)
        self.assertEqual(decoded_major, major)
        self.assertEqual(decoded_minor, minor)


class TestAddress(unittest.TestCase):
  """Tests for Address dataclass."""

  def test_address_creation(self):
    addr = Address(module=1, node=2, object=3)
    self.assertEqual(addr.module, 1)
    self.assertEqual(addr.node, 2)
    self.assertEqual(addr.object, 3)

  def test_address_pack(self):
    addr = Address(module=1, node=2, object=3)
    packed = addr.pack()
    self.assertEqual(len(packed), 6)
    # Little-endian u16 values: 1, 2, 3
    self.assertEqual(packed, b"\x01\x00\x02\x00\x03\x00")

  def test_address_unpack(self):
    data = b"\x01\x00\x02\x00\x03\x00"
    addr = Address.unpack(data)
    self.assertEqual(addr.module, 1)
    self.assertEqual(addr.node, 2)
    self.assertEqual(addr.object, 3)

  def test_address_pack_unpack_roundtrip(self):
    original = Address(module=256, node=512, object=65535)
    packed = original.pack()
    unpacked = Address.unpack(packed)
    self.assertEqual(unpacked, original)

  def test_address_str(self):
    addr = Address(module=1, node=2, object=3)
    self.assertEqual(str(addr), "1:2:3")

  def test_address_frozen(self):
    addr = Address(module=1, node=2, object=3)
    with self.assertRaises(AttributeError):
      addr.module = 4  # type: ignore

  def test_address_equality(self):
    addr1 = Address(module=1, node=2, object=3)
    addr2 = Address(module=1, node=2, object=3)
    addr3 = Address(module=1, node=2, object=4)
    self.assertEqual(addr1, addr2)
    self.assertNotEqual(addr1, addr3)

  def test_address_hash(self):
    addr1 = Address(module=1, node=2, object=3)
    addr2 = Address(module=1, node=2, object=3)
    self.assertEqual(hash(addr1), hash(addr2))
    # Can be used as dict key
    d = {addr1: "value"}
    self.assertEqual(d[addr2], "value")


class TestIpPacket(unittest.TestCase):
  """Tests for IpPacket structure."""

  def test_ip_packet_pack(self):
    packet = IpPacket(protocol=6, payload=b"\x01\x02\x03")
    packed = packet.pack()
    # Size = 1 (protocol) + 1 (version) + 2 (opts_len) + 3 (payload) = 7
    self.assertEqual(packed[:2], b"\x07\x00")  # size
    self.assertEqual(packed[2], 6)  # protocol
    self.assertEqual(packed[3], 0x30)  # version 3.0
    self.assertEqual(packed[4:6], b"\x00\x00")  # options length
    self.assertEqual(packed[6:], b"\x01\x02\x03")  # payload

  def test_ip_packet_unpack(self):
    data = b"\x07\x00\x06\x30\x00\x00\x01\x02\x03"
    packet = IpPacket.unpack(data)
    self.assertEqual(packet.protocol, 6)
    self.assertEqual(packet.payload, b"\x01\x02\x03")
    self.assertEqual(packet.options, b"")

  def test_ip_packet_with_options(self):
    packet = IpPacket(protocol=6, payload=b"\x01", options=b"\xAB\xCD")
    packed = packet.pack()
    # Size = 1 + 1 + 2 + 2 (opts) + 1 (payload) = 7
    self.assertEqual(packed[4:6], b"\x02\x00")  # options length = 2
    self.assertEqual(packed[6:8], b"\xAB\xCD")  # options
    self.assertEqual(packed[8:], b"\x01")  # payload

  def test_ip_packet_roundtrip(self):
    original = IpPacket(protocol=7, payload=b"test_payload", options=b"\x01\x02")
    packed = original.pack()
    unpacked = IpPacket.unpack(packed)
    self.assertEqual(unpacked.protocol, original.protocol)
    self.assertEqual(unpacked.payload, original.payload)
    self.assertEqual(unpacked.options, original.options)


class TestHarpPacket(unittest.TestCase):
  """Tests for HarpPacket structure."""

  def test_harp_packet_action_property(self):
    # Test action byte calculation with response_required
    packet = HarpPacket(
      src=Address(0, 0, 0),
      dst=Address(0, 0, 1),
      seq=1,
      protocol=2,
      action_code=3,
      payload=b"",
      response_required=True,
    )
    self.assertEqual(packet.action, 0x13)  # 3 | 0x10

  def test_harp_packet_action_no_response(self):
    packet = HarpPacket(
      src=Address(0, 0, 0),
      dst=Address(0, 0, 1),
      seq=1,
      protocol=2,
      action_code=3,
      payload=b"",
      response_required=False,
    )
    self.assertEqual(packet.action, 0x03)

  def test_harp_packet_pack(self):
    packet = HarpPacket(
      src=Address(2, 1, 65535),
      dst=Address(1, 1, 257),
      seq=5,
      protocol=2,
      action_code=3,
      payload=b"\xFF",
      response_required=True,
    )
    packed = packet.pack()

    # Verify source address
    self.assertEqual(packed[0:6], Address(2, 1, 65535).pack())
    # Verify dest address
    self.assertEqual(packed[6:12], Address(1, 1, 257).pack())
    # Verify sequence number
    self.assertEqual(packed[12], 5)
    # Verify protocol
    self.assertEqual(packed[14], 2)
    # Verify action (3 | 0x10 = 0x13)
    self.assertEqual(packed[15], 0x13)

  def test_harp_packet_unpack(self):
    # Build a packet, pack it, then unpack
    original = HarpPacket(
      src=Address(2, 1, 65535),
      dst=Address(1, 1, 257),
      seq=42,
      protocol=2,
      action_code=4,
      payload=b"test",
      response_required=True,
    )
    packed = original.pack()
    unpacked = HarpPacket.unpack(packed)

    self.assertEqual(unpacked.src, original.src)
    self.assertEqual(unpacked.dst, original.dst)
    self.assertEqual(unpacked.seq, original.seq)
    self.assertEqual(unpacked.protocol, original.protocol)
    self.assertEqual(unpacked.action_code, original.action_code)
    self.assertEqual(unpacked.response_required, original.response_required)
    self.assertEqual(unpacked.payload, original.payload)


class TestHoiPacket(unittest.TestCase):
  """Tests for HoiPacket structure."""

  def test_hoi_packet_action_property(self):
    packet = HoiPacket(
      interface_id=1, action_code=3, action_id=4, params=b"", response_required=True
    )
    self.assertEqual(packet.action, 0x13)

  def test_hoi_packet_action_no_response(self):
    packet = HoiPacket(
      interface_id=1, action_code=3, action_id=4, params=b"", response_required=False
    )
    self.assertEqual(packet.action, 0x03)

  def test_hoi_packet_pack_empty_params(self):
    packet = HoiPacket(
      interface_id=1, action_code=3, action_id=42, params=b"", response_required=False
    )
    packed = packet.pack()

    self.assertEqual(packed[0], 1)  # interface_id
    self.assertEqual(packed[1], 0x03)  # action
    self.assertEqual(packed[2:4], b"\x2A\x00")  # action_id = 42 (little-endian)
    self.assertEqual(packed[4], 0)  # version byte
    self.assertEqual(packed[5], 0)  # num_fragments

  def test_hoi_packet_count_fragments(self):
    # Create params with 2 DataFragments (each: type_id, flags, length, data)
    # Fragment 1: i32 (type=3), 4 bytes
    frag1 = b"\x03\x00\x04\x00" + b"\x01\x02\x03\x04"
    # Fragment 2: u8 (type=4), 1 byte
    frag2 = b"\x04\x00\x01\x00" + b"\x05"

    packet = HoiPacket(
      interface_id=0, action_code=0, action_id=1, params=frag1 + frag2, response_required=False
    )
    packed = packet.pack()
    self.assertEqual(packed[5], 2)  # num_fragments

  def test_hoi_packet_roundtrip(self):
    params = b"\x03\x00\x04\x00\x01\x00\x00\x00"  # One i32 fragment
    original = HoiPacket(
      interface_id=1, action_code=0, action_id=5, params=params, response_required=True
    )
    packed = original.pack()
    unpacked = HoiPacket.unpack(packed)

    self.assertEqual(unpacked.interface_id, original.interface_id)
    self.assertEqual(unpacked.action_code, original.action_code)
    self.assertEqual(unpacked.action_id, original.action_id)
    self.assertEqual(unpacked.params, original.params)
    self.assertEqual(unpacked.response_required, original.response_required)


class TestRegistrationPacket(unittest.TestCase):
  """Tests for RegistrationPacket structure."""

  def test_registration_packet_pack(self):
    packet = RegistrationPacket(
      action_code=12,
      response_code=0,
      req_address=Address(2, 1, 65535),
      res_address=Address(0, 0, 0),
      options=b"",
    )
    packed = packet.pack()

    self.assertEqual(packed[0:2], b"\x0C\x00")  # action_code = 12
    self.assertEqual(packed[2:4], b"\x00\x00")  # response_code = 0

  def test_registration_packet_roundtrip(self):
    original = RegistrationPacket(
      action_code=RegistrationActionCode.HARP_PROTOCOL_REQUEST,
      response_code=0,
      req_address=Address(2, 5, 65535),
      res_address=Address(0, 0, 0),
      options=b"\x05\x02\x02\x01",
    )
    packed = original.pack()
    unpacked = RegistrationPacket.unpack(packed)

    self.assertEqual(unpacked.action_code, original.action_code)
    self.assertEqual(unpacked.response_code, original.response_code)
    self.assertEqual(unpacked.req_address, original.req_address)
    self.assertEqual(unpacked.res_address, original.res_address)
    self.assertEqual(unpacked.options, original.options)


class TestHoiParams(unittest.TestCase):
  """Tests for HoiParams builder."""

  def test_empty_params(self):
    params = HoiParams().build()
    self.assertEqual(params, b"")
    self.assertEqual(HoiParams().count(), 0)

  def test_i8(self):
    params = HoiParams().add(-128, I8).build()
    # DataFragment: [type=1][flags=0][length=1][data=-128]
    self.assertEqual(params[0], HamiltonDataType.I8)
    self.assertEqual(params[1], 0)  # flags
    self.assertEqual(params[2:4], b"\x01\x00")  # length = 1
    self.assertEqual(params[4], 0x80)  # -128 as signed byte

  def test_i16(self):
    params = HoiParams().add(-1000, I16).build()
    self.assertEqual(params[0], HamiltonDataType.I16)
    self.assertEqual(params[2:4], b"\x02\x00")  # length = 2

  def test_i32(self):
    params = HoiParams().add(100000, I32).build()
    self.assertEqual(params[0], HamiltonDataType.I32)
    self.assertEqual(params[2:4], b"\x04\x00")  # length = 4

  def test_i64(self):
    params = HoiParams().add(2**40, I64).build()
    self.assertEqual(params[0], HamiltonDataType.I64)
    self.assertEqual(params[2:4], b"\x08\x00")  # length = 8

  def test_u8(self):
    params = HoiParams().add(255, U8).build()
    self.assertEqual(params[0], HamiltonDataType.U8)
    self.assertEqual(params[4], 255)

  def test_u16(self):
    params = HoiParams().add(65535, U16).build()
    self.assertEqual(params[0], HamiltonDataType.U16)
    self.assertEqual(params[4:6], b"\xFF\xFF")

  def test_u32(self):
    params = HoiParams().add(0xDEADBEEF, U32).build()
    self.assertEqual(params[0], HamiltonDataType.U32)
    self.assertEqual(params[4:8], b"\xEF\xBE\xAD\xDE")

  def test_u64(self):
    params = HoiParams().add(0xDEADBEEFCAFEBABE, U64).build()
    self.assertEqual(params[0], HamiltonDataType.U64)

  def test_f32(self):
    params = HoiParams().add(3.14, F32).build()
    self.assertEqual(params[0], HamiltonDataType.F32)
    self.assertEqual(params[2:4], b"\x04\x00")

  def test_f64(self):
    params = HoiParams().add(3.14159265358979, F64).build()
    self.assertEqual(params[0], HamiltonDataType.F64)
    self.assertEqual(params[2:4], b"\x08\x00")

  def test_string(self):
    params = HoiParams().add("test", Str).build()
    self.assertEqual(params[0], HamiltonDataType.STRING)
    self.assertEqual(params[2:4], b"\x05\x00")  # length = 5 (including null)
    self.assertEqual(params[4:9], b"test\x00")

  def test_bool_true(self):
    params = HoiParams().add(True, Bool).build()
    self.assertEqual(params[0], HamiltonDataType.BOOL)
    self.assertEqual(params[4], 1)

  def test_bool_false(self):
    params = HoiParams().add(False, Bool).build()
    self.assertEqual(params[0], HamiltonDataType.BOOL)
    self.assertEqual(params[4], 0)

  def test_i32_array(self):
    params = HoiParams().add([1, 2, 3], I32Array).build()
    self.assertEqual(params[0], HamiltonDataType.I32_ARRAY)
    self.assertEqual(params[2:4], b"\x0C\x00")  # length = 12 (3 * 4)

  def test_u16_array(self):
    params = HoiParams().add([100, 200, 300], U16Array).build()
    self.assertEqual(params[0], HamiltonDataType.U16_ARRAY)
    self.assertEqual(params[2:4], b"\x06\x00")  # length = 6 (3 * 2)

  def test_f32_array(self):
    params = HoiParams().add([1.0, 2.0, 3.0], F32Array).build()
    self.assertEqual(params[0], HamiltonDataType.F32_ARRAY)
    self.assertEqual(params[2:4], b"\x0C\x00")  # length = 12 (3 * 4)

  def test_bool_array(self):
    params = HoiParams().add([True, False, True], BoolArray).build()
    self.assertEqual(params[0], HamiltonDataType.BOOL_ARRAY)
    self.assertEqual(params[1], 0x01)  # flags = 0x01 for bool arrays
    self.assertEqual(params[2:4], b"\x04\x00")  # length = 4 (3 bools + pad)
    self.assertEqual(params[4:7], b"\x01\x00\x01")

  def test_string_array(self):
    params = HoiParams().add(["a", "bc"], StrArray).build()
    self.assertEqual(params[0], HamiltonDataType.STRING_ARRAY)
    # String array payload is concatenated null-terminated strings (no count)
    self.assertEqual(params[4:9], b"a\x00bc\x00")

  def test_method_chaining(self):
    self.assertEqual(HoiParams().add(1, I32).add("test", Str).add(True, Bool).count(), 3)

  def test_count(self):
    builder = HoiParams().add(1, I32).add(2, I32).add(3, I32)
    self.assertEqual(builder.count(), 3)


class TestHoiParamsParser(unittest.TestCase):
  """Tests for HoiParamsParser."""

  def test_parse_i32(self):
    params = HoiParams().add(12345, I32).build()
    parser = HoiParamsParser(params)
    type_id, value = parser.parse_next()
    self.assertEqual(type_id, HamiltonDataType.I32)
    self.assertEqual(value, 12345)

  def test_parse_negative_i32(self):
    params = HoiParams().add(-12345, I32).build()
    parser = HoiParamsParser(params)
    _, value = parser.parse_next()
    self.assertEqual(value, -12345)

  def test_parse_string(self):
    params = HoiParams().add("hello", Str).build()
    parser = HoiParamsParser(params)
    type_id, value = parser.parse_next()
    self.assertEqual(type_id, HamiltonDataType.STRING)
    self.assertEqual(value, "hello")

  def test_parse_bool(self):
    params = HoiParams().add(True, Bool).build()
    parser = HoiParamsParser(params)
    type_id, value = parser.parse_next()
    self.assertEqual(type_id, HamiltonDataType.BOOL)
    self.assertEqual(value, True)

  def test_parse_i32_array(self):
    params = HoiParams().add([10, 20, 30], I32Array).build()
    parser = HoiParamsParser(params)
    type_id, value = parser.parse_next()
    self.assertEqual(type_id, HamiltonDataType.I32_ARRAY)
    self.assertEqual(value, [10, 20, 30])

  def test_parse_u16_array(self):
    params = HoiParams().add([100, 200, 300], U16Array).build()
    parser = HoiParamsParser(params)
    type_id, value = parser.parse_next()
    self.assertEqual(type_id, HamiltonDataType.U16_ARRAY)
    self.assertEqual(value, [100, 200, 300])

  def test_parse_bool_array(self):
    params = HoiParams().add([True, False, True, False], BoolArray).build()
    parser = HoiParamsParser(params)
    type_id, value = parser.parse_next()
    self.assertEqual(type_id, HamiltonDataType.BOOL_ARRAY)
    self.assertEqual(value, [True, False, True, False])

  def test_parse_multiple(self):
    params = HoiParams().add(100, I32).add("test", Str).add(False, Bool).build()
    parser = HoiParamsParser(params)

    _, v1 = parser.parse_next()
    _, v2 = parser.parse_next()
    _, v3 = parser.parse_next()

    self.assertEqual(v1, 100)
    self.assertEqual(v2, "test")
    self.assertEqual(v3, False)

  def test_parse_all(self):
    params = HoiParams().add(1, I32).add(2, I32).add(3, I32).build()
    parser = HoiParamsParser(params)
    results = parser.parse_all()

    self.assertEqual(len(results), 3)
    self.assertEqual([v for _, v in results], [1, 2, 3])

  def test_has_remaining(self):
    params = HoiParams().add(1, I32).build()
    parser = HoiParamsParser(params)
    self.assertTrue(parser.has_remaining())
    parser.parse_next()
    self.assertFalse(parser.has_remaining())

  def test_roundtrip_all_types(self):
    """Test that all types can be built and parsed correctly."""
    i8_val = -100
    i16_val = -1000
    i32_val = -100000
    i64_val = -10000000000
    u8_val = 200
    u16_val = 50000
    u32_val = 3000000000
    u64_val = 10000000000000
    f32_val = 3.14
    f64_val = 3.141592653589793
    string_val = "hello world"
    bool_val = True

    builder = HoiParams()
    builder.add(i8_val, I8)
    builder.add(i16_val, I16)
    builder.add(i32_val, I32)
    builder.add(i64_val, I64)
    builder.add(u8_val, U8)
    builder.add(u16_val, U16)
    builder.add(u32_val, U32)
    builder.add(u64_val, U64)
    builder.add(f32_val, F32)
    builder.add(f64_val, F64)
    builder.add(string_val, Str)
    builder.add(bool_val, Bool)

    params = builder.build()
    parser = HoiParamsParser(params)
    results = parser.parse_all()

    self.assertEqual(len(results), 12)
    self.assertEqual(results[0][1], i8_val)
    self.assertEqual(results[1][1], i16_val)
    self.assertEqual(results[2][1], i32_val)
    self.assertEqual(results[3][1], i64_val)
    self.assertEqual(results[4][1], u8_val)
    self.assertEqual(results[5][1], u16_val)
    self.assertEqual(results[6][1], u32_val)
    self.assertEqual(results[7][1], u64_val)
    self.assertAlmostEqual(results[8][1], f32_val, places=5)
    self.assertAlmostEqual(results[9][1], f64_val, places=10)
    self.assertEqual(results[10][1], string_val)
    self.assertEqual(results[11][1], bool_val)


class TestCommandMessage(unittest.TestCase):
  """Tests for CommandMessage builder."""

  def test_build_simple_command(self):
    dest = Address(1, 1, 257)
    params = HoiParams().add(100, I32)
    msg = CommandMessage(dest=dest, interface_id=1, method_id=4, params=params)

    src = Address(2, 1, 65535)
    packet = msg.build(src, seq=5)

    # Verify it produces valid bytes
    self.assertIsInstance(packet, bytes)
    self.assertGreater(len(packet), 20)

    # Parse and verify structure
    ip = IpPacket.unpack(packet)
    self.assertEqual(ip.protocol, 6)  # OBJECT_DISCOVERY

    harp = HarpPacket.unpack(ip.payload)
    self.assertEqual(harp.src, src)
    self.assertEqual(harp.dst, dest)
    self.assertEqual(harp.seq, 5)
    self.assertEqual(harp.protocol, 2)  # HOI2

    hoi = HoiPacket.unpack(harp.payload)
    self.assertEqual(hoi.interface_id, 1)
    self.assertEqual(hoi.action_id, 4)

  def test_build_with_response_flags(self):
    dest = Address(1, 1, 257)
    msg = CommandMessage(dest=dest, interface_id=0, method_id=1, params=HoiParams())
    src = Address(2, 1, 65535)

    # Default: HARP response required, HOI not required
    packet = msg.build(src, seq=1)
    ip = IpPacket.unpack(packet)
    harp = HarpPacket.unpack(ip.payload)
    self.assertTrue(harp.response_required)

    hoi = HoiPacket.unpack(harp.payload)
    self.assertFalse(hoi.response_required)


class TestRegistrationMessage(unittest.TestCase):
  """Tests for RegistrationMessage builder."""

  def test_build_registration_request(self):
    dest = Address(0, 0, 65534)
    msg = RegistrationMessage(dest=dest, action_code=RegistrationActionCode.REGISTRATION_REQUEST)

    src = Address(2, 1, 65535)
    req_addr = Address(2, 1, 65535)
    res_addr = Address(0, 0, 0)

    packet = msg.build(src, req_addr, res_addr, seq=1)
    self.assertIsInstance(packet, bytes)

    # Parse and verify
    ip = IpPacket.unpack(packet)
    self.assertEqual(ip.protocol, 6)

    harp = HarpPacket.unpack(ip.payload)
    self.assertEqual(harp.protocol, 3)  # Registration

    reg = RegistrationPacket.unpack(harp.payload)
    self.assertEqual(reg.action_code, RegistrationActionCode.REGISTRATION_REQUEST)

  def test_add_registration_option(self):
    dest = Address(0, 0, 65534)
    msg = RegistrationMessage(dest=dest, action_code=RegistrationActionCode.HARP_PROTOCOL_REQUEST)
    msg.add_registration_option(
      RegistrationOptionType.HARP_PROTOCOL_REQUEST, protocol=2, request_id=1
    )

    src = Address(2, 1, 65535)
    packet = msg.build(src, Address(0, 0, 0), Address(0, 0, 0), seq=1)

    # Parse and verify options were added
    ip = IpPacket.unpack(packet)
    harp = HarpPacket.unpack(ip.payload)
    reg = RegistrationPacket.unpack(harp.payload)

    self.assertGreater(len(reg.options), 0)


class TestInitMessage(unittest.TestCase):
  """Tests for InitMessage builder."""

  def test_build_init_message(self):
    msg = InitMessage(timeout=30)
    packet = msg.build()

    self.assertIsInstance(packet, bytes)
    # Protocol 7 packet
    self.assertEqual(packet[2], 7)

  def test_custom_timeout(self):
    msg = InitMessage(timeout=60)
    packet = msg.build()
    # Timeout is encoded in parameters - just verify packet builds
    self.assertIsInstance(packet, bytes)


class TestInitResponse(unittest.TestCase):
  """Tests for InitResponse parser."""

  def test_parse_init_response(self):
    # Build a mock Protocol 7 response
    # Format: [size:2][protocol:1][version:1][opts_len:2][frame:4][params...]
    # Frame: [version:1][msg_id:1][count:1][unknown:1]
    # Each param: [id:1][type:1][reserved:2][value:2]
    response = (
      b"\x16\x00"  # size = 22
      b"\x07"  # protocol = 7
      b"\x30"  # version = 3.0
      b"\x00\x00"  # opts_len = 0
      b"\x00\x00\x03\x00"  # frame: version=0, msg_id=0, count=3, unknown=0
      b"\x01\x10\x00\x00\x05\x00"  # param 1: id=1, type=16, value=5 (client_id)
      b"\x02\x10\x00\x00\x01\x00"  # param 2: id=2, type=16, value=1 (conn_type)
      b"\x04\x10\x00\x00\x1e\x00"  # param 3: id=4, type=16, value=30 (timeout)
    )

    parsed = InitResponse.from_bytes(response)
    self.assertEqual(parsed.client_id, 5)
    self.assertEqual(parsed.connection_type, 1)
    self.assertEqual(parsed.timeout, 30)


class TestRegistrationResponse(unittest.TestCase):
  """Tests for RegistrationResponse parser."""

  def test_parse_registration_response(self):
    # Build a valid registration response
    src = Address(0, 0, 65534)
    dst = Address(2, 1, 65535)
    reg = RegistrationPacket(
      action_code=RegistrationActionCode.HARP_PROTOCOL_RESPONSE,
      response_code=0,
      req_address=Address(0, 0, 0),
      res_address=Address(0, 0, 0),
      options=b"",
    )
    harp = HarpPacket(src=src, dst=dst, seq=10, protocol=3, action_code=13, payload=reg.pack())
    ip = IpPacket(protocol=6, payload=harp.pack())
    response_bytes = ip.pack()

    parsed = RegistrationResponse.from_bytes(response_bytes)
    self.assertEqual(parsed.ip.protocol, 6)
    self.assertEqual(parsed.harp.src, src)
    self.assertEqual(parsed.harp.dst, dst)
    self.assertEqual(parsed.harp.seq, 10)
    self.assertEqual(parsed.harp.protocol, 3)  # Registration protocol
    self.assertEqual(parsed.registration.action_code, RegistrationActionCode.HARP_PROTOCOL_RESPONSE)

  def test_registration_response_with_options(self):
    # Build a registration response with HARP_PROTOCOL_RESPONSE options
    # Options format: [option_id:1][length:1][data:n]
    # HARP_PROTOCOL_RESPONSE (6) contains object IDs as u16 values
    options = bytes(
      [
        RegistrationOptionType.HARP_PROTOCOL_RESPONSE,  # option_id = 6
        6,  # length = 6 bytes (padding u16 + 2 object IDs)
        0x00,
        0x00,  # padding u16
        0x01,
        0x00,  # object_id = 1
        0x02,
        0x00,  # object_id = 2
      ]
    )

    reg = RegistrationPacket(
      action_code=RegistrationActionCode.HARP_PROTOCOL_RESPONSE,
      response_code=0,
      req_address=Address(0, 0, 0),
      res_address=Address(0, 0, 0),
      options=options,
    )
    harp = HarpPacket(
      src=Address(0, 0, 65534),
      dst=Address(2, 1, 65535),
      seq=5,
      protocol=3,
      action_code=13,
      payload=reg.pack(),
    )
    ip = IpPacket(protocol=6, payload=harp.pack())

    parsed = RegistrationResponse.from_bytes(ip.pack())
    self.assertEqual(len(parsed.registration.options), len(options))
    self.assertEqual(parsed.registration.options, options)

  def test_sequence_number_property(self):
    reg = RegistrationPacket(
      action_code=0, response_code=0, req_address=Address(0, 0, 0), res_address=Address(0, 0, 0)
    )
    harp = HarpPacket(
      src=Address(0, 0, 0),
      dst=Address(0, 0, 0),
      seq=42,
      protocol=3,
      action_code=0,
      payload=reg.pack(),
    )
    ip = IpPacket(protocol=6, payload=harp.pack())

    parsed = RegistrationResponse.from_bytes(ip.pack())
    self.assertEqual(parsed.sequence_number, 42)

  def test_registration_request_roundtrip(self):
    # Build a registration request, pack it, then parse as response
    src = Address(2, 5, 65535)
    dst = Address(0, 0, 65534)
    reg = RegistrationPacket(
      action_code=RegistrationActionCode.REGISTRATION_REQUEST,
      response_code=0,
      req_address=Address(2, 5, 65535),
      res_address=Address(0, 0, 0),
      options=b"",
    )
    harp = HarpPacket(src=src, dst=dst, seq=1, protocol=3, action_code=3, payload=reg.pack())
    ip = IpPacket(protocol=6, payload=harp.pack())

    parsed = RegistrationResponse.from_bytes(ip.pack())
    self.assertEqual(parsed.harp.src, src)
    self.assertEqual(parsed.harp.dst, dst)
    self.assertEqual(parsed.registration.req_address, Address(2, 5, 65535))
    self.assertEqual(parsed.registration.res_address, Address(0, 0, 0))


class TestCommandResponse(unittest.TestCase):
  """Tests for CommandResponse parser."""

  def test_parse_command_response(self):
    # Build a valid response
    src = Address(1, 1, 257)
    dst = Address(2, 1, 65535)
    hoi = HoiPacket(
      interface_id=1, action_code=Hoi2Action.COMMAND_RESPONSE, action_id=4, params=b""
    )
    harp = HarpPacket(src=src, dst=dst, seq=5, protocol=2, action_code=4, payload=hoi.pack())
    ip = IpPacket(protocol=6, payload=harp.pack())
    response_bytes = ip.pack()

    parsed = CommandResponse.from_bytes(response_bytes)
    self.assertEqual(parsed.ip.protocol, 6)
    self.assertEqual(parsed.harp.src, src)
    self.assertEqual(parsed.harp.dst, dst)
    self.assertEqual(parsed.harp.seq, 5)
    self.assertEqual(parsed.hoi.interface_id, 1)
    self.assertEqual(parsed.hoi.action_id, 4)

  def test_sequence_number_property(self):
    hoi = HoiPacket(interface_id=0, action_code=4, action_id=0, params=b"")
    harp = HarpPacket(
      src=Address(0, 0, 0),
      dst=Address(0, 0, 0),
      seq=42,
      protocol=2,
      action_code=4,
      payload=hoi.pack(),
    )
    ip = IpPacket(protocol=6, payload=harp.pack())

    parsed = CommandResponse.from_bytes(ip.pack())
    self.assertEqual(parsed.sequence_number, 42)


class TestHamiltonCommand(unittest.TestCase):
  """Tests for HamiltonCommand base class."""

  def test_command_requires_protocol(self):
    class BadCommand(HamiltonCommand):
      interface_id = 0
      command_id = 1

    with self.assertRaises(ValueError):
      BadCommand(Address(0, 0, 0))

  def test_command_requires_interface_id(self):
    class BadCommand(HamiltonCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      command_id = 1

    with self.assertRaises(ValueError):
      BadCommand(Address(0, 0, 0))

  def test_command_requires_command_id(self):
    class BadCommand(HamiltonCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 0

    with self.assertRaises(ValueError):
      BadCommand(Address(0, 0, 0))

  def test_valid_command(self):
    class TestCommand(HamiltonCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 1
      command_id = 42

    cmd = TestCommand(Address(1, 1, 257))
    self.assertEqual(cmd.dest, Address(1, 1, 257))
    self.assertEqual(cmd.interface_id, 1)
    self.assertEqual(cmd.command_id, 42)

  def test_build_parameters_default(self):
    class TestCommand(HamiltonCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 0
      command_id = 1

    cmd = TestCommand(Address(0, 0, 0))
    params = cmd.build_parameters()
    self.assertEqual(params.build(), b"")

  def test_build_requires_source_address(self):
    class TestCommand(HamiltonCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 0
      command_id = 1

    cmd = TestCommand(Address(1, 1, 257))
    with self.assertRaises(ValueError):
      cmd.build()  # No source address set

  def test_build_with_source_address(self):
    class TestCommand(HamiltonCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 1
      command_id = 4

      def build_parameters(self):
        return HoiParams().add(100, I32)

    cmd = TestCommand(Address(1, 1, 257))
    cmd.source_address = Address(2, 1, 65535)
    cmd.sequence_number = 1

    packet = cmd.build()
    self.assertIsInstance(packet, bytes)

    # Verify structure
    ip = IpPacket.unpack(packet)
    harp = HarpPacket.unpack(ip.payload)
    hoi = HoiPacket.unpack(harp.payload)

    self.assertEqual(hoi.interface_id, 1)
    self.assertEqual(hoi.action_id, 4)

  def test_get_log_params(self):
    class TestCommand(HamiltonCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 0
      command_id = 1

      def __init__(self, dest: Address, value: int, name: str):
        super().__init__(dest)
        self.value = value
        self.name = name

    cmd = TestCommand(Address(0, 0, 0), value=42, name="test")
    log_params = cmd.get_log_params()

    self.assertEqual(log_params["value"], 42)
    self.assertEqual(log_params["name"], "test")
    self.assertNotIn("dest", log_params)
    self.assertNotIn("self", log_params)


class TestInterpretResponseAutoDecode(unittest.TestCase):
  """Tests for interpret_response auto-decode when command has Response class."""

  def test_auto_decode_with_response_class(self):
    """Command with nested Response decodes via parse_into_struct."""
    from pylabrobot.liquid_handling.backends.hamilton.tcp.wire_types import I64

    class CommandWithResponse(HamiltonCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 1
      command_id = 0

      @dataclass(frozen=True)
      class Response:
        value: I64

    cmd = CommandWithResponse(Address(0, 0, 0))
    params = HoiParams().add(42, I64).build()
    hoi = HoiPacket(
      interface_id=1,
      action_code=Hoi2Action.COMMAND_RESPONSE,
      action_id=0,
      params=params,
    )
    harp = HarpPacket(
      src=Address(0, 0, 0),
      dst=Address(0, 0, 0),
      seq=0,
      protocol=2,
      action_code=4,
      payload=hoi.pack(),
    )
    ip = IpPacket(protocol=6, payload=harp.pack())
    response = CommandResponse.from_bytes(ip.pack())
    result = cmd.interpret_response(response)
    self.assertIsInstance(result, CommandWithResponse.Response)
    self.assertEqual(result.value, 42)

  def test_auto_decode_fallback_no_response_class(self):
    """Command without Response returns None when params empty."""
    class CommandNoResponse(HamiltonCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 0
      command_id = 1

    cmd = CommandNoResponse(Address(0, 0, 0))
    hoi = HoiPacket(interface_id=0, action_code=4, action_id=1, params=b"")
    harp = HarpPacket(
      src=Address(0, 0, 0),
      dst=Address(0, 0, 0),
      seq=0,
      protocol=2,
      action_code=4,
      payload=hoi.pack(),
    )
    ip = IpPacket(protocol=6, payload=harp.pack())
    response = CommandResponse.from_bytes(ip.pack())
    result = cmd.interpret_response(response)
    self.assertIsNone(result)

  def test_auto_decode_fallback_parse_response_parameters(self):
    """Command with parse_response_parameters override but no Response uses override."""
    class CommandWithOverride(HamiltonCommand):
      protocol = HamiltonProtocol.OBJECT_DISCOVERY
      interface_id = 1
      command_id = 0

      @classmethod
      def parse_response_parameters(cls, data):
        parser = HoiParamsParser(data)
        _, v = parser.parse_next()
        return {"value": v}

    cmd = CommandWithOverride(Address(0, 0, 0))
    params = HoiParams().add(100, I32).build()
    hoi = HoiPacket(
      interface_id=1,
      action_code=Hoi2Action.COMMAND_RESPONSE,
      action_id=0,
      params=params,
    )
    harp = HarpPacket(
      src=Address(0, 0, 0),
      dst=Address(0, 0, 0),
      seq=0,
      protocol=2,
      action_code=4,
      payload=hoi.pack(),
    )
    ip = IpPacket(protocol=6, payload=harp.pack())
    response = CommandResponse.from_bytes(ip.pack())
    result = cmd.interpret_response(response)
    self.assertEqual(result, {"value": 100})


class TestProtocolEnums(unittest.TestCase):
  """Tests for protocol enum values."""

  def test_hamilton_protocol_values(self):
    self.assertEqual(HamiltonProtocol.PIPETTE, 0x02)
    self.assertEqual(HamiltonProtocol.REGISTRATION, 0x03)
    self.assertEqual(HamiltonProtocol.OBJECT_DISCOVERY, 0x06)
    self.assertEqual(HamiltonProtocol.INITIALIZATION, 0x07)

  def test_hoi2_action_values(self):
    self.assertEqual(Hoi2Action.STATUS_REQUEST, 0)
    self.assertEqual(Hoi2Action.STATUS_RESPONSE, 1)
    self.assertEqual(Hoi2Action.COMMAND_REQUEST, 3)
    self.assertEqual(Hoi2Action.COMMAND_RESPONSE, 4)
    self.assertEqual(Hoi2Action.COMMAND_EXCEPTION, 5)

  def test_registration_action_code_values(self):
    self.assertEqual(RegistrationActionCode.REGISTRATION_REQUEST, 0)
    self.assertEqual(RegistrationActionCode.HARP_PROTOCOL_REQUEST, 12)
    self.assertEqual(RegistrationActionCode.HARP_PROTOCOL_RESPONSE, 13)

  def test_hamilton_data_type_values(self):
    self.assertEqual(HamiltonDataType.I8, 1)
    self.assertEqual(HamiltonDataType.I32, 3)
    self.assertEqual(HamiltonDataType.STRING, 15)
    self.assertEqual(HamiltonDataType.BOOL, 23)
    self.assertEqual(HamiltonDataType.I32_ARRAY, 27)


class TestDecodeFragment(unittest.TestCase):
  """Tests for decode_fragment() and correct Python types."""

  def test_decode_i32(self):
    data = struct.pack("<i", 42)
    out = decode_fragment(HamiltonDataType.I32, data)
    self.assertIsInstance(out, int)
    self.assertEqual(out, 42)

  def test_decode_f32(self):
    data = struct.pack("<f", 3.14)
    out = decode_fragment(HamiltonDataType.F32, data)
    self.assertIsInstance(out, float)
    self.assertAlmostEqual(out, 3.14, places=5)

  def test_decode_bool(self):
    data = struct.pack("?", True)
    out = decode_fragment(HamiltonDataType.BOOL, data)
    self.assertIsInstance(out, bool)
    self.assertIs(out, True)

  def test_decode_i16_array(self):
    data = struct.pack("<hhh", 1, 2, 3)
    out = decode_fragment(HamiltonDataType.I16_ARRAY, data)
    self.assertIsInstance(out, list)
    self.assertEqual(out, [1, 2, 3])

  def test_decode_string(self):
    data = b"hello\x00"
    out = decode_fragment(HamiltonDataType.STRING, data)
    self.assertIsInstance(out, str)
    self.assertEqual(out, "hello")

  def test_decode_structure(self):
    data = b"raw\x00"
    out = decode_fragment(HamiltonDataType.STRUCTURE, data)
    self.assertIsInstance(out, bytes)
    self.assertEqual(out, data)

  def test_decode_structure_array(self):
    # Two STRUCTURE sub-fragments: [30][0][len:2][payload]
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
    self.assertIsInstance(out, list)
    self.assertEqual(len(out), 2)
    self.assertEqual(out[0], p1)
    self.assertEqual(out[1], p2)

  def test_decode_unknown_type_id_raises(self):
    with self.assertRaises(ValueError) as ctx:
      decode_fragment(0xFF, b"")
    self.assertIn("Unknown DataFragment type_id", str(ctx.exception))


class TestWireTypeRoundTrip(unittest.TestCase):
  """Encode via WireType.encode_into, decode via decode_from; round-trip equality."""

  def _roundtrip_scalar(self, alias, value, type_id):
    meta = alias.__metadata__[0]
    params = HoiParams()
    meta.encode_into(value, params)
    frag = params._fragments[0]
    payload = frag[4 : 4 + int.from_bytes(frag[2:4], "little")]
    decoded = decode_fragment(type_id, payload)
    self.assertEqual(decoded, value, f"round-trip for {alias}")

  def _roundtrip_array(self, alias, value, type_id):
    meta = alias.__metadata__[0]
    params = HoiParams()
    meta.encode_into(value, params)
    frag = params._fragments[0]
    payload = frag[4 : 4 + int.from_bytes(frag[2:4], "little")]
    decoded = decode_fragment(type_id, payload)
    self.assertEqual(decoded, value, f"round-trip for {alias}")

  def test_roundtrip_i32(self):
    from pylabrobot.liquid_handling.backends.hamilton.tcp.wire_types import I32

    self._roundtrip_scalar(I32, 42, HamiltonDataType.I32)

  def test_roundtrip_f32(self):
    from pylabrobot.liquid_handling.backends.hamilton.tcp.wire_types import F32

    value = 2.5  # exactly representable in float32
    meta = F32.__metadata__[0]
    params = HoiParams()
    meta.encode_into(value, params)
    frag = params._fragments[0]
    payload = frag[4 : 4 + int.from_bytes(frag[2:4], "little")]
    decoded = decode_fragment(HamiltonDataType.F32, payload)
    self.assertEqual(decoded, value)

  def test_roundtrip_bool(self):
    from pylabrobot.liquid_handling.backends.hamilton.tcp.wire_types import Bool

    self._roundtrip_scalar(Bool, True, HamiltonDataType.BOOL)

  def test_roundtrip_i16_array(self):
    from pylabrobot.liquid_handling.backends.hamilton.tcp.wire_types import I16Array

    self._roundtrip_array(I16Array, [1, 2, 3], HamiltonDataType.I16_ARRAY)

  def test_roundtrip_string(self):
    from pylabrobot.liquid_handling.backends.hamilton.tcp.wire_types import Str

    meta = Str.__metadata__[0]
    params = HoiParams()
    meta.encode_into("hello", params)
    frag = params._fragments[0]
    payload = frag[4 : 4 + int.from_bytes(frag[2:4], "little")]
    decoded = decode_fragment(HamiltonDataType.STRING, payload)
    self.assertEqual(decoded, "hello")


class TestCountedFlatArray(unittest.TestCase):
  """Tests for CountedFlatArray decode in parse_into_struct."""

  def test_counted_flat_array_single_level(self):
    """Encode count (I64) + N flat fragments, decode with CountedFlatArray DTO."""
    # Wire: count=2, then 2× (interface_id: I64, name: Str, version: I64)
    data = (
      HoiParams()
      .add(2, I64)
      .add(1, I64)
      .add("if1", Str)
      .add(10, I64)
      .add(2, I64)
      .add("if2", Str)
      .add(20, I64)
      .build()
    )

    @dataclass
    class _InterfaceWire:
      interface_id: I64
      name: Str
      version: I64

    @dataclass
    class _GetInterfacesResponse:
      interfaces: Annotated[list[_InterfaceWire], CountedFlatArray()]

    r = parse_into_struct(HoiParamsParser(data), _GetInterfacesResponse)
    self.assertEqual(len(r.interfaces), 2)
    self.assertEqual(r.interfaces[0].interface_id, 1)
    self.assertEqual(r.interfaces[0].name, "if1")
    self.assertEqual(r.interfaces[0].version, 10)
    self.assertEqual(r.interfaces[1].interface_id, 2)
    self.assertEqual(r.interfaces[1].name, "if2")
    self.assertEqual(r.interfaces[1].version, 20)

  def test_counted_flat_array_nested(self):
    """Encode outer count + N×(2 scalars + inner count + M×2 scalars), decode with nested CountedFlatArray."""
    # Wire: enum_count=1, then enum_id=1, name="E1", value_count=2, then ("v1", 10), ("v2", 20)
    data = (
      HoiParams()
      .add(1, I64)
      .add(1, I64)
      .add("E1", Str)
      .add(2, I64)
      .add("v1", Str)
      .add(10, I64)
      .add("v2", Str)
      .add(20, I64)
      .build()
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

    r = parse_into_struct(HoiParamsParser(data), _GetEnumsResponse)
    self.assertEqual(len(r.enums), 1)
    self.assertEqual(r.enums[0].enum_id, 1)
    self.assertEqual(r.enums[0].name, "E1")
    self.assertEqual(len(r.enums[0].values), 2)
    self.assertEqual(r.enums[0].values[0].name, "v1")
    self.assertEqual(r.enums[0].values[0].value, 10)
    self.assertEqual(r.enums[0].values[1].name, "v2")
    self.assertEqual(r.enums[0].values[1].value, 20)


class TestIntrospectionResponseDecode(unittest.TestCase):
  """Round-trip tests for introspection command Response auto-decode."""

  def test_get_object_command_interpret_response(self):
    """GetObjectCommand.Response decodes name, version, method_count, subobject_count."""
    from pylabrobot.liquid_handling.backends.hamilton.tcp.introspection import GetObjectCommand

    cmd = GetObjectCommand(Address(0, 0, 0))
    params = (
      HoiParams()
      .add("RootObj", Str)
      .add("1.0", Str)
      .add(3, I32)
      .add(2, I32)
      .build()
    )
    hoi = HoiPacket(
      interface_id=0,
      action_code=Hoi2Action.COMMAND_RESPONSE,
      action_id=0,
      params=params,
    )
    harp = HarpPacket(
      src=Address(0, 0, 0),
      dst=Address(0, 0, 0),
      seq=0,
      protocol=2,
      action_code=4,
      payload=hoi.pack(),
    )
    ip = IpPacket(protocol=6, payload=harp.pack())
    response = CommandResponse.from_bytes(ip.pack())
    result = cmd.interpret_response(response)
    self.assertIsInstance(result, GetObjectCommand.Response)
    self.assertEqual(result.name, "RootObj")
    self.assertEqual(result.version, "1.0")
    self.assertEqual(result.method_count, 3)
    self.assertEqual(result.subobject_count, 2)


class TestInterface0Capability(unittest.IsolatedAsyncioTestCase):
  """Tests for Interface 0 capability checks and clean traversal."""

  def test_interface0_constants(self):
    """Interface 0 method ID constants match the introspection command IDs."""
    from pylabrobot.liquid_handling.backends.hamilton.tcp.introspection import (
      GET_ENUMS,
      GET_INTERFACES,
      GET_METHOD,
      GET_OBJECT,
      GET_STRUCTS,
      GET_SUBOBJECT_ADDRESS,
    )

    self.assertEqual(GET_OBJECT, 1)
    self.assertEqual(GET_METHOD, 2)
    self.assertEqual(GET_SUBOBJECT_ADDRESS, 3)
    self.assertEqual(GET_INTERFACES, 4)
    self.assertEqual(GET_ENUMS, 5)
    self.assertEqual(GET_STRUCTS, 6)

  async def test_get_supported_interface0_method_ids_returns_expected_set(self):
    """get_supported_interface0_method_ids returns method_ids for interface 0 only."""
    from pylabrobot.liquid_handling.backends.hamilton.tcp.introspection import (
      GetMethodCommand,
      GetObjectCommand,
      HamiltonIntrospection,
    )

    addr = Address(1, 1, 100)
    get_object_response = GetObjectCommand.Response(
      name="Obj",
      version="1.0",
      method_count=2,
      subobject_count=0,
    )
    get_method_responses = [
      {"interface_id": 0, "method_id": 1, "call_type": 0, "name": "GetObject"},
      {"interface_id": 0, "method_id": 3, "call_type": 0, "name": "GetSubobjectAddress"},
    ]

    async def mock_send(cmd, **kwargs):
      if isinstance(cmd, GetObjectCommand):
        return get_object_response
      if isinstance(cmd, GetMethodCommand):
        idx = cmd.method_index
        return get_method_responses[idx] if idx < len(get_method_responses) else {}
      return None

    backend = AsyncMock()
    backend.send_command = AsyncMock(side_effect=mock_send)
    backend._registry = None
    introspection = HamiltonIntrospection(backend)
    result = await introspection.get_supported_interface0_method_ids(addr)
    self.assertEqual(result, {1, 3})

  async def test_resolve_raises_when_parent_does_not_support_get_subobject_address(self):
    """resolve() raises KeyError with clear message when parent does not support (0, 3)."""
    from pylabrobot.liquid_handling.backends.hamilton.tcp.introspection import (
      GetObjectCommand,
      ObjectInfo,
    )
    from pylabrobot.liquid_handling.backends.hamilton.tcp_backend import ObjectRegistry

    root_addr = Address(1, 1, 48896)
    root_info = ObjectInfo(
      name="Root",
      version="1.0",
      method_count=0,
      subobject_count=1,
      address=root_addr,
    )

    async def mock_send(cmd, **kwargs):
      if isinstance(cmd, GetObjectCommand):
        return GetObjectCommand.Response(
          name="Root",
          version="1.0",
          method_count=0,
          subobject_count=1,
        )
      return None

    transport = AsyncMock()
    transport.send_command = AsyncMock(side_effect=mock_send)
    registry = ObjectRegistry(transport)
    registry.set_root_addresses([root_addr])
    registry.register("Root", root_info)

    with self.assertRaises(KeyError) as ctx:
      await registry.resolve("Root.Child")
    self.assertIn("GetSubobjectAddress", str(ctx.exception))
    self.assertIn("interface 0, method 3", str(ctx.exception))
    self.assertIn("Child", str(ctx.exception))


if __name__ == "__main__":
  unittest.main()
