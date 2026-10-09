import typing

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.hid import HID

# Budget for one complete command exchange: every packet write and the whole response
# reconciliation share it. It bounds the exchange as one operation, rather than giving each
# read its own budget, so a lost connection fails the command instead of each step in turn.
_COMMAND_TIMEOUT = 3


class InhecoTECControlBox(HasLifespan):
  def __init__(
    self,
    vid=0x03EB,
    pid=0x2023,
    serial_number=None,
  ):
    super().__init__()
    self._transaction_lock = anyio.Lock()
    self.io = HID(
      human_readable_device_name="Inheco Control Box",
      vid=vid,
      pid=pid,
      serial_number=serial_number,
      max_input_report_size=64,
    )

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Own the HID transport for this lifespan.

    If HID entry fails, ensure that libusb drivers were installed as per docs.
    """
    await super()._enter_lifespan(stack)

    await stack.enter_async_context(self.io.lifespan())

  @typing.no_type_check
  def _generate_packets(self, msg):
    """Generate packets for the given message.

    Splits the message into packets of 7 bytes each. The last packet contains the checksum.
    """

    ch_array1 = [None] * 128
    report_buffer = [0] * 9
    crc = 161
    for i, char in enumerate(msg):
      ch_array1[i] = char if ord(char) <= ord("`") or char == "#" else chr(ord(char) - 32)
      if ch_array1[i] != "#":
        crc = self._crc8(ord(ch_array1[i]), crc=crc)
    ch_array1[len(msg)] = "w" if crc in (35, 0) else chr(crc)
    num1 = len(msg) + 1
    if num1 < 2:
      report_buffer[0] = 0
    if num1 <= 8:
      for i in range(num1):
        report_buffer[i + 1] = ord(ch_array1[i])
      return [report_buffer]
    else:
      num2 = (num1 - 1) // 7  # number of packets
      command = []
      for index1 in range(num2 + 1):
        for index2 in range(7):
          if index2 + index1 * 7 >= num1:
            report_buffer[index2 + 1] = 0
          else:
            report_buffer[index2 + 1] = ord(ch_array1[index2 + index1 * 7])
        if index1 != num2:
          report_buffer[8] = ord("#")
        else:
          if (num2 + 1) * 7 + 1 == num1:
            report_buffer[8] = ord(ch_array1[num1 - 1])
            if report_buffer[8] > 96:
              report_buffer[8] -= 32
          else:
            report_buffer[8] = 0
        command.append(report_buffer)
      return command

  def _crc8(self, data, crc: int) -> int:
    """Meme crc8 implementation"""
    num = 8
    while num > 0:
      if ((data ^ crc) & 1) == 1:
        crc ^= 24
        crc >>= 1
        crc |= 128
      else:
        crc >>= 1
      data >>= 1
      num -= 1
    return crc

  async def _read_until_end(self, timeout: int) -> str:
    """Read until a packet ends with a \\x00 byte. May read multiple packets.

    The caller's exchange deadline bounds this loop across all packets.
    """
    response = b""
    while True:
      report = await self.io.read_to_boundary()
      if not report.started_at_boundary or not report.ended_at_boundary:
        raise RuntimeError("Incomplete Inheco HID report")
      packet = report.data
      if packet.endswith(b"\x00"):
        response += packet.rstrip(b"\x00")  # strip trailing \x00's
        break
      elif packet.endswith(b"#"):
        response += packet[:-1]
        continue
      else:
        # I have never seen this happen, commands always end with \x00 or '#'
        print("weird packet, please report", packet)
        response += packet

    return response.decode("unicode_escape")

  async def _read_response(self, command: str, timeout: int) -> str:
    """Read the response for a given command.

    "The MTC/STC replies to the first four characters of every command with a modified echo. The
    modification changes the capitals of the commands to small letters. i.e. the reply to 5ASE1
    is 5ase0. Therefore it is easy to identify correct answers to the commands. This feature may
    increase integrity of the communication."

    The caller's exchange deadline bounds this loop, so replies to earlier commands are
    skipped until the matching echo arrives or that deadline expires.
    """

    while True:
      response = await self._read_until_end(timeout=timeout)

      if response[:4] == command[:4].lower():
        return response

  async def send_command(self, command: str, timeout: int = _COMMAND_TIMEOUT):
    """Send a command to the device and return the response.

    Run one complete command exchange as a single serialized transaction. Waiting for the
    transaction and the checkpoint before the first write stay cancellable. Once a write may
    have committed, every remaining packet write and the whole response reconciliation are
    shielded under one deadline, so cancellation cannot abandon a command the device has
    already received. The transaction is released before the final checkpoint, so a pending
    external cancellation wins over a stored timeout.
    """
    # Pure computation, so it happens before the transaction is taken.
    packets = self._generate_packets(command)

    await anyio.lowlevel.checkpoint_if_cancelled()
    await self._transaction_lock.acquire()
    try:
      # Intentionally redundant with the cancellable acquire above on a contended lock, and
      # the only stop on an uncontended one: anyio's fast-path `acquire` ends in a shielded
      # checkpoint that swallows a cancellation arriving while the task waits there, so the
      # pre-lock checkpoint cannot catch it. Nothing pins this line alone, so a green suite
      # is not evidence it is dead; deleting it does cause a real post-cancellation write.
      # The pre-lock checkpoint above is unpinned alone for the mirror-image reason: on a
      # contended lock this one catches the same cancellation, so deleting either one by
      # itself leaves the suite green. The two are complementary across the contended and
      # uncontended paths, not duplicates; keep both.
      # Do not remove it, above all if the acquire is ever shielded.
      await anyio.lowlevel.checkpoint_if_cancelled()
      response = ""
      with anyio.CancelScope(shield=True):
        with anyio.move_on_after(timeout) as exchange_scope:
          for packet in packets:
            await self.io.write(bytes(packet[1:]), report_id=bytes(packet[0]))

          response = await self._read_response(command, timeout=timeout)
        exchange_timed_out = exchange_scope.cancelled_caught
    finally:
      self._transaction_lock.release()
    await anyio.lowlevel.checkpoint()

    if exchange_timed_out:
      raise TimeoutError("Timeout while waiting for response from device.")

    if response[4] != "0":
      raise RuntimeError(f"Error response from device: {response}")

    return response[5:-1]  # cut off command, error status, and final checksum byte

  async def set_touchscreen(self, active: bool):
    await self.send_command(f"0ADD{1 if active else 0}")
    await self.reset_action_display()

  async def reset_action_display(self):
    return await self.send_command("0ASD")
