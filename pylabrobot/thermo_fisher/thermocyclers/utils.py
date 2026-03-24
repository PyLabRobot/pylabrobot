import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List
from xml.dom import minidom

from pylabrobot.capabilities.thermocycling import (
  Protocol,
  Stage,
  Step,
)


@dataclass
class RunProgress:
  stage: str
  elapsed_time: int
  remaining_time: int
  running: bool


def _generate_run_info_files(
  protocol: Protocol,
  block_id: int,
  sample_volume: float,
  run_mode: str,
  protocol_name: str,
  cover_enabled: bool,
  cover_temp: float,
  user_name: str,
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
  def step_to_scpi(step: Step, step_index: int) -> dict:
    multiline: List[dict] = []

    infinite_hold = step.hold_seconds == float("inf")

    if infinite_hold and min(step.temperature) < 20:
      multiline.append({"cmd": "CoverRAMP", "params": {}, "args": ["30"]})

    multiline.append(
      {
        "cmd": "RAMP",
        "params": {"rate": str(step.rate if step.rate is not None else 100)},
        "args": [str(t) for t in step.temperature],
      }
    )

    if infinite_hold:
      multiline.append({"cmd": "HOLD", "params": {}, "args": []})
    elif step.hold_seconds > 0:
      multiline.append({"cmd": "HOLD", "params": {}, "args": [str(step.hold_seconds)]})

    return {
      "cmd": "STEP",
      "params": {},
      "args": [str(step_index)],
      "tag": "multiline.step",
      "multiline": multiline,
    }

  def stage_to_scpi(stage: Stage, stage_index: int, stage_name_prefix: str) -> dict:
    return {
      "cmd": "STAGe",
      "params": {"repeat": str(stage.repeats)},
      "args": [stage_index, f"{stage_name_prefix}_{stage_index}"],
      "tag": "multiline.stage",
      "multiline": [step_to_scpi(step, i + 1) for i, step in enumerate(stage.steps)],
    }

  stages = protocol.stages
  assert len(stages) == len(stage_name_prefixes), (
    "Number of stages must match number of stage names"
  )

  data = {
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
