"""Unit tests for ``KX2ArmBackend._read_axis_config``.

Covers the per-axis Elmo register read and its validation branches:

* UF1 / UF2 -> motor conversion factor (zero on either is invalid).
* XM[1..2], VH[3], VL[3] -> bounded vs unlimited travel decision.
* CA[45] -> encoder socket index in 1..4.
* CA[40+ca45] -> encoder type code (1 / 2 / 24 are the only supported).

The test populates a per-(cmd, idx) dict that a fake driver reads from.
No CAN, no asyncio loop concerns beyond a one-shot ``asyncio.run``.
"""
import asyncio
import unittest
from typing import Any, Dict, Tuple

from pylabrobot.paa.kx2.arm_backend import KX2ArmBackend
from pylabrobot.paa.kx2.config import Axis, AxisConfig
from pylabrobot.paa.kx2.driver import CanError, JointMoveDirection


def _baseline_register_map() -> Dict[Tuple[str, int], Any]:
  """Plausible "all-good, bounded-travel" register snapshot for one axis.

  Values picked so:
    * UF1/UF2 give a non-zero conv factor (UF1=10000, UF2=1 -> 10000).
    * XM/VH/VL satisfy the bounded-travel branch (xm1==xm2==0).
    * CA[45]==1 (valid socket), CA[41]==24 (absolute encoder).
    * SP[2] != 100000 so max_vel takes the SP[2]/denom branch.
  """
  reg: Dict[Tuple[str, int], Any] = {}
  # _read_io_names: UI[5..10] (digital), UI[11..12] (analog), UI[13..16] (outputs)
  for idx in range(5, 17):
    reg[("UI", idx)] = 0  # all-unassigned -> "" entries
  reg[("UI", 24)] = 12345  # serial, value irrelevant
  # Conversion factor: UF1/UF2.
  reg[("UF", 1)] = 10000.0
  reg[("UF", 2)] = 1.0
  # Bounded travel: xm1==xm2==0 hits the "(xm1==0 and xm2==0)" branch.
  reg[("XM", 1)] = 0
  reg[("XM", 2)] = 0
  reg[("UF", 3)] = 180.0   # max_travel
  reg[("UF", 4)] = -180.0  # min_travel
  reg[("VH", 3)] = 1000
  reg[("VL", 3)] = -1000
  # Encoder socket + type: socket 1, type 24 (absolute).
  reg[("CA", 45)] = 1
  reg[("CA", 41)] = 24
  reg[("CA", 46)] = 1  # equals ca45 -> num3 = 1.0 (skips FF[3] read)
  # Velocity / accel.
  reg[("SP", 2)] = 50000
  reg[("VH", 2)] = 200000
  reg[("SD", 0)] = 100000
  reg[("FF", 3)] = 1.0  # only used if ca45 != ca46
  return reg


class _FakeDriver:
  """Minimal stand-in for KX2Driver; only resolves query_int / query_float
  against a (cmd, idx) -> value dict. Raises KeyError on unmapped reads so
  test misconfig surfaces loudly."""

  def __init__(self, reg: Dict[Tuple[str, int], Any]):
    self.reg = reg
    self.calls: list = []

  async def query_int(self, node_id: int, cmd: str, idx: int) -> int:
    self.calls.append(("int", node_id, cmd, idx))
    return int(self.reg[(cmd, idx)])

  async def query_float(self, node_id: int, cmd: str, idx: int) -> float:
    self.calls.append(("float", node_id, cmd, idx))
    return float(self.reg[(cmd, idx)])


def _build_backend(reg: Dict[Tuple[str, int], Any]) -> KX2ArmBackend:
  """Bypass __init__ — _read_axis_config only touches self.driver."""
  backend = KX2ArmBackend.__new__(KX2ArmBackend)
  backend.driver = _FakeDriver(reg)  # type: ignore[assignment]
  return backend


class ReadAxisConfigHappyPathTests(unittest.TestCase):
  def test_returns_axis_config_with_expected_fields(self):
    """Full register snapshot -> AxisConfig with the values derived from
    the snapshot."""
    reg = _baseline_register_map()
    backend = _build_backend(reg)
    cfg = asyncio.run(backend._read_axis_config(Axis.SHOULDER))

    self.assertIsInstance(cfg, AxisConfig)
    self.assertEqual(cfg.motor_conversion_factor, 10000.0)
    self.assertEqual(cfg.max_travel, 180.0)
    self.assertEqual(cfg.min_travel, -180.0)
    self.assertFalse(cfg.unlimited_travel)
    self.assertTrue(cfg.absolute_encoder)
    self.assertEqual(cfg.joint_move_direction, JointMoveDirection.Normal)
    # max_vel = SP[2] / (conv * num3) = 50000 / (10000 * 1.0) = 5.0
    self.assertAlmostEqual(cfg.max_vel, 5.0, places=9)
    # max_accel = SD[0] / 1.01 / (conv * num3) = 100000 / 1.01 / 10000
    self.assertAlmostEqual(cfg.max_accel, 100000 / 1.01 / 10000.0, places=9)
    # Default I/O dicts: all-empty channel labels (codes were 0).
    self.assertEqual(cfg.digital_inputs, {1: "", 2: "", 3: "", 4: "", 5: "", 6: ""})
    self.assertEqual(cfg.analog_inputs, {1: "", 2: ""})
    self.assertEqual(cfg.outputs, {1: "", 2: "", 3: "", 4: ""})

  def test_unlimited_travel_branch_sets_shortest_way_for_motion_axis(self):
    """xm1 > vl3 and xm2 < vh3 -> unlimited travel; for motion axes, the
    joint_move_direction flips to ShortestWay."""
    reg = _baseline_register_map()
    # xm range strictly inside vl/vh range.
    reg[("XM", 1)] = -500
    reg[("XM", 2)] = 500
    reg[("VH", 3)] = 1000
    reg[("VL", 3)] = -1000
    backend = _build_backend(reg)
    cfg = asyncio.run(backend._read_axis_config(Axis.SHOULDER))
    self.assertTrue(cfg.unlimited_travel)
    self.assertEqual(cfg.joint_move_direction, JointMoveDirection.ShortestWay)

  def test_unlimited_travel_keeps_normal_direction_on_non_motion_axis(self):
    """Same unlimited-travel path on a non-motion axis (e.g. RAIL) keeps
    the joint_move_direction at Normal."""
    reg = _baseline_register_map()
    reg[("XM", 1)] = -500
    reg[("XM", 2)] = 500
    backend = _build_backend(reg)
    cfg = asyncio.run(backend._read_axis_config(Axis.RAIL))
    self.assertTrue(cfg.unlimited_travel)
    self.assertEqual(cfg.joint_move_direction, JointMoveDirection.Normal)

  def test_sp2_is_100000_uses_vh2_path_for_max_vel(self):
    """SP[2] == 100000 sentinel -> max_vel comes from VH[2]/1.01/denom."""
    reg = _baseline_register_map()
    reg[("SP", 2)] = 100000
    reg[("VH", 2)] = 200000
    backend = _build_backend(reg)
    cfg = asyncio.run(backend._read_axis_config(Axis.SHOULDER))
    # 200000 / 1.01 / (10000 * 1.0)
    self.assertAlmostEqual(cfg.max_vel, 200000 / 1.01 / 10000.0, places=9)

  def test_ca45_neq_ca46_pulls_ff3_into_denom(self):
    """When the position encoder (CA[46]) differs from the commutation
    encoder (CA[45]), num3 is read from FF[3] instead of defaulting to 1."""
    reg = _baseline_register_map()
    reg[("CA", 46)] = 2  # different from CA[45]=1
    reg[("FF", 3)] = 4.0
    backend = _build_backend(reg)
    cfg = asyncio.run(backend._read_axis_config(Axis.SHOULDER))
    # denom = 10000 * 4.0; max_vel = 50000 / 40000 = 1.25
    self.assertAlmostEqual(cfg.max_vel, 1.25, places=9)


