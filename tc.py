import os
import platform
import textwrap
import xml
import xml.etree.ElementTree as ET

import requests


class ThermoCyclerStep:
  def __init__(self, temperature: float, duration: float, lid_temperature: float, slope: float):
    self.temperature = temperature
    self.duration = duration
    self.lid_temperature = lid_temperature
    self.slope = slope

from typing import List
class ThermoCycler:
  def __init__(self, ip: str) -> None:
    self.ip = ip
    self.port = 8080
    self.timeout = 5

  async def run(self, steps: List[ThermoCyclerStep]):
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
