# adapted from
# github.com/dgretton/labspace/blob/b7be3ae9fe1aa71fbe135a3993f9c6522a014025/roarm/roarm_server.py

import time
import asyncio
import json
import serial  # type: ignore
import threading
import os
import serial.tools # type: ignore
import serial.tools.list_ports # type: ignore

class RoArmServer:
    CMD_XYZT_DIRECT_CTRL = 1041
    CMD_XYZT_GOAL_CTRL = 104

    def __init__(self, host, port, usb_ports, baud_rate, print_read=False):
        self.host = host
        self.port = port
        self.usb_ports = usb_ports
        self.baud_rate = baud_rate
        self.serial = None
        self.active_serial_port = None
        self.serial_lock = threading.RLock()
        self._prev_xyz = None

        if print_read:
            # Start the serial reading thread
            self.serial_thread = threading.Thread(target=self.read_serial)
            self.serial_thread.daemon = True
            self.serial_thread.start()

    def _ensure_serial(self):
        system_usbs = [usb.device for usb in serial.tools.list_ports.comports()]
        system_usbs.extend(['/dev/' + d for d in os.listdir('/dev') if 'usb' in d.lower()])
        if self.active_serial_port not in system_usbs:
            print(f"Active serial port {self.active_serial_port} is not in system USBs. " + \
                  "Closing serial port.")
            try:
                self.serial is not None and self.serial.close()
            except Exception:
                pass
            self.serial = None
        while self.serial is None:
            for usb_port in self.usb_ports:
                print(f'Attempting to open serial port {usb_port}.')
                try:
                    self.serial = serial.Serial(usb_port,
                                                baudrate=self.baud_rate,
                                                dsrdtr=None,
                                                timeout=.1)
                    self.serial.setRTS(False)
                    self.serial.setDTR(False)
                    self.active_serial_port = usb_port
                    print(f"Opened serial port {usb_port}.")
                    return
                except serial.SerialException:
                    print(f"Failed to open serial port {usb_port}.")
            print("Retrying in 1 second.")
            time.sleep(1)

    def send_serial(self, command):
        self._ensure_serial()
        with self.serial_lock:
            assert self.serial is not None, "Serial port not open"
            self.serial.write(command.encode() + b'\n') # ignore: union-attr

    def read_serial(self):
        self._ensure_serial()
        assert self.serial is not None, "Serial port not open"
        while True:
            with self.serial_lock:
                data = self.serial.readline().decode('utf-8')
                if data:
                    print(f"Received from arm: {data}", end='')
            time.sleep(0.1)

    async def handle_client(self, reader, writer):
        while True:
            data = await reader.read(4096)
            if not data:
                break

            message = json.loads(data.decode())
            print(f"Received from client: {message}")

            with self.serial_lock:
                response = await self.process_command(message)

            writer.write(json.dumps(response).encode())
            await writer.drain()

        writer.close()

    async def process_command(self, command):
        if command['type'] == 'move_xyzt_interp':
            return await self.move_xyzt_interp(command['x'],
                                                command['y'],
                                                command['z'],
                                                command['grip_angle'],
                                                command['speed'])
        elif command['type'] == 'move_xyzt':
            return await self.move_xyzt(command['x'],
                                        command['y'],
                                        command['z'],
                                        command['grip_angle'])
        return {"status": "error", "message": "Unknown command"}

    async def move_xyzt_interp(self, x, y, z, t, speed):
        # Convert meters to millimeters
        x_mm = x * 1000
        y_mm = y * 1000
        z_mm = z * 1000

        arm_command = json.dumps({
            "T": self.CMD_XYZT_GOAL_CTRL,
            "x": x_mm,
            "y": y_mm,
            "z": z_mm,
            "t": t,
            "spd":speed
        })

        self.send_serial(arm_command)

        # Wait for a short time to allow the arm to process the command
        await asyncio.sleep(0.1)

        assert self.serial is not None, "Serial port not open"
        data = ''
        for _ in range(4):
            data += self.serial.readline().decode('utf-8').strip()
        print(f"Received from arm: {data}", end='')

        estimated_time = self.estimate_time(x, y, z, speed)
        await asyncio.sleep(estimated_time)

        self.remember_position(x, y, z)

        print(f'sending response: {{"status": "success", "message": "Moved arm to x:{x}m, y:{y}m, z:{z}m, t:{t}rad", "response": {data}}}')
        return {"status": "success", "message": f"Moved arm to x:{x}m, y:{y}m, z:{z}m, t:{t}rad", "device_message": data}

    async def move_xyzt(self, x, y, z, t):
        # Convert meters to millimeters
        x_mm = x * 1000
        y_mm = y * 1000
        z_mm = z * 1000

        arm_command = json.dumps({
            "T": self.CMD_XYZT_DIRECT_CTRL,
            "x": x_mm,
            "y": y_mm,
            "z": z_mm,
            "t": t,
            "spd":0.25
        })

        self.send_serial(arm_command)

        # Wait for a short time to allow the arm to process the command
        await asyncio.sleep(0.1)

        assert self.serial is not None, "Serial port not open"
        data = ''
        for _ in range(4):
            data += self.serial.readline().decode('utf-8').strip()
        print(f"Received from arm: {data}", end='')

        estimated_time = self.estimate_time(x, y, z)
        await asyncio.sleep(estimated_time)

        self.remember_position(x, y, z)

        return {"status": "success", "message": f"Moved arm to x:{x}m, y:{y}m, z:{z}m, t:{t}rad"}

    def estimate_time(self, x, y, z, speed=None):
        if speed is None or speed > 0.25:
            speed = 0.25
        if speed < 0.001:
            speed = 0.001
        if self._prev_xyz is None:
            return 10
        prev_x, prev_y, prev_z = self._prev_xyz
        estimated_time = 1
        estimated_time += ((x - prev_x)**2 + (y - prev_y)**2 + (z - prev_z)**2)**0.5 / speed
        # add extra time if the arm has a negative x coordinate (reaching behind its base)
        # and the y coordinate has changed sign
        if x < 0 and prev_x >= 0 and y*prev_y < 0:
            estimated_time += 8
        estimated_time = min(estimated_time, 60)
        return estimated_time

    def remember_position(self, x, y, z):
        self._prev_xyz = (x, y, z)

    async def run(self):
        server = await asyncio.start_server(
            self.handle_client, self.host, self.port)

        addr = server.sockets[0].getsockname()
        print(f'Serving on {addr}')

        async with server:
            await server.serve_forever()

if __name__ == "__main__":
    import sys
    host = '0.0.0.0' if '--host' not in sys.argv else sys.argv[sys.argv.index('--host')+1]
    usb_ports = ['/dev/ttyUSB0', '/dev/tty.usbserial-10'] if '--usb' not in sys.argv \
      else sys.argv[sys.argv.index('--usb')+1].split(',')
    roarm_server = RoArmServer(host, 8659, usb_ports, 115200)
    asyncio.run(roarm_server.run())
