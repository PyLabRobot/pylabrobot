import json
import logging
import struct
from collections import deque
from typing import Any, Dict, List, TypedDict

from pylabrobot.io.binary import Reader

logger = logging.getLogger(__name__)


class TDCLType(TypedDict):
  name: str
  size: int
  format: str


TDCL_DATA_TYPE_MAP: Dict[int, TDCLType] = {
  0x00: {"name": "U16RD", "size": 2, "format": ">H"},
  0x01: {"name": "U32RD", "size": 4, "format": ">I"},
  0x02: {"name": "U16MD", "size": 2, "format": ">H"},
  0x03: {"name": "U16MD2", "size": 2, "format": ">H"},
  0x04: {"name": "U16MD3", "size": 2, "format": ">H"},
  0x05: {"name": "U16MD4", "size": 2, "format": ">H"},
  0x06: {"name": "U16MD5", "size": 2, "format": ">H"},
  0x07: {"name": "U16MD6", "size": 2, "format": ">H"},
  0x08: {"name": "U16MD7", "size": 2, "format": ">H"},
  0x09: {"name": "U16MD8", "size": 2, "format": ">H"},
  0x0A: {"name": "x100U16TEMP", "size": 2, "format": ">H"},  # Divide by 100
  0x0B: {"name": "x10U16RWL", "size": 2, "format": ">H"},  # Divide by 10
  0x0C: {"name": "U32TIME", "size": 4, "format": ">I"},
  0x0D: {"name": "U32DARK", "size": 4, "format": ">I"},
  0x0E: {"name": "U32MD", "size": 4, "format": ">I"},
  0x0F: {"name": "U8RATIO", "size": 1, "format": ">B"},
  0x10: {"name": "U16ATT", "size": 2, "format": ">H"},
  0x11: {"name": "U16GAIN", "size": 2, "format": ">H"},
  0x12: {"name": "U16MULT", "size": 2, "format": ">H"},
  0x13: {"name": "U16MULT_H", "size": 2, "format": ">H"},
  0x14: {"name": "U32MTIME", "size": 4, "format": ">I"},
  0x15: {"name": "U16RD_DARK", "size": 2, "format": ">H"},
  0x16: {"name": "U16MD_DARK", "size": 2, "format": ">H"},
  0x17: {"name": "x10U16MWL", "size": 2, "format": ">H"},  # Divide by 10
  0x18: {"name": "U16MGAIN", "size": 2, "format": ">H"},
  0x19: {"name": "U8BYTE", "size": 1, "format": ">B"},
  0x1A: {"name": "U16READ_COUNT", "size": 2, "format": ">H"},
  0x1B: {"name": "U16RD_HOR", "size": 2, "format": ">H"},
  0x1C: {"name": "U16MD_HOR", "size": 2, "format": ">H"},
  0x1D: {"name": "U16RD_VER", "size": 2, "format": ">H"},
  0x1E: {"name": "U16MD_VER", "size": 2, "format": ">H"},
  0x1F: {"name": "U8MIR_POS", "size": 1, "format": ">B"},
  0x20: {"name": "U16VIB", "size": 2, "format": ">H"},
}

PACKET_TYPE = {
  1: "MsgAscii",
  2: "MsgTerminate",
  3: "MsgBinary",
  129: "RespReady",
  130: "RespTerminate",
  131: "RespBinary",
  132: "RespBusy",
  133: "RespMessage",
  134: "RespError",
  135: "RespLog",
  136: "RespBinaryHeader",
  137: "RespAsyncError",
}


