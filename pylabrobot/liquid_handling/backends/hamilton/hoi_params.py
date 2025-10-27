"""HOI parameter builder with automatic DataFragment wrapping.

This module provides HoiParams, which automatically wraps all values with
DataFragment headers: [type_id:1][flags:1][length:2][data:n]

DataFragments are ONLY used for HOI2 command parameters. All other packet
types (IP, HARP, ConnectionPacket, Registration) use raw Wire serialization.

Example:
    params = (HoiParams()
              .i32(100)
              .string("test")
              .u32_array([1, 2, 3])
              .build())

    # Creates concatenated DataFragments:
    # [0x03|0x00|0x04|0x00|100][0x0F|0x00|0x05|0x00|"test\0"][0x1C|0x00|...array...]
"""

from __future__ import annotations

import struct
from typing import Any

from .wire import Wire


# Hamilton type IDs (from official ParameterTypes enumeration)
TYPE_I8 = 1
TYPE_I16 = 2
TYPE_I32 = 3
TYPE_U8 = 4
TYPE_U16 = 5
TYPE_U32 = 6
TYPE_STRING = 15
TYPE_U8_ARRAY = 22
TYPE_BOOL = 23
TYPE_I8_ARRAY = 24
TYPE_I16_ARRAY = 25
TYPE_U16_ARRAY = 26
TYPE_I32_ARRAY = 27
TYPE_U32_ARRAY = 28
TYPE_BOOL_ARRAY = 29
TYPE_STRING_ARRAY = 34
TYPE_I64 = 36
TYPE_U64 = 37
TYPE_I64_ARRAY = 38
TYPE_U64_ARRAY = 39
TYPE_F32 = 40
TYPE_F64 = 41
TYPE_F32_ARRAY = 42
TYPE_F64_ARRAY = 43


