# mypy: disable-error-code = attr-defined

import os

from flask import Flask

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends import SerializingSavingBackend
from pylabrobot.liquid_handling.resources import STARLetDeck
from pylabrobot.server.liquid_handling_api import lh_api


def create_app(lh):
  app = Flask(__name__, )
  app.register_blueprint(lh_api)
  app.lh = lh

  return app


if __name__ == '__main__':
  lh = LiquidHandler(backend=SerializingSavingBackend(num_channels=8), deck=STARLetDeck())
  app = create_app(lh=lh)

  host = os.environ.get("HOST", "0.0.0.0")
  port = int(os.environ.get("PORT", 5001))
  app.run(debug=True, host=host, port=port)
