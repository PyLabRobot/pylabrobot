import textwrap
from typing import Any, Dict, List, cast

import anyio
import pytest

from pylabrobot.io import Socket
from pylabrobot.legacy.thermocycling.standard import Protocol, Stage, Step
from pylabrobot.legacy.thermocycling.thermo_fisher.proflex import ProflexBackend
from pylabrobot.testing.concurrency import AnyioTestBase


class ProtocolSocket:
  """Typed software Socket double for the literal protocol transcript."""

  def __init__(self) -> None:
    self.writes: List[bytes] = []
    self.reads: List[bytes] = []
    self.eof_reads: List[bytes] = []
    self.options: Dict[str, Any] = {}

  async def write(self, data: bytes, timeout: float = 30) -> None:
    del timeout
    self.writes.append(data)
    await anyio.lowlevel.checkpoint()

  async def read(self, size: int = 1024, timeout: float = 30) -> bytes:
    del size, timeout
    await anyio.lowlevel.checkpoint()
    return self.reads.pop(0)

  async def read_until_eof(self, timeout: float = 30) -> bytes:
    del timeout
    await anyio.lowlevel.checkpoint()
    return self.eof_reads.pop(0)


class TestProflexBackend(AnyioTestBase):
  async def _enter_lifespan(self, stack, **kwargs):
    del stack, kwargs
    self.proflex = ProflexBackend(ip="1.2.3.4")
    self.proflex.num_temp_zones = 2
    self.io = ProtocolSocket()
    self.proflex.io = cast(Socket, self.io)

  async def test_run_protocol(self):
    scpi_command = (
      textwrap.dedent(
        """
    TBC2:Protocol -Volume=25 -RunMode=Fast cloning_protocol <multiline.outer>
        STAGe -repeat=1 1 InitHold_1 <multiline.stage>
                STEP 1 <multiline.step>
                        RAMP -rate=100 37 37
                        HOLD
                </multiline.step>
        </multiline.stage>
        STAGe -repeat=30 2 OnePot_2 <multiline.stage>
                STEP 1 <multiline.step>
                        RAMP -rate=100 37 37
                        HOLD 300
                </multiline.step>
                STEP 2 <multiline.step>
                        RAMP -rate=100 16 16
                        HOLD 300
                </multiline.step>
        </multiline.stage>
        STAGe -repeat=1 3 digestDenature_3 <multiline.stage>
                STEP 1 <multiline.step>
                        RAMP -rate=100 37 37
                        HOLD 300
                </multiline.step>
                STEP 2 <multiline.step>
                        RAMP -rate=100 60 60
                        HOLD 300
                </multiline.step>
        </multiline.stage>
        STAGe -repeat=1 4 FinalHold_4 <multiline.stage>
                STEP 1 <multiline.step>
                        CoverRAMP 30
                        RAMP -rate=100 4 4
                        HOLD
                </multiline.step>
        </multiline.stage>
</multiline.outer>
    """
      ).strip()
      + "\r\n"
    )

    pretty_xml_scpi = (
      textwrap.dedent(
        """
    FILe:WRITe -encoding=plain runs:runname/cloning_protocol.method <multiline.write>
            <?xml version="1.0" encoding="UTF-8" standalone="yes"?>

    <TCProtocol>
      <FileVersion>1.0.1</FileVersion>
      <ProtocolName>cloning_protocol</ProtocolName>
      <UserName>LifeTechnologies</UserName>
      <BlockID>2</BlockID>
      <SampleVolume>25</SampleVolume>
      <RunMode>Fast</RunMode>
      <CoverTemperature>105</CoverTemperature>
      <CoverSetting>On</CoverSetting>
      <TCStage>
        <StageFlag>CYCLING</StageFlag>
        <NumOfRepetitions>1</NumOfRepetitions>
        <TCStep>
          <RampRate>6.0</RampRate>
          <RampRateUnit>DEGREES_PER_SECOND</RampRateUnit>
          <Temperature>37</Temperature>
          <Temperature>37</Temperature>
          <HoldTime>-1</HoldTime>
          <ExtTemperature>0</ExtTemperature>
          <ExtHoldTime>0</ExtHoldTime>
          <ExtStartingCycle>1</ExtStartingCycle>
        </TCStep>
      </TCStage>
      <TCStage>
        <StageFlag>CYCLING</StageFlag>
        <NumOfRepetitions>30</NumOfRepetitions>
        <TCStep>
          <RampRate>6.0</RampRate>
          <RampRateUnit>DEGREES_PER_SECOND</RampRateUnit>
          <Temperature>37</Temperature>
          <Temperature>37</Temperature>
          <HoldTime>300</HoldTime>
          <ExtTemperature>0</ExtTemperature>
          <ExtHoldTime>0</ExtHoldTime>
          <ExtStartingCycle>1</ExtStartingCycle>
        </TCStep>
        <TCStep>
          <RampRate>6.0</RampRate>
          <RampRateUnit>DEGREES_PER_SECOND</RampRateUnit>
          <Temperature>16</Temperature>
          <Temperature>16</Temperature>
          <HoldTime>300</HoldTime>
          <ExtTemperature>0</ExtTemperature>
          <ExtHoldTime>0</ExtHoldTime>
          <ExtStartingCycle>1</ExtStartingCycle>
        </TCStep>
      </TCStage>
      <TCStage>
        <StageFlag>CYCLING</StageFlag>
        <NumOfRepetitions>1</NumOfRepetitions>
        <TCStep>
          <RampRate>6.0</RampRate>
          <RampRateUnit>DEGREES_PER_SECOND</RampRateUnit>
          <Temperature>37</Temperature>
          <Temperature>37</Temperature>
          <HoldTime>300</HoldTime>
          <ExtTemperature>0</ExtTemperature>
          <ExtHoldTime>0</ExtHoldTime>
          <ExtStartingCycle>1</ExtStartingCycle>
        </TCStep>
        <TCStep>
          <RampRate>6.0</RampRate>
          <RampRateUnit>DEGREES_PER_SECOND</RampRateUnit>
          <Temperature>60</Temperature>
          <Temperature>60</Temperature>
          <HoldTime>300</HoldTime>
          <ExtTemperature>0</ExtTemperature>
          <ExtHoldTime>0</ExtHoldTime>
          <ExtStartingCycle>1</ExtStartingCycle>
        </TCStep>
      </TCStage>
      <TCStage>
        <StageFlag>CYCLING</StageFlag>
        <NumOfRepetitions>1</NumOfRepetitions>
        <TCStep>
          <RampRate>6.0</RampRate>
          <RampRateUnit>DEGREES_PER_SECOND</RampRateUnit>
          <Temperature>4</Temperature>
          <Temperature>4</Temperature>
          <HoldTime>-1</HoldTime>
          <ExtTemperature>0</ExtTemperature>
          <ExtHoldTime>0</ExtHoldTime>
          <ExtStartingCycle>1</ExtStartingCycle>
        </TCStep>
      </TCStage>
    </TCProtocol>

    </multiline.write>
    """
      ).strip()
      + "\r\n"
    )

    tmp_scpi = (
      textwrap.dedent(
        """
    FILe:WRITe -encoding=plain runs:runname/runname.tmp <multiline.write>
            -remoterun= true
    -hub= testhub
    -user= Guest
    -method= cloning_protocol
    -volume= 25
    -cover= 105
    -mode= Fast
    -coverEnabled= On
    -notes=\x20
    </multiline.write>
    """
      ).strip()
      + "\r\n"
    )

    protocol = Protocol(
      stages=[
        # initial hold
        Stage(
          steps=[
            Step(temperature=[37] * 2, hold_seconds=float("inf")),
          ],
          repeats=1,
        ),
        # one pot stage
        Stage(
          steps=[
            Step(temperature=[37] * 2, hold_seconds=300, rate=100),
            Step(temperature=[16] * 2, hold_seconds=300, rate=100),
          ],
          repeats=30,
        ),
        # digest denature stage
        Stage(
          steps=[
            Step(temperature=[37] * 2, hold_seconds=300, rate=100),
            Step(temperature=[60] * 2, hold_seconds=300, rate=100),
          ],
          repeats=1,
        ),
        # final hold
        Stage(
          steps=[
            Step(temperature=[4] * 2, hold_seconds=float("inf"), rate=100),
          ],
          repeats=1,
        ),
      ]
    )

    self.io.reads = [
      b'OK RUNS:EXISts? -type=folders "runname" False\n',
      b'OK RUNS:NEW "runname"\n',
      b"OK CMD 1\n",
    ]
    self.io.eof_reads = [
      ("OK " + pretty_xml_scpi).encode("ascii"),
      ("OK " + tmp_scpi).encode("ascii"),
      ("OK " + scpi_command).encode("ascii"),
      b"NEXT TBC1:RunProtocol -SampleVolume=25 -RunMode=Fast -CoverTemperature=105 -CoverEnabled=On -User=Guest elute 'runname'\n",
    ]

    await self.proflex.run_protocol(
      protocol=protocol,
      block_id=1,
      block_max_volume=25,
      run_name="runname",
      protocol_name="cloning_protocol",
      stage_name_prefixes=["InitHold", "OnePot", "digestDenature", "FinalHold"],
    )

    assert self.io.writes == [
      b"RUNS:EXISTS? -type=folders runname\r\n",
      b"RUNS:NEW runname\r\n",
      pretty_xml_scpi.encode(),
      tmp_scpi.encode(),
      scpi_command.encode(),
      b"TBC2:RunProtocol -User=Admin -CoverTemperature=105 -CoverEnabled=On cloning_protocol runname\r\n",
      b"TBC2:ESTimatedTime?\r\n",
    ]

  def test_ssl_init(self):
    backend = ProflexBackend(ip="1.2.3.4", use_ssl=True, serial_number="SN123")
    assert backend.use_ssl
    assert backend.device_shared_secret == b"53rv1c3SN123"
    assert backend.port == 7443
    assert backend.io._ssl_context is not None

  def test_ssl_init_missing_serial(self):
    with pytest.raises(ValueError, match="Serial number is required"):
      ProflexBackend(ip="1.2.3.4", use_ssl=True)
