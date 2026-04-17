import argparse
import asyncio
import contextlib
import dataclasses
import datetime
import importlib.util
import inspect
import io
import json
import pathlib
import sys
import traceback
import types
import typing
import uuid


def _utcnow() -> datetime.datetime:
  return datetime.datetime.now(datetime.timezone.utc)


def _to_jsonable(value: typing.Any) -> typing.Any:
  if dataclasses.is_dataclass(value):
    return _to_jsonable(dataclasses.asdict(value))
  if isinstance(value, pathlib.Path):
    return str(value)
  if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
    return value.isoformat()
  if isinstance(value, dict):
    return {str(k): _to_jsonable(v) for k, v in value.items()}
  if isinstance(value, (list, tuple, set)):
    return [_to_jsonable(v) for v in value]
  if isinstance(value, (str, int, float, bool)) or value is None:
    return value
  return repr(value)


@dataclasses.dataclass
class FirmwareCommandRecord:
  timestamp: str
  backend_label: str
  module: str
  command: str
  kwargs: dict
  response: typing.Any = None
  error_type: typing.Optional[str] = None
  error_message: typing.Optional[str] = None

  def to_dict(self) -> dict:
    return _to_jsonable(dataclasses.asdict(self))


@dataclasses.dataclass
class RobotRunResult:
  run_id: str
  status: str
  script_path: str
  operation: str
  started_at: str
  finished_at: str
  timeout_seconds: float
  exception_type: typing.Optional[str] = None
  exception_message: typing.Optional[str] = None
  traceback: typing.Optional[str] = None
  notes: typing.Optional[list] = None
  metadata: typing.Optional[dict] = None
  firmware_context: typing.Optional[list] = None
  artifacts: typing.Optional[dict] = None
  cleanup_error: typing.Optional[str] = None

  def to_dict(self) -> dict:
    return _to_jsonable(dataclasses.asdict(self))


@dataclasses.dataclass
class RobotRunJob:
  script: pathlib.Path
  result_json: pathlib.Path
  raw_log: pathlib.Path
  command_log_jsonl: pathlib.Path
  artifact_dir: pathlib.Path
  operation: str = "unspecified"
  timeout_seconds: float = 300.0
  cleanup_timeout_seconds: float = 30.0
  run_id: str = dataclasses.field(default_factory=lambda: uuid.uuid4().hex)
  metadata: dict = dataclasses.field(default_factory=dict)


class _TeeTextIO(io.TextIOBase):
  def __init__(self, *targets: typing.TextIO):
    self.targets = targets

  def write(self, data: str) -> int:
    for target in self.targets:
      target.write(data)
      target.flush()
    return len(data)

  def flush(self) -> None:
    for target in self.targets:
      target.flush()


class RobotJobContext:
  def __init__(
    self,
    *,
    run_id: str,
    script_path: pathlib.Path,
    artifact_dir: pathlib.Path,
    command_log_jsonl: pathlib.Path,
    operation: str,
    metadata: typing.Optional[dict] = None,
  ):
    self.run_id = run_id
    self.script_path = script_path
    self.artifact_dir = artifact_dir
    self.command_log_jsonl = command_log_jsonl
    self.operation = operation
    self.metadata = metadata or {}
    self.notes: list[str] = []
    self._registered_backends: list[dict] = []

  def add_note(self, message: str) -> None:
    self.notes.append(message)

  def write_json_artifact(self, relative_path: str, data: typing.Any) -> pathlib.Path:
    target = self.artifact_dir / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(_to_jsonable(data), indent=2, sort_keys=True), encoding="utf-8")
    return target

  def register_backend(
    self,
    backend: typing.Any,
    *,
    label: typing.Optional[str] = None,
    capture_commands: bool = True,
  ) -> typing.Any:
    backend_label = label or f"{type(backend).__name__}_{len(self._registered_backends)}"
    registration = {
      "label": backend_label,
      "backend": backend,
      "original_send_command": getattr(backend, "send_command", None),
      "capture_commands": capture_commands,
    }
    self._registered_backends.append(registration)

    if capture_commands and registration["original_send_command"] is not None:
      original_send_command = registration["original_send_command"]

      async def wrapped_send_command(instance, *args, **kwargs):
        module = kwargs.get("module")
        command = kwargs.get("command")
        if len(args) >= 1:
          module = args[0]
        if len(args) >= 2:
          command = args[1]

        command_kwargs = {
          key: value
          for key, value in kwargs.items()
          if key not in {"module", "command"}
        }

        record = FirmwareCommandRecord(
          timestamp=_utcnow().isoformat(),
          backend_label=backend_label,
          module=str(module),
          command=str(command),
          kwargs=_to_jsonable(command_kwargs),
        )
        try:
          response = await original_send_command(*args, **kwargs)
          record.response = _to_jsonable(response)
          return response
        except Exception as exc:
          record.error_type = type(exc).__name__
          record.error_message = str(exc)
          raise
        finally:
          self._append_command_record(record)

      backend.send_command = types.MethodType(wrapped_send_command, backend)

    return backend

  def _append_command_record(self, record: FirmwareCommandRecord) -> None:
    self.command_log_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with self.command_log_jsonl.open("a", encoding="utf-8") as handle:
      handle.write(json.dumps(record.to_dict(), sort_keys=True))
      handle.write("\n")

  def collect_firmware_context(self) -> list[dict]:
    contexts = []
    for registration in self._registered_backends:
      backend = registration["backend"]
      head96_information = getattr(backend, "_head96_information", None)
      contexts.append(
        {
          "label": registration["label"],
          "backend_type": type(backend).__name__,
          "pip_firmware_version": _to_jsonable(getattr(backend, "_pip_firmware_version", None)),
          "head96_information": _to_jsonable(head96_information),
          "machine_conf": _to_jsonable(getattr(backend, "_machine_conf", None)),
          "extended_conf": _to_jsonable(getattr(backend, "_extended_conf", None)),
        }
      )
    return contexts

  async def best_effort_stop_backends(self) -> None:
    for registration in reversed(self._registered_backends):
      backend = registration["backend"]
      stop_steps = [
        ("move_all_channels_in_z_safety", None),
        ("move_core_96_to_safe_position", None),
        ("park_iswap", None),
        ("park_autoload", None),
        ("stop", None),
      ]
      for method_name, argument in stop_steps:
        method = getattr(backend, method_name, None)
        if method is None:
          continue
        try:
          result = method() if argument is None else method(argument)
          if inspect.isawaitable(result):
            await result
        except Exception as exc:  # pragma: no cover - cleanup is intentionally best effort
          self.add_note(f"cleanup step {method_name} failed on {registration['label']}: {exc}")

  def restore_backend_hooks(self) -> None:
    for registration in self._registered_backends:
      original_send_command = registration["original_send_command"]
      if registration["capture_commands"] and original_send_command is not None:
        registration["backend"].send_command = original_send_command


def _load_script_module(script_path: pathlib.Path):
  module_name = f"robot_loop_script_{uuid.uuid4().hex}"
  spec = importlib.util.spec_from_file_location(module_name, script_path)
  if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to import runner script from {script_path}")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


async def _invoke_callable(func: typing.Callable, context: RobotJobContext):
  signature = inspect.signature(func)
  if len(signature.parameters) == 0:
    result = func()
  else:
    result = func(context)
  if inspect.isawaitable(result):
    return await result
  return result


async def execute_job(job: RobotRunJob) -> RobotRunResult:
  job.artifact_dir.mkdir(parents=True, exist_ok=True)
  job.result_json.parent.mkdir(parents=True, exist_ok=True)
  job.raw_log.parent.mkdir(parents=True, exist_ok=True)
  job.command_log_jsonl.parent.mkdir(parents=True, exist_ok=True)

  context = RobotJobContext(
    run_id=job.run_id,
    script_path=job.script,
    artifact_dir=job.artifact_dir,
    command_log_jsonl=job.command_log_jsonl,
    operation=job.operation,
    metadata=job.metadata,
  )
  started_at = _utcnow()
  status = "success"
  exception_type = None
  exception_message = None
  traceback_text = None
  cleanup_error = None

  with job.raw_log.open("a", encoding="utf-8") as raw_log_handle:
    tee_stdout = _TeeTextIO(sys.stdout, raw_log_handle)
    tee_stderr = _TeeTextIO(sys.stderr, raw_log_handle)
    with contextlib.redirect_stdout(tee_stdout), contextlib.redirect_stderr(tee_stderr):
      print(f"[robot-loop] run_id={job.run_id} operation={job.operation} script={job.script}")
      cleanup_callable = None

      try:
        module = _load_script_module(job.script)
        run_callable = getattr(module, "run", None)
        cleanup_callable = getattr(module, "cleanup", None)
        if run_callable is None:
          raise RuntimeError(f"Runner script {job.script} must define a callable named 'run'.")
        await asyncio.wait_for(_invoke_callable(run_callable, context), timeout=job.timeout_seconds)
      except asyncio.TimeoutError as exc:
        status = "timeout"
        exception_type = type(exc).__name__
        exception_message = f"runner timed out after {job.timeout_seconds} seconds"
        traceback_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
      except Exception as exc:  # pylint: disable=broad-except
        status = "failure"
        exception_type = type(exc).__name__
        exception_message = str(exc)
        traceback_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
      finally:
        try:
          if cleanup_callable is not None:
            await asyncio.wait_for(
              _invoke_callable(cleanup_callable, context), timeout=job.cleanup_timeout_seconds
            )
        except Exception as exc:  # pylint: disable=broad-except
          cleanup_error = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        try:
          await asyncio.wait_for(
            context.best_effort_stop_backends(), timeout=job.cleanup_timeout_seconds
          )
        except Exception as exc:  # pylint: disable=broad-except
          suffix = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
          cleanup_error = suffix if cleanup_error is None else cleanup_error + "\n" + suffix
        finally:
          context.restore_backend_hooks()

  finished_at = _utcnow()
  result = RobotRunResult(
    run_id=job.run_id,
    status=status,
    script_path=str(job.script),
    operation=job.operation,
    started_at=started_at.isoformat(),
    finished_at=finished_at.isoformat(),
    timeout_seconds=job.timeout_seconds,
    exception_type=exception_type,
    exception_message=exception_message,
    traceback=traceback_text,
    notes=context.notes,
    metadata=job.metadata,
    firmware_context=context.collect_firmware_context(),
    cleanup_error=cleanup_error,
    artifacts={
      "artifact_dir": str(job.artifact_dir),
      "result_json": str(job.result_json),
      "raw_log": str(job.raw_log),
      "command_log_jsonl": str(job.command_log_jsonl),
    },
  )
  job.result_json.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
  return result


