import asyncio
import hashlib
import hmac
import logging
import re
import ssl
import warnings
from base64 import b64decode
from typing import Any, Dict, List, Optional, cast

from pylabrobot.device import DeviceBackend
from pylabrobot.io import Socket

logger = logging.getLogger(__name__)


class ThermoFisherThermocyclerDriver(DeviceBackend):
  """SCPI driver for ThermoFisher thermocyclers (ProFlex / ATC).

  Owns the socket connection and handles SSL/auth, block discovery,
  power management, file I/O, buzzer, etc.
  """

  def __init__(
    self,
    ip: str,
    use_ssl: bool = False,
    serial_number: Optional[str] = None,
  ):
    super().__init__()
    self.ip = ip
    self.use_ssl = use_ssl

    if use_ssl:
      self.port = 7443
      if serial_number is None:
        raise ValueError("Serial number is required for SSL connection (port 7443)")
      self.device_shared_secret = f"53rv1c3{serial_number}".encode("utf-8")

      ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
      ssl_context.check_hostname = False
      ssl_context.verify_mode = ssl.CERT_NONE
      # TLSv1 is required for legacy ThermoFisher hardware - silence deprecation warning
      with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="ssl.TLSVersion.TLSv1 is deprecated")
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1
        ssl_context.maximum_version = ssl.TLSVersion.TLSv1
      try:
        # This is required for some legacy devices that use older ciphers or protocols
        # that are disabled by default in newer OpenSSL versions.
        ssl_context.set_ciphers("DEFAULT:@SECLEVEL=0")
      except (ValueError, ssl.SSLError):
        # This might fail on some systems/implementations, but it's worth a try
        pass
    else:
      self.port = 7000
      self.device_shared_secret = b"f4ct0rymt55"
      ssl_context = None

    self.io = Socket(
      human_readable_device_name="Thermo Fisher Thermocycler",
      host=ip,
      port=self.port,
      ssl_context=ssl_context,
      server_hostname=serial_number,
    )
    self._num_blocks: Optional[int] = None
    self.num_temp_zones: int = 0
    self.bid: str = ""
    self.available_blocks: List[int] = []
    self.logger = logging.getLogger("pylabrobot.thermo_fisher.thermocycler")
    self.current_runs: Dict[int, str] = {}

  @property
  def num_blocks(self) -> int:
    if self._num_blocks is None:
      raise ValueError("Number of blocks not set. Call setup() first.")
    return self._num_blocks

  # ----- Authentication / connection -----

  def _get_auth_token(self, challenge: str):
    challenge_bytes = challenge.encode("utf-8")
    return hmac.new(self.device_shared_secret, challenge_bytes, hashlib.md5).hexdigest()

  # ----- SCPI message building / parsing -----

  def _build_scpi_msg(self, data: dict) -> str:
    def generate_output(data_dict: dict, indent_level=0) -> str:
      lines = []
      if indent_level == 0:
        line = data_dict["cmd"]
        for k, v in data_dict.get("params", {}).items():
          if v is True:
            line += f" -{k}"
          elif v is False:
            pass
          else:
            line += f" -{k}={v}"
        for val in data_dict.get("args", []):
          line += f" {val}"
        if "multiline" in data_dict:
          line += f" <{data_dict['tag']}>"
        lines.append(line)

        if "multiline" in data_dict:
          lines += generate_multiline(data_dict, indent_level + 1)
          lines.append(f"</{data_dict['tag']}>")
      return "\n".join(lines)

    def generate_multiline(multi_dict, indent_level=0) -> List[str]:
      def indent():
        return " " * 8 * indent_level

      lines = []
      for element in multi_dict["multiline"]:
        line = indent() + element["cmd"]
        for k, v in element.get("params", {}).items():
          line += f" -{k}={v}"
        for arg in element.get("args", []):
          line += f" {arg}"

        if "multiline" in element:
          line += f" <{element['tag']}>"
          lines.append(line)
          lines += generate_multiline(element, indent_level + 1)
          lines.append(indent() + f"</{element['tag']}>")
        else:
          lines.append(line)
      return lines

    return generate_output(data) + "\r\n"

  def _parse_scpi_response(self, response: str):
    START_TAG_REGEX = re.compile(r"(.*?)<(multiline\.[a-zA-Z0-9_]+)>")
    END_TAG_REGEX = re.compile(r"</(multiline\.[a-zA-Z0-9_]+)>")
    PARAM_REGEX = re.compile(r"^-([A-Za-z0-9_]+)(?:=(.*))?$")

    def parse_command_line(line):
      start_match = START_TAG_REGEX.search(line)
      if start_match:
        cmd_part = start_match.group(1).strip()
        tag_name = start_match.group(2)
      else:
        cmd_part = line
        tag_name = None

      if not cmd_part:
        return None, [], tag_name

      parts = cmd_part.split()
      command = parts[0]
      args = parts[1:]
      return command, args, tag_name

    def process_args(args_list):
      params: Dict[str, Any] = {}
      positional_args = []
      for arg in args_list:
        match = PARAM_REGEX.match(arg)
        if match:
          key = match.group(1)
          value = match.group(2)
          if value is None:
            params[key] = True
          else:
            params[key] = value
        else:
          positional_args.append(arg)
      return positional_args, params

    def parse_structure(scpi_resp: str):
      first_space_idx = scpi_resp.find(" ")
      status = scpi_resp[:first_space_idx]
      scpi_resp = scpi_resp[first_space_idx + 1 :]
      lines = scpi_resp.split("\n")

      root = {"status": status, "multiline": []}
      stack = [root]

      for original_line in lines:
        line = original_line.strip()
        if not line:
          continue
        end_match = END_TAG_REGEX.match(line)
        if end_match:
          if len(stack) > 1:
            stack.pop()
          else:
            raise ValueError("Unmatched end tag: </{}>".format(end_match.group(1)))
          continue

        command, args, start_tag = parse_command_line(line)
        if command is not None:
          pos_args, params = process_args(args)
          node = {"cmd": command, "args": pos_args, "params": params}
          if start_tag:
            node["multiline"] = []
            stack[-1]["multiline"].append(node)  # type: ignore
            stack.append(node)
            node["tag"] = start_tag
          else:
            stack[-1]["multiline"].append(node)  # type: ignore

      if len(stack) != 1:
        raise ValueError("Unbalanced tags in response.")
      return root

    if response.startswith("ERRor"):
      raise ValueError(f"Error response: {response}")

    result = parse_structure(response)
    status_val = result["status"]
    result = result["multiline"][0]
    result["status"] = status_val
    return result

  # ----- Low-level I/O -----

  async def _read_response(self, timeout=1, read_once=True) -> str:
    try:
      if read_once:
        response_b = await self.io.read(timeout=timeout)
      else:
        response_b = await self.io.read_until_eof(timeout=timeout)
      response = response_b.decode("ascii")
      self.logger.debug("Response received: %s", response)
      return response
    except TimeoutError:
      return ""
    except Exception as e:
      self.logger.error("Error reading from socket: %s", e)
      return ""

  async def send_command(self, data, response_timeout=1, read_once=True):
    msg = self._build_scpi_msg(data)
    self.logger.debug("Command sent: %s", msg.strip())

    await self.io.write(msg.encode("ascii"), timeout=response_timeout)
    return await self._read_response(timeout=response_timeout, read_once=read_once)

  async def _scpi_authenticate(self):
    await self.io.setup()
    await self._read_response(timeout=5)
    challenge_res = await self.send_command({"cmd": "CHAL?"})
    challenge = self._parse_scpi_response(challenge_res)["args"][0]
    auth = self._get_auth_token(challenge)
    auth_res = await self.send_command({"cmd": "AUTH", "args": [auth]})
    if self._parse_scpi_response(auth_res)["status"] != "OK":
      raise ValueError("Authentication failed")
    acc_res = await self.send_command(
      {"cmd": "ACCess", "params": {"stealth": True}, "args": ["Controller"]}
    )
    if self._parse_scpi_response(acc_res)["status"] != "OK":
      raise ValueError("Access failed")

  # ----- Block discovery -----

  async def _load_num_blocks_and_type(self):
    block_present_val = await self.get_block_presence()
    if block_present_val == "0":
      raise ValueError("Block not present")
    self.bid = await self.get_block_id()
    if self.bid == "12":
      self._num_blocks = 1
      self.num_temp_zones = 6
    elif self.bid == "13":
      self._num_blocks = 3
      self.num_temp_zones = 2
    elif self.bid == "31":
      self._num_blocks = 1
      self.num_temp_zones = 3
    else:
      raise NotImplementedError("Only BID 31, 12 and 13 are supported")

  async def _load_available_blocks(self) -> None:
    await self._scpi_authenticate()
    await self._load_num_blocks_and_type()
    assert self._num_blocks is not None, "Number of blocks not set"
    for block_id in range(self._num_blocks):
      block_error = await self.get_error(block_id=block_id)
      if block_error != "0":
        raise ValueError(f"Block {block_id} has error: {block_error}")
      if not await self.is_block_running(block_id=block_id):
        if block_id not in self.available_blocks:
          self.available_blocks.append(block_id)

  # ----- Block helpers -----

  async def get_block_id(self):
    res = await self.send_command({"cmd": "TBC:BID?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get block ID")
    return self._parse_scpi_response(res)["args"][0]

  async def get_block_presence(self):
    res = await self.send_command({"cmd": "TBC:BlockPresence?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get block presence")
    return self._parse_scpi_response(res)["args"][0]

  async def is_block_running(self, block_id: int) -> bool:
    run_name = await self.get_run_name(block_id=block_id)
    return run_name != "-"

  async def get_run_name(self, block_id: int) -> str:
    res = await self.send_command({"cmd": f"TBC{block_id + 1}:RUNTitle?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get run title")
    return cast(str, self._parse_scpi_response(res)["args"][0])

  async def get_error(self, block_id: int):
    res = await self.send_command({"cmd": f"TBC{block_id + 1}:ERROR?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get error")
    return self._parse_scpi_response(res)["args"][0]

  # ----- Power -----

  async def power_on(self):
    res = await self.send_command({"cmd": "POWER", "args": ["On"]}, response_timeout=20)
    if res == "" or self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to power on")

  async def power_off(self):
    res = await self.send_command({"cmd": "POWER", "args": ["Off"]})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to power off")

  # ----- File I/O -----

  async def _write_file(self, filename: str, data: str, encoding="plain"):
    write_res = await self.send_command(
      {
        "cmd": "FILe:WRITe",
        "params": {"encoding": encoding},
        "args": [filename],
        "multiline": [{"cmd": data}],
        "tag": "multiline.write",
      },
      response_timeout=1,
      read_once=False,
    )
    if self._parse_scpi_response(write_res)["status"] != "OK":
      raise ValueError("Failed to write file")

  # ----- Nickname -----

  async def get_nickname(self) -> str:
    res = await self.send_command({"cmd": "SYST:SETT:NICK?"})
    return cast(str, self._parse_scpi_response(res)["args"][0])

  async def set_nickname(self, nickname: str) -> None:
    res = await self.send_command({"cmd": "SYST:SETT:NICK", "args": [nickname]})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to set nickname")

  # ----- Buzzer -----

  async def buzzer_on(self):
    res = await self.send_command({"cmd": "BUZZer+"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to turn on buzzer")

  async def buzzer_off(self):
    res = await self.send_command({"cmd": "BUZZer-"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to turn off buzzer")

  async def send_morse_code(self, morse_code: str):
    short_beep_duration = 0.1
    long_beep_duration = short_beep_duration * 3
    space_duration = short_beep_duration * 3
    assert all(char in ".- " for char in morse_code), "Invalid characters in morse code"
    for char in morse_code:
      if char == ".":
        await self.buzzer_on()
        await asyncio.sleep(short_beep_duration)
        await self.buzzer_off()
      elif char == "-":
        await self.buzzer_on()
        await asyncio.sleep(long_beep_duration)
        await self.buzzer_off()
      elif char == " ":
        await asyncio.sleep(space_duration)
      await asyncio.sleep(short_beep_duration)

  # ----- Idle temperature control (used during setup) -----

  async def set_block_idle_temp(
    self, temp: float, block_id: int, control_enabled: bool = True
  ) -> None:
    if block_id not in self.available_blocks:
      raise ValueError(f"Block {block_id} is not available")
    res = await self.send_command(
      {"cmd": f"TBC{block_id + 1}:BLOCK", "args": [1 if control_enabled else 0, temp]}
    )
    if self._parse_scpi_response(res)["status"] != "NEXT":
      raise ValueError("Failed to set block idle temperature")
    follow_up = await self._read_response()
    if self._parse_scpi_response(follow_up)["status"] != "OK":
      raise ValueError("Failed to set block idle temperature")

  async def set_cover_idle_temp(
    self, temp: float, block_id: int, control_enabled: bool = True
  ) -> None:
    if block_id not in self.available_blocks:
      raise ValueError(f"Block {block_id} not available")
    res = await self.send_command(
      {"cmd": f"TBC{block_id + 1}:COVER", "args": [1 if control_enabled else 0, temp]}
    )
    if self._parse_scpi_response(res)["status"] != "NEXT":
      raise ValueError("Failed to set cover idle temperature")
    follow_up = await self._read_response()
    if self._parse_scpi_response(follow_up)["status"] != "OK":
      raise ValueError("Failed to set cover idle temperature")

  # ----- Setup / stop (lifecycle) -----

  async def setup(
    self,
    block_idle_temp: float = 25.0,
    cover_idle_temp: float = 105.0,
    blocks_to_setup: Optional[List[int]] = None,
  ):
    await self._scpi_authenticate()
    await self.power_on()
    await self._load_num_blocks_and_type()
    if blocks_to_setup is None:
      await self._load_available_blocks()
      if len(self.available_blocks) == 0:
        raise ValueError("No available blocks. Set blocks_to_setup to force setup")
    else:
      self.available_blocks = blocks_to_setup
    for block_index in self.available_blocks:
      await self.set_block_idle_temp(temp=block_idle_temp, block_id=block_index)
      await self.set_cover_idle_temp(temp=cover_idle_temp, block_id=block_index)

  async def stop(self):
    for block_id in list(self.current_runs.keys()):
      await self.abort_run(block_id=block_id)
      await self.deactivate_lid(block_id=block_id)
      await self.deactivate_block(block_id=block_id)
    await self.io.stop()

  # ----- Run management (device-level, not per-block) -----

  async def check_run_exists(self, run_name: str) -> bool:
    res = await self.send_command(
      {"cmd": "RUNS:EXISTS?", "args": [run_name], "params": {"type": "folders"}}
    )
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to check if run exists")
    return cast(str, self._parse_scpi_response(res)["args"][1]) == "True"

  async def create_run(self, run_name: str):
    res = await self.send_command({"cmd": "RUNS:NEW", "args": [run_name]}, response_timeout=10)
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to create run")
    return self._parse_scpi_response(res)["args"][0]

  async def get_log_by_runname(self, run_name: str) -> str:
    res = await self.send_command(
      {"cmd": "FILe:READ?", "args": [f"RUNS:{run_name}/{run_name}.log"]},
      response_timeout=5,
      read_once=False,
    )
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get log")
    res.replace("\n", "")
    encoded_log_match = re.search(r"<quote>(.*?)</quote>", res, re.DOTALL)
    if not encoded_log_match:
      raise ValueError("Failed to parse log content")
    encoded_log = encoded_log_match.group(1).strip()
    return b64decode(encoded_log).decode("utf-8")

  async def get_elapsed_run_time_from_log(self, run_name: str) -> int:
    """Parse a log to find the elapsed run time in hh:mm:ss format and convert to total seconds."""
    log = await self.get_log_by_runname(run_name)
    elapsed_time_match = re.search(r"Run Time:\s*(\d+):(\d+):(\d+)", log)
    if not elapsed_time_match:
      raise ValueError("Failed to parse elapsed time from log. Expected hh:mm:ss format.")
    hours = int(elapsed_time_match.group(1))
    minutes = int(elapsed_time_match.group(2))
    seconds = int(elapsed_time_match.group(3))
    return (hours * 3600) + (minutes * 60) + seconds

  # ----- Methods used by stop() -----

  async def abort_run(self, block_id: int):
    if not await self.is_block_running(block_id=block_id):
      self.logger.info("Failed to abort protocol: no run is currently running on this block")
      return
    run_name = await self.get_run_name(block_id=block_id)
    abort_res = await self.send_command({"cmd": f"TBC{block_id + 1}:AbortRun", "args": [run_name]})
    if self._parse_scpi_response(abort_res)["status"] != "OK":
      self.logger.error(abort_res)
      self.logger.error("Failed to abort protocol")
      raise ValueError("Failed to abort protocol")
    self.logger.info("Protocol aborted")
    await asyncio.sleep(10)

  async def deactivate_lid(self, block_id: int):
    return await self.set_cover_idle_temp(temp=105, control_enabled=False, block_id=block_id)

  async def deactivate_block(self, block_id: int):
    return await self.set_block_idle_temp(temp=25, control_enabled=False, block_id=block_id)
