import inspect
import json
import os

from flask import Flask, jsonify, render_template, request

import pylabrobot.resources as resources_module
from pylabrobot.resources import Resource

app = Flask(__name__, template_folder=".", static_folder=".")


FILENAME = "data.json"


@app.route('/')
def hello_world():
  return render_template("gui.html")


@app.route('/data')
def data():
  # with open("data.json", "r") as f:
  #   return f.read()
  return jsonify({'name': 'deck', 'type': 'OTDeck', 'size_x': 624.3, 'size_y': 565.2, 'size_z': 900, 'location': {'x': 0, 'y': 0, 'z': 0}, 'category': 'deck', 'children': [{'name': 'trash_container', 'type': 'Resource', 'size_x': 172.86, 'size_y': 165.86, 'size_z': 82, 'location': {'x': 265.0, 'y': 271.5, 'z': 0.0}, 'category': None, 'model': None, 'children': [{'name': 'trash', 'type': 'Trash', 'size_x': 172.86, 'size_y': 165.86, 'size_z': 82, 'location': {'x': 82.84, 'y': 53.56, 'z': 5}, 'category': None, 'model': None, 'children': [], 'parent_name': 'trash_container'}], 'parent_name': 'deck'}], 'parent_name': None})


@app.route("/plates")
def plates():
  return jsonify(["Cos_1536_10ul",
    "Cos_1536_10ul_L",
    "Cos_1536_10ul_P",
    "Cos_384_DW",
    "Cos_384_DW_L",
    "Cos_384_DW_P",
    "Cos_384_PCR",
    "Cos_384_PCR_L",
    "Cos_384_PCR_P",
    "Cos_384_Sq",
    "Cos_384_Sq_L",
    "Cos_384_Sq_P",
    "Cos_384_Sq_Rd",
    "Cos_384_Sq_Rd_L",
    "Cos_384_Sq_Rd_P",
    "Cos_96_DW_1mL",
    "Cos_96_DW_1mL_L",
    "Cos_96_DW_1mL_P",
    "Cos_96_DW_2mL",
    "Cos_96_DW_2mL_L",
    "Cos_96_DW_2mL_P",
    "Cos_96_DW_500ul",
    "Cos_96_DW_500ul_L",
    "Cos_96_DW_500ul_P",
    "Cos_96_EZWash",
    "Cos_96_EZWash_L",
    "Cos_96_EZWash_P",
    "Cos_96_FL",
    "Cos_96_Filter",
    "Cos_96_Filter_L",
    "Cos_96_Filter_P",
    "Cos_96_Fl_L",
    "Cos_96_Fl_P",
    "Cos_96_HalfArea",
    "Cos_96_HalfArea_L",
    "Cos_96_HalfArea_P",
    "Cos_96_PCR",
    "Cos_96_PCR_L",
    "Cos_96_PCR_P",
    "Cos_96_ProtCryst",
    "Cos_96_ProtCryst_L",
    "Cos_96_ProtCryst_P",
    "Cos_96_Rd",
    "Cos_96_Rd_L",
    "Cos_96_Rd_P",
    "Cos_96_SpecOps",
    "Cos_96_SpecOps_L",
    "Cos_96_SpecOps_P",
    "Cos_96_UV",
    "Cos_96_UV_L",
    "Cos_96_UV_P",
    "Cos_96_Vb",
    "Cos_96_Vb_L",
    "Cos_96_Vb_P"])

@app.route("/tip_racks")
def tip_racks():
  return jsonify(["Rick", "Rick Plate"])

@app.route("/resource/<resource_id>")
def resource(resource_id):
  resource_classes = [c[0] for c in inspect.getmembers(resources_module)]

  if resource_id not in resource_classes:
    return jsonify({"error": f"Resource '{resource_id}' not found."})

  resource_class = getattr(resources_module, resource_id)
  resource_name = request.args.get("name")
  resource = resource_class(name=resource_name)
  return jsonify(resource.serialize())


# catch all for static
@app.route('/<path:path>')
def static_proxy(path):
  return app.send_static_file(path)


@app.route("/save", methods=["POST"])
def save():
  data = request.get_json()

  try:
    deck = Resource.deserialize(data)
  except Exception as e:
    print(str(e))
    import traceback
    traceback.print_exc()
    return jsonify({"error": str(e), "success": False}), 400

  with open(FILENAME, "w") as f:
    serialized = deck.serialize()
    serialized = json.dumps(serialized, indent=2)
    f.write(serialized)
    path = os.path.abspath(FILENAME)
    print(path)

  return jsonify({"success": True})


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=5001, debug=True)
