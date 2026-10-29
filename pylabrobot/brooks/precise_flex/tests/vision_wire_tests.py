"""What every vision method sends and returns, on the controller and both vision server connections.

A fake network answers at the socket, by port: the controller on :10100, the vision server's
property protocol on :1450 and its image stream on :1500. Every reply is one the arm gave.
"""

import io
import unittest
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, patch

from pylabrobot.brooks.precise_flex import PreciseFlexDriver
from pylabrobot.brooks.precise_flex.driver.errors import PreciseFlexVisionError
from pylabrobot.brooks.precise_flex.driver.features.vision import (
  PreciseFlexVision,
  StereoParameters,
  VisionConfiguration,
)
from pylabrobot.io.socket import Socket

from .wire_tests import _REPLIES

try:
  from PIL import Image as _PILImage
except ImportError:
  _PILImage = None  # type: ignore[assignment]

_CONTROLLER_PORT, _VISION_SERVER_PORT, _IMAGE_PORT = 10100, 1450, 1500
_LINK = {_CONTROLLER_PORT: "controller", _VISION_SERVER_PORT: "vision", _IMAGE_PORT: "image"}

_STEREO_PARAMETERS = "unknown_process unknown_tool 150 0.5 4 2 3 63 2 1000 "
_CONTROLLER_REPLIES: Dict[str, str] = {
  **_REPLIES,
  "StereoParam 1 1": f"0 {_STEREO_PARAMETERS}",
  "StereoLocate 1 1": "-4030",
  "VToolProperty System Info": "-4016",
  "Vprocess Camera1": "-4012 0",  # the arm's answer to a process that failed (`Vprocess snap`)
}

_ACQUIRE_PROPERTIES = (
  "acquiremode activelayer monochromedisplay acquirepath acquireprefix acquiremaxsaveimages "
  "acquireextension jpegquality backlightcompensation brightness contrast hue saturation "
  "sharpness gamma whitebalance gain exposure cameranumber name defaultdisplayresults "
  "tooldisplayresults"
)
_ARUCO_PROPERTIES = (
  "width height relativetoolname adaptivethreshold cornerrefinement cornerrefinementwindowsize "
  "cornerrefinementmaxiterations cornerrefinementminaccuracy dictionaryconfiguration "
  "calculateintensity showresults resultdisplaycolor resultdisplayscale cameranumber name xpos "
  "ypos defaultdisplayresults tooldisplayresults"
)
_BARCODE_PROPERTIES = (
  "width height relativetoolname showresults resultdisplaycolor showbarcodetext resultfontsize "
  "findall codabar code39 code93 code128 datamatrix pdf417 ean8 ean13 itf maxicode aztec qrcode "
  "rss14 upca upce upceanextensions imb pharmacode cameranumber name xpos ypos "
  "defaultdisplayresults tooldisplayresults"
)
_LED_PROPERTIES = "brightness delay bank cameranumber name defaultdisplayresults tooldisplayresults"
_SHARPNESS_PROPERTIES = (
  "width height relativetoolname upperthreshold lowerthreshold showresults resultdisplaycolor "
  "resultdisplayscale cameranumber name xpos ypos defaultdisplayresults tooldisplayresults"
)
# The tools of the arm's active project, in the order it listed them: name -> (type, properties).
_TOOLS: Dict[str, Tuple[str, str]] = {
  "acq1": ("Acquire", _ACQUIRE_PROPERTIES),
  "acq2": ("Acquire", _ACQUIRE_PROPERTIES),
  "aruco1": ("FiducialLocator", _ARUCO_PROPERTIES),
  "aruco2": ("FiducialLocator", _ARUCO_PROPERTIES),
  "barcode_read1": ("BarcodeRead", _BARCODE_PROPERTIES),
  "barcode_read2": ("BarcodeRead", _BARCODE_PROPERTIES),
  "led": ("LightControl", _LED_PROPERTIES),
  "sharpness_detector1": ("SharpnessDetector", _SHARPNESS_PROPERTIES),
  "sharpness_detector2": ("SharpnessDetector", _SHARPNESS_PROPERTIES),
}
_TOOL_TYPES = (
  "ObjectFinder Classifier BarcodeRead Acquire ArcFitter ClearGrip ComputedLine "
  "ComputeIntersection ComputePointOnLine EdgeFinder SharpnessDetector FindBlob FindMid FixedFrame "
  "ImageProcess FiducialLocator LightControl LineFitter PixelWindow PixelWindowColor PointFinder "
  "SensorWindow"
)
_RESOLUTIONS = (
  "160x120 320x240 640x480 800x600 1024x768 1280x720 1280x960 1600x1200 1920x1080 2048x1536 "
  "2592x1944"
)
_PROJECTS = "arucos_cam1,arucos_cam2,VisionTest,vision_project"
_PROCESSES = "Camera1,Camera2,LightControl"
_VISION_SERVER_REPLIES: Dict[str, str] = {
  "property get system.listtools": "0 " + " ".join(_TOOLS),
  **{f"property get system.tooltype {t}": f"0 {kind}" for t, (kind, _) in _TOOLS.items()},
  **{f"property get system.toolproperties {t}": f"0 {p}" for t, (_, p) in _TOOLS.items()},
  "property get system.cameracount": "0 2",
  **{f"property get system.cameraname {c}": f"0 Cam{c}" for c in (1, 2)},
  **{f"property get system.cameratype {c}": "0 DirectShow" for c in (1, 2)},
  **{f"property get system.cameraframewidth {c}": "0 2592" for c in (1, 2)},
  **{f"property get system.cameraframeheight {c}": "0 1944" for c in (1, 2)},
  **{f"property get system.cameraresolutions {c}": f"0 {_RESOLUTIONS}" for c in (1, 2)},
  "property get system.engineversion": "0 5.3.3.0",
  "property get system.islicensed": "0 True",
  "property get system.tooltypes": f"0 {_TOOL_TYPES}",
  "property get system.listprojects": f"0 {_PROJECTS}",
  "property get system.projectname": "0 VisionTest",
  "property get system.listprocesses": f"0 {_PROCESSES}",
}


