import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pylabrobot.molecular_devices.filtermax import (
  FilterMaxF5,
  FilterSlideCatalog,
  KineticTiming,
  WellScanSettings,
)
from pylabrobot.molecular_devices.filtermax.errors import (
  FilterMaxCatalogError,
  FilterMaxDeviceError,
  FilterMaxIdentityError,
  FilterMaxProtocolError,
  FilterMaxSlideNotInstalledError,
  FilterMaxUnsupportedOperationError,
)
from pylabrobot.molecular_devices.filtermax.protocol import (
  ACK,
  ENQ,
  EOT,
  FilterMaxTransport,
  build_frame,
)
from pylabrobot.resources import (
  Coordinate,
  Plate,
  Resource,
  Well,
  cor_96_wellplate_360uL_Fb,
  create_ordered_items_2d,
)


def make_plate() -> Plate:
  """Represent the captured Costar dimensions with ordinary PLR resources."""
  return Plate(
    name="costar",
    size_x=127.70,
    size_y=85.70,
    size_z=14.27,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.85,
      dy=8.32,
      dz=0,
      item_dx=9.02,
      item_dy=9.00,
      size_x=6.40,
      size_y=6.40,
      size_z=10.69,
    ),
  )


def device_message(payload: str) -> bytes:
  return bytes((ENQ,)) + build_frame(payload) + bytes((EOT,))


def exchange_response(payload: str) -> bytes:
  return bytes((ACK, ACK)) + device_message(payload)


def measurement_response(*payloads: str) -> bytes:
  return bytes((ACK, ACK)) + b"".join(device_message(payload) for payload in payloads)


