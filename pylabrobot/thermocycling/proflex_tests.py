import textwrap
import unittest
import unittest.mock

from pylabrobot.thermocycling.proflex import ProflexBackend
from pylabrobot.thermocycling.standard import Protocol, Stage, Step


class TestProflexBackend(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    await super().asyncSetUp()
    self.proflex = ProflexBackend(ip="1.2.3.4")
    self.proflex.io.write = unittest.mock.AsyncMock()  # type: ignore
    self.proflex.io.read = unittest.mock.AsyncMock()  # type: ignore

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
    -notes= 
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

    self.proflex.io.read.side_effect = [  # type: ignore
      'OK RUNS:EXISts? -type=folders "runname" False\n',
      'OK RUNS:NEW "runname"\n',
      "OK " + pretty_xml_scpi,
      "OK " + tmp_scpi,
      "OK " + pretty_xml_scpi,
      "NEXT TBC1:RunProtocol -SampleVolume=25 -RunMode=Fast -CoverTemperature=105 -CoverEnabled=On -User=Guest elute 'runname'\n",
      "OK CMD 1\n",
    ]

    await self.proflex.run_protocol(
      protocol=protocol,
      block_id=1,
      block_max_volume=25,
      run_name="runname",
      protocol_name="cloning_protocol",
      stage_name_prefixes=["InitHold", "OnePot", "digestDenature", "FinalHold"],
    )

    self.proflex.io.write.assert_has_calls(  # type: ignore
      [
        unittest.mock.call("RUNS:EXISTS? -type=folders runname\r\n", timeout=1),
        unittest.mock.call("RUNS:NEW runname\r\n", timeout=10),
        unittest.mock.call(pretty_xml_scpi, timeout=1),
        unittest.mock.call(tmp_scpi, timeout=1),
        unittest.mock.call(scpi_command, timeout=5),
        unittest.mock.call(
          "TBC2:RunProtocol -User=Admin -CoverTemperature=105 -CoverEnabled=On cloning_protocol runname\r\n",
          timeout=2,
        ),
        unittest.mock.call("TBC2:ESTimatedTime?\r\n", timeout=1),
      ],
      any_order=False,
    )
