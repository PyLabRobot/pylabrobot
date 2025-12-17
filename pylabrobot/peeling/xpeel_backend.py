import asyncio
import logging
import time
from dataclasses import dataclass
from typing import List, Optional

import serial

from pylabrobot.io.serial import Serial


class PlatePeeler:
    """
    Client for the Azenta Life Sciences Automated Plate Seal Remover (XPeel)
    RS-232 interface. All commands use lowercase ASCII, begin with '*' and end
    with <CR><LF>.
    """

    DEFAULT_PORT = "COM3"
    BAUDRATE = 9600
    RESPONSE_TIMEOUT = 20.0

    @dataclass(frozen=True)
    class ErrorInfo:
        code: int
        description: str

    ERROR_DEFINITIONS = {
        0: ErrorInfo(0, "No error"),
        1: ErrorInfo(1, "Conveyor motor stalled"),
        2: ErrorInfo(2, "Elevator motor stalled"),
        3: ErrorInfo(3, "Take up spool stalled"),
        4: ErrorInfo(4, "Seal not removed"),
        5: ErrorInfo(5, "Illegal command"),
        6: ErrorInfo(6, "No plate found (only when plate check is enabled)"),
        7: ErrorInfo(7, "Out of tape or tape broke"),
        8: ErrorInfo(8, "Parameters not saved"),
        9: ErrorInfo(9, "Stop button pressed while running"),
        10: ErrorInfo(10, "Seal sensor unplugged or broke"),
        20: ErrorInfo(20, "Less than 30 seals left on supply roll"),
        21: ErrorInfo(21, "Room for less than 30 seals on take-up spool"),
        51: ErrorInfo(51, "Emergency stop: cover open or hardware problem"),
        52: ErrorInfo(52, "Circuitry fault detected: remove power"),
    }

    def __init__(self, port=None, simulating=False, logger=None, timeout=None):
        self.simulating = simulating
        self.logger = logger or logging.getLogger(__name__)
        self.port = port or self.DEFAULT_PORT
        self.response_timeout = timeout if timeout is not None else self.RESPONSE_TIMEOUT

        self._serial_timeout = timeout if timeout is not None else self.response_timeout
        self.io: Optional[Serial] = None if simulating else Serial(
            port=self.port,
            baudrate=self.BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self._serial_timeout,
            write_timeout=self._serial_timeout,
            rtscts=False,
        )

    def __enter__(self):
        try:
            asyncio.run(self.setup())
        except RuntimeError as exc:
            if "asyncio.run()" in str(exc):
                raise RuntimeError(
                    "PlatePeeler.__enter__ cannot be used while an asyncio event loop is running; "
                    "use 'async with' instead."
                ) from exc
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self.simulating:
            try:
                asyncio.run(self.stop())
            except RuntimeError as exc:
                if "asyncio.run()" in str(exc):
                    raise RuntimeError(
                        "PlatePeeler.__exit__ cannot be used while an asyncio event loop is running; "
                        "use 'async with' instead."
                    ) from exc
                raise

    async def __aenter__(self):
        await self.setup()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

    async def setup(self):
        if self.simulating:
            return
        if self.io is None:
            raise RuntimeError("Serial interface not initialized.")
        await self.io.setup()

    async def stop(self):
        if self.simulating:
            return
        if self.io is None:
            return
        await self.io.stop()
        self.logger.info("Serial interface closed.")

    @classmethod
    def describe_error(cls, code: int) -> str:
        """
        Translate an XPeel error/status code to a human-readable message.
        """
        info = cls.ERROR_DEFINITIONS.get(code)
        if info:
            return info.description
        return f"Unknown error code {code}"

    @classmethod
    def parse_ready_line(cls, line: str):
        """
        Parse a ready line like '*ready:06,01,00' to extract the primary error code
        and its description. Returns a tuple (code: int, description: str).
        """
        if not line.startswith("*ready:"):
            return None
        try:
            # Expected format: *ready:CC,PP,TT (CC = error/condition code)
            parts = line.split(":")[1].split(",")
            code = int(parts[0])
            return code, cls.describe_error(code)
        except Exception:
            return None

    async def _send_command(
        self, cmd, action_desc=None, expect_ack=False, wait_for_ready=False, clear_buffer=True
    ) -> List[str]:
        """
        Send a command and collect responses until *ready (optional) or timeout.

        Returns a list of response lines (strings).
        """
        full_cmd = cmd if cmd.endswith("\r\n") else f"{cmd}\r\n"
        if action_desc:
            print(action_desc)
        if self.simulating:
            msg = f"[SIMULATION] Would send command: {full_cmd.strip()}"
            print(msg)
            if self.logger:
                self.logger.info(msg)
            return []

        if self.io is None:
            raise RuntimeError("Serial interface not initialized; call setup() first.")

        self.logger.info(f"Sending command: {full_cmd.strip()}")
        print(f"Sending command: {full_cmd.strip()}")
        if clear_buffer:
            await self.io.reset_input_buffer()
        await self.io.write(full_cmd.encode("ascii"))

        responses: List[str] = []
        start = time.time()
        while time.time() - start < self.response_timeout:
            raw = await self.io.readline()
            line = raw.decode("ascii", errors="ignore").strip()
            if not line:
                continue

            display_line = line
            if line.startswith("*ready:"):
                parsed = self.parse_ready_line(line)
                if parsed:
                    code, desc = parsed
                    display_line = f"{line} [{desc}]"

            responses.append(display_line)
            self.logger.info(f"Received: {display_line}")
            print(f"Received: {display_line}")

            if line.startswith("*ack"):
                if not wait_for_ready:
                    break
                continue

            if wait_for_ready and line.startswith("*ready"):
                break

            if not wait_for_ready and not expect_ack:
                break

        if time.time() - start >= self.response_timeout:
            self.logger.warning(
                "Timed out waiting for response to %s after %.2fs",
                full_cmd.strip(),
                self.response_timeout,
            )

        return responses


    async def get_status(self):
        """Request instrument status; returns *ready:XX,XX,XX."""
        return await self._send_command("*stat", action_desc="Requesting status...")

    async def version(self):
        """Request firmware version."""
        return await self._send_command("*version", action_desc="Requesting version...")

    async def reset(self):
        """Request reset; instrument replies with ack then ready."""
        return await self._send_command(
            "*reset", action_desc="Requesting reset...", expect_ack=True, wait_for_ready=True
        )

    async def restart(self):
        """Request restart; instrument replies with ack then poweron/homing/ready."""
        return await self._send_command(
            "*restart", action_desc="Requesting restart...", expect_ack=True, wait_for_ready=True
        )

    async def peel(self, parameter_set, adhere_time):
        """
        Run an automated de-seal cycle.

        Args:
            parameter_set: Parameter set number (1-9). Each set has different
                begin peel location and speed settings:

                ===========  ===================  ============
                Set Number   Begin Peel Location  Speed
                ===========  ===================  ============
                1            Default -2 mm        fast
                2            Default -2 mm        slow
                3            Default              fast
                4            Default              slow
                5            Default +2 mm        fast
                6            Default +2 mm        slow
                7            Default +4 mm        fast
                8            Default +4 mm        slow
                9            custom               custom
                ===========  ===================  ============

            adhere_time: Adhere time setting (1-4):
                - 1: 2.5s
                - 2: 5s
                - 3: 7.5s
                - 4: 10s
        """
        if parameter_set not in range(1, 10):
            raise ValueError("parameter_set must be in 1-9")
        if adhere_time not in range(1, 5):
            raise ValueError("adhere_time must be in 1-4")
        cmd = f"*xpeel:{parameter_set}{adhere_time}"
        return await self._send_command(
            cmd,
            action_desc="Starting XPeel cycle...",
            expect_ack=True,
            wait_for_ready=True,
        )

    async def seal_check(self):
        """Check for seal presence; ready response encodes result."""
        return await self._send_command(
            "*sealcheck", action_desc="Running seal check...", expect_ack=True, wait_for_ready=True
        )

    async def tape_remaining(self):
        """Query remaining tape."""
        return await self._send_command(
            "*tapeleft", action_desc="Checking tape remaining...", expect_ack=True, wait_for_ready=True
        )

    async def plate_check(self, enabled=True):
        """Enable or disable plate presence check."""
        flag = "y" if enabled else "n"
        return await self._send_command(
            f"*platecheck:{flag}",
            action_desc=f"Setting plate check to {'on' if enabled else 'off'}...",
            expect_ack=True,
            wait_for_ready=True,
        )

    async def seal_sensor_status(self):
        """Get seal sensor threshold status."""
        return await self._send_command(
            "*sealstat", action_desc="Requesting seal status...", expect_ack=True, wait_for_ready=True
        )

    async def set_seal_threshold_higher(self, value):
        """Set higher seal sensor threshold (0-999)."""
        if not 0 <= value <= 999:
            raise ValueError("value must be between 0 and 999")
        return await self._send_command(
            f"*sealhigher:{value:03d}",
            action_desc=f"Setting higher seal threshold to {value}...",
            expect_ack=True,
            wait_for_ready=True,
        )

    async def set_seal_threshold_lower(self, value):
        """Set lower seal sensor threshold (0-999)."""
        if not 0 <= value <= 999:
            raise ValueError("value must be between 0 and 999")
        return await self._send_command(
            f"*seallower:{value:03d}",
            action_desc=f"Setting lower seal threshold to {value}...",
            expect_ack=True,
            wait_for_ready=True,
        )

    async def move_conveyor_out(self):
        """Move conveyor out; ack then ready expected."""
        return await self._send_command(
            "*moveout",
            action_desc="Moving conveyor out...",
            expect_ack=True,
            wait_for_ready=True,
        )

    async def move_conveyor_in(self):
        """Move conveyor in; ack then ready expected."""
        return await self._send_command(
            "*movein",
            action_desc="Moving conveyor in...",
            expect_ack=True,
            wait_for_ready=True,
        )

    async def move_elevator_down(self):
        """Move elevator down; ack then ready expected."""
        return await self._send_command(
            "*movedown",
            action_desc="Moving elevator down...",
            expect_ack=True,
            wait_for_ready=True,
        )

    async def move_elevator_up(self):
        """Move elevator up; ack then ready expected."""
        return await self._send_command(
            "*moveup",
            action_desc="Moving elevator up...",
            expect_ack=True,
            wait_for_ready=True,
        )

    async def advance_tape(self):
        """Advance tape / move spool; ack then ready expected."""
        return await self._send_command(
            "*movespool",
            action_desc="Advancing tape...",
            expect_ack=True,
            wait_for_ready=True,
        )