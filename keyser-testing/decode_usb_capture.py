"""Decode Tecan EVO USB capture from Wireshark.

Extracts and decodes Tecan firmware commands from a USB pcap file.
Save your Wireshark capture as a .pcap or .pcapng file, then run:

  python keyser-testing/decode_usb_capture.py <capture_file>

Prerequisites:
  pip install pyshark   (or: pip install scapy)

Alternative: Export from Wireshark as CSV/JSON and use the manual decode below.

If you don't want to install pyshark, you can also:
1. In Wireshark, filter by: usb.transfer_type == 0x03 (bulk transfer)
2. Look for packets to/from the Tecan device (VID=0x0C47, PID=0x4000)
3. Export packet bytes — Tecan commands start with 0x02 (STX) and end with 0x00 (NUL)
4. The format is: STX + module(2 chars) + command(3 chars) + params(comma-sep) + NUL
"""

import sys


def decode_tecan_packet(data: bytes, direction: str = "?") -> str:
  """Decode a single Tecan USB packet.

  Args:
    data: Raw packet bytes
    direction: "TX" (host→device) or "RX" (device→host)

  Returns:
    Human-readable string describing the command/response.
  """
  if len(data) < 4:
    return f"[{direction}] Too short: {data.hex()}"

  # Tecan commands: STX(0x02) + module + command + params + NUL(0x00)
  if data[0] == 0x02 and data[-1] == 0x00:
    payload = data[1:-1].decode("ascii", errors="replace")
    module = payload[:2]
    cmd_and_params = payload[2:]

    # Split command (3 chars) from params
    cmd = cmd_and_params[:3]
    params = cmd_and_params[3:] if len(cmd_and_params) > 3 else ""

    return f"[{direction}] {module} {cmd} {params}"

  # Tecan responses: STX(0x02) + module(2) + status_byte + data + NUL(0x00)
  if data[0] == 0x02:
    payload = data[1:-1] if data[-1] == 0x00 else data[1:]
    if len(payload) >= 3:
      module = payload[:2].decode("ascii", errors="replace")
      status = payload[2] ^ 0x80  # bit 7 is parity
      rest = payload[3:].decode("ascii", errors="replace") if len(payload) > 3 else ""
      return f"[{direction}] {module} status={status} data={rest}"

  return f"[{direction}] Raw: {data.hex()}"


def decode_hex_dump(hex_string: str, direction: str = "?") -> str:
  """Decode from a hex string (e.g., copied from Wireshark hex view)."""
  clean = hex_string.replace(" ", "").replace("\n", "").replace(":", "")
  data = bytes.fromhex(clean)
  return decode_tecan_packet(data, direction)


# ── Interactive mode ──
def interactive():
  """Paste hex strings from Wireshark to decode them."""
  print("Tecan USB Packet Decoder")
  print("Paste hex bytes from Wireshark (e.g., '02 43 35 50 49 41 00')")
  print("Prefix with > for TX (host→device) or < for RX (device→host)")
  print("Type 'q' to quit.\n")

  while True:
    line = input("hex> ").strip()
    if line.lower() == "q":
      break
    if not line:
      continue

    direction = "?"
    if line.startswith(">"):
      direction = "TX"
      line = line[1:].strip()
    elif line.startswith("<"):
      direction = "RX"
      line = line[1:].strip()

    try:
      print(f"  {decode_hex_dump(line, direction)}")
    except Exception as e:
      print(f"  Error: {e}")
    print()


if __name__ == "__main__":
  if len(sys.argv) > 1 and sys.argv[1] != "--interactive":
    # Try pyshark for pcap files
    try:
      import pyshark
      cap = pyshark.FileCapture(sys.argv[1], display_filter="usb.transfer_type == 0x03")
      for pkt in cap:
        try:
          data = bytes.fromhex(pkt.data.usb_capdata.replace(":", ""))
          direction = "TX" if hasattr(pkt, "usb") and pkt.usb.endpoint_address_direction == "0" else "RX"
          decoded = decode_tecan_packet(data, direction)
          print(f"  {float(pkt.sniff_timestamp):.3f}  {decoded}")
        except Exception:
          continue
    except ImportError:
      print("pyshark not installed. Use interactive mode:")
      print(f"  python {sys.argv[0]} --interactive")
      print(f"\nOr install: pip install pyshark")
  else:
    interactive()
