"""Tests for Hamilton TCP protocol implementation.

This module tests the packet structures, message builders, parameter encoding,
and command classes in the Hamilton TCP protocol stack.
"""

import unittest

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
  HamiltonDataType,
  HamiltonProtocol,
  Hoi2Action,
  RegistrationActionCode,
  RegistrationOptionType,
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
    packet = IpPacket(protocol=6, payload=b"\x01", options=b"\xab\xcd")
    packed = packet.pack()
    # Size = 1 + 1 + 2 + 2 (opts) + 1 (payload) = 7
    self.assertEqual(packed[4:6], b"\x02\x00")  # options length = 2
    self.assertEqual(packed[6:8], b"\xab\xcd")  # options
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
      payload=b"\xff",
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
    self.assertEqual(packed[2:4], b"\x2a\x00")  # action_id = 42 (little-endian)
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

    self.assertEqual(packed[0:2], b"\x0c\x00")  # action_code = 12
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
    params = HoiParams().i8(-128).build()
    # DataFragment: [type=1][flags=0][length=1][data=-128]
    self.assertEqual(params[0], HamiltonDataType.I8)
    self.assertEqual(params[1], 0)  # flags
    self.assertEqual(params[2:4], b"\x01\x00")  # length = 1
    self.assertEqual(params[4], 0x80)  # -128 as signed byte

  def test_i16(self):
    params = HoiParams().i16(-1000).build()
    self.assertEqual(params[0], HamiltonDataType.I16)
    self.assertEqual(params[2:4], b"\x02\x00")  # length = 2

  def test_i32(self):
    params = HoiParams().i32(100000).build()
    self.assertEqual(params[0], HamiltonDataType.I32)
    self.assertEqual(params[2:4], b"\x04\x00")  # length = 4

  def test_i64(self):
    params = HoiParams().i64(2**40).build()
    self.assertEqual(params[0], HamiltonDataType.I64)
    self.assertEqual(params[2:4], b"\x08\x00")  # length = 8

  def test_u8(self):
    params = HoiParams().u8(255).build()
    self.assertEqual(params[0], HamiltonDataType.U8)
    self.assertEqual(params[4], 255)

  def test_u16(self):
    params = HoiParams().u16(65535).build()
    self.assertEqual(params[0], HamiltonDataType.U16)
    self.assertEqual(params[4:6], b"\xff\xff")

  def test_u32(self):
    params = HoiParams().u32(0xDEADBEEF).build()
    self.assertEqual(params[0], HamiltonDataType.U32)
    self.assertEqual(params[4:8], b"\xef\xbe\xad\xde")

  def test_u64(self):
    params = HoiParams().u64(0xDEADBEEFCAFEBABE).build()
    self.assertEqual(params[0], HamiltonDataType.U64)

  def test_f32(self):
    params = HoiParams().f32(3.14).build()
    self.assertEqual(params[0], HamiltonDataType.F32)
    self.assertEqual(params[2:4], b"\x04\x00")

  def test_f64(self):
    params = HoiParams().f64(3.14159265358979).build()
    self.assertEqual(params[0], HamiltonDataType.F64)
    self.assertEqual(params[2:4], b"\x08\x00")

  def test_string(self):
    params = HoiParams().string("test").build()
    self.assertEqual(params[0], HamiltonDataType.STRING)
    self.assertEqual(params[2:4], b"\x05\x00")  # length = 5 (including null)
    self.assertEqual(params[4:9], b"test\x00")

  def test_bool_true(self):
    params = HoiParams().bool_value(True).build()
    self.assertEqual(params[0], HamiltonDataType.BOOL)
    self.assertEqual(params[4], 1)

  def test_bool_false(self):
    params = HoiParams().bool_value(False).build()
    self.assertEqual(params[0], HamiltonDataType.BOOL)
    self.assertEqual(params[4], 0)

  def test_i32_array(self):
    params = HoiParams().i32_array([1, 2, 3]).build()
    self.assertEqual(params[0], HamiltonDataType.I32_ARRAY)
    self.assertEqual(params[2:4], b"\x0c\x00")  # length = 12 (3 * 4)

  def test_u16_array(self):
    params = HoiParams().u16_array([100, 200, 300]).build()
    self.assertEqual(params[0], HamiltonDataType.U16_ARRAY)
    self.assertEqual(params[2:4], b"\x06\x00")  # length = 6 (3 * 2)

  def test_f32_array(self):
    params = HoiParams().f32_array([1.0, 2.0, 3.0]).build()
    self.assertEqual(params[0], HamiltonDataType.F32_ARRAY)
    self.assertEqual(params[2:4], b"\x0c\x00")  # length = 12 (3 * 4)

  def test_bool_array(self):
    params = HoiParams().bool_array([True, False, True]).build()
    self.assertEqual(params[0], HamiltonDataType.BOOL_ARRAY)
    self.assertEqual(params[1], 0x01)  # flags = 0x01 for bool arrays
    self.assertEqual(params[2:4], b"\x03\x00")  # length = 3
    self.assertEqual(params[4:7], b"\x01\x00\x01")

  def test_string_array(self):
    params = HoiParams().string_array(["a", "bc"]).build()
    self.assertEqual(params[0], HamiltonDataType.STRING_ARRAY)
    # String arrays have u32 count prefix
    self.assertEqual(params[4:8], b"\x02\x00\x00\x00")  # count = 2

  def test_method_chaining(self):
    self.assertEqual(HoiParams().i32(1).string("test").bool_value(True).count(), 3)

  def test_count(self):
    builder = HoiParams().i32(1).i32(2).i32(3)
    self.assertEqual(builder.count(), 3)


