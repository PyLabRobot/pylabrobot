import unittest
import unittest.mock

from pylabrobot.machines.machine import Machine, MachineBackend


class TestMachine(unittest.TestCase):
  class MockBackend(MachineBackend):
    def __init__(self, mock_param):
      self.mock_param = mock_param

    async def setup(self):
      pass

    async def stop(self):
      pass

    def serialize(self):
      return {**super().serialize(), "mock_param": self.mock_param}

  class MockMachine(Machine):
    pass

  def test_serialize(self):
    m = self.MockMachine(backend=self.MockBackend("mock_param"))
    self.assertEqual(
      m.serialize(),
      {
        "backend": {
          "mock_param": "mock_param",
          "type": "MockBackend",
        },
      },
    )

  def test_deserialize(self):
    m = self.MockMachine(backend=self.MockBackend("mock_param"))
    Machine.deserialize(m.serialize())  # shouldn't raise
