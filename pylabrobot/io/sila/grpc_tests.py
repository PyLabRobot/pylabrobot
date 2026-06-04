import base64
import unittest
from unittest.mock import MagicMock

from pylabrobot.io.sila.grpc import (
  WIRE_32BIT,
  WIRE_64BIT,
  WIRE_LENGTH_DELIMITED,
  WIRE_VARINT,
  command_execution_uuid,
  decode_command_confirmation,
  decode_fields,
  decode_grpc_error,
  decode_sila_string_response,
  decode_varint,
  encode_signed_varint,
  encode_varint,
  extract_proto_strings,
  get_field_bytes,
  get_field_varint,
  length_delimited,
  lock_server_params,
  metadata_lock_identifier,
  sila_integer,
  sila_string,
  unlock_server_params,
  varint_as_signed,
  varint_field,
)

# ---------------------------------------------------------------------------
# Varint encoding / decoding
# ---------------------------------------------------------------------------


class TestVarintEncoding(unittest.TestCase):
  def test_single_byte(self):
    self.assertEqual(encode_varint(0), b"\x00")
    self.assertEqual(encode_varint(1), b"\x01")
    self.assertEqual(encode_varint(127), b"\x7f")

  def test_multi_byte(self):
    self.assertEqual(encode_varint(128), b"\x80\x01")
    self.assertEqual(encode_varint(300), b"\xac\x02")

  def test_large_value(self):
    encoded = encode_varint(0xFFFFFFFF)
    self.assertEqual(len(encoded), 5)

  def test_roundtrip(self):
    for value in [0, 1, 127, 128, 255, 256, 16384, 2**32 - 1, 2**63 - 1]:
      encoded = encode_varint(value)
      decoded, pos = decode_varint(encoded, 0)
      self.assertEqual(decoded, value)
      self.assertEqual(pos, len(encoded))


class TestSignedVarintEncoding(unittest.TestCase):
  def test_positive(self):
    self.assertEqual(encode_signed_varint(42), encode_varint(42))

  def test_negative(self):
    encoded = encode_signed_varint(-1)
    decoded, _ = decode_varint(encoded, 0)
    self.assertEqual(varint_as_signed(decoded), -1)

  def test_negative_roundtrip(self):
    for value in [-1, -100, -(2**31)]:
      encoded = encode_signed_varint(value)
      decoded, _ = decode_varint(encoded, 0)
      self.assertEqual(varint_as_signed(decoded), value)


class TestVarintAsSigned(unittest.TestCase):
  def test_positive_unchanged(self):
    self.assertEqual(varint_as_signed(42), 42)

  def test_max_positive(self):
    self.assertEqual(varint_as_signed(0x7FFFFFFFFFFFFFFF), 0x7FFFFFFFFFFFFFFF)

  def test_overflow_becomes_negative(self):
    self.assertEqual(varint_as_signed(0xFFFFFFFFFFFFFFFF), -1)


# ---------------------------------------------------------------------------
# Wire-format field encoding
# ---------------------------------------------------------------------------


class TestLengthDelimited(unittest.TestCase):
  def test_basic(self):
    result = length_delimited(1, b"hello")
    fields = decode_fields(result)
    self.assertEqual(get_field_bytes(fields, 1), b"hello")

  def test_empty_data(self):
    result = length_delimited(1, b"")
    fields = decode_fields(result)
    self.assertEqual(get_field_bytes(fields, 1), b"")

  def test_field_number_encoding(self):
    result = length_delimited(2, b"x")
    fields = decode_fields(result)
    self.assertIsNone(get_field_bytes(fields, 1))
    self.assertEqual(get_field_bytes(fields, 2), b"x")


class TestVarintField(unittest.TestCase):
  def test_basic(self):
    result = varint_field(1, 42)
    fields = decode_fields(result)
    self.assertEqual(get_field_varint(fields, 1), 42)

  def test_zero(self):
    result = varint_field(1, 0)
    fields = decode_fields(result)
    self.assertEqual(get_field_varint(fields, 1), 0)


# ---------------------------------------------------------------------------
# Protobuf decoding
# ---------------------------------------------------------------------------


class TestDecodeFields(unittest.TestCase):
  def test_empty(self):
    self.assertEqual(decode_fields(b""), {})

  def test_varint_field(self):
    data = varint_field(1, 150)
    fields = decode_fields(data)
    self.assertEqual(len(fields), 1)
    self.assertEqual(fields[1][0], (WIRE_VARINT, 150))

  def test_length_delimited_field(self):
    data = length_delimited(2, b"test")
    fields = decode_fields(data)
    self.assertEqual(len(fields), 1)
    self.assertEqual(fields[2][0][0], WIRE_LENGTH_DELIMITED)
    self.assertEqual(fields[2][0][1], b"test")

  def test_multiple_fields(self):
    data = varint_field(1, 10) + length_delimited(2, b"abc")
    fields = decode_fields(data)
    self.assertEqual(get_field_varint(fields, 1), 10)
    self.assertEqual(get_field_bytes(fields, 2), b"abc")

  def test_64bit_field(self):
    # Tag for field 1, wire type 1 (64-bit): (1 << 3) | 1 = 0x09
    data = b"\x09" + b"\x01\x02\x03\x04\x05\x06\x07\x08"
    fields = decode_fields(data)
    self.assertEqual(fields[1][0][0], WIRE_64BIT)
    self.assertEqual(len(fields[1][0][1]), 8)

  def test_32bit_field(self):
    # Tag for field 1, wire type 5 (32-bit): (1 << 3) | 5 = 0x0d
    data = b"\x0d" + b"\x01\x02\x03\x04"
    fields = decode_fields(data)
    self.assertEqual(fields[1][0][0], WIRE_32BIT)
    self.assertEqual(len(fields[1][0][1]), 4)


class TestGetFieldBytes(unittest.TestCase):
  def test_missing_field(self):
    fields = decode_fields(varint_field(1, 42))
    self.assertIsNone(get_field_bytes(fields, 2))

  def test_wrong_wire_type(self):
    fields = decode_fields(varint_field(1, 42))
    self.assertIsNone(get_field_bytes(fields, 1))


class TestGetFieldVarint(unittest.TestCase):
  def test_missing_field(self):
    fields = decode_fields(length_delimited(1, b"x"))
    self.assertIsNone(get_field_varint(fields, 2))

  def test_wrong_wire_type(self):
    fields = decode_fields(length_delimited(1, b"x"))
    self.assertIsNone(get_field_varint(fields, 1))


# ---------------------------------------------------------------------------
# Proto string extraction
# ---------------------------------------------------------------------------


class TestExtractProtoStrings(unittest.TestCase):
  def test_simple_string(self):
    data = length_delimited(1, b"hello world")
    strings = extract_proto_strings(data)
    self.assertIn("hello world", strings)

  def test_nested_strings(self):
    inner = length_delimited(1, b"inner")
    outer = length_delimited(1, inner)
    strings = extract_proto_strings(outer)
    self.assertIn("inner", strings)

  def test_non_utf8_skipped(self):
    data = length_delimited(1, b"\xff\xfe\xfd\xfc")
    strings = extract_proto_strings(data)
    self.assertEqual(strings, [])

  def test_empty_data(self):
    self.assertEqual(extract_proto_strings(b""), [])

  def test_invalid_data(self):
    self.assertEqual(extract_proto_strings(b"\xff\xff\xff"), [])


# ---------------------------------------------------------------------------
# SiLA wrapper types
# ---------------------------------------------------------------------------


class TestSilaString(unittest.TestCase):
  def test_roundtrip(self):
    encoded = sila_string("hello")
    fields = decode_fields(encoded)
    value = get_field_bytes(fields, 1)
    self.assertEqual(value, b"hello")

  def test_unicode(self):
    encoded = sila_string("caf\u00e9")
    fields = decode_fields(encoded)
    self.assertEqual(get_field_bytes(fields, 1), "caf\u00e9".encode("utf-8"))

  def test_empty_string(self):
    encoded = sila_string("")
    fields = decode_fields(encoded)
    self.assertEqual(get_field_bytes(fields, 1), b"")


