"""Hardware driver for the Micronic rack scanner.

This driver does not call Micronic Code Reader or IO Monitor. It owns the local
scanner path directly:

- acquire a rack image through a user-supplied scan command, Windows TWAIN
  helper, or SANE ``scanimage`` command,
- read the rack ID through the side serial barcode reader,
- decode tube DataMatrix codes locally, and
- return the standard PLR rack-reading result.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess  # nosec B404 - local scanner/rack-id helper execution is the interface.
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.rack_reading import (
  LayoutInfo,
  RackReaderState,
  RackScanEntry,
  RackScanResult,
)
from pylabrobot.device import Driver
from pylabrobot.io.serial import Serial


ROWS = "ABCDEFGH"
COLS = 12
RACK_ROWS = 8
RACK_COLS = 12


class MicronicError(Exception):
  """Raised when Micronic driver operations fail."""


@dataclass(frozen=True)
class DecodeResult:
  tube_id: str
  method: str


class MicronicDriver(Driver):
  """Driver that controls the Micronic scanner without the OEM app."""

  def __init__(
    self,
    twain_scanner_path: Optional[str] = None,
    twain_source: str = "AVA6PlusG",
    sane_device: Optional[str] = None,
    scanner_backend: str = "auto",
    scan_command: Optional[Sequence[str]] = None,
    image_extension: Optional[str] = None,
    image_dir: Optional[str] = None,
    serial_port: str = "COM4",
    rack_id_command: Optional[Sequence[str]] = None,
    scanner_timeout_ms: int = 90000,
    serial_timeout_ms: int = 2500,
    min_wells: int = 96,
    keep_images: bool = False,
    image_input: Optional[str] = None,
    rack_id_override: Optional[str] = None,
  ):
    super().__init__()
    self.twain_scanner_path = twain_scanner_path
    self.twain_source = twain_source
    self.sane_device = sane_device
    self.scanner_backend = scanner_backend
    self.scan_command = list(scan_command) if scan_command is not None else None
    self.image_extension = normalize_image_extension(image_extension) if image_extension else None
    self.image_dir = (
      Path(image_dir) if image_dir else Path(tempfile.gettempdir()) / "alakascan-micronic"
    )
    self.serial_port = serial_port
    self.rack_id_command = list(rack_id_command) if rack_id_command is not None else None
    self.scanner_timeout_ms = scanner_timeout_ms
    self.serial_timeout_ms = serial_timeout_ms
    self.min_wells = min_wells
    self.keep_images = keep_images
    self.image_input = image_input
    self.rack_id_override = rack_id_override
    self._state = RackReaderState.IDLE
    self._last_result: Optional[RackScanResult] = None
    self._scan_task: Optional[asyncio.Future[RackScanResult]] = None
    self._scan_error: Optional[Exception] = None
    self._reported_scanning_since_trigger = False
    self.last_image_path: Optional[Path] = None
    self.last_scan_metadata: dict[str, object] = {}
    self.last_decode_metadata: dict[str, object] = {}

  async def setup(self, backend_params: Optional[BackendParams] = None):
    del backend_params
    self.image_dir.mkdir(parents=True, exist_ok=True)

  async def stop(self):
    scan_task = self._scan_task
    if scan_task is None:
      if self._scan_error is not None:
        raise self._scan_error
      return
    if scan_task.done():
      self._complete_scan_task(scan_task)
    else:
      try:
        self._last_result = await scan_task
      except asyncio.CancelledError:
        self._scan_error = MicronicError("Micronic rack scan was cancelled.")
        self._state = RackReaderState.IDLE
      except Exception as exc:
        self._scan_error = exc
        self._state = RackReaderState.IDLE
      else:
        self._scan_error = None
        self._state = RackReaderState.DATAREADY
      finally:
        self._scan_task = None
    if self._scan_error is not None:
      raise self._scan_error

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "twain_scanner_path": self.twain_scanner_path,
      "twain_source": self.twain_source,
      "sane_device": self.sane_device,
      "scanner_backend": self.scanner_backend,
      "scan_command": self.scan_command,
      "image_extension": self.image_extension,
      "image_dir": str(self.image_dir),
      "serial_port": self.serial_port,
      "rack_id_command": self.rack_id_command,
      "scanner_timeout_ms": self.scanner_timeout_ms,
      "serial_timeout_ms": self.serial_timeout_ms,
      "min_wells": self.min_wells,
      "keep_images": self.keep_images,
      "image_input": self.image_input,
      "rack_id_override": self.rack_id_override,
    }

  async def get_rack_reader_state(self) -> RackReaderState:
    if self._state == RackReaderState.SCANNING and not self._reported_scanning_since_trigger:
      self._reported_scanning_since_trigger = True
      return self._state
    self._complete_finished_scan_task()
    if self._scan_error is not None:
      raise self._scan_error
    return self._state

  async def trigger_rack_scan(self) -> None:
    self._complete_finished_scan_task()
    if self._scan_task is not None and not self._scan_task.done():
      raise MicronicError("Micronic rack scan is already in progress.")
    self._last_result = None
    self._state = RackReaderState.SCANNING
    self._scan_error = None
    self._reported_scanning_since_trigger = False
    loop = asyncio.get_running_loop()
    self._scan_task = loop.run_in_executor(None, self._scan_rack_blocking)

  async def scan_rack_id(self, timeout: float, poll_interval: float) -> str:
    del timeout, poll_interval
    if self.rack_id_override:
      return self.rack_id_override
    if self.rack_id_command is not None:
      return await asyncio.to_thread(
        read_rack_id_command,
        format_command(
          self.rack_id_command,
          serial_port=self.serial_port,
          timeout_ms=self.serial_timeout_ms,
        ),
        self.serial_timeout_ms,
      )
    return await read_rack_id_plr_serial(
      serial_port=self.serial_port,
      timeout_ms=self.serial_timeout_ms,
    )

  async def get_scan_result(self) -> RackScanResult:
    self._complete_finished_scan_task()
    if self._scan_error is not None:
      raise self._scan_error
    if self._state == RackReaderState.SCANNING:
      raise MicronicError("Micronic rack scan is still in progress.")
    if self._last_result is None:
      raise MicronicError("No Micronic rack scan has completed yet.")
    return self._last_result

  async def get_rack_id(self) -> str:
    self._complete_finished_scan_task()
    if self._scan_error is not None:
      raise self._scan_error
    if self._state == RackReaderState.SCANNING:
      raise MicronicError("Micronic rack scan is still in progress.")
    if self._last_result is not None:
      return self._last_result.rack_id
    return await self.scan_rack_id(timeout=0, poll_interval=0)

  def _complete_finished_scan_task(self) -> None:
    if self._scan_task is not None and self._scan_task.done():
      self._complete_scan_task(self._scan_task)

  def _complete_scan_task(self, task: asyncio.Future[RackScanResult]) -> None:
    if task is not self._scan_task:
      return

    self._scan_task = None
    try:
      self._last_result = task.result()
    except asyncio.CancelledError:
      self._scan_error = MicronicError("Micronic rack scan was cancelled.")
      self._state = RackReaderState.IDLE
    except Exception as exc:
      self._scan_error = exc
      self._state = RackReaderState.IDLE
    else:
      self._scan_error = None
      self._state = RackReaderState.DATAREADY

  async def get_layouts(self) -> list[LayoutInfo]:
    return [LayoutInfo(name="8x12")]

  async def get_current_layout(self) -> str:
    return "8x12"

  async def set_current_layout(self, layout: str) -> None:
    normalized = layout.strip().lower().replace(" ", "")
    if normalized not in {"8x12", "96(8x12)", "96"}:
      raise MicronicError(f"Unsupported Micronic rack layout: {layout}")

  def _scan_rack_blocking(self) -> RackScanResult:
    self.image_dir.mkdir(parents=True, exist_ok=True)
    image_extension = choose_image_extension(
      image_extension=self.image_extension,
      image_input=self.image_input,
      scanner_backend=self.scanner_backend,
      scan_command=self.scan_command,
      twain_scanner_path=self.twain_scanner_path,
      sane_device=self.sane_device,
    )
    image_path = (
      self.image_dir / f"micronic_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.{image_extension}"
    )

    self.last_scan_metadata = run_scan(
      twain_scanner_path=self.twain_scanner_path,
      twain_source=self.twain_source,
      sane_device=self.sane_device,
      scanner_backend=self.scanner_backend,
      scan_command=self.scan_command,
      output_path=image_path,
      timeout_ms=self.scanner_timeout_ms,
      image_input=self.image_input,
    )
    self.last_image_path = image_path

    rack_id = read_rack_id(
      serial_port=self.serial_port,
      timeout_ms=self.serial_timeout_ms,
      rack_id_override=self.rack_id_override,
      rack_id_command=self.rack_id_command,
    )
    decoded, self.last_decode_metadata = decode_image(image_path)
    if len(decoded) < self.min_wells:
      missing = ", ".join(position for position in iter_positions() if position not in decoded)
      raise MicronicError(
        f"Micronic decode found {len(decoded)} wells; expected at least {self.min_wells}. "
        f"Missing: {missing}"
      )

    now = datetime.now()
    date_text = now.strftime("%Y%m%d")
    time_text = now.strftime("%H%M%S")
    entries = [
      RackScanEntry(
        position=position,
        tube_id=decoded[position].tube_id if position in decoded else None,
        status="OK" if position in decoded else "NOREAD",
        free_text=decoded[position].method if position in decoded else "",
      )
      for position in iter_positions()
    ]

    if not self.keep_images and self.image_input is None:
      try:
        image_path.unlink()
        self.last_image_path = None
      except OSError:
        pass

    return RackScanResult(
      rack_id=rack_id,
      date=date_text,
      time=time_text,
      entries=entries,
    )


def run_scan(
  output_path: Path,
  timeout_ms: int,
  twain_scanner_path: Optional[str] = None,
  twain_source: str = "AVA6PlusG",
  sane_device: Optional[str] = None,
  scanner_backend: str = "auto",
  scan_command: Optional[Sequence[str]] = None,
  image_input: Optional[str] = None,
) -> dict[str, object]:
  if image_input:
    source_path = Path(image_input)
    if not source_path.exists():
      raise MicronicError(f"Image input does not exist: {source_path}")
    output_path.write_bytes(source_path.read_bytes())
    return {"stdout": "", "stderr": "", "source": str(source_path)}

  if scan_command is not None:
    command = format_command(
      scan_command,
      output_path=output_path,
      timeout_ms=timeout_ms,
      twain_source=twain_source,
      sane_device=sane_device or "",
    )
    return run_scan_command(command, output_path, timeout_ms, source="command")

  backend = normalize_scanner_backend(scanner_backend)
  if backend == "command":
    raise MicronicError("Command scan requested, but scan_command was not configured.")

  if backend in {"auto", "twain"}:
    resolved_twain_path = resolve_twain_scanner_path(twain_scanner_path)
    if resolved_twain_path is not None:
      return run_scan_command(
        [resolved_twain_path, str(output_path), twain_source, str(timeout_ms)],
        output_path,
        timeout_ms,
        source="twain",
      )
    if backend == "twain":
      raise MicronicError(
        "TWAIN scan requested, but no TWAIN helper was configured. Set "
        "twain_scanner_path, MICRONIC_TWAIN_SCANNER_PATH, or put twain_scan on PATH."
      )

  if backend in {"auto", "sane"}:
    scanimage_path = shutil.which("scanimage")
    if scanimage_path is not None:
      command = [scanimage_path]
      if sane_device:
        command.extend(["--device-name", sane_device])
      command.extend(["--format=tiff", "--output-file", str(output_path)])
      return run_scan_command(command, output_path, timeout_ms, source="sane")
    if backend == "sane":
      raise MicronicError("SANE scan requested, but scanimage was not found on PATH.")

  raise MicronicError(
    "No scan acquisition method is available. Configure scan_command, "
    "twain_scanner_path/MICRONIC_TWAIN_SCANNER_PATH, or install SANE scanimage."
  )


def run_scan_command(
  command: Sequence[str],
  output_path: Path,
  timeout_ms: int,
  source: str,
) -> dict[str, object]:
  try:
    completed = subprocess.run(  # nosec B603 - operator-configured command, shell=False.
      list(command),
      check=False,
      capture_output=True,
      text=True,
      timeout=(timeout_ms / 1000) + 15,
    )
  except FileNotFoundError as exc:
    raise MicronicError(f"Scan command was not found: {command[0]}") from exc

  if completed.returncode != 0:
    raise MicronicError(
      "Scan command failed with exit code "
      f"{completed.returncode}: {completed.stderr.strip() or completed.stdout.strip()}"
    )
  if not output_path.exists():
    raise MicronicError(f"Scan command did not create image: {output_path}")
  return {
    "stdout": completed.stdout.strip(),
    "stderr": completed.stderr.strip(),
    "source": source,
    "command": list(command),
  }


def read_rack_id(
  serial_port: str = "COM4",
  timeout_ms: int = 2500,
  rack_id_override: Optional[str] = None,
  rack_id_command: Optional[Sequence[str]] = None,
) -> str:
  if rack_id_override:
    return rack_id_override

  if rack_id_command is not None:
    command = format_command(
      rack_id_command,
      serial_port=serial_port,
      timeout_ms=timeout_ms,
    )
    return read_rack_id_command(command, timeout_ms)

  return asyncio.run(read_rack_id_plr_serial(serial_port=serial_port, timeout_ms=timeout_ms))


async def read_rack_id_plr_serial(serial_port: str, timeout_ms: int) -> str:
  deadline = time.monotonic() + timeout_ms / 1000
  chunks: list[bytes] = []
  io = Serial(
    human_readable_device_name="Micronic rack ID reader",
    port=serial_port,
    baudrate=9600,
    bytesize=7,
    parity="E",
    stopbits=1,
    timeout=0.1,
    write_timeout=1.0,
  )
  try:
    await io.setup()
    await io.reset_input_buffer()
    await io.write(b"<t>\r\n")
    while time.monotonic() < deadline:
      value = await io.read(1)
      if value:
        chunks.append(value)
        if value in {b"\r", b"\n"}:
          break
  except Exception as exc:
    raise MicronicError(
      "Rack ID serial read failed. Install the PLR serial extra with "
      "`pip install pylabrobot[serial]` and verify the serial port: "
      f"{exc}"
    ) from exc
  finally:
    await io.stop()

  return extract_rack_id(b"".join(chunks).decode("utf-8", errors="ignore"))


def read_rack_id_command(command: Sequence[str], timeout_ms: int) -> str:
  try:
    completed = subprocess.run(  # nosec B603 - operator-configured command, shell=False.
      list(command),
      check=False,
      capture_output=True,
      text=True,
      timeout=(timeout_ms / 1000) + 5,
    )
  except FileNotFoundError as exc:
    raise MicronicError(f"Rack ID command was not found: {command[0]}") from exc

  if completed.returncode != 0:
    raise MicronicError(
      "Rack ID command failed with exit code "
      f"{completed.returncode}: {completed.stderr.strip() or completed.stdout.strip()}"
    )
  return extract_rack_id(completed.stdout)


def extract_rack_id(text: str) -> str:
  match = re.search(r"\d{6,}", text)
  return match.group(0) if match else "NOREAD"


def normalize_scanner_backend(scanner_backend: str) -> str:
  backend = scanner_backend.strip().lower()
  if backend not in {"auto", "twain", "sane", "command"}:
    raise MicronicError(
      "Unsupported scanner backend "
      f"{scanner_backend!r}; expected 'auto', 'twain', 'sane', or 'command'."
    )
  return backend


def normalize_image_extension(image_extension: str) -> str:
  normalized = image_extension.strip().lstrip(".")
  if not normalized:
    raise MicronicError("image_extension must not be empty.")
  return normalized


def choose_image_extension(
  image_extension: Optional[str],
  image_input: Optional[str],
  scanner_backend: str,
  scan_command: Optional[Sequence[str]],
  twain_scanner_path: Optional[str],
  sane_device: Optional[str],
) -> str:
  if image_extension:
    return normalize_image_extension(image_extension)
  if image_input and Path(image_input).suffix:
    return normalize_image_extension(Path(image_input).suffix)

  backend = normalize_scanner_backend(scanner_backend)
  if backend == "sane" or (
    backend == "auto"
    and scan_command is None
    and twain_scanner_path is None
    and (sane_device is not None or os.name != "nt")
  ):
    return "tiff"
  return "bmp"


def resolve_twain_scanner_path(twain_scanner_path: Optional[str]) -> Optional[str]:
  if twain_scanner_path:
    return twain_scanner_path

  env_path = os.environ.get("MICRONIC_TWAIN_SCANNER_PATH")
  if env_path:
    return env_path

  return shutil.which("twain_scan.exe") or shutil.which("twain_scan")


def format_command(command: Sequence[str], **values: object) -> list[str]:
  return [part.format(**values) for part in command]


def decode_image(image_path: Path) -> tuple[dict[str, DecodeResult], dict[str, object]]:
  cv2, np, zxingcpp, Image, ImageOps = import_decode_dependencies()
  with Image.open(image_path) as loaded_image:
    image = loaded_image.convert("L")
  full_results = zxingcpp.read_barcodes(
    image,
    formats=zxingcpp.BarcodeFormat.DataMatrix,
    try_rotate=True,
    try_downscale=True,
    try_invert=True,
  )

  detected: list[tuple[float, float, str]] = []
  for result in full_results:
    if not is_tube_id(result.text):
      continue
    corners = [
      result.position.top_left,
      result.position.top_right,
      result.position.bottom_right,
      result.position.bottom_left,
    ]
    detected.append(
      (
        sum(corner.x for corner in corners) / 4,
        sum(corner.y for corner in corners) / 4,
        result.text,
      )
    )

  if len(detected) < 24:
    raise MicronicError(f"Only {len(detected)} DataMatrix codes were found in the full image.")

  xs = fitted_axis(cluster_axis([item[0] for item in detected], RACK_ROWS, 90), RACK_ROWS)
  ys = fitted_axis(cluster_axis([item[1] for item in detected], RACK_COLS, 90), RACK_COLS)
  x_pitch = abs(xs[-1] - xs[0]) / (RACK_ROWS - 1)
  y_pitch = abs(ys[-1] - ys[0]) / (RACK_COLS - 1)

  decoded: dict[str, DecodeResult] = {}
  for x, y, tube_id in detected:
    scan_col = min(range(RACK_ROWS), key=lambda index: abs(xs[index] - x))
    scan_row = min(range(RACK_COLS), key=lambda index: abs(ys[index] - y))
    if abs(xs[scan_col] - x) > x_pitch * 0.45 or abs(ys[scan_row] - y) > y_pitch * 0.45:
      continue
    decoded[rack_position(scan_row, scan_col)] = DecodeResult(tube_id=tube_id, method="full-image")

  for scan_row in range(RACK_COLS):
    for scan_col in range(RACK_ROWS):
      position = rack_position(scan_row, scan_col)
      if position in decoded:
        continue
      crop_result = decode_well_crop(
        image,
        xs[scan_col],
        ys[scan_row],
        cv2,
        np,
        zxingcpp,
        Image,
        ImageOps,
      )
      if crop_result:
        decoded[position] = crop_result

  duplicate_ids = find_duplicate_ids(decoded)
  if duplicate_ids:
    raise MicronicError(
      f"Duplicate tube IDs decoded from more than one well: {', '.join(duplicate_ids)}"
    )

  metadata = {
    "imageSize": image.size,
    "fullImageDecoded": len(detected),
    "gridX": [round(value, 1) for value in xs],
    "gridY": [round(value, 1) for value in ys],
    "decodedWells": len(decoded),
    "missing": [position for position in iter_positions() if position not in decoded],
  }
  return decoded, metadata


def import_decode_dependencies():
  try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    import zxingcpp  # type: ignore
    from PIL import Image, ImageOps  # type: ignore
  except ImportError as exc:
    raise MicronicError(
      "Micronic decode dependencies are missing. Install pillow, "
      "opencv-python-headless, numpy, and zxing-cpp."
    ) from exc
  return cv2, np, zxingcpp, Image, ImageOps


def cluster_axis(values: list[float], expected_count: int, tolerance: float) -> list[float]:
  if not values:
    raise MicronicError("No decoded barcode positions are available for grid calibration.")

  clusters: list[list[float]] = []
  for value in sorted(values):
    if not clusters:
      clusters.append([value])
      continue
    mean = sum(clusters[-1]) / len(clusters[-1])
    if abs(value - mean) > tolerance:
      clusters.append([value])
    else:
      clusters[-1].append(value)

  means = [sum(cluster) / len(cluster) for cluster in clusters]
  if len(means) == expected_count:
    return means
  if len(means) >= 2:
    return [
      means[0] + index * (means[-1] - means[0]) / (expected_count - 1)
      for index in range(expected_count)
    ]
  raise MicronicError(
    f"Could not fit {expected_count} grid clusters from {len(values)} decoded positions."
  )


def fitted_axis(means: list[float], expected_count: int) -> list[float]:
  return [
    means[0] + index * (means[-1] - means[0]) / (expected_count - 1)
    for index in range(expected_count)
  ]


def rack_position(scan_row: int, scan_col: int) -> str:
  return f"{ROWS[RACK_ROWS - 1 - scan_col]}{RACK_COLS - scan_row:02d}"


def iter_positions() -> Iterable[str]:
  for row in ROWS:
    for column in range(1, COLS + 1):
      yield f"{row}{column:02d}"


def is_tube_id(value: object) -> bool:
  return isinstance(value, str) and value.isdigit() and len(value) == 10


def decode_well_crop(
  image, center_x, center_y, cv2, np, zxingcpp, Image, ImageOps
) -> Optional[DecodeResult]:
  for size in [150, 160, 180, 200, 220, 240]:
    crop = centered_crop(image, center_x, center_y, size)
    decoded = decode_pil_variants(crop, zxingcpp, ImageOps)
    if decoded:
      return DecodeResult(tube_id=decoded, method=f"crop-{size}")

  for size in [100, 120, 140, 160]:
    crop = centered_crop(image, center_x, center_y, size)
    decoded = decode_perspective_crop(crop, cv2, np, zxingcpp, Image, ImageOps)
    if decoded:
      return DecodeResult(tube_id=decoded, method=f"perspective-{size}")

  return None


def centered_crop(image, center_x: float, center_y: float, size: int):
  half = size / 2
  return image.crop(
    (
      int(round(center_x - half)),
      int(round(center_y - half)),
      int(round(center_x + half)),
      int(round(center_y + half)),
    )
  )


def decode_pil_variants(crop, zxingcpp, ImageOps) -> Optional[str]:
  for variant in [crop, ImageOps.autocontrast(crop), ImageOps.equalize(crop)]:
    decoded = decode_with_zxing(variant, zxingcpp, ImageOps)
    if decoded:
      return decoded
  return None


def decode_with_zxing(image, zxingcpp, ImageOps) -> Optional[str]:
  binarizers = [
    zxingcpp.Binarizer.LocalAverage,
    zxingcpp.Binarizer.GlobalHistogram,
    zxingcpp.Binarizer.FixedThreshold,
  ]
  for scale in [1, 2, 3, 4]:
    scaled = image if scale == 1 else image.resize((image.width * scale, image.height * scale))
    for invert in [False, True]:
      candidate = ImageOps.invert(scaled) if invert else scaled
      for border in [0, 20, 50]:
        padded = ImageOps.expand(candidate, border=border, fill=255) if border else candidate
        for binarizer in binarizers:
          for pure in [False, True]:
            results = zxingcpp.read_barcodes(
              padded,
              formats=zxingcpp.BarcodeFormat.DataMatrix,
              try_rotate=True,
              try_downscale=False,
              try_invert=True,
              binarizer=binarizer,
              is_pure=pure,
            )
            for result in results:
              if is_tube_id(result.text):
                return str(result.text)
  return None


def order_box(points, np):
  points = np.array(points, dtype=np.float32)
  sums = points.sum(axis=1)
  diffs = np.diff(points, axis=1).ravel()
  return np.array(
    [
      points[np.argmin(sums)],
      points[np.argmin(diffs)],
      points[np.argmax(sums)],
      points[np.argmax(diffs)],
    ],
    dtype=np.float32,
  )


def decode_perspective_crop(crop, cv2, np, zxingcpp, Image, ImageOps) -> Optional[str]:
  crop_array = np.array(crop)
  for threshold in [30, 40, 50, 60, 70, 80, 90, 100, 120, 140]:
    mask = (crop_array < threshold).astype(np.uint8) * 255
    for candidate_mask in candidate_masks(mask, cv2, np):
      if not candidate_mask.any():
        continue
      points = np.column_stack(np.where(candidate_mask > 0))[:, ::-1].astype(np.float32)
      if len(points) < 40:
        continue
      rect = cv2.minAreaRect(points)
      (rect_x, rect_y), (rect_w, rect_h), _angle = rect
      if rect_w < 25 or rect_h < 25 or rect_w > crop.width * 0.9 or rect_h > crop.height * 0.9:
        continue
      if max(rect_w, rect_h) / max(1, min(rect_w, rect_h)) > 2:
        continue

      box = cv2.boxPoints(rect)
      center = np.array([rect_x, rect_y], dtype=np.float32)
      for margin in [0.9, 1.0, 1.1, 1.2, 1.35]:
        source = order_box((box - center) * margin + center, np)
        for output_size in [60, 80, 100, 120, 160]:
          destination = np.array(
            [
              [0, 0],
              [output_size - 1, 0],
              [output_size - 1, output_size - 1],
              [0, output_size - 1],
            ],
            dtype=np.float32,
          )
          matrix = cv2.getPerspectiveTransform(source, destination)
          warped = cv2.warpPerspective(
            crop_array, matrix, (output_size, output_size), borderValue=255
          )
          for mode_array in perspective_variants(warped, threshold, cv2, Image, ImageOps):
            decoded = decode_with_zxing(mode_array, zxingcpp, ImageOps)
            if decoded:
              return decoded
  return None


def candidate_masks(mask, cv2, np):
  yield mask
  number, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
  combined = np.zeros_like(mask)
  size = mask.shape[0]
  for index in range(1, number):
    _x, _y, width, height, area = stats[index]
    center_x, center_y = centroids[index]
    if area < 15 or width < 8 or height < 8:
      continue
    if abs(center_x - size / 2) > size * 0.33 or abs(center_y - size / 2) > size * 0.33:
      continue
    if width > size * 0.85 or height > size * 0.85:
      continue
    combined[labels == index] = 255
  yield combined


def perspective_variants(warped, threshold: int, cv2, Image, ImageOps):
  yield Image.fromarray(warped)
  yield ImageOps.autocontrast(Image.fromarray(warped))
  _, binary = cv2.threshold(warped, min(220, threshold + 70), 255, cv2.THRESH_BINARY)
  yield Image.fromarray(binary)
  yield Image.fromarray(255 - binary)


def find_duplicate_ids(decoded: dict[str, DecodeResult]) -> list[str]:
  seen: dict[str, str] = {}
  duplicates: list[str] = []
  for position, result in decoded.items():
    previous = seen.get(result.tube_id)
    if previous and previous != position:
      duplicates.append(result.tube_id)
    seen[result.tube_id] = position
  return sorted(set(duplicates))
