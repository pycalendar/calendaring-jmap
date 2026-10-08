.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

================================
Sync a calendar to a local cache
================================

A complete script that keeps a SQLite cache of one calendar's events in sync, using :doc:`authentication <authenticate>`, :doc:`incremental sync <sync>`, and :doc:`error handling <errors>` together.

.. code-block:: python

    import sqlite3

    from calendaring_jmap import get_jmap_client
    from calendaring_jmap.error import JMAPMethodError

    DB_PATH = "events.db"


    def init_db(conn):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "  id TEXT PRIMARY KEY,"
            "  calendar_id TEXT NOT NULL,"
            "  ical TEXT NOT NULL"
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sync_state ("
            "  calendar_id TEXT PRIMARY KEY,"
            "  token TEXT NOT NULL"
            ")"
        )
        conn.commit()


    def load_token(conn, calendar_id):
        row = conn.execute(
            "SELECT token FROM sync_state WHERE calendar_id = ?", (calendar_id,)
        ).fetchone()
        return row[0] if row else None


    def save_token(conn, calendar_id, token):
        conn.execute(
            "INSERT INTO sync_state (calendar_id, token) VALUES (?, ?) "
            "ON CONFLICT(calendar_id) DO UPDATE SET token = excluded.token",
            (calendar_id, token),
        )
        conn.commit()


    def sync_calendar(client, conn, calendar_id):
        token = load_token(conn, calendar_id)
        if token is None:
            token = client.get_sync_token()
            save_token(conn, calendar_id, token)
            return

        try:
            added, modified, deleted, token = client.get_objects_by_sync_token(token)
        except JMAPMethodError as e:
            if e.error_type == "serverPartialFail":
                # The server dropped our baseline; start over from a fresh token.
                token = client.get_sync_token()
                save_token(conn, calendar_id, token)
                return
            raise

        for obj in list(added) + list(modified):
            ical_str = obj.get_icalendar_instance().to_ical().decode()
            conn.execute(
                "INSERT INTO events (id, calendar_id, ical) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET ical = excluded.ical",
                (obj.id, calendar_id, ical_str),
            )
        for event_id in deleted:
            conn.execute("DELETE FROM events WHERE id = ?", (event_id,))

        save_token(conn, calendar_id, token)
        conn.commit()


    if __name__ == "__main__":
        conn = sqlite3.connect(DB_PATH)
        init_db(conn)

        with get_jmap_client() as client:
            for cal in client.get_calendars():
                sync_calendar(client, conn, cal.id)

        conn.close()

This reads configuration the same way every other example does, through :func:`~calendaring_jmap.get_jmap_client`'s explicit-argument/environment-variable/config-file chain; see :doc:`authenticate` for how to set that up.

The first run for a calendar has no stored token, so it only records a baseline and caches nothing; the next run starts fetching real deltas. :meth:`~calendaring_jmap.client.JMAPClient.get_objects_by_sync_token`'s :class:`~calendaring_jmap.error.JMAPMethodError` with ``error_type="serverPartialFail"`` means the server truncated the change list; the same :doc:`error-handling pattern <errors>` applies here as anywhere else this error can occur.