class HoiParams:
    """Builder for HOI parameters with automatic DataFragment wrapping.

    Each parameter is wrapped with DataFragment header before being added:
    [type_id:1][flags:1][length:2][data:n]

    This ensures HOI parameters are always correctly formatted and eliminates
    the possibility of forgetting to add DataFragment headers.
    """

    def __init__(self):
        self._fragments: list[bytes] = []

    def _add_fragment(self, type_id: int, data: bytes) -> 'HoiParams':
        """Add a DataFragment with the given type_id and data.

        Creates: [type_id:1][flags:1][length:2][data:n]
        """
        fragment = (Wire.write()
                    .u8(type_id)
                    .u8(0)  # flags (always 0)
                    .u16(len(data))
                    .bytes(data)
                    .finish())
        self._fragments.append(fragment)
        return self

    # Scalar integer types
    def i8(self, value: int) -> 'HoiParams':
        """Add signed 8-bit integer parameter."""
        data = Wire.write().i8(value).finish()
        return self._add_fragment(TYPE_I8, data)

    def i16(self, value: int) -> 'HoiParams':
        """Add signed 16-bit integer parameter."""
        data = Wire.write().i16(value).finish()
        return self._add_fragment(TYPE_I16, data)

    def i32(self, value: int) -> 'HoiParams':
        """Add signed 32-bit integer parameter."""
        data = Wire.write().i32(value).finish()
        return self._add_fragment(TYPE_I32, data)

    def i64(self, value: int) -> 'HoiParams':
        """Add signed 64-bit integer parameter."""
        data = Wire.write().i64(value).finish()
        return self._add_fragment(TYPE_I64, data)

    def u8(self, value: int) -> 'HoiParams':
        """Add unsigned 8-bit integer parameter."""
        data = Wire.write().u8(value).finish()
        return self._add_fragment(TYPE_U8, data)

    def u16(self, value: int) -> 'HoiParams':
        """Add unsigned 16-bit integer parameter."""
        data = Wire.write().u16(value).finish()
        return self._add_fragment(TYPE_U16, data)

    def u32(self, value: int) -> 'HoiParams':
        """Add unsigned 32-bit integer parameter."""
        data = Wire.write().u32(value).finish()
        return self._add_fragment(TYPE_U32, data)

    def u64(self, value: int) -> 'HoiParams':
        """Add unsigned 64-bit integer parameter."""
        data = Wire.write().u64(value).finish()
        return self._add_fragment(TYPE_U64, data)

    # Floating-point types
    def f32(self, value: float) -> 'HoiParams':
        """Add 32-bit float parameter."""
        data = Wire.write().f32(value).finish()
        return self._add_fragment(TYPE_F32, data)

    def f64(self, value: float) -> 'HoiParams':
        """Add 64-bit double parameter."""
        data = Wire.write().f64(value).finish()
        return self._add_fragment(TYPE_F64, data)

    # String and bool
    def string(self, value: str) -> 'HoiParams':
        """Add null-terminated string parameter."""
        data = Wire.write().string(value).finish()
        return self._add_fragment(TYPE_STRING, data)

    def bool(self, value: bool) -> 'HoiParams':
        """Add boolean parameter."""
        data = Wire.write().u8(1 if value else 0).finish()
        return self._add_fragment(TYPE_BOOL, data)

    # Array types
    def i8_array(self, values: list[int]) -> 'HoiParams':
        """Add array of signed 8-bit integers.

        Format: [count:4][element0][element1]...
        """
        writer = Wire.write().u32(len(values))
        for val in values:
            writer.i8(val)
        return self._add_fragment(TYPE_I8_ARRAY, writer.finish())

    def i16_array(self, values: list[int]) -> 'HoiParams':
        """Add array of signed 16-bit integers."""
        writer = Wire.write().u32(len(values))
        for val in values:
            writer.i16(val)
        return self._add_fragment(TYPE_I16_ARRAY, writer.finish())

    def i32_array(self, values: list[int]) -> 'HoiParams':
        """Add array of signed 32-bit integers."""
        writer = Wire.write().u32(len(values))
        for val in values:
            writer.i32(val)
        return self._add_fragment(TYPE_I32_ARRAY, writer.finish())

    def i64_array(self, values: list[int]) -> 'HoiParams':
        """Add array of signed 64-bit integers."""
        writer = Wire.write().u32(len(values))
        for val in values:
            writer.i64(val)
        return self._add_fragment(TYPE_I64_ARRAY, writer.finish())

    def u8_array(self, values: list[int]) -> 'HoiParams':
        """Add array of unsigned 8-bit integers."""
        writer = Wire.write().u32(len(values))
        for val in values:
            writer.u8(val)
        return self._add_fragment(TYPE_U8_ARRAY, writer.finish())

    def u16_array(self, values: list[int]) -> 'HoiParams':
        """Add array of unsigned 16-bit integers."""
        writer = Wire.write().u32(len(values))
        for val in values:
            writer.u16(val)
        return self._add_fragment(TYPE_U16_ARRAY, writer.finish())

    def u32_array(self, values: list[int]) -> 'HoiParams':
        """Add array of unsigned 32-bit integers."""
        writer = Wire.write().u32(len(values))
        for val in values:
            writer.u32(val)
        return self._add_fragment(TYPE_U32_ARRAY, writer.finish())

    def u64_array(self, values: list[int]) -> 'HoiParams':
        """Add array of unsigned 64-bit integers."""
        writer = Wire.write().u32(len(values))
        for val in values:
            writer.u64(val)
        return self._add_fragment(TYPE_U64_ARRAY, writer.finish())

    def f32_array(self, values: list[float]) -> 'HoiParams':
        """Add array of 32-bit floats."""
        writer = Wire.write().u32(len(values))
        for val in values:
            writer.f32(val)
        return self._add_fragment(TYPE_F32_ARRAY, writer.finish())

    def f64_array(self, values: list[float]) -> 'HoiParams':
        """Add array of 64-bit doubles."""
        writer = Wire.write().u32(len(values))
        for val in values:
            writer.f64(val)
        return self._add_fragment(TYPE_F64_ARRAY, writer.finish())

    def bool_array(self, values: list[bool]) -> 'HoiParams':
        """Add array of booleans (stored as u8: 0 or 1)."""
        writer = Wire.write().u32(len(values))
        for val in values:
            writer.u8(1 if val else 0)
        return self._add_fragment(TYPE_BOOL_ARRAY, writer.finish())

    def string_array(self, values: list[str]) -> 'HoiParams':
        """Add array of null-terminated strings.

        Format: [count:4][str0\0][str1\0]...
        """
        writer = Wire.write().u32(len(values))
        for val in values:
            writer.string(val)
        return self._add_fragment(TYPE_STRING_ARRAY, writer.finish())

    def build(self) -> bytes:
        """Return concatenated DataFragments."""
        return b''.join(self._fragments)

    def count(self) -> int:
        """Return number of fragments (parameters)."""
        return len(self._fragments)


class HoiParamsParser:
    """Parser for HOI DataFragment parameters.

    Parses DataFragment-wrapped values from HOI response payloads.
    """

    def __init__(self, data: bytes):
        self._data = data
        self._offset = 0

    def parse_next(self) -> tuple[int, Any]:
        """Parse the next DataFragment and return (type_id, value).

        Returns:
            Tuple of (type_id, parsed_value)

        Raises:
            ValueError: If data is malformed or insufficient
        """
        if self._offset + 4 > len(self._data):
            raise ValueError(f"Insufficient data for DataFragment header at offset {self._offset}")

        # Parse DataFragment header
        reader = Wire.read(self._data[self._offset:])
        type_id = reader.u8()
        flags = reader.u8()
        length = reader.u16()

        data_start = self._offset + 4
        data_end = data_start + length

        if data_end > len(self._data):
            raise ValueError(f"DataFragment data extends beyond buffer: need {data_end}, have {len(self._data)}")

        # Extract data payload
        fragment_data = self._data[data_start:data_end]
        value = self._parse_value(type_id, fragment_data)

        # Move offset past this fragment
        self._offset = data_end

        return (type_id, value)

    def _parse_value(self, type_id: int, data: bytes) -> Any:
        """Parse value based on type_id using dispatch table."""
        reader = Wire.read(data)

        # Dispatch table for scalar types
        scalar_parsers = {
            TYPE_I8: reader.i8,
            TYPE_I16: reader.i16,
            TYPE_I32: reader.i32,
            TYPE_I64: reader.i64,
            TYPE_U8: reader.u8,
            TYPE_U16: reader.u16,
            TYPE_U32: reader.u32,
            TYPE_U64: reader.u64,
            TYPE_F32: reader.f32,
            TYPE_F64: reader.f64,
            TYPE_STRING: reader.string,
        }

        # Check scalar types first
        if type_id in scalar_parsers:
            return scalar_parsers[type_id]()

        # Special case: bool
        if type_id == TYPE_BOOL:
            return reader.u8() == 1

        # Dispatch table for array element parsers
        array_element_parsers = {
            TYPE_I8_ARRAY: reader.i8,
            TYPE_I16_ARRAY: reader.i16,
            TYPE_I32_ARRAY: reader.i32,
            TYPE_I64_ARRAY: reader.i64,
            TYPE_U8_ARRAY: reader.u8,
            TYPE_U16_ARRAY: reader.u16,
            TYPE_U32_ARRAY: reader.u32,
            TYPE_U64_ARRAY: reader.u64,
            TYPE_F32_ARRAY: reader.f32,
            TYPE_F64_ARRAY: reader.f64,
            TYPE_STRING_ARRAY: reader.string,
        }

        # Handle arrays
        if type_id in array_element_parsers:
            count = reader.u32()
            return [array_element_parsers[type_id]() for _ in range(count)]

        # Special case: bool array
        if type_id == TYPE_BOOL_ARRAY:
            count = reader.u32()
            return [reader.u8() == 1 for _ in range(count)]

        # Unknown type
        raise ValueError(f"Unknown or unsupported type_id: {type_id}")

    def has_remaining(self) -> bool:
        """Check if there are more DataFragments to parse."""
        return self._offset < len(self._data)

    def parse_all(self) -> list[tuple[int, Any]]:
        """Parse all remaining DataFragments.

        Returns:
            List of (type_id, value) tuples
        """
        results = []
        while self.has_remaining():
            results.append(self.parse_next())
        return results

