# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Synchronous JMAP client.

Wraps session establishment, HTTP communication, and method dispatching
into a single object with a clean public API.

Auth note: JMAP has no 401-challenge-retry dance (unlike CalDAV).
Credentials are sent upfront on every request. A 401/403 is a hard failure.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from calendaring_jmap._http import HTTPBasicAuth, HTTPBearerAuth, requests
from calendaring_jmap._methods.calendar import (
    build_calendar_get,
    build_calendar_set_create,
    build_calendar_set_destroy,
    build_calendar_set_update,
    parse_calendar_get,
    parse_calendar_set,
)
from calendaring_jmap._methods.contact import (
    build_address_book_get,
    build_contact_get_by_query_result,
    build_contact_query,
    parse_address_book_get,
    parse_contact_get,
)
from calendaring_jmap._methods.event import (
    build_event_changes,
    build_event_get,
    build_event_get_by_query_result,
    build_event_query,
    build_event_set_create,
    build_event_set_destroy,
    build_event_set_update,
    parse_event_changes,
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
    parse_task_list_get,
    parse_task_set,
)
from calendaring_jmap.constants import (
    BUSY_STATUS_UNAVAILABLE,
    CALENDAR_CAPABILITY,
    CONTACTS_CAPABILITY,
    CORE_CAPABILITY,
    LINK_REL_ENCLOSURE,
    PARTICIPATION_STATUS_ACCEPTED,
    PARTICIPATION_STATUS_DECLINED,
    PARTICIPATION_STATUS_TENTATIVE,
    PRINCIPALS_CAPABILITY,
    TASK_CAPABILITY,
    UTC_DATETIME_FORMAT,
)
from calendaring_jmap.convert import ical_to_jscal
from calendaring_jmap.convert._patch import _NULL_FOR_UPDATE
from calendaring_jmap.convert._utils import _duration_to_timedelta
from calendaring_jmap.error import (
    _DEFAULT_ERROR_TYPE,
    JMAPAuthError,
    JMAPCapabilityError,
    JMAPMethodError,
)
from calendaring_jmap.objects.attachment import JMAPAttachment
from calendaring_jmap.objects.busy_interval import BusyInterval
from calendaring_jmap.objects.calendar import JMAPCalendar
from calendaring_jmap.objects.calendar_object import JMAPCalendarObject
from calendaring_jmap.objects.contact import JMAPAddressBook, JMAPContact
from calendaring_jmap.session import Session, _expand_uri_template, fetch_session

log = logging.getLogger("calendaring_jmap")

_DEFAULT_USING = [CORE_CAPABILITY, CALENDAR_CAPABILITY]
_TASK_USING = [CORE_CAPABILITY, TASK_CAPABILITY]
_PRINCIPALS_USING = [CORE_CAPABILITY, CALENDAR_CAPABILITY, PRINCIPALS_CAPABILITY]
_CONTACTS_USING = [CORE_CAPABILITY, CONTACTS_CAPABILITY]


