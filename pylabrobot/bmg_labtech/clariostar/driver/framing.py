"""CLARIOstar wire framing for both interfaces.

Both interfaces carry the same logical payloads (command family, command, parameters), in BMG's
framing (`pylabrobot.bmg_labtech.protocol.framing`) with the CLARIOstar's header byte, 0x0C.

FTDI: VID 0x0403 / PID 0xBB68. USB: VID 0x0483 / PID 0xA29A.
"""

from pylabrobot.bmg_labtech.protocol.framing import USB_ACK

# The CLARIOstar's header byte, in its FTDI frames and its USB packets.
FTDI_HEADER = 0x0C

# The host's acknowledgement of a multi-packet reply, asking for its continuation packets.
USB_HOST_ACK = bytes([0x00, FTDI_HEADER, USB_ACK, 0x01])
