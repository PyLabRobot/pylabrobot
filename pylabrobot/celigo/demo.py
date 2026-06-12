"""Runnable demo: Celigo setup + plate load/unload, against a simulated board.

There is no hardware attached, so this drives a :class:`MockBoard` that speaks the real
USB-IO wire protocol (framing from :mod:`pylabrobot.celigo.packets`) and auto-answers
each command. It lets you *see* the driver issue a faithful command sequence and decode
the responses.

The load/unload choreography: set X/Y move currents, drive the stage out to the load
station (large relative moves), then absolute moves back under the optics, raise Z to
imaging height, and read the plate barcode. ``Door`` on this instrument is the stage
moving to the eject station, not a separate motor.

Run with::

    python -m pylabrobot.celigo.demo
"""

from __future__ import annotations

import struct

from pylabrobot.celigo import ezstepper
from pylabrobot.celigo.controller import CeligoController
from pylabrobot.celigo.packets import (
  ACK_STATE,
  RX_HEADER_SIZE,
  IO_CTLR_CMDS,
  fletcher16,
)

# Motor axis designations: 1=X, 2=Y, 3=Z/focus, 4=filter.
X_AXIS, Y_AXIS, Z_AXIS, FILTER_AXIS = 1, 2, 3, 4


class MockBoard:
  """A fake transport that answers each request the way the board would.

  Implements ``write`` / ``read`` / ``purge``. On each ``write`` it parses the request
  header and queues a valid response packet (ack OK, echoed cmd/seq, a plausible
  payload). Motor queries get an OEM-wrapped ``/0`<data>`` reply.
  """

  def __init__(self):
    self._out = b""
    self.log = []  # (cmd, ez_string_or_None)
    self.dac = {}  # channel -> last written raw value

  # -- transport interface ---------------------------------------------------
  def write(self, data: bytes) -> int:
    cmd = IO_CTLR_CMDS(data[0])
    seq = struct.unpack_from(">i", data, 1)[0]
    payload = data[11:]
    self._out += self._response(cmd, seq, payload)
    return len(data)

  def read(self, n: int) -> bytes:
    chunk, self._out = self._out[:n], self._out[n:]
    return chunk

  def purge(self) -> None:
    self._out = b""

  # -- response synthesis ----------------------------------------------------
  def _response(self, cmd: IO_CTLR_CMDS, seq: int, payload: bytes) -> bytes:
    body = b""
    if cmd == IO_CTLR_CMDS.CONTROLLER_STATUS:
      body = struct.pack(">II", 0, 0)  # status=EMPTY, ext=NO_ERROR
    elif cmd == IO_CTLR_CMDS.SEND_MOTOR_CONFIG:
      # 8 UARTs x (status byte + 4 slots); X/Y/Z/filter present on UART 0.
      buf = bytearray()
      for u in range(8):
        buf.append(0)
        for s in range(4):
          buf.append(s + 1 if u == 0 and s < 4 else 127)
      body = bytes(buf)
    elif cmd in (IO_CTLR_CMDS.MOTOR_CMD_QUERY_WLEN, IO_CTLR_CMDS.MOTOR_CMD_QUERY):
      ez = self._decode_motor(cmd, payload)
      self.log.append((cmd.name, ez))
      data = self._motor_reply(ez)
      body = struct.pack(">H", 0) + struct.pack(">H", len(data)) + data
    elif cmd == IO_CTLR_CMDS.READ_BARCODE_MSG:
      body = b"3603-A"  # pretend the reader returns a plate id
      self.log.append((cmd.name, None))
    elif cmd == IO_CTLR_CMDS.WRITE_DA_CHANNEL:
      ch, val = struct.unpack_from(">HH", payload, 0)
      self.dac[ch] = val
      self.log.append((cmd.name, None))
    elif cmd == IO_CTLR_CMDS.GET_ANALOG_OUT_VALUE:
      (ch,) = struct.unpack_from(">H", payload, 0)
      body = struct.pack(">HH", ch, self.dac.get(ch, 0))  # echo + stored value
      self.log.append((cmd.name, None))
    else:
      self.log.append((cmd.name, None))
    return _rx_packet(cmd, seq, body)

  @staticmethod
  def _decode_motor(cmd: IO_CTLR_CMDS, payload: bytes) -> str:
    if cmd == IO_CTLR_CMDS.MOTOR_CMD_QUERY_WLEN:
      # STX + addr + '1' + tokens + ETX + xor  -> reconstruct "/<addr><tokens>R"
      text = payload.decode("latin-1")
      try:
        inner = text[text.index("\x02") + 1 : text.index("\x03")]
        return "/" + inner[0] + inner[2:]  # drop the device-index '1'
      except ValueError:
        return text
    return payload.rstrip(b"\x00").decode("latin-1")

  @staticmethod
  def _motor_reply(ez: str) -> bytes:
    # encoder query "?8" -> a position; everything else -> ready/no-error.
    data = "0`4491" if "?8" in ez else "0`"
    return ("\x02" + data + "\x03q").encode("latin-1")


