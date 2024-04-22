packets = []
for i in range(33):
    packet = bytearray([0xaa])  # First byte is constant
    packet.append(i & 0xFF)  # Second byte increments
    packet.append(0x0e)  # Third byte is constant
    packet.append(0x0e + (i & 0xFF))  # Fourth byte increments from 0x0e
    packet.extend([0x00] * 8)  # Remaining 8 bytes are zeros
    packets.append(packet)

for i, packet in enumerate(packets):
    payload_str = ' '.join(format(x, '02x') for x in packet)  # Convert bytes to hexadecimal string
    print(f"{i:02d} - {payload_str}")
