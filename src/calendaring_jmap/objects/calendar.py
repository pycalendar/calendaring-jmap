# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JMAP Calendar object.

Represents a JMAP Calendar resource as returned by ``Calendar/get``.
Properties are defined in the JMAP Calendars specification.
"""

from __future__ import annotations

from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, cast, overload

from calendaring_jmap.objects.calendar_object import JMAPCalendarObject

if TYPE_CHECKING:
    from calendaring_jmap.async_client import AsyncJMAPClient
    from calendaring_jmap.client import JMAPClient

## Phantom type parameter distinguishing a sync-backed JMAPCalendar from an
## async-backed one, so overloads below can key off self's type instead of
## the runtime-only _is_async flag mypy can't see. No code ever constructs
## JMAPCalendar[Literal[True]] or [Literal[False]] explicitly: get_calendars()
## return types (client.py, async_client.py) pin it for callers instead.
_M = TypeVar("_M", bound=bool)


def _to_utcdate(dt: datetime) -> str:
    """Convert a datetime to JMAP UTCDate format (YYYY-MM-DDTHH:MM:SSZ).

    Naive datetimes are assumed to be UTC.  Aware datetimes are converted to
    UTC before formatting.  Microseconds are dropped as JMAP does not allow them.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class JMAPCalendar(Generic[_M]):
    """A JMAP Calendar object.

    Attributes:
        id: Server-assigned calendar identifier.
        name: Display name of the calendar. Per-user: a sharee who sets
            this gets their own copy, leaving the owner's name unchanged.
        description: Optional longer description.
        color: Optional CSS color string (e.g. ``"#ff0000"``). Per-user,
            same override rule as ``name``.
        is_subscribed: Whether the user is subscribed to this calendar.
        my_rights: Dict of right names → bool for the current user.
        sort_order: Hint for display ordering (lower = first). Per-user.
        is_visible: Whether the calendar should be displayed. Per-user.
        time_zone: IANA time zone used to resolve floating events on this
            calendar (e.g. for alerts, availability). ``None`` falls back to
            the account's own time zone. Per-user, same override rule as
            ``name``.
        share_with: Map of Principal ID to a dict of right names → bool
            (``mayReadItems``, ``mayWriteAll``, etc.). Only visible to and
            settable by users with the ``mayShare`` right; the server
            returns ``None`` here for anyone else. Not per-user: this is the
            calendar's actual sharing configuration.
        default_alerts_with_time: Map of alert ID to Alert dict, applied to
            new timed events on this calendar when ``useDefaultAlerts`` is
            set. Per-user, not inherited from the owner.
        default_alerts_without_time: Same as ``default_alerts_with_time``,
            for all-day events.
    """

    id: str
    name: str
    description: str | None = None
    color: str | None = None
    is_subscribed: bool = True
    my_rights: dict = field(default_factory=dict)
    sort_order: int = 0
    is_visible: bool = True
    time_zone: str | None = None
    share_with: dict | None = None
    default_alerts_with_time: dict | None = None
    default_alerts_without_time: dict | None = None

    # Injected by JMAPClient.get_calendars() / AsyncJMAPClient.get_calendars()
    _client: JMAPClient | AsyncJMAPClient | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _is_async: bool = field(default=False, init=False, repr=False, compare=False)

    ## The JMAP account this calendar was fetched from, i.e. what was passed
    ## to get_calendars(account_id=...). Not a Calendar wire property: it is
    ## what makes .search()/.add_event()/.get_object_by_uid() target the
    ## calendar's actual owning account instead of the caller's own primary
    ## account when the calendar was reached via someone else's share.
    _account_id: str | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def _bound_client(self) -> JMAPClient | AsyncJMAPClient:
        """The client this calendar was bound to by ``get_calendars()``.

        A ``JMAPCalendar`` only ever reaches user code through
        ``JMAPClient.get_calendars()`` or ``AsyncJMAPClient.get_calendars()``,
        both of which set ``_client`` before returning it, so it is always
        bound by the time any other method runs.
        """
        assert self._client is not None
        return self._client

    @property
    def _bound_async_client(self: JMAPCalendar[Literal[True]]) -> AsyncJMAPClient:
        """Same as :attr:`_bound_client`, narrowed for the ``_async_*`` helpers.

        Only called from a method whose public overload already restricted
        ``self`` to ``JMAPCalendar[Literal[True]]``, i.e. one that
        ``AsyncJMAPClient.get_calendars()`` produced, so ``_client`` is always
        an ``AsyncJMAPClient`` here.
        """
        assert self._is_async
        return cast("AsyncJMAPClient", self._bound_client)

    @classmethod
    def from_jmap(cls, data: dict) -> JMAPCalendar:
        """Construct a JMAPCalendar from a raw JMAP Calendar JSON dict.

        Unknown keys in ``data`` are silently ignored so that forward
        compatibility is maintained as the spec evolves.
        """
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description"),
            color=data.get("color"),
            is_subscribed=data.get("isSubscribed", True),
            my_rights=data.get("myRights", {}),
            sort_order=data.get("sortOrder", 0),
            is_visible=data.get("isVisible", True),
            time_zone=data.get("timeZone"),
            share_with=data.get("shareWith"),
            default_alerts_with_time=data.get("defaultAlertsWithTime"),
            default_alerts_without_time=data.get("defaultAlertsWithoutTime"),
        )

    def to_jmap(self) -> dict:
        """Serialise to a JMAP Calendar JSON dict for ``Calendar/set``.

        ``id`` and ``myRights`` are intentionally excluded, both are
        server-set and must not appear in create or update payloads.
        Optional fields are included only when they hold a non-default value.
        """
        d: dict = {
            "name": self.name,
            "isSubscribed": self.is_subscribed,
            "sortOrder": self.sort_order,
            "isVisible": self.is_visible,
        }
        if self.description is not None:
            d["description"] = self.description
        if self.color is not None:
            d["color"] = self.color
        if self.time_zone is not None:
            d["timeZone"] = self.time_zone
        if self.share_with is not None:
            d["shareWith"] = self.share_with
        if self.default_alerts_with_time is not None:
            d["defaultAlertsWithTime"] = self.default_alerts_with_time
        if self.default_alerts_without_time is not None:
            d["defaultAlertsWithoutTime"] = self.default_alerts_without_time
        return d

    @overload
    def search(
        self: JMAPCalendar[Literal[False]], **searchargs: Any
    ) -> list[JMAPCalendarObject]: ...
    @overload
    def search(
        self: JMAPCalendar[Literal[True]], **searchargs: Any
    ) -> Coroutine[Any, Any, list[JMAPCalendarObject]]: ...
    def search(self: Any, **searchargs: Any):
        """Search for calendar objects in this calendar.

        When called on an async-backed calendar, returns a coroutine that
        must be awaited.

        Accepted keyword arguments (all optional):

        - ``start`` (datetime or str): only events ending after this time
          (maps to JMAP ``after`` filter).
        - ``end`` (datetime or str): only events starting before this time
          (maps to JMAP ``before`` filter).
        - ``text`` (str): free-text search across title, description,
          locations, and participants.

        Returns:
            List of :class:`~calendaring_jmap.objects.calendar_object.JMAPCalendarObject`
            for all matching objects.
        """
        if self._is_async:
            return self._async_search(**searchargs)
        start = searchargs.get("start")
        end = searchargs.get("end")
        if isinstance(start, datetime):
            start = _to_utcdate(start)
        if isinstance(end, datetime):
            end = _to_utcdate(end)
        return self._bound_client._search(
            calendar_id=self.id,
            start=start,
            end=end,
            text=searchargs.get("text"),
            parent=self,
            account_id=self._account_id,
        )

    async def _async_search(
        self: JMAPCalendar[Literal[True]], **searchargs: Any
    ) -> list[JMAPCalendarObject]:
        start = searchargs.get("start")
        end = searchargs.get("end")
        if isinstance(start, datetime):
            start = _to_utcdate(start)
        if isinstance(end, datetime):
            end = _to_utcdate(end)
        return await self._bound_async_client._search(
            calendar_id=self.id,
            start=start,
            end=end,
            text=searchargs.get("text"),
            parent=self,
            account_id=self._account_id,
        )

    @overload
    def get_object_by_uid(
        self: JMAPCalendar[Literal[False]], uid: str, comp_class: Any = None
    ) -> JMAPCalendarObject: ...
    @overload
    def get_object_by_uid(
        self: JMAPCalendar[Literal[True]], uid: str, comp_class: Any = None
    ) -> Coroutine[Any, Any, JMAPCalendarObject]: ...
    def get_object_by_uid(self: Any, uid: str, comp_class: Any = None):
        """Get a calendar object by its iCalendar UID.

        When called on an async-backed calendar, returns a coroutine that
        must be awaited.

        Args:
            uid: The iCalendar UID to search for.
            comp_class: JMAP ``CalendarEvent/query`` has no native
                component-type filter, so this argument is currently ignored.

        Returns:
            A :class:`~calendaring_jmap.objects.calendar_object.JMAPCalendarObject`
            for the matching object.

        Raises:
            JMAPMethodError: If no object with this UID is found.
        """
        if self._is_async:
            return self._async_get_object_by_uid(uid)
        return self._bound_client._get_object_by_uid(
            uid, calendar_id=self.id, parent=self, account_id=self._account_id
        )

    async def _async_get_object_by_uid(
        self: JMAPCalendar[Literal[True]], uid: str
    ) -> JMAPCalendarObject:
        return await self._bound_async_client._get_object_by_uid(
            uid, calendar_id=self.id, parent=self, account_id=self._account_id
        )

    @overload
    def add_event(self: JMAPCalendar[Literal[False]], ical_str: str) -> str: ...
    @overload
    def add_event(self: JMAPCalendar[Literal[True]], ical_str: str) -> Coroutine[Any, Any, str]: ...
    def add_event(self: Any, ical_str: str):
        """Add an event to this calendar from an iCalendar string.

        When called on an async-backed calendar, returns a coroutine that
        must be awaited.

        Args:
            ical_str: A VCALENDAR string representing the event.

        Returns:
            The server-assigned JMAP event ID. This returns a string ID
            rather than a calendar object: the ``CalendarEvent/set`` response
            does not include the full object, so a follow-up GET would be
            required.

        Raises:
            JMAPMethodError: If the server rejects the create request.
        """
        if self._is_async:
            return self._async_add_event(ical_str)
        return self._bound_client.create_event(self.id, ical_str, account_id=self._account_id)

    async def _async_add_event(self: JMAPCalendar[Literal[True]], ical_str: str) -> str:
        return await self._bound_async_client.create_event(
            self.id, ical_str, account_id=self._account_id
        )
