import asyncio
import hashlib
import hmac
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from xml.dom import minidom

from pylabrobot.io import Socket
from pylabrobot.thermocycling.standard import LidStatus, Protocol, Stage, Step

from .backend import ThermocyclerBackend


class ProflexPCRProtocol:
  # @classmethod
  # def from_dict(cls, data: dict):
  #   instance = cls()
  #   instance.data = data
  #   instance.stage_count = len(data["multiline"])
  #   instance.current_stage = data["multiline"][-1] if data["multiline"] else None
  #   instance.step_count = len(instance.current_stage["multiline"]) if instance.current_stage else 0
  #   instance.protocol_name = data["args"][0]
  #   instance.block_id = data["_block_id"]
  #   instance.cover_temp = data["_coverTemp"]
  #   instance.cover_enabled = data["_coverEnabled"]
  #   instance.infinite_holds = data["_infinite_holds"]
  #   return instance
  pass


def _generate_run_info_files(
  protocol: Protocol,
  block_id: int,
  sample_volume: float = 50,
  run_mode: str = "Fast",
  protocol_name: str = "PCR_Protocol",
  cover_enabled: bool = True,
  cover_temp: float = 105.0,
  user_name="LifeTechnologies",
  file_version="1.0.1",
  remote_run="true",
  hub="testhub",
  user="Guest",
  notes="",
  default_ramp_rate=100,
  ramp_rate_unit="DEGREES_PER_SECOND",
):
  root = ET.Element("TCProtocol")
  file_version_el = ET.SubElement(root, "FileVersion")
  file_version_el.text = file_version

  protocol_name_el = ET.SubElement(root, "ProtocolName")
  protocol_name_el.text = protocol_name

  user_name_el = ET.SubElement(root, "UserName")
  user_name_el.text = user_name

  block_id_el = ET.SubElement(root, "BlockID")
  block_id_el.text = str(block_id + 1)

  sample_volume_el = ET.SubElement(root, "SampleVolume")
  sample_volume_el.text = str(sample_volume)

  run_mode_el = ET.SubElement(root, "RunMode")
  run_mode_el.text = str(run_mode)

  cover_temp_el = ET.SubElement(root, "CoverTemperature")
  cover_temp_el.text = str(cover_temp)

  cover_setting_el = ET.SubElement(root, "CoverSetting")
  cover_setting_el.text = "On" if cover_enabled else "Off"

  for stage_obj in protocol.stages:
    if isinstance(stage_obj, Step):
      stage = Stage(steps=[stage_obj], repeats=1)
    else:
      stage = stage_obj

    stage_el = ET.SubElement(root, "TCStage")
    stage_flag_el = ET.SubElement(stage_el, "StageFlag")
    stage_flag_el.text = "CYCLING"

    num_repetitions_el = ET.SubElement(stage_el, "NumOfRepetitions")
    num_repetitions_el.text = str(stage.repeats)

    for step in stage.steps:
      step_el = ET.SubElement(stage_el, "TCStep")

      ramp_rate_el = ET.SubElement(step_el, "RampRate")
      ramp_rate_el.text = str(
        int(step.rate if step.rate is not None else default_ramp_rate) / 100 * 6
      )

      ramp_rate_unit_el = ET.SubElement(step_el, "RampRateUnit")
      ramp_rate_unit_el.text = ramp_rate_unit

      for t_val in step.temperature:
        temp_el = ET.SubElement(step_el, "Temperature")
        temp_el.text = str(t_val)

      hold_time_el = ET.SubElement(step_el, "HoldTime")
      if step.hold_seconds == float("inf"):
        hold_time_el.text = "-1"
      elif step.hold_seconds == 0:
        hold_time_el.text = "0"
      else:
        hold_time_el.text = str(step.hold_seconds)

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
    xml_declaration + reparsed.toprettyxml(indent="  ")[len('<?xml version="1.0" ?>') :]
  )

  output2_lines = [
    f"-remoterun= {remote_run}",
    f"-hub= {hub}",
    f"-user= {user}",
    f"-method= {protocol_name}",
    f"-volume= {sample_volume}",
    f"-cover= {cover_temp}",
    f"-mode= {run_mode}",
    f"-coverEnabled= {'On' if cover_enabled else 'Off'}",
    f"-notes= {notes}",
  ]
  output2_string = "\n".join(output2_lines)

  return pretty_xml_as_string, output2_string