class SparkPacket:
  def __init__(self, data_bytes: bytes) -> None:
    self.raw_data: bytes = data_bytes
    if len(self.raw_data) < 5:
      raise ValueError("Packet too short")

    reader = Reader(self.raw_data, little_endian=False)
    self.indicator: int = reader.u8()
    self.type: str = PACKET_TYPE.get(self.indicator, f"Unknown_{self.indicator}")
    self.seq_num: int = reader.u8()
    self.payload_len: int = reader.u16()
    payload_end = 4 + self.payload_len
    if len(self.raw_data) < payload_end + 1:
      raise ValueError("Packet data shorter than indicated payload length")

    self.payload_bytes: bytes = self.raw_data[4:payload_end]
    self.checksum: int = self.raw_data[payload_end]
    self.parsed_payload: Dict[str, Any] = self._parse_payload()

  def _parse_payload(self) -> Dict[str, Any]:
    try:
      if self.indicator == 129:
        return self._parse_resp_ready()
      if self.indicator == 130:
        return self._parse_resp_terminate()
      if self.indicator == 131:
        return self._parse_resp_binary(is_header=False)
      if self.indicator == 132:
        return self._parse_resp_busy()
      if self.indicator == 133:
        return self._parse_resp_message()
      if self.indicator == 134:
        return self._parse_resp_error(is_async=False)
      if self.indicator == 135:
        return self._parse_resp_log()
      if self.indicator == 136:
        return self._parse_resp_binary(is_header=True)
      if self.indicator == 137:
        return self._parse_resp_error(is_async=True)
      return {"raw_payload": self.payload_bytes}
    except Exception as e:
      logger.error(f"Error parsing payload for type {self.type} (seq {self.seq_num}): {e}")
      return {"parsing_error": str(e), "raw_payload": self.payload_bytes}

  def _parse_resp_ready(self) -> Dict[str, Any]:
    if not self.payload_bytes:
      return {"message": None}
    return {"message": self.payload_bytes.decode("utf-8", errors="ignore")}

  def _parse_resp_terminate(self) -> Dict[str, Any]:
    if len(self.payload_bytes) < 4:
      return {"time": None}
    reader = Reader(self.payload_bytes, little_endian=False)
    return {"time": reader.u32()}

  def _parse_resp_binary(self, is_header: bool = False) -> Dict[str, Any]:
    return {"is_header": is_header, "data": self.payload_bytes}

  def _parse_resp_busy(self) -> Dict[str, Any]:
    if len(self.payload_bytes) < 4:
      return {"time": None}
    reader = Reader(self.payload_bytes, little_endian=False)
    return {"time": reader.u32()}

  def _parse_resp_log(self) -> Dict[str, Any]:
    return {"message": self.payload_bytes.decode("utf-8", errors="ignore")}

  def _parse_resp_message(self) -> Dict[str, Any]:
    reader = Reader(self.payload_bytes, little_endian=False)
    number = reader.u16()
    message_str = reader.remaining().decode("utf-8", errors="ignore")
    parts = message_str.split("|")
    return {"number": number, "format": parts[0], "args": parts[1:]}

  def _parse_resp_error(self, is_async: bool = False) -> Dict[str, Any]:
    reader = Reader(self.payload_bytes, little_endian=False)
    timestamp = reader.u32()
    number = reader.u16()
    message_str = reader.remaining().decode("utf-8", errors="ignore")
    parts = message_str.split("|")
    return {
      "async": is_async,
      "timestamp": timestamp,
      "number": number,
      "format": parts[0],
      "args": parts[1:],
    }

  def to_dict(self) -> Dict[str, Any]:
    payload_serializable = {}
    if self.parsed_payload:
      for k, v in self.parsed_payload.items():
        if isinstance(v, bytes):
          payload_serializable[k] = v.hex()
        else:
          payload_serializable[k] = v
    return {
      "type": self.type,
      "indicator": self.indicator,
      "seq_num": self.seq_num,
      "payload_len": self.payload_len,
      "payload": payload_serializable,
      "checksum": self.checksum,
    }


