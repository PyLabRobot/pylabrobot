from flask import Blueprint, request, jsonify
from wtforms import Form
from wtforms import StringField, DecimalField, FieldList, FormField
from wtforms.validators import DataRequired, AnyOf

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends import Mock


lh_api = Blueprint("liquid handling", __name__, url_prefix="/api/v1/liquid_handling")

lh = LiquidHandler(backend=Mock())


class KWArg(Form):
  field = StringField('name', validators=[DataRequired()])
  value = StringField('value', validators=[DataRequired()])
  type = StringField('type', validators=[DataRequired(), AnyOf(['int', 'float', 'str'])])


@lh_api.route("/")
def index():
  return "Hello, World!"


@lh_api.route("/setup", methods=["POST"])
def setup():
  lh.setup()
  return jsonify({"status": "running"})

@lh_api.route("/stop", methods=["POST"])
def stop():
  lh.stop()
  return jsonify({"status": "stopped"})

@lh_api.route("/status", methods=["POST"])
def status():
  status = "running" if lh.setup_finished else "stopped"
  return jsonify({"status": status})


@lh_api.route("/labware", methods=["POST"])
def define_labware():
  data = request.get_json()
  lh.load_from_json(content=data)
  return jsonify({"status": "ok"})

class TipForm(Form):
  resource = StringField("resource", validators=[DataRequired()])
  channels = FieldList(StringField("channels", validators=[DataRequired()]), min_entries=1, max_entries=16)

@lh_api.route("/pick-up-tips", methods=["POST"])
def pick_up_tips():
  data = request.get_json()
  form = TipForm.from_json(data)

  if not form.validate():
    return jsonify({"error": form.errors}), 400

  if not lh.deck.has_resource(form.resource.data):
    return jsonify({"error": "resource not found"}), 404

  lh.pickup_tips(
    form.resource.data,
    *data["channels"],
    **data.get("kwargs", {})
  )

  return "OK"

@lh_api.route("/discard-tips", methods=["POST"])
def discard_tips():
  data = request.get_json()
  form = TipForm.from_json(data)

  if not form.validate():
    return jsonify({"error": form.errors}), 400

  if not lh.deck.has_resource(form.resource.data):
    return jsonify({"error": "resource not found"}), 404

  lh.discard_tips(
    form.resource.data,
    *data["channels"],
    **data.get("kwargs", {})
  )

  return "OK"

class AspirationSingleChannelForm(Form):
  well = StringField('well', validators=[DataRequired()])
  volume = DecimalField('volume', validators=[DataRequired()])

class AspirationForm(Form):
  resource = StringField("resource", validators=[DataRequired()])
  channels = FieldList(FormField(AspirationSingleChannelForm), min_entries=1, max_entries=16)
  kwargs = FieldList(FormField(KWArg))

@lh_api.route("/aspirate", methods=["POST"])
def aspirate():
  data = request.get_json()
  form = AspirationForm.from_json(data)

  if not form.validate():
    return jsonify({"error": form.errors}), 400

  if not lh.deck.has_resource(form.resource.data):
    return jsonify({"error": "resource not found"}), 404

  lh.aspirate(
    form.resource.data,
    *[(channel["well"], channel["volume"]) for channel in data["channels"]],
    **data.get("kwargs", {})
  )

  return "OK"

class DispenseSingleChannelForm(Form):
  well = StringField('well', validators=[DataRequired()])
  volume = DecimalField('volume', validators=[DataRequired()])

class DispenseForm(Form):
  resource = StringField("resource", validators=[DataRequired()])
  channels = FieldList(FormField(DispenseSingleChannelForm), min_entries=1, max_entries=16)
  kwargs = FieldList(FormField(KWArg))

@lh_api.route("/dispense", methods=["POST"])
def dispense():
  data = request.get_json()
  form = DispenseForm.from_json(data)

  if not form.validate():
    return jsonify({"error": form.errors}), 400

  if not lh.deck.has_resource(form.resource.data):
    return jsonify({"error": "resource not found"}), 404

  lh.dispense(
    form.resource.data,
    *[(channel["well"], channel["volume"]) for channel in data["channels"]],
    **data.get("kwargs", {})
  )

  return "OK"
