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
from typing import Any, Literal

from calendaring_jmap._http import HTTPBasicAuth, HTTPBearerAuth, requests
from calendaring_jmap._methods.calendar import (
    build_calendar_get,
    build_calendar_set_create,
    build_calendar_set_destroy,
    build_calendar_set_update,
    parse_calendar_get,
    parse_calendar_set,
)
from calendaring_jmap._methods.event import (
    build_event_changes,
    build_event_get,
    build_event_query,
    build_event_set_create,
    build_event_set_destroy,
    build_event_set_update,
    parse_event_changes,
    parse_event_get,
    parse_event_set,
)
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
    CALENDAR_CAPABILITY,
    CORE_CAPABILITY,
    PARTICIPATION_STATUS_ACCEPTED,
    PARTICIPATION_STATUS_DECLINED,
    PARTICIPATION_STATUS_TENTATIVE,
    TASK_CAPABILITY,
)
from calendaring_jmap.convert import ical_to_jscal
from calendaring_jmap.convert._patch import _NULL_FOR_UPDATE
from calendaring_jmap.error import JMAPAuthError, JMAPMethodError
from calendaring_jmap.objects.calendar import JMAPCalendar
from calendaring_jmap.objects.calendar_object import JMAPCalendarObject
from calendaring_jmap.session import Session, fetch_session

log = logging.getLogger("calendaring_jmap")