class MeasurementBlock:
  def __init__(self, header_packet: SparkPacket, data_packets: "deque[SparkPacket]") -> None:
    if not header_packet or header_packet.indicator != 136:
      raise ValueError("Invalid header packet provided")

    self.header_packet: SparkPacket = header_packet
    self.data_packets: "deque[SparkPacket]" = data_packets
    self.seq_num: int = header_packet.seq_num
    self.byte_buffer: bytes = b""

    header_type_codes = list(self.header_packet.parsed_payload["data"])
    self.header_types: List[TDCLType] = []
    for code in header_type_codes:
      type_info = TDCL_DATA_TYPE_MAP.get(code)
      if type_info:
        self.header_types.append(type_info)
      else:
        logger.warning(f"Unknown TDCL data type code: {code} in seq {self.seq_num}")
    self.header_type_names: List[str] = [t["name"] for t in self.header_types]

  def _ensure_buffer(self, num_bytes: int) -> None:
    while len(self.byte_buffer) < num_bytes:
      if not self.data_packets:
        raise ValueError(
          f"Incomplete data: Needed {num_bytes}, buffer has {len(self.byte_buffer)} for seq {self.seq_num}"
        )
      self.byte_buffer += self.data_packets.popleft().parsed_payload["data"]

  def _consume_buffer(self, num_bytes: int) -> bytes:
    self._ensure_buffer(num_bytes)
    data = self.byte_buffer[:num_bytes]
    self.byte_buffer = self.byte_buffer[num_bytes:]
    return data

  def _read_u16_from_buffer(self) -> int:
    reader = Reader(self._consume_buffer(2), little_endian=False)
    return reader.u16()

  def _parse_generic_payload(self, types: List[TDCLType]) -> Dict[str, Any]:
    parsed = {}
    for i, type_info in enumerate(types):
      size = type_info["size"]
      fmt = type_info["format"]
      name = type_info["name"]

      data_bytes = self._consume_buffer(size)
      try:
        value = struct.unpack(fmt, data_bytes)[0]
      except struct.error as e:
        raise ValueError(f"Error unpacking {name} ({fmt}): {e}")

      if name == "x100U16TEMP":
        value /= 100.0
      if name in ["x10U16RWL", "x10U16MWL"]:
        value /= 10.0

      field_name = f"{name}_{i}"
      parsed[field_name] = value
    return parsed

  def parse(self) -> Dict[str, Any]:
    result = {
      "sequence_number": self.seq_num,
      "header_types": self.header_type_names,
    }

    try:
      inner_mult_index = -1
      rd_md_found = False
      for i in reversed(range(len(self.header_type_names))):
        if "U16RD" in self.header_type_names[i] or "U16MD" in self.header_type_names[i]:
          rd_md_found = True
        if rd_md_found and self.header_type_names[i] == "U16MULT":
          inner_mult_index = i
          break

      outer_mult_index = -1
      if inner_mult_index > 0:
        for i in reversed(range(inner_mult_index)):
          if self.header_type_names[i] == "U16MULT":
            outer_mult_index = i
            break

      if outer_mult_index != -1:
        logger.info(f"Detected Nested MULT structure for seq {self.seq_num}")
        result["structure_type"] = "nested_mult"
        self._parse_nested_mult(result, outer_mult_index, inner_mult_index)
      elif inner_mult_index != -1:
        logger.info(f"Detected Single MULT-RD-MD structure for seq {self.seq_num}")
        result["structure_type"] = "single_mult"
        self._parse_single_mult(result, inner_mult_index)
      else:
        logger.info(f"Using generic linear parser for seq {self.seq_num}")
        result["structure_type"] = "linear"
        self._parse_linear(result)

      if self.byte_buffer:
        result["remaining_buffer"] = self.byte_buffer.hex()
        logger.warning(
          f"Remaining buffer after parsing seq {self.seq_num}: {len(self.byte_buffer)} bytes"
        )

    except Exception as e:
      result["parsing_error"] = str(e)
      logger.error(f"Error in parse_measurement_block for seq {self.seq_num}: {e}", exc_info=True)
      if self.byte_buffer:
        result["raw_payload_on_error"] = self.byte_buffer.hex()

    return result

  def _parse_nested_mult(
    self, result: Dict[str, Any], outer_mult_index: int, inner_mult_index: int
  ) -> None:
    outer_mult_types = self.header_types[:outer_mult_index]
    if outer_mult_types:
      result.update(self._parse_generic_payload(outer_mult_types))

    outer_mult = self._read_u16_from_buffer()
    result["outer_mult"] = outer_mult

    common_types = self.header_types[outer_mult_index + 1 : inner_mult_index]
    inner_loop_types = self.header_types[inner_mult_index + 1 :]

    measurements = []
    for i in range(outer_mult):
      measurement: Dict[str, Any] = {"outer_index": i}
      if common_types:
        measurement.update(self._parse_generic_payload(common_types))

      inner_mult = self._read_u16_from_buffer()
      measurement["inner_mult"] = inner_mult

      inner_loops = []
      for j in range(inner_mult):
        inner_loop_data = {"inner_index": j}
        inner_loop_data.update(self._parse_generic_payload(inner_loop_types))
        inner_loops.append(inner_loop_data)
      measurement["inner_loops"] = inner_loops
      measurements.append(measurement)
    result["measurements"] = measurements

  def _parse_single_mult(self, result: Dict[str, Any], inner_mult_index: int) -> None:
    initial_types = self.header_types[:inner_mult_index]
    if initial_types:
      result.update(self._parse_generic_payload(initial_types))

    mult = self._read_u16_from_buffer()
    result["mult"] = mult

    inner_loop_types = self.header_types[inner_mult_index + 1 :]
    rd_md_pairs = []
    for i in range(mult):
      pair_data = {"index": i}
      pair_data.update(self._parse_generic_payload(inner_loop_types))
      rd_md_pairs.append(pair_data)
    result["rd_md_pairs"] = rd_md_pairs

  def _parse_linear(self, result: Dict[str, Any]) -> None:
    while self.data_packets:
      self.byte_buffer += self.data_packets.popleft().parsed_payload["data"]

    total_size = sum(t["size"] for t in self.header_types)
    if len(self.byte_buffer) < total_size:
      logger.warning(
        f"Buffer size {len(self.byte_buffer)} less than expected {total_size} for linear parse in seq {self.seq_num}"
      )

    # Attempt to parse what's available
    parsed = {}
    offset = 0
    for i, type_info in enumerate(self.header_types):
      size = type_info["size"]
      if offset + size > len(self.byte_buffer):
        logger.warning(
          f"Payload too short for type {type_info['name']} at offset {offset} in linear parse seq {self.seq_num}"
        )
        parsed[f"unparsed_tail_{offset}"] = self.byte_buffer[offset:].hex()
        break

      data_bytes = self.byte_buffer[offset : offset + size]
      fmt = type_info["format"]
      name = type_info["name"]
      try:
        value = struct.unpack(fmt, data_bytes)[0]
      except struct.error as e:
        raise ValueError(f"Error unpacking {name} ({fmt}) at offset {offset}: {e}")

      if name == "x100U16TEMP":
        value /= 100.0
      if name in ["x10U16RWL", "x10U16MWL"]:
        value /= 10.0

      parsed[f"{name}_{i}"] = value
      offset += size

    result["parsed_data"] = parsed
    self.byte_buffer = self.byte_buffer[offset:]