def load_job(
  *,
  script: typing.Optional[str] = None,
  result_json: typing.Optional[str] = None,
  raw_log: typing.Optional[str] = None,
  command_log_jsonl: typing.Optional[str] = None,
  artifact_dir: typing.Optional[str] = None,
  operation: str = "unspecified",
  timeout_seconds: float = 300.0,
  cleanup_timeout_seconds: float = 30.0,
  run_id: typing.Optional[str] = None,
  metadata: typing.Optional[dict] = None,
  job_json: typing.Optional[str] = None,
) -> RobotRunJob:
  payload = {}
  if job_json is not None:
    payload = json.loads(pathlib.Path(job_json).read_text(encoding="utf-8"))

  script_path = pathlib.Path(script or payload["script"]).resolve()
  artifact_root = pathlib.Path(
    artifact_dir or payload.get("artifact_dir") or script_path.parent / "robot_loop_artifacts"
  ).resolve()
  resolved_run_id = run_id or payload.get("run_id") or uuid.uuid4().hex

  result_path = pathlib.Path(
    result_json or payload.get("result_json") or artifact_root / f"{resolved_run_id}_result.json"
  ).resolve()
  raw_log_path = pathlib.Path(
    raw_log or payload.get("raw_log") or artifact_root / f"{resolved_run_id}_raw.log"
  ).resolve()
  command_log_path = pathlib.Path(
    command_log_jsonl
    or payload.get("command_log_jsonl")
    or artifact_root / f"{resolved_run_id}_commands.jsonl"
  ).resolve()

  combined_metadata = {}
  combined_metadata.update(payload.get("metadata", {}))
  if metadata:
    combined_metadata.update(metadata)

  return RobotRunJob(
    script=script_path,
    result_json=result_path,
    raw_log=raw_log_path,
    command_log_jsonl=command_log_path,
    artifact_dir=artifact_root,
    operation=payload.get("operation", operation),
    timeout_seconds=float(payload.get("timeout_seconds", timeout_seconds)),
    cleanup_timeout_seconds=float(
      payload.get("cleanup_timeout_seconds", cleanup_timeout_seconds)
    ),
    run_id=resolved_run_id,
    metadata=combined_metadata,
  )


def _parse_metadata(args_metadata: typing.Optional[str]) -> dict:
  if args_metadata is None:
    return {}
  return typing.cast(dict, json.loads(args_metadata))


def main(argv: typing.Optional[list[str]] = None) -> int:
  parser = argparse.ArgumentParser(description="Run a local Codex hardware iteration job.")
  parser.add_argument("--script", help="Path to the generated Python runner script.")
  parser.add_argument("--job-json", help="Optional path to a JSON job specification.")
  parser.add_argument("--result-json", help="Path where the structured result JSON should be written.")
  parser.add_argument("--raw-log", help="Path where the raw stdout/stderr log should be written.")
  parser.add_argument(
    "--command-log-jsonl",
    help="Path where captured backend firmware command records should be written.",
  )
  parser.add_argument("--artifact-dir", help="Directory for run artifacts.")
  parser.add_argument("--operation", default="unspecified", help="Operation category for this run.")
  parser.add_argument("--timeout", type=float, default=300.0, help="Main run timeout in seconds.")
  parser.add_argument(
    "--cleanup-timeout", type=float, default=30.0, help="Cleanup timeout in seconds."
  )
  parser.add_argument("--run-id", help="Optional run identifier.")
  parser.add_argument("--metadata-json", help="Inline JSON metadata to include in the result.")

  args = parser.parse_args(argv)
  metadata = _parse_metadata(args.metadata_json)
  job = load_job(
    script=args.script,
    job_json=args.job_json,
    result_json=args.result_json,
    raw_log=args.raw_log,
    command_log_jsonl=args.command_log_jsonl,
    artifact_dir=args.artifact_dir,
    operation=args.operation,
    timeout_seconds=args.timeout,
    cleanup_timeout_seconds=args.cleanup_timeout,
    run_id=args.run_id,
    metadata=metadata,
  )
  result = asyncio.run(execute_job(job))
  print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
  return 0 if result.status == "success" else 1


if __name__ == "__main__":
  raise SystemExit(main())
