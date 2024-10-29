import os
import platform
import textwrap
import xml
import xml.dom.minidom
import xml.etree.ElementTree as ET

from typing import List
import requests


import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List
import xml.etree.ElementTree as ET
import datetime
from typing import List

def format_datetime(dt: datetime.datetime) -> str:
    dt = dt.astimezone()
    dt_str = dt.isoformat(timespec='microseconds')
    if '.' in dt_str:
        separator = "+" if '+' in dt_str else "-"
        datetime_part, timezone_part = dt_str.rsplit(separator, 1)
        if len(datetime_part.split('.')[1]) == 6:
            datetime_part += '0'
        dt_str = f"{datetime_part}{separator}{timezone_part}"
    return dt_str


def parse_datetime_with_precision(date_string):
    # Trim to 6 microsecond digits if there are more than 6
    if '.' in date_string:
        date_string, timezone = date_string[:-6], date_string[-6:]  # Split datetime and timezone part
        date_string = date_string[:26] + timezone  # Ensure only 6 microsecond digits
    return datetime.datetime.fromisoformat(date_string)


class Step:
    def __init__(self, number: int, slope: float, plateau_temperature: float, plateau_time: int,
                 overshoot_slope1: float, overshoot_temperature: float, overshoot_time: int,
                 overshoot_slope2: float, goto_number: int, loop_number: int, pid_number: int, lid_temp: int):
        self.number = number
        self.slope = slope
        self.plateau_temperature = plateau_temperature
        self.plateau_time = plateau_time
        self.overshoot_slope1 = overshoot_slope1
        self.overshoot_temperature = overshoot_temperature
        self.overshoot_time = overshoot_time
        self.overshoot_slope2 = overshoot_slope2
        self.goto_number = goto_number
        self.loop_number = loop_number
        self.pid_number = pid_number
        self.lid_temp = lid_temp

    def to_xml(self):
        step_element = ET.Element("Step")
        ET.SubElement(step_element, "Number").text = str(self.number)
        ET.SubElement(step_element, "Slope").text = str(self.slope)
        ET.SubElement(step_element, "PlateauTemperature").text = str(self.plateau_temperature)
        ET.SubElement(step_element, "PlateauTime").text = str(self.plateau_time)
        ET.SubElement(step_element, "OverShootSlope1").text = str(self.overshoot_slope1)
        ET.SubElement(step_element, "OverShootTemperature").text = str(self.overshoot_temperature)
        ET.SubElement(step_element, "OverShootTime").text = str(self.overshoot_time)
        ET.SubElement(step_element, "OverShootSlope2").text = str(self.overshoot_slope2)
        ET.SubElement(step_element, "GotoNumber").text = str(self.goto_number)
        ET.SubElement(step_element, "LoopNumber").text = str(self.loop_number)
        ET.SubElement(step_element, "PIDNumber").text = str(self.pid_number)
        ET.SubElement(step_element, "LidTemp").text = str(self.lid_temp)
        return step_element


class PID:
    def __init__(self, number: int, p_heating: float, p_cooling: float, i_heating: float, i_cooling: float,
                 d_heating: float, d_cooling: float, p_lid: float, i_lid: float):
        self.number = number
        self.p_heating = p_heating
        self.p_cooling = p_cooling
        self.i_heating = i_heating
        self.i_cooling = i_cooling
        self.d_heating = d_heating
        self.d_cooling = d_cooling
        self.p_lid = p_lid
        self.i_lid = i_lid

    def to_xml(self):
        pid_element = ET.Element("PID", number=str(self.number))
        ET.SubElement(pid_element, "PHeating").text = str(self.p_heating)
        ET.SubElement(pid_element, "PCooling").text = str(self.p_cooling)
        ET.SubElement(pid_element, "IHeating").text = str(self.i_heating)
        ET.SubElement(pid_element, "ICooling").text = str(self.i_cooling)
        ET.SubElement(pid_element, "DHeating").text = str(self.d_heating)
        ET.SubElement(pid_element, "DCooling").text = str(self.d_cooling)
        ET.SubElement(pid_element, "PLid").text = str(self.p_lid)
        ET.SubElement(pid_element, "ILid").text = str(self.i_lid)
        return pid_element


