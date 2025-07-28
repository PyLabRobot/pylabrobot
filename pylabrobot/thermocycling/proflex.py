import asyncio
import hashlib
import hmac
import logging
import re
import xml.etree.ElementTree as ET
from xml.dom import minidom

from .backend import ThermocyclerBackend


class ProflexPCRProtocol:
  def __init__(
    self,
    volume=50,
    runMode="Fast",
    blockId=1,
    protocol_name="PCR_Protocol",
    coverTemp=105,
    coverEnabled="On",
  ):
    self.data = {
      "status": "OK",
      "cmd": f"TBC{blockId}:Protocol",
      "params": {"Volume": str(volume), "RunMode": runMode},
      "args": [protocol_name],
      "tag": "multiline.outer",
      "multiline": [],
      "_coverTemp": coverTemp,
      "_coverEnabled": coverEnabled,
      "_blockId": blockId,
      "_infinite_holds": [],
    }
    self.current_stage = None
    self.stage_count = 0
    self.step_count = 0
    self.protocol_name = protocol_name
    self.blockId = blockId
    self.coverTemp = coverTemp
    self.coverEnabled = coverEnabled
    self.infinite_holds = []

  @classmethod
  def from_dict(cls, data: dict):
    instance = cls()
    instance.data = data
    instance.stage_count = len(data["multiline"])
    instance.current_stage = data["multiline"][-1] if data["multiline"] else None
    instance.step_count = len(instance.current_stage["multiline"]) if instance.current_stage else 0
    instance.protocol_name = data["args"][0]
    instance.blockId = data["_blockId"]
    instance.coverTemp = data["_coverTemp"]
    instance.coverEnabled = data["_coverEnabled"]
    instance.infinite_holds = data["_infinite_holds"]
    return instance

  def add_stage(self, cycles, stage_name_base="_PCR"):
    self.stage_count += 1
    stage = {
      "cmd": "STAGe",
      "params": {"repeat": str(cycles)},
      "args": [self.stage_count, f"{stage_name_base}_{self.stage_count}"],
      "tag": "multiline.stage",
      "multiline": [],
    }
    self.data["multiline"].append(stage)
    self.current_stage = stage
    self.step_count = 0

  def add_step(
    self,
    temp_list: list[float],
    time: int = 0,
    ramprate: int = 100,
    hold: bool = True,
    infinite_hold: bool = False,
  ):
    self.step_count += 1
    step = {
      "cmd": "STEP",
      "params": {},
      "args": [str(self.step_count)],
      "tag": "multiline.step",
      "multiline": [],
    }

    if infinite_hold and min(temp_list) < 20:
      step["multiline"].append({"cmd": "CoverRAMP", "params": {}, "args": ["30"]})

    step["multiline"].append(
      {"cmd": "RAMP", "params": {"rate": str(ramprate)}, "args": [str(t) for t in temp_list]}
    )

    if infinite_hold:
      step["multiline"].append({"cmd": "HOLD", "params": {}, "args": []})
      self.infinite_holds.append([self.stage_count, self.step_count])
    elif hold and time > 0:
      step["multiline"].append({"cmd": "HOLD", "params": {}, "args": [str(time)]})

    self.current_stage["multiline"].append(step)

  def get_target_temp(self, stagenum: int, stepnum: int):
    step_data = self.data["multiline"][stagenum - 1]["multiline"][stepnum - 1]["multiline"]
    while step_data and step_data[0]["cmd"] != "RAMP":
      step_data = step_data[1:]
    temp_str_list = step_data[0]["args"]
    return [float(i) for i in temp_str_list]

  def adjust_ramp_args(self, num_temp_zones, data=None):
    if data is None:
      data = self.data
    if isinstance(data, dict):
      if data.get("cmd") == "RAMP" and "args" in data:
        ramp_args = data["args"]
        if len(ramp_args) != num_temp_zones:
          data["args"] = [ramp_args[0]] * num_temp_zones
      for val in data.values():
        self.adjust_ramp_args(num_temp_zones, val)
    elif isinstance(data, list):
      for item in data:
        self.adjust_ramp_args(num_temp_zones, item)

  def set_block_id(self, blockId):
    self.blockId = blockId
    self.data["_blockId"] = blockId
    self.data["cmd"] = f"TBC{blockId}:Protocol"

  def gen_protocol_data(self):
    if self.data["multiline"]:
      self.data["_infinite_holds"] = self.infinite_holds
      return self.data
    else:
      raise ValueError("No stages added to the protocol")

  def generate_run_info_files(
    self,
    user_name="LifeTechnologies",
    file_version="1.0.1",
    remote_run="true",
    hub="testhub",
    user="Guest",
    notes="",
    default_ramp_rate=6,
    ramp_rate_unit="DEGREES_PER_SECOND",
  ):
    input_data = self.data
    protocol_name = self.protocol_name
    block_id = str(self.blockId)
    params = input_data.get("params", {})
    sample_volume = params.get("Volume", "50")
    run_mode = params.get("RunMode", "Fast")

    root = ET.Element("TCProtocol")
    file_version_el = ET.SubElement(root, "FileVersion")
    file_version_el.text = file_version

    protocol_name_el = ET.SubElement(root, "ProtocolName")
    protocol_name_el.text = protocol_name

    user_name_el = ET.SubElement(root, "UserName")
    user_name_el.text = user_name

    block_id_el = ET.SubElement(root, "BlockID")
    block_id_el.text = block_id

    sample_volume_el = ET.SubElement(root, "SampleVolume")
    sample_volume_el.text = str(sample_volume)

    run_mode_el = ET.SubElement(root, "RunMode")
    run_mode_el.text = str(run_mode)

    cover_temp_el = ET.SubElement(root, "CoverTemperature")
    cover_temp_el.text = str(self.coverTemp)

    cover_setting_el = ET.SubElement(root, "CoverSetting")
    cover_setting_el.text = self.coverEnabled

    multiline_data = input_data.get("multiline", [])
    for stage_obj in multiline_data:
      if stage_obj.get("cmd", "").lower() == "stage":
        stage_el = ET.SubElement(root, "TCStage")
        stage_flag_el = ET.SubElement(stage_el, "StageFlag")
        stage_flag_el.text = "CYCLING"

        repeat_str = stage_obj.get("params", {}).get("repeat", "1")
        num_repetitions_el = ET.SubElement(stage_el, "NumOfRepetitions")
        num_repetitions_el.text = repeat_str

        steps = stage_obj.get("multiline", [])
        for step_obj in steps:
          if step_obj.get("cmd", "").lower() == "step":
            step_el = ET.SubElement(stage_el, "TCStep")
            ramp_rate_value = default_ramp_rate
            step_commands = step_obj.get("multiline", [])

            temperature_list = []
            hold_time_value = -1

            for cmd_obj in step_commands:
              cmd_name = cmd_obj.get("cmd", "").lower()
              if cmd_name == "ramp":
                temperature_list = cmd_obj.get("args", [])
                ramp_param = cmd_obj.get("params", {}).get("rate")
                if ramp_param is not None:
                  ramp_rate_value = int(ramp_param) / 100 * 6
              elif cmd_name == "hold":
                hold_args = cmd_obj.get("args", [])
                if hold_args:
                  try:
                    hold_time_value = int(hold_args[0])
                  except ValueError:
                    hold_time_value = 0
              elif cmd_name == "coverramp":
                pass

            ramp_rate_el = ET.SubElement(step_el, "RampRate")
            ramp_rate_el.text = str(ramp_rate_value)

            ramp_rate_unit_el = ET.SubElement(step_el, "RampRateUnit")
            ramp_rate_unit_el.text = ramp_rate_unit

            for t_val in temperature_list:
              temp_el = ET.SubElement(step_el, "Temperature")
              temp_el.text = str(t_val)

            hold_time_el = ET.SubElement(step_el, "HoldTime")
            hold_time_el.text = str(hold_time_value)

            ext_temp_el = ET.SubElement(step_el, "ExtTemperature")
            ext_temp_el.text = "0"

            ext_hold_el = ET.SubElement(step_el, "ExtHoldTime")
            ext_hold_el.text = "0"

            ext_start_cycle_el = ET.SubElement(step_el, "ExtStartingCycle")
            ext_start_cycle_el.text = "1"

    rough_string = ET.tostring(root, encoding="utf-8")
    reparsed = minidom.parseString(rough_string)

    xml_declaration = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    pretty_xml_as_string = (
      xml_declaration + reparsed.toprettyxml(indent="   ")[len('<?xml version="1.0" ?>') :]
    )

    output2_lines = [
      f"-remoterun= {remote_run}",
      f"-hub= {hub}",
      f"-user= {user}",
      f"-method= {protocol_name}",
      f"-volume= {sample_volume}",
      f"-cover= {self.coverTemp}",
      f"-mode= {run_mode}",
      f"-coverEnabled= {self.coverEnabled}",
      f"-notes= {notes}",
    ]
    output2_string = "\n".join(output2_lines)

    return pretty_xml_as_string, output2_string


