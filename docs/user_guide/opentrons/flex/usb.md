# Flex over USB

The Flex USB-B connection carries the robot's HTTP API over serial, so no IP
address is needed. Close the Opentrons App before connecting: it uses the same port.

Install serial support:

```sh
pip install 'pylabrobot[serial]'
python -m serial.tools.list_ports
```

Use the Flex's port from that list (for example `/dev/cu.usbmodem1201` on macOS;
typically `/dev/ttyACM0` on Linux or `COM3` on Windows):

```python
from pylabrobot.opentrons import Flex

flex = Flex(serial_port="/dev/cu.usbmodem1201")
await flex.connect()
try:
    print(flex.robot_model, flex.software_version)
finally:
    await flex.disconnect()
```

Run this in an async context or a notebook. `connect()` reads health without
moving the robot. Supply either `serial_port` or `host`, not both. The usual
`setup()` and pipette operations work through the selected transport;
`setup()` homes the robot.

`HTTPSerial` reuses PLR's HTTP logging, errors, and capture/replay. A serial
timeout closes the port without retrying; check the robot's state before
reconnecting or repeating a command.
