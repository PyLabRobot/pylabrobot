"""Direct integration for the Micronic RD235 rack scanner.

This driver does not call Micronic Code Reader or IO Monitor. It owns the local
scanner path directly:

- acquire a rack image through a caller-supplied :class:`Scanner`,
- read barcodes through the side serial barcode reader, and
- decode tube barcodes and return position-indexed rack results.
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal, Optional

from pylabrobot.io.serial import Serial
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.tube_rack import TubeRack

from .errors import MicronicError
from .scanner import Scanner

logger = logging.getLogger(__name__)

ROWS = "ABCDEFGH"
COLS = 12
RACK_ROWS = 8
RACK_COLS = 12


@dataclass(frozen=True)
class DecodeResult:
  """A tube barcode decoded from a rack image.

  Attributes:
    tube_id: Ten-digit tube identifier.
    method: Image-decoding strategy that produced the result.
  """

  tube_id: str
  method: str


@dataclass
class RackScanEntry:
  """One decoded rack position.

  Attributes:
    position: Rack position such as ``"A1"``.
    tube_id: Decoded tube identifier, or ``None`` when no code was read.
    status: ``"OK"`` when a tube code was read, otherwise ``"NOREAD"``.
    barcode: Structured representation of the decoded tube barcode.
  """

  position: str
  tube_id: Optional[str]
  status: Literal["OK", "NOREAD"]
  barcode: Optional[Barcode] = None


@dataclass
class RackScanResult:
  """The rack identifier and position-indexed tube scan results.

  Attributes:
    rack_id: Side barcode value, or ``"NOREAD"`` when no rack code was read.
    entries: Results for all 96 positions in row-major order.
    rack_barcode: Structured representation of the rack barcode.
  """

  rack_id: str
  entries: list[RackScanEntry]
  rack_barcode: Optional[Barcode] = None


class MicronicRD235:
  """Control a Micronic RD235 rack scanner without the OEM application.

  Args:
    scanner: Image acquisition implementation for the flatbed scanner.
    serial_port: Port for the side rack-barcode reader.
    image_dir: Directory for temporary or retained rack images.
    scanner_timeout: Image acquisition timeout in seconds.
    serial_timeout: Side barcode read timeout in seconds.
    keep_images: Preserve acquired images after decoding when ``True``.
  """

  def __init__(
    self,
    scanner: Scanner,
    serial_port: str,
    image_dir: Optional[str] = None,
    scanner_timeout: float = 90.0,
    serial_timeout: float = 2.5,
    keep_images: bool = False,
  ) -> None:
    """Initialize the code reader and its serial transport.

    Raises:
      ValueError: If either device timeout is not positive.
    """
    if scanner_timeout <= 0:
      raise ValueError("scanner_timeout must be positive")
    if serial_timeout <= 0:
      raise ValueError("serial_timeout must be positive")
    self.scanner = scanner
    self.image_dir = (
      Path(image_dir) if image_dir else Path(tempfile.gettempdir()) / "pylabrobot-micronic"
    )
    self.scanner_timeout = scanner_timeout
    self.serial_timeout = serial_timeout
    self.keep_images = keep_images
    self.io = Serial(
      human_readable_device_name="Micronic rack ID reader",
      port=serial_port,
      baudrate=9600,
      bytesize=7,
      parity="E",
      stopbits=1,
      timeout=0.1,
      write_timeout=1.0,
    )
    self.last_image_path: Optional[Path] = None
    self.last_scan_metadata: dict[str, object] = {}
    self.last_decode_metadata: dict[str, object] = {}
    self._scan_lock = asyncio.Lock()

  async def setup(self) -> None:
    """Create the image directory and connect to the side barcode reader."""
    self.image_dir.mkdir(parents=True, exist_ok=True)
    await self.scanner.setup()
    try:
      await self.io.setup()
    except BaseException:
      try:
        await self.scanner.stop()
      except Exception:
        logger.warning("Failed to stop Micronic scanner after setup failure", exc_info=True)
      raise
    logger.info("Set up Micronic code reader")

  async def stop(self) -> None:
    """Disconnect from the side barcode reader and image scanner."""
    try:
      await self.io.stop()
    finally:
      await self.scanner.stop()
    logger.info("Stopped Micronic code reader")

  async def _read_barcode(self) -> str:
    """Trigger and parse one side rack-barcode read.

    Returns:
      The first sequence of at least six digits, or ``"NOREAD"``.

    Raises:
      MicronicError: If serial communication fails.
    """
    deadline = time.monotonic() + self.serial_timeout
    chunks: list[bytes] = []
    try:
      await self.io.reset_input_buffer()
      await self.io.write(b"<t>\r\n")
      while time.monotonic() < deadline:
        value = await self.io.read(1)
        if value:
          chunks.append(value)
          if value in {b"\r", b"\n"}:
            break
    except Exception as exc:
      logger.exception("Micronic rack ID serial read failed")
      raise MicronicError(
        "Rack ID serial read failed. Install the PLR serial extra with "
        "`pip install pylabrobot[serial]` and verify the serial port: "
        f"{exc}"
      ) from exc
    text = b"".join(chunks).decode("utf-8", errors="ignore")
    match = re.search(r"\d{6,}", text)
    return match.group(0) if match else "NOREAD"

  async def _acquire_image(self) -> Path:
    """Acquire one rack image and retain its scanner metadata."""
    self.image_dir.mkdir(parents=True, exist_ok=True)
    image_path = (
      self.image_dir
      / f"micronic_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.{self.scanner.image_extension}"
    )
    try:
      self.last_scan_metadata = await self.scanner.acquire(image_path, self.scanner_timeout)
    except asyncio.CancelledError:
      self._release_image(image_path)
      raise
    except Exception:
      self._release_image(image_path)
      raise
    self.last_image_path = image_path
    return image_path

  def _release_image(self, image_path: Path) -> None:
    """Delete an acquired image unless image retention is enabled."""
    if not self.keep_images:
      try:
        image_path.unlink()
        self.last_image_path = None
      except FileNotFoundError:
        self.last_image_path = None
      except OSError:
        logger.warning("Could not remove Micronic rack image %s", image_path, exc_info=True)

  @staticmethod
  def _validate_rack(rack: TubeRack) -> None:
    """Reject rack resources that do not represent an 8x12 layout."""
    if rack.num_items_x != RACK_COLS or rack.num_items_y != RACK_ROWS:
      raise MicronicError(
        f"Micronic code reader only supports {RACK_ROWS}x{RACK_COLS} racks; "
        f"got {rack.num_items_y}x{rack.num_items_x}."
      )

  async def scan_rack(self, rack: TubeRack, timeout: float = 90.0) -> RackScanResult:
    """Scan the rack ID and all tube positions in an 8x12 rack.

    Args:
      rack: Rack resource whose 96 positions correspond to the scanner layout.
      timeout: Overall operation timeout in seconds.

    Returns:
      Rack and tube barcode results.

    Raises:
      MicronicError: If the rack shape is unsupported, a scan is already running, image
        acquisition fails, or decoding fails.
      asyncio.TimeoutError: If the operation exceeds ``timeout``.
    """
    logger.info("Starting Micronic rack scan")
    try:
      result = await asyncio.wait_for(self._scan_rack(rack), timeout=timeout)
    except Exception:
      logger.exception("Micronic rack scan failed")
      raise
    logger.info(
      "Completed Micronic rack scan: rack_id=%s decoded=%d",
      result.rack_id,
      sum(entry.status == "OK" for entry in result.entries),
    )
    return result

  async def scan_rack_id(self, timeout: float = 5.0) -> str:
    """Read the side rack barcode.

    Args:
      timeout: Overall operation timeout in seconds.

    Returns:
      The decoded rack identifier, or ``"NOREAD"``.

    Raises:
      MicronicError: If serial communication fails.
      asyncio.TimeoutError: If the operation exceeds ``timeout``.
    """
    try:
      rack_id = await asyncio.wait_for(self._read_barcode(), timeout=timeout)
    except Exception:
      logger.exception("Micronic rack ID scan failed")
      raise
    logger.info("Completed Micronic rack ID scan: rack_id=%s", rack_id)
    return rack_id

  async def _scan_rack(self, rack: TubeRack) -> RackScanResult:
    """Coordinate one cancellation-safe rack scan."""
    self._validate_rack(rack)
    if self._scan_lock.locked():
      raise MicronicError("Micronic rack scan is already in progress.")
    await self._scan_lock.acquire()
    release_lock = True
    image_path: Optional[Path] = None
    try:
      rack_id = await self._read_barcode()
      image_path = await self._acquire_image()
      loop = asyncio.get_running_loop()
      scan_future = loop.run_in_executor(
        None,
        self._decode_rack_image,
        image_path,
        rack_id,
      )
      try:
        return await asyncio.shield(scan_future)
      except asyncio.CancelledError:
        release_lock = False
        scan_future.add_done_callback(partial(self._finish_cancelled_scan, image_path=image_path))
        image_path = None
        raise
    finally:
      if image_path is not None:
        self._release_image(image_path)
      if release_lock:
        self._release_scan_lock()

  def _finish_cancelled_scan(
    self,
    future: asyncio.Future[RackScanResult],
    *,
    image_path: Path,
  ) -> None:
    """Release scan resources after a cancelled caller's decode finishes."""
    try:
      exception = future.exception()
    except asyncio.CancelledError:
      exception = None
    if exception is not None:
      logger.error("Cancelled Micronic rack scan later failed: %s", exception)
    self._release_image(image_path)
    self._release_scan_lock()

  def _release_scan_lock(self) -> None:
    """Release the scan lock if it is currently held."""
    if self._scan_lock.locked():
      self._scan_lock.release()

  def _decode_rack_image(
    self,
    image_path: Path,
    rack_id: str,
  ) -> RackScanResult:
    """Decode an acquired rack image in a worker thread."""
    decoded, self.last_decode_metadata = decode_image(image_path)

    for position, result in decoded.items():
      logger.debug("Micronic decoded %s via %s", position, result.method)

    entries = [
      RackScanEntry(
        position=position,
        tube_id=decoded[position].tube_id if position in decoded else None,
        status="OK" if position in decoded else "NOREAD",
        barcode=(
          Barcode(
            data=decoded[position].tube_id,
            symbology="DataMatrix",
            position_on_resource="bottom",
          )
          if position in decoded
          else None
        ),
      )
      for position in iter_positions()
    ]

    return RackScanResult(
      rack_id=rack_id,
      entries=entries,
      rack_barcode=Barcode(
        data=rack_id,
        symbology="Code 128 (Subset B and C)",
        position_on_resource="right",
      )
      if rack_id != "NOREAD"
      else None,
    )


