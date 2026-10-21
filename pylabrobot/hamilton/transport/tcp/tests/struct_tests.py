"""Explicit structure encoding and nested DataFragment wire compatibility."""

import unittest
from dataclasses import dataclass
from typing import Annotated

from pylabrobot.hamilton.transport.tcp.messages import HoiParams, HoiParamsParser, parse_into_struct
from pylabrobot.hamilton.transport.tcp.wire_types import U16, Str, Struct, StructArray


@dataclass(frozen=True)
class _Sample:
  """A structure declaring encoding explicitly and decoding through annotations."""

  count: U16
  name: Str

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in the order required by this wire structure."""
    return params.add(self.count, U16).add(self.name, Str)


@dataclass(frozen=True)
class _Envelope:
  """Nested structure and structure-array fields."""

  sample: Annotated[_Sample, Struct()]
  samples: Annotated[list[_Sample], StructArray()]

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode nested fields through their explicit structure encoders."""
    return params.add(self.sample, Struct()).add(self.samples, StructArray())


class TestExplicitStructEncoding(unittest.TestCase):
  def test_struct_encoder_defines_fields_without_dataclass_reflection(self) -> None:
    """Encoding follows the explicit method and ignores host-only attributes."""

    class SampleWithMetadata:
      """A plain object can implement the structure encoding contract."""

      def __init__(self) -> None:
        """Keep a host-only label alongside the values destined for firmware."""
        self.note = "not a wire field"
        self.sample = _Sample(0x1234, "abc")

      def encode_into(self, params: HoiParams) -> HoiParams:
        """Select only the sample fields for transmission."""
        return self.sample.encode_into(params)

    encoded = HoiParams.from_struct(SampleWithMetadata()).build()
    self.assertEqual(encoded, bytes.fromhex("0500020034120f00040061626300"))

  def test_nested_struct_and_array_match_wire_bytes(self) -> None:
    """Each nested value keeps its type, length, ordering, and scalar fragments."""
    first = _Sample(0x1234, "abc")
    second = _Sample(7, "q")
    envelope = _Envelope(first, [first, second])
    expected = bytes.fromhex(
      "1e000e00"
      "0500020034120f00040061626300"
      "1f002200"
      "1e000e00"
      "0500020034120f00040061626300"
      "1e000c00"
      "0500020007000f0002007100"
    )
    encoded = HoiParams.from_struct(envelope).build()
    self.assertEqual(encoded, expected)
    self.assertEqual(parse_into_struct(HoiParamsParser(expected), _Envelope), envelope)

  def test_empty_struct_array_has_an_empty_payload(self) -> None:
    """Zero elements still produce a correctly typed, zero-length fragment."""
    self.assertEqual(HoiParams().add([], StructArray()).build(), bytes.fromhex("1f000000"))

  def test_struct_appends_to_existing_parameters(self) -> None:
    """An explicit encoder preserves fields already in the supplied builder."""
    params = HoiParams().u8(9)
    result = _Sample(0x1234, "abc").encode_into(params)
    self.assertIs(result, params)
    self.assertEqual(result.build(), bytes.fromhex("04000100090500020034120f00040061626300"))