class _JMAPClientBase:
    def __init__(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
        auth=None,
        auth_type: str | None = None,
        timeout: int = 30,
    ) -> None:
        self.url = url
        self.username = username
        self.password = password
        self.timeout = timeout
        self._session_cache: Session | None = None
        ## Holds a requests/niquests Session for JMAPClient or a niquests
        ## AsyncSession for AsyncJMAPClient; the two subclasses' concrete
        ## session types have no common typed base to name here.
        self._http_session: Any = None

        if auth is not None:
            self._auth = auth
        else:
            self._auth = self._build_auth(auth_type)

    def _build_auth(self, auth_type: str | None):
        """Select and construct the auth object.

        **The JMAP support is experimental, the API may change in minor-releases**

        JMAP supports Basic and Bearer auth; Digest is not supported.
        When ``auth_type`` is ``None`` the type is inferred from the
        credentials supplied: a username triggers Basic, a password
        alone triggers Bearer, and neither raises :class:`JMAPAuthError`.
        """
        effective_type = auth_type
        if effective_type is None:
            if self.username:
                effective_type = "basic"
            elif self.password:
                effective_type = "bearer"
            else:
                raise JMAPAuthError(
                    url=self.url,
                    reason="No credentials provided. Supply username+password or a bearer token.",
                )

        if effective_type == "basic":
            if not self.username or not self.password:
                raise JMAPAuthError(
                    url=self.url,
                    reason="Basic auth requires both username and password.",
                )
            return HTTPBasicAuth(self.username, self.password)
        elif effective_type == "bearer":
            if not self.password:
                raise JMAPAuthError(
                    url=self.url,
                    reason="Bearer auth requires a token supplied as the password argument.",
                )
            return HTTPBearerAuth(self.password)
        else:
            raise JMAPAuthError(
                url=self.url,
                reason=f"Unsupported auth_type {effective_type!r}. Use 'basic' or 'bearer'.",
            )

    @staticmethod
    def _resolve_account(session: Session, account_id: str | None) -> str:
        """Return ``account_id``, or ``session.account_id`` when it is ``None``."""
        return account_id if account_id is not None else session.account_id

    @staticmethod
    def _account_supports(session: Session, capability: str) -> bool:
        """Return whether the session's own account advertises ``capability``.

        ``session.account_capabilities`` (like ``session.account_id``) is
        scoped to the account chosen for calendars, so this only ever says
        something about that one account, never a different one; use
        ``session.server_capabilities`` for a server-wide check instead.
        """
        return capability in session.account_capabilities

    @staticmethod
    def _supports_principals(session: Session) -> bool:
        """Return whether this account advertises :rfc:`9670` Principal support.

        This is the real gate for ``Principal/getAvailability``, not the
        draft's own narrower ``:availability`` sub-capability: confirmed
        live that Cyrus implements the method fully without ever
        advertising that sub-capability, so checking for it would wrongly
        skip Cyrus every time.
        """
        return _JMAPClientBase._account_supports(session, PRINCIPALS_CAPABILITY)

    @staticmethod
    def _current_user_principal_id(session: Session) -> str | None:
        """Return the caller's own Principal id from the session, if any.

        :rfc:`9670#section-1.5.1`: ``currentUserPrincipalId`` is a property of the
        ``urn:ietf:params:jmap:principals`` entry in ``accountCapabilities``.
        No ``Principal/query``/``Principal/get`` call is needed for this.
        """
        return session.account_capabilities.get(PRINCIPALS_CAPABILITY, {}).get(
            "currentUserPrincipalId"
        )

    @staticmethod
    def _should_fall_back_to_query(error_type: str) -> bool:
        """Return whether a ``Principal/getAvailability`` error means the
        server doesn't support the method, as opposed to a real failure."""
        return error_type in ("unknownMethod", "accountNotSupportedByMethod")

    @staticmethod
    def _can_use_principal_availability(session: Session, account_id: str) -> bool:
        """Return whether ``get_availability`` may try the primary path for
        ``account_id``.

        ``Principal/getAvailability`` never carries an explicit ``accountId``
        (confirmed live: Cyrus rejects one in this method's own args), so it
        can only ever report on the session's own account. A different
        ``account_id`` always needs the fallback, which does take an
        explicit ``accountId``.
        """
        return account_id == session.account_id and _JMAPClientBase._supports_principals(session)

    @staticmethod
    def _can_skip_contacts_request(session: Session, account_id: str) -> bool:
        """Return whether ``get_address_books`` may skip the request and
        return ``[]`` for ``account_id`` without ever asking the server.

        ``session.server_capabilities`` says something about every account,
        so a server with no Contacts support at all is honored regardless
        of ``account_id``. ``session.account_capabilities`` only ever
        describes the session's own account (like ``session.account_id``
        itself), so the narrower "this account specifically lacks it"
        check only applies there; a different account on a server that
        does support Contacts always sends the real request and lets a
        genuine error surface, the same way every other capability-gated
        method that takes an explicit ``account_id`` already does.
        """
        if CONTACTS_CAPABILITY not in session.server_capabilities:
            return True
        return account_id == session.account_id and not _JMAPClientBase._account_supports(
            session, CONTACTS_CAPABILITY
        )

    @staticmethod
    def _warn_contacts_unsupported(account_id: str) -> None:
        """Log the warning ``get_address_books`` emits in place of raising
        when ``account_id`` doesn't advertise Contacts support."""
        log.warning(
            "Account %s does not advertise capability %s; get_address_books "
            "returning an empty list instead of raising.",
            account_id,
            CONTACTS_CAPABILITY,
        )

    @staticmethod
    def _as_utc_datetime(local_datetime: str) -> str:
        """Convert one of ``get_availability``'s own UTC-treated
        ``start``/``end`` strings to the ``UTCDateTime`` format
        ``Principal/getAvailability`` requires. Confirmed live that Cyrus
        rejects a bare, unsuffixed value here with ``invalidArguments``.
        """
        return f"{local_datetime}Z"

    @staticmethod
    def _raise_set_error(api_url: str, err: dict) -> None:
        raise JMAPMethodError(
            url=api_url,
            reason=f"set failed: {err}",
            error_type=err.get("type", _DEFAULT_ERROR_TYPE),
        )

    @staticmethod
    def _filter_from(**conditions) -> dict | None:
        """Build a FilterCondition dict from keyword args, dropping any
        that are ``None``, or ``None`` itself if every one was. Shared by
        every ``_build_*_search_calls`` that builds a filter this way.
        """
        filter_dict = {k: v for k, v in conditions.items() if v is not None}
        return filter_dict or None

    @staticmethod
    def _build_event_search_calls(
        account_id: str,
        calendar_id: str | None,
        start: str | None,
        end: str | None,
        text: str | None,
    ) -> list[tuple]:
        """Return a batched [CalendarEvent/query, CalendarEvent/get] call list for _search."""
        # JMAP Calendars draft-29 section 5.11.1 defines this as "inCalendar"
        # (singular, one Id), not "inCalendars" (a list).
        filter_condition = _JMAPClientBase._filter_from(
            inCalendar=calendar_id, after=start, before=end, text=text
        )
        query_call = build_event_query(account_id, filter_condition=filter_condition)
        get_call = build_event_get_by_query_result(account_id)
        return [query_call, get_call]

    @staticmethod
    def _build_availability_fallback_calls(account_id: str, start: str, end: str) -> list[tuple]:
        """Return a batched [CalendarEvent/query, CalendarEvent/get] call list
        for computing availability client-side, for servers without
        ``Principal/getAvailability`` support.

        ``expandRecurrences`` is always on: without it, a recurring series
        is found by the ``after``/``before`` filter but returned as one
        object carrying only the master occurrence's own ``start``, not
        whichever occurrence(s) actually fall in the window (confirmed live
        against both Cyrus and Stalwart). ``start``/``end`` must be
        ``LocalDateTime`` (no ``Z``/UTC suffix); confirmed live that Cyrus
        rejects a ``Z``-suffixed value here with ``invalidArguments``.
        """
        query_call = build_event_query(
            account_id,
            filter_condition={"after": start, "before": end},
            expand_recurrences=True,
        )
        get_call = build_event_get_by_query_result(
            account_id, properties=["start", "duration", "freeBusyStatus", "timeZone"]
        )
        return [query_call, get_call]

    @staticmethod
    def _build_contact_search_calls(
        account_id: str, text: str | None, email: str | None
    ) -> list[tuple]:
        """Return a batched [ContactCard/query, ContactCard/get] call list for search_contacts."""
        filter_condition = _JMAPClientBase._filter_from(text=text, email=email)
        query_call = build_contact_query(account_id, filter_condition=filter_condition)
        get_call = build_contact_get_by_query_result(account_id)
        return [query_call, get_call]

    @staticmethod
    def _jscal_start_to_utc_datetime(start: str, time_zone: str | None) -> str:
        """Convert a JSCalendar ``start``/``timeZone`` pair to a ``Z``-suffixed
        ``UTCDateTime`` string, matching what ``Principal/getAvailability``
        returns, so ``BusyInterval.start``/``.end`` have one consistent
        format regardless of which path produced them (:rfc:`8984`'s own three
        ``start`` shapes: already ``Z``-suffixed UTC, ``timeZone``-qualified,
        or floating/naive with neither, see ``jscal_to_ical._start_to_dtstart``).
        A floating start (no ``timeZone``) has no true UTC equivalent; it is
        treated as UTC, matching ``get_availability``'s own documented
        treatment of its ``start``/``end`` window parameters.
        """
        if start.endswith("Z"):
            return start
        naive = datetime.fromisoformat(start)
        if time_zone:
            try:
                naive = naive.replace(tzinfo=ZoneInfo(time_zone))
            except ZoneInfoNotFoundError:
                # Non-IANA TZID: no way to resolve an offset, fall through
                # and treat it as UTC like a floating time.
                pass
        if naive.tzinfo is not None:
            naive = naive.astimezone(timezone.utc)
        return naive.strftime(UTC_DATETIME_FORMAT)

    @classmethod
    def _busy_intervals_from_events(cls, events: list[dict]) -> list[BusyInterval]:
        """Compute BusyInterval objects from raw event dicts for the fallback path.

        :rfc:`8984#section-4.4.2`: ``freeBusyStatus`` is ``"free"`` or
        ``"busy"``, default ``"busy"`` when absent. Events marked ``"free"``
        don't count toward busy time.
        """
        intervals = []
        for event in events:
            if event.get("freeBusyStatus", "busy") == "free":
                continue
            duration = _duration_to_timedelta(event.get("duration", "PT0S"))
            # Resolve start to UTC first, then add the duration: adding the
            # duration to the still-local start and converting that would
            # re-apply timeZone to a value already made UTC by a Z-suffixed
            # start, double-converting it.
            start = cls._jscal_start_to_utc_datetime(event["start"], event.get("timeZone"))
            end_dt = datetime.strptime(start, UTC_DATETIME_FORMAT) + duration
            end = end_dt.strftime(UTC_DATETIME_FORMAT)
            interval = BusyInterval(start=start, end=end, busy_status=BUSY_STATUS_UNAVAILABLE)
            intervals.append(interval)
        return intervals

    @staticmethod
    def _build_event_update_patch(ical_str: str) -> tuple[dict, frozenset[str]]:
        """Build a JSCalendar PatchObject for a ``CalendarEvent/set`` update.

        :rfc:`8620#section-5.3` merge semantics preserve properties absent from
        the patch, so any optional property removed client-side must be
        explicitly nulled to actually clear it server-side.  Returns the patch
        together with the set
        of keys that were null-injected purely for this cleanup (i.e. were not
        present in the converted iCalendar) so the caller can drop them if the
        server refuses to null a property it does not support.
        """
        patch = ical_to_jscal(ical_str)
        patch.pop("uid", None)  # uid is server-immutable after creation; patch must omit it
        nulled: set[str] = set()
        for key in _NULL_FOR_UPDATE:
            if key not in patch:
                patch[key] = None
                nulled.add(key)
        return patch, frozenset(nulled)

    @staticmethod
    def _unsupported_null_keys(
        responses: list, event_id: str, patch: dict, nulled: frozenset[str]
    ) -> set[str] | None:
        """Detect an update that failed *only* because the server rejects
        null-clearing of properties it does not support.

        Some servers (e.g. Stalwart for ``recurrenceRule``) reject a property
        outright in ``CalendarEvent/set``, even when it is being set to ``null``.
        Nulling such a property is harmless cleanup: it was absent from the
        new iCalendar, so we report it as droppable, letting the caller
        retry the update without it.

        Returns the set of droppable keys when the failure is exactly this case,
        or ``None`` when the update succeeded or failed for a genuine reason (in
        which case the caller proceeds to :meth:`_parse_update_response`,
        which raises the real error).  Some servers report only one offending
        property per response, so the caller retries in a loop, dropping the
        reported keys until the update succeeds or hits a genuine error; each
        returned key is guaranteed still present in ``patch``, so the loop
        strictly shrinks the patch and terminates.
        """
        for method_name, resp_args, _ in responses:
            if method_name == "CalendarEvent/set":
                _, _, _, _, not_updated, _ = parse_event_set(resp_args)
                err = not_updated.get(event_id)
                if not err or err.get("type") != "invalidProperties":
                    return None
                props = set(err.get("properties") or [])
                droppable = {p for p in props if p in nulled and p in patch and patch[p] is None}
                # Only retry when every offending property is null-cleanup we can
                # safely omit; if the client actually set one of them to a value,
                # the rejection is genuine and must surface.
                if props and props == droppable:
                    return droppable
                return None
        return None

    # ---------------------------------------------------------------------------
    # Shared response parsers, pure synchronous, used by both sync and async
    # clients.  Each method takes the raw ``methodResponses`` list returned by
    # ``_request()`` plus whatever extra context is needed to build the result
    # or raise an informative error, and returns/raises exactly what the public
    # method should return/raise.
    # ---------------------------------------------------------------------------

    @staticmethod
    def _first_matching_list(responses: list, method_name: str, parser) -> list:
        """Return ``parser(resp_args)`` for the first response whose method
        name is ``method_name``, or ``[]`` if there is none.

        Shared by every ``_parse_*`` method whose whole job is "find one
        response by method name, hand its args to a parser, otherwise
        return an empty list" (``parser`` can itself bind extra state onto
        each result, e.g. :meth:`_parse_get_calendars`'s own client/account
        binding). A method that needs to raise instead of returning ``[]``
        (e.g. :meth:`_parse_get_sync_token_response`) doesn't fit this
        shape and isn't a caller.
        """
        for name, resp_args, _ in responses:
            if name == method_name:
                return parser(resp_args)
        return []

    @staticmethod
    def _parse_get_calendars(
        responses: list, client, is_async: bool, account_id: str | None = None
    ) -> list[JMAPCalendar[Any]]:
        def _bind(resp_args: dict) -> list[JMAPCalendar[Any]]:
            calendars = parse_calendar_get(resp_args)
            for cal in calendars:
                cal._client = client
                cal._is_async = is_async
                cal._account_id = account_id
            return calendars

        return _JMAPClientBase._first_matching_list(responses, "Calendar/get", _bind)

    @staticmethod
    def _parse_get_address_books(responses: list) -> list[JMAPAddressBook]:
        return _JMAPClientBase._first_matching_list(
            responses, "AddressBook/get", parse_address_book_get
        )

    @staticmethod
    def _parse_search_contacts_response(responses: list) -> list[JMAPContact]:
        return _JMAPClientBase._first_matching_list(responses, "ContactCard/get", parse_contact_get)

    @staticmethod
    def _no_set_response_error(api_url: str, set_method: str) -> JMAPMethodError:
        """Build the error raised when a batched response never contains the
        expected ``set_method`` entry at all (as opposed to containing it
        with a per-object failure, which goes through :meth:`_raise_set_error`
        instead). Shared by all three ``_parse_*_response`` methods below."""
        return JMAPMethodError(url=api_url, reason=f"No {set_method} response")

    @staticmethod
    def _parse_create_response(responses: list, api_url: str, set_method: str, parse_set) -> str:
        """Parse a ``*/set`` response for a create call using client creation id ``"new-0"``.

        Shared by every object type's ``create_*`` method: calendars, events, and
        tasks all use the same single-object-create shape.
        """
        for method_name, resp_args, _ in responses:
            if method_name == set_method:
                created, _, _, not_created, _, _ = parse_set(resp_args)
                if "new-0" in not_created:
                    _JMAPClientBase._raise_set_error(api_url, not_created["new-0"])
                if "new-0" not in created:
                    raise JMAPMethodError(
                        url=api_url,
                        reason=f"{set_method} response missing created entry for new-0",
                    )
                return created["new-0"]["id"]
        raise _JMAPClientBase._no_set_response_error(api_url, set_method)

    @staticmethod
    def _parse_update_response(
        responses: list, api_url: str, set_method: str, parse_set, object_id: str
    ) -> None:
        """Parse a ``*/set`` response for an update call, raising on failure.

        Shared by every object type's ``update_*`` method.
        """
        for method_name, resp_args, _ in responses:
            if method_name == set_method:
                _, _, _, _, not_updated, _ = parse_set(resp_args)
                if object_id in not_updated:
                    _JMAPClientBase._raise_set_error(api_url, not_updated[object_id])
                return
        raise _JMAPClientBase._no_set_response_error(api_url, set_method)

    @staticmethod
    def _parse_delete_response(
        responses: list, api_url: str, set_method: str, parse_set, object_id: str
    ) -> None:
        """Parse a ``*/set`` response for a destroy call, raising on failure.

        Shared by every object type's ``delete_*`` method.
        """
        for method_name, resp_args, _ in responses:
            if method_name == set_method:
                _, _, _, _, _, not_destroyed = parse_set(resp_args)
                if object_id in not_destroyed:
                    _JMAPClientBase._raise_set_error(api_url, not_destroyed[object_id])
                return
        raise _JMAPClientBase._no_set_response_error(api_url, set_method)

    @staticmethod
    def _find_participant_id_by_email(
        participants: dict, own_email: str, api_url: str, event_id: str
    ) -> str:
        """Return the id of the entry in ``participants`` whose email is ``own_email``.

        Matching is a case-insensitive comparison against each participant's
        ``email`` property, falling back to ``calendarAddress`` (stripping a
        leading ``mailto:``) for a participant with no ``email`` set. JMAP
        Calendars itself matches by ``ParticipantIdentity.calendarAddress``,
        a mechanism this client does not implement (see ``share_calendar``'s
        docstring for the related Principal-id limitation); this fallback
        covers a spec-compliant server, or another client's participant,
        that only ever set ``calendarAddress``.

        Shared by :meth:`JMAPClient._find_own_participant_id` and
        :meth:`AsyncJMAPClient._find_own_participant_id`.

        Raises:
            JMAPMethodError: If no participant matches ``own_email``.
        """
        target = own_email.lower()
        for participant_id, participant in participants.items():
            email = participant.get("email") or participant.get("calendarAddress") or ""
            email = email.removeprefix("mailto:")
            if email.lower() == target:
                return participant_id
        raise JMAPMethodError(
            url=api_url,
            reason=f"No participant matching {own_email!r} on event {event_id!r}",
            error_type="notFound",
        )

    @staticmethod
    def _parse_get_event_response(
        responses: list, api_url: str, event_id: str
    ) -> JMAPCalendarObject:
        for method_name, resp_args, _ in responses:
            if method_name == "CalendarEvent/get":
                items = parse_event_get(resp_args)
                if not items:
                    raise JMAPMethodError(
                        url=api_url,
                        reason=f"Event not found: {event_id}",
                        error_type="notFound",
                    )
                return JMAPCalendarObject(data=items[0], parent=None)
        raise JMAPMethodError(url=api_url, reason="No CalendarEvent/get response")

    @staticmethod
    def _parse_search_response(
        responses: list, parent: JMAPCalendar | None
    ) -> list[JMAPCalendarObject]:
        return _JMAPClientBase._first_matching_list(
            responses,
            "CalendarEvent/get",
            lambda resp_args: [
                JMAPCalendarObject(data=item, parent=parent) for item in parse_event_get(resp_args)
            ],
        )

    @staticmethod
    def _parse_availability_fallback_response(responses: list) -> list[BusyInterval]:
        """Parse the ``CalendarEvent/get`` response from
        ``_build_availability_fallback_calls`` into busy intervals.

        Shared by the sync and async ``_get_availability_via_fallback``.
        """
        return _JMAPClientBase._first_matching_list(
            responses,
            "CalendarEvent/get",
            lambda resp_args: _JMAPClientBase._busy_intervals_from_events(
                parse_event_get(resp_args)
            ),
        )

    @staticmethod
    def _parse_get_sync_token_response(responses: list, api_url: str) -> str:
        for method_name, resp_args, _ in responses:
            if method_name == "CalendarEvent/get":
                return resp_args.get("state", "")
        raise JMAPMethodError(url=api_url, reason="No CalendarEvent/get response")

    @staticmethod
    def _parse_event_changes_response(
        responses: list, api_url: str
    ) -> tuple[list[str], list[str], list[str], str]:
        """Parse a CalendarEvent/changes response.

        Returns ``(created_ids, updated_ids, destroyed_ids, new_sync_token)``.
        Raises :class:`JMAPMethodError` when the server truncated the result.
        """
        created_ids: list[str] = []
        updated_ids: list[str] = []
        destroyed: list[str] = []
        new_sync_token: str = ""
        for method_name, resp_args, _ in responses:
            if method_name == "CalendarEvent/changes":
                _, new_sync_token, has_more, created_ids, updated_ids, destroyed = (
                    parse_event_changes(resp_args)
                )
                if has_more:
                    raise JMAPMethodError(
                        url=api_url,
                        reason=(
                            "CalendarEvent/changes response was truncated by the server "
                            "(hasMoreChanges=true). Call get_sync_token() to obtain a "
                            "fresh baseline and re-sync."
                        ),
                        error_type="serverPartialFail",
                    )
        return created_ids, updated_ids, destroyed, new_sync_token

    @staticmethod
    def _assemble_sync_token_result(
        get_responses: list,
        created_ids: list[str],
        updated_ids: list[str],
        destroyed: list[str],
        new_sync_token: str,
    ) -> tuple[list[JMAPCalendarObject], list[JMAPCalendarObject], list[str], str]:
        events_by_id: dict[str, JMAPCalendarObject] = {}
        for method_name, resp_args, _ in get_responses:
            if method_name == "CalendarEvent/get":
                for item in parse_event_get(resp_args):
                    events_by_id[item["id"]] = JMAPCalendarObject(data=item, parent=None)
        added = [events_by_id[i] for i in created_ids if i in events_by_id]
        modified = [events_by_id[i] for i in updated_ids if i in events_by_id]
        return added, modified, destroyed, new_sync_token

    @staticmethod
    def _parse_get_task_lists_response(responses: list) -> list[dict]:
        return _JMAPClientBase._first_matching_list(responses, "TaskList/get", parse_task_list_get)

    @staticmethod
    def _parse_get_task_response(responses: list, api_url: str, task_id: str) -> dict:
        for method_name, resp_args, _ in responses:
            if method_name == "Task/get":
                items = resp_args.get("list", [])
                if not items:
                    raise JMAPMethodError(
                        url=api_url,
                        reason=f"Task not found: {task_id}",
                        error_type="notFound",
                    )
                return items[0]
        raise JMAPMethodError(url=api_url, reason="No Task/get response")

    @staticmethod
    def _build_attachment_patch(
        existing_links: dict | None, name: str, content_type: str, download_url: str
    ) -> dict:
        """Build a ``links`` patch adding one attachment Link to ``existing_links``.

        Replaces the whole ``links`` property rather than patching a single
        new map key: confirmed live that both Cyrus and Stalwart reject a
        ``{"links/<id>": {...}}`` patch into a map that doesn't yet contain
        that key. ``existing_links`` may be ``None`` (an absent or explicitly
        null ``links`` property both mean "no links").
        """
        link_id = str(uuid.uuid4())
        return {
            "links": {
                **(existing_links or {}),
                link_id: {
                    "@type": "Link",
                    "href": download_url,
                    "title": name,
                    "contentType": content_type,
                    "rel": LINK_REL_ENCLOSURE,
                },
            }
        }

    @staticmethod
    def _parse_event_attachments(event_data: dict) -> list[JMAPAttachment]:
        """Return the attachment Links from an event's ``links`` property.

        ``links`` may be absent (the common case for an event with none) or
        explicitly ``None``; both are treated as "no links".
        """
        return [
            JMAPAttachment.from_jmap(link_id, link_data)
            for link_id, link_data in (event_data.get("links") or {}).items()
            if JMAPAttachment.is_attachment(link_data)
        ]

    @staticmethod
    def _build_upload_headers(content_type: str) -> dict:
        """Return headers for a raw blob upload, overriding the session default."""
        return {"Content-Type": content_type}

    @staticmethod
    def _build_download_headers() -> dict:
        """Return headers for a raw blob download, overriding the session
        default ``Accept: application/json`` (set for JMAP method calls,
        wrong for a request whose response body is arbitrary binary data)."""
        return {"Accept": "*/*"}

    @staticmethod
    def _check_blob_response(response, url: str) -> None:
        """Raise on an HTTP error from a raw blob upload or download.

        Shared by :meth:`upload_attachment`/:meth:`download_attachment` on
        both the sync and async client. Same 401/403 handling as
        :func:`~calendaring_jmap.session.fetch_session`; any other non-2xx
        (e.g. 404 for an unknown blobId, confirmed live on both Cyrus and
        Stalwart) surfaces as a plain HTTP error, since neither blob call
        has a JMAP methodResponses envelope to carry a JMAP-style error in.
        """
        if response.status_code in (401, 403):
            raise JMAPAuthError(url=url, reason=f"HTTP {response.status_code} from blob URL")
        response.raise_for_status()

    @staticmethod
    def _require_blob_url(url: str | None, which: str, api_url: str) -> str:
        """Return ``url``, raising clearly if the server omitted it from Session."""
        if url is None:
            raise JMAPCapabilityError(
                url=api_url,
                reason=f"Server did not advertise a {which} in its Session object",
            )
        return url

    @classmethod
    def _build_blob_download_url(
        cls, session: Session, account_id: str, blob_id: str, content_type: str, name: str
    ) -> str:
        """Expand the Session's ``downloadUrl`` template for one blob.

        Shared by :meth:`download_attachment`/:meth:`attach_to_event` on
        both the sync and async client. ``content_type``/``name`` are taken
        as given: :meth:`download_attachment` passes ``or ""`` for its own
        optional parameters (see its docstring for why that's safe);
        :meth:`attach_to_event` always has real values, since both become
        properties on the Link it writes.
        """
        download_url = cls._require_blob_url(session.download_url, "downloadUrl", session.api_url)
        return _expand_uri_template(
            download_url,
            {
                "accountId": account_id,
                "blobId": blob_id,
                "type": content_type,
                "name": name,
            },
        )