def _result(name: str, data: bytes) -> bytes:
  """One result as the vision server frames it on :1500."""
  name_bytes = name.encode("ascii")
  header = bytes([0x01, len(name_bytes), 0, 0, 0]) + len(data).to_bytes(4, "little") + bytes(7)
  return header + name_bytes + data


def _jpeg(colour: Tuple[int, int, int]) -> bytes:
  """A 2x2 JPEG frame of one colour."""
  assert _PILImage is not None
  out = io.BytesIO()
  _PILImage.new("RGB", (2, 2), colour).save(out, format="JPEG")
  return out.getvalue()


class _FakeNetwork:
  """Answers every socket by port and logs each line written, tagged with its link."""

  def __init__(self) -> None:
    self.sent: List[str] = []
    self.image_chunks: List[bytes] = []
    # Pushed onto the image stream when a camera acquire is triggered, as the vision server does.
    self.frames_on_acquire: List[bytes] = []
    self.controller_replies = dict(_CONTROLLER_REPLIES)
    self.unreachable: List[int] = []
    self._pending: Dict[int, List[str]] = {}

  def install(self, test: unittest.TestCase) -> None:
    network = self

    async def setup(sock: Socket) -> None:
      if sock._port in network.unreachable:
        raise ConnectionRefusedError(sock._port)
      network.sent.append(f"{_LINK[sock._port]}: <open>")

    async def stop(sock: Socket) -> None:
      network.sent.append(f"{_LINK[sock._port]}: <close>")

    async def write(sock: Socket, data: bytes, timeout: Optional[float] = None) -> None:
      line = data.decode().strip()
      network.sent.append(f"{_LINK[sock._port]}: {line}")
      network._pending.setdefault(sock._port, []).append(line)
      if "system.cameraacquire" in line:
        network.image_chunks.extend(network.frames_on_acquire)

    async def readline(sock: Socket, timeout: Optional[float] = None) -> bytes:
      line = network._pending[sock._port].pop(0)
      vision = sock._port == _VISION_SERVER_PORT
      replies = _VISION_SERVER_REPLIES if vision else network.controller_replies
      return (replies.get(line, "0") + "\r\n").encode()

    async def read(sock: Socket, num_bytes: int = 128, timeout: Optional[float] = None) -> bytes:
      network.sent.append(f"{_LINK[sock._port]}: <read>")
      return network.image_chunks.pop(0) if network.image_chunks else b""

    for name, fn in (
      ("setup", setup),
      ("stop", stop),
      ("write", write),
      ("readline", readline),
      ("read", read),
    ):
      p = patch.object(Socket, name, fn)
      p.start()
      test.addCleanup(p.stop)


