"""Interpolation utilities for calibration and modeling."""

from typing import Dict, Literal

InterpolationBoundsHandling = Literal["error", "clip", "extrapolate"]
InterpolationMethod = Literal["linear"]  # future: "cubic", "spline"


def interpolate_1d(
  x: float,
  data: Dict[float, float],
  *,
  bounds_handling: InterpolationBoundsHandling = "error",
  method: InterpolationMethod = "linear",
) -> float:
  """Perform one-dimensional interpolation or extrapolation over calibration data.

  This function estimates a continuous value between (or beyond) known calibration
  points using one-dimensional interpolation. Currently, only linear interpolation
  is implemented; additional methods such as cubic or spline interpolation will be
  supported in the future.

  Args:
    x: The input value to interpolate.
    data: A dictionary mapping known x-values to corresponding y-values. The keys must form a monotonically increasing sequence.
    bounds_handling: Defines how to handle x-values outside the calibration range:
      - "error": Raise ValueError (strict physical bounds; default)
      - "clip":  Return the nearest boundary value
      - "extrapolate": Extend linearly using the first or last segment slope
    method: Interpolation method to use:
      - "linear": Piecewise linear interpolation (default)
      - "cubic": Cubic interpolation (not yet implemented)
      - "spline": Spline interpolation (not yet implemented)

  Returns:
    The interpolated or extrapolated y-value.

  Raises:
    ValueError: If the calibration data is empty or x is out of range and bounds_handling="error".
  """
  if len(data) == 0:
    raise ValueError("Interpolation data is empty.")

  if method != "linear":
    raise ValueError(f"Interpolation method '{method}' is not valid.")

  xs = sorted(data.keys())
  ys = [data[k] for k in xs]

  # --- Handle boundaries --------------------------------------------------- #
  if x < xs[0]:
    if bounds_handling == "error":
      raise ValueError(f"x={x} below range {xs[0]}–{xs[-1]}")
    if bounds_handling == "clip":
      return ys[0]
    if bounds_handling == "extrapolate":
      x0, x1 = xs[0], xs[1]
      y0, y1 = ys[0], ys[1]
      return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

  if x > xs[-1]:
    if bounds_handling == "error":
      raise ValueError(f"x={x} above range {xs[0]}–{xs[-1]}")
    if bounds_handling == "clip":
      return ys[-1]
    if bounds_handling == "extrapolate":
      x0, x1 = xs[-2], xs[-1]
      y0, y1 = ys[-2], ys[-1]
      return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

  # --- Exact match --------------------------------------------------------- #
  for i, xv in enumerate(xs):
    if x == xv:
      return ys[i]

  # --- Find enclosing interval and interpolate ----------------------------- #
  for i in range(1, len(xs)):
    if xs[i] >= x:
      x0, x1 = xs[i - 1], xs[i]
      y0, y1 = ys[i - 1], ys[i]
      return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

  # --- Fallback (should never occur) --------------------------------------- #
  raise RuntimeError("Interpolation failed due to an unexpected error.")
