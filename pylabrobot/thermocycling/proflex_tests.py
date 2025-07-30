import sys
import unittest
from unittest.mock import patch

from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.thermocycling.proflex import ProflexBackend
from pylabrobot.thermocycling.standard import BlockStatus, LidStatus, Protocol, Stage, Step


class TestProflexBackend(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    await super().asyncSetUp()
    self.proflex = ProflexBackend(ip="1.2.3.4")
    self.proflex.io.write = unittest.mock.AsyncMock()
    self.proflex.io.read = unittest.mock.AsyncMock()

  async def test_run_protocol(self):
    self.proflex.io.read.side_effect = [
      "OK CMD False False\n",
      "OK CMD False False\n",
      "OK CMD False False\n",
      "OK CMD False False\n",
      "OK CMD False False\n",
      "NEXT CMD False False\n",
      "OK CMD 1 2\n",
    ]

    scpi_command = """
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
    protocol = Protocol(
      stages=[
        # initial hold
        Stage(
          steps=[
            Step(temperature=[37]*2, hold_seconds=float("inf")),
          ],
          repeats=1,
        ),
        # one pot stage
        Stage(
          steps=[
            Step(temperature=[37]*2, hold_seconds=300, rate=100),
            Step(temperature=[16]*2, hold_seconds=300, rate=100),
          ],
          repeats=30,
        ),
        # digest denature stage
        Stage(
          steps=[
            Step(temperature=[37]*2, hold_seconds=300, rate=100),
            Step(temperature=[60]*2, hold_seconds=300, rate=100),
          ],
          repeats=1,
        ),
        # final hold
        Stage(
          steps=[
            Step(temperature=[4]*2, hold_seconds=float("inf"), rate=100),
          ],
          repeats=1,
        ),
      ]
    )
    await self.proflex.run_protocol(
      protocol=protocol,
      block_id=1,
      sample_volume=25,
      run_name="runname",
      protocol_name="cloning_protocol",
      stage_name_prefixes=["InitHold", "OnePot", "digestDenature", "FinalHold"],
    )

    assert unittest.mock.call(scpi_command.strip()) in self.proflex.io.write.calls
