from setuptools import find_packages, setup

with open("pylabrobot/version.txt", "r", encoding="utf-8") as f:
  __version__ = f.read().strip()

with open("README.md", "r", encoding="utf-8") as f:
  long_description = f.read()


extras_fw = ["pyserial", "pyusb", "libusb_package<=1.0.26.2"]

extras_http = ["requests", "types-requests"]

extras_plate_reading = [
  "pylibftdi",
]

extras_websockets = ["websockets==15.0.1"]

extras_visualizer = extras_websockets

extras_opentrons = ["opentrons-http-api-client"]

extras_server = [
  "flask[async]",
]


extras_inheco = ["hid"]

extras_agrow = ["pymodbus==3.6.8"]

extras_dev = (
  extras_fw
  + extras_http
  + extras_plate_reading
  + extras_websockets
  + extras_visualizer
  + extras_opentrons
  + extras_server
  + extras_inheco
  + extras_agrow
  + [
    "pydata-sphinx-theme",
    "myst_nb",
    "sphinx_copybutton",
    "pytest",
    "pytest-timeout",
    "mypy",
    "responses",
    "sphinx-reredirects",
    "ruff==0.2.1",
    "nbconvert",
    "sphinx-sitemap",
  ]
)

# Some extras are not available on all platforms. `dev` should be available everywhere
extras_all = extras_dev

setup(
  name="PyLabRobot",
  version=__version__,
  packages=find_packages(exclude="tools"),
  description="A hardware agnostic platform for lab automation",
  long_description=long_description,
  long_description_content_type="text/markdown",
  install_requires=["typing_extensions"],
  url="https://github.com/pylabrobot/pylabrobot.git",
  package_data={"pylabrobot": ["visualizer/*", "version.txt"]},
  extras_require={
    "fw": extras_fw,
    "http": extras_http,
    "plate_reading": extras_plate_reading,
    "websockets": extras_websockets,
    "visualizer": extras_visualizer,
    "inheco": extras_inheco,
    "opentrons": extras_opentrons,
    "server": extras_server,
    "agrow": extras_agrow,
    "dev": extras_dev,
    "all": extras_all,
  },
  entry_points={
    "console_scripts": [
      "lh-server=pylabrobot.server.liquid_handling_server:main",
      "plr-gui=pylabrobot.gui.gui:main",
    ],
  },
)
