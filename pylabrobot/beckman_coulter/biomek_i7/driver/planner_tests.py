import unittest
from typing import Sequence

from pylabrobot.beckman_coulter.biomek_i7.driver.planning import merge

START = {"X": 0, "Y": 0, "Z": 0, "GY": 0, "GZ": 0, "GR": 0}


def anything(moves: Sequence[merge.Move]) -> bool:
  return True


class DirectCandidateTests(unittest.TestCase):
  def test_one_axis_at_a_time_in_biomeks_order(self) -> None:
    moves = merge.direct_candidate({"GR": 4, "Y": 2, "X": 1, "GY": 3, "S": 9})
    # The span is not a planned axis: it goes with the first move.
    self.assertEqual(moves, [{"S": 9, "X": 1}, {"Y": 2}, {"GY": 3}, {"GR": 4}])

  def test_nothing_to_move(self) -> None:
    self.assertEqual(merge.direct_candidate({}), [])


class MergePathTests(unittest.TestCase):
  """Biomek's merging of a planned path (spec §5.4), step by step."""

  def test_a_clear_travel_is_one_move(self) -> None:
    path = merge.direct_candidate({"X": 1, "Y": 2, "GY": 3, "GR": 4})
    self.assertEqual(merge.merge_path(path, START, anything), [{"X": 1, "Y": 2, "GY": 3, "GR": 4}])

  def test_moves_join_only_where_the_joined_move_is_clear(self) -> None:
    # X and Y together would cut a corner (not clear); Y with GR is clear.
    def viable(moves: Sequence[merge.Move]) -> bool:
      return not any("X" in m and "Y" in m for m in moves)

    path = [{"X": 1}, {"Y": 2}, {"GR": 4}]
    self.assertEqual(merge.merge_path(path, START, viable), [{"X": 1}, {"Y": 2, "GR": 4}])

  def test_a_vertical_move_never_joins_a_horizontal_move_of_its_part(self) -> None:
    path = [{"Z": 5}, {"X": 1}, {"GZ": 5}, {"GY": 3}]
    # Z up and GZ up go first (step 3) and join; X and GY join; never Z or GZ with X or GY.
    self.assertEqual(merge.merge_path(path, START, anything), [{"Z": 5, "GZ": 5}, {"X": 1, "GY": 3}])

  def test_a_later_step_on_the_same_axis_is_summed_into_an_earlier_one(self) -> None:
    path = [{"X": 1}, {"GR": 4}, {"X": 3}]
    # In steps: X +1, GR +4, X +2; the later X pulled forward and summed: X +3, then GR.
    merged = merge.merge_path(path, START, lambda moves: not any("GR" in m and "X" in m for m in moves))
    self.assertEqual(merged, [{"X": 3}, {"GR": 4}])

  def test_vertical_steps_are_summed_only_the_same_way(self) -> None:
    path = [{"Z": 5}, {"X": 1}, {"Z": 2}]
    # Up then down: not pulled together; the descent stays after the travel.
    merged = merge.merge_path(path, START, anything)
    self.assertEqual(merged, [{"Z": 5}, {"X": 1}, {"Z": 2}])

  def test_a_rise_moves_earlier_while_clear(self) -> None:
    path = [{"X": 1}, {"Z": 5}]
    self.assertEqual(merge.merge_path(path, START, anything), [{"Z": 5}, {"X": 1}])

    def low_x(moves: Sequence[merge.Move]) -> bool:  # Z up before X is not clear
      return not (moves and "Z" in moves[0])

    self.assertEqual(merge.merge_path(path, START, low_x), [{"X": 1}, {"Z": 5}])

  def test_x_pairs_with_a_later_y(self) -> None:
    path = [{"X": 1}, {"GR": 4}, {"Y": 2}]

    def no_turn_with_x(moves: Sequence[merge.Move]) -> bool:
      return not any("GR" in m and "X" in m for m in moves)

    # Y pulled forward next to X (step 4), the two joined; the turn after them.
    self.assertEqual(merge.merge_path(path, START, no_turn_with_x), [{"X": 1, "Y": 2}, {"GR": 4}])

  def test_a_span8s_probes_move_as_its_z_and_y(self) -> None:
    start = {"X": 0, "Y": 0, "S": 7, **{f"Z{i}": 0 for i in range(1, 9)}}
    path = [{f"Z{i}": 5 for i in range(1, 9)}, {"X": 1}, {"Y": 2, "S": 7}]
    merged = merge.merge_path(path, start, anything)
    self.assertEqual(merged, [{f"Z{i}": 5 for i in range(1, 9)}, {"X": 1, "Y": 2, "S": 7}])


if __name__ == "__main__":
  unittest.main()
