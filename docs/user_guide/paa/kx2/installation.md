# KX2 installation

Install from a PyLabRobot checkout containing the KX2 driver:

```bash
pip install -e ".[canopen,serial]"
```

The arm uses CANopen at 500 kbit/s through its CAN USB connection. The optional barcode reader uses a separate USB-to-serial connection; omit `serial` if unused. The Python driver uses portable `python-can` adapter interfaces through `pylabrobot.io.CANopen`.

## CAN adapter

Install the driver for the adapter fitted to your arm and identify its channel. On Linux, a SocketCAN adapter normally appears as `can0`. Configure the interface before opening PLR:

```bash
sudo ip link set can0 up type can bitrate 500000
sudo ip link set can0 txqueuelen 1000
```

Pass `interface="socketcan", channel="can0"` to `KX2`. On Windows or macOS, choose the interface and channel supported by your adapter's python-can backend (for example `pcan` with its enumerated channel). OS driver installation is adapter-specific; the device driver itself does not call a vendor DLL. Use exclusive control of the bus while operating the arm.

The arm must expose nodes 1, 2, 3, 4 and 6 and support NMT state readback through node guarding or producer heartbeat. For Gold drives without node guarding, the transport briefly enables heartbeat and restores the exact original period. Setup saves the NMT state before bringing nodes operational and restores it on teardown.

## Barcode reader

The serial connection uses 9600 baud, 8 data bits, no parity, one stop bit, and no RTS/CTS. Identify the actual port:

```bash
python -m serial.tools.list_ports -v
```

Use that port (such as `/dev/ttyUSB0`, an enumerated `/dev/cu.*` device, or `COM3`) with `KX2BarcodeReader(port=...)` or `KX2(barcode_port=...)`. Install the USB-to-serial adapter manufacturer's driver if your operating system does not enumerate it. Configure the scanner for external triggering; PLR does not overwrite its mode or read-window settings.

Continue with [hello world](hello-world.ipynb).