class TestSilaInteger(unittest.TestCase):
  def test_positive(self):
    encoded = sila_integer(42)
    fields = decode_fields(encoded)
    self.assertEqual(get_field_varint(fields, 1), 42)

  def test_zero(self):
    encoded = sila_integer(0)
    fields = decode_fields(encoded)
    self.assertEqual(get_field_varint(fields, 1), 0)


# ---------------------------------------------------------------------------
# SiLA message builders
# ---------------------------------------------------------------------------


class TestLockServerParams(unittest.TestCase):
  def test_contains_lock_id(self):
    data = lock_server_params("my-lock", 60)
    fields = decode_fields(data)
    sila_str_field = get_field_bytes(fields, 1)
    assert sila_str_field is not None
    inner = decode_fields(sila_str_field)
    self.assertEqual(get_field_bytes(inner, 1), b"my-lock")

  def test_contains_timeout(self):
    data = lock_server_params("x", 120)
    fields = decode_fields(data)
    sila_int_field = get_field_bytes(fields, 2)
    assert sila_int_field is not None
    inner = decode_fields(sila_int_field)
    self.assertEqual(get_field_varint(inner, 1), 120)


class TestUnlockServerParams(unittest.TestCase):
  def test_contains_lock_id(self):
    data = unlock_server_params("my-lock")
    fields = decode_fields(data)
    sila_str_field = get_field_bytes(fields, 1)
    assert sila_str_field is not None
    inner = decode_fields(sila_str_field)
    self.assertEqual(get_field_bytes(inner, 1), b"my-lock")


class TestMetadataLockIdentifier(unittest.TestCase):
  def test_structure(self):
    data = metadata_lock_identifier("lock-123")
    fields = decode_fields(data)
    sila_str_field = get_field_bytes(fields, 1)
    assert sila_str_field is not None
    inner = decode_fields(sila_str_field)
    self.assertEqual(get_field_bytes(inner, 1), b"lock-123")


class TestCommandExecutionUuid(unittest.TestCase):
  def test_structure(self):
    data = command_execution_uuid("abc-def-123")
    fields = decode_fields(data)
    self.assertEqual(get_field_bytes(fields, 1), b"abc-def-123")


# ---------------------------------------------------------------------------
# SiLA response decoders
# ---------------------------------------------------------------------------


class TestDecodeSilaStringResponse(unittest.TestCase):
  def test_roundtrip(self):
    response = length_delimited(1, sila_string("result"))
    self.assertEqual(decode_sila_string_response(response), "result")

  def test_missing_outer_field_raises(self):
    with self.assertRaises(ValueError) as ctx:
      decode_sila_string_response(b"")
    self.assertIn("No SiLA String", str(ctx.exception))

  def test_missing_inner_value_raises(self):
    # Outer field 1 present but contains no inner field 1
    response = length_delimited(1, varint_field(2, 99))
    with self.assertRaises(ValueError) as ctx:
      decode_sila_string_response(response)
    self.assertIn("No value", str(ctx.exception))


class TestDecodeCommandConfirmation(unittest.TestCase):
  def test_delegates_to_string_response(self):
    response = length_delimited(1, sila_string("uuid-abc"))
    self.assertEqual(decode_command_confirmation(response), "uuid-abc")


# ---------------------------------------------------------------------------
# gRPC error decoding
# ---------------------------------------------------------------------------


class TestDecodeGrpcError(unittest.TestCase):
  def test_base64_protobuf_details(self):
    # Encode a protobuf message with a string field
    proto_msg = length_delimited(1, b"Something went wrong")
    b64 = base64.b64encode(proto_msg).decode("ascii")

    error = MagicMock()
    error.details.return_value = b64
    result = decode_grpc_error(error)
    self.assertIn("Something went wrong", result)

  def test_no_details_returns_str(self):
    error = MagicMock()
    error.details.return_value = ""
    result = decode_grpc_error(error)
    self.assertIn("MagicMock", result)  # str(error)

  def test_non_base64_returns_raw_details(self):
    error = MagicMock()
    error.details.return_value = "plain text error"
    result = decode_grpc_error(error)
    self.assertEqual(result, "plain text error")

  def test_no_details_method(self):
    error = MagicMock(spec=[])
    result = decode_grpc_error(error)
    self.assertIsInstance(result, str)