def _vision(arm: PreciseFlexDriver) -> PreciseFlexVision:
  """The vision feature of an arm set up with its vision server."""
  assert arm.vision is not None
  return arm.vision


class _Unchecked:
  """What a call returns, where no reply the arm gave stands behind it."""


_UNCHECKED = _Unchecked()

_Case = Tuple[str, Callable[[PreciseFlexDriver], Awaitable[Any]], List[str], Any]

# (name, call, every line sent tagged with its link, the value returned or the exception raised),
# on an arm set up with its vision server.
_CASES: List[_Case] = [
  (
    "request_vision_tool_property",
    lambda arm: _vision(arm).request_vision_tool_property("System", "CameraCount"),
    ["controller: VToolProperty System CameraCount"],
    "2",
  ),
  (
    "request_vision_tool_property, an unknown property",
    lambda arm: _vision(arm).request_vision_tool_property("System", "Info"),
    ["controller: VToolProperty System Info"],
    PreciseFlexVisionError,
  ),
  (
    "request_camera_count",
    lambda arm: _vision(arm).request_camera_count(),
    ["controller: VToolProperty System CameraCount"],
    2,
  ),
  (
    "request_vision_version",
    lambda arm: _vision(arm).request_vision_version(),
    ["vision: property get system.engineversion"],
    "5.3.3.0",
  ),
  (
    "request_is_licensed",
    lambda arm: _vision(arm).request_is_licensed(),
    ["vision: property get system.islicensed"],
    True,
  ),
  (
    "request_projects",
    lambda arm: _vision(arm).request_projects(),
    ["vision: property get system.listprojects"],
    _PROJECTS.split(","),
  ),
  (
    "request_project_name",
    lambda arm: _vision(arm).request_project_name(),
    ["vision: property get system.projectname"],
    "VisionTest",
  ),
  (
    "request_processes",
    lambda arm: _vision(arm).request_processes(),
    ["vision: property get system.listprocesses"],
    _PROCESSES.split(","),
  ),
  (
    "request_vision_tools",
    lambda arm: _vision(arm).request_vision_tools(),
    ["vision: property get system.listtools"],
    list(_TOOLS),
  ),
  (
    "enumerate_project",
    lambda arm: _vision(arm).enumerate_project(),
    ["vision: property get system.listprocesses", "vision: property get system.listtools"],
    {"processes": sorted(_PROCESSES.split(",")), "vision_tools": sorted(_TOOLS)},
  ),
  (
    "request_camera_name_front",
    lambda arm: _vision(arm).request_camera_name(),
    ["vision: property get system.cameraname 1"],
    "Cam1",
  ),
  (
    "request_camera_name_bottom",
    lambda arm: _vision(arm).request_camera_name("bottom"),
    ["vision: property get system.cameraname 2"],
    "Cam2",
  ),
  (
    "request_camera_type",
    lambda arm: _vision(arm).request_camera_type(),
    ["vision: property get system.cameratype 1"],
    "DirectShow",
  ),
  (
    "request_camera_width",
    lambda arm: _vision(arm).request_camera_width(),
    ["vision: property get system.cameraframewidth 1"],
    2592,
  ),
  (
    "request_camera_height",
    lambda arm: _vision(arm).request_camera_height(),
    ["vision: property get system.cameraframeheight 1"],
    1944,
  ),
  (
    "request_camera_resolutions",
    lambda arm: _vision(arm).request_camera_resolutions(),
    ["vision: property get system.cameraresolutions 1"],
    _RESOLUTIONS.split(),
  ),
  (
    "request_vision_tool_property_value",
    lambda arm: _vision(arm).request_vision_tool_property_value("acq1", "exposure"),
    ["vision: property get acq1.exposure"],
    _UNCHECKED,
  ),
  (
    "request_vision_tool_properties",
    lambda arm: _vision(arm).request_vision_tool_properties("led"),
    ["vision: property get system.toolproperties led"],
    _LED_PROPERTIES.split(),
  ),
  (
    "request_vision_tool_property_info",
    lambda arm: _vision(arm).request_vision_tool_property_info("acq1", "exposure"),
    ["vision: property get system.toolpropertyinfo acq1 exposure"],
    _UNCHECKED,
  ),
  (
    "request_vision_tool_type",
    lambda arm: _vision(arm).request_vision_tool_type("acq1"),
    ["vision: property get system.tooltype acq1"],
    "Acquire",
  ),
  (
    "request_vision_tool_types",
    lambda arm: _vision(arm).request_vision_tool_types(),
    ["vision: property get system.tooltypes"],
    _TOOL_TYPES.split(),
  ),
  (
    "start_led_controller",
    lambda arm: _vision(arm).start_led("front", 80, delay=5),
    [
      "controller: VToolProperty led Bank 1",
      "controller: VToolProperty led Brightness 80",
      "controller: VToolProperty led Delay 5",
      "controller: Vprocess LightControl",
    ],
    None,
  ),
  (
    "start_led_vision",
    lambda arm: _vision(arm).start_led("bottom", 60, delay=5, use_server="vision"),
    [
      "vision: property set led.bank 2",
      "vision: property set led.brightness 60",
      "vision: property set led.delay 5",
      "vision: property set system.runtool led",
    ],
    None,
  ),
  (
    "start_led, an unknown camera",
    lambda arm: _vision(arm).start_led("left"),  # type: ignore[arg-type]
    [],
    ValueError,
  ),
  (
    "stop_led_controller",
    lambda arm: _vision(arm).stop_led(),
    [
      "controller: VToolProperty led Bank 1",
      "controller: VToolProperty led Brightness 0",
      "controller: Vprocess LightControl",
    ],
    None,
  ),
  (
    "stop_led_vision",
    lambda arm: _vision(arm).stop_led("bottom", use_server="vision"),
    [
      "vision: property set led.bank 2",
      "vision: property set led.brightness 0",
      "vision: property set system.runtool led",
    ],
    None,
  ),
  (
    "capture_image, the stream ends",
    lambda arm: _vision(arm).capture_image("front"),
    ["image: <read>", "vision: property set system.cameraacquire 1", "image: <read>"],
    RuntimeError,
  ),
  (
    "save_image",
    lambda arm: _vision(arm).save_image("bottom", acquire_prefix="p", acquire_path="/rd"),
    [
      "controller: VToolProperty acq2 acquiremode ACQUIRE_AND_SAVE",
      "controller: VToolProperty acq2 acquirepath /rd",
      "controller: VToolProperty acq2 acquireprefix p",
      "controller: Vprocess Camera2",
      "controller: VToolProperty acq2 acquiremode NORMAL_ACQUIRE",
    ],
    _UNCHECKED,
  ),
  (
    "save_image, the process fails",
    lambda arm: _vision(arm).save_image("front"),
    [
      "controller: VToolProperty acq1 acquiremode ACQUIRE_AND_SAVE",
      "controller: Vprocess Camera1",
      "controller: VToolProperty acq1 acquiremode NORMAL_ACQUIRE",
    ],
    PreciseFlexVisionError,
  ),
  (
    "save_image, an unknown camera",
    lambda arm: _vision(arm).save_image("sideways"),  # type: ignore[arg-type]
    [],
    ValueError,
  ),
  (
    "set_camera_setting",
    lambda arm: _vision(arm)._set_camera_setting("front", "exposure", 30),
    ["vision: property set acq1.exposure 30", "vision: property set system.runtool acq1"],
    None,
  ),
  (
    "set_barcode_symbologies",
    lambda arm: _vision(arm).set_barcode_symbologies("barcode_read1", ["code128", "qrcode"]),
    [
      "vision: property set barcode_read1.code128 true",
      "vision: property set barcode_read1.qrcode true",
    ],
    None,
  ),
  (
    "read_barcode",
    lambda arm: _vision(arm).read_barcode("Barcode"),
    ["controller: Vprocess Barcode", "controller: VresultInfoString barcode_read1 1"],
    _UNCHECKED,
  ),
  (
    "request_stereo_parameters",
    lambda arm: _vision(arm).request_stereo_parameters(),
    ["controller: StereoParam 1 1"],
    StereoParameters("unknown_process", "unknown_tool", 150.0, 0.5, 4, 2, 3, 63.0, 2.0, 1000),
  ),
  (
    "set_stereo_parameters",
    lambda arm: _vision(arm).set_stereo_parameters(StereoParameters.from_reply(_STEREO_PARAMETERS)),
    ["controller: StereoParam 1 1 unknown_process unknown_tool 150.0 0.5 4 2 3 63.0 2.0 1000"],
    None,
  ),
  (
    "locate_target, nothing located",
    lambda arm: _vision(arm).locate_target(),
    ["controller: StereoLocate 1 1"],
    PreciseFlexVisionError,
  ),
]