class Method:
    def __init__(self, name: str, creator: str, date_time: datetime.datetime, variant: int, plate_type: int,
                 fluid_quantity: float, post_heating: bool, start_block_temperature: int, start_lid_temperature: int,
                 steps: List[Step], pid_settings: List[PID]):
        self.name = name
        self.creator = creator
        self.date_time = date_time
        self.variant = variant
        self.plate_type = plate_type
        self.fluid_quantity = fluid_quantity
        self.post_heating = post_heating
        self.start_block_temperature = start_block_temperature
        self.start_lid_temperature = start_lid_temperature
        self.steps = steps
        self.pid_settings = pid_settings

    def to_xml(self):
        method_element = ET.Element("Method", methodName=self.name, creator=self.creator, dateTime=format_datetime(self.date_time))
        ET.SubElement(method_element, "Variant").text = str(self.variant)
        ET.SubElement(method_element, "PlateType").text = str(self.plate_type)
        ET.SubElement(method_element, "FluidQuantity").text = str(self.fluid_quantity)
        ET.SubElement(method_element, "PostHeating").text = str(self.post_heating).lower()
        ET.SubElement(method_element, "StartBlockTemperature").text = str(self.start_block_temperature)
        ET.SubElement(method_element, "StartLidTemperature").text = str(self.start_lid_temperature)

        for step in self.steps:
            method_element.append(step.to_xml())

        pidset_element = ET.SubElement(method_element, "PIDSet")
        for pid in self.pid_settings:
            pidset_element.append(pid.to_xml())

        return method_element


class PreMethod:
    def __init__(self, name: str, creator: str, date_time: datetime.datetime, target_block_temperature: int, target_lid_temp: int):
        self.name = name
        self.creator = creator
        self.date_time = date_time
        self.target_block_temperature = target_block_temperature
        self.target_lid_temp = target_lid_temp

    def to_xml(self):
        pre_method_element = ET.Element("PreMethod", methodName=self.name, creator=self.creator, dateTime=format_datetime(self.date_time))
        ET.SubElement(pre_method_element, "TargetBlockTemperature").text = str(self.target_block_temperature)
        ET.SubElement(pre_method_element, "TargetLidTemp").text = str(self.target_lid_temp)
        return pre_method_element

from xml.dom import minidom
class MethodSet:
    def __init__(self, delete_all_methods: bool, pre_method: PreMethod, methods: List[Method]):
        self.delete_all_methods = delete_all_methods
        self.pre_method = pre_method
        self.methods = methods

    def to_xml(self, pretty_print=False):
        root = ET.Element("MethodSet")
        ET.SubElement(root, "DeleteAllMethods").text = str(self.delete_all_methods).lower()
        root.append(self.pre_method.to_xml())
        for method in self.methods:
            root.append(method.to_xml())

        xml_string = ET.tostring(root, encoding="unicode")
        xml_declaration = '<?xml version="1.0" encoding="utf-8"?>\r\n'

        if pretty_print:
            xml_string = minidom.parseString(xml_string).toprettyxml(indent="  ")
            xml_string = "\r\n".join(line for line in xml_string.splitlines() if line.strip())
            return xml_declaration + xml_string

        return xml_declaration + xml_string.replace("\n", "\r\n")

    @classmethod
    def from_xml(cls, xml_data: str):
        root = ET.fromstring(xml_data)

        delete_all_methods = root.find('DeleteAllMethods').text.lower() == 'true'

        pre_method_element = root.find('PreMethod')
        pre_method = PreMethod(
            name=pre_method_element.get('methodName'),
            creator=pre_method_element.get('creator'),
            date_time=datetime.datetime.fromisoformat(pre_method_element.get('dateTime')[:26]),  # Parse up to 6 microsecond digits
            target_block_temperature=int(pre_method_element.find('TargetBlockTemperature').text),
            target_lid_temp=int(pre_method_element.find('TargetLidTemp').text)
        )

        methods = []
        for method_element in root.findall('Method'):
            steps = []
            for step_element in method_element.findall('Step'):
                steps.append(Step(
                    number=int(step_element.find('Number').text),
                    slope=float(step_element.find('Slope').text),
                    plateau_temperature=int(step_element.find('PlateauTemperature').text),
                    plateau_time=int(step_element.find('PlateauTime').text),
                    overshoot_slope1=float(step_element.find('OverShootSlope1').text),
                    overshoot_temperature=int(step_element.find('OverShootTemperature').text),
                    overshoot_time=int(step_element.find('OverShootTime').text),
                    overshoot_slope2=float(step_element.find('OverShootSlope2').text),
                    goto_number=int(step_element.find('GotoNumber').text),
                    loop_number=int(step_element.find('LoopNumber').text),
                    pid_number=int(step_element.find('PIDNumber').text),
                    lid_temp=int(step_element.find('LidTemp').text)
                ))

            pid_settings = []
            for pid_element in method_element.find('PIDSet').findall('PID'):
                pid_settings.append(PID(
                    number=int(pid_element.get('number')),
                    p_heating=int(pid_element.find('PHeating').text),
                    p_cooling=int(pid_element.find('PCooling').text),
                    i_heating=int(pid_element.find('IHeating').text),
                    i_cooling=int(pid_element.find('ICooling').text),
                    d_heating=int(pid_element.find('DHeating').text),
                    d_cooling=int(pid_element.find('DCooling').text),
                    p_lid=int(pid_element.find('PLid').text),
                    i_lid=int(pid_element.find('ILid').text)
                ))

            methods.append(Method(
                name=method_element.get('methodName'),
                creator=method_element.get('creator'),
                date_time=datetime.datetime.fromisoformat(method_element.get('dateTime')[:26]),  # Parse up to 6 microsecond digits
                variant=int(method_element.find('Variant').text),
                plate_type=int(method_element.find('PlateType').text),
                fluid_quantity=int(method_element.find('FluidQuantity').text),
                post_heating=method_element.find('PostHeating').text.lower() == 'true',
                start_block_temperature=int(method_element.find('StartBlockTemperature').text),
                start_lid_temperature=int(method_element.find('StartLidTemperature').text),
                steps=steps,
                pid_settings=pid_settings
            ))

        return cls(delete_all_methods, pre_method, methods)



