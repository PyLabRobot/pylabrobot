import os

from flask import Flask
import wtforms_json

from pylabrobot.server.liquid_handling_api import lh_api

app = Flask(__name__, )
app.register_blueprint(lh_api)
app.config["WTF_CSRF_ENABLED"] = False
wtforms_json.init()


@app.route('/')
def hello_world():
  return "Hello, World!"


if __name__ == '__main__':
  host = os.environ.get("HOST", "0.0.0.0")
  port = int(os.environ.get("PORT", 5001))
  app.run(debug=True, host=host, port=port)
