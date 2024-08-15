# pylint: disable=missing-class-docstring

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
    m = self.MockMachine(name="test",
                         size_x=10,
                         size_y=10,
                         size_z=10,
                         backend=self.MockBackend("mock_param"))
    self.assertEqual(m.serialize(), {
      "name": "test",
      "size_x": 10,
      "size_y": 10,
      "size_z": 10,
      "location": None,
      "rotation": {
        "type": "Rotation",
        "x": 0, "y": 0, "z": 0
      },
      "type": "MockMachine",
      "children": [],
      "category": None,
      "parent_name": None,
      "model": None,
      "backend": {"mock_param": "mock_param", "type": "MockBackend"}
    })

  def test_deserialize(self):
    m = self.MockMachine(name="test",
                         size_x=10,
                         size_y=10,
                         size_z=10,
                         backend=self.MockBackend("mock_param"))
    self.assertEqual(Machine.deserialize(m.serialize()), m)