class ThermoCycler:
  def __init__(self, ip: str) -> None:
    self.ip = ip
    self.port = 8080
    self.timeout = 5

  async def run(self, steps: List[Method]):
    print("Running steps", steps)

  @classmethod
  async def get_device_ip(cls):
    # list devices using arp
    arp_entries = []
    platform = platform.system()
    if platform.lower() == 'Windows':
      # Windows: Internet Address, Physical Address, Type
      pattern = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F\-]{17})\s+(\w+)")
      for match in pattern.finditer(output):
        arp_entries.append({
          'IP Address': match.group(1),
          'MAC Address': match.group(2),
          'Type': match.group(3),
          'Name': None  # Windows doesn't show a name in arp -a output
        })
    elif platform.lower() == 'Darwin' or platform.lower() == 'Linux':
      # macOS & Linux: Optional Name, IP Address, MAC Address
      pattern = re.compile(r"(?:(\S+)\s+)?\((\d{1,3}(?:\.\d{1,3}){3})\)\s+at\s+([0-9a-fA-F\:]{17})")
      for match in pattern.finditer(output):
        arp_entries.append({
          'Name': match.group(1) if match.group(1) else None,  # Capture the name if present
          'IP Address': match.group(2),
          'MAC Address': match.group(3),
        })
    return arp_entries

  async def get_status(self):
    request_id = 1305594243
    request = f"""
    <s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
      <s:Body>
        <GetStatus xmlns="http://sila.coop" xmlns:i="http://www.w3.org/2001/XMLSchema-instance">
          <requestId>{request_id}</requestId>
        </GetStatus>
      </s:Body>
    </s:Envelope>
    """

    resp = """
    <?xml version="1.0" encoding="UTF-8"?>
    <SOAP-ENV:Envelope
      xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
      xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xmlns:xsd="http://www.w3.org/2001/XMLSchema"
      xmlns:i="http://inheco.com"
      xmlns:s="http://sila.coop"
    >
    <SOAP-ENV:Body>
      <s:GetStatusResponse>
      <s:GetStatusResult>
        <s:returnCode>1</s:returnCode>
        <s:message>Success.</s:message>
        <s:duration>PT1S</s:duration>
        <s:deviceClass>30</s:deviceClass>
      </s:GetStatusResult>
      <s:deviceId>122b63a3-8fe1-40df-a535-8803a971951a</s:deviceId>
      <s:state>inError</s:state>
      <s:subStates>
        <s:CommandDescription>
          <s:requestId>881683259</s:requestId>
          <s:commandName>Reset</s:commandName>
          <s:queuePosition>1</s:queuePosition>
          <s:startedAt>2024-09-12T10:35:20Z</s:startedAt>
          <s:currentState>processing</s:currentState>
          <s:dataWaiting xsi:nil="true"/>
        </s:CommandDescription>
      </s:subStates>
      <s:locked>false</s:locked>
      <s:PMSId>http://169.254.193.225:7071/ihc</s:PMSId>
      <s:currentTime>2024-09-12T10:35:21Z</s:currentTime>
      </s:GetStatusResponse></SOAP-ENV:Body>
    </SOAP-ENV:Envelope>
    """

    data = ET.fromstring(resp)
    # state = inError
    status = data.find(".//{http://sila.coop}state").text

  command_id = 980077706

  def send_command(self, command: str):
    ThermoCycler.command_id += 1
    req = f"""<s:Envelope
      xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
      <s:Body>
        <{command}
          xmlns="http://sila.coop"
          xmlns:i="http://www.w3.org/2001/XMLSchema-instance">
          <requestId>{ThermoCycler.command_id}</requestId>
          <lockId i:nil="true"/>
        </{command}>
      </s:Body>
    </s:Envelope>"""
    # req = textwrap.dedent(req).replace("\n", "")
    # remove all white space before each line
    req = " ".join([line.lstrip() for line in req.split("\n")])
    print(req)

    res = requests.post(
      f"http://{self.ip}:{self.port}/",
      data=req,
      headers={
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": f"http://sila.coop/{command}"
      },
      timeout=self.timeout
    )
    print(res.text)
    return res

  def open_door(self):
    return self.send_command("OpenDoor")

  def close_door(self):
    return self.send_command("CloseDoor")

  def get_status1(self):
    return self.send_command("GetStatus")
