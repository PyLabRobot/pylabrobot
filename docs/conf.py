# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import commonmark # to convert markdown docstrings to rst
from sphinx.ext.napoleon import _process_docstring

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
import os
import sys
sys.path.insert(0, os.path.abspath('..'))


# -- Project information -----------------------------------------------------

project = 'PyHamilton'
copyright = '2022, PyHamilton'
author = 'The PyHamilton authors'


# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
  'sphinx.ext.napoleon',
  'sphinx.ext.autodoc',
  'sphinx.ext.autosummary',
  'sphinx.ext.autosectionlabel',
  'sphinx.ext.intersphinx',
  'sphinx.ext.mathjax',
  'myst_nb',
  'sphinx_copybutton'
]

intersphinx_mapping = {
    'python': ('https://docs.python.org/3/', None),
}

# Add any paths that contain templates here, relative to this directory.
templates_path = ['_templates']

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = [
  'build/*',
  '_templates',
  'Thumbs.db',
  '.DS_Store',
  'jupyter_execute'
]

# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = 'sphinx_book_theme'

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ['_static']

html_theme_options = {
  'repository_url': 'https://github.com/pyhamilton/pyhamilton',
  'use_repository_button': False,
  'use_edit_page_button': True,
  'repository_branch': 'usb',
  'path_to_docs': 'docs',
  'use_issues_button': False,
}


autodoc_default_flags = ['members']
autosummary_generate = True
autosummary_ignore_module_all = False
autosectionlabel_prefix_document = True
always_document_param_types = True

napoleon_attr_annotations = True
napoleon_google_docstring = True
napoleon_preprocess_types = True
autodoc_typehints = 'both'
napoleon_use_rtype = False
napoleon_use_ivar = True

nb_execution_mode = 'off'
myst_enable_extensions = ['dollarmath']

source_suffix = ['.rst', '.md']


def docstring(app, what, name, obj, options, lines):
  """ Convert Markdown docstrings to reStructuredText.

  Based on https://stackoverflow.com/a/70174158/8101290.
  """

  wrapped = []
  literal = False
  for line in lines:
    if line.strip().startswith(r'```'):
      literal = not literal
    if not literal:
      line = ' '.join(x.rstrip() for x in line.split('\n'))
    indent = (len(line) - len(line.lstrip())) > 0
    if indent and not literal:
      wrapped.append(' ' + line.lstrip())
    else:
      if literal:
        wrapped.append('\n' + line)
      else:
        wrapped.append('\n' + line.strip())
  ast = commonmark.Parser().parse(''.join(wrapped))
  rst = commonmark.ReStructuredTextRenderer().render(ast)
  lines.clear()
  lines += rst.splitlines()


def setup(app):
  app.connect('autodoc-process-docstring', docstring)

