from docutils.parsers.rst import Directive, directives
from docutils import nodes
from .nodes import plr_card_grid_placeholder


def _split_tags(raw):
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def _ensure_env_map(env):
    if not hasattr(env, "plr_cards"):
        env.plr_cards = {}
    return env.plr_cards


class _BaseCardDirective(Directive):
    has_content = False
    required_arguments = 0
    optional_arguments = 0
    final_argument_whitespace = False
    option_spec = {
        "header": directives.unchanged_required,
        "card_description": directives.unchanged,
        "image": directives.unchanged,
        "image_hover": directives.unchanged,
        "link": directives.unchanged_required,
        "tags": directives.unchanged,
    }

    def run(self):
        env = self.state.document.settings.env
        docname = env.docname
        cards_map = _ensure_env_map(env)
        cards = cards_map.setdefault(docname, [])
        cards.append({
            "header": self.options.get("header", ""),
            "desc": self.options.get("card_description", ""),
            "image": self.options.get("image", ""),
            "image_hover": self.options.get("image_hover", ""),
            "link": self.options.get("link", ""),
            "tags": _split_tags(self.options.get("tags", "")),
        })
        # No visible node; actual rendering happens in the grid placeholder.
        return []


class PyLabRobotCard(_BaseCardDirective):
    """
    Add a card. Aliases:
      - .. customcarditem::        (compat)
      - .. plrcard::               (PyLabRobot)
    Options:
      :header: Title (required)
      :card_description: Short text
      :image: path/to/image.png
      :link: path/to/page.html (required)
      :tags: Tag1, Tag2
    """


class _BaseGridDirective(Directive):
    has_content = False

    def run(self):
        return [plr_card_grid_placeholder("")]


class PyLabRobotCardGrid(_BaseGridDirective):
    """
    Insert a grid of the cards collected in this page. Aliases:
      - .. cardgrid::              (compat)
      - .. plrcardgrid::           (PyLabRobot)
    """


def _purge(app, env, docname):
    if hasattr(env, "plr_cards") and docname in env.plr_cards:
        del env.plr_cards[docname]


def _merge(app, env, docnames, other):
    if not hasattr(other, "plr_cards"):
        return
    if not hasattr(env, "plr_cards"):
        env.plr_cards = {}
    env.plr_cards.update(other.plr_cards)


def _page_ctx(app, pagename, templatename, context, doctree):
    env = app.builder.env
    per_page = getattr(env, "plr_cards", {}).get(pagename, [])
    all_tags = []
    for cards in getattr(env, "plr_cards", {}).values():
        for c in cards:
            all_tags.extend(c.get("tags", []))
    all_tags = sorted(set(all_tags), key=str.lower)
    context["plr_cards"] = per_page
    context["plr_cards_all_tags"] = all_tags

import os
from docutils import nodes


def _replace_placeholders(app, doctree, fromdocname):
    env = app.builder.env
    per_page = getattr(env, "plr_cards", {}).get(fromdocname, [])

    # prefix like "", "../", "../../" depending on nesting of fromdocname
    docdir = os.path.dirname(fromdocname).replace("\\", "/")
    depth = 0 if docdir == "" else docdir.count("/") + 1
    prefix = "../" * depth

    def is_url(p):
        return p.startswith(("http://", "https://"))

    cards_render = []
    for c in per_page:
        img = (c.get("image") or "").replace("\\", "/")
        img_hover = (c.get("image_hover") or "").replace("\\", "/")

        image_url = ""
        image_hover_url = ""

        # Resolve main image
        if img:
            if is_url(img) or img.startswith("/"):
                image_url = img  # absolute http(s) or site-root path
            else:
                if img.startswith("_static/"):
                    image_url = prefix + img
                else:
                    image_url = prefix + img

        # Resolve hover image
        if img_hover:
            if is_url(img_hover) or img_hover.startswith("/"):
                image_hover_url = img_hover
            else:
                if img_hover.startswith("_static/"):
                    image_hover_url = prefix + img_hover
                else:
                    image_hover_url = prefix + img_hover

        cards_render.append({
            **c,
            "image_url": image_url,
            "image_hover_url": image_hover_url,
        })

    page_tags = sorted({t for c in per_page for t in c.get("tags", [])}, key=str.lower)

    for node in doctree.traverse(plr_card_grid_placeholder):
        html = app.builder.templates.render("plr_card_grid.html", {
            "cards": cards_render,
            "all_tags": page_tags,
        })
        raw = nodes.raw("", html, format="html")
        node.replace_self(raw)


def setup(app):
    from sphinx.application import Sphinx  # noqa: F401

    # Register directives (compat + PLR names)
    app.add_directive("customcarditem", PyLabRobotCard)     # compat
    app.add_directive("plrcard", PyLabRobotCard)            # PLR
    app.add_directive("cardgrid", PyLabRobotCardGrid)       # compat
    app.add_directive("plrcardgrid", PyLabRobotCardGrid)    # PLR

    # Events
    app.connect("env-purge-doc", _purge)
    app.connect("env-merge-info", _merge)
    app.connect("html-page-context", _page_ctx)
    app.connect("doctree-resolved", _replace_placeholders)

    return {
        "version": "1.0",
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }
