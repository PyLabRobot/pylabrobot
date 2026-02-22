import asyncio
import random
import socket
import struct
import time
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any, List, Optional, Tuple, Union

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.liquid_handling.standard import (
  SingleChannelAspiration,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  SingleChannelDispense,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  Drop,
  DropTipRack,
  Pickup,
  PickupTipRack,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
)


class ParameterTypes(Enum):
  Void = 0
  Int8Bit = 1
  Int16Bit = 2
  Int32Bit = 3
  UInt8Bit = 4
  UInt16Bit = 5
  UInt32Bit = 6
  String = 15
  UInt8Array = 22
  Bool = 23
  Int8Array = 24
  Int16Array = 25
  UInt16Array = 26
  Int32Array = 27
  UInt32Array = 28
  BoolArray = 29
  Structure = 30
  StructureArray = 31
  Enum = 32
  HcResult = 33
  StringArray = 34
  EnumArray = 35
  Int64Bit = 36
  UInt64Bit = 37
  Int64Array = 38
  UInt64Array = 39
  Real32Bit = 40
  Real64Bit = 41
  Real32Array = 42
  Real64Array = 43


class StructureWrapper:
  def __init__(self, data=None):
    self.members = []  # List of member data
    self.m_member_names = []  # List of member names

    if data is not None:
      offset = 0
      while offset < len(data):
        fragment_length = struct.unpack_from("H", data, offset + 2)[0]
        data_fragment = parse_data_fragment(data[offset:])

        if data_fragment["format"] == ParameterTypes.EnumArray:
          enumeration_wrapper_array = data_fragment["fragment_data"]
          # undefined?
          if enumeration_wrapper_array is None:
            enumeration_wrapper_array = ["???"]
          self.members.append(enumeration_wrapper_array)
        else:
          self.members.append(data_fragment["fragment_data"])

        self.m_member_names.append("")
        offset += fragment_length + 4

  def encode(self):
    encoded_data = b""
    for member in self.members:
      encoded_data += encode_data_fragment(member["value"], member["type"])
    return encoded_data


# TODO:
DataFragment = dict


def parse_data_fragment(data: bytes) -> DataFragment:
  padded_bit_field = 0x1
  parameter_type = ParameterTypes(struct.unpack_from("B", data)[0])
  flgas = struct.unpack_from("B", data, 1)[0]
  length = struct.unpack_from("H", data, 2)[0]
  is_padded = (flgas & padded_bit_field) == padded_bit_field

  if parameter_type == ParameterTypes.Int8Bit:
    fragment_data = struct.unpack_from("b", data, 4)[0]
  elif parameter_type == ParameterTypes.Int16Bit:
    fragment_data = struct.unpack_from("h", data, 4)[0]
  elif parameter_type == ParameterTypes.Int32Bit:
    fragment_data = struct.unpack_from("i", data, 4)[0]
  elif parameter_type == ParameterTypes.UInt8Bit:
    fragment_data = struct.unpack_from("B", data, 4)[0]
  elif parameter_type == ParameterTypes.UInt16Bit or parameter_type == ParameterTypes.HcResult:
    fragment_data = struct.unpack_from("H", data, 4)[0]
  elif parameter_type == ParameterTypes.UInt32Bit:
    fragment_data = struct.unpack_from("I", data, 4)[0]
  elif parameter_type == ParameterTypes.String:
    length_adj = length - 1 if is_padded else length
    if length_adj > 0:
      fragment_data = data[4 : 4 + length_adj - 1].decode("ascii")
    else:
      fragment_data = ""
  elif parameter_type == ParameterTypes.UInt8Array:
    length_adj = length - 1 if is_padded else length
    fragment_data = list(data[4 : 4 + length_adj])
  elif parameter_type == ParameterTypes.Bool:
    fragment_data = struct.unpack_from("?", data, 4)[0]
  elif parameter_type == ParameterTypes.Int8Array:
    length_adj = length - 1 if is_padded else length
    fragment_data = list(struct.unpack_from(f"{length_adj}b", data, 4))
  elif parameter_type == ParameterTypes.Int16Array:
    fragment_data = list(struct.unpack_from(f"{length // 2}h", data, 4))
  elif parameter_type == ParameterTypes.UInt16Array:
    fragment_data = list(struct.unpack_from(f"{length // 2}H", data, 4))
  elif parameter_type == ParameterTypes.Int32Array:
    fragment_data = list(struct.unpack_from(f"{length // 4}i", data, 4))
  elif parameter_type == ParameterTypes.UInt32Array:
    fragment_data = list(struct.unpack_from(f"{length // 4}I", data, 4))
  elif parameter_type == ParameterTypes.BoolArray:
    # new_types
    length_adj = length - 1 if is_padded else length
    fragment_data = [struct.unpack_from("?", data, 4 + i)[0] for i in range(length_adj)]
  elif parameter_type == ParameterTypes.Real32Bit:
    fragment_data = struct.unpack_from("f", data, 4)[0]
  elif parameter_type == ParameterTypes.Real64Bit:
    fragment_data = struct.unpack_from("d", data, 4)[0]
  elif parameter_type == ParameterTypes.Real32Array:
    fragment_data = list(struct.unpack_from(f"{length // 4}f", data, 4))
  elif parameter_type == ParameterTypes.Real64Array:
    fragment_data = list(struct.unpack_from(f"{length // 8}d", data, 4))
  elif parameter_type == ParameterTypes.Structure:
    struct_length = struct.unpack_from("H", data, 2)[0]
    struct_data = data[4 : 4 + struct_length]
    fragment_data = StructureWrapper(struct_data)
  elif parameter_type == ParameterTypes.StructureArray:
    struct_length = struct.unpack_from("H", data, 2)[0]
    struct_data = data[4 : 4 + struct_length]
    structure_wrappers = []
    current_offset = 0

    while current_offset < len(struct_data):
      frag_length = struct.unpack_from("H", struct_data, current_offset + 2)[0]
      fragment = parse_data_fragment(struct_data[current_offset:])
      structure_wrappers.append(fragment["fragment_data"])
      current_offset += frag_length + 4

    fragment_data = structure_wrappers
  elif parameter_type == ParameterTypes.Enum:
    # decode as 32-bit unsigned integer
    fragment_data = struct.unpack_from("I", data, 4)[0]
  elif parameter_type == ParameterTypes.EnumArray:
    fragment_data = list(struct.unpack_from(f"{length // 4}I", data, 4))
  elif parameter_type == ParameterTypes.Int64Array:
    fragment_data = list(struct.unpack_from(f"{length // 8}q", data, 4))
  else:
    raise ValueError(f"Unsupported parameter type: {parameter_type}")

  return {
    "format": parameter_type,
    "flags": flgas,
    "length": length + 4,  # total length includes the format, flags, and length fields
    "is_padded": is_padded,
    "fragment_data": fragment_data,
  }


def encode_data_fragment(obj: Any, parameter_type: ParameterTypes, padded=False) -> bytes:
  format = struct.pack("B", parameter_type.value)
  data = b""
  flags = 0

  if parameter_type == ParameterTypes.Int8Bit:
    data = struct.pack("b", obj)
  elif parameter_type == ParameterTypes.Int16Bit:
    data = struct.pack("h", obj)
  elif parameter_type == ParameterTypes.Int32Bit:
    data = struct.pack("i", obj)
  elif parameter_type == ParameterTypes.UInt8Bit:
    data = struct.pack("B", obj)
    padded = True
  elif parameter_type == ParameterTypes.UInt16Bit:
    data = struct.pack("H", obj)
  elif parameter_type == ParameterTypes.UInt32Bit:
    data = struct.pack("I", obj)
  elif parameter_type == ParameterTypes.String:
    data = obj.encode("ascii") + b"\x00"
  elif parameter_type == ParameterTypes.UInt8Array:
    data = bytes(obj)
  elif parameter_type == ParameterTypes.Bool:
    data = struct.pack("?", obj)
    padded = True
  elif parameter_type == ParameterTypes.Int8Array:
    data = struct.pack(f"{len(obj)}b", *obj)
  elif parameter_type == ParameterTypes.Int16Array:
    data = struct.pack(f"{len(obj)}h", *obj)
  elif parameter_type == ParameterTypes.UInt16Array:
    data = struct.pack(f"{len(obj)}H", *obj)
  elif parameter_type == ParameterTypes.Int32Array:
    data = struct.pack(f"{len(obj)}i", *obj)
  elif parameter_type == ParameterTypes.UInt32Array:
    data = struct.pack(f"{len(obj)}I", *obj)
  elif parameter_type == ParameterTypes.BoolArray:
    data = b"".join([struct.pack("?", b) for b in obj])
  elif parameter_type == ParameterTypes.Real32Bit:
    data = struct.pack("f", obj)
  elif parameter_type == ParameterTypes.Real64Bit:
    data = struct.pack("d", obj)
  elif parameter_type == ParameterTypes.Real32Array:
    data = struct.pack(f"{len(obj)}f", *obj)
  elif parameter_type == ParameterTypes.Real64Array:
    data = struct.pack(f"{len(obj)}d", *obj)
  elif parameter_type == ParameterTypes.Structure:
    struct_data = obj.encode()
    data = struct_data
  elif parameter_type == ParameterTypes.StructureArray:
    data = b"".join([encode_data_fragment(o, ParameterTypes.Structure) for o in obj])
  elif parameter_type == ParameterTypes.Enum:
    # encode as 32-bit unsigned integer
    data = struct.pack("I", obj)
  elif parameter_type == ParameterTypes.EnumArray:
    data = struct.pack(f"{len(obj)}I", *obj)
  else:
    raise ValueError(f"Unsupported parameter type: {parameter_type}")

  if padded:
    flags |= Prep.HoiPacket2.BitField.Padded
    data += b"\x00"

  length = len(data)

  return format + struct.pack("B", flags) + struct.pack("H", length) + data