def decode_image(image_path: Path) -> tuple[dict[str, DecodeResult], dict[str, object]]:
  """Decode all tube barcodes in a Micronic rack image.

  Args:
    image_path: Path to the acquired rack image.

  Returns:
    Position-indexed decode results and diagnostic metadata.

  Raises:
    MicronicError: If dependencies are missing, the grid cannot be calibrated, or duplicate tube
      identifiers are found.
  """
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


def import_decode_dependencies() -> tuple[Any, Any, Any, Any, Any]:
  """Import optional image-decoding dependencies on demand.

  Returns:
    The OpenCV, NumPy, zxing-cpp, Pillow Image, and Pillow ImageOps modules.

  Raises:
    MicronicError: If any decoding dependency is unavailable.
  """
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
  """Cluster detected coordinates along one scanner axis.

  Args:
    values: Detected barcode coordinates.
    expected_count: Number of rack rows or columns expected on the axis.
    tolerance: Maximum gap within one cluster, in image pixels.

  Returns:
    Cluster centers, interpolated to ``expected_count`` when necessary.

  Raises:
    MicronicError: If fewer than two usable clusters can be found.
  """
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
    return fitted_axis(means, expected_count)
  raise MicronicError(
    f"Could not fit {expected_count} grid clusters from {len(values)} decoded positions."
  )