class ProflexBackend(ThermocyclerBackend):
  """Backend for Proflex thermocycler."""

  def __init__(self, ip: str, port: int = 7000, shared_secret: bytes = b"f4ct0rymt55", debug=False):
    self.ip = ip
    self.port = port
    self.device_shared_secret = shared_secret
    self._socket_reader = None
    self._socket_writer = None
    self.num_blocks = 1
    self.num_temp_zones = 0
    self.available_blocks = []
    self.logger = logging.getLogger("pylabrobot.thermocycler.proflex")
    self.current_run = None
    self.running_block = None
    self.prot_time_elapsed = 0
    self.prot_time_remaining = 0

  async def connect_device(self):
    """Establish a connection to the thermocycler."""
    self._socket_reader, self._socket_writer = await asyncio.open_connection(self.ip, self.port)

  async def disconnect_device(self):
    """Close the connection to the thermocycler."""
    if self._socket_writer:
      self._socket_writer.close()
      await self._socket_writer.wait_closed()
      self._socket_reader = None
      self._socket_writer = None

  def _get_auth_token(self, challenge: str):
    challenge_bytes = challenge.encode("utf-8")
    return hmac.new(self.device_shared_secret, challenge_bytes, hashlib.md5).hexdigest()

  def _build_scpi_msg(self, data):
    def generate_output(data_dict, indent_level=0):
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

    def generate_multiline(multi_dict, indent_level=0):
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

    return generate_output(data)

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
      params = {}
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
            stack[-1]["multiline"].append(node)
            stack.append(node)
            node["tag"] = start_tag
          else:
            stack[-1]["multiline"].append(node)

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

  async def _read_response(self, timeout=1, readonce=True):
    if not self._socket_reader:
      raise ConnectionError("Socket not connected.")

    chunks = []
    while True:
      try:
        data = await asyncio.wait_for(self._socket_reader.read(1024), timeout=timeout)
        if not data:
          break
        elif readonce:
          chunks.append(data.decode("ascii"))
          break
        else:
          chunks.append(data.decode("ascii"))
      except asyncio.TimeoutError:
        break
      except UnicodeDecodeError:
        continue

    response = "".join(chunks)
    self.logger.debug("Response received: %s", response)
    return response

  async def send_command(self, command: str, response_timeout=1, readonce=True):
    if not self._socket_writer:
      raise ConnectionError("Socket not connected.")

    command += "\r\n"
    self.logger.debug("Command sent: %s", command.strip())

    self._socket_writer.write(command.encode("ascii"))
    await self._socket_writer.drain()

    return await self._read_response(timeout=response_timeout, readonce=readonce)

  async def scpi_send_data(self, data, response_timeout=1, readonce=True):
    msg = self._build_scpi_msg(data)
    return await self.send_command(msg, response_timeout=response_timeout, readonce=readonce)

  async def _scpi_authenticate(self):
    await self.connect_device()
    await self._read_response(timeout=5)
    challenge_res = await self.scpi_send_data({"cmd": "CHAL?"})
    challenge = self._parse_scpi_response(challenge_res)["args"][0]
    auth = self._get_auth_token(challenge)
    auth_res = await self.scpi_send_data({"cmd": "AUTH", "args": [auth]})
    if self._parse_scpi_response(auth_res)["status"] != "OK":
      raise ValueError("Authentication failed")
    acc_res = await self.scpi_send_data(
      {"cmd": "ACCess", "params": {"stealth": True}, "args": ["Controller"]}
    )
    if self._parse_scpi_response(acc_res)["status"] != "OK":
      raise ValueError("Access failed")

  async def _scpi_check_block_type(self):
    block_present_val = await self.scpi_get_block_presence()
    if block_present_val == "0":
      raise ValueError("Block not present")
    self.bid = await self.scpi_get_block_id()
    if self.bid == "12":
      self.num_blocks = 1
      self.num_temp_zones = 6
    elif self.bid == "13":
      self.num_blocks = 3
      self.num_temp_zones = 2
    else:
      raise NotImplementedError("Only BID 12 and 13 are supported")

  async def scpi_get_available_blocks(self):
    await self._scpi_authenticate()
    await self._scpi_check_block_type()
    for i in range(1, self.num_blocks + 1):
      block_error = await self.scpi_get_error(blockId=i)
      if block_error != "0":
        raise ValueError(f"Block{i} has error: {block_error}")
      run_title = await self.scpi_get_run_title(blockId=i)
      if run_title == "-":
        if i not in self.available_blocks:
          self.available_blocks.append(i)
    return self.available_blocks

  async def scpi_get_block_temps(self, blockId=1):
    res = await self.scpi_send_data({"cmd": f"TBC{blockId}:TBC:BlockTemperatures?"})
    return self._parse_scpi_response(res)["args"]

  async def scpi_get_sample_temps(self, blockId=1):
    res = await self.scpi_send_data({"cmd": f"TBC{blockId}:TBC:SampleTemperatures?"})
    return self._parse_scpi_response(res)["args"]

  async def scpi_get_cover_temps(self, blockId=1):
    res = await self.scpi_send_data({"cmd": f"TBC{blockId}:TBC:CoverTemperatures?"})
    return self._parse_scpi_response(res)["args"]

  async def scpi_get_nickname(self):
    res = await self.scpi_send_data({"cmd": "SYST:SETT:NICK?"})
    return self._parse_scpi_response(res)["args"][0]

  async def scpi_set_nickname(self, nickname: str):
    res = await self.scpi_send_data({"cmd": "SYST:SETT:NICK", "args": [nickname]})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to set nickname")

  async def scpi_set_block_idle_temp(self, temp: float = 25, controlEnabled=1, blockId=1):
    if blockId not in self.available_blocks:
      raise ValueError(f"Block {blockId} is not available")
    res = await self.scpi_send_data({"cmd": f"TBC{blockId}:BLOCK", "args": [controlEnabled, temp]})
    if self._parse_scpi_response(res)["status"] != "NEXT":
      raise ValueError("Failed to set block idle temperature")
    follow_up = await self._read_response()
    if self._parse_scpi_response(follow_up)["status"] != "OK":
      raise ValueError("Failed to set block idle temperature")

  async def scpi_set_cover_idle_temp(self, temp: float = 105, controlEnabled=1, blockId=1):
    if blockId not in self.available_blocks:
      raise ValueError(f"Block {blockId} not available")
    res = await self.scpi_send_data({"cmd": f"TBC{blockId}:COVER", "args": [controlEnabled, temp]})
    if self._parse_scpi_response(res)["status"] != "NEXT":
      raise ValueError("Failed to set cover idle temperature")
    follow_up = await self._read_response()
    if self._parse_scpi_response(follow_up)["status"] != "OK":
      raise ValueError("Failed to set cover idle temperature")

  async def scpi_ramp_block(self, target_temps: list[float], rate: float = 100, blockId=1):
    if blockId not in self.available_blocks:
      raise ValueError(f"Block {blockId} not available")
    res = await self.scpi_send_data(
      {"cmd": f"TBC{blockId}:RAMP", "params": {"rate": rate}, "args": target_temps},
      response_timeout=60,
    )
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to ramp block temperature")

  async def scpi_block_ramp_single_temp(self, target_temp: float, rate: float = 100, blockId=1):
    if blockId not in self.available_blocks:
      raise ValueError(f"Block {blockId} not available")
    res = await self.scpi_send_data(
      {"cmd": f"TBC{blockId}:BlockRAMP", "params": {"rate": rate}, "args": [target_temp]},
      response_timeout=60,
    )
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to ramp block temperature")

  async def scpi_cover_ramp(self, target_temp: float, blockId=1):
    if blockId not in self.available_blocks:
      raise ValueError(f"Block {blockId} not available")
    res = await self.scpi_send_data(
      {"cmd": f"TBC{blockId}:CoverRAMP", "params": {}, "args": [target_temp]}, response_timeout=60
    )
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to ramp cover temperature")

  async def scpi_buzzer_on(self):
    res = await self.scpi_send_data({"cmd": "BUZZer+"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to turn on buzzer")

  async def scpi_buzzer_off(self):
    res = await self.scpi_send_data({"cmd": "BUZZer-"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to turn off buzzer")

  async def scpi_continue(self, blockId=1):
    for _ in range(3):
      await asyncio.sleep(1)
      res = await self.scpi_send_data({"cmd": f"TBC{blockId}:CONTinue"})
      if self._parse_scpi_response(res)["status"] != "OK":
        raise ValueError("Failed to continue from indefinite hold")

  async def scpi_write_file(self, filename: str, data: str, encoding="plain"):
    write_res = await self.scpi_send_data(
      {
        "cmd": "FILe:WRITe",
        "params": {"encoding": encoding},
        "args": [filename],
        "multiline": [{"cmd": data}],
        "tag": "multiline.write",
      },
      response_timeout=1,
      readonce=False,
    )
    if self._parse_scpi_response(write_res)["status"] != "OK":
      raise ValueError("Failed to write file")

  async def scpi_get_block_id(self):
    res = await self.scpi_send_data({"cmd": "TBC:BID?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get block ID")
    return self._parse_scpi_response(res)["args"][0]

  async def scpi_get_block_presence(self):
    res = await self.scpi_send_data({"cmd": "TBC:BlockPresence?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get block presence")
    return self._parse_scpi_response(res)["args"][0]

  async def scpi_check_run_exists(self, runName="testrun"):
    res = await self.scpi_send_data(
      {"cmd": "RUNS:EXISTS?", "args": [runName], "params": {"type": "folders"}}
    )
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to check if run exists")
    return self._parse_scpi_response(res)["args"][1]

  async def scpi_create_run(self, runName="testrun"):
    res = await self.scpi_send_data({"cmd": "RUNS:NEW", "args": [runName]}, response_timeout=10)
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to create run")
    return self._parse_scpi_response(res)["args"][0]

  async def scpi_get_run_title(self, blockId=1):
    res = await self.scpi_send_data({"cmd": f"TBC{blockId}:RUNTitle?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get run title")
    return self._parse_scpi_response(res)["args"][0]

  async def scpi_get_run_progress(self, blockId=1):
    res = await self.scpi_send_data({"cmd": f"TBC{blockId}:RUNProgress?"})
    parsed_res = self._parse_scpi_response(res)
    if parsed_res["status"] != "OK":
      raise ValueError("Failed to get run status")
    if parsed_res["cmd"] == f"TBC{blockId}:RunProtocol":
      await self._read_response()
      return False
    return self._parse_scpi_response(res)["params"]

  async def scpi_get_estimated_run_time(self, blockId=1):
    res = await self.scpi_send_data({"cmd": f"TBC{blockId}:ESTimatedTime?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get estimated run time")
    return self._parse_scpi_response(res)["args"][0]

  async def scpi_get_elapsed_run_time(self, blockId=1):
    res = await self.scpi_send_data({"cmd": f"TBC{blockId}:ELAPsedTime?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get elapsed run time")
    return int(self._parse_scpi_response(res)["args"][0])

  async def scpi_get_remaining_run_time(self, blockId=1):
    res = await self.scpi_send_data({"cmd": f"TBC{blockId}:REMainingTime?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get remaining run time")
    return int(self._parse_scpi_response(res)["args"][0])

  async def scpi_get_error(self, blockId=1):
    res = await self.scpi_send_data({"cmd": f"TBC{blockId}:ERROR?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get error")
    return self._parse_scpi_response(res)["args"][0]

  async def scpi_power_on(self):
    res = await self.scpi_send_data({"cmd": "POWER", "args": ["On"]}, response_timeout=20)
    if res == "" or self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to power on")

  async def scpi_power_off(self):
    res = await self.scpi_send_data({"cmd": "POWER", "args": ["Off"]})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to power off")

  async def _scpi_write_run_info(self, protocol: ProflexPCRProtocol, runName="testrun"):
    xmlfile, tmpfile = protocol.generate_run_info_files()
    await self.scpi_write_file(f"runs:{runName}/{protocol.protocol_name}.method", xmlfile)
    await self.scpi_write_file(f"runs:{runName}/{runName}.tmp", tmpfile)

  async def _scpi_run_protocol(self, protocol: ProflexPCRProtocol, runName="testrun", user="Guest"):
    load_res = await self.scpi_send_data(
      protocol.gen_protocol_data(), response_timeout=5, readonce=False
    )
    if self._parse_scpi_response(load_res)["status"] != "OK":
      logging.error(load_res)
      logging.error("Protocol failed to load")
      raise ValueError("Protocol failed to load")

    start_res = await self.scpi_send_data(
      {
        "cmd": f"TBC{protocol.blockId}:RunProtocol",
        "params": {
          "User": user,
          "CoverTemperature": protocol.coverTemp,
          "CoverEnabled": protocol.coverEnabled,
        },
        "args": [protocol.protocol_name, runName],
      },
      response_timeout=2,
      readonce=False,
    )

    if self._parse_scpi_response(start_res)["status"] == "NEXT":
      logging.info("Protocol started")
    else:
      logging.error(start_res)
      logging.error("Protocol failed to start")
      raise ValueError("Protocol failed to start")

    total_time = await self.scpi_get_estimated_run_time(blockId=protocol.blockId)
    total_time = float(total_time)
    logging.info(f"Estimated run time: {total_time}")
    self.current_run = runName
    self.running_block = protocol.blockId

  async def _scpi_abort_run(self, blockId, run_name):
    abort_res = await self.scpi_send_data({"cmd": f"TBC{blockId}:AbortRun", "args": [run_name]})
    if self._parse_scpi_response(abort_res)["status"] != "OK":
      logging.error(abort_res)
      logging.error("Failed to abort protocol")
      raise ValueError("Failed to abort protocol")
    logging.info("Protocol aborted")

  async def check_if_running(self, protocol: ProflexPCRProtocol):
    blockId = protocol.blockId
    progress = await self.scpi_get_run_progress(blockId=blockId)
    if not progress:
      logging.info("Protocol completed")
      return False, "completed", self.prot_time_elapsed, 0

    if progress["RunTitle"] == "-":
      await self._read_response(timeout=5)
      logging.info("Protocol completed")
      return False, "completed", self.prot_time_elapsed, 0

    if progress["Stage"] == "POSTRun":
      logging.info("Protocol in POSTRun")
      return True, "POSTRun", self.prot_time_elapsed, 0

    if progress["Stage"] != "-" and progress["Step"] != "-":
      if [int(progress["Stage"]), int(progress["Step"])] in protocol.infinite_holds:
        while True:
          block_temps = await self.scpi_get_block_temps(blockId=blockId)
          target_temps = protocol.get_target_temp(int(progress["Stage"]), int(progress["Step"]))
          if all(
            abs(float(block_temps[i]) - target_temps[i]) < 0.5 for i in range(len(block_temps))
          ):
            break
          await asyncio.sleep(5)
        logging.info("Infinite hold")
        return False, "infinite_hold", self.prot_time_elapsed, self.prot_time_remaining

    time_elapsed = await self.scpi_get_elapsed_run_time(blockId=blockId)
    self.prot_time_elapsed = time_elapsed
    remaining_time = await self.scpi_get_remaining_run_time(blockId=blockId)
    self.prot_time_remaining = remaining_time

    logging.info(f"Elapsed time: {time_elapsed}")
    logging.info(f"Remaining time: {remaining_time}")
    return True, progress["Stage"], time_elapsed, remaining_time

  # *************Three core methods for running a protocol***********************

  async def setup(self, blockIdleTemp=25, coverIdleTemp=105, blocks_to_setup: list[int] = None):
    await self._scpi_authenticate()
    await self.scpi_power_on()
    await self._scpi_check_block_type()
    if blocks_to_setup is None:
      await self.scpi_get_available_blocks()
    else:
      self.available_blocks = blocks_to_setup
    for block_index in self.available_blocks:
      await self.scpi_set_block_idle_temp(temp=blockIdleTemp, blockId=block_index)
      await self.scpi_set_cover_idle_temp(temp=coverIdleTemp, blockId=block_index)

  async def open_lid(self):
    raise NotImplementedError("Open lid command is not implemented for Proflex thermocycler")

  async def close_lid(self):
    raise NotImplementedError("Close lid command is not implemented for Proflex thermocycler")

  async def run_protocol(self, protocol: ProflexPCRProtocol, runName="testrun", user="Admin"):
    run_exists = await self.scpi_check_run_exists(runName)
    if run_exists == "False":
      await self.scpi_create_run(runName)
    else:
      logging.warning(f"Run {runName} already exists")
    await self._scpi_write_run_info(protocol, runName)
    await self._scpi_run_protocol(protocol, runName, user)

  async def stop(self):
    blockId = self.running_block
    is_running = (await self.scpi_get_run_title(blockId=blockId)) != "-"
    if is_running:
      await self._scpi_abort_run(blockId, self.current_run)
      asyncio.sleep(10)
    await self.scpi_set_cover_idle_temp(controlEnabled=0, blockId=blockId)
    await self.scpi_set_block_idle_temp(controlEnabled=0, blockId=blockId)
    await self.disconnect_device()
