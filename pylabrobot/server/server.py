import os

from flask import Flask
import wtforms_json

from liquid_handling_api import lh_api

app = Flask(__name__, )
app.register_blueprint(lh_api)
app.config["WTF_CSRF_ENABLED"] = False
wtforms_json.init()


@app.route('/')
def hello_world():
  return 'Hello, World!'


if __name__ == '__main__':
  app.run(debug=True, host=os.environ.get("HOST", "0.0.0.0"), port=os.environ.get("PORT", 5001))