def fitted_axis(means: list[float], expected_count: int) -> list[float]:
  """Fit evenly spaced coordinates between the first and last cluster centers."""
  return [
    means[0] + index * (means[-1] - means[0]) / (expected_count - 1)
    for index in range(expected_count)
  ]


def rack_position(scan_row: int, scan_col: int) -> str:
  """Map scanner-oriented grid coordinates to a rack position name."""
  return f"{ROWS[RACK_ROWS - 1 - scan_col]}{RACK_COLS - scan_row}"


def iter_positions() -> Iterable[str]:
  """Yield every 8x12 rack position in row-major order."""
  for row in ROWS:
    for column in range(1, COLS + 1):
      yield f"{row}{column}"


def is_tube_id(value: object) -> bool:
  """Return whether a decoded value is a ten-digit Micronic tube identifier."""
  return isinstance(value, str) and value.isdigit() and len(value) == 10


def decode_well_crop(
  image: Any,
  center_x: float,
  center_y: float,
  cv2: Any,
  np: Any,
  zxingcpp: Any,
  Image: Any,
  ImageOps: Any,
) -> Optional[DecodeResult]:
  """Decode one well using progressively larger direct and perspective-corrected crops."""
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


def centered_crop(image: Any, center_x: float, center_y: float, size: int) -> Any:
  """Return a square image crop centered on scanner coordinates."""
  half = size / 2
  return image.crop(
    (
      int(round(center_x - half)),
      int(round(center_y - half)),
      int(round(center_x + half)),
      int(round(center_y + half)),
    )
  )


