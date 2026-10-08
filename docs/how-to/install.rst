.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

=======
Install
=======

.. code-block:: bash

    pip install calendaring-jmap

No separate install step is needed for async support: niquests, the HTTP library this package uses, is always installed and provides it directly.

If niquests can't be installed in your environment, the ``requests`` extra provides a fallback HTTP library:

.. code-block:: bash

    pip install calendaring-jmap[requests]

The package ships inline type hints and a ``py.typed`` marker (:pep:`561`), so a type checker reads them directly without a separate stub package.

From source
===========

To install from a clone, for example to test an unreleased change:

.. code-block:: bash

    git clone https://github.com/pycalendar/calendaring-jmap
    cd calendaring-jmap
    pip install -e ".[dev]"
