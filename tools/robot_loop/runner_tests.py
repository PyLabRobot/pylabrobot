import json
import pathlib
import tempfile
import textwrap
import unittest

from tools.robot_loop.runner import execute_job, load_job


class FakeBackend:
  def __init__(self):
    self._pip_firmware_version = "2009-05-01"
    self._machine_conf = {"channels": 8}
    self._extended_conf = {"iswap_installed": False}
    self.cleanup_calls = []

  async def send_command(self, module, command, **kwargs):
    if command == "ERR":
      raise RuntimeError("firmware rejected command")
    return {"module": module, "command": command, "kwargs": kwargs}

  async def move_all_channels_in_z_safety(self):
    self.cleanup_calls.append("move_all_channels_in_z_safety")

  async def stop(self):
    self.cleanup_calls.append("stop")


class RobotLoopRunnerTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self._tempdir = tempfile.TemporaryDirectory()
    self.addCleanup(self._tempdir.cleanup)
    self.tmp_path = pathlib.Path(self._tempdir.name)

  def _write_script(self, directory: pathlib.Path, name: str, body: str) -> pathlib.Path:
    script_path = directory / name
    script_path.write_text(textwrap.dedent(body), encoding="utf-8")
    return script_path

  async def test_execute_job_success_captures_firmware_context_and_commands(self):
    script = self._write_script(
      self.tmp_path,
      "success_script.py",
      """
      from tools.robot_loop.runner_tests import FakeBackend

      async def run(context):
        backend = FakeBackend()
        context.register_backend(backend, label="fake-star")
        await backend.send_command("C0", "RF")
      """,
    )
    job = load_job(script=str(script), artifact_dir=str(self.tmp_path), run_id="success")
    result = await execute_job(job)

    self.assertEqual(result.status, "success")
    self.assertEqual(result.firmware_context[0]["pip_firmware_version"], "2009-05-01")

    commands = job.command_log_jsonl.read_text(encoding="utf-8").strip().splitlines()
    self.assertEqual(len(commands), 1)
    self.assertEqual(json.loads(commands[0])["command"], "RF")

  async def test_execute_job_failure_records_exception(self):
    script = self._write_script(
      self.tmp_path,
      "failure_script.py",
      """
      async def run(context):
        raise ValueError("bad run")
      """,
    )
    job = load_job(script=str(script), artifact_dir=str(self.tmp_path), run_id="failure")
    result = await execute_job(job)

    self.assertEqual(result.status, "failure")
    self.assertEqual(result.exception_type, "ValueError")
    self.assertIn("bad run", result.exception_message)

  async def test_execute_job_timeout_records_timeout_status(self):
    script = self._write_script(
      self.tmp_path,
      "timeout_script.py",
      """
      import asyncio

      async def run(context):
        await asyncio.sleep(1)
      """,
    )
    job = load_job(
      script=str(script),
      artifact_dir=str(self.tmp_path),
      run_id="timeout",
      timeout_seconds=0.01,
    )
    result = await execute_job(job)

    self.assertEqual(result.status, "timeout")
    self.assertEqual(result.exception_type, "TimeoutError")

  async def test_execute_job_cleanup_runs_on_registered_backend(self):
    script = self._write_script(
      self.tmp_path,
      "cleanup_script.py",
      """
      from tools.robot_loop.runner_tests import FakeBackend

      backend = FakeBackend()

      async def run(context):
        context.register_backend(backend, label="fake-star")
        await backend.send_command("C0", "ERR")
      """,
    )
    job = load_job(script=str(script), artifact_dir=str(self.tmp_path), run_id="cleanup")
    result = await execute_job(job)

    self.assertEqual(result.status, "failure")
    command_record = json.loads(job.command_log_jsonl.read_text(encoding="utf-8").strip())
    self.assertEqual(command_record["error_type"], "RuntimeError")
    raw_log = job.raw_log.read_text(encoding="utf-8")
    self.assertIn("run_id=cleanup", raw_log)
