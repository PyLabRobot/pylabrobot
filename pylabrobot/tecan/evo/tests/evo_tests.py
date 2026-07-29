"""Unit tests for the TecanEVO composite setup path.

Covers two setup-path regressions: the backend `_on_setup` signatures must
accept the `backend_params` keyword the capability layer always passes, and
`TecanEVO.setup()` must reference `self.driver` (the attribute `Device` sets).
"""

import unittest
from unittest.mock import AsyncMock

from pylabrobot.resources import EVO150Deck
from pylabrobot.tecan.evo import TecanEVO
from pylabrobot.tecan.evo.driver import TecanEVODriver
from pylabrobot.tecan.evo.firmware.arm_base import EVOArm
from pylabrobot.tecan.evo.pip_backend import EVOPIPBackend
from pylabrobot.tecan.evo.roma_backend import EVORoMaBackend


async def _mock_send(module, command, params=None, **kwargs):
  """Minimal firmware report stubs sufficient to drive `_on_setup`."""
  if command == "RPX":
    return {"data": [5000]}
  if command == "RPY":
    return {"data": [1500, 90]}
  if command == "RPZ":
    return {"data": [2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000]}
  if command == "RNT":
    return {"data": [8]}
  return {"data": []}


class BackendOnSetupSignatureTests(unittest.IsolatedAsyncioTestCase):
  """`Capability._on_setup` always calls `backend._on_setup(backend_params=...)`.

  The stock EVO backends used to declare `_on_setup(self)`, which raised
  `TypeError: unexpected keyword argument 'backend_params'` on every setup.
  """

  def setUp(self):
    super().setUp()
    EVOArm._pos_cache.clear()
    self.driver = TecanEVODriver()
    self.driver.send_command = AsyncMock(side_effect=_mock_send)
    self.deck = EVO150Deck()

  async def test_pip_backend_on_setup_accepts_backend_params(self):
    backend = EVOPIPBackend(driver=self.driver, deck=self.deck, diti_count=8)
    await backend._on_setup(backend_params=None)  # must not raise TypeError

  async def test_roma_backend_on_setup_accepts_backend_params(self):
    backend = EVORoMaBackend(driver=self.driver, deck=self.deck)
    await backend._on_setup(backend_params=None)  # must not raise TypeError


class TecanEVOSetupTests(unittest.IsolatedAsyncioTestCase):
  """End-to-end `TecanEVO.setup()` — exercises both the `_driver` typo (F1) and
  the `backend_params` mismatch (F3), which together crash composite setup."""

  def setUp(self):
    super().setUp()
    EVOArm._pos_cache.clear()

  async def test_setup_drives_driver_and_pip_without_error(self):
    evo = TecanEVO(name="evo", deck=EVO150Deck(), has_roma=False, diti_count=8)
    # TecanEVO builds its driver internally; mock the shared instance both the
    # composite (driver.setup) and the pip backend (send_command) reach through.
    evo.driver.setup = AsyncMock()
    evo.driver.send_command = AsyncMock(side_effect=_mock_send)

    await evo.setup()

    evo.driver.setup.assert_awaited_once()
    self.assertTrue(evo._setup_finished)


class TecanEVOPurgeOrderingTests(unittest.IsolatedAsyncioTestCase):
  """The plunger purge must run AFTER RoMa init.

  For a non-rail-1 wash the LiHa traverses right (toward the RoMa) to purge, so
  the RoMa must be initialized and parked clear first. v1b1 originally purged
  inside pip._on_setup — before RoMa init — which is only collision-safe because
  it assumed the wash was at rail 1 (far left, away from the RoMa).
  """

  def setUp(self):
    super().setUp()
    EVOArm._pos_cache.clear()

  async def test_setup_purges_after_roma_init(self):
    evo = TecanEVO(name="evo", deck=EVO150Deck(), has_roma=True, diti_count=8)
    order: list = []
    evo.driver.setup = AsyncMock()
    evo.pip._on_setup = AsyncMock(side_effect=lambda *a, **k: order.append("pip_init"))
    evo._roma_needs_init = AsyncMock(return_value=False)  # skip the LiHa-home dance
    evo.arm._on_setup = AsyncMock(side_effect=lambda *a, **k: order.append("roma_init"))
    evo._pip_backend.purge = AsyncMock(side_effect=lambda *a, **k: order.append("purge"))

    await evo.setup()

    self.assertEqual(order, ["pip_init", "roma_init", "purge"])


if __name__ == "__main__":
  unittest.main()