class JMAPClient(_JMAPClientBase):
    """Synchronous JMAP client for calendar operations.

    Usage::

        from calendaring_jmap import get_jmap_client
        client = get_jmap_client(url="https://jmap.example.com/.well-known/jmap",
                                  username="alice", password="secret")
        calendars = client.get_calendars()

    Args:
        url: URL of the JMAP session endpoint (``/.well-known/jmap``).
        username: Username for Basic auth.
        password: Password for Basic auth, or bearer token if no username.
        auth: A pre-built requests-compatible auth object. Takes precedence
              over username/password if provided.
        auth_type: Force a specific auth type: ``"basic"`` or ``"bearer"``.
        timeout: HTTP request timeout in seconds.

    Unless a method's own docstring says otherwise, its ``account_id``
    parameter is the JMAP account to operate on, defaulting to the
    authenticated user's own primary account; pass a different account
    (typically one shared with you via ``share_calendar``) to operate on
    that account's data instead.
    """

    def _get_http_session(self):
        """Return the persistent HTTP session, creating it on first call."""
        if self._http_session is None:
            sess = requests.Session()
            sess.auth = self._auth
            sess.headers.update({"Content-Type": "application/json", "Accept": "application/json"})
            self._http_session = sess
        return self._http_session

    def close(self) -> None:
        """Release the persistent HTTP session and its connection pool.

        Only needed when the client was not used as a context manager -- the
        documented Quick Start builds one directly, and without this there
        was no way to hand the sockets back.  Idempotent; the session is
        recreated on the next request.
        """
        if self._http_session is not None:
            self._http_session.close()
            self._http_session = None

    def __enter__(self) -> JMAPClient:
        self._get_http_session()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        ## Last-resort net for a client that was neither closed nor used as a
        ## context manager.  Interpreter shutdown can have torn down enough
        ## for this to fail, and an exception here is unraisable noise.
        try:
            self.close()
        except Exception:
            pass

    def _get_session(self) -> Session:
        """Return the cached Session, fetching it on first call."""
        if self._session_cache is None:
            self._session_cache = fetch_session(self.url, auth=self._auth, timeout=self.timeout)
        return self._session_cache

    def _request(self, method_calls: list[tuple], using: list[str] | None = None) -> list:
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
            requests.HTTPError: On other non-2xx HTTP responses.
        """
        session = self._get_session()

        payload = {
            "using": using if using is not None else _DEFAULT_USING,
            "methodCalls": list(method_calls),
        }

        log.debug("JMAP POST to %s: %d method call(s)", session.api_url, len(method_calls))

        response = self._get_http_session().post(
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

    def get_calendars(self, account_id: str | None = None) -> list[JMAPCalendar[Literal[False]]]:
        """Fetch all calendars for an account.

        Args:
            account_id: Pass a different account here to browse calendars
                another user has shared with you, once ``share_calendar``
                has granted access; the session only lists accounts you
                can actually reach (see ``Session.raw["accounts"]``).

        Returns:
            List of :class:`~calendaring_jmap.objects.calendar.JMAPCalendar` objects.
        """
        session = self._get_session()
        target_account = self._resolve_account(session, account_id)
        responses = self._request([build_calendar_get(target_account)])
        return self._parse_get_calendars(responses, self, False, account_id=target_account)

    def create_calendar(
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
        session = self._get_session()
        cal: dict = {"name": name}
        if color is not None:
            cal["color"] = color
        if timezone is not None:
            cal["timeZone"] = timezone
        call = build_calendar_set_create(session.account_id, {"new-0": cal})
        responses = self._request([call])
        return self._parse_create_response(
            responses, session.api_url, "Calendar/set", parse_calendar_set
        )

    def _update_calendar_patch(
        self, calendar_id: str, patch: dict, account_id: str | None = None
    ) -> None:
        session = self._get_session()
        target_account = self._resolve_account(session, account_id)
        call = build_calendar_set_update(target_account, {calendar_id: patch})
        responses = self._request([call])
        self._parse_update_response(
            responses, session.api_url, "Calendar/set", parse_calendar_set, calendar_id
        )

    def update_calendar(
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
            account_id: The account owning ``calendar_id``; pass the owner's
                account here to update a calendar shared with you.

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
        self._update_calendar_patch(calendar_id, patch, account_id=account_id)

    def delete_calendar(
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
            account_id: The account owning ``calendar_id``; pass the owner's
                account here to delete a calendar shared with you.

        Raises:
            JMAPMethodError: If the server rejects the delete. ``error_type``
                is ``"calendarHasEvent"`` when the calendar still has events
                and ``on_destroy_remove_events`` was left ``False``.
        """
        session = self._get_session()
        target_account = self._resolve_account(session, account_id)
        call = build_calendar_set_destroy(target_account, [calendar_id], on_destroy_remove_events)
        responses = self._request([call])
        self._parse_delete_response(
            responses, session.api_url, "Calendar/set", parse_calendar_set, calendar_id
        )

    def share_calendar(
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
            owning_account_id: The account owning ``calendar_id``; pass the
                owner's account here to re-share a calendar that was
                itself shared with you (if you hold ``mayShare`` on it).

        Raises:
            JMAPMethodError: If the server rejects the update, for example
                with ``forbidden`` when the caller lacks ``mayShare``.
        """
        self._update_calendar_patch(
            calendar_id, {f"shareWith/{account_id}": rights}, account_id=owning_account_id
        )

    def get_calendar_subscriptions(self) -> list[JMAPCalendar[Literal[False]]]:
        """Fetch the calendars the authenticated account is subscribed to.

        JMAP Calendars has no separate subscription object; this is
        ``get_calendars()`` filtered to ``is_subscribed``.

        Returns:
            List of :class:`~calendaring_jmap.objects.calendar.JMAPCalendar`
            objects with ``is_subscribed`` set.
        """
        return [cal for cal in self.get_calendars() if cal.is_subscribed]

    def set_default_alerts(
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
            account_id: The account owning ``calendar_id``; pass the owner's
                account here to set default alerts on a calendar shared
                with you.

        Raises:
            JMAPMethodError: If the server rejects the update.
        """
        patch: dict = {}
        if alerts_with_time is not None:
            patch["defaultAlertsWithTime"] = alerts_with_time
        if alerts_without_time is not None:
            patch["defaultAlertsWithoutTime"] = alerts_without_time
        self._update_calendar_patch(calendar_id, patch, account_id=account_id)

    def _create_event_impl(
        self,
        calendar_id: str,
        ical_str: str,
        account_id: str | None,
        send_scheduling_messages: bool,
    ) -> str:
        session = self._get_session()
        jscal = ical_to_jscal(ical_str, calendar_id=calendar_id)
        target_account = self._resolve_account(session, account_id)
        call = build_event_set_create(
            target_account, {"new-0": jscal}, send_scheduling_messages=send_scheduling_messages
        )
        responses = self._request([call])
        return self._parse_create_response(
            responses, session.api_url, "CalendarEvent/set", parse_event_set
        )

    def create_event(self, calendar_id: str, ical_str: str, account_id: str | None = None) -> str:
        """Create a calendar event from an iCalendar string.

        Args:
            calendar_id: The JMAP calendar ID to create the event in.
            ical_str: A VCALENDAR string representing the event.
            account_id: The account owning ``calendar_id``; pass the owner's
                account here to add an event to a calendar shared with you
                (see ``get_calendars(account_id=...)``).

        Returns:
            The server-assigned JMAP event ID.

        Raises:
            JMAPMethodError: If the server rejects the create request.
            ValueError: If ``ical_str`` is malformed in a way ``ical_to_jscal``
                rejects (missing ``UID``/``DTSTART``, a negative duration,
                mismatched ``DTSTART``/``DTEND`` value types).
        """
        return self._create_event_impl(
            calendar_id, ical_str, account_id, send_scheduling_messages=False
        )

    def send_invite(self, calendar_id: str, ical_str: str, account_id: str | None = None) -> str:
        """Create a calendar event and notify its participants.

        Identical to :meth:`create_event`, except the server actually
        dispatches iTIP invitations to the event's participants (an iCalendar
        string with ATTENDEE/ORGANIZER properties). ``create_event`` itself
        never sends scheduling messages; use this instead when the event has
        participants who need to be notified.

        Args:
            calendar_id: The JMAP calendar ID to create the event in.
            ical_str: A VCALENDAR string representing the event.
            account_id: The account owning ``calendar_id``, same as
                :meth:`create_event`.

        Returns:
            The server-assigned JMAP event ID.

        Raises:
            JMAPMethodError: If the server rejects the create request, or
                with ``error_type == "noSupportedScheduleMethods"`` if a
                participant has no usable delivery method.
            ValueError: Same conditions as :meth:`create_event`.
        """
        return self._create_event_impl(
            calendar_id, ical_str, account_id, send_scheduling_messages=True
        )

    def get_event(self, event_id: str, account_id: str | None = None) -> JMAPCalendarObject:
        """Fetch a calendar event by JMAP event ID.

        Args:
            event_id: The JMAP event ID to retrieve.
            account_id: The account owning ``event_id``; pass the owner's
                account here to fetch an event on a calendar shared with
                you.

        Returns:
            A :class:`~calendaring_jmap.objects.calendar_object.JMAPCalendarObject`
            wrapping the raw JSCalendar dict.  ``parent`` is ``None`` since
            no :class:`~calendaring_jmap.objects.calendar.JMAPCalendar` is available
            at the client level.

        Raises:
            JMAPMethodError: If the event is not found.
        """
        session = self._get_session()
        target_account = self._resolve_account(session, account_id)
        responses = self._request([build_event_get(target_account, ids=[event_id])])
        return self._parse_get_event_response(responses, session.api_url, event_id)

    def upload_attachment(
        self, data: bytes, content_type: str, account_id: str | None = None
    ) -> str:
        """Upload binary data as a JMAP blob (:rfc:`8620#section-6.1`).

        Unlike every other method on this client, this is a raw HTTP POST
        to the Session's ``uploadUrl``, not a JMAP method call: there is no
        ``methodResponses`` envelope, the server returns a flat JSON object
        instead.

        Args:
            data: The raw bytes to upload.
            content_type: Media type of ``data``, sent as the request's
                ``Content-Type`` header.
            account_id: The account to upload into.

        Returns:
            The server-assigned blob id, for use with
            :meth:`download_attachment` or :meth:`attach_to_event`.

        Raises:
            JMAPCapabilityError: If the server's Session object has no
                ``uploadUrl``.
            JMAPAuthError: On HTTP 401 or 403.
            requests.HTTPError: On any other non-2xx HTTP response.
        """
        session = self._get_session()
        target_account = self._resolve_account(session, account_id)
        upload_url = self._require_blob_url(session.upload_url, "uploadUrl", session.api_url)
        url = _expand_uri_template(upload_url, {"accountId": target_account})
        response = self._get_http_session().post(
            url, data=data, headers=self._build_upload_headers(content_type), timeout=self.timeout
        )
        self._check_blob_response(response, url)
        return response.json()["blobId"]

    def download_attachment(
        self,
        blob_id: str,
        content_type: str | None = None,
        filename: str | None = None,
        account_id: str | None = None,
    ) -> bytes:
        """Download a JMAP blob by id (:rfc:`8620#section-6.2`).

        Raw HTTP GET to the Session's ``downloadUrl``; the response body is
        the raw binary content.

        Args:
            blob_id: The blob id, as returned by :meth:`upload_attachment`.
            content_type: Media type of the blob, if known. Confirmed live
                against Cyrus and Stalwart that both look up a blob by
                ``blobId`` alone: an omitted or wrong ``content_type``/
                ``filename`` only affects the response's own
                ``Content-Type``/``Content-Disposition`` headers, not
                whether the download succeeds.
            filename: Filename to suggest for the download, if known.
            account_id: The account owning ``blob_id``.

        Returns:
            The blob's raw bytes.

        Raises:
            JMAPCapabilityError: If the server's Session object has no
                ``downloadUrl``.
            JMAPAuthError: On HTTP 401 or 403.
            requests.HTTPError: On any other non-2xx HTTP response, including
                404 for an unknown ``blob_id`` (confirmed live on both Cyrus
                and Stalwart).
        """
        session = self._get_session()
        target_account = self._resolve_account(session, account_id)
        url = self._build_blob_download_url(
            session, target_account, blob_id, content_type or "", filename or ""
        )
        response = self._get_http_session().get(
            url, headers=self._build_download_headers(), timeout=self.timeout
        )
        self._check_blob_response(response, url)
        ## requests.Response.content is typed as bytes but can actually be
        ## None (status_code == 0 or raw is None, e.g. some connection-level
        ## failures represented as a response rather than an exception);
        ## _check_blob_response already confirmed a successful response to a
        ## GET, so this only guards a genuinely unusual case.
        return response.content or b""

    def attach_to_event(
        self,
        event_id: str,
        blob_id: str,
        name: str,
        content_type: str,
        account_id: str | None = None,
    ) -> None:
        """Attach an uploaded blob to a calendar event.

        Adds one Link to the event's ``links`` property (:rfc:`8984#section-4.2.7`)
        with ``rel: "enclosure"``. The Link's ``href`` is set to ``blob_id``'s own
        download URL, not ``blobId``: draft-ietf-jmap-calendars section 5.3 allows
        a Link to carry ``blobId`` directly, but confirmed live against Cyrus and
        Stalwart that neither server persists it (Cyrus rejects the update;
        Stalwart accepts it and silently drops the Link). ``href`` round-trips
        correctly on both.

        Not safe against a concurrent write to this event's ``links``: reads the
        current value, then replaces the whole property with the merged result,
        with no ``ifInState`` guard. Whichever write lands second wins silently.
        Serialize calls for a given ``event_id`` if that matters to your use case.

        Known Cyrus limitation: Cyrus accepts a Link with ``rel: "enclosure"``
        but does not reliably persist that property, so :meth:`get_event_attachments`
        may not find what this method just wrote. See
        :meth:`JMAPAttachment.is_attachment
        <calendaring_jmap.objects.attachment.JMAPAttachment.is_attachment>`
        for the full finding. Stalwart is unaffected.

        Args:
            event_id: The JMAP event ID to attach to.
            blob_id: The blob id, as returned by :meth:`upload_attachment`.
            name: Human-readable title for the attachment.
            content_type: Media type of the blob.
            account_id: The account owning ``event_id``.

        Raises:
            JMAPCapabilityError: If the server's Session object has no
                ``downloadUrl``.
            JMAPMethodError: If the event is not found, or the server
                rejects the update.
        """
        session = self._get_session()
        target_account = self._resolve_account(session, account_id)
        url = self._build_blob_download_url(session, target_account, blob_id, content_type, name)
        responses = self._request(
            [build_event_get(target_account, ids=[event_id], properties=["links"])]
        )
        event = self._parse_get_event_response(responses, session.api_url, event_id)
        patch = self._build_attachment_patch(event.data.get("links", {}), name, content_type, url)
        call = build_event_set_update(target_account, {event_id: patch})
        responses = self._request([call])
        self._parse_update_response(
            responses, session.api_url, "CalendarEvent/set", parse_event_set, event_id
        )

    def get_event_attachments(
        self, event_id: str, account_id: str | None = None
    ) -> list[JMAPAttachment]:
        """Return the attachments on a calendar event.

        Args:
            event_id: The JMAP event ID to inspect.
            account_id: The account owning ``event_id``.

        Returns:
            List of :class:`~calendaring_jmap.objects.attachment.JMAPAttachment`,
            one per ``links`` entry whose ``rel`` is ``"enclosure"``
            (see :meth:`JMAPAttachment.is_attachment
            <calendaring_jmap.objects.attachment.JMAPAttachment.is_attachment>`
            for the known Cyrus limitation). Other ``links`` entries (e.g. a
            conference URL) are not included.

        Raises:
            JMAPMethodError: If the event is not found.
        """
        session = self._get_session()
        target_account = self._resolve_account(session, account_id)
        responses = self._request(
            [build_event_get(target_account, ids=[event_id], properties=["links"])]
        )
        event = self._parse_get_event_response(responses, session.api_url, event_id)
        return self._parse_event_attachments(event.data)

    def _find_own_participant_id(
        self, event_id: str, own_email: str, account_id: str | None = None
    ) -> str:
        """Return the participant id in ``event_id`` whose email is ``own_email``.

        JMAP participant ids are server-assigned per event and cannot be
        derived or guessed, so responding to an invitation requires fetching
        the live event first. Only the ``participants`` property is fetched,
        not the whole event. See :meth:`_find_participant_id_by_email` for
        the matching rules.

        Raises:
            JMAPMethodError: If the event has no participant matching
                ``own_email``.
        """
        session = self._get_session()
        target_account = self._resolve_account(session, account_id)
        responses = self._request(
            [build_event_get(target_account, ids=[event_id], properties=["participants"])]
        )
        event = self._parse_get_event_response(responses, session.api_url, event_id)
        return self._find_participant_id_by_email(
            event.data.get("participants", {}), own_email, session.api_url, event_id
        )

    def _respond_to_invitation(
        self,
        event_id: str,
        own_email: str,
        participation_status: str,
        account_id: str | None = None,
    ) -> None:
        session = self._get_session()
        target_account = self._resolve_account(session, account_id)
        participant_id = self._find_own_participant_id(event_id, own_email, account_id=account_id)
        patch = {f"participants/{participant_id}/participationStatus": participation_status}
        call = build_event_set_update(
            target_account, {event_id: patch}, send_scheduling_messages=True
        )
        responses = self._request([call])
        self._parse_update_response(
            responses, session.api_url, "CalendarEvent/set", parse_event_set, event_id
        )

    def accept_invitation(
        self, event_id: str, own_email: str, account_id: str | None = None
    ) -> None:
        """Accept a meeting invitation, notifying the organizer.

        Finds the participant on ``event_id`` matching ``own_email`` and
        sets their ``participationStatus`` to accepted. Only that one
        property is touched; per draft-ietf-jmap-calendars section 5.9, a
        non-origin account may never modify anything but its own
        participant properties.

        There is no counter-proposal method (iTIP COUNTER): neither
        :rfc:`8984` nor draft-ietf-jmap-calendars-29 define one. Confirmed
        live against Cyrus and Stalwart that a non-origin account can still
        write ``start``/``duration`` directly through the server's own
        rights model, but doing so is a plain update outside the section 5.9
        per-user-property restriction above, not a supported scheduling
        primitive, and is not exposed as a method here.

        Args:
            event_id: The JMAP event ID to respond to.
            own_email: The email address identifying which participant on
                the event is you.
            account_id: The account owning ``event_id``.

        Raises:
            JMAPMethodError: If no participant matches ``own_email``, or if
                the server rejects the update.
        """
        self._respond_to_invitation(
            event_id, own_email, PARTICIPATION_STATUS_ACCEPTED, account_id=account_id
        )

    def decline_invitation(
        self, event_id: str, own_email: str, account_id: str | None = None
    ) -> None:
        """Decline a meeting invitation, notifying the organizer.

        Same as :meth:`accept_invitation`, setting ``participationStatus``
        to declined instead.
        """
        self._respond_to_invitation(
            event_id, own_email, PARTICIPATION_STATUS_DECLINED, account_id=account_id
        )

    def tentatively_accept(
        self, event_id: str, own_email: str, account_id: str | None = None
    ) -> None:
        """Tentatively accept a meeting invitation, notifying the organizer.

        Same as :meth:`accept_invitation`, setting ``participationStatus``
        to tentative instead.
        """
        self._respond_to_invitation(
            event_id, own_email, PARTICIPATION_STATUS_TENTATIVE, account_id=account_id
        )

    def update_event(self, event_id: str, ical_str: str, account_id: str | None = None) -> None:
        """Update a calendar event from an iCalendar string.

        Args:
            event_id: The JMAP event ID to update.
            ical_str: A VCALENDAR string with the updated event data.
            account_id: The account owning ``event_id``; pass the owner's
                account here to update an event on a calendar shared with
                you.

        Raises:
            JMAPMethodError: If the server rejects the update.
            ValueError: If ``ical_str`` is malformed in a way ``ical_to_jscal``
                rejects (missing ``UID``/``DTSTART``, a negative duration,
                mismatched ``DTSTART``/``DTEND`` value types).
        """
        session = self._get_session()
        target_account = self._resolve_account(session, account_id)
        patch, nulled = self._build_event_update_patch(ical_str)
        while True:
            responses = self._request([build_event_set_update(target_account, {event_id: patch})])
            drop = self._unsupported_null_keys(responses, event_id, patch, nulled)
            if not drop:
                break
            for key in drop:
                patch.pop(key, None)
        self._parse_update_response(
            responses, session.api_url, "CalendarEvent/set", parse_event_set, event_id
        )

    def _search(
        self,
        calendar_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
        text: str | None = None,
        parent: JMAPCalendar | None = None,
        account_id: str | None = None,
    ) -> list[JMAPCalendarObject]:
        session = self._get_session()
        calls = self._build_event_search_calls(
            self._resolve_account(session, account_id),
            calendar_id,
            start,
            end,
            text,
        )
        responses = self._request(calls)
        return self._parse_search_response(responses, parent)

    def search_events(
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
            account_id: Pass a different account here to search a calendar
                shared with you.

        Returns:
            List of :class:`~calendaring_jmap.objects.calendar_object.JMAPCalendarObject`
            instances.  ``parent`` is ``None`` on these objects since no
            :class:`~calendaring_jmap.objects.calendar.JMAPCalendar` is available at
            the client level; use :meth:`JMAPCalendar.search` if you need ``parent``
            set.
        """
        return self._search(
            calendar_id=calendar_id, start=start, end=end, text=text, account_id=account_id
        )

    def get_availability(
        self,
        account_ids: list[str],
        start: str,
        end: str,
        show_details: bool = False,
    ) -> dict[str, list[BusyInterval]]:
        """Return busy intervals for each of your own accounts over a time period.

        Tries ``Principal/getAvailability`` (draft-ietf-jmap-calendars
        section 2.2) first, using the current user's own Principal id (from
        the session, no extra round trip). Falls back per account to a
        ``CalendarEvent/query`` scan of that account's own calendars when
        the account doesn't advertise :rfc:`9670` Principal support at all, the
        call fails with ``unknownMethod``/``accountNotSupportedByMethod``, or
        ``account_id`` isn't the session's own account.

        ``Principal/getAvailability`` never carries an explicit ``accountId``
        (confirmed live: Cyrus rejects one in this method's own args), so it
        can only ever report on the session's own account; any other entry
        in ``account_ids`` always goes through the fallback, which does
        scope to an explicit account. Only ever reports your own
        availability, never another user's: checking someone else's by
        email would need resolving that email to a Principal id via
        ``Principal/query``, which this client doesn't implement yet (the
        same limitation :meth:`share_calendar` already has for Principal
        ids in general).

        Args:
            account_ids: JMAP accounts to check, one entry in the result per
                account. Only the session's own account can use the primary
                path; other accounts always use the fallback.
            start: Start of the period, inclusive (``YYYY-MM-DDTHH:MM:SS``,
                treated as UTC; matches :meth:`search_events`'s convention).
            end: End of the period, exclusive (``YYYY-MM-DDTHH:MM:SS``,
                treated as UTC).
            show_details: If true, populate each interval's ``event`` where
                permitted. Confirmed live: on Stalwart this needs the
                account's server to support returning event details at all;
                omitting ``eventProperties`` there returns ``event: None``
                even with ``show_details=True``. On Cyrus, details are
                returned by default.

        Returns:
            Dict mapping each account id to its list of
            :class:`~calendaring_jmap.objects.busy_interval.BusyInterval` objects.

        Raises:
            JMAPMethodError: If ``Principal/getAvailability`` fails for a
                reason other than lack of support (e.g. ``forbidden``,
                ``tooLarge``), or if the fallback's ``CalendarEvent/query``
                fails (e.g. ``expandDurationTooLarge``, ``cannotCalculateOccurrences``
                for a window with too many recurrence instances to expand).
        """
        session = self._get_session()
        result: dict[str, list[BusyInterval]] = {}
        for account_id in account_ids:
            if self._can_use_principal_availability(session, account_id):
                principal_id = self._current_user_principal_id(session)
                if principal_id is not None:
                    try:
                        result[account_id] = self._get_availability_via_principal(
                            principal_id, start, end, show_details
                        )
                        continue
                    except JMAPMethodError as e:
                        if not self._should_fall_back_to_query(e.error_type):
                            raise
            result[account_id] = self._get_availability_via_fallback(account_id, start, end)
        return result

    def _get_availability_via_principal(
        self, principal_id: str, start: str, end: str, show_details: bool
    ) -> list[BusyInterval]:
        session = self._get_session()
        call = build_get_availability(
            principal_id,
            self._as_utc_datetime(start),
            self._as_utc_datetime(end),
            show_details=show_details,
        )
        responses = self._request([call], using=_PRINCIPALS_USING)
        for method_name, resp_args, _ in responses:
            if method_name == "Principal/getAvailability":
                return [BusyInterval.from_jmap(bp) for bp in parse_get_availability(resp_args)]
        raise JMAPMethodError(url=session.api_url, reason="No Principal/getAvailability response")

    def _get_availability_via_fallback(
        self, account_id: str, start: str, end: str
    ) -> list[BusyInterval]:
        calls = self._build_availability_fallback_calls(account_id, start, end)
        responses = self._request(calls)
        return self._parse_availability_fallback_response(responses)

    def get_address_books(self, account_id: str | None = None) -> list[JMAPAddressBook]:
        """Fetch all address books for an account.

        A server that doesn't advertise :rfc:`9610` Contacts support at all
        does not raise here: it logs a warning and returns an empty list
        instead, regardless of ``account_id``. For a server that does
        support Contacts in general but a different ``account_id`` lacks
        it specifically, a genuine error surfaces normally instead, the
        same as :meth:`search_contacts` always does (see
        :meth:`_can_skip_contacts_request` for why only the session's own
        account gets the graceful behavior).

        Args:
            account_id: Pass a different account here to browse address
                books another user has shared with you. Defaults to the
                session's own (calendar) account.

        Returns:
            List of :class:`~calendaring_jmap.objects.contact.JMAPAddressBook`
            objects, or ``[]`` if Contacts isn't supported (see above).

        Raises:
            JMAPMethodError: If a different account's own lack of Contacts
                support surfaces as a method-level error (e.g.
                ``accountNotSupportedByMethod``), or the request otherwise
                fails.
            requests.HTTPError: If the request otherwise fails at the HTTP
                level.
        """
        session = self._get_session()
        target_account = self._resolve_account(session, account_id)
        if self._can_skip_contacts_request(session, target_account):
            self._warn_contacts_unsupported(target_account)
            return []
        responses = self._request([build_address_book_get(target_account)], using=_CONTACTS_USING)
        return self._parse_get_address_books(responses)

    def search_contacts(
        self,
        text: str | None = None,
        email: str | None = None,
        account_id: str | None = None,
    ) -> list[JMAPContact]:
        """Search for contact cards.

        All parameters are optional; omitting both returns every contact in
        the account. Results are fetched in a single batched JMAP request
        using a result reference from ``ContactCard/query`` into
        ``ContactCard/get``, the same pattern :meth:`search_events` uses.

        Unlike :meth:`get_address_books`, an account that doesn't advertise
        :rfc:`9610` Contacts support raises normally here, not an empty
        list: this method's whole purpose is resolving contact information
        for something the caller is about to act on (e.g. an invitation),
        so silently returning nothing risks going unnoticed. Confirmed live
        that the actual error is either a request-level ``requests.HTTPError``
        (HTTP 400, ``unknownCapability``) if the server has no Contacts
        support at all, or a method-level ``JMAPMethodError`` with
        ``error_type`` ``"accountNotSupportedByMethod"`` if the server
        supports Contacts but this particular account doesn't.

        Args:
            text: Free-text search across the whole card. Confirmed live
                that Cyrus matches this as a substring against the name, not
                exact-only. Confirmed live that Stalwart v0.16.21 does not
                match this against the card's name at all, only against the
                email address (the same reach ``email`` already has there):
                a name-only search that works on Cyrus can silently return
                nothing on Stalwart. Use ``email`` when the value being
                searched for might be an email address.
            email: Match against any address in the card's ``emails``.
                Confirmed live that both Cyrus and Stalwart match this as a
                substring too, e.g. ``"alice"`` matches
                ``"alice@example.com"``.
            account_id: Pass a different account here to search an address
                book shared with you.

        Returns:
            List of :class:`~calendaring_jmap.objects.contact.JMAPContact` instances.

        Raises:
            JMAPMethodError: If the account doesn't support Contacts (see
                above), or the request otherwise fails.
            requests.HTTPError: If the server has no Contacts support at all
                (see above).
        """
        session = self._get_session()
        target_account = self._resolve_account(session, account_id)
        calls = self._build_contact_search_calls(target_account, text, email)
        responses = self._request(calls, using=_CONTACTS_USING)
        return self._parse_search_contacts_response(responses)

    def get_sync_token(self) -> str:
        """Return the current CalendarEvent state string for use as a sync token.

        Calls ``CalendarEvent/get`` with an empty ID list, so no event data
        is transferred, only the ``state`` field from the response.

        Returns:
            Opaque state string. Pass to :meth:`get_objects_by_sync_token` to
            retrieve only what changed since this point.
        """
        session = self._get_session()
        responses = self._request([build_event_get(session.account_id, ids=[])])
        return self._parse_get_sync_token_response(responses, session.api_url)

    def get_objects_by_sync_token(
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
        session = self._get_session()
        responses = self._request([build_event_changes(session.account_id, sync_token)])
        created_ids, updated_ids, destroyed, new_sync_token = self._parse_event_changes_response(
            responses, session.api_url
        )
        fetch_ids = created_ids + updated_ids
        if not fetch_ids:
            return [], [], destroyed, new_sync_token
        get_responses = self._request([build_event_get(session.account_id, ids=fetch_ids)])
        return self._assemble_sync_token_result(
            get_responses, created_ids, updated_ids, destroyed, new_sync_token
        )

    def delete_event(
        self,
        event_id: str,
        account_id: str | None = None,
        send_scheduling_messages: bool = False,
    ) -> None:
        """Delete a calendar event.

        Args:
            event_id: The JMAP event ID to delete.
            account_id: The account owning ``event_id``; pass the owner's
                account here to delete an event on a calendar shared with
                you.
            send_scheduling_messages: If true, and this account is the
                event's origin, the server sends an iTIP CANCEL to the
                event's participants (draft-ietf-jmap-calendars section 5.9.2.2).

        Raises:
            JMAPMethodError: If the server rejects the delete.
        """
        session = self._get_session()
        target_account = self._resolve_account(session, account_id)
        responses = self._request(
            [build_event_set_destroy(target_account, [event_id], send_scheduling_messages)]
        )
        self._parse_delete_response(
            responses, session.api_url, "CalendarEvent/set", parse_event_set, event_id
        )

    def _get_object_by_uid(
        self,
        uid: str,
        calendar_id: str | None = None,
        parent: JMAPCalendar | None = None,
        account_id: str | None = None,
    ) -> JMAPCalendarObject:
        # RFC 8984 FilterCondition has no uid field; UID matching is done client-side.
        for obj in self._search(calendar_id=calendar_id, parent=parent, account_id=account_id):
            if obj.data.get("uid") == uid:
                return obj

        raise JMAPMethodError(
            url=self._get_session().api_url, reason=f"No calendar object found with UID: {uid}"
        )

    def get_task_lists(self) -> list[dict]:
        """Fetch all task lists for the authenticated account.

        Returns:
            List of raw JMAP TaskList dicts as returned by the server.
        """
        session = self._get_session()
        responses = self._request([build_task_list_get(session.account_id)], using=_TASK_USING)
        return self._parse_get_task_lists_response(responses)

    def create_task(self, task_list_id: str, title: str, **kwargs) -> str:
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
        session = self._get_session()
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
        responses = self._request([call], using=_TASK_USING)
        return self._parse_create_response(responses, session.api_url, "Task/set", parse_task_set)

    def get_task(self, task_id: str) -> dict:
        """Fetch a task by ID.

        Args:
            task_id: The JMAP task ID to retrieve.

        Returns:
            Raw JMAP Task dict as returned by the server.

        Raises:
            JMAPMethodError: If the task is not found.
        """
        session = self._get_session()
        responses = self._request(
            [build_task_get(session.account_id, ids=[task_id])], using=_TASK_USING
        )
        return self._parse_get_task_response(responses, session.api_url, task_id)

    def update_task(self, task_id: str, patch: dict) -> None:
        """Update a task with a partial patch.

        Args:
            task_id: The JMAP task ID to update.
            patch: Partial patch dict mapping property names to new values.

        Raises:
            JMAPMethodError: If the server rejects the update.
        """
        session = self._get_session()
        call = build_task_set_update(session.account_id, {task_id: patch})
        responses = self._request([call], using=_TASK_USING)
        self._parse_update_response(responses, session.api_url, "Task/set", parse_task_set, task_id)

    def delete_task(self, task_id: str) -> None:
        """Delete a task.

        Args:
            task_id: The JMAP task ID to delete.

        Raises:
            JMAPMethodError: If the server rejects the delete.
        """
        session = self._get_session()
        responses = self._request(
            [build_task_set_destroy(session.account_id, [task_id])], using=_TASK_USING
        )
        self._parse_delete_response(responses, session.api_url, "Task/set", parse_task_set, task_id)