class SparkParser:
  def __init__(self, data_bytes_list: List[bytes]) -> None:
    self.all_packets: List[SparkPacket] = []
    for data_bytes in data_bytes_list:
      try:
        self.all_packets.append(SparkPacket(data_bytes))
      except Exception as e:
        logger.error(f"Failed to parse packet from bytes: {data_bytes.hex()[:40]}... - {e}")

    self.sequences: Dict[int, List[SparkPacket]] = {}
    for packet in self.all_packets:
      self.sequences.setdefault(packet.seq_num, []).append(packet)

    for seq_num in self.sequences:
      self.sequences[seq_num].sort(key=lambda p: p.indicator)  # Headers before data

  def save_all_packets(self, filename: str = "spark_all_packets.json") -> None:
    with open(filename, "w") as f:
      json.dump([p.to_dict() for p in self.all_packets], f, indent=4)
    logger.info(f"All {len(self.all_packets)} packets parsed and saved to {filename}")

  def process_all_sequences(self) -> Dict[int, Any]:
    results: Dict[int, Any] = {}
    for seq_num, packets_in_seq in self.sequences.items():
      logger.info(f"\nProcessing Sequence: {seq_num}")
      if any(p.indicator == 136 for p in packets_in_seq):  # Check for header
        measurement_data = self._process_measurement_stream(packets_in_seq)
        results[seq_num] = measurement_data
        logger.info(f"Measurement stream for seq {seq_num} saved.")
      else:
        logger.info(
          f"No header packet in sequence {seq_num}, skipping measurement stream processing."
        )
        results[seq_num] = {"info": "No header packet found"}
    return results

  def _process_measurement_stream(self, packets: List[SparkPacket]) -> List[Dict[str, Any]]:
    seq_num = packets[0].seq_num
    results = []

    header_packets = sorted([p for p in packets if p.indicator == 136], key=lambda x: x.seq_num)
    data_packets = deque(
      sorted([p for p in packets if p.indicator == 131], key=lambda x: x.seq_num)
    )

    header_idx = 0
    while header_idx < len(header_packets):
      header_packet = header_packets[header_idx]
      header_type_codes = list(header_packet.parsed_payload["data"])
      header_type_names: List[str] = []
      for c in header_type_codes:
        t_info = TDCL_DATA_TYPE_MAP.get(c)
        if t_info:
          header_type_names.append(t_info["name"])

      if "U16MULT_H" in header_type_names:
        logger.info(f"Found U16MULT_H in seq {seq_num}")
        if not data_packets:
          logger.warning(f"Missing data packet for U16MULT_H count in seq {seq_num}")
          header_idx += 1
          continue

        try:
          count_packet = data_packets.popleft()
          count_payload = count_packet.parsed_payload["data"]
          reader = Reader(count_payload, little_endian=False)
          num_headers = reader.u16()
          logger.info(f"U16MULT_H indicates {num_headers} measurement blocks.")
        except Exception as e:
          logger.error(f"Error reading U16MULT_H count in seq {seq_num}: {e}")
          header_idx += 1
          continue

        grouped_results = []
        header_idx += 1  # Move past the U16MULT_H header

        for i in range(num_headers):
          if header_idx < len(header_packets):
            current_header = header_packets[header_idx]
            logger.info(f"Processing grouped header {i + 1}/{num_headers} in seq {seq_num}")
            block = MeasurementBlock(current_header, data_packets)
            grouped_results.append(block.parse())
            header_idx += 1
          else:
            logger.warning(
              f"Expected {num_headers} headers, but only found {header_idx} in seq {seq_num}"
            )
            break
        results.append({"type": "grouped", "count": num_headers, "blocks": grouped_results})
      else:
        logger.info(f"Processing standalone header in seq {seq_num}")
        block = MeasurementBlock(header_packet, data_packets)
        results.append({"type": "standalone", "block": block.parse()})
        header_idx += 1
    return results


def parse_single_spark_packet(data_bytes: bytes) -> Dict[str, Any]:
  """Parses a single Spark packet bytes and returns a dictionary."""
  try:
    packet = SparkPacket(data_bytes)
    return packet.to_dict()
  except Exception as e:
    logger.error(f"Failed to parse single packet: {e}", exc_info=True)
    return {"error": str(e), "hex_string": data_bytes.hex()}
