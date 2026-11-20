"""Biomek's coordinated-move model, checked against coordinated moves Biomek made.

`LOGGED_MOVES` holds logged `AM` commands, one of each kind, each with the positions the Master
Controller planned it from, and the Set Move Parameters and Move All timeout it sent.
"""

import math
import unittest

from pylabrobot.beckman_coulter.biomek_i7.driver import motion
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_configuration

TABLES = generic_configuration().motor_tables

LOGGED_MOVES = [
  {
    "cpositions": {
      "GR": [-175443, -175443],
      "GY": [63763, 63763],
      "GZ": [-159, -159],
      "X": [35841, 35841],
      "Y": [37266, 37266],
      "Z": [0, 0],
    },
    "cutoff_velocity": 0,
    "kind": "multichannel full axes=0",
    "module": "multichannel",
    "motions": {},
    "speed": 1000,
    "targets": {"GR": -175443, "GY": 63763, "GZ": -159, "X": 35841, "Y": 37266, "Z": 0},
    "timeout": 5000,
  },
  {
    "cpositions": {"GR": [-175774, -175774], "GZ": [-161, -161]},
    "cutoff_velocity": 0,
    "kind": "span8 full axes=0",
    "module": "span8",
    "motions": {},
    "speed": 1000,
    "targets": {"GR": -175774, "GZ": -161},
    "timeout": 5000,
  },
  {
    "cpositions": {"GG": [0, 0]},
    "cutoff_velocity": 0,
    "kind": "multichannel slow axes=0",
    "module": "multichannel",
    "motions": {},
    "speed": 200,
    "targets": {"GG": 0},
    "timeout": 5000,
  },
  {
    "cpositions": {
      "D": [91463, 91463],
      "GG": [0, 0],
      "GR": [-175443, -175443],
      "GY": [63730, 63730],
      "GZ": [-160, -160],
      "X": [4833, 4833],
      "Y": [10952, 10952],
      "Z": [0, 0],
    },
    "cutoff_velocity": 0,
    "kind": "multichannel full jerk-corrected s-curve trapezoid axes=3",
    "module": "multichannel",
    "motions": {
      "D": {"acceleration": 1228495, "position": 43176, "velocity": 196608},
      "X": {"acceleration": 51, "jerk": 1280000, "position": 4831, "velocity": 8},
      "Y": {"acceleration": 25, "jerk": 640000, "position": 10953, "velocity": 4},
    },
    "speed": 1000,
    "targets": {
      "D": 43176,
      "GG": 0,
      "GR": -175443,
      "GY": 63730,
      "GZ": -160,
      "X": 4831,
      "Y": 10953,
      "Z": 0,
    },
    "timeout": 5411,
  },
  {
    "cpositions": {"D3": [1320, 1320], "Z3": [-78827, -78827]},
    "cutoff_velocity": 3600,
    "kind": "span8 slow hysteresis jerk-corrected pump axes=2",
    "module": "span8",
    "motions": {
      "D3": {"acceleration": 10000, "position": 720, "velocity": 604},
      "Z3": {"acceleration": 4000, "jerk": 21792949, "position": -78587, "velocity": 488},
    },
    "speed": 26,
    "targets": {"D3": 720, "Z3": -78587},
    "timeout": 6007,
  },
  {
    "cpositions": {"Z": [-255, -255]},
    "cutoff_velocity": 0,
    "kind": "multichannel full jerk-corrected s-curve axes=1",
    "module": "multichannel",
    "motions": {
      "Z": {"acceleration": 128000, "jerk": 698690, "position": -31982, "velocity": 24320}
    },
    "speed": 1000,
    "targets": {"Z": -31982},
    "timeout": 6678,
  },
  {
    "cpositions": {
      "Y1": [203370, 203370],
      "Y2": [203370, 203370],
      "Y3": [203370, 203370],
      "Y4": [203370, 203370],
      "Y5": [203370, 203370],
      "Y6": [203370, 203370],
      "Y7": [203370, 203370],
      "Y8": [203370, 203370],
    },
    "cutoff_velocity": 0,
    "kind": "span8 full axes=0 span-y",
    "module": "span8",
    "motions": {},
    "speed": 1000,
    "targets": {
      "Y1": 203370,
      "Y2": 203370,
      "Y3": 203370,
      "Y4": 203370,
      "Y5": 203370,
      "Y6": 203370,
      "Y7": 203370,
      "Y8": 203370,
    },
    "timeout": 5000,
  },
  {
    "cpositions": {
      "D": [43161, 43161],
      "GG": [34983, 34983],
      "GR": [-175443, -175443],
      "GY": [63747, 63747],
      "GZ": [-796, -796],
      "X": [35692, 35692],
      "Y": [37274, 37274],
      "Z": [0, 0],
    },
    "cutoff_velocity": 0,
    "kind": "multichannel full jerk-corrected s-curve axes=4",
    "module": "multichannel",
    "motions": {
      "GG": {"acceleration": 100, "jerk": 546, "position": 34981, "velocity": 19},
      "GR": {"acceleration": 50, "jerk": 273, "position": -175442, "velocity": 9},
      "X": {"acceleration": 50, "jerk": 273, "position": 35691, "velocity": 9},
      "Z": {"acceleration": 128000, "jerk": 698880, "position": -2561, "velocity": 24320},
    },
    "speed": 1000,
    "targets": {
      "D": 43161,
      "GG": 34981,
      "GR": -175442,
      "GY": 63747,
      "GZ": -796,
      "X": 35691,
      "Y": 37274,
      "Z": -2561,
    },
    "timeout": 5490,
  },
  {
    "cpositions": {
      "GR": [-175443, -175443],
      "GY": [63744, 63744],
      "GZ": [-796, -796],
      "X": [35767, 35767],
      "Y": [37244, 37244],
      "Z": [0, 0],
    },
    "cutoff_velocity": 0,
    "kind": "multichannel full axes=0",
    "module": "multichannel",
    "motions": {},
    "speed": 1000,
    "targets": {"GR": -175443, "GY": 63744, "GZ": -796, "X": 35767, "Y": 37244, "Z": 0},
    "timeout": 5000,
  },
]


