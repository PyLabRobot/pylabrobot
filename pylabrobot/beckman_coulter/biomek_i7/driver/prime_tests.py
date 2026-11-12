"""Priming the Span-8 on the simulator, against Biomek's logged prime (the Master Controller's log,
2026-10-09)."""

import asyncio
from typing import Dict, List, Tuple

from pylabrobot.beckman_coulter.biomek_i7.device import BiomekI7
from pylabrobot.beckman_coulter.biomek_i7.driver.pod_move_tests import PodMoveTest
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_deck
from pylabrobot.beckman_coulter.biomek_i7.resource_model.labware import biomek_tip

# The logged prime: the probes into the wash station, each pump's full stroke and the air gap.
LOGGED_Z = [-72401, -72247, -72407, -72736, -72571, -72338, -72298, -72356]
LOGGED_X, LOGGED_Y = -7761, 203370
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

  async def test_a_prime_moves_as_biomeks(self) -> None:
    pod, pipettes, moves = await self.setup_pipettes()
    self.assertEqual(await pipettes.prime(3, minimum_stroke_seconds=0), 3)
    lowered = next(t for t, s in moves if s == 250)
    self.assertEqual([lowered[f"Z{n}"] for n in range(1, 9)], LOGGED_Z)
    travel = next(t for t, _ in moves if "X" in t)
    self.assertEqual(travel["X"], LOGGED_X)
    self.assertAlmostEqual(travel["Y"], LOGGED_Y, delta=2)  # the deck position's calibration
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

  async def test_a_prime_ends_parked_as_biomeks(self) -> None:
    # Logged after the prime: X/Y where it primed, the probes at 9029, the gripper to its park.
    pod, pipettes, moves = await self.setup_pipettes()
    await pipettes.prime(1, minimum_stroke_seconds=0)
    for axis, logged in [("X", LOGGED_X), ("GY", 63629), ("GR", -175774), ("GG", 34873)]:
      self.assertEqual(pod.cpositions[axis][1], logged, axis)
    self.assertAlmostEqual(pod.cpositions["Y1"][1], LOGGED_Y, delta=2)
    self.assertEqual({pod.cpositions[f"Z{n}"][1] for n in range(1, 9)}, {9029})

  async def test_the_96_head_pod_parks_as_biomeks(self) -> None:
    driver = await self.homed()
    BiomekI7(driver=driver, deck=generic_deck())
    pod = self.pod(driver, "left")
    await pod.park()
    logged = {"X": 35843, "Y": 37258, "Z": -256, "GY": 63763, "GR": -175443, "GG": 34983}
    self.assertEqual({a: pod.cpositions[a][1] for a in logged}, logged)

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
