"""Prep calibration: MLPrepCalibration commands and session workflows.

Firmware-path resolution is JIT: each ``PrepCommand`` subclass declares its own
``firmware_path``, and :meth:`PrepClient.send_command` resolves it via the
introspection registry (cache-hot after the first call).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import (
  TYPE_CHECKING,
  Awaitable,
  Callable,
  List,
  Literal,
  Optional,
  Tuple,
  TypeVar,
  Union,
)

from pylabrobot.resources.tip_rack import TipSpot

from . import prep_commands as PrepCmd

if TYPE_CHECKING:
  from .client import PrepClient
  from .info import PrepInstrumentInfo

logger = logging.getLogger(__name__)

_TCalibResult = TypeVar("_TCalibResult")

# Same mapping as Prep channels for TipPositionParameters / channel indices.
_CHANNEL_INDEX = {
  0: PrepCmd.ChannelIndex.RearChannel,
  1: PrepCmd.ChannelIndex.FrontChannel,
}


@dataclass(frozen=True)
class CalibrationCommandReport:
  """Structured report for one calibration command execution."""

  command: str
  result: object
  before: PrepCmd.CalibrationValues
  after: PrepCmd.CalibrationValues
  diff: PrepCmd.CalibrationValuesDiff

  @property
  def changed_fields_count(self) -> int:
    channel_changes = sum(
      len(cd.changes) for cd in self.diff.channel_diffs if cd.state == "changed"
    )
    return (
      len(self.diff.top_level_changes)
      + channel_changes
      + sum(1 for cd in self.diff.channel_diffs if cd.state in ("added", "removed"))
    )


class PrepCalibration:
  """Calibration façade: firmware MLPrepCalibration object + DeckConfiguration site defs."""

  def __init__(self, *, driver: "PrepClient", info: "PrepInstrumentInfo") -> None:
    self._driver = driver
    self._info = info
    self._calibration_session_active: bool = False

  @property
  def client(self) -> "PrepClient":
    """Alias for code that uses ``client.send_command`` (driver is the TCP client)."""
    return self._driver

  @property
  def num_channels(self) -> int:
    n = self._info.config.num_channels
    if n is None:
      raise RuntimeError("Instrument config has no num_channels (finish Prep.setup first).")
    return n

  @property
  def has_mph(self) -> bool:
    h = self._info.config.has_mph
    if h is None:
      raise RuntimeError("Instrument config has no has_mph (finish Prep.setup first).")
    return h

  def _set_calibration_session_active(self, active: bool) -> None:
    self._calibration_session_active = active

  def calibration_session(
    self,
    *,
    float_tol: float = 1e-6,
    report_after_command: bool = True,
    report_scope: Literal["related", "full"] = "related",
    session_read_timeout: Optional[float] = None,
  ) -> PrepCalibrationSession:
    """Create a managed calibration session bound to this façade."""
    return PrepCalibrationSession(
      self,
      float_tol=float_tol,
      report_after_command=report_after_command,
      report_scope=report_scope,
      session_read_timeout=session_read_timeout,
    )

  async def get_calibration_site_definitions(self) -> Tuple[PrepCmd.CalibrationSiteInfo, ...]:
    """Return calibration site definitions from DeckConfiguration (GetCalibrationSiteDefinitions, cmd=3)."""
    result = await self._driver.send_command(PrepCmd.PrepGetCalibrationSiteDefinitions())
    if result is None or not getattr(result, "sites", None):
      return ()
    return tuple(
      PrepCmd.CalibrationSiteInfo(
        id=int(s.id),
        left_bottom_front_x=float(s.left_bottom_front_x),
        left_bottom_front_y=float(s.left_bottom_front_y),
        left_bottom_front_z=float(s.left_bottom_front_z),
        length=float(s.length),
        width=float(s.width),
        height=float(s.height),
        post=bool(s.post),
      )
      for s in result.sites
    )

  async def begin_calibration(self) -> None:
    """Enter calibration mode (BeginCalibration, cmd=1)."""
    await self._driver.send_command(PrepCmd.PrepBeginCalibration())

  async def cancel_calibration(self) -> None:
    """Cancel an active calibration session (CancelCalibration, cmd=2)."""
    await self._driver.send_command(PrepCmd.PrepCancelCalibration())

  async def end_calibration(self, date_time: Optional[PrepCmd.HoiDateTime] = None) -> None:
    """End calibration and store results with timestamp (EndCalibration, cmd=3)."""
    if date_time is None:
      date_time = PrepCmd.HoiDateTime.now()
    await self._driver.send_command(PrepCmd.PrepEndCalibration(date_time=date_time))

  async def reset_calibration(self, store: bool = False) -> None:
    """Reset calibration data (ResetCalibration, cmd=4)."""
    await self._driver.send_command(PrepCmd.PrepResetCalibration(store=store))

  async def calibration_initialize(self) -> None:
    """Initialize calibration hardware (CalibrationInitialize, cmd=5)."""
    await self._driver.send_command(PrepCmd.PrepCalibrationInitialize())

  async def read_calibration_values(
    self, read_timeout: Optional[float] = None
  ) -> PrepCmd.CalibrationValues:
    """Read calibration values (GetCalibrationValues, cmd=16)."""
    result = await self._driver.send_command(
      PrepCmd.PrepGetCalibrationValues(),
      read_timeout=read_timeout,
    )
    if result is None:
      return PrepCmd.CalibrationValues(
        independent_offset_x=0.0,
        mph_offset_x=0.0,
        channel_values=(),
      )

    return PrepCmd.CalibrationValues(
      independent_offset_x=float(result.independent_offset_x),
      mph_offset_x=float(result.mph_offset_x),
      channel_values=tuple(
        PrepCmd.ChannelCalibrationValuesInfo(
          index=int(cv.index),
          y_offset=float(cv.y_offset),
          z_offset=float(cv.z_offset),
          squeeze_position=int(cv.squeeze_position),
          z_touchoff=int(cv.z_touchoff),
          pressure_shift=int(cv.pressure_shift),
          pressure_monitoring_shift=int(cv.pressure_monitoring_shift),
          dispenser_return_distance=float(cv.dispenser_return_distance),
          z_tip_height=float(cv.z_tip_height),
          core_ii=bool(cv.core_ii),
        )
        for cv in (result.channel_values or [])
      ),
    )


class PrepCalibrationSession:
  """Context manager for stateful Prep calibration workflows."""

  def __init__(
    self,
    cal: PrepCalibration,
    *,
    float_tol: float = 1e-6,
    report_after_command: bool = True,
    report_scope: Literal["related", "full"] = "related",
    session_read_timeout: Optional[float] = None,
  ) -> None:
    self._cal = cal
    self.float_tol = float_tol
    self.report_after_command = report_after_command
    self.report_scope = report_scope
    self.session_read_timeout = session_read_timeout

    self._started = False
    self._ended = False
    self._baseline: Optional[PrepCmd.CalibrationValues] = None
    self._last_snapshot: Optional[PrepCmd.CalibrationValues] = None
    self.history: List[CalibrationCommandReport] = []

    if report_scope not in ("related", "full"):
      raise ValueError(f"report_scope must be 'related' or 'full', got: {report_scope}")

  @property
  def baseline(self) -> PrepCmd.CalibrationValues:
    if self._baseline is None:
      raise RuntimeError("Session baseline unavailable. Enter the session first.")
    return self._baseline

  @property
  def last_snapshot(self) -> PrepCmd.CalibrationValues:
    if self._last_snapshot is None:
      raise RuntimeError("Session snapshot unavailable. Enter the session first.")
    return self._last_snapshot

  def _effective_timeout(self, read_timeout: Optional[float]) -> Optional[float]:
    return self.session_read_timeout if read_timeout is None else read_timeout

  def _ensure_started(self) -> None:
    if not self._started:
      raise RuntimeError("Calibration session is not started. Call `await session.start()` first.")
    if self._ended:
      raise RuntimeError("Calibration session is already ended.")

  def _select_snapshot_scope(
    self,
    values: PrepCmd.CalibrationValues,
    *,
    channel: Optional[PrepCmd.ChannelIndex] = None,
  ) -> PrepCmd.CalibrationValues:
    if self.report_scope == "full" or channel is None:
      return values
    channel_index = int(channel)
    return PrepCmd.CalibrationValues(
      independent_offset_x=values.independent_offset_x,
      mph_offset_x=values.mph_offset_x,
      channel_values=tuple(cv for cv in values.channel_values if cv.index == channel_index),
    )

  def _log_report(self, report: CalibrationCommandReport) -> None:
    if report.diff.has_changes:
      logger.info(
        "Calibration session %s changed %d field(s)",
        report.command,
        report.changed_fields_count,
      )
    else:
      logger.info("Calibration session %s produced no calibration changes", report.command)

    if logger.isEnabledFor(logging.DEBUG):
      logger.debug(
        "Calibration report diff for %s:\n%s",
        report.command,
        PrepCmd.format_calibration_diff(report.diff),
      )

  async def _get_calibration_values(
    self,
    *,
    read_timeout: Optional[float] = None,
  ) -> PrepCmd.CalibrationValues:
    return await self._cal.read_calibration_values(read_timeout=read_timeout)

  async def _run_with_report(
    self,
    command_name: str,
    op: Callable[[Optional[float]], Awaitable[_TCalibResult]],
    *,
    channel: Optional[PrepCmd.ChannelIndex] = None,
    read_timeout: Optional[float] = None,
  ) -> Union[_TCalibResult, CalibrationCommandReport]:
    timeout = self._effective_timeout(read_timeout)
    if not self.report_after_command:
      result = await op(timeout)
      self._last_snapshot = await self._get_calibration_values(read_timeout=timeout)
      return result

    before_full = await self._get_calibration_values(read_timeout=timeout)
    result = await op(timeout)
    after_full = await self._get_calibration_values(read_timeout=timeout)
    self._last_snapshot = after_full

    before = self._select_snapshot_scope(before_full, channel=channel)
    after = self._select_snapshot_scope(after_full, channel=channel)
    diff = PrepCmd.diff_calibration_values(before, after, float_tol=self.float_tol)
    report = CalibrationCommandReport(
      command=command_name,
      result=result,
      before=before,
      after=after,
      diff=diff,
    )
    self.history.append(report)
    self._log_report(report)
    return report

  async def __aenter__(self) -> PrepCalibrationSession:
    await self.start()
    return self

  async def start(self) -> PrepCalibrationSession:
    """Start calibration mode and capture baseline snapshot."""
    if self._started:
      return self
    if self._ended:
      raise RuntimeError("Calibration session is already ended; create a new session.")
    if self._cal._calibration_session_active:
      raise RuntimeError("A calibration session is already active on this PrepCalibration.")
    await self._cal.begin_calibration()
    await self._cal.calibration_initialize()
    self._cal._set_calibration_session_active(True)
    try:
      snapshot = await self._get_calibration_values(read_timeout=self.session_read_timeout)
    except Exception:
      self._cal._set_calibration_session_active(False)
      raise
    self._baseline = snapshot
    self._last_snapshot = snapshot
    self._started = True
    logger.info("Calibration session started")
    return self

  async def __aexit__(self, exc_type, exc, tb) -> bool:
    if self._ended:
      return False
    try:
      await self.end(save=False)
    except Exception:
      logger.exception("Failed to rollback calibration session")
      if exc is None:
        raise
    return False

  async def snapshot(self, *, read_timeout: Optional[float] = None) -> PrepCmd.CalibrationValues:
    self._ensure_started()
    snapshot = await self._get_calibration_values(
      read_timeout=self._effective_timeout(read_timeout)
    )
    self._last_snapshot = snapshot
    return snapshot

  async def diff_from_start(
    self,
    *,
    float_tol: Optional[float] = None,
    read_timeout: Optional[float] = None,
  ) -> PrepCmd.CalibrationValuesDiff:
    self._ensure_started()
    current = await self.snapshot(read_timeout=read_timeout)
    return PrepCmd.diff_calibration_values(
      self.baseline,
      current,
      float_tol=self.float_tol if float_tol is None else float_tol,
    )

  async def diff_from_last(
    self,
    *,
    float_tol: Optional[float] = None,
    read_timeout: Optional[float] = None,
  ) -> PrepCmd.CalibrationValuesDiff:
    self._ensure_started()
    previous = self.last_snapshot
    current = await self.snapshot(read_timeout=read_timeout)
    return PrepCmd.diff_calibration_values(
      previous,
      current,
      float_tol=self.float_tol if float_tol is None else float_tol,
    )

  async def end(
    self, *, save: bool = True, date_time: Optional[PrepCmd.HoiDateTime] = None
  ) -> None:
    """End the calibration session, optionally saving values."""
    if self._ended:
      return
    self._ensure_started()
    if save:
      await self._cal.end_calibration(date_time=date_time)
      logger.info("Calibration session ended and saved")
    else:
      await self._cal.cancel_calibration()
      logger.info("Calibration session ended without saving")
    self._ended = True
    self._started = False
    self._cal._set_calibration_session_active(False)

  async def rollback(self) -> None:
    """End the session without saving (alias for ``end(save=False)``)."""
    await self.end(save=False)

  async def commit(self) -> None:
    """Save calibration and end the session (alias for ``end(save=True)``)."""
    await self.end(save=True)

  async def reset(self, *, store: bool = False) -> None:
    """Reset calibration values during an active calibration session."""
    self._ensure_started()
    await self._cal.reset_calibration(store=store)
    self._last_snapshot = await self._get_calibration_values(read_timeout=self.session_read_timeout)

  async def calibrate_x_axis(
    self,
    *,
    site_index: int,
    channel: PrepCmd.ChannelIndex,
    read_timeout: Optional[float] = None,
  ) -> Union[float, CalibrationCommandReport]:
    self._ensure_started()

    async def _op(timeout: Optional[float]) -> float:
      result = await self._cal.client.send_command(
        PrepCmd.PrepCalibrateXAxis(
          site_index=site_index,
          channel=int(channel),
        ),
        read_timeout=timeout,
      )
      return float(result.offset)

    return await self._run_with_report(
      f"calibrate_x_axis(channel={channel.name}, site_index={site_index})",
      _op,
      channel=channel,
      read_timeout=read_timeout,
    )

  async def calibrate_y_axis(
    self,
    *,
    site_index: int,
    channel: PrepCmd.ChannelIndex,
    read_timeout: Optional[float] = None,
  ) -> Union[float, CalibrationCommandReport]:
    self._ensure_started()

    async def _op(timeout: Optional[float]) -> float:
      result = await self._cal.client.send_command(
        PrepCmd.PrepCalibrateYAxis(
          site_index=site_index,
          channel=int(channel),
        ),
        read_timeout=timeout,
      )
      return float(result.offset)

    return await self._run_with_report(
      f"calibrate_y_axis(channel={channel.name}, site_index={site_index})",
      _op,
      channel=channel,
      read_timeout=read_timeout,
    )

  async def calibrate_z_axis(
    self,
    *,
    site_index: int,
    channel: PrepCmd.ChannelIndex,
    read_timeout: Optional[float] = None,
  ) -> Union[float, CalibrationCommandReport]:
    self._ensure_started()

    async def _op(timeout: Optional[float]) -> float:
      result = await self._cal.client.send_command(
        PrepCmd.PrepCalibrateZAxis(
          site_index=site_index,
          channel=int(channel),
        ),
        read_timeout=timeout,
      )
      return float(result.offset)

    return await self._run_with_report(
      f"calibrate_z_axis(channel={channel.name}, site_index={site_index})",
      _op,
      channel=channel,
      read_timeout=read_timeout,
    )

  async def calibrate_squeeze_tips(
    self,
    tip_spots: List[TipSpot],
    *,
    use_channels: Optional[List[int]] = None,
    z_seek_offset: Optional[float] = None,
    read_timeout: Optional[float] = None,
  ) -> Union[Tuple[int, ...], CalibrationCommandReport]:
    self._ensure_started()

    async def _op(timeout: Optional[float]) -> Tuple[int, ...]:
      channels = use_channels if use_channels is not None else list(range(len(tip_spots)))
      assert len(tip_spots) == len(channels)

      indexed_spots = {ch: spot for ch, spot in zip(channels, tip_spots)}
      tip_positions: List[PrepCmd.TipPositionParameters] = []
      for ch in range(self._cal.num_channels):
        if ch not in indexed_spots:
          continue
        spot = indexed_spots[ch]
        loc = spot.get_absolute_location("c", "c", "t")
        tip_positions.append(
          PrepCmd.TipPositionParameters.for_op(
            _CHANNEL_INDEX[ch],
            loc,
            spot.get_tip(),
            z_seek_offset=z_seek_offset,
          )
        )

      result = await self._cal.client.send_command(
        PrepCmd.PrepCalibrateSqueezeTips(
          channels=tip_positions,
        ),
        read_timeout=timeout,
      )
      if result is None or not getattr(result, "positions", None):
        return ()
      return tuple(int(p) for p in result.positions)

    return await self._run_with_report(
      "calibrate_squeeze_tips",
      _op,
      read_timeout=read_timeout,
    )

  async def calibrate_squeeze_tips_mph(
    self,
    tip_spot: Union[TipSpot, List[TipSpot]],
    *,
    z_seek_offset: Optional[float] = None,
    read_timeout: Optional[float] = None,
  ) -> Union[Tuple[int, ...], CalibrationCommandReport]:
    self._ensure_started()

    async def _op(timeout: Optional[float]) -> Tuple[int, ...]:
      if not self._cal.has_mph:
        raise RuntimeError(
          "Instrument does not have an 8MPH head. Cannot use calibrate_squeeze_tips_mph."
        )
      spots = tip_spot if isinstance(tip_spot, list) else [tip_spot]
      if not spots:
        raise ValueError("calibrate_squeeze_tips_mph: tip_spot list is empty")

      ref_spot = spots[0]
      loc = ref_spot.get_absolute_location("c", "c", "t")
      tip_position = PrepCmd.TipPositionParameters.for_op(
        PrepCmd.ChannelIndex.MPHChannel,
        loc,
        ref_spot.get_tip(),
        z_seek_offset=z_seek_offset,
      )

      result = await self._cal.client.send_command(
        PrepCmd.PrepCalibrateSqueezeTips(
          channels=[tip_position],
        ),
        read_timeout=timeout,
      )
      if result is None or not getattr(result, "positions", None):
        return ()
      return tuple(int(p) for p in result.positions)

    return await self._run_with_report(
      "calibrate_squeeze_tips_mph",
      _op,
      channel=PrepCmd.ChannelIndex.MPHChannel,
      read_timeout=read_timeout,
    )


__all__ = [
  "CalibrationCommandReport",
  "PrepCalibration",
  "PrepCalibrationSession",
]
