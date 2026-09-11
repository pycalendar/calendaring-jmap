# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sphinx configuration for calendaring-jmap documentation."""

import datetime
import os

project = "calendaring-jmap"
this_year = datetime.date.today().year  # noqa: DTZ011
copyright = f"{this_year}, calendaring-jmap contributors"  # noqa: A001
author = "calendaring-jmap contributors"

# Extensions
extensions = [
    "notfound.extension",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_copybutton",
    "sphinx_design",
    "sphinx_issues",
]

# sphinx_issues configuration: enables :issue:`N`, :pr:`N`, :user:`name` roles
issues_github_path = "pycalendar/calendaring-jmap"

# Theme configuration
html_theme = "pydata_sphinx_theme"
html_theme_options = {
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/pycalendar/calendaring-jmap",
            "icon": "fa-brands fa-square-github",
            "type": "fontawesome",
            "attributes": {"target": "_blank", "rel": "noopener me"},
        },
        {
            "name": "PyPI",
            "url": "https://pypi.org/project/calendaring-jmap",
            "icon": "fa-custom fa-pypi",
            "type": "fontawesome",
            "attributes": {"target": "_blank", "rel": "noopener me"},
        },
    ],
    "footer_start": ["nlnet", "copyright"],
    "footer_end": ["theme-version", "sphinx-version"],
    "logo": {"text": "calendaring-jmap"},
    "use_edit_page_button": True,
    "show_toc_level": 2,
    "navbar_align": "content",
    "show_nav_level": 1,
    "navigation_with_keys": True,
    "collapse_navigation": False,
    "search_bar_text": "Search documentation",
}

html_context = {
    "github_user": "pycalendar",
    "github_repo": "calendaring-jmap",
    "github_version": "main",
    "doc_path": "docs",
}

templates_path = ["_templates"]
html_static_path = ["_static"]
# Custom fa-custom/fa-pypi icon used in icon_links above
html_js_files = [("js/custom-icons.js", {"defer": "defer"})]

# notfound.extension configuration
notfound_template = "404.html"
# Defaults to READTHEDOCS_CANONICAL_URL's path on RTD; outside RTD that env
# var isn't set and the extension falls back to a hardcoded "/en/latest/",
# which 404s every asset on a local build. Use root-relative paths instead
# when not building on RTD.
if not os.environ.get("READTHEDOCS"):
    notfound_urls_prefix = "/"

# Intersphinx mapping
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "icalendar": ("https://icalendar.readthedocs.io/en/latest/", None),
}

# Napoleon settings (Google-style docstrings)
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
# Render "Attributes:" as :ivar: field-list entries instead of standalone
# attribute directives, which collide with autodoc's own dataclass field
# introspection and produce "duplicate object description" warnings.
napoleon_use_ivar = True

# Autodoc settings
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}
