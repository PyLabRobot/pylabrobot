"""Unit tests for :class:`DarwinController` behaviour that does not touch the wire.

Wire-level behaviour (commutation, homing, moves, W-axis parameter apply)
is covered by :mod:`.darwin_golden_frame_tests` and :mod:`.timing_tests`
instead; this module is for state that :meth:`DarwinController.set_head_type`
and :meth:`DarwinController.get_head_type` manage purely in Python.
"""

from __future__ import annotations

import unittest

from .controller import DarwinController
from .darwin_golden_frame_tests import FakeGeminiTransport


class HeadTypeTrackingTests(unittest.TestCase):
  def test_get_head_type_defaults_to_unknown(self):
    controller = DarwinController(FakeGeminiTransport())
    self.assertEqual(controller.get_head_type(), "unknown")

  def test_get_head_type_reflects_the_most_recent_set_head_type(self):
    controller = DarwinController(FakeGeminiTransport())
    controller.set_head_type("96_d_200")
    self.assertEqual(controller.get_head_type(), "96_d_200")

  def test_ul_to_mm_uses_the_currently_set_head_type(self):
    # 96_d_70 and 96_f_50 have distinct W-axis calibration configs (see
    # HEAD_CONFIGS in waxis_config.py), so the same volume converts to a
    # different mm figure once the head type changes.
    controller = DarwinController(FakeGeminiTransport())
    controller.set_head_type("96_d_70")
    at_96_d_70 = controller.ul_to_mm(50.0)
    controller.set_head_type("96_f_50")
    at_96_f_50 = controller.ul_to_mm(50.0)
    self.assertNotEqual(at_96_d_70, at_96_f_50)


if __name__ == "__main__":
  unittest.main()
