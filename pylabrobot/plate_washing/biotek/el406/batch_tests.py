# mypy: disable-error-code="union-attr,assignment,arg-type"


from pylabrobot.plate_washing.biotek.el406.mock_tests import PT96, EL406TestCase


class TestBatchContextManager(EL406TestCase):
  """Test the batch() context manager lifecycle."""

  async def test_batch_sets_in_batch_flag(self):
    """_in_batch should be True inside the context manager."""
    self.assertFalse(self.backend._in_batch)
    async with self.backend.batch(PT96):
      self.assertTrue(self.backend._in_batch)
    self.assertFalse(self.backend._in_batch)

  async def test_batch_sends_start_batch_commands(self):
    """Entering a batch should send start_batch commands to the device."""
    initial_count = len(self.backend.io.written_data)
    async with self.backend.batch(PT96):
      # start_batch sends 7 pre-batch commands + 1 START_STEP = 8 commands.
      # Each framed command sends header (and optionally data) as separate writes.
      self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_batch_sends_start_step_command(self):
    """Entering a batch should send the START_STEP (0x8D) command."""
    async with self.backend.batch(PT96):
      # Find the START_STEP command (0x8D) among the written data
      found = any(len(d) >= 3 and d[2] == 0x8D for d in self.backend.io.written_data)
      self.assertTrue(found, "START_STEP (0x8D) command not found in written data")

  async def test_batch_sends_cleanup_on_exit(self):
    """Exiting a batch should run cleanup_after_protocol."""
    async with self.backend.batch(PT96):
      count_inside = len(self.backend.io.written_data)

    # cleanup_after_protocol sends: home_motors, end_of_batch
    self.assertGreater(len(self.backend.io.written_data), count_inside)

  async def test_batch_sends_cleanup_on_exception(self):
    """Cleanup should run even when an exception is raised inside the batch."""
    with self.assertRaises(ValueError):
      async with self.backend.batch(PT96):
        count_inside = len(self.backend.io.written_data)
        raise ValueError("test error")

    # Cleanup commands should still have been sent
    self.assertGreater(len(self.backend.io.written_data), count_inside)
    self.assertFalse(self.backend._in_batch)

  async def test_batch_resets_flag_on_exception(self):
    """_in_batch should be reset even if the batch body raises."""
    with self.assertRaises(RuntimeError):
      async with self.backend.batch(PT96):
        raise RuntimeError("boom")

    self.assertFalse(self.backend._in_batch)


class TestBatchReentrancy(EL406TestCase):
  """Test that nested batch() calls are no-op passthroughs."""

  async def test_nested_batch_is_noop(self):
    """A batch() inside a batch() should not start a new batch."""
    async with self.backend.batch(PT96):
      count_before_inner = len(self.backend.io.written_data)
      async with self.backend.batch(PT96):
        count_after_inner = len(self.backend.io.written_data)

      # Inner batch should not have sent any start_batch commands
      self.assertEqual(count_before_inner, count_after_inner)

  async def test_nested_batch_does_not_cleanup(self):
    """An inner batch exiting should not trigger cleanup."""
    async with self.backend.batch(PT96):
      async with self.backend.batch(PT96):
        pass
      # Still inside the outer batch
      self.assertTrue(self.backend._in_batch)

    # Only now should _in_batch be False (outer batch exited)
    self.assertFalse(self.backend._in_batch)

  async def test_in_batch_stays_true_after_inner_exits(self):
    """_in_batch should remain True after an inner batch exits."""
    async with self.backend.batch(PT96):
      async with self.backend.batch(PT96):
        self.assertTrue(self.backend._in_batch)
      self.assertTrue(self.backend._in_batch)


class TestStepAutoBatching(EL406TestCase):
  """Test that step commands auto-batch when called outside a batch."""

  async def test_step_auto_batches(self):
    """A step command called outside a batch should auto-wrap in a batch."""
    self.assertFalse(self.backend._in_batch)
    await self.backend.shake(PT96, duration=5, intensity="Medium")
    # After the step, the auto-batch should have cleaned up
    self.assertFalse(self.backend._in_batch)

  async def test_step_sends_start_and_cleanup(self):
    """An auto-batched step should send start_batch + step + cleanup."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.shake(PT96, duration=5, intensity="Medium")
    written = self.backend.io.written_data[initial_count:]

    # Should contain START_STEP (0x8D) from start_batch
    has_start = any(len(d) >= 3 and d[2] == 0x8D for d in written)
    self.assertTrue(has_start, "Auto-batched step should send START_STEP")

    # Should contain the shake command (0xA3)
    has_shake = any(len(d) >= 3 and d[2] == 0xA3 for d in written)
    self.assertTrue(has_shake, "Auto-batched step should send SHAKE command")

    # Should contain end-of-batch marker (0x8C) from cleanup
    has_end = any(len(d) >= 3 and d[2] == 0x8C for d in written)
    self.assertTrue(has_end, "Auto-batched step should send end-of-batch marker")

  async def test_step_inside_batch_does_not_double_batch(self):
    """A step called inside a user batch should not create its own batch."""
    async with self.backend.batch(PT96):
      count_before = len(self.backend.io.written_data)
      await self.backend.shake(PT96, duration=5, intensity="Medium")
      written_during_step = self.backend.io.written_data[count_before:]

      # Should NOT contain START_STEP (0x8D) since we're already in a batch
      has_start = any(len(d) >= 3 and d[2] == 0x8D for d in written_during_step)
      self.assertFalse(has_start, "Step inside batch should not send START_STEP")

      # Should NOT contain end-of-batch (0x8C) since batch is still open
      has_end = any(len(d) >= 3 and d[2] == 0x8C for d in written_during_step)
      self.assertFalse(has_end, "Step inside batch should not send end-of-batch")

  async def test_multiple_steps_in_batch_share_single_batch(self):
    """Multiple steps inside one batch should only start/cleanup once."""
    initial_count = len(self.backend.io.written_data)

    async with self.backend.batch(PT96):
      await self.backend.shake(PT96, duration=5, intensity="Medium")
      await self.backend.shake(PT96, duration=3, intensity="Fast")

    written = self.backend.io.written_data[initial_count:]

    # Exactly one START_STEP
    start_count = sum(1 for d in written if len(d) >= 3 and d[2] == 0x8D)
    self.assertEqual(start_count, 1, "Should have exactly one START_STEP")

    # Exactly one end-of-batch marker
    end_count = sum(1 for d in written if len(d) >= 3 and d[2] == 0x8C)
    self.assertEqual(end_count, 1, "Should have exactly one end-of-batch marker")

    # Two shake commands
    shake_count = sum(1 for d in written if len(d) >= 3 and d[2] == 0xA3)
    self.assertEqual(shake_count, 2, "Should have two SHAKE commands")
