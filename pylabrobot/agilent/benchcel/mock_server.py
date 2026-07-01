"""In-process mock server for the Agilent BenchCel 4R TCP protocol."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import struct
from typing import Dict, Optional

from .driver import (
  AXIS_GRIPPER,
  AXIS_THETA,
  AXIS_X,
  AXIS_Z,
  CMD_ACK,
  CMD_AXIS_BOUNDS,
  CMD_CURRENT_POSITION,
  CMD_ERROR,
  CMD_GENERAL_STATUS,
  CMD_HOME,
  CMD_HOME_MOTORS,
  CMD_JOG,
  CMD_LOAD_PLATE,
  CMD_MOVE_TO_TARGET,
  CMD_PICK,
  CMD_PLACE,
  CMD_ROBOT_GRIPPER,
  CMD_SAVE_TEACHPOINT,
  CMD_SENSOR_STATUS,
  CMD_SET_LABWARE,
  CMD_SETTINGS_COMMIT,
  CMD_STACKER_GRIPPER,
  CMD_UNLOAD_PLATE,
  AxisBoundsResponse,
  Frame,
  Teachpoint,
  make_frame,
  parse_frame_from_buffer,
)
from .labware import DEVICE_PAYLOAD_LENGTH, BenchCelLabwareSettings

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class _Pose:
  theta: float = 0.0
  x: float = 0.0
  z: float = 10.0
  gripper: float = -1.0


_DEFAULT_BOUNDS = AxisBoundsResponse(
  theta_min=-115.0,
  x_min=-360.9,
  z_min=-1.5,
  gripper_min=-1.5,
  theta_max=115.0,
  x_max=360.9,
  z_max=104.0,
  gripper_max=11.0,
  raw_payload=b"",
  float_values=(-115.0, -360.9, -1.5, -1.5, 115.0, 360.9, 104.0, 11.0),
)


class BenchCelMockServer:
  """Small asyncio TCP server emulating the BenchCel binary protocol.

  The mock is wire-compatible for the commands implemented by
  :class:`~pylabrobot.agilent.benchcel.driver.BenchCel4RBackend` and is
  intended for backend tests and manual protocol debugging.
  """

  def __init__(
    self,
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    close_on_home_motors: bool = True,
  ):
    self.host = host
    self.port = port
    self.close_on_home_motors = close_on_home_motors
    self._server: Optional[asyncio.AbstractServer] = None
    self._pose = _Pose()
    self._bounds = _DEFAULT_BOUNDS
    self._teachpoints: Dict[int, Teachpoint] = {}
    self._plate_in_gripper = False
    self.plate_presence = [0, 1, 128, 118]
    self.air_pressure = [56, 56, 45, 47]
    self.stacker_grippers_open = [False, False, False, False]
    self.labware: Optional[BenchCelLabwareSettings] = None
    self.received_frames: list[Frame] = []

  async def __aenter__(self) -> "BenchCelMockServer":
    await self.start()
    return self

  async def __aexit__(self, exc_type, exc, tb) -> None:
    await self.stop()

  async def start(self) -> None:
    """Start accepting TCP connections."""
    if self._server is not None:
      return
    self._server = await asyncio.start_server(self._handle_client, host=self.host, port=self.port)
    sockets = list(self._server.sockets or [])
    if sockets:
      self.port = sockets[0].getsockname()[1]
    logger.info("BenchCelMockServer listening on %s:%d", self.host, self.port)

  async def stop(self) -> None:
    """Stop the server and wait for its listening socket to close."""
    if self._server is None:
      return
    self._server.close()
    await self._server.wait_closed()
    self._server = None

  async def serve_forever(self) -> None:
    """Run until cancelled."""
    if self._server is None:
      await self.start()
    assert self._server is not None
    await self._server.serve_forever()

  async def _handle_client(
    self,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
  ) -> None:
    buffer = bytearray()
    try:
      while not reader.at_eof():
        chunk = await reader.read(4096)
        if not chunk:
          break
        buffer.extend(chunk)
        while True:
          frame = parse_frame_from_buffer(buffer)
          if frame is None:
            break
          self.received_frames.append(frame)
          close_after_response = await self._handle_frame(frame, writer)
          await writer.drain()
          if close_after_response:
            writer.close()
            await writer.wait_closed()
            return
    finally:
      if not writer.is_closing():
        writer.close()
        await writer.wait_closed()

  async def _handle_frame(self, frame: Frame, writer: asyncio.StreamWriter) -> bool:
    """Handle one incoming frame. Return True to close the TCP session."""
    if frame.command_id == CMD_HOME_MOTORS:
      self._pose = _Pose()
      if not self.close_on_home_motors:
        self._write_ack(writer, CMD_HOME_MOTORS)
      return self.close_on_home_motors

    if frame.command_id == CMD_HOME:
      self._pose = _Pose()
      self._write_ack(writer, CMD_HOME)
      return False

    if frame.command_id == CMD_LOAD_PLATE:
      stacker = self._read_stacker_payload(frame, writer)
      if stacker is not None:
        self._write_ack(writer, CMD_LOAD_PLATE, stacker)
      return False

    if frame.command_id == CMD_UNLOAD_PLATE:
      stacker = self._read_stacker_payload(frame, writer)
      if stacker is not None:
        self._write_ack(writer, CMD_UNLOAD_PLATE, stacker)
      return False

    if frame.command_id in (CMD_PICK, CMD_PLACE):
      target_id = self._read_target_payload(frame, writer)
      if target_id is None:
        return False
      self._move_to_target_id(target_id, approach_height=0.0)
      self._plate_in_gripper = frame.command_id == CMD_PICK
      self._pose.gripper = 5.0 if self._plate_in_gripper else -1.0
      self._write_ack(writer, frame.command_id)
      return False

    if frame.command_id == CMD_MOVE_TO_TARGET:
      if len(frame.payload) != 10:
        self._write_error(writer, "Malformed move-to-target payload")
        return False
      _, target_id, _, approach_height = struct.unpack("<BBff", frame.payload)
      self._move_to_target_id(target_id, approach_height=approach_height)
      self._write_ack(writer, CMD_MOVE_TO_TARGET)
      return False

    if frame.command_id == CMD_JOG:
      self._handle_jog(frame, writer)
      return False

    if frame.command_id == CMD_STACKER_GRIPPER:
      if len(frame.payload) != 2 or frame.payload[0] not in (0, 1, 2, 3):
        self._write_error(writer, "Malformed stacker-gripper payload")
        return False
      self.stacker_grippers_open[frame.payload[0]] = bool(frame.payload[1])
      self._write_ack(writer, CMD_STACKER_GRIPPER)
      return False

    if frame.command_id == CMD_ROBOT_GRIPPER:
      if frame.payload == b"\x01":
        self._pose.gripper = -1.0
        self._plate_in_gripper = False
      elif frame.payload == b"\x00":
        self._pose.gripper = 5.0
      else:
        self._write_error(writer, "Malformed gripper payload")
        return False
      self._write_ack(writer, CMD_ROBOT_GRIPPER)
      return False

    if frame.command_id == CMD_SAVE_TEACHPOINT:
      self._save_teachpoint(frame, writer)
      return False

    if frame.command_id == CMD_SET_LABWARE:
      self._handle_set_labware(frame, writer)
      return False

    if frame.command_id == CMD_SETTINGS_COMMIT:
      # Echo the empty commit frame, mirroring observed device behavior.
      writer.write(make_frame(CMD_SETTINGS_COMMIT))
      return False

    if frame.command_id == CMD_SENSOR_STATUS:
      if len(frame.payload) != 1 or frame.payload[0] not in (0, 1, 2, 3):
        self._write_error(writer, "Invalid stacker index")
        return False
      writer.write(make_frame(CMD_SENSOR_STATUS, self._sensor_payload(frame.payload[0])))
      return False

    if frame.command_id == CMD_GENERAL_STATUS:
      writer.write(make_frame(CMD_GENERAL_STATUS, self._general_status_payload()))
      return False

    if frame.command_id == CMD_AXIS_BOUNDS:
      writer.write(make_frame(CMD_AXIS_BOUNDS, struct.pack("<8f", *self._bounds.float_values)))
      return False

    if frame.command_id == CMD_CURRENT_POSITION:
      selector = frame.payload[0] if frame.payload else 0
      writer.write(make_frame(CMD_CURRENT_POSITION, self._current_position_payload(selector)))
      return False

    self._write_error(writer, f"Unknown command 0x{frame.command_id:02x}")
    return False

  def _read_stacker_payload(self, frame: Frame, writer: asyncio.StreamWriter) -> Optional[int]:
    try:
      return self._validate_stacker_payload(frame.payload)
    except ValueError as exc:
      self._write_error(writer, str(exc))
      return None

  def _read_target_payload(self, frame: Frame, writer: asyncio.StreamWriter) -> Optional[int]:
    try:
      return self._validate_target_payload(frame.payload)
    except ValueError as exc:
      self._write_error(writer, str(exc))
      return None

  def _write_ack(self, writer: asyncio.StreamWriter, command_id: int, *extra: int) -> None:
    writer.write(make_frame(CMD_ACK, bytes([command_id, *extra])))

  def _write_error(self, writer: asyncio.StreamWriter, message: str) -> None:
    writer.write(make_frame(CMD_ERROR, message.encode("ascii", errors="replace")))

  @staticmethod
  def _validate_stacker_payload(payload: bytes) -> int:
    if len(payload) < 2 or payload[0] != 0x01 or payload[1] not in (0, 1, 2, 3):
      raise ValueError(f"invalid stacker payload: {payload.hex()}")
    return payload[1]

  @staticmethod
  def _validate_target_payload(payload: bytes) -> int:
    if len(payload) != 4 or payload[0] != 0x01 or payload[2:] != b"\x00\x01":
      raise ValueError(f"invalid target payload: {payload.hex()}")
    return payload[1]

  def _handle_jog(self, frame: Frame, writer: asyncio.StreamWriter) -> None:
    if len(frame.payload) != 5:
      self._write_error(writer, "Malformed jog payload")
      return
    axis, delta = struct.unpack("<Bf", frame.payload)
    if axis == AXIS_THETA:
      new_value = self._pose.theta + delta
      if not self._bounds.theta_min <= new_value <= self._bounds.theta_max:
        self._write_error(writer, "Theta position out of bounds")
        return
      self._pose.theta = new_value
    elif axis == AXIS_X:
      new_value = self._pose.x + delta
      if not self._bounds.x_min <= new_value <= self._bounds.x_max:
        self._write_error(writer, "X position out of bounds")
        return
      self._pose.x = new_value
    elif axis == AXIS_Z:
      new_value = self._pose.z + delta
      if not self._bounds.z_min <= new_value <= self._bounds.z_max:
        self._write_error(writer, "Z position out of bounds")
        return
      self._pose.z = new_value
    elif axis == AXIS_GRIPPER:
      new_value = self._pose.gripper + delta
      if not self._bounds.gripper_min <= new_value <= self._bounds.gripper_max:
        self._write_error(writer, "Gripper position out of bounds")
        return
      self._pose.gripper = new_value
    else:
      self._write_error(writer, f"Unknown jog axis {axis}")
      return
    self._write_ack(writer, CMD_JOG)

  def _save_teachpoint(self, frame: Frame, writer: asyncio.StreamWriter) -> None:
    if len(frame.payload) != 27:
      self._write_error(writer, "Malformed save-teachpoint payload")
      return
    values = struct.unpack("<BfffBBfff", frame.payload)
    teachpoint = Teachpoint(
      teachpoint_id=values[0],
      theta=values[1],
      x=values[2],
      z=values[3],
      something_above_this_point=bool(values[4]),
      respect_approach_height_when_not_holding_plate=bool(values[5]),
      approach_height=values[6],
      cavity_depth=values[7],
      gripper_open_limit=values[8],
    )
    self._teachpoints[teachpoint.teachpoint_id] = teachpoint

  def _handle_set_labware(self, frame: Frame, writer: asyncio.StreamWriter) -> None:
    if len(frame.payload) != DEVICE_PAYLOAD_LENGTH:
      self._write_error(writer, "Malformed labware settings payload")
      return
    settings = BenchCelLabwareSettings.from_device_payload(frame.payload)
    # The device rejects geometry where the stack hold position is not above the
    # plate hold position (observed "too close" rejections had stack <= plate).
    if settings.gripper_holding_stack_position <= settings.gripper_holding_plate_position:
      self._write_error(writer, "The labware gripper positions are too close")
      return
    self.labware = settings

  def _move_to_target_id(self, target_id: int, approach_height: float) -> None:
    if target_id in (0, 1, 2, 3):
      self._pose.theta = 0.0
      self._pose.x = (-270.0, -90.0, 90.0, 270.0)[target_id]
      self._pose.z = max(self._bounds.z_min, min(self._bounds.z_max, approach_height))
      return

    teachpoint = self._teachpoints.get(target_id)
    if teachpoint is None:
      self._pose.theta = 0.0
      self._pose.x = 0.0
      self._pose.z = 10.0
      return

    self._pose.theta = teachpoint.theta
    self._pose.x = teachpoint.x
    self._pose.z = max(
      self._bounds.z_min,
      min(self._bounds.z_max, teachpoint.z + approach_height),
    )

  def _sensor_payload(self, stacker_index: int) -> bytes:
    return struct.pack(
      "<BB8H",
      stacker_index,
      0x08,
      self.air_pressure[stacker_index],
      1 if stacker_index % 2 == 0 else 0,
      0 if stacker_index % 2 == 0 else 1,
      240 + stacker_index,
      self.plate_presence[stacker_index],
      241 + stacker_index,
      0,
      1,
    )

  def _general_status_payload(self) -> bytes:
    payload = bytearray(66)
    struct.pack_into("<f", payload, 4, self._pose.theta)
    struct.pack_into("<f", payload, 12, self._pose.x)
    struct.pack_into("<f", payload, 20, self._pose.z)
    struct.pack_into("<f", payload, 28, self._pose.gripper)
    return bytes(payload)

  def _current_position_payload(self, selector: int) -> bytes:
    payload = bytearray(33)
    payload[0] = selector
    struct.pack_into("<f", payload, 1, self._pose.theta)
    struct.pack_into("<f", payload, 5, self._pose.x)
    struct.pack_into("<f", payload, 9, self._pose.z)
    struct.pack_into("<f", payload, 13, self._pose.gripper)
    return bytes(payload)


def build_arg_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description="Run a mock Agilent BenchCel 4R TCP server")
  parser.add_argument("--host", default="127.0.0.1")
  parser.add_argument("--port", type=int, default=7612)
  parser.add_argument("--verbose", action="store_true")
  return parser


async def _amain() -> None:
  args = build_arg_parser().parse_args()
  logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
  server = BenchCelMockServer(host=args.host, port=args.port)
  await server.start()
  print(f"BenchCelMockServer listening on {server.host}:{server.port}")
  try:
    await server.serve_forever()
  except asyncio.CancelledError:
    pass


def main() -> None:
  asyncio.run(_amain())


if __name__ == "__main__":
  main()