_DEFAULT_USING = [CORE_CAPABILITY, CALENDAR_CAPABILITY]
_TASK_USING = [CORE_CAPABILITY, TASK_CAPABILITY]


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
    def _raise_set_error(api_url: str, err: dict) -> None:
        raise JMAPMethodError(
            url=api_url,
            reason=f"set failed: {err}",
            error_type=err.get("type", "serverError"),
        )

    @staticmethod
    def _build_event_search_calls(
        account_id: str,
        calendar_id: str | None,
        start: str | None,
        end: str | None,
        text: str | None,
    ) -> list[tuple]:
        """Return a batched [CalendarEvent/query, CalendarEvent/get] call list for _search."""
        filter_dict: dict = {}
        if calendar_id is not None:
            # JMAP Calendars draft-29 §5.11.1 defines this as "inCalendar"
            # (singular, one Id), not "inCalendars" (a list).
            filter_dict["inCalendar"] = calendar_id
        if start is not None:
            filter_dict["after"] = start
        if end is not None:
            filter_dict["before"] = end
        if text is not None:
            filter_dict["text"] = text
        query_call = build_event_query(account_id, filter_condition=filter_dict or None)
        get_call = (
            "CalendarEvent/get",
            {
                "accountId": account_id,
                "#ids": {
                    "resultOf": "ev-query-0",
                    "name": "CalendarEvent/query",
                    "path": "/ids",
                },
            },
            "ev-get-1",
        )
        return [query_call, get_call]

    @staticmethod
    def _build_event_update_patch(ical_str: str) -> tuple[dict, frozenset[str]]:
        """Build a JSCalendar PatchObject for a ``CalendarEvent/set`` update.

        RFC 8620 merge semantics preserve properties absent from the patch, so
        any optional property removed client-side must be explicitly nulled to
        actually clear it server-side.  Returns the patch together with the set
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
        Nulling such a property is harmless cleanup — it was absent from the new
        iCalendar — so we report it as droppable, letting the caller retry the
        update without it.

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
    # Shared response parsers — pure synchronous; used by both sync and async
    # clients.  Each method takes the raw ``methodResponses`` list returned by
    # ``_request()`` plus whatever extra context is needed to build the result
    # or raise an informative error, and returns/raises exactly what the public
    # method should return/raise.
    # ---------------------------------------------------------------------------

    @staticmethod
    def _parse_get_calendars(
        responses: list, client, is_async: bool, account_id: str | None = None
    ) -> list[JMAPCalendar[Any]]:
        for method_name, resp_args, _ in responses:
            if method_name == "Calendar/get":
                calendars = parse_calendar_get(resp_args)
                for cal in calendars:
                    cal._client = client
                    cal._is_async = is_async
                    cal._account_id = account_id
                return calendars
        return []

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
        raise JMAPMethodError(url=api_url, reason=f"No {set_method} response")

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
        raise JMAPMethodError(url=api_url, reason=f"No {set_method} response")

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
        raise JMAPMethodError(url=api_url, reason=f"No {set_method} response")

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
        for method_name, resp_args, _ in responses:
            if method_name == "CalendarEvent/get":
                return [
                    JMAPCalendarObject(data=item, parent=parent)
                    for item in parse_event_get(resp_args)
                ]
        return []

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
        for method_name, resp_args, _ in responses:
            if method_name == "TaskList/get":
                return parse_task_list_get(resp_args)
        return []

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
                error_type = resp_args.get("type", "serverError")
                raise JMAPMethodError(
                    url=session.api_url,
                    reason=f"Method call failed: {resp_args}",
                    error_type=error_type,
                )

        return method_responses

    def get_calendars(self, account_id: str | None = None) -> list[JMAPCalendar[Literal[False]]]:
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

        ``name``, ``color``, and ``timeZone`` are per-user properties (JMAP
        Calendars §4.3). Called by the calendar's owner, this changes the
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
            account_id: The JMAP account owning ``calendar_id``. Defaults to
                the authenticated user's own primary account; pass the
                owner's account here to delete a calendar shared with you.

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
        support yet (RFC 9670), so resolving an email address to a Principal
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
        to all-day events (JMAP Calendars §4). Each is a map of alert ID to
        Alert dict (RFC 8984 §4.5.2). Pass ``None`` to leave a property
        unchanged; pass ``{}`` to clear it.

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
            account_id: The JMAP account owning ``calendar_id``. Defaults to
                the authenticated user's own primary account; pass the
                owner's account here to add an event to a calendar shared
                with you (see ``get_calendars(account_id=...)``).

        Returns:
            The server-assigned JMAP event ID.

        Raises:
            JMAPMethodError: If the server rejects the create request.
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
            account_id: The JMAP account owning ``calendar_id``. Defaults to
                the authenticated user's own primary account.

        Returns:
            The server-assigned JMAP event ID.

        Raises:
            JMAPMethodError: If the server rejects the create request, or
                with ``error_type == "noSupportedScheduleMethods"`` if a
                participant has no usable delivery method.
        """
        return self._create_event_impl(
            calendar_id, ical_str, account_id, send_scheduling_messages=True
        )

    def get_event(self, event_id: str, account_id: str | None = None) -> JMAPCalendarObject:
        """Fetch a calendar event by JMAP event ID.

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
        session = self._get_session()
        target_account = self._resolve_account(session, account_id)
        responses = self._request([build_event_get(target_account, ids=[event_id])])
        return self._parse_get_event_response(responses, session.api_url, event_id)

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
        property is touched; per JMAP Calendars §5.9, a non-origin account
        may never modify anything but its own participant properties.

        There is no counter-proposal method (iTIP COUNTER): neither
        RFC 8984 nor draft-ietf-jmap-calendars-29 define one. Confirmed
        live against Cyrus and Stalwart that a non-origin account can still
        write ``start``/``duration`` directly through the server's own
        rights model, but doing so is a plain update outside the §5.9
        per-user-property restriction above, not a supported scheduling
        primitive, and is not exposed as a method here.

        Args:
            event_id: The JMAP event ID to respond to.
            own_email: The email address identifying which participant on
                the event is you.
            account_id: The JMAP account owning ``event_id``. Defaults to
                the authenticated user's own primary account.

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
            account_id: The JMAP account owning ``event_id``. Defaults to
                the authenticated user's own primary account; pass the
                owner's account here to update an event on a calendar
                shared with you.

        Raises:
            JMAPMethodError: If the server rejects the update.
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
            account_id: The JMAP account to search. Defaults to the
                authenticated user's own primary account; pass a different
                account here to search a calendar shared with you.

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

    def get_sync_token(self) -> str:
        """Return the current CalendarEvent state string for use as a sync token.

        Calls ``CalendarEvent/get`` with an empty ID list — no event data is
        transferred, only the ``state`` field from the response.

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
            account_id: The JMAP account owning ``event_id``. Defaults to
                the authenticated user's own primary account; pass the
                owner's account here to delete an event on a calendar
                shared with you.
            send_scheduling_messages: If true, and this account is the
                event's origin, the server sends an iTIP CANCEL to the
                event's participants (JMAP Calendars §5.9.2.2).

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
