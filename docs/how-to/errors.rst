.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

=============
Handle errors
=============

All calendaring-jmap errors extend :class:`~calendaring_jmap.error.JMAPBaseError`, and the JMAP-specific ones extend :class:`~calendaring_jmap.error.JMAPError`, which adds an ``error_type`` string. See :doc:`../explanation/design` for why the hierarchy is shaped this way.

Catch specific error types
==========================

.. code-block:: python

    from calendaring_jmap.error import (
        JMAPAuthError,
        JMAPCapabilityError,
        JMAPError,
        JMAPMethodError,
    )

    try:
        event_id = client.create_event(calendar_id, ical)
    except JMAPAuthError:
        print("Authentication failed (401/403)")
    except JMAPCapabilityError:
        print("Server does not support urn:ietf:params:jmap:calendars")
    except JMAPMethodError as e:
        print(f"Server rejected the request: {e.error_type}, {e.reason}")
    except JMAPError as e:
        print(f"Protocol error: {e}")

See :doc:`../reference/errors` for what each error class means and the full list of JMAP method error types.
