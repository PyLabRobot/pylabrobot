"""Unit tests for :mod:`.block`."""

from __future__ import annotations

import unittest

from .block import HeadBlock, HeadBlockError, head_block_for_identifiers, parse_item_identifier


class ParseItemIdentifierTests(unittest.TestCase):
  """parse_item_identifier."""

  def test_a1_is_the_origin(self):
    self.assertEqual(parse_item_identifier("A1"), (0, 0))

  def test_h12_is_the_last_cell_of_a_96_grid(self):
    self.assertEqual(parse_item_identifier("H12"), (7, 11))

  def test_two_letter_row_label_for_a_1536_head(self):
    # A 32-row 1536 head reaches row label "AF" (zero-based row 31).
    self.assertEqual(parse_item_identifier("AF48"), (31, 47))

  def test_malformed_identifier_raises_head_block_error(self):
    with self.assertRaises(HeadBlockError) as ctx:
      parse_item_identifier("1A")
    self.assertIn("'1A'", str(ctx.exception))

  def test_three_letter_row_label_raises_head_block_error(self):
    with self.assertRaises(HeadBlockError):
      parse_item_identifier("AAA1")


class HeadBlockGeometryTests(unittest.TestCase):
  """HeadBlock's derived properties and fits_within."""

  def test_single_cell_block_dimensions(self):
    block = HeadBlock(row_start=2, row_stop=3, col_start=4, col_stop=5)
    self.assertEqual(block.num_rows, 1)
    self.assertEqual(block.num_columns, 1)
    self.assertEqual(block.num_barrels, 1)

  def test_rectangle_block_dimensions(self):
    block = HeadBlock(row_start=0, row_stop=3, col_start=0, col_stop=4)
    self.assertEqual(block.num_rows, 3)
    self.assertEqual(block.num_columns, 4)
    self.assertEqual(block.num_barrels, 12)

  def test_fits_within_a_head_at_least_as_large(self):
    block = HeadBlock(row_start=0, row_stop=8, col_start=0, col_stop=12)
    self.assertTrue(block.fits_within(8, 12))
    self.assertTrue(block.fits_within(16, 24))

  def test_does_not_fit_a_head_with_too_few_rows(self):
    block = HeadBlock(row_start=0, row_stop=9, col_start=0, col_stop=1)
    self.assertFalse(block.fits_within(8, 12))

  def test_does_not_fit_a_head_with_too_few_columns(self):
    block = HeadBlock(row_start=0, row_stop=1, col_start=0, col_stop=13)
    self.assertFalse(block.fits_within(8, 12))

  def test_oversized_in_both_dimensions_does_not_fit(self):
    block = HeadBlock(row_start=0, row_stop=16, col_start=0, col_stop=24)
    self.assertFalse(block.fits_within(8, 12))


class HeadBlockForIdentifiersTests(unittest.TestCase):
  """head_block_for_identifiers: the shapes it accepts."""

  def test_single_well(self):
    block = head_block_for_identifiers(["C4"])
    self.assertEqual((block.row_start, block.row_stop), (2, 3))
    self.assertEqual((block.col_start, block.col_stop), (3, 4))

  def test_full_column(self):
    block = head_block_for_identifiers([f"{row}1" for row in "ABCDEFGH"])
    self.assertEqual((block.row_start, block.row_stop), (0, 8))
    self.assertEqual((block.col_start, block.col_stop), (0, 1))

  def test_full_row(self):
    block = head_block_for_identifiers([f"A{col}" for col in range(1, 13)])
    self.assertEqual((block.row_start, block.row_stop), (0, 1))
    self.assertEqual((block.col_start, block.col_stop), (0, 12))

  def test_quadrant(self):
    identifiers = [f"{row}{col}" for row in "ABCD" for col in range(1, 7)]
    block = head_block_for_identifiers(identifiers)
    self.assertEqual((block.row_start, block.row_stop), (0, 4))
    self.assertEqual((block.col_start, block.col_stop), (0, 6))
    self.assertEqual(block.num_barrels, 24)

  def test_offset_block_keeps_its_own_anchor(self):
    # A 2x3 block starting away from the grid's own origin: the block's
    # bounds should reflect exactly where it sits, not be shifted to A1.
    identifiers = ["C4", "C5", "C6", "D4", "D5", "D6"]
    block = head_block_for_identifiers(identifiers)
    self.assertEqual((block.row_start, block.row_stop), (2, 4))
    self.assertEqual((block.col_start, block.col_stop), (3, 6))
    self.assertEqual((block.num_rows, block.num_columns), (2, 3))

  def test_duplicate_identifiers_are_ignored(self):
    block = head_block_for_identifiers(["A1", "A1", "B1"])
    self.assertEqual((block.row_start, block.row_stop), (0, 2))

  def test_order_of_identifiers_does_not_matter(self):
    forward = head_block_for_identifiers(["A1", "A2", "B1", "B2"])
    backward = head_block_for_identifiers(["B2", "B1", "A2", "A1"])
    self.assertEqual(forward, backward)


class HeadBlockForIdentifiersRejectionTests(unittest.TestCase):
  """head_block_for_identifiers: rejections, asserted on message content."""

  def test_empty_selection_is_rejected(self):
    with self.assertRaises(HeadBlockError) as ctx:
      head_block_for_identifiers([])
    self.assertIn("No items were selected", str(ctx.exception))

  def test_l_shape_is_rejected_with_missing_cell_named(self):
    # A1, A2, B1 form an L; B2 is the missing corner that would complete it.
    with self.assertRaises(HeadBlockError) as ctx:
      head_block_for_identifiers(["A1", "A2", "B1"])
    message = str(ctx.exception)
    self.assertIn("do not form a contiguous rectangular block", message)
    self.assertIn("A1:B2", message)
    self.assertIn("B2", message)

  def test_diagonal_pair_is_rejected_with_both_gaps_named(self):
    with self.assertRaises(HeadBlockError) as ctx:
      head_block_for_identifiers(["A1", "B2"])
    message = str(ctx.exception)
    self.assertIn("A1:B2", message)
    self.assertIn("A2", message)
    self.assertIn("B1", message)

  def test_rectangle_with_a_hole_is_rejected(self):
    identifiers = [f"{row}{col}" for row in "ABC" for col in range(1, 4)]
    identifiers.remove("B2")
    with self.assertRaises(HeadBlockError) as ctx:
      head_block_for_identifiers(identifiers)
    message = str(ctx.exception)
    self.assertIn("A1:C3", message)
    self.assertIn("B2", message)

  def test_many_missing_cells_are_summarized_with_a_count(self):
    # A single corner cell out of a 9x9 block: 80 cells missing, well past
    # the point where the message should stop spelling every one out.
    with self.assertRaises(HeadBlockError) as ctx:
      head_block_for_identifiers(["A1", "I9"])
    message = str(ctx.exception)
    self.assertIn("and", message)
    self.assertIn("more", message)

  def test_invalid_identifier_in_the_set_is_rejected(self):
    with self.assertRaises(HeadBlockError) as ctx:
      head_block_for_identifiers(["A1", "not-an-id"])
    self.assertIn("not-an-id", str(ctx.exception))


if __name__ == "__main__":
  unittest.main()