def _rx_packet(cmd: IO_CTLR_CMDS, seq: int, body: bytes) -> bytes:
  header = bytearray(RX_HEADER_SIZE)
  header[0] = ACK_STATE.ACK_OK
  header[1] = int(cmd)
  struct.pack_into(">i", header, 2, seq)
  struct.pack_into(">i", header, 6, len(body))
  a, b = fletcher16(header, 10)
  header[10], header[11] = a, b
  return bytes(header) + body


# -- choreography ------------------------------------------------------------


def _move_out(ctrl: CeligoController, axis: int, command: str, label: str):
  print(f"  [{label}] {command.strip()!r}")
  ctrl.send_ezstepper(command + "\r" if not command.endswith("\r") else command)


def setup(ctrl: CeligoController):
  print("SETUP")
  status, ext = ctrl.get_status()
  print(f"  controller status = {status!r}, ext = {ext}")
  motors = ctrl.get_motor_configuration()
  print(f"  motors found: {[(m.uart_index, m.motor_index) for m in motors]}")
  print(f"  A1 parked encoder (Y ?8) = {ctrl.get_encoder_position(Y_AXIS)}")


def load_plate(ctrl: CeligoController):
  print("LOAD PLATE  (stage -> load station -> back under optics -> Z up)")
  # 1) relax to move currents
  _move_out(
    ctrl,
    X_AXIS,
    ezstepper.single_command(ezstepper.EZCommand.SET_MOVE_CURRENT, 65, X_AXIS),
    "X current",
  )
  _move_out(
    ctrl,
    Y_AXIS,
    ezstepper.single_command(ezstepper.EZCommand.SET_MOVE_CURRENT, 55, Y_AXIS),
    "Y current",
  )
  # 2) drive stage out to the load/eject station
  _move_out(ctrl, X_AXIS, _vmove(X_AXIS, ezstepper.EZCommand.MOVE_NEGATIVE, 25000), "X out")
  _move_out(ctrl, Y_AXIS, _vmove(Y_AXIS, ezstepper.EZCommand.MOVE_POSITIVE, 25000), "Y out")
  # 3) (operator places plate) ... move back under the optics
  _move_out(ctrl, Y_AXIS, ezstepper.move_absolute(Y_AXIS, 5335), "Y in")
  _move_out(ctrl, X_AXIS, ezstepper.move_absolute(X_AXIS, -136), "X in")
  _move_out(ctrl, Y_AXIS, ezstepper.move_absolute(Y_AXIS, 4502), "Y settle")
  # 4) raise Z to imaging height
  _move_out(
    ctrl,
    Z_AXIS,
    ezstepper.multi_command(
      [
        (ezstepper.EZCommand.SET_HOLD_CURRENT, 25),
        (ezstepper.EZCommand.SET_VELOCITY, 25197),
        (ezstepper.EZCommand.SET_ACCELERATION, 25197),
        (ezstepper.EZCommand.MOVE_ABSOLUTE, 10337),
      ],
      Z_AXIS,
    ),
    "Z up",
  )
  # 5) read barcode
  ctrl.send_barcode_command("0004")
  print(f"  barcode = {ctrl.read_barcode_response()!r}")


def unload_plate(ctrl: CeligoController):
  print("UNLOAD PLATE  (Z down -> stage out to eject)")
  _move_out(ctrl, Z_AXIS, ezstepper.move_absolute(Z_AXIS, 0), "Z down")
  _move_out(ctrl, X_AXIS, _vmove(X_AXIS, ezstepper.EZCommand.MOVE_NEGATIVE, 25000), "X eject")
  _move_out(ctrl, Y_AXIS, _vmove(Y_AXIS, ezstepper.EZCommand.MOVE_POSITIVE, 25000), "Y eject")


def _vmove(axis: int, move_cmd, steps: int) -> str:
  return ezstepper.multi_command(
    [
      (ezstepper.EZCommand.SET_VELOCITY, 3543),
      (ezstepper.EZCommand.SET_ACCELERATION, 3543),
      (move_cmd, steps),
    ],
    axis,
  )


def main():
  board = MockBoard()
  ctrl = CeligoController(board)
  print("=== Celigo setup + load/unload demo (simulated board) ===\n")
  setup(ctrl)
  print()
  load_plate(ctrl)
  print()
  unload_plate(ctrl)
  print(f"\nDone. {len(board.log)} commands issued to the (mock) board.")


if __name__ == "__main__":
  main()
