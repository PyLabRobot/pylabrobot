"""Smoke test: the setup + load/unload demo runs end-to-end against the mock board."""

import unittest

from pylabrobot.celigo.controller import CeligoController
from pylabrobot.celigo.demo import MockBoard, load_plate, setup, unload_plate


class TestDemo(unittest.TestCase):
  def test_runs_and_issues_expected_commands(self):
    board = MockBoard()
    ctrl = CeligoController(board)
    setup(ctrl)
    load_plate(ctrl)
    unload_plate(ctrl)

    motor_cmds = [ez for name, ez in board.log if ez is not None]
    # the captured load/unload signatures appear in the issued commands
    self.assertIn("/1m65R", motor_cmds)  # X move current
    self.assertIn("/2m55R", motor_cmds)  # Y move current
    self.assertTrue(any("D25000R" in c for c in motor_cmds))  # stage out
    self.assertTrue(any("A-136R" in c for c in motor_cmds))  # negative absolute move
    self.assertTrue(any(c.startswith("/3") and "A10337R" in c for c in motor_cmds))  # Z up

  def test_encoder_readback(self):
    board = MockBoard()
    ctrl = CeligoController(board)
    self.assertEqual(ctrl.get_encoder_position(2), 4491)


if __name__ == "__main__":
  unittest.main()
