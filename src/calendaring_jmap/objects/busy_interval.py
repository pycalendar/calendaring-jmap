# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JMAP BusyPeriod object.

Represents one interval of a ``Principal/getAvailability`` response
(draft-ietf-jmap-calendars section 2.2), or one computed client-side by the
``CalendarEvent/query`` fallback path when a server doesn't implement that
method. Read-only: nothing here is ever sent back to the server, so there
is no ``to_jmap``.
"""

from __future__ import annotations

from dataclasses import dataclass

from calendaring_jmap.constants import BUSY_STATUS_UNAVAILABLE
from calendaring_jmap.objects.calendar_object import JMAPCalendarObject


@dataclass
class BusyInterval:
    """One busy/free interval.

    Attributes:
        start: Start of the interval (``UTCDateTime`` string).
        end: End of the interval (``UTCDateTime`` string).
        busy_status: One of ``"confirmed"``, ``"tentative"``,
            ``"unavailable"`` (see ``constants.BUSY_STATUS_*``).
        event: The underlying event, when the server populated it. This can
            be ``None`` even when details were requested: confirmed live
            that this happens when the caller lacks ``mayReadItems``, the
            event is marked private, or (Stalwart-specific) the caller
            didn't also pass a server-supported ``eventProperties`` list.
    """

    start: str
    end: str
    busy_status: str
    event: JMAPCalendarObject | None = None

    @classmethod
    def from_jmap(cls, data: dict) -> BusyInterval:
        """Construct a BusyInterval from a raw JMAP BusyPeriod JSON dict.

        Unknown keys in ``data`` are silently ignored so that forward
        compatibility is maintained as the spec evolves.
        """
        event_data = data.get("event")
        return cls(
            start=data["utcStart"],
            end=data["utcEnd"],
            busy_status=data.get("busyStatus", BUSY_STATUS_UNAVAILABLE),
            event=JMAPCalendarObject(data=event_data, parent=None)
            if event_data is not None
            else None,
        )
