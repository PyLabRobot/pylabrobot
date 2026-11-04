import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, List, Optional, Tuple, cast
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.usb import USB
from pylabrobot.legacy.plate_reading.tecan.spark20m.enums import SparkDevice
from pylabrobot.legacy.plate_reading.tecan.spark20m.spark_backend import ExperimentalSparkBackend
from pylabrobot.legacy.plate_reading.tecan.spark20m.spark_reader_async import SparkReaderAsync
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.testing.concurrency import AnyioTestBase

sys.modules["usb.core"] = MagicMock()
sys.modules["usb.util"] = MagicMock()


@asynccontextmanager
async def empty_background_read(
  device: SparkDevice, read_timeout: int = 100
) -> AsyncIterator[List[bytes]]:
  del device, read_timeout
  yield []


class TestExperimentalSparkBackend(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    assert kwargs == {}
    # Patch SparkReaderAsync
    self.reader_patcher = patch(
      "pylabrobot.legacy.plate_reading.tecan.spark20m.spark_backend.SparkReaderAsync"
    )
    self.MockReaderClass = self.reader_patcher.start()
    stack.callback(self.reader_patcher.stop)
    self.mock_reader = self.MockReaderClass.return_value

    self.mock_reader.send_command = AsyncMock(return_value=True)

    async def send_private(
      command: str, *args: object, _on_commit: Optional[Callable[[], None]] = None, **kwargs: object
    ) -> Optional[str]:
      result = await self.mock_reader.send_command(command, *args, **kwargs)
      if _on_commit is not None:
        _on_commit()
      return cast(Optional[str], result)

    self.mock_reader._send_command = send_private
    self.mock_reader.background_read = empty_background_read

    # Patch processor functions
    self.abs_proc_patcher = patch(
      "pylabrobot.legacy.plate_reading.tecan.spark20m.spark_backend.process_absorbance"
    )
    self.mock_process_absorbance = self.abs_proc_patcher.start()
    stack.callback(self.abs_proc_patcher.stop)

    self.fluo_proc_patcher = patch(
      "pylabrobot.legacy.plate_reading.tecan.spark20m.spark_backend.process_fluorescence"
    )
    self.mock_process_fluorescence = self.fluo_proc_patcher.start()
    stack.callback(self.fluo_proc_patcher.stop)

    self.backend = ExperimentalSparkBackend()
    # Populate devices so device-connected checks pass
    self.backend.reader.devices = {
      SparkDevice.ABSORPTION: MagicMock(),
      SparkDevice.FLUORESCENCE: MagicMock(),
      SparkDevice.PLATE_TRANSPORT: MagicMock(),
    }

  async def test_open(self) -> None:
    await self.backend.open()
    self.mock_reader.send_command.assert_called()

  async def test_read_absorbance(self) -> None:
    cast(Any, self.backend)._run_measurement = AsyncMock(return_value=[])
    self.mock_process_absorbance.return_value = [[0.5]]

    plate = MagicMock(spec=Plate)
    plate.num_items_x = 2
    plate.num_items_y = 2
    plate.get_size_y.return_value = 100

    well = MagicMock(spec=Well)
    well.parent = plate
    well.get_row.return_value = 0
    well.get_column.return_value = 0
    well.location = MagicMock()
    well.location.x = 0
    well.location.y = 0
    well.location.z = 0
    well.get_anchor.return_value = MagicMock()

    plate.get_item.return_value = well

    results = await self.backend.read_absorbance(plate, None, wavelength=600)

    assert len(results) == 1
    assert results[0]["wavelength"] == 600
    assert results[0]["data"] == [[0.5]]

  async def test_read_fluorescence(self) -> None:
    cast(Any, self.backend)._run_measurement = AsyncMock(return_value=[])
    self.mock_process_fluorescence.return_value = [[100.0]]

    plate = MagicMock(spec=Plate)
    plate.num_items_x = 2
    plate.num_items_y = 2
    plate.get_size_y.return_value = 100

    well = MagicMock(spec=Well)
    well.parent = plate
    well.get_row.return_value = 0
    well.get_column.return_value = 0
    well.location = MagicMock()
    well.location.x = 0
    well.location.y = 0
    well.location.z = 0
    well.get_anchor.return_value = MagicMock()

    plate.get_item.return_value = well

    results = await self.backend.read_fluorescence(
      plate, [well], excitation_wavelength=480, emission_wavelength=520
    )

    assert len(results) == 1
    assert results[0]["ex_wavelength"] == 480
    assert results[0]["em_wavelength"] == 520
    assert results[0]["data"] == [[100.0]]

  @pytest.mark.parametrize(
    ("measurement", "post_start_command"),
    [
      ("absorbance", 1),
      ("absorbance", 2),
      ("absorbance", 3),
      ("absorbance", 4),
      ("absorbance", 5),
      ("fluorescence", 1),
      ("fluorescence", 2),
      ("fluorescence", 3),
      ("fluorescence", 4),
      ("fluorescence", 5),
      ("fluorescence", 6),
      ("fluorescence", 7),
      ("fluorescence", 8),
      ("fluorescence", 9),
      ("fluorescence", 10),
      ("fluorescence", 11),
    ],
  )
  async def test_cancellation_at_each_post_start_setup_command_cleans_once(
    self, measurement: str, post_start_command: int
  ) -> None:
    commands: List[str] = []
    events: List[str] = []
    scopes: List[anyio.CancelScope] = []
    started = False
    setup_command = 0
    bulk_reader_entered = False

    async def send(command: str, *args: object, **kwargs: object) -> Optional[str]:
      del args, kwargs
      nonlocal setup_command
      commands.append(command)
      events.append(command)
      if started and command not in {
        "MESSAGE TYPE=ALL TIME_INTERVAL=0",
        "MEASUREMENT END",
      }:
        setup_command += 1
        if setup_command == post_start_command:
          scopes[0].cancel()
          await anyio.sleep_forever()
      return "OK"

    async def send_start(
      command: str, *args: object, _on_commit: Optional[Callable[[], None]] = None, **kwargs: object
    ) -> Optional[str]:
      del args, kwargs
      nonlocal started
      commands.append(command)
      events.append(command)
      assert command == "MEASUREMENT START"
      assert _on_commit is not None
      _on_commit()
      started = True
      return "OK"

    @asynccontextmanager
    async def background_read(
      device_type: SparkDevice, read_timeout: int = 100
    ) -> AsyncIterator[List[bytes]]:
      del device_type, read_timeout
      nonlocal bulk_reader_entered
      bulk_reader_entered = True
      yield []

    @asynccontextmanager
    async def reader_lifespan(**kwargs: Any) -> AsyncIterator[HasLifespan]:
      assert kwargs == {}
      events.append("reader.enter")
      try:
        yield self.backend.reader
      finally:
        events.append("reader.exit")

    self.mock_reader.send_command.side_effect = send
    cast(Any, self.backend.reader)._send_command = send_start
    setattr(self.backend.reader, "background_read", background_read)
    setattr(self.backend.reader, "lifespan", reader_lifespan)
    cast(Any, self.backend.config_control).init_module = AsyncMock()

    with anyio.CancelScope() as scope:
      scopes.append(scope)
      async with self.backend:
        if measurement == "absorbance":
          await self.backend.read_absorbance(MagicMock(spec=Plate), None, wavelength=600)
        else:
          await self.backend.read_fluorescence(
            MagicMock(spec=Plate), [], excitation_wavelength=480, emission_wavelength=520
          )

    post_start_events = events[events.index("MEASUREMENT START") :]
    assert setup_command == post_start_command
    assert post_start_events.count("MESSAGE TYPE=ALL TIME_INTERVAL=0") == 1
    assert post_start_events.count("MEASUREMENT END") == 1
    assert post_start_events[-3:] == [
      "MESSAGE TYPE=ALL TIME_INTERVAL=0",
      "MEASUREMENT END",
      "reader.exit",
    ]
    assert not bulk_reader_entered

  async def test_later_setup_failure_preserves_identity_when_cleanup_succeeds(self) -> None:
    failure = RuntimeError("later setup failed")
    commands: List[str] = []
    started = False
    setup_command = 0

    async def send(command: str, *args: object, **kwargs: object) -> Optional[str]:
      del args, kwargs
      nonlocal setup_command
      commands.append(command)
      if started and command not in {
        "MESSAGE TYPE=ALL TIME_INTERVAL=0",
        "MEASUREMENT END",
      }:
        setup_command += 1
        if setup_command == 3:
          raise failure
      return "OK"

    async def send_start(
      command: str, *args: object, _on_commit: Optional[Callable[[], None]] = None, **kwargs: object
    ) -> Optional[str]:
      del args, kwargs
      nonlocal started
      commands.append(command)
      assert _on_commit is not None
      _on_commit()
      started = True
      return "OK"

    self.mock_reader.send_command.side_effect = send
    cast(Any, self.backend.reader)._send_command = send_start

    with pytest.raises(RuntimeError) as error_info:
      await self.backend.read_absorbance(MagicMock(spec=Plate), None, wavelength=600)

    assert error_info.value is failure
    assert commands[-2:] == ["MESSAGE TYPE=ALL TIME_INTERVAL=0", "MEASUREMENT END"]

  async def test_setup_cleanup_failures_prioritize_end_with_exact_context(self) -> None:
    setup_failure = RuntimeError("later setup failed")
    interval_failure = RuntimeError("interval cleanup failed")
    end_failure = RuntimeError("end cleanup failed")
    started = False
    setup_command = 0

    async def send(command: str, *args: object, **kwargs: object) -> Optional[str]:
      del args, kwargs
      nonlocal setup_command
      if command == "MESSAGE TYPE=ALL TIME_INTERVAL=0":
        raise interval_failure
      if command == "MEASUREMENT END":
        raise end_failure
      if started:
        setup_command += 1
        if setup_command == 3:
          raise setup_failure
      return "OK"

    async def send_start(
      command: str, *args: object, _on_commit: Optional[Callable[[], None]] = None, **kwargs: object
    ) -> Optional[str]:
      del command, args, kwargs
      nonlocal started
      assert _on_commit is not None
      _on_commit()
      started = True
      return "OK"

    self.mock_reader.send_command.side_effect = send
    cast(Any, self.backend.reader)._send_command = send_start

    with pytest.raises(RuntimeError) as error_info:
      await self.backend.read_absorbance(MagicMock(spec=Plate), None, wavelength=600)

    assert error_info.value is end_failure
    assert error_info.value.__context__ is interval_failure
    assert interval_failure.__context__ is setup_failure

  async def test_start_prewrite_failure_does_not_end_measurement(self) -> None:
    failure = RuntimeError("START prewrite failed")
    commands: List[str] = []

    async def send(command: str, *args: object, **kwargs: object) -> Optional[str]:
      del args, kwargs
      commands.append(command)
      return "OK"

    async def fail_start(
      command: str, *args: object, _on_commit: Optional[Callable[[], None]] = None, **kwargs: object
    ) -> Optional[str]:
      del args, kwargs
      commands.append(command)
      assert _on_commit is not None
      raise failure

    self.mock_reader.send_command.side_effect = send
    cast(Any, self.backend.reader)._send_command = fail_start

    with pytest.raises(RuntimeError) as error_info:
      await self.backend.read_absorbance(MagicMock(spec=Plate), None, wavelength=600)

    assert error_info.value is failure
    assert commands.count("MEASUREMENT START") == 1
    assert "MESSAGE TYPE=ALL TIME_INTERVAL=0" not in commands
    assert "MEASUREMENT END" not in commands

  async def test_start_response_failure_after_commit_ends_measurement_once(self) -> None:
    failure = RuntimeError("START response failed")
    commands: List[str] = []

    async def send(command: str, *args: object, **kwargs: object) -> Optional[str]:
      del args, kwargs
      commands.append(command)
      return "OK"

    async def fail_committed_start(
      command: str, *args: object, _on_commit: Optional[Callable[[], None]] = None, **kwargs: object
    ) -> Optional[str]:
      del args, kwargs
      commands.append(command)
      assert _on_commit is not None
      _on_commit()
      raise failure

    self.mock_reader.send_command.side_effect = send
    cast(Any, self.backend.reader)._send_command = fail_committed_start

    with pytest.raises(RuntimeError) as error_info:
      await self.backend.read_absorbance(MagicMock(spec=Plate), None, wavelength=600)

    assert error_info.value is failure
    assert commands.count("MEASUREMENT START") == 1
    assert commands.count("MESSAGE TYPE=ALL TIME_INTERVAL=0") == 1
    assert commands.count("MEASUREMENT END") == 1

  async def test_start_cancellation_after_commit_ends_measurement_once(self) -> None:
    commands: List[str] = []
    scopes: List[anyio.CancelScope] = []

    async def send(command: str, *args: object, **kwargs: object) -> Optional[str]:
      del args, kwargs
      commands.append(command)
      return "OK"

    async def cancel_committed_start(
      command: str, *args: object, _on_commit: Optional[Callable[[], None]] = None, **kwargs: object
    ) -> Optional[str]:
      del args, kwargs
      commands.append(command)
      assert _on_commit is not None
      _on_commit()
      scopes[0].cancel()
      await anyio.sleep_forever()
      return None

    self.mock_reader.send_command.side_effect = send
    cast(Any, self.backend.reader)._send_command = cancel_committed_start

    with anyio.CancelScope() as scope:
      scopes.append(scope)
      await self.backend.read_absorbance(MagicMock(spec=Plate), None, wavelength=600)

    assert commands.count("MEASUREMENT START") == 1
    assert commands.count("MESSAGE TYPE=ALL TIME_INTERVAL=0") == 1
    assert commands.count("MEASUREMENT END") == 1

  async def test_background_reader_entry_failure_uses_outer_cleanup(self) -> None:
    failure = RuntimeError("bulk reader did not enter")
    commands: List[str] = []

    async def send(command: str, *args: object, **kwargs: object) -> Optional[str]:
      del args, kwargs
      commands.append(command)
      return "OK"

    @asynccontextmanager
    async def fail_background_read(
      device_type: SparkDevice, read_timeout: int = 100
    ) -> AsyncIterator[List[bytes]]:
      del device_type, read_timeout
      raise failure
      yield []

    self.mock_reader.send_command.side_effect = send
    setattr(self.backend.reader, "background_read", fail_background_read)

    with pytest.raises(RuntimeError) as error_info:
      await self.backend.read_absorbance(MagicMock(spec=Plate), None, wavelength=600)

    assert error_info.value is failure
    assert commands.count("MESSAGE TYPE=ALL TIME_INTERVAL=0") == 1
    assert commands.count("MEASUREMENT END") == 1
    assert commands[-2:] == ["MESSAGE TYPE=ALL TIME_INTERVAL=0", "MEASUREMENT END"]

  async def test_successful_absorbance_command_order_and_single_cleanup_are_unchanged(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    events: List[str] = []

    async def send(command: str, *args: object, **kwargs: object) -> Optional[str]:
      del args, kwargs
      events.append(command)
      return "OK"

    @asynccontextmanager
    async def background_read(
      device_type: SparkDevice, read_timeout: int = 100
    ) -> AsyncIterator[List[bytes]]:
      del device_type, read_timeout
      events.append("bulk_reader.start")
      try:
        yield []
      finally:
        events.append("bulk_reader.done")

    async def scan(plate: Plate, wells: Optional[List[Well]], z: float) -> None:
      del plate, wells
      assert z == 9150
      events.append("scan")

    self.mock_reader.send_command.side_effect = send
    setattr(self.backend.reader, "background_read", background_read)
    cast(Any, self.backend).scan_plate_range = scan
    self.mock_process_absorbance.return_value = [[0.5]]
    self._skip_drain(monkeypatch)

    result = await self.backend.read_absorbance(MagicMock(spec=Plate), None, wavelength=600)

    assert result[0]["data"] == [[0.5]]
    assert events == [
      "MESSAGE TYPE=TEMPERATURE TIME_INTERVAL=200",
      "MODE MEASUREMENT=ABS",
      "MEASUREMENT START",
      "SPEED MOVEMENT=NORMAL MODULE=MTP",
      "SCAN DIRECTION=UP",
      "TIME SETTLE=50000",
      "READ LABEL=1 COUNT=10",
      "EXCITATION TYPE=BP WAVELENGTH=6000 BANDWIDTH=200 LABEL=1",
      "bulk_reader.start",
      "PREPARE REFERENCE=YES",
      "scan",
      "MESSAGE TYPE=ALL TIME_INTERVAL=0",
      "MEASUREMENT END",
      "bulk_reader.done",
    ]

  async def test_get_average_temperature(self) -> None:
    # Mock reader messages
    self.mock_reader.msgs = [
      {"number": 100, "args": ["2500"]},  # 25.00
      {"number": 100, "args": ["2600"]},  # 26.00
      {"number": 200, "args": ["something else"]},
    ]

    temp = await self.backend.get_average_temperature()
    assert temp == 25.5

  async def test_get_average_temperature_empty(self) -> None:
    self.mock_reader.msgs = []
    temp = await self.backend.get_average_temperature()
    assert temp is None

  async def test_experimental_read_absorbance_spectrum(self) -> None:
    cast(Any, self.backend)._run_measurement = AsyncMock(return_value=[])
    # Patch process_absorbance_spectrum
    with patch(
      "pylabrobot.legacy.plate_reading.tecan.spark20m.spark_backend.process_absorbance_spectrum"
    ) as mock_proc:
      mock_proc.return_value = {500.0: [[0.5, 0.6]], 510.0: [[0.7, 0.8]]}

      plate = MagicMock(spec=Plate)
      plate.num_items_x = 2
      plate.num_items_y = 2
      plate.get_size_y.return_value = 100

      well = MagicMock(spec=Well)
      well.parent = plate
      well.get_row.return_value = 0
      well.get_column.return_value = 0
      well.location = MagicMock()
      well.location.x = 0
      well.location.y = 0
      well.location.z = 0
      well.get_anchor.return_value = MagicMock()

      plate.get_item.return_value = well

      results = await self.backend.experimental_read_absorbance_spectrum(
        plate, None, wavelength_start=500, wavelength_end=550, wavelength_step=10
      )

    assert len(results) == 2
    assert results[0]["wavelength"] == 500.0
    assert results[0]["data"] == [[0.5, 0.6]]
    assert results[1]["wavelength"] == 510.0
    assert results[1]["data"] == [[0.7, 0.8]]

  async def test_experimental_read_absorbance_spectrum_validation(self) -> None:
    plate = MagicMock(spec=Plate)

    with pytest.raises(ValueError):
      await self.backend.experimental_read_absorbance_spectrum(
        plate, None, wavelength_start=550, wavelength_end=500
      )

    with pytest.raises(ValueError):
      await self.backend.experimental_read_absorbance_spectrum(
        plate, None, wavelength_start=500, wavelength_end=550, wavelength_step=0
      )

  async def test_experimental_read_fluorescence_excitation_spectrum(self) -> None:
    cast(Any, self.backend)._run_measurement = AsyncMock(return_value=[])
    # Patch process_fluorescence_spectrum
    with patch(
      "pylabrobot.legacy.plate_reading.tecan.spark20m.spark_backend.process_fluorescence_spectrum"
    ) as mock_proc:
      mock_proc.return_value = {440.0: [[100.0]], 450.0: [[200.0]]}

      plate = MagicMock(spec=Plate)
      plate.num_items_x = 2
      plate.num_items_y = 2
      plate.get_size_y.return_value = 100

      well = MagicMock(spec=Well)
      well.parent = plate
      well.get_row.return_value = 0
      well.get_column.return_value = 0
      well.location = MagicMock()
      well.location.x = 0
      well.location.y = 0
      well.location.z = 0
      well.get_anchor.return_value = MagicMock()

      plate.get_item.return_value = well

      results = await self.backend.experimental_read_fluorescence_excitation_spectrum(
        plate,
        [well],
        excitation_wavelength_start=440,
        excitation_wavelength_end=480,
        emission_wavelength=545,
        excitation_wavelength_step=10,
      )

    assert len(results) == 2
    assert results[0]["ex_wavelength"] == 440.0
    assert results[0]["em_wavelength"] == 545
    assert results[0]["data"] == [[100.0]]
    assert results[1]["ex_wavelength"] == 450.0
    assert results[1]["data"] == [[200.0]]

  async def test_experimental_read_fluorescence_excitation_spectrum_validation(self) -> None:
    plate = MagicMock(spec=Plate)
    well = MagicMock(spec=Well)

    with pytest.raises(ValueError):
      await self.backend.experimental_read_fluorescence_excitation_spectrum(
        plate,
        [well],
        excitation_wavelength_start=480,
        excitation_wavelength_end=440,
        emission_wavelength=545,
      )

    with pytest.raises(ValueError):
      await self.backend.experimental_read_fluorescence_excitation_spectrum(
        plate,
        [well],
        excitation_wavelength_start=440,
        excitation_wavelength_end=480,
        emission_wavelength=545,
        excitation_wavelength_step=-1,
      )

  async def test_experimental_read_fluorescence_emission_spectrum(self) -> None:
    cast(Any, self.backend)._run_measurement = AsyncMock(return_value=[])
    # Patch process_fluorescence_spectrum
    with patch(
      "pylabrobot.legacy.plate_reading.tecan.spark20m.spark_backend.process_fluorescence_spectrum"
    ) as mock_proc:
      mock_proc.return_value = {550.0: [[100.0]], 560.0: [[200.0]]}

      plate = MagicMock(spec=Plate)
      plate.num_items_x = 12
      plate.num_items_y = 8
      plate.get_size_y.return_value = 100

      well = MagicMock(spec=Well)
      well.parent = plate
      well.get_row.return_value = 0
      well.get_column.return_value = 0
      well.location = MagicMock()
      well.location.x = 0
      well.location.y = 0
      well.location.z = 0
      well.get_anchor.return_value = MagicMock()

      plate.get_item.return_value = well

      results = await self.backend.experimental_read_fluorescence_emission_spectrum(
        plate,
        [well],
        excitation_wavelength=440,
        emission_wavelength_start=550,
        emission_wavelength_end=600,
        emission_wavelength_step=10,
      )

    assert len(results) == 2
    assert results[0]["em_wavelength"] == 550.0
    assert results[0]["ex_wavelength"] == 440
    assert results[0]["data"] == [[100.0]]
    assert results[1]["em_wavelength"] == 560.0
    assert results[1]["data"] == [[200.0]]

  async def test_experimental_read_fluorescence_emission_spectrum_validation(self) -> None:
    plate = MagicMock(spec=Plate)
    well = MagicMock(spec=Well)

    with pytest.raises(ValueError):
      await self.backend.experimental_read_fluorescence_emission_spectrum(
        plate,
        [well],
        excitation_wavelength=440,
        emission_wavelength_start=600,
        emission_wavelength_end=550,
      )

    with pytest.raises(ValueError):
      await self.backend.experimental_read_fluorescence_emission_spectrum(
        plate,
        [well],
        excitation_wavelength=440,
        emission_wavelength_start=550,
        emission_wavelength_end=600,
        emission_wavelength_step=-1,
      )

  async def test_all_implemented_reads_enter_measurement_ownership(self) -> None:
    ownership_entries = 0

    @asynccontextmanager
    async def ownership() -> AsyncIterator[
      Tuple[Callable[[], None], Callable[[], Awaitable[None]]]
    ]:
      nonlocal ownership_entries
      ownership_entries += 1

      async def cleanup() -> None:
        pass

      yield lambda: None, cleanup

    cast(Any, self.backend)._measurement_session = ownership
    cast(Any, self.backend)._setup_absorbance = AsyncMock()
    cast(Any, self.backend)._setup_fluorescence = AsyncMock()
    cast(Any, self.backend)._run_measurement = AsyncMock(return_value=[])
    plate = MagicMock(spec=Plate)

    with (
      patch(
        "pylabrobot.legacy.plate_reading.tecan.spark20m.spark_backend.process_absorbance_spectrum",
        return_value={},
      ),
      patch(
        "pylabrobot.legacy.plate_reading.tecan.spark20m.spark_backend.process_fluorescence_spectrum",
        return_value={},
      ),
    ):
      await self.backend.read_absorbance(plate, None, wavelength=600)
      await self.backend.read_fluorescence(
        plate, [], excitation_wavelength=480, emission_wavelength=520
      )
      await self.backend.experimental_read_absorbance_spectrum(
        plate, None, wavelength_start=500, wavelength_end=510
      )
      await self.backend.experimental_read_fluorescence_excitation_spectrum(
        plate,
        [],
        excitation_wavelength_start=440,
        excitation_wavelength_end=450,
        emission_wavelength=520,
      )
      await self.backend.experimental_read_fluorescence_emission_spectrum(
        plate,
        [],
        excitation_wavelength=480,
        emission_wavelength_start=500,
        emission_wavelength_end=510,
      )

    assert ownership_entries == 5

  async def test_backend_inherits_managed_methods(self) -> None:
    assert ExperimentalSparkBackend.__aenter__ is HasLifespan.__aenter__
    assert ExperimentalSparkBackend.__aexit__ is HasLifespan.__aexit__
    assert ExperimentalSparkBackend.setup is HasLifespan.setup
    assert ExperimentalSparkBackend.stop is HasLifespan.stop

  async def test_reader_entry_failure_prevents_initialization(self) -> None:
    events: List[str] = []
    self.backend.reader = cast(
      SparkReaderAsync, LifecycleReader(events, entry_failure=RuntimeError("reader failed"))
    )

    async def initialize() -> None:
      events.append("config.init")

    cast(Any, self.backend.config_control).init_module = initialize

    with pytest.raises(RuntimeError, match="reader failed"):
      async with self.backend:
        pass

    assert events == ["reader.enter"]

  async def test_initialization_failure_closes_reader(self) -> None:
    events: List[str] = []
    self.backend.reader = cast(SparkReaderAsync, LifecycleReader(events))

    async def initialize() -> None:
      events.append("config.init")
      raise RuntimeError("initialization failed")

    cast(Any, self.backend.config_control).init_module = initialize

    with pytest.raises(RuntimeError, match="initialization failed"):
      async with self.backend:
        pass

    assert events == ["reader.enter", "config.init", "reader.exit"]

  async def test_body_cancellation_closes_reader(self) -> None:
    events: List[str] = []
    self.backend.reader = cast(SparkReaderAsync, LifecycleReader(events))
    self._record_backend_initialization(events)

    with anyio.CancelScope() as scope:
      async with self.backend:
        scope.cancel()
        await anyio.sleep_forever()

    assert events[:3] == ["reader.enter", "config.init", "interval.off"]
    assert events[-1] == "reader.exit"

  async def test_backend_rejects_lifespan_options_before_reader_entry(self) -> None:
    events: List[str] = []
    self.backend.reader = cast(SparkReaderAsync, LifecycleReader(events))

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with self.backend.lifespan(unexpected=True):
        pass

    assert events == []

  async def test_background_reader_is_joined_on_normal_exit(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    events: List[str] = []
    self._configure_measurement(events)
    self._skip_drain(monkeypatch)

    results = await self.backend._run_measurement(SparkDevice.ABSORPTION, MagicMock(), None)

    assert results == []
    assert events == [
      "bulk_reader.start",
      "measurement.prepare",
      "scan",
      "interval.off",
      "measurement.end",
      "bulk_reader.done",
    ]

  async def test_background_reader_is_joined_on_scan_failure(self) -> None:
    events: List[str] = []
    self._configure_measurement(events, scan_failure=RuntimeError("scan failed"))

    with pytest.raises(RuntimeError, match="scan failed"):
      await self.backend._run_measurement(SparkDevice.ABSORPTION, MagicMock(), None)

    assert events.index("measurement.end") < events.index("bulk_reader.done")
    assert events[-3:] == ["interval.off", "measurement.end", "bulk_reader.done"]

  async def test_background_reader_failure_reaches_measurement_caller(self) -> None:
    events: List[str] = []
    self._configure_measurement(events)
    reader = SparkReaderAsync()
    reader.devices[SparkDevice.ABSORPTION] = cast(USB, MagicMock())
    self.backend.reader = reader

    async def scan(plate: Plate, wells: Optional[List[Well]], z: float) -> None:
      del plate, wells, z
      events.append("scan")
      await anyio.sleep_forever()

    cast(Any, self.backend).scan_plate_range = scan

    with patch.object(reader, "_read_packet", side_effect=RuntimeError("bulk read failed")):
      with pytest.raises(RuntimeError, match="bulk read failed"):
        await self.backend._run_measurement(SparkDevice.ABSORPTION, MagicMock(), None)

    assert events == ["measurement.prepare", "scan", "interval.off", "measurement.end"]

  async def test_measurement_cancellation_runs_cleanup_before_reader_join(self) -> None:
    events: List[str] = []
    scopes: List[anyio.CancelScope] = []
    self._configure_measurement(events)

    async def scan(plate: Plate, wells: Optional[List[Well]], z: float) -> None:
      del plate, wells, z
      events.append("scan")
      scopes[0].cancel()
      await anyio.sleep_forever()

    cast(Any, self.backend).scan_plate_range = scan
    self._record_backend_initialization(events)
    with anyio.CancelScope() as scope:
      scopes.append(scope)
      async with self.backend:
        await self.backend._run_measurement(SparkDevice.ABSORPTION, MagicMock(), None)

    assert events[:3] == ["reader.enter", "config.init", "interval.off"]
    assert events.index("measurement.end") < events.index("bulk_reader.done")
    assert events[-2:] == ["bulk_reader.done", "reader.exit"]

  async def test_cleanup_command_failure_still_joins_reader(self) -> None:
    events: List[str] = []
    interval_error = RuntimeError("interval cleanup failed")
    self._configure_measurement(events, cleanup_failure=interval_error)

    with pytest.raises(RuntimeError, match="interval cleanup failed") as error_info:
      await self.backend._run_measurement(SparkDevice.ABSORPTION, MagicMock(), None)

    assert error_info.value is interval_error
    assert events[-3:] == ["interval.off", "measurement.end", "bulk_reader.done"]

  async def test_both_cleanup_failures_prioritize_end_and_join_reader(self) -> None:
    events: List[str] = []
    interval_error = RuntimeError("interval cleanup failed")
    end_error = RuntimeError("end cleanup failed")
    self._configure_measurement(
      events,
      cleanup_failure=interval_error,
      end_failure=end_error,
    )

    with pytest.raises(RuntimeError, match="end cleanup failed") as error_info:
      await self.backend._run_measurement(SparkDevice.ABSORPTION, MagicMock(), None)

    assert error_info.value is end_error
    assert error_info.value.__context__ is interval_error
    assert events[-3:] == ["interval.off", "measurement.end", "bulk_reader.done"]

  async def test_preparation_failure_cleans_session_before_joining_reader(self) -> None:
    events: List[str] = []
    failure = RuntimeError("prepare failed")
    self._configure_measurement(events, prepare_failure=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with self.backend._measurement_session() as (start_committed, cleanup):
        start_committed()
        await self.backend._run_measurement(
          SparkDevice.ABSORPTION, MagicMock(), None, cleanup=cleanup
        )

    assert error_info.value is failure
    assert events == [
      "bulk_reader.start",
      "measurement.prepare",
      "interval.off",
      "measurement.end",
      "bulk_reader.done",
    ]

  async def test_failed_end_is_retried_by_session_owner(self) -> None:
    events: List[str] = []
    failure = RuntimeError("end failed")
    self._configure_measurement(events)
    calls = 0

    async def end() -> None:
      nonlocal calls
      calls += 1
      events.append("measurement.end")
      if calls == 1:
        raise failure

    cast(Any, self.backend.measurement_control).end_measurement = end
    with pytest.raises(RuntimeError) as error_info:
      async with self.backend._measurement_session() as (start_committed, cleanup):
        start_committed()
        await cleanup()

    assert error_info.value is failure
    assert events == ["interval.off", "measurement.end", "interval.off", "measurement.end"]

  async def test_drain_uses_anyio_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
    events: List[str] = []
    clock = 0.0
    sleeps: List[float] = []
    self._configure_measurement(events)

    def current_time() -> float:
      return clock

    async def sleep(delay: float) -> None:
      nonlocal clock
      sleeps.append(delay)
      clock += delay

    monkeypatch.setattr(anyio, "current_time", current_time)
    monkeypatch.setattr(anyio, "sleep", sleep)

    with anyio.fail_after(0.2):
      await self.backend._run_measurement(SparkDevice.ABSORPTION, MagicMock(), None)

    assert sleeps == [0.5] * 5
    assert clock == 2.5

  def _record_backend_initialization(self, events: List[str]) -> None:
    async def initialize() -> None:
      events.append("config.init")

    async def interval_off() -> None:
      events.append("interval.off")

    cast(Any, self.backend.config_control).init_module = initialize
    cast(Any, self.backend.data_control).turn_all_interval_messages_off = interval_off

  def _configure_measurement(
    self,
    events: List[str],
    *,
    prepare_failure: Optional[Exception] = None,
    scan_failure: Optional[Exception] = None,
    cleanup_failure: Optional[Exception] = None,
    end_failure: Optional[Exception] = None,
  ) -> None:
    self.backend.reader = cast(SparkReaderAsync, MeasurementReader(events))

    async def prepare(*, measure_reference: bool) -> None:
      assert measure_reference
      events.append("measurement.prepare")
      if prepare_failure is not None:
        raise prepare_failure

    async def scan(plate: Plate, wells: Optional[List[Well]], z: float) -> None:
      del plate, wells
      assert z == 9150
      events.append("scan")
      if scan_failure is not None:
        raise scan_failure

    async def interval_off() -> None:
      events.append("interval.off")
      if cleanup_failure is not None:
        raise cleanup_failure

    async def end_measurement() -> None:
      events.append("measurement.end")
      if end_failure is not None:
        raise end_failure

    cast(Any, self.backend.measurement_control).prepare_instrument = prepare
    cast(Any, self.backend).scan_plate_range = scan
    cast(Any, self.backend.data_control).turn_all_interval_messages_off = interval_off
    cast(Any, self.backend.measurement_control).end_measurement = end_measurement

  def _skip_drain(self, monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter([0.0, 5.0])
    monkeypatch.setattr(anyio, "current_time", lambda: next(times))


class LifecycleReader:
  def __init__(self, events: List[str], entry_failure: Optional[Exception] = None) -> None:
    self.events = events
    self.entry_failure = entry_failure

  @asynccontextmanager
  async def lifespan(self) -> AsyncIterator["LifecycleReader"]:
    self.events.append("reader.enter")
    if self.entry_failure is not None:
      raise self.entry_failure
    try:
      yield self
    finally:
      self.events.append("reader.exit")


class MeasurementReader:
  def __init__(self, events: List[str], failure: Optional[Exception] = None) -> None:
    self.events = events
    self.failure = failure

  @asynccontextmanager
  async def lifespan(self) -> AsyncIterator["MeasurementReader"]:
    self.events.append("reader.enter")
    try:
      yield self
    finally:
      self.events.append("reader.exit")

  @asynccontextmanager
  async def background_read(
    self, device: SparkDevice, read_timeout: int = 100
  ) -> AsyncIterator[List[bytes]]:
    del device, read_timeout
    self.events.append("bulk_reader.start")
    if self.failure is not None:
      raise self.failure
    try:
      yield []
    finally:
      self.events.append("bulk_reader.done")