def decode_pil_variants(crop: Any, zxingcpp: Any, ImageOps: Any) -> Optional[str]:
  """Try original, autocontrasted, and equalized variants of an image crop."""
  for variant in [crop, ImageOps.autocontrast(crop), ImageOps.equalize(crop)]:
    decoded = decode_with_zxing(variant, zxingcpp, ImageOps)
    if decoded:
      return decoded
  return None


def decode_with_zxing(image: Any, zxingcpp: Any, ImageOps: Any) -> Optional[str]:
  """Search image scales, polarity, borders, and binarizers for a tube identifier."""
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


def order_box(points: Any, np: Any) -> Any:
  """Order four rectangle corners clockwise from the top-left corner."""
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


def decode_perspective_crop(
  crop: Any,
  cv2: Any,
  np: Any,
  zxingcpp: Any,
  Image: Any,
  ImageOps: Any,
) -> Optional[str]:
  """Locate, rectify, and decode a DataMatrix candidate within one well crop."""
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


def candidate_masks(mask: Any, cv2: Any, np: Any) -> Iterator[Any]:
  """Yield the raw threshold mask and its centered connected components."""
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


def perspective_variants(
  warped: Any,
  threshold: int,
  cv2: Any,
  Image: Any,
  ImageOps: Any,
) -> Iterator[Any]:
  """Yield grayscale and binary variants of a rectified DataMatrix candidate."""
  yield Image.fromarray(warped)
  yield ImageOps.autocontrast(Image.fromarray(warped))
  _, binary = cv2.threshold(warped, min(220, threshold + 70), 255, cv2.THRESH_BINARY)
  yield Image.fromarray(binary)
  yield Image.fromarray(255 - binary)


def find_duplicate_ids(decoded: dict[str, DecodeResult]) -> list[str]:
  """Return tube identifiers assigned to more than one rack position."""
  seen: dict[str, str] = {}
  duplicates: list[str] = []
  for position, result in decoded.items():
    previous = seen.get(result.tube_id)
    if previous and previous != position:
      duplicates.append(result.tube_id)
    seen[result.tube_id] = position
  return sorted(set(duplicates))
