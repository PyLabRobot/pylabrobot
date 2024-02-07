import datetime
import logging

from pylabrobot.__version__ import __version__

# Create a logger
logger = logging.getLogger("pylabrobot")
logger.setLevel(logging.DEBUG)

# Add a file handler
now = datetime.datetime.now().strftime("%Y%m%d")
fh = logging.FileHandler(f"pylabrobot-{now}.log")
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logger.addHandler(fh)
