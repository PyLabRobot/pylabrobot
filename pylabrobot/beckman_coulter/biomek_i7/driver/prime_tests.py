"""Priming the Span-8 on the simulator."""

import asyncio
from typing import Dict, List, Tuple

from pylabrobot.beckman_coulter.biomek_i7.device import BiomekI7
from pylabrobot.beckman_coulter.biomek_i7.driver.pod_move_tests import PodMoveTest
from pylabrobot.beckman_coulter.biomek_i7.resource_model.labware import biomek_tip
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_deck

PUMPS = [f"D{n}" for n in range(1, 9)]


class PrimeTests(PodMoveTest):
  async def setup_pipettes(self):  # type: ignore[no-untyped-def]
    driver = await self.homed()
    BiomekI7(driver=driver, deck=generic_deck())
    pod = self.pod(driver, "right")
    assert pod.pipettes is not None
    moves: List[Tuple[Dict[str, int], int]] = []
    original = pod.move_axes

    async def move_axes(targets, speed=1000, **kwargs):  # type: ignore[no-untyped-def]
      moves.append((dict(targets), speed))
      return await original(targets, speed, **kwargs)

    pod.move_axes = move_axes  # type: ignore[method-assign, assignment]
    return pod, pod.pipettes, moves

  async def test_a_prime_strokes_every_pump_in_the_wash_station(self) -> None:
    pod, pipettes, moves = await self.setup_pipettes()
    self.assertEqual(await pipettes.prime(3, minimum_stroke_seconds=0), 3)
    # The eight probes go down into the wash station together, before the first stroke.
    lowered = next(i for i, (t, s) in enumerate(moves) if s == 250)
    self.assertEqual(set(moves[lowered][0]), {f"Z{n}" for n in range(1, 9)})
    self.assertLess(lowered, next(i for i, (t, _) in enumerate(moves) if set(t) == set(PUMPS)))
    strokes = [(t, s) for t, s in moves if set(t) == set(PUMPS)]
    self.assertEqual(strokes[:2], [({a: 24000 for a in PUMPS}, 801), ({a: 0 for a in PUMPS}, 801)])
    self.assertEqual(len(strokes), 3 * 2 + 1)
    self.assertEqual(strokes[-1], ({a: 480 for a in PUMPS}, 10))  # the 20 uL system air gap
    self.assertFalse(pipettes.priming)
    self.assertEqual(pipettes.valves["D1"], "output")

  async def test_start_and_stop(self) -> None:
    pod, pipettes, moves = await self.setup_pipettes()
    await pipettes.start_prime()
    self.assertTrue(pipettes.priming)
    with self.assertRaises(RuntimeError):
      await pipettes.start_prime()
    await asyncio.sleep(0.05)
    strokes = await pipettes.stop_prime()
    self.assertGreaterEqual(strokes, 1)
    self.assertIn(({a: 480 for a in PUMPS}, 10), moves)
    with self.assertRaises(RuntimeError):
      await pipettes.stop_prime()

  async def test_an_instant_simulator_strokes_no_faster_than_the_least_time(self) -> None:
    # The simulator here answers at once; strokes still take a second each, and a stop is prompt.
    pod, pipettes, moves = await self.setup_pipettes()
    loop = asyncio.get_running_loop()
    await pipettes.start_prime()
    await asyncio.sleep(1.5)
    stopping = loop.time()
    self.assertEqual(await pipettes.stop_prime(), 2)
    self.assertLess(loop.time() - stopping, 0.5)

  async def test_no_prime_with_tips(self) -> None:
    pod, pipettes, moves = await self.setup_pipettes()
    pipettes._set_tips({1: biomek_tip("T190F", "t")})
    with self.assertRaises(RuntimeError):
      await pipettes.start_prime()
    self.assertEqual(moves, [])
