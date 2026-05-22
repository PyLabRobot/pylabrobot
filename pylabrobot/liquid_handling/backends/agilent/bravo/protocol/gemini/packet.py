"""Gemini 8-byte Packet codec + InstructionAddress.

Packet layout::

    byte 0   src_addr   (DevID << 6) | NodeID
    byte 1   dest_addr  (DevID << 6) | NodeID
    byte 2   (msg_id << 4) | cmd_type       -- msg_id is 2 bits, cmd_type is 4 bits
    byte 3   sub_command
    bytes 4-7  cmd_val (big-endian uint32)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.enums import CommandTypes, PACKET_SIZE


# --- InstructionAddress -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InstructionAddress:
    """Controller-tree address: 6-bit node ID + 2-bit device ID, packed into 1 byte.

    Encoding: ``byte = (dev_id << 6) | (node_id & 0x3F)``
    """

    node_id: int
    dev_id: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.node_id <= 0x3F:
            raise ValueError(f"node_id {self.node_id} out of range 0..63")
        if not 0 <= self.dev_id <= 0x03:
            raise ValueError(f"dev_id {self.dev_id} out of range 0..3")

    @property
    def byte(self) -> int:
        return ((self.dev_id & 0x03) << 6) | (self.node_id & 0x3F)

    @classmethod
    def from_byte(cls, b: int) -> "InstructionAddress":
        return cls(node_id=b & 0x3F, dev_id=(b >> 6) & 0x03)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.node_id}.{self.dev_id}"


HOST_ADDRESS = InstructionAddress(0, 0)
MASTER_ADDRESS = InstructionAddress(1, 0)
BROADCAST_ADDRESS = InstructionAddress(63, 0)


# --- Packet -------------------------------------------------------------------


@dataclass(slots=True)
class Packet:
    """One 8-byte Gemini packet.

    ``cmd_type`` is one of :class:`CommandTypes`. ``msg_id`` is a 2-bit rotating
    counter (0..3) used to correlate SETCMD/GETCMD requests with their responses;
    it's encoded into the high nibble of byte 2.
    """

    src: InstructionAddress
    dest: InstructionAddress
    cmd_type: int
    sub_command: int
    cmd_val: int = 0
    msg_id: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.cmd_type <= 0x0F:
            raise ValueError(f"cmd_type {self.cmd_type} out of range 0..15")
        if not 0 <= self.msg_id <= 0x03:
            raise ValueError(f"msg_id {self.msg_id} out of range 0..3")
        if not 0 <= self.sub_command <= 0xFF:
            raise ValueError(f"sub_command {self.sub_command} out of range 0..255")
        if not 0 <= self.cmd_val <= 0xFFFFFFFF:
            raise ValueError(f"cmd_val {self.cmd_val} out of range 0..2^32-1")

    def to_bytes(self) -> bytes:
        b2 = ((self.msg_id & 0x03) << 4) | (self.cmd_type & 0x0F)
        return struct.pack(
            ">BBBBI",
            self.src.byte,
            self.dest.byte,
            b2,
            self.sub_command & 0xFF,
            self.cmd_val & 0xFFFFFFFF,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "Packet":
        if len(data) != PACKET_SIZE:
            raise ValueError(f"Packet requires exactly {PACKET_SIZE} bytes, got {len(data)}")
        src, dest, b2, sub, val = struct.unpack(">BBBBI", data)
        return cls(
            src=InstructionAddress.from_byte(src),
            dest=InstructionAddress.from_byte(dest),
            cmd_type=b2 & 0x0F,
            sub_command=sub,
            cmd_val=val,
            msg_id=(b2 >> 4) & 0x03,
        )

    # Convenience constructors -------------------------------------------------

    @classmethod
    def get_request(
        cls,
        dest: InstructionAddress,
        sub_command: int,
        msg_id: int = 0,
        src: InstructionAddress = HOST_ADDRESS,
    ) -> "Packet":
        return cls(
            src=src,
            dest=dest,
            cmd_type=CommandTypes.GETCMD,
            sub_command=sub_command,
            msg_id=msg_id,
        )

    @classmethod
    def set_request(
        cls,
        dest: InstructionAddress,
        sub_command: int,
        value: int,
        msg_id: int = 0,
        src: InstructionAddress = HOST_ADDRESS,
    ) -> "Packet":
        return cls(
            src=src,
            dest=dest,
            cmd_type=CommandTypes.SETCMD,
            sub_command=sub_command,
            cmd_val=value & 0xFFFFFFFF,
            msg_id=msg_id,
        )

    def is_response(self) -> bool:
        return self.cmd_type in (
            CommandTypes.SETCMD_RESP,
            CommandTypes.GETCMD_RESP,
            CommandTypes.SETCMD_ERR_RESP,
            CommandTypes.GETCMD_ERR_RESP,
        )

    def is_error(self) -> bool:
        return self.cmd_type in (
            CommandTypes.SETCMD_ERR_RESP,
            CommandTypes.GETCMD_ERR_RESP,
        )
