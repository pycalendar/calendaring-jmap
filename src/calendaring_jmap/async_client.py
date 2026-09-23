# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Asynchronous JMAP client.

Mirrors JMAPClient with all public methods as coroutines.
Uses niquests' AsyncSession for HTTP: the one part of calendaring-jmap with
no fallback to another HTTP library. See :mod:`calendaring_jmap._http`.

All response-parsing logic lives in _JMAPClientBase (client.py); each method
here is a ~3-line async wrapper: get session, send request, delegate to parser.
"""

from __future__ import annotations

import logging
import uuid
import warnings
from typing import TYPE_CHECKING, Literal

from calendaring_jmap._http import require_async_session

if TYPE_CHECKING:
    ## require_async_session() returns this same class at runtime, but as a
    ## function call mypy can't treat it as a type; this import is only for
    ## the annotations below.
    from niquests import AsyncSession
else:
    ## The async JMAP client is built on niquests' AsyncSession and has no
    ## fallback - so this raises if niquests is absent.
    AsyncSession = require_async_session()

from calendaring_jmap._methods.calendar import (
    build_calendar_get,
    build_calendar_set_create,
    build_calendar_set_destroy,
    build_calendar_set_update,
    parse_calendar_set,
)
from calendaring_jmap._methods.event import (
    build_event_changes,
    build_event_get,
    build_event_set_create,
    build_event_set_destroy,
    build_event_set_update,
    parse_event_get,
    parse_event_set,
)
from calendaring_jmap._methods.principal import build_get_availability, parse_get_availability
from calendaring_jmap._methods.task import (
    build_task_get,
    build_task_list_get,
    build_task_set_create,
    build_task_set_destroy,
    build_task_set_update,
    parse_task_set,
)
from calendaring_jmap.client import _DEFAULT_USING, _PRINCIPALS_USING, _TASK_USING, _JMAPClientBase
from calendaring_jmap.constants import (
    PARTICIPATION_STATUS_ACCEPTED,
    PARTICIPATION_STATUS_DECLINED,
    PARTICIPATION_STATUS_TENTATIVE,
)
from calendaring_jmap.convert import ical_to_jscal
from calendaring_jmap.error import _DEFAULT_ERROR_TYPE, JMAPAuthError, JMAPMethodError
from calendaring_jmap.objects.busy_interval import BusyInterval
from calendaring_jmap.objects.calendar import JMAPCalendar
from calendaring_jmap.objects.calendar_object import JMAPCalendarObject
from calendaring_jmap.session import Session, async_fetch_session

log = logging.getLogger("calendaring_jmap")


class AsyncJMAPClient(_JMAPClientBase):
    """Asynchronous JMAP client for calendar operations.

    **The JMAP support is experimental, the API may change in minor-releases**

    Usage::

        from calendaring_jmap import get_async_jmap_client
        async with get_async_jmap_client(url="https://jmap.example.com/.well-known/jmap",
                                          username="alice", password="secret") as client:
            calendars = await client.get_calendars()

    Args:
        url: URL of the JMAP session endpoint (``/.well-known/jmap``).
        username: Username for Basic auth.
        password: Password for Basic auth, or bearer token if no username.
        auth: A pre-built niquests-compatible auth object. Takes precedence
              over username/password if provided.
        auth_type: Force a specific auth type: ``"basic"`` or ``"bearer"``.
        timeout: HTTP request timeout in seconds.
    """

    def _get_http_session(self) -> AsyncSession:
        """Return the persistent async HTTP session, creating it on first call."""
        if self._http_session is None:
            sess = AsyncSession()
            sess.auth = self._auth
            sess.headers.update({"Content-Type": "application/json", "Accept": "application/json"})
            self._http_session = sess
        return self._http_session

    async def aclose(self) -> None:
        """Release the persistent HTTP session and its connection pool.

        Only needed when the client was not used as an async context manager
        -- the documented Quick Start builds one directly.  Idempotent; the
        session is recreated on the next request.
        """
        if self._http_session is not None:
            await self._http_session.close()
            self._http_session = None

    async def __aenter__(self) -> AsyncJMAPClient:
        self._get_http_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.aclose()

    def __del__(self) -> None:
        ## Closing an async session needs an event loop, which is long gone by the
        ## time __del__ runs, so all we can do is say so.  getattr() rather than
        ## attribute access: __init__ may have raised before setting it, and an
        ## AttributeError here would be reported as "exception ignored in __del__"
        ## on top of whatever actually went wrong.
        if getattr(self, "_http_session", None) is None:
            return
        try:
            warnings.warn(
                f"{type(self).__name__} was garbage collected with an open HTTP "
                "session; use 'async with' or await aclose()",
                ResourceWarning,
                ## stacklevel=1 on purpose, and explicitly because ruff's B028
                ## wants it stated: pointing any higher is a lie in __del__, where
                ## the caller is the garbage collector.  source= is what makes the
                ## warning actionable instead - with `python -X tracemalloc` it
                ## reports where the leaked client was allocated.
                stacklevel=1,
                source=self,
            )
        except Exception:
            ## __del__ must not raise.  At interpreter shutdown the warnings
            ## machinery may already be torn down and stderr may be closed, and
            ## there is neither anything to recover nor anywhere to report it.
            pass

    async def _get_session(self) -> Session:
        """Return the cached Session, fetching it on first call."""
        if self._session_cache is None:
            self._session_cache = await async_fetch_session(
                self.url, auth=self._auth, timeout=self.timeout
            )
        return self._session_cache

    async def _request(self, method_calls: list[tuple], using: list[str] | None = None) -> list:
        """POST a batch of JMAP method calls and return the methodResponses.

        Args:
            method_calls: List of 3-tuples ``(method_name, args_dict, call_id)``.
            using: Capability URN list for the ``using`` field. Defaults to
                ``_DEFAULT_USING`` (core + calendars).

        Returns:
            List of 3-tuples ``(method_name, response_args, call_id)`` from
            the server's ``methodResponses`` array.

        Raises:
            JMAPAuthError: On HTTP 401 or 403.
            JMAPMethodError: If any methodResponse is an ``error`` response.
        """
        session = await self._get_session()

        payload = {
            "using": using if using is not None else _DEFAULT_USING,
            "methodCalls": list(method_calls),
        }

        log.debug("JMAP POST to %s: %d method call(s)", session.api_url, len(method_calls))

        response = await self._get_http_session().post(
            session.api_url,
            json=payload,
            timeout=self.timeout,
        )

        if response.status_code in (401, 403):
            raise JMAPAuthError(
                url=session.api_url,
                reason=f"HTTP {response.status_code} from API endpoint",
            )

        response.raise_for_status()

        data = response.json()
        method_responses = data.get("methodResponses", [])

        for resp in method_responses:
            method_name, resp_args, call_id = resp
            if method_name == "error":
                error_type = resp_args.get("type", _DEFAULT_ERROR_TYPE)
                raise JMAPMethodError(
                    url=session.api_url,
                    reason=f"Method call failed: {resp_args}",
                    error_type=error_type,
                )

        return method_responses

    async def get_calendars(
        self, account_id: str | None = None
    ) -> list[JMAPCalendar[Literal[True]]]:
        """Fetch all calendars for an account.

        Args:
            account_id: The JMAP account to query. Defaults to the
                authenticated user's own primary account. Pass a different
                account here to browse calendars another user has shared with
                you, once ``share_calendar`` has granted access; the session
                only lists accounts you can actually reach (see
                ``Session.raw["accounts"]``).

        Returns:
            List of :class:`~calendaring_jmap.objects.calendar.JMAPCalendar` objects.
        """
        session = await self._get_session()
        target_account = self._resolve_account(session, account_id)
        responses = await self._request([build_calendar_get(target_account)])
        return self._parse_get_calendars(responses, self, True, account_id=target_account)

    async def create_calendar(
        self,
        name: str,
        color: str | None = None,
        timezone: str | None = None,
    ) -> str:
        """Create a calendar.

        Returns:
            The server-assigned JMAP calendar ID.

        Raises:
            JMAPMethodError: If the server rejects the create request.
        """
        session = await self._get_session()
        cal: dict = {"name": name}
        if color is not None:
            cal["color"] = color
        if timezone is not None:
            cal["timeZone"] = timezone
        call = build_calendar_set_create(session.account_id, {"new-0": cal})
        responses = await self._request([call])
        return self._parse_create_response(
            responses, session.api_url, "Calendar/set", parse_calendar_set
        )

    async def _update_calendar_patch(
        self, calendar_id: str, patch: dict, account_id: str | None = None
    ) -> None:
        session = await self._get_session()
        target_account = self._resolve_account(session, account_id)
        call = build_calendar_set_update(target_account, {calendar_id: patch})
        responses = await self._request([call])
        self._parse_update_response(
            responses, session.api_url, "Calendar/set", parse_calendar_set, calendar_id
        )

    async def update_calendar(
        self,
        calendar_id: str,
        name: str | None = None,
        color: str | None = None,
        timezone: str | None = None,
        account_id: str | None = None,
    ) -> None:
        """Update a calendar's name, color, or time zone.

        ``name``, ``color``, and ``timeZone`` are per-user properties
        (draft-ietf-jmap-calendars section 4.3). Called by the calendar's owner, this changes the
        value for everyone until a sharee sets their own override. Called by
        a sharee, it only ever changes that sharee's own view; it can never
        rename or recolor the calendar for the owner or anyone else it is
        shared with.

        Args:
            account_id: The JMAP account owning ``calendar_id``. Defaults to
                the authenticated user's own primary account; pass the
                owner's account here to update a calendar shared with you.

        Raises:
            JMAPMethodError: If the server rejects the update.
        """
        patch: dict = {}
        if name is not None:
            patch["name"] = name
        if color is not None:
            patch["color"] = color
        if timezone is not None:
            patch["timeZone"] = timezone
        await self._update_calendar_patch(calendar_id, patch, account_id=account_id)

    async def delete_calendar(
        self,
        calendar_id: str,
        on_destroy_remove_events: bool = False,
        account_id: str | None = None,
    ) -> None:
        """Delete a calendar.

        Args:
            calendar_id: The JMAP calendar ID to delete.
            on_destroy_remove_events: If ``False`` (the default), deleting a
                calendar that still has events fails with a
                ``calendarHasEvent`` error instead of deleting anything. Pass
                ``True`` to remove those events along with the calendar.
            account_id: The JMAP account owning ``calendar_id``. Defaults to
                the authenticated user's own primary account; pass the
                owner's account here to delete a calendar shared with you.

        Raises:
            JMAPMethodError: If the server rejects the delete. ``error_type``
                is ``"calendarHasEvent"`` when the calendar still has events
                and ``on_destroy_remove_events`` was left ``False``.
        """
        session = await self._get_session()
        target_account = self._resolve_account(session, account_id)
        call = build_calendar_set_destroy(target_account, [calendar_id], on_destroy_remove_events)
        responses = await self._request([call])
        self._parse_delete_response(
            responses, session.api_url, "Calendar/set", parse_calendar_set, calendar_id
        )

    async def share_calendar(
        self,
        calendar_id: str,
        account_id: str,
        rights: dict,
        owning_account_id: str | None = None,
    ) -> None:
        """Share a calendar with another account.

        ``account_id`` must already be a resolved JMAP Principal ID, not an
        email address. This client has no ``Principal/query``/``Principal/get``
        support yet (:rfc:`9670`), so resolving an email address to a Principal
        ID is left to the caller.

        Replaces ``account_id``'s entry in the calendar's ``shareWith`` map
        wholesale; other accounts already sharing the calendar are left
        untouched. ``rights`` itself is not merged with any rights that
        account already had, so granting an additional right to an account
        that is already shared with needs the full rights dict, not just the
        new key. Requires the caller to already hold the ``mayShare`` right,
        and cannot grant a right the caller does not themselves hold.

        Args:
            account_id: The account to grant rights to.
            rights: Dict of right names to bool, e.g. ``{"mayReadItems": True}``.
                Replaces any rights this account already had on this calendar.
            owning_account_id: The JMAP account owning ``calendar_id``.
                Defaults to the authenticated user's own primary account;
                pass the owner's account here to re-share a calendar that
                was itself shared with you (if you hold ``mayShare`` on it).

        Raises:
            JMAPMethodError: If the server rejects the update, for example
                with ``forbidden`` when the caller lacks ``mayShare``.
        """
        await self._update_calendar_patch(
            calendar_id, {f"shareWith/{account_id}": rights}, account_id=owning_account_id
        )

    async def get_calendar_subscriptions(self) -> list[JMAPCalendar[Literal[True]]]:
        """Fetch the calendars the authenticated account is subscribed to.

        JMAP Calendars has no separate subscription object; this is
        ``get_calendars()`` filtered to ``is_subscribed``.

        Returns:
            List of :class:`~calendaring_jmap.objects.calendar.JMAPCalendar`
            objects with ``is_subscribed`` set.
        """
        calendars = await self.get_calendars()
        return [cal for cal in calendars if cal.is_subscribed]

    async def set_default_alerts(
        self,
        calendar_id: str,
        alerts_with_time: dict | None = None,
        alerts_without_time: dict | None = None,
        account_id: str | None = None,
    ) -> None:
        """Set a calendar's default alerts for new events.

        ``alerts_with_time`` applies to timed events, ``alerts_without_time``
        to all-day events (draft-ietf-jmap-calendars section 4). Each is a
        map of alert ID to Alert dict (:rfc:`8984#section-4.5.2`). Pass
        ``None`` to leave a property unchanged; pass ``{}`` to clear it.

        Args:
            account_id: The JMAP account owning ``calendar_id``. Defaults to
                the authenticated user's own primary account; pass the
                owner's account here to set default alerts on a calendar
                shared with you.

        Raises:
            JMAPMethodError: If the server rejects the update.
        """
        patch: dict = {}
        if alerts_with_time is not None:
            patch["defaultAlertsWithTime"] = alerts_with_time
        if alerts_without_time is not None:
            patch["defaultAlertsWithoutTime"] = alerts_without_time
        await self._update_calendar_patch(calendar_id, patch, account_id=account_id)

    async def _create_event_impl(
        self,
        calendar_id: str,
        ical_str: str,
        account_id: str | None,
        send_scheduling_messages: bool,
    ) -> str:
        session = await self._get_session()
        jscal = ical_to_jscal(ical_str, calendar_id=calendar_id)
        target_account = self._resolve_account(session, account_id)
        call = build_event_set_create(
            target_account, {"new-0": jscal}, send_scheduling_messages=send_scheduling_messages
        )
        responses = await self._request([call])
        return self._parse_create_response(
            responses, session.api_url, "CalendarEvent/set", parse_event_set
        )

    async def create_event(
        self, calendar_id: str, ical_str: str, account_id: str | None = None
    ) -> str:
        """Create a calendar event from an iCalendar string.

        Args:
            calendar_id: The JMAP calendar ID to create the event in.
            ical_str: A VCALENDAR string representing the event.
            account_id: The JMAP account owning ``calendar_id``. Defaults to
                the authenticated user's own primary account; pass the
                owner's account here to add an event to a calendar shared
                with you (see ``get_calendars(account_id=...)``).

        Returns:
            The server-assigned JMAP event ID.

        Raises:
            JMAPMethodError: If the server rejects the create request.
        """
        return await self._create_event_impl(
            calendar_id, ical_str, account_id, send_scheduling_messages=False
        )

    async def send_invite(
        self, calendar_id: str, ical_str: str, account_id: str | None = None
    ) -> str:
        """Create a calendar event and notify its participants.

        Identical to :meth:`create_event`, except the server actually
        dispatches iTIP invitations to the event's participants (an iCalendar
        string with ATTENDEE/ORGANIZER properties). ``create_event`` itself
        never sends scheduling messages; use this instead when the event has
        participants who need to be notified.

        Args:
            calendar_id: The JMAP calendar ID to create the event in.
            ical_str: A VCALENDAR string representing the event.
            account_id: The JMAP account owning ``calendar_id``. Defaults to
                the authenticated user's own primary account.

        Returns:
            The server-assigned JMAP event ID.

        Raises:
            JMAPMethodError: If the server rejects the create request, or
                with ``error_type == "noSupportedScheduleMethods"`` if a
                participant has no usable delivery method.
        """
        return await self._create_event_impl(
            calendar_id, ical_str, account_id, send_scheduling_messages=True
        )

    async def get_event(self, event_id: str, account_id: str | None = None) -> JMAPCalendarObject:
        """Fetch a calendar event as an iCalendar string.

        Args:
            event_id: The JMAP event ID to retrieve.
            account_id: The JMAP account owning ``event_id``. Defaults to
                the authenticated user's own primary account; pass the
                owner's account here to fetch an event on a calendar shared
                with you.

        Returns:
            A :class:`~calendaring_jmap.objects.calendar_object.JMAPCalendarObject`
            wrapping the raw JSCalendar dict.  ``parent`` is ``None`` since
            no :class:`~calendaring_jmap.objects.calendar.JMAPCalendar` is available
            at the client level.

        Raises:
            JMAPMethodError: If the event is not found.
        """
        session = await self._get_session()
        target_account = self._resolve_account(session, account_id)
        responses = await self._request([build_event_get(target_account, ids=[event_id])])
        return self._parse_get_event_response(responses, session.api_url, event_id)

    async def _find_own_participant_id(
        self, event_id: str, own_email: str, account_id: str | None = None
    ) -> str:
        """Return the participant id in ``event_id`` whose email is ``own_email``.

        Only the ``participants`` property is fetched, not the whole event.
        See :meth:`JMAPClient._find_participant_id_by_email` for the matching rules.
        """
        session = await self._get_session()
        target_account = self._resolve_account(session, account_id)
        responses = await self._request(
            [build_event_get(target_account, ids=[event_id], properties=["participants"])]
        )
        event = self._parse_get_event_response(responses, session.api_url, event_id)
        return self._find_participant_id_by_email(
            event.data.get("participants", {}), own_email, session.api_url, event_id
        )

    async def _respond_to_invitation(
        self,
        event_id: str,
        own_email: str,
        participation_status: str,
        account_id: str | None = None,
    ) -> None:
        session = await self._get_session()
        target_account = self._resolve_account(session, account_id)
        participant_id = await self._find_own_participant_id(
            event_id, own_email, account_id=account_id
        )
        patch = {f"participants/{participant_id}/participationStatus": participation_status}
        call = build_event_set_update(
            target_account, {event_id: patch}, send_scheduling_messages=True
        )
        responses = await self._request([call])
        self._parse_update_response(
            responses, session.api_url, "CalendarEvent/set", parse_event_set, event_id
        )

    async def accept_invitation(
        self, event_id: str, own_email: str, account_id: str | None = None
    ) -> None:
        """Accept a meeting invitation, notifying the organizer.

        See :meth:`JMAPClient.accept_invitation` for the full semantics.
        """
        await self._respond_to_invitation(
            event_id, own_email, PARTICIPATION_STATUS_ACCEPTED, account_id=account_id
        )

    async def decline_invitation(
        self, event_id: str, own_email: str, account_id: str | None = None
    ) -> None:
        """Decline a meeting invitation, notifying the organizer.

        Same as :meth:`accept_invitation`, setting ``participationStatus``
        to declined instead.
        """
        await self._respond_to_invitation(
            event_id, own_email, PARTICIPATION_STATUS_DECLINED, account_id=account_id
        )

    async def tentatively_accept(
        self, event_id: str, own_email: str, account_id: str | None = None
    ) -> None:
        """Tentatively accept a meeting invitation, notifying the organizer.

        Same as :meth:`accept_invitation`, setting ``participationStatus``
        to tentative instead.
        """
        await self._respond_to_invitation(
            event_id, own_email, PARTICIPATION_STATUS_TENTATIVE, account_id=account_id
        )

    async def update_event(
        self, event_id: str, ical_str: str, account_id: str | None = None
    ) -> None:
        """Update a calendar event from an iCalendar string.

        Args:
            event_id: The JMAP event ID to update.
            ical_str: A VCALENDAR string with the updated event data.
            account_id: The JMAP account owning ``event_id``. Defaults to
                the authenticated user's own primary account; pass the
                owner's account here to update an event on a calendar
                shared with you.

        Raises:
            JMAPMethodError: If the server rejects the update.
        """
        session = await self._get_session()
        target_account = self._resolve_account(session, account_id)
        patch, nulled = self._build_event_update_patch(ical_str)
        while True:
            responses = await self._request(
                [build_event_set_update(target_account, {event_id: patch})]
            )
            drop = self._unsupported_null_keys(responses, event_id, patch, nulled)
            if not drop:
                break
            for key in drop:
                patch.pop(key, None)
        self._parse_update_response(
            responses, session.api_url, "CalendarEvent/set", parse_event_set, event_id
        )

    async def _search(
        self,
        calendar_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
        text: str | None = None,
        parent: JMAPCalendar | None = None,
        account_id: str | None = None,
    ) -> list[JMAPCalendarObject]:
        session = await self._get_session()
        calls = self._build_event_search_calls(
            self._resolve_account(session, account_id),
            calendar_id,
            start,
            end,
            text,
        )
        responses = await self._request(calls)
        return self._parse_search_response(responses, parent)

    async def search_events(
        self,
        calendar_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
        text: str | None = None,
        account_id: str | None = None,
    ) -> list[JMAPCalendarObject]:
        """Search for calendar events.

        All parameters are optional; omitting all returns every event in the account.
        Results are fetched in a single batched JMAP request using a result reference
        from ``CalendarEvent/query`` into ``CalendarEvent/get``.

        Args:
            calendar_id: Limit results to this calendar.
            start: Only events ending after this datetime (``YYYY-MM-DDTHH:MM:SS``).
            end: Only events starting before this datetime (``YYYY-MM-DDTHH:MM:SS``).
            text: Free-text search across title, description, locations, and participants.
            account_id: The JMAP account to search. Defaults to the
                authenticated user's own primary account; pass a different
                account here to search a calendar shared with you.

        Returns:
            List of :class:`~calendaring_jmap.objects.calendar_object.JMAPCalendarObject`
            instances.  ``parent`` is ``None`` on these objects; use
            :meth:`JMAPCalendar.search` if you need ``parent`` set.
        """
        return await self._search(
            calendar_id=calendar_id, start=start, end=end, text=text, account_id=account_id
        )

    async def get_availability(
        self,
        account_ids: list[str],
        start: str,
        end: str,
        show_details: bool = False,
    ) -> dict[str, list[BusyInterval]]:
        """Return busy intervals for each of your own accounts over a time period.

        See :meth:`JMAPClient.get_availability` for the full semantics.
        """
        session = await self._get_session()
        result: dict[str, list[BusyInterval]] = {}
        for account_id in account_ids:
            if self._can_use_principal_availability(session, account_id):
                principal_id = self._current_user_principal_id(session)
                if principal_id is not None:
                    try:
                        result[account_id] = await self._get_availability_via_principal(
                            principal_id, start, end, show_details
                        )
                        continue
                    except JMAPMethodError as e:
                        if not self._should_fall_back_to_query(e.error_type):
                            raise
            result[account_id] = await self._get_availability_via_fallback(account_id, start, end)
        return result

    async def _get_availability_via_principal(
        self, principal_id: str, start: str, end: str, show_details: bool
    ) -> list[BusyInterval]:
        session = await self._get_session()
        call = build_get_availability(
            principal_id,
            self._as_utc_datetime(start),
            self._as_utc_datetime(end),
            show_details=show_details,
        )
        responses = await self._request([call], using=_PRINCIPALS_USING)
        for method_name, resp_args, _ in responses:
            if method_name == "Principal/getAvailability":
                return [BusyInterval.from_jmap(bp) for bp in parse_get_availability(resp_args)]
        raise JMAPMethodError(url=session.api_url, reason="No Principal/getAvailability response")

    async def _get_availability_via_fallback(
        self, account_id: str, start: str, end: str
    ) -> list[BusyInterval]:
        calls = self._build_availability_fallback_calls(account_id, start, end)
        responses = await self._request(calls)
        for method_name, resp_args, _ in responses:
            if method_name == "CalendarEvent/get":
                return self._busy_intervals_from_events(parse_event_get(resp_args))
        return []

    async def get_sync_token(self) -> str:
        """Return the current CalendarEvent state string for use as a sync token.

        Calls ``CalendarEvent/get`` with an empty ID list, so no event data
        is transferred, only the ``state`` field from the response.

        Returns:
            Opaque state string. Pass to :meth:`get_objects_by_sync_token` to
            retrieve only what changed since this point.
        """
        session = await self._get_session()
        responses = await self._request([build_event_get(session.account_id, ids=[])])
        return self._parse_get_sync_token_response(responses, session.api_url)

    async def get_objects_by_sync_token(
        self, sync_token: str
    ) -> tuple[list[JMAPCalendarObject], list[JMAPCalendarObject], list[str], str]:
        """Fetch events changed since a previous sync token.

        Calls ``CalendarEvent/changes`` to discover which events were created,
        modified, or destroyed since ``sync_token`` was issued. Created and
        modified events are returned as
        :class:`~calendaring_jmap.objects.calendar_object.JMAPCalendarObject` instances;
        destroyed events are returned as IDs (the objects no longer exist on the server).

        Args:
            sync_token: A state string previously returned by :meth:`get_sync_token`
                or by a prior call to this method.

        Returns:
            A 4-tuple ``(added, modified, deleted, new_sync_token)``:

            - ``added``: objects for newly created events (``parent`` is ``None``).
            - ``modified``: objects for updated events (``parent`` is ``None``).
            - ``deleted``: Event IDs that were destroyed.
            - ``new_sync_token``: Pass to the next call to this method as ``sync_token``.

        Raises:
            JMAPMethodError: If the server reports ``hasMoreChanges: true``.
        """
        session = await self._get_session()
        responses = await self._request([build_event_changes(session.account_id, sync_token)])
        created_ids, updated_ids, destroyed, new_sync_token = self._parse_event_changes_response(
            responses, session.api_url
        )
        fetch_ids = created_ids + updated_ids
        if not fetch_ids:
            return [], [], destroyed, new_sync_token
        get_responses = await self._request([build_event_get(session.account_id, ids=fetch_ids)])
        return self._assemble_sync_token_result(
            get_responses, created_ids, updated_ids, destroyed, new_sync_token
        )

    async def delete_event(
        self,
        event_id: str,
        account_id: str | None = None,
        send_scheduling_messages: bool = False,
    ) -> None:
        """Delete a calendar event.

        Args:
            event_id: The JMAP event ID to delete.
            account_id: The JMAP account owning ``event_id``. Defaults to
                the authenticated user's own primary account; pass the
                owner's account here to delete an event on a calendar
                shared with you.
            send_scheduling_messages: If true, and this account is the
                event's origin, the server sends an iTIP CANCEL to the
                event's participants (draft-ietf-jmap-calendars section 5.9.2.2).

        Raises:
            JMAPMethodError: If the server rejects the delete.
        """
        session = await self._get_session()
        target_account = self._resolve_account(session, account_id)
        responses = await self._request(
            [build_event_set_destroy(target_account, [event_id], send_scheduling_messages)]
        )
        self._parse_delete_response(
            responses, session.api_url, "CalendarEvent/set", parse_event_set, event_id
        )

    async def _get_object_by_uid(
        self,
        uid: str,
        calendar_id: str | None = None,
        parent: JMAPCalendar | None = None,
        account_id: str | None = None,
    ) -> JMAPCalendarObject:
        # RFC 8984 FilterCondition has no uid field; UID matching is done client-side.
        results = await self._search(calendar_id=calendar_id, parent=parent, account_id=account_id)
        for obj in results:
            if obj.data.get("uid") == uid:
                return obj
        session = await self._get_session()
        raise JMAPMethodError(
            url=session.api_url, reason=f"No calendar object found with UID: {uid}"
        )

    async def get_task_lists(self) -> list[dict]:
        """Fetch all task lists for the authenticated account.

        Returns:
            List of raw JMAP TaskList dicts as returned by the server.
        """
        session = await self._get_session()
        responses = await self._request(
            [build_task_list_get(session.account_id)], using=_TASK_USING
        )
        return self._parse_get_task_lists_response(responses)

    async def create_task(self, task_list_id: str, title: str, **kwargs) -> str:
        """Create a task in a task list.

        Args:
            task_list_id: The JMAP task list ID to create the task in.
            title: Task title (maps to VTODO ``SUMMARY``).
            **kwargs: Optional JMAP Task fields using wire names: ``description``,
                ``due``, ``start``, ``timeZone``, ``estimatedDuration``,
                ``percentComplete``, ``progress``, ``priority``.

        Returns:
            The server-assigned JMAP task ID.

        Raises:
            JMAPMethodError: If the server rejects the create request.
        """
        session = await self._get_session()
        task_dict = {
            "@type": "Task",
            "uid": str(uuid.uuid4()),
            "taskListId": task_list_id,
            "title": title,
            "percentComplete": 0,
            "progress": "needs-action",
            "priority": 0,
        }
        task_dict.update(kwargs)
        call = build_task_set_create(session.account_id, {"new-0": task_dict})
        responses = await self._request([call], using=_TASK_USING)
        return self._parse_create_response(responses, session.api_url, "Task/set", parse_task_set)

    async def get_task(self, task_id: str) -> dict:
        """Fetch a task by ID.

        Args:
            task_id: The JMAP task ID to retrieve.

        Returns:
            Raw JMAP Task dict as returned by the server.

        Raises:
            JMAPMethodError: If the task is not found.
        """
        session = await self._get_session()
        responses = await self._request(
            [build_task_get(session.account_id, ids=[task_id])], using=_TASK_USING
        )
        return self._parse_get_task_response(responses, session.api_url, task_id)

    async def update_task(self, task_id: str, patch: dict) -> None:
        """Update a task with a partial patch.

        Args:
            task_id: The JMAP task ID to update.
            patch: Partial patch dict mapping property names to new values.

        Raises:
            JMAPMethodError: If the server rejects the update.
        """
        session = await self._get_session()
        call = build_task_set_update(session.account_id, {task_id: patch})
        responses = await self._request([call], using=_TASK_USING)
        self._parse_update_response(responses, session.api_url, "Task/set", parse_task_set, task_id)

    async def delete_task(self, task_id: str) -> None:
        """Delete a task.

        Args:
            task_id: The JMAP task ID to delete.

        Raises:
            JMAPMethodError: If the server rejects the delete.
        """
        session = await self._get_session()
        responses = await self._request(
            [build_task_set_destroy(session.account_id, [task_id])], using=_TASK_USING
        )
        self._parse_delete_response(responses, session.api_url, "Task/set", parse_task_set, task_id)