def load_moves():
  return LOGGED_MOVES


class LoggedMoveTests(unittest.TestCase):
  """Every logged move plans to exactly what the Master Controller sent."""

  def test_every_logged_move(self) -> None:
    moves = load_moves()
    for move in moves:
      axes = TABLES.axes(move["module"])
      cpositions = {a.name: (0, 0) for a in axes}
      cpositions.update({n: (c[0], c[1]) for n, c in move["cpositions"].items()})
      motions, timeout, _ = motion.plan_move(
        axes, cpositions, move["targets"], move["speed"], move["cutoff_velocity"]
      )
      planned = {
        m.axis: {
          "position": m.position,
          "velocity": m.velocity,
          "acceleration": m.acceleration,
          **(
            {"jerk": m.jerk if m.jerk is not None else 0}
            if "jerk" in move["motions"].get(m.axis, {})
            else {}
          ),
        }  # fmt: skip
        for m in motions
      }
      with self.subTest(kind=move["kind"], targets=move["targets"]):
        self.assertEqual(planned, move["motions"])
        if motions:
          self.assertEqual(timeout, move["timeout"])

  def test_fixture_covers_every_kind_of_move(self) -> None:
    kinds = " ".join(m["kind"] for m in load_moves())
    # Hysteresis is measured per instrument and the generic configuration has none: it is
    # checked on its own (`HysteresisTests`), and the logged moves with it in the local replay.
    for kind in ("multichannel", "span8", "slow", "trapezoid", "pump", "jerk-corrected",
                 "span-y", "axes=4", "axes=0"):  # fmt: skip
      self.assertIn(kind, kinds)


class MoveTimeTests(unittest.TestCase):
  def test_short_s_curve_never_reaches_max_acceleration(self) -> None:
    # 280 counts of the Multichannel Z at its limits: pure jerk, 4 phases of (d / 2j)^(1/3).
    t = motion.s_curve_move_time(280, 24320, 128000, 698880)
    self.assertAlmostEqual(t, 4 * (280 / 2 / 698880) ** (1 / 3), places=12)
    self.assertEqual(math.ceil(1000 * t), 235)

  def test_trapezoid(self) -> None:
    self.assertEqual(motion.trapezoid_move_time(200, 10, 1), 30)  # reaches v: v/a + d/v
    self.assertEqual(motion.trapezoid_move_time(4, 10, 1), 4)  # triangle: 2 sqrt(d/a), exact

  def test_zero_distance_takes_no_time(self) -> None:
    self.assertEqual(motion.s_curve_move_time(0, 1, 1, 1), 0)
    self.assertEqual(motion.pump_move_time(0, 1, 1, 1, 1), 0)

  def test_invalid_input_raises(self) -> None:
    with self.assertRaises(ValueError):
      motion.s_curve_move_time(10, 0, 1, 1)


