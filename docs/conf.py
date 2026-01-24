# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
import os
import shutil
import sys

sys.path.insert(0, os.path.abspath(".."))
# Allow importing local Sphinx extensions (e.g., pylabrobot_cards)
sys.path.append(os.path.abspath("./_exts"))

# -- Project information -----------------------------------------------------

project = "PyLabRobot"
copyright = "2025, PyLabRobot"
author = "The PyLabRobot authors"

# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
  "sphinx.ext.napoleon",
  "sphinx.ext.autodoc",
  "pylabrobot_cards",  # NEW: PLR cards (plrcard/plrcardgrid + compat)
  "sphinx.ext.autosummary",
  "sphinx.ext.autosectionlabel",
  "sphinx.ext.intersphinx",
  "sphinx.ext.mathjax",
  "myst_nb",
  "sphinx_copybutton",
  "IPython.sphinxext.ipython_console_highlighting",
  "sphinx_reredirects",
  "sphinx_sitemap",
  "sphinx_tabs.tabs"
]

intersphinx_mapping = {
  "python": ("https://docs.python.org/3/", None),
}

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = [
  "build/*",
  "_templates",
  "Thumbs.db",
  ".DS_Store",
  "jupyter_execute",
]

autodoc_default_options = {
  # 'members': False,
  # 'undoc-members': False,
  "show-inheritance": True,
  # 'special-members': '__init__,__getitem__',
  "exclude-members": "__weakref__",
}

default_role = "code"  # allow single backticks for inline code

# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = "pydata_sphinx_theme"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = [
  "_static",
  "resources/library/img",
]
html_extra_path = ["resources/library/img"]

# --- PLR cards assets (CSS/JS) ---
# Ensure lists exist, then append our assets without clobbering existing values.
html_css_files = list(globals().get("html_css_files", []))
if "plr_cards.css" not in html_css_files:
  html_css_files.append("plr_cards.css")  # served from _static/plr_cards.css

html_js_files = list(globals().get("html_js_files", []))
if "plr_cards.js" not in html_js_files:
  html_js_files.append("plr_cards.js")    # served from _static/plr_cards.js

# NOTE: templates_path already includes "_templates", which is where
#       plr_card_grid.html should live.

html_theme_options = {
  "show_nav_level": 1,
  "use_edit_page_button": True,
  "navbar_start": ["navbar-logo"],
  "navbar_center": ["navbar-nav"],
  "navbar_end": ["theme-switcher", "navbar-icon-links"],
  "navbar_persistent": ["search-button"],
  "icon_links": [
    {
      "name": "X",
      "url": "https://x.com/pylabrobot",
      "icon": "fa-brands fa-x-twitter",
    },
    {
      "name": "GitHub",
      "url": "https://github.com/pylabrobot/pylabrobot",
      "icon": "fa-brands fa-github",
    },
    {
      "name": "YouTube",
      "url": "https://youtube.com/@pylabrobot",
      "icon": "fa-brands fa-youtube",
    },
    {
      "name": "Bluesky",
      "url": "https://bsky.app/profile/pylabrobot.bsky.social",
      "icon": "fa-brands fa-bluesky",
    },
  ],
  "logo": {
    "text": "PyLabRobot",
  },
}

html_context = {
  "github_user": "pylabrobot",
  "github_repo": "pylabrobot",
  "github_version": "main",
  "doc_path": "docs",
}

html_logo = "_static/logo.png"

autodoc_default_flags = ["members"]
autosummary_generate = True
autosummary_ignore_module_all = False
autosectionlabel_prefix_document = True
always_document_param_types = True

napoleon_attr_annotations = True
napoleon_google_docstring = True
napoleon_preprocess_types = True
autodoc_typehints = "both"
napoleon_use_rtype = False
napoleon_use_ivar = True

nb_execution_mode = "off"
myst_enable_extensions = ["dollarmath", "attrs_inline"]
myst_heading_anchors = 3

redirects = {
  "installation.html": "user_guide/installation.html",
  "contributing.html": "contributor_guide/index.html",
  "configuration.html": "user_guide/configuration.html",
  "new-machine-type.html": "contributor_guide/new_machine_type.html",
  "new-concrete-backend.html": "contributor_guide/new_concrete_backend.html",
  "how-to-open-source.html": "contributor_guide/how_to_open_source.html",
  "basic.html": "user_guide/basic.html",
  "using-the-visualizer.html": "user_guide/using_the_visualizer.html",
  "using-trackers.html": "user_guide/using_trackers.html",
  "writing-robot-agnostic-methods.html": "user_guide/writing_robot_agnostic_methods.html",
  "hamilton-star/hamilton-star.html": "user_guide/hamilton_star/hamilton_star.html",
  "hamilton-star/iswap-module.html": "user_guide/hamilton_star/iswap_module.html",
  "plate_reading.html": "user_guide/plate_reading.html",
  "cytation5.html": "user_guide/cytation5.html",
  "pumps.html": "user_guide/pumps.html",
  "scales.html": "user_guide/scales.html",
  "temperature.html": "user_guide/temperature.html",
  "tilting.html": "user_guide/tilting.html",
  "heating-shaking.html": "user_guide/heating_shaking.html",
  "fans.html": "user_guide/fans.html",
}

html_baseurl = "https://docs.pylabrobot.org/"

suppress_warnings = []
if tags.has("no-api"):
  exclude_patterns.append("api/**")
  suppress_warnings.append("toc.excluded")

suppress_warnings.append("autosectionlabel.*")

html_favicon = "_static/favicon.ico"

def copy_cookbook_assets(app, exception):
  if exception:
    return
  src = os.path.join(app.srcdir, "cookbook", "assets")
  dst = os.path.join(app.outdir, "cookbook", "assets")
  if not os.path.exists(src):
    return
  os.makedirs(dst, exist_ok=True)
  for root, _, files in os.walk(src):
    for f in files:
      s = os.path.join(root, f)
      r = os.path.relpath(s, src)
      d = os.path.join(dst, r)
      os.makedirs(os.path.dirname(d), exist_ok=True)
      shutil.copy2(s, d)

def setup(app):
  app.connect("build-finished", copy_cookbook_assets)