class FakeSerial:
  def __init__(self, incoming: bytes):
    self.incoming = bytearray(incoming)
    self.writes: list[bytes] = []
    self.setup_called = False
    self.stop_called = False

  async def setup(self) -> None:
    self.setup_called = True

  async def stop(self) -> None:
    self.stop_called = True

  async def write(self, data: bytes) -> None:
    self.writes.append(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    if not self.incoming:
      return b""
    count = min(num_bytes, len(self.incoming))
    data = bytes(self.incoming[:count])
    del self.incoming[:count]
    return data


def host_payloads(io: FakeSerial):
  return [data[2:-5].decode("ascii") for data in io.writes if data.startswith(b"\x02")]


class TestFilterMaxF5(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    self.plate = make_plate()
    self.catalog = FilterSlideCatalog.from_softmax_xml(
      Path(__file__).with_name("filtermax_slides.xml")
    )

  def make_driver(self, incoming: bytes) -> tuple[FilterMaxF5, FakeSerial]:
    driver = FilterMaxF5("test", "COM1", self.catalog)
    io = FakeSerial(incoming)
    driver.io = io  # type: ignore[assignment]
    driver._transport = FilterMaxTransport(io)  # type: ignore[arg-type]
    return driver, io

  def test_library_plate_geometry_is_local_to_plate(self) -> None:
    """Use library well centers and depth, unaffected by deck placement."""
    plate = cor_96_wellplate_360uL_Fb("library_plate")
    deck = Resource("deck", size_x=500, size_y=500, size_z=100)
    deck.assign_child_resource(plate, location=Coordinate(200, 100, 50))
    self.assertEqual(
      FilterMaxF5._plate_command(plate, read_height=7.5),
      "PLATE Temp 12776 8548 1420 1067 1430 1128 12 8 900 900 686 686 0 300 0 750 1 1",
    )

  async def test_custom_plate_shape_spacing_and_well_selection(self) -> None:
    """Encode asymmetric geometry and mask selected wells in a non-96-well plate."""
    plate = Plate(
      "custom",
      size_x=100,
      size_y=60,
      size_z=20,
      ordered_items=create_ordered_items_2d(
        Well,
        num_items_x=3,
        num_items_y=2,
        dx=12,
        dy=8,
        dz=2,
        item_dx=20,
        item_dy=25,
        size_x=10,
        size_y=6,
        size_z=15,
        cross_section_type="rectangle",
      ),
    )
    incoming = b"".join(
      (
        exchange_response("+ 2 1"),
        exchange_response("+"),
        exchange_response("+"),
        exchange_response("+"),
        measurement_response("+ 100 200 300", "+ 400 500 600", "+ INFO"),
        exchange_response("+ 25"),
        exchange_response("+"),
      )
    )
    driver, io = self.make_driver(incoming)
    result = await driver.read_absorbance(
      plate,
      450,
      wells=[plate.get_well("B3"), plate.get_well("A2"), plate.get_well("B3")],
      read_height=8.75,
    )
    self.assertEqual(result[0].data, [[None, 0.2, None], [None, None, 0.6]])
    self.assertIn(
      "PLATE Temp 10000 6000 2000 1500 1700 2400 3 2 2000 2500 1000 600 0 300 0 875 1 1",
      host_payloads(io),
    )
    self.assertIn("ABS 0 1 450 1 2 1 1 0 0 0 0 0 3 1 1 0 O e INFO", host_payloads(io))

  async def test_invalid_geometry_is_rejected_before_io(self) -> None:
    """Do not transmit geometry the single regular-grid command cannot represent."""
    missing_location = make_plate()
    missing_location.get_well("A1").location = None
    irregular = make_plate()
    irregular.get_well("C4").location = Coordinate(42, 52, 0)
    rotated = make_plate()
    rotated.rotate(z=90)
    unequal = make_plate()
    unequal.get_well("D5").rotate(z=90)
    for plate in (missing_location, irregular, rotated, unequal):
      for mode in ("absorbance", "luminescence"):
        with self.subTest(plate=plate, mode=mode):
          driver, _ = self.make_driver(b"")
          with patch.object(driver._transport, "exchange", new_callable=AsyncMock) as exchange:
            with self.assertRaises(ValueError):
              if mode == "absorbance":
                await driver.read_absorbance(plate, 450, read_height=10.27)
              else:
                await driver.read_luminescence(plate)
            exchange.assert_not_awaited()

  async def test_foreign_wells_and_empty_selection_are_rejected_before_io(self) -> None:
    """Same-named wells from another plate cannot select this plate's wells."""
    other = make_plate()
    for wells in ([], [other.get_well("C4")], ["C4"]):
      for mode in ("absorbance", "luminescence"):
        with self.subTest(wells=wells, mode=mode):
          driver, _ = self.make_driver(b"")
          with patch.object(driver._transport, "exchange", new_callable=AsyncMock) as exchange:
            with self.assertRaises(ValueError):
              if mode == "absorbance":
                await driver.read_absorbance(
                  self.plate,
                  450,
                  wells=wells,  # type: ignore[arg-type]
                  read_height=10.27,
                )
              else:
                await driver.read_luminescence(self.plate, wells=wells)  # type: ignore[arg-type]
            exchange.assert_not_awaited()

  async def test_invalid_read_settings_are_rejected_before_io(self) -> None:
    """Reject non-finite heights and unknown orientations without moving hardware."""
    driver, _ = self.make_driver(b"")
    with patch.object(driver._transport, "exchange", new_callable=AsyncMock) as exchange:
      for height in (-1, float("nan"), float("inf")):
        with self.subTest(read_height=height), self.assertRaises(ValueError):
          await driver.read_absorbance(self.plate, 450, read_height=height)
      with self.assertRaises(ValueError):
        await driver.read_absorbance(
          self.plate,
          450,
          read_height=10.27,
          orientation="invalid",  # type: ignore[arg-type]
        )
      exchange.assert_not_awaited()

  async def test_setup_verifies_captured_f5_identity(self) -> None:
    identity = (
      "+\rAnthos Fluoro\rV1.2 b13 11.12.2014\r141\r1191\r"
      "Excitation Filter Slider Nr: 2\rEmission Filter Slider Nr: 1\r"
      "Barcode: not installed\rTemperatureControl: installed\rDevice Code: 57855\r"
      "Plate Control: TRUE\rPIC Firmware: + V1.0 b5 30.01.2006\rCPLD Version: 6"
    )
    driver, io = self.make_driver(exchange_response(identity))
    await driver.setup()
    self.assertTrue(io.setup_called)
    self.assertEqual((await driver.get_error_log()), ())
    self.assertEqual(driver._instrument_info.device_code, 57855)  # type: ignore[union-attr]

  async def test_setup_refuses_an_unexpected_identity_and_closes_port(self) -> None:
    identity = (
      "+\rNot an F5\rV1.0\r141\r1191\r"
      "Excitation Filter Slider Nr: 2\rEmission Filter Slider Nr: 1\r"
      "Barcode: not installed\rTemperatureControl: installed\rDevice Code: 1\r"
      "Plate Control: TRUE\rPIC Firmware: + V1.0\rCPLD Version: 6"
    )
    driver, io = self.make_driver(exchange_response(identity))
    with self.assertRaises(FilterMaxIdentityError):
      await driver.setup()
    self.assertTrue(io.stop_called)

  async def test_control_commands_match_captured_payloads(self) -> None:
    incoming = b"".join(
      (
        exchange_response("+ READY"),
        exchange_response("+ 0 0 0 0 0 0 0 0 0 0 0"),
        exchange_response("+ 2 1"),
        exchange_response("+ 6512"),
        exchange_response("+ -67"),
        exchange_response("+"),
        exchange_response("+"),
        exchange_response("+ 24.6"),
        exchange_response("+"),
        exchange_response("+"),
        exchange_response("+ 0 0 // 0 0 //"),
      )
    )
    driver, io = self.make_driver(incoming)
    status = await driver.get_status()
    self.assertTrue(status.ready)
    self.assertEqual(len(status.state_bits), 11)
    slides = await driver.get_filter_slides()
    self.assertEqual((slides.excitation_slide_id, slides.emission_slide_id), (2, 1))
    await driver.move_plate_tray_out()
    await driver.move_plate_tray_in()
    await driver.move_filter_slide_out("excitation")
    await driver.move_filter_slide_in("emission")
    self.assertEqual(await driver.get_temperature(), 24.6)
    await driver.set_temperature(25.0)
    await driver.deactivate_temperature_control()
    await driver.start_shaking("linear", "medium", 5)
    self.assertEqual(
      host_payloads(io),
      [
        "STAT",
        "SGS",
        "CS",
        "E P",
        "L P",
        "E A",
        "L B",
        "TG",
        "TS 25",
        "TS 0",
        "SHAKE X 0 3 40 5",
      ],
    )

  async def test_absorbance_masks_hardware_row_and_scales_milliod(self) -> None:
    row = "+ " + " ".join(str(100 + index) for index in range(12))
    incoming = b"".join(
      (
        exchange_response("+ 2 1"),
        exchange_response("+"),
        exchange_response("+"),
        exchange_response("+ -67"),
        measurement_response(row, "+ INFO"),
        exchange_response("+ 24.5"),
        exchange_response("+ 6512"),
      )
    )
    driver, io = self.make_driver(incoming)
    results = await driver.read_absorbance(
      self.plate, 450, wells=[self.plate.get_well("C4")], read_height=10.27
    )
    self.assertEqual(len(results), 1)
    value = results[0].data[2][3]
    self.assertIsNotNone(value)
    assert value is not None
    self.assertAlmostEqual(value, 0.103)
    self.assertIsNone(results[0].data[2][2])
    payloads = host_payloads(io)
    self.assertIn(
      "ABS 0 1 450 3 3 1 1 0 0 0 0 0 3 1 1 0 O e INFO",
      payloads,
    )
    self.assertIn(
      "SF A 2 260 12 1 340 10 1 405 10 1 450 8 1 595 8 1 620 8 1",
      payloads,
    )

  async def test_absorbance_portrait_uses_captured_shift_and_orientation_code(self) -> None:
    rows = ["+ " + " ".join(str(row * 10 + column) for column in range(12)) for row in range(8)]
    incoming = b"".join(
      (
        exchange_response("+ 2 1"),
        exchange_response("+"),
        exchange_response("+ 0 1"),
        exchange_response("+"),
        exchange_response("+ -67"),
        measurement_response(*rows, "+ INFO"),
        exchange_response("+ 24.5"),
        exchange_response("+ 6512"),
      )
    )
    driver, io = self.make_driver(incoming)
    result = (
      await driver.read_absorbance(self.plate, 450, read_height=10.27, orientation="portrait")
    )[0]

    self.assertEqual((len(result.data), len(result.data[0])), (8, 12))
    self.assertAlmostEqual(result.data[7][11], 0.081)  # type: ignore[arg-type]
    self.assertIn(
      "PLATE Temp 12770 8570 1427 1069 1405 1118 12 8 902 900 640 640 0 300 0 1027 1 1",
      host_payloads(io),
    )
    self.assertIn("SHIFT", host_payloads(io))
    self.assertIn(
      "ABS 0 1 450 1 8 1 1 0 0 0 0 0 4 1 1 0 O e INFO",
      host_payloads(io),
    )

  async def test_absorbance_fill_preserves_points(self) -> None:
    scan_rows = [
      "+ " + " ".join(str(value) for value in range(scan_y * 60, (scan_y + 1) * 60))
      for scan_y in range(5)
    ]
    incoming = b"".join(
      (
        exchange_response("+ 2 1"),
        exchange_response("+"),
        exchange_response("+"),
        exchange_response("+ -67"),
        measurement_response(*scan_rows, "+ INFO"),
        exchange_response("+ 25.0"),
        exchange_response("+ 6512"),
      )
    )
    driver, _ = self.make_driver(incoming)
    result = (
      await driver.read_absorbance(
        self.plate,
        450,
        wells=[self.plate.get_well("C4")],
        well_scan=WellScanSettings(pattern="fill", density=5),
        read_height=10.27,
      )
    )[0]
    self.assertEqual(len(result.scan_points["C4"]), 21)
    self.assertAlmostEqual(result.scan_points["C4"][0].value, 16 / 1000)
    self.assertEqual(
      len({(point.grid_column, point.grid_row) for point in result.scan_points["C4"]}),
      21,
    )
    positions = [(point.grid_column, point.grid_row) for point in result.scan_points["C4"]]
    self.assertEqual(positions[:3], [(1, 0), (2, 0), (3, 0)])
    self.assertEqual(positions[-3:], [(1, 4), (2, 4), (3, 4)])
    self.assertIn((2, 2), positions)
    self.assertAlmostEqual(result.data[2][3], 0.137)  # type: ignore[arg-type]

  async def test_horizontal_scan_preserves_grid_indices_and_mean(self) -> None:
    """Scan positions are integer sample indices, with unchanged optical densities."""
    row = "+ " + " ".join(str(value) for value in range(36))
    incoming = b"".join(
      (
        exchange_response("+ 2 1"),
        exchange_response("+"),
        exchange_response("+"),
        exchange_response("+ -67"),
        measurement_response(row, "+ INFO"),
        exchange_response("+ 25.0"),
        exchange_response("+ 6512"),
      )
    )
    driver, _ = self.make_driver(incoming)
    result = (
      await driver.read_absorbance(
        self.plate,
        450,
        wells=[self.plate.get_well("A2")],
        well_scan=WellScanSettings(pattern="horizontal", density=3),
        read_height=10.27,
      )
    )[0]
    self.assertEqual(
      [(point.grid_column, point.grid_row) for point in result.scan_points["A2"]],
      [(0, 0), (1, 0), (2, 0)],
    )
    self.assertEqual([point.value for point in result.scan_points["A2"]], [0.003, 0.004, 0.005])
    self.assertAlmostEqual(result.data[0][1], 0.004)  # type: ignore[arg-type]

  def test_fill_density_28_matches_softmax_616_point_mask(self) -> None:
    layout = FilterMaxF5._scan_layout(WellScanSettings(pattern="fill", density=28))
    self.assertEqual(len(layout), 616)

  async def test_absorbance_kinetic_uses_captured_countdown(self) -> None:
    row = "+ " + " ".join("100" for _ in range(12))
    intermediate_cycle = measurement_response(row) + exchange_response("+ 25.0")
    final_cycle = measurement_response(row, "+ INFO") + exchange_response("+ 25.0")
    incoming = b"".join(
      (
        exchange_response("+ 2 1"),
        exchange_response("+"),
        exchange_response("+"),
        exchange_response("+ -67"),
        intermediate_cycle,
        intermediate_cycle,
        final_cycle,
        exchange_response("+ 6512"),
      )
    )
    driver, io = self.make_driver(incoming)
    results = await driver.read_absorbance(
      self.plate,
      450,
      wells=[self.plate.get_well("C4")],
      kinetic=KineticTiming(interval=0.001, reads=3),
      read_height=10.27,
    )
    self.assertEqual(len(results), 3)
    self.assertEqual([result.elapsed_time for result in results], [0.0, 0.001, 0.002])
    commands = [payload for payload in host_payloads(io) if payload.startswith("ABS ")]
    self.assertEqual([command.split()[-5] for command in commands], ["3", "2", "1"])

  async def test_luminescence_kinetic_info_arrives_only_after_final_cycle(self) -> None:
    row = "+ " + " ".join("0" for _ in range(12))
    incoming = b"".join(
      (
        exchange_response("+ 2 1"),
        exchange_response("+"),
        exchange_response("+"),
        exchange_response("+ -67"),
        measurement_response(row),
        exchange_response("+ 25.0"),
        measurement_response(row),
        exchange_response("+ 25.0"),
        measurement_response(row, "+ INFO"),
        exchange_response("+ 25.0"),
        exchange_response("+ 6512"),
      )
    )
    driver, io = self.make_driver(incoming)
    results = await driver.read_luminescence(
      self.plate,
      wells=[self.plate.get_well("C4")],
      kinetic=KineticTiming(interval=0.001, reads=3),
    )
    self.assertEqual(len(results), 3)
    self.assertEqual([result.elapsed_time for result in results], [0.0, 0.001, 0.002])
    commands = [payload for payload in host_payloads(io) if payload.startswith("LUM ")]
    self.assertEqual([command.split()[-4] for command in commands], ["3", "2", "1"])

  async def test_malformed_values_after_final_info_still_eject_tray(self) -> None:
    incoming = b"".join(
      (
        exchange_response("+ 2 1"),
        exchange_response("+"),
        exchange_response("+"),
        exchange_response("+ -67"),
        measurement_response("+ 1 2", "+ INFO"),
        exchange_response("+ 6512"),
      )
    )
    driver, io = self.make_driver(incoming)
    with self.assertRaisesRegex(FilterMaxProtocolError, "Expected 12 absorbance values"):
      await driver.read_absorbance(
        self.plate, 450, wells=[self.plate.get_well("C4")], read_height=10.27
      )
    self.assertEqual(host_payloads(io)[-1], "E P")
    self.assertNotIn("STOP", host_payloads(io))

  async def test_captured_e62_is_preserved_in_session_error_log(self) -> None:
    error = "- E62: ABS Led 7 ADCValue: 16277 Gain 255 LedHighPoti 44 LedLowPoti 255"
    incoming = b"".join(
      (
        exchange_response("+ 2 1"),
        exchange_response("+"),
        exchange_response("+"),
        exchange_response("+ -67"),
        measurement_response(error),
        exchange_response("+ 6512"),
      )
    )
    driver, _ = self.make_driver(incoming)
    with self.assertRaisesRegex(FilterMaxDeviceError, "E62"):
      await driver.read_absorbance(
        self.plate,
        450,
        wells=[self.plate.get_well("C4")],
        reference_wavelength=595,
        read_height=10.27,
      )
    errors = await driver.get_error_log()
    self.assertEqual(
      (errors[0].code, errors[0].command),
      (62, "ABS 1 2 450 595 3 3 1 1 0 0 0 0 0 3 1 1 0 O e INFO"),
    )

  async def test_lum_dual_returns_raw_channels(self) -> None:
    channel_1 = "+ " + " ".join(str(index) for index in range(12))
    channel_2 = "+ " + " ".join(str(index + 20) for index in range(12))
    incoming = b"".join(
      (
        exchange_response("+ 2 1"),
        exchange_response("+"),
        exchange_response("+"),
        exchange_response("+ -67"),
        measurement_response(channel_1, channel_2, "+ INFO"),
        exchange_response("+ 25.0"),
        exchange_response("+ 25.0"),
        exchange_response("+ 6512"),
      )
    )
    driver, io = self.make_driver(incoming)
    results = await driver.read_luminescence(
      self.plate, wells=[self.plate.get_well("C4")], channels=2
    )
    self.assertEqual([result.data[2][3] for result in results], [3, 23])
    self.assertIn(
      "LUM 1 2 0 0 3 3 1 1 0 0 0 400000 0 3 1 1 0 O INFO",
      host_payloads(io),
    )
    self.assertIn(
      "SF B 1 595 35 2 535 25 2 535 25 4 535 25 4 625 35 10 0 0 16",
      host_payloads(io),
    )

  async def test_portrait_luminescence_is_rejected_before_transmission(self) -> None:
    driver, io = self.make_driver(b"")
    with self.assertRaisesRegex(FilterMaxUnsupportedOperationError, "only for absorbance"):
      await driver.read_luminescence(self.plate, orientation="portrait")
    self.assertEqual(io.writes, [])

  async def test_missing_excitation_slide_blocks_fluorescence(self) -> None:
    driver, io = self.make_driver(exchange_response("+ 2 1"))
    with self.assertRaises(FilterMaxSlideNotInstalledError):
      await driver.read_fluorescence(
        self.plate,
        excitation_wavelength=485,
        emission_wavelength=535,
      )
    self.assertNotIn("FL", " ".join(host_payloads(io)))

  async def test_measurement_requires_catalog_before_transmission(self) -> None:
    driver = FilterMaxF5("test", "COM1")
    io = FakeSerial(b"")
    driver.io = io  # type: ignore[assignment]
    driver._transport = FilterMaxTransport(io)  # type: ignore[arg-type]
    with self.assertRaises(FilterMaxCatalogError):
      await driver.read_absorbance(self.plate, 450, read_height=10.27)
    self.assertEqual(io.writes, [])
