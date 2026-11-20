"""Replayed boards: what they answer, and what they do not."""

import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.simulator import (
  RECORDING_ENV,
  ReplayAceUSB,
  load_recording,
  recording_path,
  replay_boards,
)

# Get Module ID answered once, and a Move answered twice (ACCEPTED, then DONE); keys leave out the
# sequence number, and the replies' sequence bytes are placeholders.
RECORDING = {
  "00000000": ["000000000002008000"],
  "1103005030750000": ["11030000500100", "11030000500200"],
}


class ReplayTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.io = ReplayAceUSB(RECORDING)
    self.board = AceBoard(io=self.io, default_timeout=0.3)
    await self.board.setup()

  async def asyncTearDown(self) -> None:
    await self.board.stop()

  async def test_a_recorded_request_is_answered_under_its_own_sequence(self) -> None:
    for _ in range(3):  # each send takes the next sequence number; the recording ignores it
      reply = await self.board.send(p.get_module_id())
      self.assertEqual(p.parse_module_id(reply.payload).type, "chassis")
    self.assertEqual([m[3] for m in self.io.requests], [1, 2, 3])
    self.assertEqual(self.io.unanswered, [])

  async def test_replies_come_in_their_recorded_order(self) -> None:
    reply = await self.board.send(p.move(3, 30000))
    self.assertEqual(reply.status, p.Status.DONE)

  async def test_an_unrecorded_request_gets_no_answer(self) -> None:
    with self.assertRaises(TimeoutError):
      await self.board.send(p.get_module_serial_number())
    self.assertEqual(self.io.unanswered, [p.get_module_serial_number().encode(1)])


class RecordingFileTests(unittest.TestCase):
  def test_gzipped_and_plain_recordings_load(self) -> None:
    data = {"boards": {"chassis": [["00000000", ["000000000002008000"]]]}}
    with tempfile.TemporaryDirectory() as folder:
      plain = Path(folder) / "recording.json"
      plain.write_text(json.dumps(data), encoding="utf-8")
      packed = Path(folder) / "recording.json.gz"
      with gzip.open(packed, "wt", encoding="utf-8") as fh:
        json.dump(data, fh)
      self.assertEqual(load_recording(plain), data)
      self.assertEqual(load_recording(packed), data)
      boards = replay_boards(packed)
    self.assertEqual([b.name for b in boards], ["replayed chassis"])

  def test_a_recording_must_be_named(self) -> None:
    environment = {k: v for k, v in os.environ.items() if k != RECORDING_ENV}
    with mock.patch.dict(os.environ, environment, clear=True):
      with self.assertRaisesRegex(ValueError, RECORDING_ENV):
        recording_path()
    with mock.patch.dict(os.environ, {RECORDING_ENV: "from-the-environment.json"}):
      self.assertEqual(recording_path(), Path("from-the-environment.json"))
      self.assertEqual(recording_path("given.json"), Path("given.json"))


if __name__ == "__main__":
  unittest.main()