class TestHoiParamsParser(unittest.TestCase):
  """Tests for HoiParamsParser."""

  def test_parse_i32(self):
    params = HoiParams().i32(12345).build()
    parser = HoiParamsParser(params)
    type_id, value = parser.parse_next()
    self.assertEqual(type_id, HamiltonDataType.I32)
    self.assertEqual(value, 12345)

  def test_parse_negative_i32(self):
    params = HoiParams().i32(-12345).build()
    parser = HoiParamsParser(params)
    _, value = parser.parse_next()
    self.assertEqual(value, -12345)

  def test_parse_string(self):
    params = HoiParams().string("hello").build()
    parser = HoiParamsParser(params)
    type_id, value = parser.parse_next()
    self.assertEqual(type_id, HamiltonDataType.STRING)
    self.assertEqual(value, "hello")

  def test_parse_bool(self):
    params = HoiParams().bool_value(True).build()
    parser = HoiParamsParser(params)
    type_id, value = parser.parse_next()
    self.assertEqual(type_id, HamiltonDataType.BOOL)
    self.assertEqual(value, True)

  def test_parse_i32_array(self):
    params = HoiParams().i32_array([10, 20, 30]).build()
    parser = HoiParamsParser(params)
    type_id, value = parser.parse_next()
    self.assertEqual(type_id, HamiltonDataType.I32_ARRAY)
    self.assertEqual(value, [10, 20, 30])

  def test_parse_u16_array(self):
    params = HoiParams().u16_array([100, 200, 300]).build()
    parser = HoiParamsParser(params)
    type_id, value = parser.parse_next()
    self.assertEqual(type_id, HamiltonDataType.U16_ARRAY)
    self.assertEqual(value, [100, 200, 300])

  def test_parse_bool_array(self):
    params = HoiParams().bool_array([True, False, True, False]).build()
    parser = HoiParamsParser(params)
    type_id, value = parser.parse_next()
    self.assertEqual(type_id, HamiltonDataType.BOOL_ARRAY)
    self.assertEqual(value, [True, False, True, False])

  def test_parse_multiple(self):
    params = HoiParams().i32(100).string("test").bool_value(False).build()
    parser = HoiParamsParser(params)

    _, v1 = parser.parse_next()
    _, v2 = parser.parse_next()
    _, v3 = parser.parse_next()

    self.assertEqual(v1, 100)
    self.assertEqual(v2, "test")
    self.assertEqual(v3, False)

  def test_parse_all(self):
    params = HoiParams().i32(1).i32(2).i32(3).build()
    parser = HoiParamsParser(params)
    results = parser.parse_all()

    self.assertEqual(len(results), 3)
    self.assertEqual([v for _, v in results], [1, 2, 3])

  def test_has_remaining(self):
    params = HoiParams().i32(1).build()
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
    builder.i8(i8_val)
    builder.i16(i16_val)
    builder.i32(i32_val)
    builder.i64(i64_val)
    builder.u8(u8_val)
    builder.u16(u16_val)
    builder.u32(u32_val)
    builder.u64(u64_val)
    builder.f32(f32_val)
    builder.f64(f64_val)
    builder.string(string_val)
    builder.bool_value(bool_val)

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
    params = HoiParams().i32(100)
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
        return HoiParams().i32(100)

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


if __name__ == "__main__":
  unittest.main()
