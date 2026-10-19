"""Generate RPC bindings and annotate heterogeneous method-handler dictionaries."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = (
  "pylabrobot/rpc/v1/device.proto",
  "pylabrobot/rpc/v1/jobs.proto",
  "pylabrobot/rpc/v1/images.proto",
  "pylabrobot/revvity/celigo/grpc/v1/service.proto",
  "pylabrobot/revvity/celigo/grpc/v1/storage.proto",
)


def main() -> None:
  """Run protoc and retain handler types across different request/response pairs."""
  subprocess.run(
    [
      sys.executable,
      "-m",
      "grpc_tools.protoc",
      "-I.",
      "--python_out=.",
      "--pyi_out=.",
      "--grpc_python_out=.",
      *SCHEMAS,
    ],
    cwd=ROOT,
    check=True,
  )
  for schema in SCHEMAS:
    binding = ROOT / schema.replace(".proto", "_pb2_grpc.py")
    binding.write_text(
      binding.read_text().replace(
        "    rpc_method_handlers = {",
        "    rpc_method_handlers: dict[str, grpc.RpcMethodHandler] = {",
      )
    )


if __name__ == "__main__":
  main()
