# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sphinx configuration for calendaring-jmap documentation."""

import datetime

project = "calendaring-jmap"
this_year = datetime.date.today().year  # noqa: DTZ011
copyright = f"{this_year}, calendaring-jmap contributors"  # noqa: A001
author = "calendaring-jmap contributors"

# Extensions
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_copybutton",
]

# Theme configuration
html_theme = "pydata_sphinx_theme"
html_theme_options = {
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/pycalendar/calendaring-jmap",
            "icon": "fa-brands fa-square-github",
            "type": "fontawesome",
        },
        {
            "name": "PyPI",
            "url": "https://pypi.org/project/calendaring-jmap",
            "icon": "fa-custom fa-pypi",
            "type": "fontawesome",
        },
    ],
    "footer_start": ["copyright"],
    "footer_end": ["theme-version", "sphinx-version"],
    "logo": {"text": "calendaring-jmap"},
    "use_edit_page_button": True,
    "show_toc_level": 2,
    "navigation_with_keys": True,
}

html_context = {
    "github_user": "pycalendar",
    "github_repo": "calendaring-jmap",
    "github_version": "main",
    "doc_path": "docs",
}

templates_path = ["_templates"]

# Intersphinx mapping
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "icalendar": ("https://icalendar.readthedocs.io/en/latest/", None),
}

# Napoleon settings (Google-style docstrings)
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True

# Autodoc settings
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}