_DISCOVERY: List[str] = [
  "vision: property get system.listtools",
  *(
    f"vision: property get system.{read} {t}"
    for t in _TOOLS
    for read in ("tooltype", "toolproperties")
  ),
  "vision: property get system.cameracount",
  *(
    f"vision: property get system.{read} {c}"
    for c in (1, 2)
    for read in (
      "cameraname",
      "cameratype",
      "cameraframewidth",
      "cameraframeheight",
      "cameraresolutions",
    )
  ),
  "vision: property get system.engineversion",
  "vision: property get system.islicensed",
  "vision: property get system.tooltypes",
  "vision: property get system.listprojects",
  "vision: property get system.projectname",
  "vision: property get system.listprocesses",
]

_SETUP_WITHOUT_VISION: List[str] = [
  "controller: <open>",
  "controller: mode 0",
  "controller: hp 1 20",
  "controller: attach 1",
  "controller: freemode -1",
  "controller: pd 16078",
  "controller: pd 16077",
  "controller: pd 2003",
  "controller: pd 2002",
  "controller: version",
  "controller: VToolProperty System CameraCount",
  "controller: pd 2700",
  "controller: pd 2702",
  "controller: pd 2704",
  "controller: pd 2705",
  "controller: pd 2706",
  "controller: pd 16050",
  "controller: pd 16051",
  "controller: pd 100",
  "controller: pd 101",
  "controller: pd 102",
  "controller: pd 103",
  "controller: pd 110",
  "controller: pd 116",
  "controller: pd 2000",
  "controller: pd 2004",
  "controller: pd 16076",
  "controller: pd 16075",
  "controller: pd 2701",
  "controller: pd 2703",
  "controller: sysState",
  "controller: wherej",
  "controller: wherej",
  "controller: wherej",
  "controller: wherej",
  "controller: home",
  "controller: wherej",
  "controller: wherej",
  "controller: wherej",
  "controller: wherej",
  "controller: pd 2800",
  "controller: wherej",
  "controller: wherej",
  "controller: wherej",
  "controller: wherej",
]

_SETUP: List[str] = _SETUP_WITHOUT_VISION + ["vision: <open>", "image: <open>"] + _DISCOVERY

_STOP: List[str] = [
  "controller: attach 0",
  "controller: hp 0",
  "controller: exit",
  "image: <close>",
  "vision: <close>",
  "controller: <close>",
]


class TestPreciseFlexVisionWire(unittest.IsolatedAsyncioTestCase):
  """Each vision method sends exactly what it sends today, on the link it sends it on."""

  def setUp(self) -> None:
    sleep = patch(
      "pylabrobot.brooks.precise_flex.driver.features.arm.asyncio.sleep", new=AsyncMock()
    )
    sleep.start()
    self.addCleanup(sleep.stop)
    self.network = _FakeNetwork()
    self.network.install(self)

  async def _arm(self, vision_host: Optional[str] = "vision-server", **kwargs) -> PreciseFlexDriver:
    arm = PreciseFlexDriver(
      host="pf400",
      gripper_length=162.0,
      gripper_z_offset=0.0,
      closed_gripper_position=80.0,
      vision_host=vision_host,
    )
    await arm.setup(**kwargs)
    return arm

  async def test_setup_with_vision(self):
    arm = await self._arm()
    self.assertEqual(self.network.sent, _SETUP)
    config = _vision(arm).configuration
    self.assertEqual(
      {name: tool.type for name, tool in config.vision_tools.items()},
      {name: kind for name, (kind, _) in _TOOLS.items()},
    )
    self.assertEqual(
      [(camera.name, camera.width, camera.height) for camera in config.cameras.values()],
      [("Cam1", 2592, 1944), ("Cam2", 2592, 1944)],
    )
    self.assertEqual(
      (
        config.vision_version,
        config.licensed,
        config.active_project,
        len(config.vision_tool_types),
      ),
      ("5.3.3.0", True, "VisionTest", 22),
    )

  async def test_setup_without_the_vision_server(self):
    without_module = _REPLIES["version"].split(", IntelliGuide")[0]
    for name, kwargs, vision_built in (
      ("skip_vision", {"skip_vision": True}, False),
      ("no IntelliGuide module", {}, False),
      ("vision server unreachable", {}, True),
      ("no vision_host", {"vision_host": None}, True),
    ):
      with self.subTest(name):
        self.network.sent.clear()
        self.network.controller_replies = dict(_CONTROLLER_REPLIES)
        self.network.unreachable = []
        if name == "no IntelliGuide module":
          self.network.controller_replies["version"] = without_module
        if name == "vision server unreachable":
          self.network.unreachable = [_VISION_SERVER_PORT]
        arm = await self._arm(**kwargs)
        self.assertEqual([c for c in self.network.sent if not c.startswith("controller")], [])
        self.assertEqual(arm.vision is not None, vision_built)
        if arm.vision is not None:
          self.assertFalse(arm.vision.configuration.discovered)

  async def test_without_a_vision_server_its_methods_raise(self):
    arm = await self._arm(vision_host=None)
    self.network.sent.clear()
    for call in (
      lambda: _vision(arm).request_vision_version(),
      lambda: _vision(arm).capture_image(1),
      lambda: _vision(arm).start_led(use_server="vision"),
    ):
      with self.assertRaisesRegex(RuntimeError, "pass vision_host at setup"):
        await call()
    self.assertEqual(self.network.sent, [])

  async def test_stop_closes_the_vision_server(self):
    arm = await self._arm()
    self.network.sent.clear()
    await arm.stop()
    self.assertEqual(self.network.sent, _STOP)

  async def test_a_method_needs_its_tool_type(self):
    for name, configuration, sent in (
      ("discovered without it", VisionConfiguration(discovered=True), []),
      ("not discovered", VisionConfiguration(), ["controller: Vprocess Barcode"]),
    ):
      with self.subTest(name):
        arm = await self._arm()
        _vision(arm).configuration = configuration
        self.network.sent.clear()
        if sent:
          await _vision(arm).read_barcode("Barcode")
        else:
          with self.assertRaisesRegex(RuntimeError, "'BarcodeRead' vision tool type"):
            await _vision(arm).read_barcode("Barcode")
        self.assertEqual(self.network.sent[:1], sent)

  @unittest.skipIf(_PILImage is None, "Pillow is not installed")
  async def test_capture_image_returns_the_wanted_frame(self):
    arm = await self._arm()
    self.network.sent.clear()
    self.network.image_chunks = [_result("Primary Image [1]", b"stale")]  # left by a tool run
    self.network.frames_on_acquire = [
      _result("Primary Image [2]", _jpeg((0, 0, 200))),
      _result("Primary Image [1]", _jpeg((200, 0, 0))),
    ]
    frame = await _vision(arm).capture_image("front")
    self.assertEqual(frame.shape, (2, 2, 3))  # height x width x RGB
    self.assertGreater(frame[..., 0].mean(), frame[..., 2].mean())  # camera 1's red frame
    self.assertEqual(
      self.network.sent,
      [
        "image: <read>",
        "image: <read>",
        "vision: property set system.cameraacquire 1",
        "image: <read>",
        "image: <read>",
      ],
    )

  async def test_every_vision_method(self):
    for name, call, expected, outcome in _CASES:
      with self.subTest(name):
        arm = await self._arm()
        self.network.sent.clear()
        if isinstance(outcome, type) and issubclass(outcome, Exception):
          with self.assertRaises(outcome):
            await call(arm)
        else:
          returned = await call(arm)
          if outcome is not _UNCHECKED:
            self.assertEqual(returned, outcome)
        self.assertEqual(self.network.sent, expected)


if __name__ == "__main__":
  unittest.main()