class Prep(LiquidHandlerBackend):
  def __init__(self, host: str = "192.168.100.102", port: int = 2000):
    self.pipettor_source = Prep.HarpPacket.HarpAddress((0x0002, 0x0004, 0x0006))
    self.pipettor_destination = Prep.HarpPacket.HarpAddress((0xE000, 0x0001, 0x1000))

    self.source_address = Prep.HarpPacket.HarpAddress((0x0002, 0x0004, 0x0004))
    self.destination_address = Prep.HarpPacket.HarpAddress((0x0001, 0x0001, 0x1500))

    self._id = 0
    self.host = host
    self.port = port
    self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

  async def setup(self, smart: bool = False):
    self.socket.connect((self.host, self.port))
    self.socket.settimeout(30)

    await self.initialize(
      tip_drop_params=Prep.InitTipDropParameters(
        default_values=True,
        x_position=287.0,
        rolloff_distance=3,
        channel_parameters=[],
      ),
      smart=smart,
    )

    await super().setup()

  async def stop(self):
    self.socket.close()
    await super().stop()

  def _generate_id(self) -> int:
    """continuously generate unique ids 0 <= x <= 0xff."""
    self._id += 1
    return self._id % 0xFF

  def _assemble_command(
    self,
    command_id: int,
    parameters: List[Tuple[ParameterTypes]],
    harp_source: "Prep.HarpPacket.HarpAddress",
    harp_destination: "Prep.HarpPacket.HarpAddress",
    hoi_action: "Prep.HoiPacket2.Hoi2Action",
  ) -> bytes:
    hoi_packet = Prep.HoiPacket2(
      interface_id=1,
      action=hoi_action,
      action_id=command_id,
      version=0,
      data_fragments=[
        encode_data_fragment(value, parameter_type) for value, parameter_type in parameters
      ],
    )

    harp_packet = Prep.HarpPacket(
      source=harp_source,
      destination=harp_destination,
      sequence_number=self._generate_id(),
      reserved_1=0,
      protocol=Prep.HarpPacket.HarpTransportableProtocol.Hoi2,
      action=Prep.HarpPacket.Action.create(
        Prep.HarpPacket.ResponseRequired.Yes, Prep.HarpPacket.PayloadDescription.CommandRequest
      ),
      options=[],  # TODO: calculate this
      version=0,
      reserved_2=0,
      payload=hoi_packet.encode(),
    )

    ip_packet = Prep.IpPacket(
      protocol=Prep.IpPacket.TransportableProtocol.Harp2,
      version=(3, 0),
      options=None,
      payload=harp_packet.encode(),
    )

    return ip_packet.encode()

  def _decode_response(self, response: bytes):
    try:
      ip_packet = Prep.IpPacket.decode(response)
    except ValueError as e:
      raise ValueError(f"Failed to decode response: {e}")

    if ip_packet.protocol == Prep.IpPacket.TransportableProtocol.Harp2:
      harp_packet = Prep.HarpPacket.decode(ip_packet.payload)
    else:
      raise ValueError(f"protocol {ip_packet.protocol} not supported")

    if harp_packet.protocol != Prep.HarpPacket.HarpTransportableProtocol.Hoi2:
      raise ValueError(f"protocol {harp_packet.protocol} not supported")

    try:
      hoi_packet = Prep.HoiPacket2.decode(harp_packet.payload)
    except ValueError as e:
      raise ValueError(f"Failed to decode HoiPacket2: {e}")

    fragments = hoi_packet.data_fragments
    if len(fragments) > 0 and fragments[0]["format"] == ParameterTypes.HcResult:
      if fragments[0]["fragment_data"] != 0:
        raise ValueError(f"Command failed with error code {fragments[0]['fragment_data']}")

    return

  async def send_command(
    self,
    command_id: int,
    parameters: bytes,
    harp_source: "Prep.HarpPacket.HarpAddress",
    harp_destination: "Prep.HarpPacket.HarpAddress",
    hoi_action: "Prep.HoiPacket2.Hoi2Action" = None,
    timeout: Optional[float] = None,
  ) -> bytes:
    command = self._assemble_command(
      command_id=command_id,
      parameters=parameters,
      harp_source=harp_source,
      harp_destination=harp_destination,
      hoi_action=hoi_action or Prep.HoiPacket2.Hoi2Action.CommandRequest,
    )
    print("Sending command:", command.hex())
    self.socket.send(command)

    response = self.socket.recv(1024)
    print("Received response:", response.hex())

    self._decode_response(response)

  class IpPacket:
    FIXED_FORMAT = "H B B H"  # ushort, ubyte, ubyte, ushort
    FIXED_SIZE = struct.calcsize(FIXED_FORMAT)

    class IpPacketOption:
      class Option(IntEnum):
        Reserved = 0
        IncompatibleVersion = 1
        UnsupportedOption = 2
        HcResultIpOption = 3

      BASE_FORMAT = "BB"  # Fixed fields: option (1 byte), length (1 byte)
      BASE_SIZE = struct.calcsize(BASE_FORMAT)

      def __init__(self, option: Option, length: int, data: bytes = None):
        self.option = option
        self.length = length
        self.data = data or b""

      def encode(self) -> bytes:
        """Encode the IpPacketOption into bytes."""
        if len(self.data) != self.length:
          raise ValueError("data length does not match length")
        return struct.pack(self.BASE_FORMAT, self.option.value, self.length) + self.data

      @classmethod
      def decode(cls, data: bytes) -> "Prep.IpPacketOption":
        """Decode the IpPacketOption from bytes."""
        if len(data) < cls.BASE_SIZE:
          raise ValueError("Data too small to decode IpPacketOption")

        option, length = struct.unpack(cls.BASE_FORMAT, data[: cls.BASE_SIZE])
        data = data[cls.BASE_SIZE : cls.BASE_SIZE + length] if length > 0 else b""
        if len(data) != length:
          raise ValueError("data length does not match length in header")
        return cls(Prep.IpPacket.IpPacketOption.Option(option), length, data)

      def __repr__(self):
        return f"IpPacketOption(option={self.option}, length={self.length}, data={self.data})"

    class TransportableProtocol(IntEnum):
      None_ = 0
      Xml = 1
      Bz = 4
      Ml600 = 5
      Harp2 = 6
      Connection = 7
      Serial = 8
      Can = 9
      MultiSerial = 10
      Last = 11
      Invalid = 255

    def __init__(
      self,
      protocol: TransportableProtocol,
      version: Tuple[int, int],
      options: Optional[List[IpPacketOption]],
      payload: bytes,
    ):
      self.protocol = protocol
      self.version = version
      self.options = options
      self.payload = payload

    @property
    def size(self) -> int:
      # exclude size field (ushort, 2 bytes)
      return self.FIXED_SIZE + (self.options_length or 0) + len(self.payload or b"") - 2

    @property
    def options_length(self) -> int:
      return sum(option.length for option in self.options) if self.options is not None else 0

    @classmethod
    def decode(cls, data: bytes) -> "Prep.IpPacket":
      """Decode an IpPacket from raw bytes."""
      if len(data) < cls.FIXED_SIZE:
        raise ValueError(
          f"Data is too small to decode (expected at least {cls.FIXED_SIZE} bytes, got {len(data)})"
        )

      # Unpack the fixed fields
      size, protocol, version_byte, options_length = struct.unpack(
        cls.FIXED_FORMAT, data[: cls.FIXED_SIZE]
      )
      version = (version_byte & 240) >> 4, version_byte & 15

      # Decode options and payload
      offset = cls.FIXED_SIZE
      options = None
      if options_length > 0:
        if len(data) < offset + options_length:
          raise ValueError("Data too small to contain options")
        options = data[offset : offset + options_length]
        offset += options_length

      payload = None
      if offset < len(data):
        payload = data[offset:]

      if not len(data) - 2 == size:
        raise ValueError("Packet size does not match size field")

      return cls(Prep.IpPacket.TransportableProtocol(protocol), version, options, payload)

    def encode(self) -> bytes:
      """Encode the IpPacket into bytes."""
      version_byte = (self.version[0] << 4) | self.version[1]
      header = struct.pack(
        self.FIXED_FORMAT, self.size, self.protocol, version_byte, self.options_length
      )
      options = self.options or b""
      return header + options + (self.payload or b"")

  class HarpPacket:
    class HarpAddress:
      FORMAT = "3H"  # 3 unsigned shorts
      SIZE = struct.calcsize(FORMAT)

      def __init__(self, address: Tuple[int, int, int]):
        self.address = address

      def encode(self):
        return struct.pack(self.FORMAT, *self.address)

      @classmethod
      def decode(cls, data):
        return cls(struct.unpack(cls.FORMAT, data[: cls.SIZE]))

      def __str__(self):
        return ".".join(hex(byte) for byte in self.address)

      def __eq__(self, other: "Prep.HarpPacket.HarpAddress") -> bool:
        return self.address == other.address

    class Action:
      FORMAT = "B"  # 1 byte
      SIZE = struct.calcsize(FORMAT)

      def __init__(self, reserved):
        self.reserved = reserved

      @property
      def response_required(self):
        return (self.reserved & 16) >> 4

      @property
      def payload_description(self):
        return self.reserved & 15

      @staticmethod
      def create(response_required, payload_description):
        reserved = (response_required << 4) | payload_description
        # TODO: why is this named reserved?
        return Prep.HarpPacket.Action(reserved)

      def encode(self):
        return struct.pack(self.FORMAT, self.reserved)

      @classmethod
      def decode(cls, data):
        (reserved,) = struct.unpack(cls.FORMAT, data[: cls.SIZE])
        return cls(reserved)

      def __eq__(self, other: "Prep.HarpPacket.Action") -> bool:
        return self.reserved == other.reserved

    BASE_FORMAT = (
      HarpAddress.FORMAT * 2 + "BB" + "B" + Action.FORMAT + "H" + "H" + "B" + "B"
    )  # source, destination, sequence_number, reserved_1, protocol, action, length, options_length, version, reserved_2
    BASE_SIZE = struct.calcsize(BASE_FORMAT)

    def __init__(
      self,
      source,
      destination,
      sequence_number,
      reserved_1,
      protocol,
      action,
      options: Optional[List["Prep.HarpPacketOption"]],
      version,
      reserved_2,
      payload: bytes,
    ):
      self.source = source
      self.destination = destination
      self.sequence_number = sequence_number
      self.reserved_1 = reserved_1
      self.protocol = protocol
      self.action = action
      self.options = options or []
      self.version = version
      self.reserved_2 = reserved_2

      # not part of the Hamilton implementation, but it is added ad-hoc. We just store it as an
      # attribute, similar to IpPacket.payload.
      self.payload = payload

    @property
    def length(self) -> int:
      return self.BASE_SIZE + self.options_length + len(self.payload)

    @property
    def options_length(self) -> int:
      return sum(option.length for option in self.options)

    def encode(self):
      header = struct.pack(
        self.BASE_FORMAT,
        *self.source.address,
        *self.destination.address,
        self.sequence_number,
        self.reserved_1,
        self.protocol.value,
        self.action.reserved,
        self.length,
        self.options_length,
        self.version,
        self.reserved_2,
      )
      options = b"".join(option.encode() for option in self.options)
      return header + options + self.payload

    @classmethod
    def decode(cls, data):
      if len(data) < cls.BASE_SIZE:
        raise ValueError(f"Data too small to decode (expected at least {cls.BASE_SIZE} bytes)")

      unpacked_data = struct.unpack(cls.BASE_FORMAT, data[: cls.BASE_SIZE])
      source = Prep.HarpPacket.HarpAddress(unpacked_data[:3])
      destination = Prep.HarpPacket.HarpAddress(unpacked_data[3:6])
      sequence_number = unpacked_data[6]
      reserved_1 = unpacked_data[7]
      protocol = Prep.HarpPacket.HarpTransportableProtocol(unpacked_data[8])
      action = Prep.HarpPacket.Action(unpacked_data[9])
      length = unpacked_data[10]
      options_length = unpacked_data[11]
      version = unpacked_data[12]
      reserved_2 = unpacked_data[13]

      offset = cls.BASE_SIZE
      options = []
      for _ in range(options_length):
        option = Prep.HarpPacket.HarpPacketOption.decode(data[offset:])
        options.append(option)
        offset += Prep.HarpPacket.HarpPacketOption.BASE_SIZE + option.length

      payload = data[offset:]
      if not cls.BASE_SIZE + options_length + len(payload) == length:
        raise ValueError("Payload length does not match length")

      return cls(
        source=source,
        destination=destination,
        sequence_number=sequence_number,
        reserved_1=reserved_1,
        protocol=protocol,
        action=action,
        options=options,
        version=version,
        reserved_2=reserved_2,
        payload=payload,
      )

    class HarpTransportableProtocol(IntEnum):
      Hoi2 = 2
      Registration2 = 3
      Lst = 4
      Undefined = 255

    class PayloadDescription(IntEnum):
      StatusRequest = 0
      StatusResponse = 1
      StatusException = 2
      CommandRequest = 3
      CommandResponse = 4
      CommandException = 5
      CommandAck = 6
      UpStreamSystemEvent = 7
      DownStreamSystemEvent = 8
      Event = 9
      InvalidActionResponse = 10
      StatusWarning = 11
      CommandWarning = 12

    class ResponseRequired(IntEnum):
      No = 0
      Yes = 1

    class HarpPacketOption:
      BASE_FORMAT = "BB"  # option (1 byte), length (1 byte)
      BASE_SIZE = struct.calcsize(BASE_FORMAT)

      class Option(IntEnum):
        Reserved = 0
        RoutingError = 1
        IncompatibleVersion = 2
        UnsupportedOptions = 3

      def __init__(self, option: Option, length: int, data=None):
        self.option = option
        self.length = length
        self.data = data or b""

      def encode(self):
        return struct.pack(self.BASE_FORMAT, self.option, self.length) + self.data

      @classmethod
      def decode(cls, data):
        base_data = data[: cls.BASE_SIZE]
        option, length = struct.unpack(cls.BASE_FORMAT, base_data)
        data = data[cls.BASE_SIZE : cls.BASE_SIZE + length] if length > 0 else b""
        return cls(option, length, data)

  class HoiPacket2:
    BASE_FORMAT = "B B H BB"  # interface_id (1 byte), action (1 byte), action_id (2 bytes), version (1 byte), number_of_fragments (1 byte)
    BASE_SIZE = struct.calcsize(BASE_FORMAT)

    def __init__(
      self,
      interface_id: int,
      action: "Prep.HoiPacket2.Hoi2Action",
      action_id: int,
      version: int,
      data_fragments: List[bytes],
    ):
      self.interface_id = interface_id
      self.action = action
      self.action_id = action_id
      self.version = version
      # for Hamilton, this is a list of `DataFragment`s. But, it is easier to just store the encoded bytes.
      self.data_fragments = data_fragments

    @property
    def number_of_fragments(self) -> int:
      return len(self.data_fragments)

    def encode(self):
      header = struct.pack(
        self.BASE_FORMAT,
        self.interface_id,
        self.action.value if isinstance(self.action, Prep.HoiPacket2.Hoi2Action) else self.action,
        self.action_id,
        self.version,
        self.number_of_fragments,
      )
      return header + b"".join(self.data_fragments)

    @classmethod
    def decode(cls, data):
      if len(data) < cls.BASE_SIZE:
        raise ValueError(
          f"Data too small to decode HoiPacket2 (expected at least {cls.BASE_SIZE} bytes)"
        )

      unpacked = struct.unpack(cls.BASE_FORMAT, data[: cls.BASE_SIZE])
      interface_id, action, action_id, version, number_of_fragments = unpacked

      offset = cls.BASE_SIZE
      fragments = []
      while offset < len(data):
        fragment = parse_data_fragment(data[offset:])
        length = fragment["length"]
        fragments.append(fragment)
        offset += length
      assert len(fragments) == number_of_fragments, "Number of fragments does not match header"

      return cls(
        interface_id=interface_id,
        action=Prep.HoiPacket2.Hoi2Action(action)
        if action <= max(Prep.HoiPacket2.Hoi2Action)
        else action,
        action_id=action_id,
        version=version,
        data_fragments=fragments,
      )

    def __repr__(self):
      return (
        f"HoiPacket2(interface_id={self.interface_id}, action={self.action}, "
        f"action_id={self.action_id}, version={self.version}, "
        f"number_of_fragments={self.number_of_fragments}, data_fragments={self.data_fragments})"
      )

    class BitField(IntEnum):
      None_ = 0
      Padded = 1
      Unused2 = 2
      Unused3 = 4
      Unused4 = 8
      Unused5 = 65536
      Unused6 = Padded
      Unused7 = Unused2

    class Hoi2Action(IntEnum):
      StatusRequest = 0
      StatusResponse = 1
      StatusException = 2
      CommandRequest = 3
      CommandResponse = 4
      CommandException = 5
      CommandAck = 6
      UpStreamSystemEvent = 7
      DownStreamSystemEvent = 8
      Event = 9
      InvalidActionResponse = 10
      StatusWarning = 11
      CommandWarning = 12

    class Hoi2Eventaction_id(IntEnum):
      Registration = 1
      Deregistration = 2
      Event = 3

    class Hoi2EventAction(IntEnum):
      EventRegisterDeregisterRequest = 1
      EventRegisterDeregisterResponse = 2
      EventNotification = 3

    class DataFragment:
      FORMAT = "I"  # Example format for individual fragments
      SIZE = struct.calcsize(FORMAT)

      def __init__(self, value):
        self.value = value

      def encode(self):
        return struct.pack(self.FORMAT, self.value)

      @classmethod
      def decode(cls, data):
        (value,) = struct.unpack(cls.FORMAT, data[: cls.SIZE])
        return cls(value)

  class TadmRecordingModes(IntEnum):
    NoRecording = 0
    Errors = 1
    All = 2

  class PressureMode(IntEnum):
    OverPressure = 0
    UnderPressure = 1

  class LLDStatus(IntEnum):
    NotDetected = 0
    Detected = 1
    Disabled = 2

  class ChannelType(IntEnum):
    NoChannel = 0
    UnknownChannelType = 1
    Single1000uLChannel = 2
    MPH8x1000uLChannel = 3

  class ChannelIndex(IntEnum):
    InvalidIndex = 0
    FrontChannel = 1
    RearChannel = 2
    MPHChannel = 3

  class ChannelAxis(IntEnum):
    YAxis = 0
    ZAxis = 1
    SqueezeAxis = 2
    DispenserAxis = 3

  class MPHChannelID(IntEnum):
    MPHChannel1 = 1
    MPHChannel2 = 2
    MPHChannel3 = 3
    MPHChannel4 = 4
    MPHChannel5 = 5
    MPHChannel6 = 6
    MPHChannel7 = 7
    MPHChannel8 = 8

  class TipDropType(IntEnum):
    FixedHeight = 0
    Stall = 1
    CLLDSeek = 2

  class ZTravelMode(IntEnum):
    ZLimitTraverse = 0
    AdjustableTraverse = 1
    CalculatedTraverse = 2
    TerrainFollow = 3

  class XYTravelMode(IntEnum):
    Direct = 0
    XFirst = 1
    YFirst = 2
    Path = 3

  class VolumeType(IntEnum):
    TransportAir = 0
    StopBack = 1
    Liquid = 2
    Blowout = 3
    InitialVolume = 4
    ErrorVolume = 5

  # class TipTypes(IntEnum):
  #   UNKNOWN = 0
  #   STANDARD = 1
  #   FILTER = 2
  #   NEEDLE = 3

  @dataclass
  class SeekParameters:
    x_start: float  # real 32 bit
    y_start: float  # real 32 bit
    z_start: float  # real 32 bit
    distance: float  # real 32 bit
    expected_position: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.x_start, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.y_start, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_start, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.distance, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.expected_position, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class XYZCoord:
    default_values: bool  # bool
    x_position: float  # real 32 bit
    y_position: float  # real 32 bit
    z_position: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.x_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.y_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_position, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class XYCoord:
    default_values: bool  # bool
    x_position: float  # real 32 bit
    y_position: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.x_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.y_position, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class ChannelYZMoveParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    y_position: float  # real 32 bit
    z_position: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.y_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_position, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class GantryMoveXYZParameters:
    default_values: bool  # bool
    gantry_x_position: float  # real 32 bit
    axis_parameters: list["Prep.ChannelYZMoveParameters"]  # array of ChannelYZMoveParameters

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.gantry_x_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.axis_parameters, ParameterTypes.StructureArray)
      return out

  @dataclass
  class PlateDimensions:
    default_values: bool  # bool
    length: float  # real 32 bit
    width: float  # real 32 bit
    height: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.length, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.width, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.height, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class TipDefinition:
    default_values: bool  # bool
    id: int  # byte (UInt8Bit)
    volume: float  # real 32 bit
    length: float  # real 32 bit
    tip_type: "Prep.TipTypes"  # enum
    has_filter: bool  # bool
    is_needle: bool  # bool
    is_tool: bool  # bool
    label: str  # string

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.id, ParameterTypes.UInt8Bit)
      out += encode_data_fragment(self.volume, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.length, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.tip_type, ParameterTypes.Enum)
      out += encode_data_fragment(self.has_filter, ParameterTypes.Bool)
      out += encode_data_fragment(self.is_needle, ParameterTypes.Bool)
      out += encode_data_fragment(self.is_tool, ParameterTypes.Bool)
      out += encode_data_fragment(self.label, ParameterTypes.String)
      return out

  @dataclass
  class TipPickupParameters:
    default_values: bool  # bool
    volume: float  # real 32 bit
    length: float  # real 32 bit
    tip_type: "Prep.TipTypes"  # enum
    has_filter: bool  # bool
    is_needle: bool  # bool
    is_tool: bool  # bool

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.volume, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.length, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.tip_type, ParameterTypes.Enum)
      out += encode_data_fragment(self.has_filter, ParameterTypes.Bool)
      out += encode_data_fragment(self.is_needle, ParameterTypes.Bool)
      out += encode_data_fragment(self.is_tool, ParameterTypes.Bool)
      return out

  @dataclass
  class AspirateParameters:
    default_values: bool  # bool
    x_position: float  # real 32 bit
    y_position: float  # real 32 bit
    prewet_volume: float  # real 32 bit
    blowout_volume: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.x_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.y_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.prewet_volume, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.blowout_volume, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class DispenseParameters:
    default_values: bool  # bool
    x_position: float  # real 32 bit
    y_position: float  # real 32 bit
    stop_back_volume: float  # real 32 bit
    cutoff_speed: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.x_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.y_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.stop_back_volume, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.cutoff_speed, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class CommonParameters:
    default_values: bool  # bool
    empty: bool  # bool
    z_minimum: float  # real 32 bit
    z_final: float  # real 32 bit
    z_liquid_exit_speed: float  # real 32 bit
    liquid_volume: float  # real 32 bit
    liquid_speed: float  # real 32 bit
    transport_air_volume: float  # real 32 bit
    tube_radius: float  # real 32 bit
    cone_height: float  # real 32 bit
    cone_bottom_radius: float  # real 32 bit
    settling_time: float  # real 32 bit
    additional_probes: int  # uint 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.empty, ParameterTypes.Bool)
      out += encode_data_fragment(self.z_minimum, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_final, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_liquid_exit_speed, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.liquid_volume, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.liquid_speed, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.transport_air_volume, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.tube_radius, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.cone_height, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.cone_bottom_radius, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.settling_time, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.additional_probes, ParameterTypes.UInt32Bit)
      return out

  @dataclass
  class NoLldParameters:
    default_values: bool  # bool
    z_fluid: float  # real 32 bit
    z_air: float  # real 32 bit
    bottom_search: bool  # bool
    z_bottom_search_offset: float  # real 32 bit
    z_bottom_offset: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.z_fluid, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_air, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.bottom_search, ParameterTypes.Bool)
      out += encode_data_fragment(self.z_bottom_search_offset, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_bottom_offset, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class LldParameters:
    default_values: bool  # bool
    z_seek: float  # real 32 bit
    z_seek_speed: float  # real 32 bit
    z_submerge: float  # real 32 bit
    z_out_of_liquid: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.z_seek, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_seek_speed, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_submerge, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_out_of_liquid, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class CLldParameters:
    default_values: bool  # bool
    sensitivity: "Prep.LldSensitivities"  # enum
    clot_check_enable: bool  # bool
    z_clot_check: float  # real 32 bit
    detect_mode: "Prep.DetectModes"  # enum

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.sensitivity, ParameterTypes.Enum)
      out += encode_data_fragment(self.clot_check_enable, ParameterTypes.Bool)
      out += encode_data_fragment(self.z_clot_check, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.detect_mode, ParameterTypes.Enum)
      return out

  @dataclass
  class PLldParameters:
    default_values: bool  # bool
    sensitivity: "Prep.LldSensitivities"  # enum
    dispenser_seek_speed: float  # real 32 bit
    lld_height_difference: float  # real 32 bit
    detect_mode: "Prep.DetectModes"  # enum

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.sensitivity, ParameterTypes.Enum)
      out += encode_data_fragment(self.dispenser_seek_speed, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.lld_height_difference, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.detect_mode, ParameterTypes.Enum)
      return out

  @dataclass
  class TadmReturnParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    entries: int  # uint 32 bit
    error: bool  # bool
    data: list[int]  # array of short (16-bit signed)

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.entries, ParameterTypes.UInt32Bit)
      out += encode_data_fragment(self.error, ParameterTypes.Bool)
      out += encode_data_fragment(self.data, ParameterTypes.Int16Array)
      return out

  @dataclass
  class TadmParameters:
    default_values: bool  # bool
    limit_curve_index: int  # ushort
    recording_mode: "Prep.TadmRecordingModes"  # enum

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.limit_curve_index, ParameterTypes.UInt16Bit)
      out += encode_data_fragment(self.recording_mode, ParameterTypes.Enum)
      return out

    @classmethod
    def default(cls) -> "Prep.TadmParameters":
      return cls(
        default_values=True,
        limit_curve_index=0,
        recording_mode=Prep.TadmRecordingModes.Errors,
      )

  @dataclass
  class AspirateMonitoringParameters:
    default_values: bool  # bool
    c_lld_enable: bool  # bool
    p_lld_enable: bool  # bool
    minimum_differential: int  # ushort
    maximum_differential: int  # ushort
    clot_threshold: int  # ushort

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.c_lld_enable, ParameterTypes.Bool)
      out += encode_data_fragment(self.p_lld_enable, ParameterTypes.Bool)
      out += encode_data_fragment(self.minimum_differential, ParameterTypes.UInt16Bit)
      out += encode_data_fragment(self.maximum_differential, ParameterTypes.UInt16Bit)
      out += encode_data_fragment(self.clot_threshold, ParameterTypes.UInt16Bit)
      return out

    @classmethod
    def default(cls) -> "Prep.AspirateMonitoringParameters":
      return cls(
        default_values=True,
        c_lld_enable=False,
        p_lld_enable=False,
        minimum_differential=30,
        maximum_differential=30,
        clot_threshold=20,
      )

  @dataclass
  class MixParameters:
    default_values: bool  # bool
    z_offset: float  # real 32 bit
    volume: float  # real 32 bit
    cycles: int  # byte (UInt8Bit)
    speed: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.z_offset, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.volume, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.cycles, ParameterTypes.UInt8Bit, padded=True)
      out += encode_data_fragment(self.speed, ParameterTypes.Real32Bit)
      return out

    @classmethod
    def default(cls) -> "Prep.MixParameters":
      return cls(
        default_values=True,
        z_offset=0.0,
        volume=0.0,
        cycles=0,
        speed=250.0,
      )

  @dataclass
  class AdcParameters:
    default_values: bool  # bool
    errors: bool  # bool
    maximum_volume: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.errors, ParameterTypes.Bool)
      out += encode_data_fragment(self.maximum_volume, ParameterTypes.Real32Bit)
      return out

    @classmethod
    def default(cls) -> "Prep.AdcParameters":
      return cls(
        default_values=True,
        errors=True,
        maximum_volume=4.5,
      )

  @dataclass
  class ChannelXYZPositionParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    position_x: float  # real 32 bit
    position_y: float  # real 32 bit
    position_z: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.position_x, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.position_y, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.position_z, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class PressureReturnParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    pressure: int  # ushort

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.pressure, ParameterTypes.UInt16Bit)
      return out

  @dataclass
  class LiquidHeightReturnParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    c_lld_detected: bool  # bool
    c_lld_liquid_height: float  # real 32 bit
    p_lld_detected: bool  # bool
    p_lld_liquid_height: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.c_lld_detected, ParameterTypes.Bool)
      out += encode_data_fragment(self.c_lld_liquid_height, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.p_lld_detected, ParameterTypes.Bool)
      out += encode_data_fragment(self.p_lld_liquid_height, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class DispenserVolumeReturnParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    volume: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.volume, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class PotentiometerParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    gain: int  # byte (UInt8Bit)
    offset: int  # byte (UInt8Bit)

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.gain, ParameterTypes.UInt8Bit)
      out += encode_data_fragment(self.offset, ParameterTypes.UInt8Bit)
      return out

  @dataclass
  class YLLDSeekParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    start_position_x: float  # real 32 bit
    start_position_y: float  # real 32 bit
    start_position_z: float  # real 32 bit
    seek_position_y: float  # real 32 bit
    seek_velocity_y: float  # real 32 bit
    lld_sensitivity: "Prep.LldSensitivities"  # enum
    detect_mode: "Prep.DetectModes"  # enum

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.start_position_x, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.start_position_y, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.start_position_z, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.seek_position_y, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.seek_velocity_y, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.lld_sensitivity, ParameterTypes.Enum)
      out += encode_data_fragment(self.detect_mode, ParameterTypes.Enum)
      return out

  @dataclass
  class ChannelSeekParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    seek_position_x: float  # real 32 bit
    seek_position_y: float  # real 32 bit
    seek_height: float  # real 32 bit
    min_seek_height: float  # real 32 bit
    final_position_z: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.seek_position_x, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.seek_position_y, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.seek_height, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.min_seek_height, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.final_position_z, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class LLDChannelSeekParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    seek_position_x: float  # real 32 bit
    seek_position_y: float  # real 32 bit
    seek_velocity_z: float  # real 32 bit
    seek_height: float  # real 32 bit
    min_seek_height: float  # real 32 bit
    final_position_z: float  # real 32 bit
    lld_sensitivity: "Prep.LldSensitivities"  # enum
    detect_mode: "Prep.DetectModes"  # enum

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.seek_position_x, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.seek_position_y, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.seek_velocity_z, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.seek_height, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.min_seek_height, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.final_position_z, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.lld_sensitivity, ParameterTypes.Enum)
      out += encode_data_fragment(self.detect_mode, ParameterTypes.Enum)
      return out

  @dataclass
  class SeekResultParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    detected: bool  # bool
    position: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.detected, ParameterTypes.Bool)
      out += encode_data_fragment(self.position, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class ChannelCounterParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    tip_pickup_counter: int  # uint 32 bit
    tip_eject_counter: int  # uint 32 bit
    aspirate_counter: int  # uint 32 bit
    dispense_counter: int  # uint 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.tip_pickup_counter, ParameterTypes.UInt32Bit)
      out += encode_data_fragment(self.tip_eject_counter, ParameterTypes.UInt32Bit)
      out += encode_data_fragment(self.aspirate_counter, ParameterTypes.UInt32Bit)
      out += encode_data_fragment(self.dispense_counter, ParameterTypes.UInt32Bit)
      return out

  @dataclass
  class ChannelCalibrationParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    dispenser_return_steps: int  # uint 32 bit
    squeeze_position: float  # real 32 bit
    z_touchoff: float  # real 32 bit
    z_tip_height: float  # real 32 bit
    pressure_monitoring_shift: int  # uint 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.dispenser_return_steps, ParameterTypes.UInt32Bit)
      out += encode_data_fragment(self.squeeze_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_touchoff, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_tip_height, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.pressure_monitoring_shift, ParameterTypes.UInt32Bit)
      return out

  @dataclass
  class LeakCheckSimpleParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    time: float  # real 32 bit
    high_pressure: bool  # bool

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.time, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.high_pressure, ParameterTypes.Bool)
      return out

  @dataclass
  class LeakCheckParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    start_position_x: float  # real 32 bit
    start_position_y: float  # real 32 bit
    start_position_z: float  # real 32 bit
    seek_distance_y: float  # real 32 bit
    pre_load_distance_y: float  # real 32 bit
    final_z: float  # real 32 bit
    tip_definition_id: int  # byte (UInt8Bit)
    test_time: float  # real 32 bit
    high_pressure: bool  # bool

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.start_position_x, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.start_position_y, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.start_position_z, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.seek_distance_y, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.pre_load_distance_y, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.final_z, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.tip_definition_id, ParameterTypes.UInt8Bit)
      out += encode_data_fragment(self.test_time, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.high_pressure, ParameterTypes.Bool)
      return out

  @dataclass
  class ChannelDriveStatus:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    y_axis_drive_status: "Prep.DriveStatus"  # struct
    z_axis_drive_status: "Prep.DriveStatus"  # struct
    dispenser_drive_status: "Prep.DriveStatus"  # struct
    squeeze_drive_status: "Prep.DriveStatus"  # struct

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.y_axis_drive_status, ParameterTypes.Structure)
      out += encode_data_fragment(self.z_axis_drive_status, ParameterTypes.Structure)
      out += encode_data_fragment(self.dispenser_drive_status, ParameterTypes.Structure)
      out += encode_data_fragment(self.squeeze_drive_status, ParameterTypes.Structure)
      return out

  @dataclass
  class AspirateParametersNoLldAndMonitoring:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    aspirate: "Prep.AspirateParameters"  # struct
    common: "Prep.CommonParameters"  # struct
    no_lld: "Prep.NoLldParameters"  # struct
    mix: "Prep.MixParameters"  # struct
    adc: "Prep.AdcParameters"  # struct
    aspirate_monitoring: "Prep.AspirateMonitoringParameters"  # struct

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.aspirate, ParameterTypes.Structure)
      out += encode_data_fragment(self.common, ParameterTypes.Structure)
      out += encode_data_fragment(self.no_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.mix, ParameterTypes.Structure)
      out += encode_data_fragment(self.adc, ParameterTypes.Structure)
      out += encode_data_fragment(self.aspirate_monitoring, ParameterTypes.Structure)
      return out

  @dataclass
  class AspirateParametersNoLldAndTadm:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    aspirate: "Prep.AspirateParameters"  # struct
    common: "Prep.CommonParameters"  # struct
    no_lld: "Prep.NoLldParameters"  # struct
    mix: "Prep.MixParameters"  # struct
    adc: "Prep.AdcParameters"  # struct
    tadm: "Prep.TadmParameters"  # struct

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.aspirate, ParameterTypes.Structure)
      out += encode_data_fragment(self.common, ParameterTypes.Structure)
      out += encode_data_fragment(self.no_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.mix, ParameterTypes.Structure)
      out += encode_data_fragment(self.adc, ParameterTypes.Structure)
      out += encode_data_fragment(self.tadm, ParameterTypes.Structure)
      return out

  @dataclass
  class AspirateParametersLldAndMonitoring:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    aspirate: "Prep.AspirateParameters"  # struct
    common: "Prep.CommonParameters"  # struct
    lld: "Prep.LldParameters"  # struct
    p_lld: "Prep.PLldParameters"  # struct
    c_lld: "Prep.CLldParameters"  # struct
    mix: "Prep.MixParameters"  # struct
    aspirate_monitoring: "Prep.AspirateMonitoringParameters"  # struct
    adc: "Prep.AdcParameters"  # struct

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.aspirate, ParameterTypes.Structure)
      out += encode_data_fragment(self.common, ParameterTypes.Structure)
      out += encode_data_fragment(self.lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.p_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.c_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.mix, ParameterTypes.Structure)
      out += encode_data_fragment(self.aspirate_monitoring, ParameterTypes.Structure)
      out += encode_data_fragment(self.adc, ParameterTypes.Structure)
      return out

  @dataclass
  class AspirateParametersLldAndTadm:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    aspirate: "Prep.AspirateParameters"  # struct
    common: "Prep.CommonParameters"  # struct
    lld: "Prep.LldParameters"  # struct
    p_lld: "Prep.PLldParameters"  # struct
    c_lld: "Prep.CLldParameters"  # struct
    mix: "Prep.MixParameters"  # struct
    tadm: "Prep.TadmParameters"  # struct
    adc: "Prep.AdcParameters"  # struct

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.aspirate, ParameterTypes.Structure)
      out += encode_data_fragment(self.common, ParameterTypes.Structure)
      out += encode_data_fragment(self.lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.p_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.c_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.mix, ParameterTypes.Structure)
      out += encode_data_fragment(self.tadm, ParameterTypes.Structure)
      out += encode_data_fragment(self.adc, ParameterTypes.Structure)
      return out

  @dataclass
  class DispenseParametersNoLld:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    dispense: "Prep.DispenseParameters"  # struct
    common: "Prep.CommonParameters"  # struct
    no_lld: "Prep.NoLldParameters"  # struct
    mix: "Prep.MixParameters"  # struct
    adc: "Prep.AdcParameters"  # struct
    tadm: "Prep.TadmParameters"  # struct

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.dispense, ParameterTypes.Structure)
      out += encode_data_fragment(self.common, ParameterTypes.Structure)
      out += encode_data_fragment(self.no_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.mix, ParameterTypes.Structure)
      out += encode_data_fragment(self.adc, ParameterTypes.Structure)
      out += encode_data_fragment(self.tadm, ParameterTypes.Structure)
      return out

  @dataclass
  class DispenseParametersLld:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    dispense: "Prep.DispenseParameters"  # struct
    common: "Prep.CommonParameters"  # struct
    lld: "Prep.LldParameters"  # struct
    c_lld: "Prep.CLldParameters"  # struct
    mix: "Prep.MixParameters"  # struct
    adc: "Prep.AdcParameters"  # struct
    tadm: "Prep.TadmParameters"  # struct

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.dispense, ParameterTypes.Structure)
      out += encode_data_fragment(self.common, ParameterTypes.Structure)
      out += encode_data_fragment(self.lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.c_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.mix, ParameterTypes.Structure)
      out += encode_data_fragment(self.adc, ParameterTypes.Structure)
      out += encode_data_fragment(self.tadm, ParameterTypes.Structure)
      return out

  @dataclass
  class DropTipParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    y_position: float  # real 32 bit
    z_seek: float  # real 32 bit
    z_tip: float  # real 32 bit
    z_final: float  # real 32 bit
    z_seek_speed: float  # real 32 bit
    drop_type: "Prep.TipDropType"  # enum

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.y_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_seek, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_tip, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_final, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_seek_speed, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.drop_type, ParameterTypes.Enum)
      return out

  @dataclass
  class InitTipDropParameters:
    default_values: bool  # bool
    x_position: float  # real 32 bit
    rolloff_distance: float  # real 32 bit
    channel_parameters: list["Prep.DropTipParameters"]  # array of DropTipParameters

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.x_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.rolloff_distance, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.channel_parameters, ParameterTypes.StructureArray)
      return out

  @dataclass
  class DispenseInitToWasteParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    x_position: float  # real 32 bit
    y_position: float  # real 32 bit
    z_position: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.x_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.y_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_position, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class MoveAxisAbsoluteParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    axis: "Prep.ChannelAxis"  # enum
    position: float  # real 32 bit
    delay: int  # uint 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.axis, ParameterTypes.Enum)
      out += encode_data_fragment(self.position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.delay, ParameterTypes.UInt32Bit)
      return out

  @dataclass
  class MoveAxisRelativeParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    axis: "Prep.ChannelAxis"  # enum
    distance: float  # real 32 bit
    delay: int  # uint 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.axis, ParameterTypes.Enum)
      out += encode_data_fragment(self.distance, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.delay, ParameterTypes.UInt32Bit)
      return out

  @dataclass
  class LimitCurveEntry:
    default_values: bool  # bool
    sample: int  # ushort (UInt16Bit)
    pressure: int  # short (Int16)

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.sample, ParameterTypes.UInt16Bit)
      out += encode_data_fragment(self.pressure, ParameterTypes.Int16Bit)
      return out

  @dataclass
  class TipPositionParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    x_position: float  # real 32 bit
    y_position: float  # real 32 bit
    z_position: float  # real 32 bit
    z_seek: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.x_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.y_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_seek, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class TipDropParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    x_position: float  # real 32 bit
    y_position: float  # real 32 bit
    z_position: float  # real 32 bit
    z_seek: float  # real 32 bit
    drop_type: "Prep.TipDropType"  # enum

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.x_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.y_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_seek, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.drop_type, ParameterTypes.Enum)
      return out

  @dataclass
  class TipHeightCalibrationParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    x_position: float  # real 32 bit
    y_position: float  # real 32 bit
    z_start: float  # real 32 bit
    z_stop: float  # real 32 bit
    z_final: float  # real 32 bit
    volume: float  # real 32 bit
    tip_type: "Prep.TipTypes"  # enum

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.x_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.y_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_start, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_stop, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.z_final, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.volume, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.tip_type, ParameterTypes.Enum)
      return out

  @dataclass
  class DispenserVolumeEntry:
    default_values: bool  # bool
    type: "Prep.VolumeType"  # enum
    volume: float  # real 32 bit

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.type, ParameterTypes.Enum)
      out += encode_data_fragment(self.volume, ParameterTypes.Real32Bit)
      return out

  @dataclass
  class DispenserVolumeStackReturnParameters:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    total_volume: float  # real 32 bit
    volumes: list["Prep.DispenserVolumeEntry"]  # array of DispenserVolumeEntry

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.total_volume, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.volumes, ParameterTypes.StructureArray)
      return out

  @dataclass
  class AspirateParametersNoLldAndMonitoring2:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    aspirate: "Prep.AspirateParameters"  # struct
    container_description: list["Prep.SegmentDescriptor"]  # array of SegmentDescriptor
    common: "Prep.CommonParameters"  # struct
    no_lld: "Prep.NoLldParameters"  # struct
    mix: "Prep.MixParameters"  # struct
    adc: "Prep.AdcParameters"  # struct
    aspirate_monitoring: "Prep.AspirateMonitoringParameters"  # struct

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.aspirate, ParameterTypes.Structure)
      out += encode_data_fragment(self.container_description, ParameterTypes.StructureArray)
      out += encode_data_fragment(self.common, ParameterTypes.Structure)
      out += encode_data_fragment(self.no_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.mix, ParameterTypes.Structure)
      out += encode_data_fragment(self.adc, ParameterTypes.Structure)
      out += encode_data_fragment(self.aspirate_monitoring, ParameterTypes.Structure)
      return out

  @dataclass
  class AspirateParametersNoLldAndTadm2:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    aspirate: "Prep.AspirateParameters"  # struct
    container_description: list["Prep.SegmentDescriptor"]  # array of SegmentDescriptor
    common: "Prep.CommonParameters"  # struct
    no_lld: "Prep.NoLldParameters"  # struct
    mix: "Prep.MixParameters"  # struct
    adc: "Prep.AdcParameters"  # struct
    tadm: "Prep.TadmParameters"  # struct

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.aspirate, ParameterTypes.Structure)
      out += encode_data_fragment(self.container_description, ParameterTypes.StructureArray)
      out += encode_data_fragment(self.common, ParameterTypes.Structure)
      out += encode_data_fragment(self.no_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.mix, ParameterTypes.Structure)
      out += encode_data_fragment(self.adc, ParameterTypes.Structure)
      out += encode_data_fragment(self.tadm, ParameterTypes.Structure)
      return out

  @dataclass
  class AspirateParametersLldAndMonitoring2:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    aspirate: "Prep.AspirateParameters"  # struct
    container_description: list["Prep.SegmentDescriptor"]  # array of SegmentDescriptor
    common: "Prep.CommonParameters"  # struct
    lld: "Prep.LldParameters"  # struct
    p_lld: "Prep.PLldParameters"  # struct
    c_lld: "Prep.CLldParameters"  # struct
    mix: "Prep.MixParameters"  # struct
    aspirate_monitoring: "Prep.AspirateMonitoringParameters"  # struct
    adc: "Prep.AdcParameters"  # struct

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.aspirate, ParameterTypes.Structure)
      out += encode_data_fragment(self.container_description, ParameterTypes.StructureArray)
      out += encode_data_fragment(self.common, ParameterTypes.Structure)
      out += encode_data_fragment(self.lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.p_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.c_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.mix, ParameterTypes.Structure)
      out += encode_data_fragment(self.aspirate_monitoring, ParameterTypes.Structure)
      out += encode_data_fragment(self.adc, ParameterTypes.Structure)
      return out

  @dataclass
  class AspirateParametersLldAndTadm2:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    aspirate: "Prep.AspirateParameters"  # struct
    container_description: list["Prep.SegmentDescriptor"]  # array of SegmentDescriptor
    common: "Prep.CommonParameters"  # struct
    lld: "Prep.LldParameters"  # struct
    p_lld: "Prep.PLldParameters"  # struct
    c_lld: "Prep.CLldParameters"  # struct
    mix: "Prep.MixParameters"  # struct
    tadm: "Prep.TadmParameters"  # struct
    adc: "Prep.AdcParameters"  # struct

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.aspirate, ParameterTypes.Structure)
      out += encode_data_fragment(self.container_description, ParameterTypes.StructureArray)
      out += encode_data_fragment(self.common, ParameterTypes.Structure)
      out += encode_data_fragment(self.lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.p_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.c_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.mix, ParameterTypes.Structure)
      out += encode_data_fragment(self.tadm, ParameterTypes.Structure)
      out += encode_data_fragment(self.adc, ParameterTypes.Structure)
      return out

  @dataclass
  class DispenseParametersNoLld2:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    dispense: "Prep.DispenseParameters"  # struct
    container_description: list["Prep.SegmentDescriptor"]  # array of SegmentDescriptor
    common: "Prep.CommonParameters"  # struct
    no_lld: "Prep.NoLldParameters"  # struct
    mix: "Prep.MixParameters"  # struct
    adc: "Prep.AdcParameters"  # struct
    tadm: "Prep.TadmParameters"  # struct

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.dispense, ParameterTypes.Structure)
      out += encode_data_fragment(self.container_description, ParameterTypes.StructureArray)
      out += encode_data_fragment(self.common, ParameterTypes.Structure)
      out += encode_data_fragment(self.no_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.mix, ParameterTypes.Structure)
      out += encode_data_fragment(self.adc, ParameterTypes.Structure)
      out += encode_data_fragment(self.tadm, ParameterTypes.Structure)
      return out

  @dataclass
  class DispenseParametersLld2:
    default_values: bool  # bool
    channel: "Prep.ChannelIndex"  # enum
    dispense: "Prep.DispenseParameters"  # struct
    container_description: list["Prep.SegmentDescriptor"]  # array of SegmentDescriptor
    common: "Prep.CommonParameters"  # struct
    lld: "Prep.LldParameters"  # struct
    c_lld: "Prep.CLldParameters"  # struct
    mix: "Prep.MixParameters"  # struct
    adc: "Prep.AdcParameters"  # struct
    tadm: "Prep.TadmParameters"  # struct

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.default_values, ParameterTypes.Bool)
      out += encode_data_fragment(self.channel, ParameterTypes.Enum)
      out += encode_data_fragment(self.dispense, ParameterTypes.Structure)
      out += encode_data_fragment(self.container_description, ParameterTypes.StructureArray)
      out += encode_data_fragment(self.common, ParameterTypes.Structure)
      out += encode_data_fragment(self.lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.c_lld, ParameterTypes.Structure)
      out += encode_data_fragment(self.mix, ParameterTypes.Structure)
      out += encode_data_fragment(self.adc, ParameterTypes.Structure)
      out += encode_data_fragment(self.tadm, ParameterTypes.Structure)
      return out

  class Error(IntEnum):
    ChannelsBusy = 3585
    InvalidChannelIndex = 3586
    SiteNotDefined = 3587
    ChannelPowerRemoved = 3588
    HeadlessChannel = 3589
    CoordinatorProxyTimeout = 3590
    CalibrationInProgress = 3591

    # User-discovered
    # TipNotFound = 3848

  class HcResult(IntEnum):
    Success = 0x0000
    GenericError = 0x0001
    GenericNotReady = 0x0002
    GenericNullParameter = 0x0003
    GenericCalledByInitHandler = 0x0004
    GenericInvalidData = 0x0005
    GenericOutOfMemory = 0x0006
    GenericWriteFault = 0x0007
    GenericReadFault = 0x0008
    GenericBufferOverflow = 0x0009
    GenericNotInitialized = 0x000A
    GenericAlreadyInitialized = 0x000B
    GenericWaitAborted = 0x000C
    GenericTimeOut = 0x000D
    GenericMissingCallBack = 0x000E
    GenericInvalidHandle = 0x000F
    GenericNotSupported = 0x0010
    GenericInvalidParameter = 0x0011
    GenericNotImplemented = 0x0012
    GenericBadCrc = 0x0013
    GenericFlashNotBlank = 0x0014
    GenericMultipleErrorsReported = 0x0015
    GenericCoordinatedCommandTimeout = 0x0016
    GenericAccessDenied = 0x0017
    GenericBusy = 0x0019
    GenericMethodObsolete = 0x001A
    GenericNotConfigured = 0x001B
    GenericNotCalibrated = 0x001C
    GenericOptionalFunctionalityNotPresent = 0x001D
    GenericResumeFromInvalidState = 0x001E
    GenericAbortFromInvalidState = 0x001F
    GenericActionAborted = 0x0020
    GenericPauseFromInvalidState = 0x0021
    GenericPaused = 0x0022
    GenericSuspended = 0x0023
    GenericExitSuspendFromInvalidState = 0x0024
    KernelMutexTimeout = 0x0101
    KernelSemaphoreTimeout = 0x0102
    KernelEventTimeout = 0x0103
    KernelNoMutex = 0x0104
    KernelMutexNotOwned = 0x0105
    KernelNoWaitingTask = 0x0106
    KernelInvalidTask = 0x0107
    KernelNoTaskControlBlock = 0x0108
    NetworkUndefinedProtocol = 0x0201
    NetworkNoDestination = 0x0202
    NetworkRegistrationError = 0x0203
    NetworkNotRegistered = 0x0204
    NetworkBusy = 0x0205
    NetworkInvalidDispatchID = 0x0206
    NetworkInvalidMessage = 0x0207
    NetworkUnsupportedParameter = 0x0208
    NetworkCommandCompleteNotValid = 0x0209
    NetworkInvalidMessageParameter = (
      0x020A  # went command id is wrong, or when parameters don't match the command
    )
    NetworkIncompatibleProtocolVersion = 0x020B
    NetworkInvalidNodeId = 0x020C
    NetworkInvalidModuleId = 0x020D
    NetworkInvalidInterfaceId = 0x020E
    NetworkInvalidAction = 0x020F
    NetworkProxySendAttemptFailed = 0x0210
    NetworkRegistrationFailedDuplicateAddress = 0x0211
    NetworkUnableToProperlyFillOutResults = 0x0212
    NetworkDuplicateEventRegistration = 0x0213
    NetworkEventRegistrationExceedsMaximumAllowedSubscribers = 0x0214
    NetworkMaximumNodeToNodeEventRegistrationsExceeded = 0x0215
    NetworkMaximumNodeToNodeEventHandlerRegistrationsExceeded = 0x0216
    NetworkUnsupportedHarpPayloadProtocol = 0x0217
    NetworkUnableToSubscribeInvalidEvent = 0x0218
    NetworkGlobalObjectDefinedButNotInstantiated = 0x0219
    NetworkNodeGlobalObjectDefinedButNotInstantiated = 0x021A
    NetworkProxyRequestValidationFailed = 0x021B
    XPortSlOsPortNotInstalled = 0x0301
    XPortSlIpTaskPriorityNotSet = 0x0302
    XPortSlTimerTaskPriorityNotSet = 0x0303
    XPortSlDriverNotSet = 0x0304
    XPortSlIpAddressNotSet = 0x0305
    XPortSlNetMaskNotSet = 0x0306
    XPortSlCmxInitFailure = 0x0307
    XPortSlMacAddressNotSet = 0x0308
    XPortSlHostNameTooShort = 0x0309
    XPortSlNostNameTooLong = 0x030A
    XPortSlHostNameInvalidChars = 0x030B
    XPortNxpLpc2xxxCanInvalidChannel = 0x0320
    XPortNxpLpc2xxxCanInvalidGroup = 0x0321
    XPortNxpLpc2xxxCanBitRate = 0x0322
    XPortNxpLpc2xxxCanRxInterruptInstall = 0x0323
    XPortNxpLpc2xxxCanRxInterrupRemove = 0x0324
    XPortNxpLpc2xxxCanTxInterruptInstall = 0x0325
    XPortNxpLpc2xxxCanTxInterrupRemove = 0x0326
    XPortNxpLpc2xxxCanTxInvalidLength = 0x0327
    XPortNxpLpc2xxxCanTxBusy = 0x0328
    XPortArcNetAlreadyConfigured = 0x0329
    XPortArcNetNotConfigured = 0x032A
    XPortArcNetInterruptInstallFailed = 0x032B
    XPortArcNetTxNoAck = 0x032C
    XPortArcNetDiagnosticTestFailed = 0x032D
    XPortArcNetNodeIdTestFailed = 0x032E
    XPortArcNetInvalidNodeId = 0x032F
    XPortArcNetTxNotAvailable = 0x0330
    XPortArcNetInvalidDataRate = 0x0331
    XPortArcNetInvalidPacketLength = 0x0332
    XPortArcNetSingleNodeNetwork = 0x0333
    XPortArcNetNoResponseToFbe = 0x0334
    XPortProtocolMismatch = 0x0341
    XPortPacketRouterNotRegistered = 0x0342
    XPortCouldNotStartPacketRouterRxThread = 0x0343
    XPortPacketRouterAlreadyRegistered = 0x0344
    XPortNoPacketToProcess = 0x0345
    XPortWireProtocolNotRegistered = 0x0346
    XPortWireProtocolAlreadyRegistered = 0x0347
    XPortWireProtocolRegistrationSpaceFull = 0x0348
    XPortPayloadProtocolNotRegistered = 0x0349
    XPortPayloadProtocolAlreadyRegsitered = 0x034A
    XPortPayloadRegistrationSpaceFull = 0x034B
    XPortAddressNotSet = 0x034C
    XPortAttemptToSendToSelf = 0x034D
    XPortTxTimeout = 0x034E
    XPortRxDuplicateFrame = 0x034F
    XPortCanWp0VersionConflict = 0x0360
    XPortCanExcessivePacketSize = 0x0361
    XPortCanWp0AckHasNoMatchingPacket = 0x0362
    XPortCanWp0WrapperOnlyOneAddressSupported = 0x0363
    XPortCanWp0ErrorStartRefused = 0x0364
    XPortCanWp0ErrorBufferOverrun = 0x0365
    XPortCanWp0InvalidFrame = 0x0366
    XPortCanWp0StrayDataFrame = 0x0367
    XPortCanWp0ShortMessage = 0x0368
    XPortCanWp0LongMessage = 0x0369
    XPortCanWp0UnknownError = 0x036A
    XPortCanWp0NoResponseFromDestination = 0x036B
    XPortCanWp0SendError = 0x036C
    XPortCanWbzUnknownFrame = 0x036D
    XPortCanWbzUnsolicitedRemoteFrame = 0x036E
    XPortCanWbzUnsolicitedDataFrame = 0x036F
    XPortCanWbzWrapperOnlyOneAddressSupported = 0x0370
    XPortCanWp0LastMessageFailed = 0x0371
    XPortIpStackConfigurationFailure = 0x0380
    XPortIpStackNotConfigured = 0x0381
    XPortSocketCreationFailure = 0x0382
    XPortSocketConfigFailure = 0x0383
    XPortSocketBindFailure = 0x0384
    XPortIpTaskAlreadyStarted = 0x0385
    XPortIpTaskNotStarted = 0x0386
    XPortTcpListenFailure = 0x0387
    XPortTcpClientAlreadyConnected = 0x0388
    XPortTcpClientNotConnected = 0x0389
    XPortTcpConnectionFailure = 0x038A
    XPortTcpCloseFailure = 0x038B
    XPortTcpSendError = 0x038C
    XPortUdpSendError = 0x038D
    XPortMalformedDiscoveryRequest = 0x038E
    XPortIpDhcpFailed = 0x038F
    XPortIpStaticAddressConfigFailed = 0x0390
    XPortArcNetBufferOverrun = 0x03A0
    XPortArcNetVersionConflict = 0x03A1
    XPortArcNetInvalidFrameType = 0x03A2
    XPortArcNetInvalidFrame = 0x03A3
    XPortArcNetUnknownError = 0x03A4
    XPortArcNetAckHasNoMatchingPacket = 0x03A5
    XPortArcNetInvalidMessageSize = 0x03A6
    XPortArcNetLastMessageFailed = 0x03A7
    XPortArcNetWp0RefusedSyn = 0x03A8
    XPortArcNetWp0MessageTooShort = 0x03A9
    XPortArcNetWp0MessageTooLong = 0x03AA
    XPortArcNetWp0InvalidSequenceNumber = 0x03AB
    XPortArcNetWp0NoResponseFromDestination = 0x03AC
    XPortRS232PppTimeout = 0x03C0
    ComLinkReferToInnerException = 0x0400
    ComLinkNotConnected = 0x0401
    ComLinkTcpConnectionFailed = 0x0402
    ComLinkFailedToCloseConnectionProperly = 0x0403
    ComLinkInvalidProtocolVersion = 0x0404
    ComLinkUnsupportedOptionsDetectedByServer = 0x0405
    ComLinkNodeIdNegotiationFailure = 0x0406
    ComLinkConnectionIntentError = 0x0407
    ComLinkUnableToConfigureKeepAlive = 0x0408
    ComLinkFailedToSendConnectionPacket = 0x0409
    ComLinkInvalidRegistrationAction = 0x040A
    ComLinkUnexpectedRequestedHarpAddressReturned = 0x040B
    ComLinkHarpAddressRegistrationFailed = 0x040C
    ComLinkHarpAddressDeregistrationFailed = 0x040D
    ComLinkIdentificationNotImplemented = 0x040E
    ComLinkIdentificationNotSupported = 0x040F
    ComLinkFailedToSendIdentificationRequest = 0x0410
    ComLinkNoResponseFromInstrumentRegistrationServer = 0x0411
    ComLinkNoRootObjectFound = 0x0412
    ComLinkEthernetObjectNotFound = 0x0413
    ComLinkMethodNotFound = 0x0414
    ComLinkProtocolActionConversionFailed = 0x0415
    ComLinkTimeout = 0x0416
    ComLinkUnableToSendOrReceive = 0x0417
    ComLinkTransportTransportableIntroductionFailure = 0x0418
    ComLinkHarpHarpableIntroductionFailure = 0x0419
    ComLinkDownloadException = 0x041A
    ComLinkSizeOfReturnParametersNotValid = 0x041B
    ComLinkRestrictedMethod = 0x041C
    ComLinkInvalidNumberOfStructureParametersFromNetworkLayer = 0x041D
    ComLinkInvalidTypeInStructureFromNetworkLayer = 0x041E
    ComLinkRs232ConnectionFailed = 0x041F
    ComLinkRs232InvalidPort = 0x0420
    ComLinkLoggingCannotBeConfiguredWhileConnectedOrConnecting = 0x0421
    ComLinkThreadAbortExceptionDetected = 0x0422
    ComLinkUnableToSend = 0x0423
    ComLinkUnableToReceive = 0x0424
    ComLinkConnectionRequiredToProceed = 0x0425
    ComLinkTooMuchDataToSend = 0x0426
    ComLinkCanConfigurationFailure = 0x0427
    ComLinkUnableToRetrieveListOfModules = 0x0428
    ComLinkTcpConnectionFailedConnectionRefused = 0x0429
    ComLinkTcpConnectionFailedHostUnreachable = 0x042A
    ComLinkTcpConnectionFailedHostNotFound = 0x042B
    ComLinkTcpConnectionFailedTimedOut = 0x042C
    ComLinkTcpConnectionFailedIsConnected = 0x042D
    ComLinkConnectionClosedWithOutstandingRequest = 0x042E
    ComLinkNotConfigured = 0x042F
    ComLinkRs232MultiFailedToConnect = 0x0430
    ComLinkAttemptToCallNonStatusRequestMethodWithMonitorConnection = 0x0431
    ComLinkPauseResumeFunctionalityNotSupported = 0x0432
    ComLinkFailedToCreateDeviceHandleForUsbDevice = 0x0433
    ComLinkUsbDeviceNotAvailable = 0x0434
    ComLinkUsbConnectionFailed = 0x0435
    ComLinkUsbConnectionLost = 0x0436
    ComLinkBonaduzError = 0x0437
    ComLinkUsbMultiFailedToConnect = 0x0438
    GenericMultipleWarningsReported = 0x8018

  class TipTypes(IntEnum):
    None_ = 0  # Use None_ since None is a reserved keyword in Python
    LowVolume = 1
    StandardVolume = 2
    HighVolume = 3

  class LldSensitivities(IntEnum):
    Low = 0
    MediumLow = 1
    MediumHigh = 2
    High = 3
    Tool = 4
    Waste = 5

  class DetectModes(IntEnum):
    Any = 0
    Primary = 1
    Secondary = 2
    All = 3

  class YAcceleration(IntEnum):
    YLowestAcceleration = 1
    YLowAcceleration = 2
    YMediumAcceleration = 3
    YDefaultAcceleration = 4

  @dataclass
  class DriveStatus:
    initialized: bool
    position: float
    encoder_position: float
    in_home_sensor: bool

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.initialized, ParameterTypes.Bool)
      out += encode_data_fragment(self.position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.encoder_position, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.in_home_sensor, ParameterTypes.Bool)
      return out

  @dataclass
  class SegmentDescriptor:
    area_top: float
    area_bottom: float
    height: float

    def encode(self) -> bytes:
      out = b""
      out += encode_data_fragment(self.area_top, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.area_bottom, ParameterTypes.Real32Bit)
      out += encode_data_fragment(self.height, ParameterTypes.Real32Bit)
      return out

  # Liquid handler backend commands

  @property
  def num_channels(self) -> int:
    return 2

  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    final_z: float = 123.87,
    timeout: Optional[float] = None,
  ):
    tip_parameters = []

    assert len(ops) == len(use_channels)
    assert max(use_channels) <= 2, "Only two channels are supported for now"

    indexed_ops = {channel_idx: op for channel_idx, op in zip(use_channels, ops)}
    for channel_idx in range(2):
      if channel_idx in indexed_ops:
        op = indexed_ops[channel_idx]
        loc = op.resource.get_absolute_location("c", "c", "t")
        z = loc.z + op.resource.get_tip().total_tip_length

        tip_parameters.append(
          Prep.TipPositionParameters(
            default_values=False,
            channel={
              0: Prep.ChannelIndex.RearChannel,
              1: Prep.ChannelIndex.FrontChannel,
            }[channel_idx],
            x_position=loc.x,
            y_position=loc.y,
            z_position=z,
            z_seek=z + 12,  # ?
          )
        )

    seek_speed = 15.0

    assert len(set(op.tip for op in ops)) == 1, "All ops must use the same tip"
    tip = ops[0].tip
    tip_definition = Prep.TipPickupParameters(
      default_values=False,
      volume=tip.maximal_volume,
      length=tip.total_tip_length - tip.fitting_depth,
      tip_type=Prep.TipTypes.StandardVolume,  # ?
      has_filter=tip.has_filter,
      is_needle=False,
      is_tool=False,
    )
    enable_tadm = False
    dispenser_volume = 0.0
    dispenser_speed = 250.0

    return await self.send_command(
      command_id=9,
      parameters=[
        (tip_parameters, ParameterTypes.StructureArray),
        (final_z, ParameterTypes.Real32Bit),
        (seek_speed, ParameterTypes.Real32Bit),
        (tip_definition, ParameterTypes.Structure),
        (enable_tadm, ParameterTypes.Bool),
        (dispenser_volume, ParameterTypes.Real32Bit),
        (dispenser_speed, ParameterTypes.Real32Bit),
      ],
      timeout=timeout,
      harp_source=Prep.HarpPacket.HarpAddress((0x0002, 0x0007, 0x0006)),
      harp_destination=self.pipettor_destination,
    )

  async def drop_tips(
    self,
    ops: List[Drop],
    use_channels: List[int],
    final_z: float = 123.87,
    seek_speed: float = 10.0,
    tip_roll_off_distance: float = 0.0,
    timeout: Optional[float] = None,
  ):
    """Drop tips from the specified resource."""

    tip_parameters = []

    assert len(ops) == len(use_channels)
    assert max(use_channels) <= 2, "Only two channels are supported for now"

    indexed_ops = {channel_idx: op for channel_idx, op in zip(use_channels, ops)}

    for channel_idx in range(2):
      if channel_idx in indexed_ops:
        op = indexed_ops[channel_idx]
        loc = op.resource.get_absolute_location("c", "c", "t")
        z = loc.z + op.resource.get_tip().total_tip_length

        tip_parameters.append(
          Prep.TipDropParameters(
            default_values=False,
            channel={
              0: Prep.ChannelIndex.RearChannel,
              1: Prep.ChannelIndex.FrontChannel,
            }[channel_idx],
            x_position=loc.x,
            y_position=loc.y,
            z_position=z,
            z_seek=z + 12,
            drop_type=Prep.TipDropType.FixedHeight,
          )
        )

    return await self.send_command(
      command_id=12,
      parameters=[
        (tip_parameters, ParameterTypes.StructureArray),
        (final_z, ParameterTypes.Real32Bit),
        (seek_speed, ParameterTypes.Real32Bit),
        (tip_roll_off_distance, ParameterTypes.Real32Bit),
      ],
      timeout=timeout,
      harp_source=Prep.HarpPacket.HarpAddress((0x0002, 0x0007, 0x0006)),
      harp_destination=self.pipettor_destination,
    )

  async def aspirate(
    self,
    ops: List[SingleChannelAspiration],
    use_channels: List[int],
    z_final: float = 96.97,
    timeout: Optional[float] = None,
  ):
    """Aspirate liquid from the specified resource using pip."""

    aspirate_parameters = []

    assert len(ops) == len(use_channels)
    assert max(use_channels) <= 2, "Only two channels are supported for now"

    indexed_ops = {channel_idx: op for channel_idx, op in zip(use_channels, ops)}
    for channel_idx in range(2):
      if channel_idx in indexed_ops:
        channel = {
          0: Prep.ChannelIndex.RearChannel,
          1: Prep.ChannelIndex.FrontChannel,
        }[channel_idx]

        op = indexed_ops[channel_idx]
        loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")

        assert op.resource.get_size_x() == op.resource.get_size_y(), "Only round"
        radius = op.resource.get_size_x() / 2

        aspirate_parameters.append(
          Prep.AspirateParametersNoLldAndMonitoring(
            default_values=False,
            channel=channel,
            aspirate=Prep.AspirateParameters(
              default_values=False,
              x_position=loc.x,
              y_position=loc.y,
              prewet_volume=0.0,
              blowout_volume=op.blow_out_air_volume or 0,
            ),
            common=Prep.CommonParameters(
              default_values=False,
              empty=True,
              z_minimum=-5.03,  # ?
              z_final=z_final,
              z_liquid_exit_speed=2.0,  # ?
              liquid_volume=op.volume,
              liquid_speed=op.flow_rate or 100,  # ?
              transport_air_volume=0,  # op.transport_air_volume,
              tube_radius=radius,
              cone_height=0.0,  # TODO:
              cone_bottom_radius=0.0,
              settling_time=1.0,
              additional_probes=0,
            ),
            no_lld=Prep.NoLldParameters(
              default_values=False,
              z_fluid=94.97,  # ?
              z_air=96.97,  # ?
              bottom_search=False,
              z_bottom_search_offset=2.0,
              z_bottom_offset=0.0,
            ),
            mix=Prep.MixParameters.default(),
            adc=Prep.AdcParameters.default(),
            aspirate_monitoring=Prep.AspirateMonitoringParameters.default(),
          )
        )

    return await self.send_command(
      command_id=1,
      parameters=[
        (aspirate_parameters, ParameterTypes.StructureArray),
      ],
      timeout=timeout,
      harp_source=Prep.HarpPacket.HarpAddress((0x0002, 0x0007, 0x0006)),
      harp_destination=self.pipettor_destination,
    )

  async def dispense(
    self,
    ops: List[SingleChannelDispense],
    use_channels: List[int],
    final_z: float = 96.97,
    timeout: Optional[float] = None,
  ):
    """Dispense liquid from the specified resource using pip."""

    dispense_parameters = []

    assert len(ops) == len(use_channels)
    assert max(use_channels) <= 2, "Only two channels are supported for now"

    indexed_ops = {channel_idx: op for channel_idx, op in zip(use_channels, ops)}
    for channel_idx in range(2):
      if channel_idx in indexed_ops:
        op = indexed_ops[channel_idx]
        loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")

        assert op.resource.get_size_x() == op.resource.get_size_y(), "Only round"
        radius = op.resource.get_size_x() / 2

        dispense_parameters.append(
          Prep.DispenseParametersNoLld(
            default_values=False,
            channel={
              0: Prep.ChannelIndex.RearChannel,
              1: Prep.ChannelIndex.FrontChannel,
            }[channel_idx],
            dispense=Prep.DispenseParameters(
              default_values=False,
              x_position=loc.x,
              y_position=loc.y,
              stop_back_volume=0.0,  # ?
              cutoff_speed=100.0,  # ?
            ),
            common=Prep.CommonParameters(
              default_values=False,
              empty=True,  # TODO
              z_minimum=-5.03,  # ?
              z_final=final_z,
              z_liquid_exit_speed=2.0,  # ?
              liquid_volume=op.volume,
              liquid_speed=op.flow_rate or 100,
              transport_air_volume=0,  # op.transport_air_volume,
              tube_radius=radius,
              cone_height=0.0,  # TODO
              cone_bottom_radius=0,  # TODO
              settling_time=0.0,  # TODO
              additional_probes=0,  # ?
            ),
            no_lld=Prep.NoLldParameters(
              default_values=False,
              z_fluid=94.97,  # ?
              z_air=99.08,  # ?
              bottom_search=False,
              z_bottom_search_offset=2.0,
              z_bottom_offset=0.0,
            ),
            mix=Prep.MixParameters.default(),
            tadm=Prep.TadmParameters.default(),
            adc=Prep.AdcParameters.default(),
          )
        )

    return await self.send_command(
      command_id=5,
      parameters=[
        (dispense_parameters, ParameterTypes.StructureArray),
      ],
      timeout=timeout,
      harp_source=Prep.HarpPacket.HarpAddress((0x0002, 0x0007, 0x0006)),
      harp_destination=self.pipettor_destination,
    )

  async def pick_up_tips96(self, pickup: PickupTipRack):
    raise NotImplementedError("This operation is not supported on the Prep")

  async def drop_tips96(self, drop: DropTipRack):
    raise NotImplementedError("This operation is not supported on the Prep")

  async def aspirate96(self, aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]):
    raise NotImplementedError("This operation is not supported on the Prep")

  async def dispense96(self, dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]):
    raise NotImplementedError("This operation is not supported on the Prep")

  async def pick_up_resource(self, pickup: ResourcePickup):
    raise NotImplementedError("This operation is not supported yet")

  async def move_picked_up_resource(self, move: ResourceMove):
    raise NotImplementedError("This operation is not supported yet")

  async def drop_resource(self, drop: ResourceDrop):
    raise NotImplementedError("This operation is not supported yet")

  # Firmware commands

  async def aspirate_tadm(
    self,
    aspirate_parameters: List["AspirateParametersNoLldAndTadm"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=2,
      parameters=[
        (aspirate_parameters, ParameterTypes.StructureArray),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def aspirate_lld(
    self,
    aspirate_parameters: List["AspirateParametersLldAndMonitoring"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=3,
      parameters=[
        (aspirate_parameters, ParameterTypes.StructureArray),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def aspirate_lld_tadm(
    self,
    aspirate_parameters: List["AspirateParametersLldAndTadm"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=4,
      parameters=[
        (aspirate_parameters, ParameterTypes.StructureArray),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def dispense_lld(
    self,
    dispense_parameters: List["DispenseParametersLld"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=6,
      parameters=[
        (dispense_parameters, ParameterTypes.StructureArray),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def dispense_initialize_to_waste(
    self,
    waste_parameters: List["DispenseInitToWasteParameters"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=7,
      parameters=[
        (waste_parameters, ParameterTypes.StructureArray),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def pick_up_tips_by_id(
    self,
    tip_parameters: List["TipPositionParameters"],
    final_z: float,
    seek_speed: float,
    tip_definition_id: int,
    enable_tadm: bool = False,
    dispenser_volume: float = 0.0,
    dispenser_speed: float = 250.0,
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=8,
      parameters=[
        (tip_parameters, ParameterTypes.StructureArray),
        (final_z, ParameterTypes.Real32Bit),
        (seek_speed, ParameterTypes.Real32Bit),
        (tip_definition_id, ParameterTypes.UInt8Bit),
        (enable_tadm, ParameterTypes.Bool),
        (dispenser_volume, ParameterTypes.Real32Bit),
        (dispenser_speed, ParameterTypes.Real32Bit),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def pick_up_needles_by_id(
    self,
    tip_parameters: List["TipPositionParameters"],
    final_z: float,
    seek_speed: float,
    tip_definition_id: int,
    blowout_offset: float = 4.0,
    blowout_speed: float = 0.0,
    enable_tadm: bool = False,
    dispenser_volume: float = 0.0,
    dispenser_speed: float = 250.0,
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=10,
      parameters=[
        (tip_parameters, ParameterTypes.StructureArray),
        (final_z, ParameterTypes.Real32Bit),
        (seek_speed, ParameterTypes.Real32Bit),
        (tip_definition_id, ParameterTypes.UInt8Bit),
        (blowout_offset, ParameterTypes.Real32Bit),
        (blowout_speed, ParameterTypes.Real32Bit),
        (enable_tadm, ParameterTypes.Bool),
        (dispenser_volume, ParameterTypes.Real32Bit),
        (dispenser_speed, ParameterTypes.Real32Bit),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def pick_up_needles(
    self,
    tip_parameters: List["TipPositionParameters"],
    final_z: float,
    seek_speed: float,
    tip_definition: "Prep.TipPickupParameters",
    blowout_offset: float = 4.0,
    blowout_speed: float = 0.0,
    enable_tadm: bool = False,
    dispenser_volume: float = 0.0,
    dispenser_speed: float = 250.0,
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=11,
      parameters=[
        (tip_parameters, ParameterTypes.StructureArray),
        (final_z, ParameterTypes.Real32Bit),
        (seek_speed, ParameterTypes.Real32Bit),
        (tip_definition, ParameterTypes.Structure),
        (blowout_offset, ParameterTypes.Real32Bit),
        (blowout_speed, ParameterTypes.Real32Bit),
        (enable_tadm, ParameterTypes.Bool),
        (dispenser_volume, ParameterTypes.Real32Bit),
        (dispenser_speed, ParameterTypes.Real32Bit),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def pick_up_tool_by_id(
    self,
    tip_definition_id: int,
    tool_position_x: float,
    tool_position_z: float,
    front_channel_position_y: float,
    rear_channel_position_y: float,
    tool_seek: float,
    tool_x_radius: float,
    tool_y_radius: float,
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=14,
      parameters=[
        (tip_definition_id, ParameterTypes.UInt8Bit),
        (tool_position_x, ParameterTypes.Real32Bit),
        (tool_position_z, ParameterTypes.Real32Bit),
        (front_channel_position_y, ParameterTypes.Real32Bit),
        (rear_channel_position_y, ParameterTypes.Real32Bit),
        (tool_seek, ParameterTypes.Real32Bit),
        (tool_x_radius, ParameterTypes.Real32Bit),
        (tool_y_radius, ParameterTypes.Real32Bit),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def pick_up_tool(
    self,
    tip_definition: "Prep.TipPickupParameters",
    tool_position_x: float,
    tool_position_z: float,
    front_channel_position_y: float,
    rear_channel_position_y: float,
    tool_seek: float,
    tool_x_radius: float,
    tool_y_radius: float,
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=15,
      parameters=[
        (tip_definition, ParameterTypes.Structure),
        (tool_position_x, ParameterTypes.Real32Bit),
        (tool_position_z, ParameterTypes.Real32Bit),
        (front_channel_position_y, ParameterTypes.Real32Bit),
        (rear_channel_position_y, ParameterTypes.Real32Bit),
        (tool_seek, ParameterTypes.Real32Bit),
        (tool_x_radius, ParameterTypes.Real32Bit),
        (tool_y_radius, ParameterTypes.Real32Bit),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def drop_tool(
    self,
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=16,
      parameters=[],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def pick_up_plate(
    self,
    plate_top_center: "Prep.XYZCoord",
    plate: "Prep.PlateDimensions",
    clearance_y: float,
    grip_speed_y: float,
    grip_distance: float,
    grip_height: float,
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=17,
      parameters=[
        (plate_top_center, ParameterTypes.Structure),
        (plate, ParameterTypes.Structure),
        (clearance_y, ParameterTypes.Real32Bit),
        (grip_speed_y, ParameterTypes.Real32Bit),
        (grip_distance, ParameterTypes.Real32Bit),
        (grip_height, ParameterTypes.Real32Bit),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def drop_plate(
    self,
    plate_top_center: "Prep.XYZCoord",
    clearance_y: float,
    acceleration_scale_x: int = 100,
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=18,
      parameters=[
        (plate_top_center, ParameterTypes.Structure),
        (clearance_y, ParameterTypes.Real32Bit),
        (acceleration_scale_x, ParameterTypes.UInt8Bit),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def move_plate(
    self,
    plate_top_center: "Prep.XYZCoord",
    acceleration_scale_x: int = 100,
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=19,
      parameters=[
        (plate_top_center, ParameterTypes.Structure),
        (acceleration_scale_x, ParameterTypes.UInt8Bit),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def transfer_plate(
    self,
    plate_source_top_center: "Prep.XYZCoord",
    plate_destination_top_center: "Prep.XYZCoord",
    plate: "Prep.PlateDimensions",
    clearance_y: float,
    grip_speed_y: float,
    grip_distance: float,
    grip_height: float,
    acceleration_scale_x: int = 100,
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=20,
      parameters=[
        (plate_source_top_center, ParameterTypes.Structure),
        (plate_destination_top_center, ParameterTypes.Structure),
        (plate, ParameterTypes.Structure),
        (clearance_y, ParameterTypes.Real32Bit),
        (grip_speed_y, ParameterTypes.Real32Bit),
        (grip_distance, ParameterTypes.Real32Bit),
        (grip_height, ParameterTypes.Real32Bit),
        (acceleration_scale_x, ParameterTypes.UInt8Bit),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def release_plate(
    self,
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=21,
      parameters=[],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def empty_dispenser(
    self,
    channels: List["ChannelIndex"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=23,
      parameters=[
        (channels, ParameterTypes.EnumArray),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def move_to_position(
    self,
    move_parameters: "Prep.GantryMoveXYZParameters",
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=26,
      parameters=[
        (move_parameters, ParameterTypes.Structure),
      ],
      timeout=timeout,
      harp_source=Prep.HarpPacket.HarpAddress((0x0002, 0x0007, 0x0006)),
      harp_destination=self.pipettor_destination,
    )

  async def move_to_position_via_lane(
    self,
    move_parameters: "Prep.GantryMoveXYZParameters",
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=27,
      parameters=[
        (move_parameters, ParameterTypes.Structure),
      ],
      timeout=timeout,
      harp_source=Prep.HarpPacket.HarpAddress((0x0002, 0x0007, 0x0006)),
      harp_destination=self.pipettor_destination,
    )

  async def move_z_up_to_safe(
    self,
    channels: List["ChannelIndex"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=28,
      parameters=[
        (channels, ParameterTypes.EnumArray),
      ],
      timeout=timeout,
      # harp_source=self.pipettor_source,
      harp_source=Prep.HarpPacket.HarpAddress((0x0002, 0x0007, 0x0006)),
      harp_destination=self.pipettor_destination,
    )

  async def z_seek_lld_position(
    self,
    seek_parameters: List["LLDChannelSeekParameters"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=29,
      parameters=[
        (seek_parameters, ParameterTypes.StructureArray),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def create_tadm_limit_curve(
    self,
    channel: "Prep.ChannelIndex",
    name: str,
    lower_limit: List["LimitCurveEntry"],
    upper_limit: List["LimitCurveEntry"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=31,
      parameters=[
        (channel, ParameterTypes.UInt32Bit),
        (name, ParameterTypes.String),
        (lower_limit, ParameterTypes.StructureArray),
        (upper_limit, ParameterTypes.StructureArray),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def erase_tadm_limit_curves(
    self,
    channel: "Prep.ChannelIndex",
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=32,
      parameters=[
        (channel, ParameterTypes.UInt32Bit),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def get_tadm_limit_curve_names(
    self,
    channel: "Prep.ChannelIndex",
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=33,
      parameters=[
        (channel, ParameterTypes.UInt32Bit),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def get_tadm_limit_curve_info(
    self,
    channel: "Prep.ChannelIndex",
    name: str,
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=34,
      parameters=[
        (channel, ParameterTypes.UInt32Bit),
        (name, ParameterTypes.String),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def retrieve_tadm_data(
    self,
    channel: "Prep.ChannelIndex",
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=35,
      parameters=[
        (channel, ParameterTypes.UInt32Bit),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def reset_tadm_fifo(
    self,
    channels: List["ChannelIndex"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=36,
      parameters=[
        (channels, ParameterTypes.EnumArray),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def aspirate_v2(
    self,
    aspirate_parameters: List["AspirateParametersNoLldAndMonitoring2"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=38,
      parameters=[
        (aspirate_parameters, ParameterTypes.StructureArray),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def aspirate_tadm_v2(
    self,
    aspirate_parameters: List["AspirateParametersNoLldAndTadm2"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=39,
      parameters=[
        (aspirate_parameters, ParameterTypes.StructureArray),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def aspirate_lld_v2(
    self,
    aspirate_parameters: List["AspirateParametersLldAndMonitoring2"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=40,
      parameters=[
        (aspirate_parameters, ParameterTypes.StructureArray),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def aspirate_lld_tadm_v2(
    self,
    aspirate_parameters: List["AspirateParametersLldAndTadm2"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=41,
      parameters=[
        (aspirate_parameters, ParameterTypes.StructureArray),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def dispense_v2(
    self,
    dispense_parameters: List["DispenseParametersNoLld2"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=42,
      parameters=[
        (dispense_parameters, ParameterTypes.StructureArray),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def dispense_lld_v2(
    self,
    dispense_parameters: List["DispenseParametersLld2"],
    timeout: Optional[float] = None,
  ) -> bytes:
    return await self.send_command(
      command_id=43,
      parameters=[
        (dispense_parameters, ParameterTypes.StructureArray),
      ],
      timeout=timeout,
      harp_source=self.pipettor_source,
      harp_destination=self.pipettor_destination,
    )

  async def initialize(
    self,
    tip_drop_params: InitTipDropParameters,
    smart: bool = False,
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=1,
      parameters=[
        (smart, ParameterTypes.Bool),
        (tip_drop_params, ParameterTypes.Structure),
      ],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )

  async def park(
    self,
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=3,
      parameters=[],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )

  async def spread(
    self,
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=4,
      parameters=[],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )

  async def add_tip_and_needle_definition(
    self,
    parameters_: "Prep.TipDefinition",
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=12,
      parameters=[
        (parameters_, ParameterTypes.Structure),
      ],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )

  async def remove_tip_and_needle_definition(
    self,
    id_: int,
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=13,
      parameters=[
        (id_, ParameterTypes.Enum),
      ],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )

  async def read_storage(
    self,
    offset: int,
    length: int,
    timeout: Optional[float] = None,
  ) -> bytes:
    result = await self.send_command(
      command_id=14,
      parameters=[
        (offset, ParameterTypes.UInt32Bit),
        (length, ParameterTypes.UInt32Bit),
      ],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )
    return result

  async def write_storage(
    self,
    offset: int,
    data: bytes,
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=15,
      parameters=[
        (offset, ParameterTypes.UInt32Bit),
        (data, ParameterTypes.UInt8Array),
      ],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )

  async def power_down_request(
    self,
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=17,
      parameters=[],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )

  async def confirm_power_down(
    self,
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=18,
      parameters=[],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )

  async def cancel_power_down(
    self,
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=19,
      parameters=[],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )

  async def remove_channel_power_for_head_swap(
    self,
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=23,
      parameters=[],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )

  async def restore_channel_power_after_head_swap(
    self,
    delay_ms: int,
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=24,
      parameters=[
        (delay_ms, ParameterTypes.UInt32Bit),
      ],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )

  async def set_deck_light(
    self,
    white: int,
    red: int,
    green: int,
    blue: int,
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=25,
      parameters=[
        (white, ParameterTypes.UInt8Bit),
        (red, ParameterTypes.UInt8Bit),
        (green, ParameterTypes.UInt8Bit),
        (blue, ParameterTypes.UInt8Bit),
      ],
      timeout=timeout,
      harp_source=Prep.HarpPacket.HarpAddress((0x0002, 0x0005, 0x0002)),
      harp_destination=self.destination_address,
    )

  async def disco_mode(self):
    """Easter egg"""
    for _ in range(69):
      await self.set_deck_light(
        white=random.randint(1, 255),
        red=random.randint(1, 255),
        green=random.randint(1, 255),
        blue=random.randint(1, 255),
      )
      await asyncio.sleep(0.1)

  async def get_deck_light(
    self,
    timeout: Optional[float] = None,
  ) -> "Tuple[int, int, int, int]":
    result = await self.send_command(
      command_id=26,
      parameters=[],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )
    if len(result) != 4:
      raise ValueError("Invalid return length for deck light data.")
    white, red, green, blue = result
    return white, red, green, blue

  async def suspended_park(
    self,
    move_parameters: "Prep.GantryMoveXYZParameters",
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=29,
      parameters=[
        (move_parameters, ParameterTypes.Structure),
      ],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )

  async def method_begin(
    self,
    automatic_pause: bool = False,
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=30,
      parameters=[
        (automatic_pause, ParameterTypes.Bool),
      ],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )

  async def method_end(
    self,
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=31,
      parameters=[],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )

  async def method_abort(
    self,
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=33,
      parameters=[],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )

  async def is_parked(
    self,
    timeout: Optional[float] = None,
  ) -> bool:
    result = await self.send_command(
      command_id=34,
      parameters=[],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )
    if len(result) != 1:
      raise ValueError("Invalid return length for is_parked status.")
    return bool(result[0])

  async def is_spread(
    self,
    timeout: Optional[float] = None,
  ) -> bool:
    result = await self.send_command(
      command_id=35,
      parameters=[],
      timeout=timeout,
      harp_source=self.source_address,
      harp_destination=self.destination_address,
    )
    if len(result) != 1:
      raise ValueError("Invalid return length for is_spread status.")
    return bool(result[0])

  # custom

  async def z_travel_configuration(
    self,
    unknown: int,
    timeout: Optional[float] = None,
  ) -> None:
    return await self.send_command(
      command_id=13,
      parameters=[
        (unknown, ParameterTypes.Enum),
      ],
      timeout=timeout,
      harp_source=Prep.HarpPacket.HarpAddress((0x0002, 0x0004, 0x0005)),
      harp_destination=Prep.HarpPacket.HarpAddress((0x0001, 0x0001, 0xBEF0)),
    )