class HysteresisTests(unittest.TestCase):
  """The Span-8 Z channels' hysteresis is negative: downward moves travel |h| further, and the
  adjusted position stays |h| above where the axis is."""

  H = -862

  def test_down_move_adds_hysteresis(self) -> None:
    cpos = (1000, 1000)
    self.assertEqual(motion.adjust_delta(cpos, -500, self.H), -500 - 862)

  def test_up_move_after_down_move_does_not(self) -> None:
    cpos = motion.update_cpos((1000, 1000), -362, self.H)
    self.assertEqual(cpos, (-362, 500))
    self.assertEqual(motion.adjust_delta(cpos, 1000 - cpos[1], self.H), 1000 - 500 + 862)

  def test_no_hysteresis_leaves_moves_alone(self) -> None:
    self.assertEqual(motion.adjust_delta((5, 5), 10, 0), 10)
    self.assertEqual(motion.update_cpos((5, 5), 15, 0), (15, 15))


class PlanTests(unittest.TestCase):
  AXES = TABLES.axes("multichannel")

  def cpositions(self, **at: int):
    out = {a.name: (0, 0) for a in self.AXES}
    out.update({n: (p, p) for n, p in at.items()})
    return out

  def test_axes_already_there_are_left_out(self) -> None:
    motions, timeout, after = motion.plan_move(self.AXES, self.cpositions(X=100), {"X": 100})
    self.assertEqual(motions, [])
    self.assertEqual(timeout, motion.MOVE_ALL_MARGIN_MS)
    self.assertEqual(after["X"], (100, 100))

  def test_leader_moves_at_its_limits_and_followers_arrive_with_it(self) -> None:
    motions, _, after = motion.plan_move(self.AXES, self.cpositions(), {"X": 100000, "Y": 1000})
    x, y = motions
    lim = self.AXES[0].limits
    self.assertEqual(
      (x.axis, x.velocity, x.acceleration), ("X", lim.max_velocity, lim.max_acceleration)
    )
    self.assertEqual(y.velocity, round(lim.max_velocity * 1000 / 100000))
    self.assertEqual(after["Y"], (1000, 1000))

  def test_speed_scales_the_leader(self) -> None:
    (x,), _, _ = motion.plan_move(self.AXES, self.cpositions(), {"X": 100000}, speed=500)
    self.assertEqual(x.velocity, round(self.AXES[0].limits.max_velocity * 0.5))

  def test_rejects_bad_speed_and_unknown_axes(self) -> None:
    with self.assertRaises(ValueError):
      motion.plan_move(self.AXES, self.cpositions(), {"X": 1}, speed=0)
    with self.assertRaises(ValueError):
      motion.plan_move(self.AXES, self.cpositions(), {"Q": 1})

  def test_unknown_position_must_be_read_first(self) -> None:
    cpositions = self.cpositions()
    cpositions["X"] = (None, 0)
    with self.assertRaises(ValueError):
      motion.plan_move(self.AXES, cpositions, {"X": 1000})


if __name__ == "__main__":
  unittest.main()


class SpawnOrderTests(unittest.TestCase):
  def test_pairs_from_the_end_each_in_order(self) -> None:
    # As logged: a Multichannel Z+D move sends D first; a Span-8 move of X and Z1-Z8 sends X, then
    # Z2 Z1, Z4 Z3, ...
    self.assertEqual(motion.spawn_order(["Z", "D"]), ["D", "Z"])
    z = [f"Z{n}" for n in range(1, 9)]
    self.assertEqual(
      motion.spawn_order(["X"] + z), ["X", "Z2", "Z1", "Z4", "Z3", "Z6", "Z5", "Z8", "Z7"]
    )
    self.assertEqual(motion.spawn_order(["X"]), ["X"])
    self.assertEqual(motion.spawn_order([]), [])