def _gen_protocol_data(
  protocol: Protocol,
  block_id: int,
  sample_volume: float,
  run_mode: str,
  cover_temp: float,
  cover_enabled: bool,
  protocol_name: str,
  stage_name_prefixes: List[str],
):
  def step_to_scpi(step: Step, step_index: int):
    data = {
      "cmd": "STEP",
      "params": {},
      "args": [str(step_index)],
      "tag": "multiline.step",
      "multiline": [],
    }

    infinite_hold = step.hold_seconds == float("inf")

    if infinite_hold and min(step.temperature) < 20:
      data["multiline"].append({"cmd": "CoverRAMP", "params": {}, "args": ["30"]})

    data["multiline"].append(
      {
        "cmd": "RAMP",
        "params": {"rate": str(step.rate if step.rate is not None else 100)},
        "args": [str(t) for t in step.temperature],
      }
    )

    if infinite_hold:
      data["multiline"].append({"cmd": "HOLD", "params": {}, "args": []})
    elif step.hold_seconds > 0:
      data["multiline"].append({"cmd": "HOLD", "params": {}, "args": [str(step.hold_seconds)]})

    return data

  def stage_to_scpi(stage: Stage, stage_index: int, stage_name_prefix: str):
    print(stage)
    return {
      "cmd": "STAGe",
      "params": {"repeat": str(stage.repeats)},
      "args": [stage_index, f"{stage_name_prefix}_{stage_index}"],
      "tag": "multiline.stage",
      "multiline": [step_to_scpi(step, i + 1) for i, step in enumerate(stage.steps)],
    }

  stages = protocol.stages
  assert len(stages) == len(
    stage_name_prefixes
  ), "Number of stages must match number of stage names"

  data = {
    # "status": "OK",
    "cmd": f"TBC{block_id + 1}:Protocol",
    "params": {"Volume": str(sample_volume), "RunMode": run_mode},
    "args": [protocol_name],
    "tag": "multiline.outer",
    "multiline": [
      stage_to_scpi(stage, stage_index=i + 1, stage_name_prefix=stage_name_prefix)
      for i, (stage, stage_name_prefix) in enumerate(zip(stages, stage_name_prefixes))
    ],
    "_blockId": block_id + 1,
    "_coverTemp": cover_temp,
    "_coverEnabled": "On" if cover_enabled else "Off",
    "_infinite_holds": [
      [stage_index, step_index]
      for stage_index, stage in enumerate(stages)
      for step_index, step in enumerate(stage.steps)
      if step.hold_seconds == float("inf")
    ],
  }

  return data