class ReadAxisConfigValidationTests(unittest.TestCase):
  """One test per validation branch: a future contributor flipping a guard
  by mistake should fail at least one of these."""

  def test_uf1_zero_raises(self):
    reg = _baseline_register_map()
    reg[("UF", 1)] = 0.0
    backend = _build_backend(reg)
    with self.assertRaises(CanError) as ctx:
      asyncio.run(backend._read_axis_config(Axis.SHOULDER))
    self.assertIn("Invalid motor conversion factor", str(ctx.exception))
    self.assertIn(f"axis {int(Axis.SHOULDER)}", str(ctx.exception))
    self.assertIn("UF[1]=0.0", str(ctx.exception))

  def test_uf2_zero_raises(self):
    reg = _baseline_register_map()
    reg[("UF", 2)] = 0.0
    backend = _build_backend(reg)
    with self.assertRaises(CanError) as ctx:
      asyncio.run(backend._read_axis_config(Axis.SHOULDER))
    self.assertIn("Invalid motor conversion factor", str(ctx.exception))
    self.assertIn("UF[2]=0.0", str(ctx.exception))

  def test_invalid_travel_limits_raises(self):
    """Neither bounded-travel nor unlimited-travel branch matches: e.g.
    xm1 > vl3 but xm2 >= vh3 -> hits the else."""
    reg = _baseline_register_map()
    reg[("XM", 1)] = -500    # > VL[3] = -1000
    reg[("XM", 2)] = 2000    # >= VH[3] = 1000  -> not unlimited, not bounded
    backend = _build_backend(reg)
    with self.assertRaises(CanError) as ctx:
      asyncio.run(backend._read_axis_config(Axis.SHOULDER))
    msg = str(ctx.exception)
    self.assertIn("Invalid travel limits or modulo settings", msg)
    self.assertIn("VH[3]=1000", msg)
    self.assertIn("VL[3]=-1000", msg)
    self.assertIn("XM[1]=-500", msg)
    self.assertIn("XM[2]=2000", msg)

  def test_ca45_zero_raises(self):
    """CA[45] == 0 fails the (0 < ca45 <= 4) guard."""
    reg = _baseline_register_map()
    reg[("CA", 45)] = 0
    backend = _build_backend(reg)
    with self.assertRaises(CanError) as ctx:
      asyncio.run(backend._read_axis_config(Axis.SHOULDER))
    self.assertIn("Invalid encoder socket", str(ctx.exception))
    self.assertIn("CA[45]=0", str(ctx.exception))

  def test_ca45_too_large_raises(self):
    """CA[45] > 4 also fails the guard (only sockets 1..4 wired)."""
    reg = _baseline_register_map()
    reg[("CA", 45)] = 5
    backend = _build_backend(reg)
    with self.assertRaises(CanError) as ctx:
      asyncio.run(backend._read_axis_config(Axis.SHOULDER))
    self.assertIn("Invalid encoder socket", str(ctx.exception))
    self.assertIn("CA[45]=5", str(ctx.exception))

  def test_unsupported_encoder_type_raises(self):
    """enc_type not in {1, 2, 24} -> raises with the offending CA register."""
    reg = _baseline_register_map()
    reg[("CA", 45)] = 1
    reg[("CA", 41)] = 7  # neither incremental (1/2) nor absolute (24)
    backend = _build_backend(reg)
    with self.assertRaises(CanError) as ctx:
      asyncio.run(backend._read_axis_config(Axis.SHOULDER))
    msg = str(ctx.exception)
    self.assertIn("Unsupported encoder type", msg)
    self.assertIn("CA[41]=7", msg)

  def test_incremental_encoder_type_1_sets_absolute_false(self):
    reg = _baseline_register_map()
    reg[("CA", 45)] = 1
    reg[("CA", 41)] = 1
    backend = _build_backend(reg)
    cfg = asyncio.run(backend._read_axis_config(Axis.SHOULDER))
    self.assertFalse(cfg.absolute_encoder)

  def test_incremental_encoder_type_2_sets_absolute_false(self):
    reg = _baseline_register_map()
    reg[("CA", 45)] = 1
    reg[("CA", 41)] = 2
    backend = _build_backend(reg)
    cfg = asyncio.run(backend._read_axis_config(Axis.SHOULDER))
    self.assertFalse(cfg.absolute_encoder)


if __name__ == "__main__":
  unittest.main()