class ProflexBackend(ThermocyclerBackend):
  """Backend for Proflex thermocycler."""

  def __init__(self, ip: str, port: int = 7000, shared_secret: bytes = b"f4ct0rymt55"):
    self.ip = ip
    self.port = port
    self.device_shared_secret = shared_secret
    self.io = Socket(host=ip, port=port)
    self._num_blocks: Optional[int] = None
    self.num_temp_zones = 0
    self.available_blocks: List[int] = []
    self.logger = logging.getLogger("pylabrobot.thermocycling.proflex")
    self.current_run = None
    self.running_blocks: List[int] = []
    self.prot_time_elapsed = 0
    self.prot_time_remaining = 0

  @property
  def num_blocks(self) -> int:
    if self._num_blocks is None:
      raise ValueError("Number of blocks not set. Call setup() first.")
    return self._num_blocks

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

  async def _read_response(self, timeout=1, read_once=True):
    try:
      response = await self.io.read(timeout=timeout)
      self.logger.debug("Response received: %s", response)
      return response
    except TimeoutError:
      return ""
    except Exception as e:
      self.logger.error("Error reading from socket: %s", e)
      return ""

  async def send_command(self, data, response_timeout=1, read_once=True):
    msg = self._build_scpi_msg(data)
    msg += "\r\n"
    print(f"Sending command: {msg.strip()}", response_timeout)
    self.logger.debug("Command sent: %s", msg.strip())

    await self.io.write(msg, timeout=response_timeout)
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
    else:
      raise NotImplementedError("Only BID 12 and 13 are supported")

  async def _get_available_blocks(self):
    await self._scpi_authenticate()
    await self._load_num_blocks_and_type()
    assert self._num_blocks is not None, "Number of blocks not set"
    for block_id in range(self._num_blocks):
      block_error = await self.get_error(block_id=block_id)
      if block_error != "0":
        raise ValueError(f"Block {block_id} has error: {block_error}")
      run_name = await self.get_run_name(block_id=block_id)
      if run_name == "-":
        if block_id not in self.available_blocks:
          self.available_blocks.append(block_id)
    return self.available_blocks

  async def get_block_current_temperature(self, block_id=1):
    res = await self.send_command({"cmd": f"TBC{block_id}:TBC:BlockTemperatures?"})
    return self._parse_scpi_response(res)["args"]

  async def get_sample_temps(self, block_id=1):
    res = await self.send_command({"cmd": f"TBC{block_id}:TBC:SampleTemperatures?"})
    return self._parse_scpi_response(res)["args"]

  async def get_nickname(self):
    res = await self.send_command({"cmd": "SYST:SETT:NICK?"})
    return self._parse_scpi_response(res)["args"][0]

  async def set_nickname(self, nickname: str):
    res = await self.send_command({"cmd": "SYST:SETT:NICK", "args": [nickname]})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to set nickname")

  async def set_block_idle_temp(self, temp: float = 25, control_enabled=1, block_id=1):
    if block_id not in self.available_blocks:
      raise ValueError(f"Block {block_id} is not available")
    res = await self.send_command({"cmd": f"TBC{block_id}:BLOCK", "args": [control_enabled, temp]})
    if self._parse_scpi_response(res)["status"] != "NEXT":
      raise ValueError("Failed to set block idle temperature")
    follow_up = await self._read_response()
    if self._parse_scpi_response(follow_up)["status"] != "OK":
      raise ValueError("Failed to set block idle temperature")

  async def set_cover_idle_temp(self, temp: float = 105, control_enabled=1, block_id=1):
    if block_id not in self.available_blocks:
      raise ValueError(f"Block {block_id} not available")
    res = await self.send_command({"cmd": f"TBC{block_id}:COVER", "args": [control_enabled, temp]})
    if self._parse_scpi_response(res)["status"] != "NEXT":
      raise ValueError("Failed to set cover idle temperature")
    follow_up = await self._read_response()
    if self._parse_scpi_response(follow_up)["status"] != "OK":
      raise ValueError("Failed to set cover idle temperature")

  async def set_block_temperature(self, temperature: List[float], block_id: int, rate: float = 100):
    if block_id not in self.available_blocks:
      raise ValueError(f"Block {block_id} not available")
    res = await self.send_command(
      {"cmd": f"TBC{block_id}:RAMP", "params": {"rate": rate}, "args": temperature},
      response_timeout=60,
    )
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to ramp block temperature")

  async def block_ramp_single_temp(self, target_temp: float, rate: float = 100, block_id=1):
    if block_id not in self.available_blocks:
      raise ValueError(f"Block {block_id} not available")
    res = await self.send_command(
      {"cmd": f"TBC{block_id}:BlockRAMP", "params": {"rate": rate}, "args": [target_temp]},
      response_timeout=60,
    )
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to ramp block temperature")

  async def set_lid_temperature(self, temperature: List[float], block_id: int):
    assert len(set(temperature)) == 1, "Lid temperature must be the same for all zones"
    target_temp = temperature[0]
    if block_id not in self.available_blocks:
      raise ValueError(f"Block {block_id} not available")
    res = await self.send_command(
      {"cmd": f"TBC{block_id}:CoverRAMP", "params": {}, "args": [target_temp]}, response_timeout=60
    )
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to ramp cover temperature")

  async def buzzer_on(self):
    res = await self.send_command({"cmd": "BUZZer+"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to turn on buzzer")

  async def buzzer_off(self):
    res = await self.send_command({"cmd": "BUZZer-"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to turn off buzzer")

  async def continue_run(self, block_id: int):
    for _ in range(3):
      await asyncio.sleep(1)
      res = await self.send_command({"cmd": f"TBC{block_id}:CONTinue"})
      if self._parse_scpi_response(res)["status"] != "OK":
        raise ValueError("Failed to continue from indefinite hold")

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

  async def check_run_exists(self, run_name) -> bool:
    res = await self.send_command(
      {"cmd": "RUNS:EXISTS?", "args": [run_name], "params": {"type": "folders"}}
    )
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to check if run exists")
    return self._parse_scpi_response(res)["args"][1] == "True"

  async def create_run(self, run_name):
    res = await self.send_command({"cmd": "RUNS:NEW", "args": [run_name]}, response_timeout=10)
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to create run")
    return self._parse_scpi_response(res)["args"][0]

  async def get_run_name(self, block_id: int):
    res = await self.send_command({"cmd": f"TBC{block_id + 1}:RUNTitle?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get run title")
    return self._parse_scpi_response(res)["args"][0]

  async def get_run_progress(self, block_id):
    res = await self.send_command({"cmd": f"TBC{block_id + 1}:RUNProgress?"})
    parsed_res = self._parse_scpi_response(res)
    if parsed_res["status"] != "OK":
      raise ValueError("Failed to get run status")
    if parsed_res["cmd"] == f"TBC{block_id + 1}:RunProtocol":
      await self._read_response()
      return False
    return self._parse_scpi_response(res)["params"]

  async def get_estimated_run_time(self, block_id):
    res = await self.send_command({"cmd": f"TBC{block_id + 1}:ESTimatedTime?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get estimated run time")
    return self._parse_scpi_response(res)["args"][0]

  async def get_elapsed_run_time(self, block_id):
    res = await self.send_command({"cmd": f"TBC{block_id + 1}:ELAPsedTime?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get elapsed run time")
    return int(self._parse_scpi_response(res)["args"][0])

  async def get_remaining_run_time(self, block_id):
    res = await self.send_command({"cmd": f"TBC{block_id + 1}:REMainingTime?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get remaining run time")
    return int(self._parse_scpi_response(res)["args"][0])

  async def get_error(self, block_id):
    res = await self.send_command({"cmd": f"TBC{block_id + 1}:ERROR?"})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get error")
    return self._parse_scpi_response(res)["args"][0]

  async def power_on(self):
    res = await self.send_command({"cmd": "POWER", "args": ["On"]}, response_timeout=20)
    if res == "" or self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to power on")

  async def power_off(self):
    res = await self.send_command({"cmd": "POWER", "args": ["Off"]})
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to power off")

  async def _scpi_write_run_info(
    self,
    protocol: Protocol,
    run_name: str,
    block_id: int,
    sample_volume: float,
    run_mode: str,
    protocol_name: str,
    cover_temp: float,
    cover_enabled: bool,
    user_name: str,
  ):
    xmlfile, tmpfile = _generate_run_info_files(
      protocol=protocol,
      block_id=block_id,
      sample_volume=sample_volume,
      run_mode=run_mode,
      protocol_name=protocol_name,
      cover_temp=cover_temp,
      cover_enabled=cover_enabled,
      # user_name=user_name,  # default here is "LifeTechnologies"
    )
    await self._write_file(f"runs:{run_name}/{protocol_name}.method", xmlfile)
    await self._write_file(f"runs:{run_name}/{run_name}.tmp", tmpfile)

  async def _scpi_run_protocol(
    self,
    protocol: Protocol,
    run_name: str,
    block_id: int,
    sample_volume: float,
    run_mode: str,
    protocol_name: str,
    cover_temp: float,
    cover_enabled: bool,
    user_name: str,
    stage_name_prefixes: List[str],
  ):
    load_res = await self.send_command(
      data=_gen_protocol_data(
        protocol=protocol,
        block_id=block_id,
        sample_volume=sample_volume,
        run_mode=run_mode,
        cover_temp=cover_temp,
        cover_enabled=cover_enabled,
        protocol_name=protocol_name,
        stage_name_prefixes=stage_name_prefixes,
      ),
      response_timeout=5,
      read_once=False,
    )
    if self._parse_scpi_response(load_res)["status"] != "OK":
      self.logger.error(load_res)
      self.logger.error("Protocol failed to load")
      raise ValueError("Protocol failed to load")

    start_res = await self.send_command(
      {
        "cmd": f"TBC{block_id + 1}:RunProtocol",
        "params": {
          "User": user_name,
          "CoverTemperature": cover_temp,
          "CoverEnabled": "On" if cover_enabled else "Off",
        },
        "args": [protocol_name, run_name],
      },
      response_timeout=2,
      read_once=False,
    )

    if self._parse_scpi_response(start_res)["status"] == "NEXT":
      self.logger.info("Protocol started")
    else:
      self.logger.error(start_res)
      self.logger.error("Protocol failed to start")
      raise ValueError("Protocol failed to start")

    total_time = await self.get_estimated_run_time(block_id=block_id)
    total_time = float(total_time)
    self.logger.info(f"Estimated run time: {total_time}")
    self.current_run = run_name
    self.running_blocks.append(block_id)

  async def abort_run(self, block_id: int):
    run_name = await self.get_run_name(block_id=block_id)
    if run_name == "-":
      self.logger.error("Failed to abort protocol: no run is currently running on this block")
      raise RuntimeError("No run is currently running on this block")
    abort_res = await self.send_command({"cmd": f"TBC{block_id + 1}:AbortRun", "args": [run_name]})
    if self._parse_scpi_response(abort_res)["status"] != "OK":
      self.logger.error(abort_res)
      self.logger.error("Failed to abort protocol")
      raise ValueError("Failed to abort protocol")
    self.logger.info("Protocol aborted")

  # TODO: nice object for returning

  @dataclass
  class RunProgress:
    stage: str
    elapsed_time: int
    remaining_time: int
    running: bool

  async def check_if_running(self, protocol: Protocol, block_id: int):
    progress = await self.get_run_progress(block_id=block_id)
    if not progress:
      self.logger.info("Protocol completed")
      return RunProgress(
        running=False, stage="completed", elapsed_time=self.prot_time_elapsed, remaining_time=0
      )

    if progress["RunTitle"] == "-":
      await self._read_response(timeout=5)
      self.logger.info("Protocol completed")
      return RunProgress(
        running=False, stage="completed", elapsed_time=self.prot_time_elapsed, remaining_time=0
      )

    if progress["Stage"] == "POSTRun":
      self.logger.info("Protocol in POSTRun")
      return RunProgress(
        running=True, stage="POSTRun", elapsed_time=self.prot_time_elapsed, remaining_time=0
      )

    # TODO: move to separate wait method
    if progress["Stage"] != "-" and progress["Step"] != "-":
      current_step = protocol.stages[int(progress["Stage"]) - 1].steps[int(progress["Step"]) - 1]
      if current_step.hold_seconds == float("inf"):
        while True:
          block_temps = await self.get_block_temps(block_id=block_id)
          target_temps = current_step.temperature
          if all(
            abs(float(block_temps[i]) - target_temps[i]) < 0.5 for i in range(len(block_temps))
          ):
            break
          await asyncio.sleep(5)
        self.logger.info("Infinite hold")
        return RunProgress(
          running=False,
          stage="infinite_hold",
          elapsed_time=self.prot_time_elapsed,
          remaining_time=self.prot_time_remaining,
        )

    time_elapsed = await self.get_elapsed_run_time(block_id=block_id)
    self.prot_time_elapsed = time_elapsed
    remaining_time = await self.get_remaining_run_time(block_id=block_id)
    self.prot_time_remaining = remaining_time

    self.logger.info(f"Elapsed time: {time_elapsed}")
    self.logger.info(f"Remaining time: {remaining_time}")
    return RunProgress(
      running=True,
      stage=progress["Stage"],
      elapsed_time=time_elapsed,
      remaining_time=remaining_time,
    )

  # *************Three core methods for running a protocol***********************

  async def setup(
    self, block_idle_temp=25, cover_idle_temp=105, blocks_to_setup: Optional[List[int]] = None
  ):
    await self._scpi_authenticate()
    await self.power_on()
    await self._load_num_blocks_and_type()
    if blocks_to_setup is None:
      await self._get_available_blocks()
    else:
      self.available_blocks = blocks_to_setup
    for block_index in self.available_blocks:
      await self.set_block_idle_temp(temp=block_idle_temp, block_id=block_index)
      await self.set_cover_idle_temp(temp=cover_idle_temp, block_id=block_index)

  async def open_lid(self):
    raise NotImplementedError("Open lid command is not implemented for Proflex thermocycler")

  async def close_lid(self):
    raise NotImplementedError("Close lid command is not implemented for Proflex thermocycler")

  async def deactivate_lid(self, block_id: int):
    return await self.set_cover_idle_temp(control_enabled=0, block_id=block_id)

  async def deactivate_block(self, block_id: int):
    return await self.set_block_idle_temp(control_enabled=0, block_id=block_id)

  async def get_lid_current_temperature(self, block_id: int) -> List[float]:
    res = await self.send_command({"cmd": f"TBC{block_id}:TBC:CoverTemperatures?"})
    return self._parse_scpi_response(res)["args"]

  async def run_protocol(
    self,
    protocol: Protocol,
    block_id: int,
    run_name="testrun",
    user="Admin",
    sample_volume: float = 50,
    run_mode: str = "Fast",
    cover_temp: float = 105,
    cover_enabled=True,
    protocol_name: str = "PCR_Protocol",
    stage_name_prefixes: Optional[List[str]] = None,
  ):
    if await self.check_run_exists(run_name):
      self.logger.warning(f"Run {run_name} already exists")
    else:
      await self.create_run(run_name)

    # wrap all Steps in Stage objects where necessary
    for i, stage in enumerate(protocol.stages):
      if isinstance(stage, Step):
        protocol.stages[i] = Stage(steps=[stage], repeats=1)

    stage_name_prefixes = stage_name_prefixes or [f"Stage_" for i in range(len(protocol.stages))]

    await self._scpi_write_run_info(
      protocol=protocol,
      block_id=block_id,
      run_name=run_name,
      user_name=user,
      sample_volume=sample_volume,
      run_mode=run_mode,
      cover_temp=cover_temp,
      cover_enabled=cover_enabled,
      protocol_name=protocol_name,
    )
    await self._scpi_run_protocol(
      protocol=protocol,
      run_name=run_name,
      block_id=block_id,
      sample_volume=sample_volume,
      run_mode=run_mode,
      cover_temp=cover_temp,
      cover_enabled=cover_enabled,
      protocol_name=protocol_name,
      user_name=user,
      stage_name_prefixes=stage_name_prefixes,
    )

  async def stop(self):
    for block_id in self.running_blocks:
      is_running = (await self.get_run_name(block_id=block_id)) != "-"
      if is_running:
        await self._scpi_abort_run(block_id, self.current_run)
        await asyncio.sleep(10)

      await self.deactivate_lid(block_id=block_id)
      await self.deactivate_block(block_id=block_id)

    await self.io.stop()

  async def get_block_status(self, *args, **kwargs):
    raise NotImplementedError

  async def get_current_cycle_index(self, block_id: int) -> int:
    progress = await self.get_run_progress(block_id=block_id)
    if progress is None:
      raise RuntimeError("No progress information available")

    if progress["RunTitle"] == "-":
      await self._read_response(timeout=5)
      raise RuntimeError("Protocol completed or not started")

    if progress["Stage"] == "POSTRun":
      raise RuntimeError("Protocol in POSTRun stage, no current cycle index")

    if progress["Stage"] != "-" and progress["Step"] != "-":
      return int(progress["Stage"]) - 1

    raise RuntimeError("Current cycle index is not available, protocol may not be running")

  async def get_current_step_index(self, block_id: int) -> int:
    progress = await self.get_run_progress(block_id=block_id)
    if progress is None:
      raise RuntimeError("No progress information available")

    if progress["RunTitle"] == "-":
      await self._read_response(timeout=5)
      raise RuntimeError("Protocol completed or not started")

    if progress["Stage"] == "POSTRun":
      raise RuntimeError("Protocol in POSTRun stage, no current cycle index")

    if progress["Stage"] != "-" and progress["Step"] != "-":
      return int(progress["Step"]) - 1

    raise RuntimeError("Current step index is not available, protocol may not be running")

  async def get_hold_time(self, *args, **kwargs):
    # deprecated
    raise NotImplementedError

  async def get_lid_open(self, *args, **kwargs):
    raise NotImplementedError

  async def get_lid_status(self, *args, **kwargs) -> LidStatus:
    raise NotImplementedError

  async def get_lid_target_temperature(self, *args, **kwargs):
    # deprecated
    raise NotImplementedError

  async def get_total_cycle_count(self, *args, **kwargs):
    # deprecated
    raise NotImplementedError

  async def get_total_step_count(self, *args, **kwargs):
    # deprecated
    raise NotImplementedError

  async def get_block_target_temperature(self, *args, **kwargs):
    # deprecated
    raise NotImplementedError
