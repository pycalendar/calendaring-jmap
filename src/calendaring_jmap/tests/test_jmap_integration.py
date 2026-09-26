# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Integration tests for calendaring-jmap against live JMAP servers.

Cyrus (port 8802):
    docker-compose -f tests/docker/cyrus/docker-compose.yml up -d

Stalwart (port 8809):
    docker-compose -f tests/docker/stalwart/docker-compose.yml up -d
    ./tests/docker/stalwart/setup_stalwart.sh

Each server's test classes are skipped automatically when that server is not
reachable, so a missing server is quiet, not a failure.
"""

import socket
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

try:
    from niquests.auth import HTTPBasicAuth
except ImportError:
    from requests.auth import HTTPBasicAuth  # type: ignore[assignment,no-redef]

from calendaring_jmap import AsyncJMAPClient, JMAPCalendarObject, JMAPClient
from calendaring_jmap._http import requests
from calendaring_jmap._methods import parse_set_response
from calendaring_jmap.client import _CONTACTS_USING, _JMAPClientBase
from calendaring_jmap.constants import CALENDAR_CAPABILITY, CONTACTS_CAPABILITY
from calendaring_jmap.convert import jscal_to_ical
from calendaring_jmap.error import JMAPMethodError
from calendaring_jmap.session import fetch_session

CYRUS_HOST = "localhost"
CYRUS_PORT = 8802
CYRUS_JMAP_URL = f"http://{CYRUS_HOST}:{CYRUS_PORT}/.well-known/jmap"
CYRUS_USERNAME = "user1"
CYRUS_PASSWORD = "x"
CYRUS_USERNAME_2 = "user2"
CYRUS_PASSWORD_2 = "x"

STALWART_HOST = "localhost"
STALWART_PORT = 8809
STALWART_JMAP_URL = f"http://{STALWART_HOST}:{STALWART_PORT}/.well-known/jmap"
STALWART_USERNAME = "testuser@example.org"
# user1@example.org (separate from STALWART_USERNAME above) is provisioned by
# setup_stalwart.sh specifically for multi-account tests like sharing.
STALWART_USERNAME_2 = "user1@example.org"
STALWART_PASSWORD_2 = "caldavtest1"
STALWART_PASSWORD = "testcaldav"

# Rights a scheduling test's second account needs on a shared calendar: read
# and write the events, plus mayRSVP to update its own participationStatus.
_SCHEDULING_RIGHTS = {"mayReadItems": True, "mayWriteAll": True, "mayRSVP": True}


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


_cyrus_up = _reachable(CYRUS_HOST, CYRUS_PORT)
_stalwart_up = _reachable(STALWART_HOST, STALWART_PORT)

# Applied per Cyrus test class, not module-wide. A module-level pytestmark
# would skip the Stalwart classes too whenever Cyrus is down, even if
# Stalwart itself is reachable.
_cyrus_skip = pytest.mark.skipif(
    not _cyrus_up,
    reason=f"Cyrus Docker not reachable on {CYRUS_HOST}:{CYRUS_PORT}. "
    "Start it with: docker-compose -f tests/docker/cyrus/docker-compose.yml up -d",
)


_DEFAULT_ICAL_START = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


def _vevent_ical(
    title: str,
    start: datetime,
    duration: timedelta,
    extra_lines: str = "",
) -> str:
    """Build a minimal single-VEVENT VCALENDAR string.

    Shared by :func:`_minimal_ical`, :func:`_recurring_ical`, and
    :func:`_invite_ical`, which each add their own ``extra_lines``
    (``RRULE``, ``ORGANIZER``/``ATTENDEE``) before ``END:VEVENT``.
    """
    end = start + duration
    uid = str(uuid.uuid4())
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//test//test//EN\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"SUMMARY:{title}\r\n"
        f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}\r\n"
        f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}\r\n"
        f"{extra_lines}"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )


def _minimal_ical(title: str = "Test Event", start: datetime | None = None) -> str:
    return _vevent_ical(title, start or _DEFAULT_ICAL_START, timedelta(hours=1))


def _recurring_ical(
    rrule: str,
    title: str = "Recurring Test Event",
    start: datetime | None = None,
    duration: timedelta = timedelta(hours=1),
) -> str:
    return _vevent_ical(
        title, start or _DEFAULT_ICAL_START, duration, extra_lines=f"RRULE:{rrule}\r\n"
    )


def _invite_ical(
    organizer_email: str,
    attendee_email: str,
    title: str = "Scheduling Test Event",
    start: datetime | None = None,
) -> str:
    extra_lines = (
        f"ORGANIZER:mailto:{organizer_email}\r\n"
        f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{attendee_email}\r\n"
    )
    return _vevent_ical(title, start or _DEFAULT_ICAL_START, timedelta(hours=1), extra_lines)


def _attendee_participation_status(event: JMAPCalendarObject, email: str) -> str:
    """Return ``participationStatus`` for the participant matching ``email``
    on a fetched event. Shared by every accept/decline/tentative scheduling
    test, which otherwise re-read the server's own copy of an invite the
    same way three times over."""
    attendee = next(p for p in event.get_data()["participants"].values() if p.get("email") == email)
    return attendee["participationStatus"]


@pytest.fixture(scope="module")
def client():
    return JMAPClient(url=CYRUS_JMAP_URL, username=CYRUS_USERNAME, password=CYRUS_PASSWORD)


@pytest.fixture(scope="module")
def session():
    return fetch_session(CYRUS_JMAP_URL, auth=HTTPBasicAuth(CYRUS_USERNAME, CYRUS_PASSWORD))


@pytest.fixture(scope="module")
def calendar_id(client):
    calendars = client.get_calendars()
    assert calendars, "Cyrus did not provision any calendars for user1"
    return calendars[0].id


@pytest.fixture
def created_event_id(client, calendar_id):
    event_id = client.create_event(calendar_id, _minimal_ical("Integration Test Event"))
    yield event_id
    try:
        client.delete_event(event_id)
    except Exception:
        pass


@pytest_asyncio.fixture
async def async_client():
    return AsyncJMAPClient(url=CYRUS_JMAP_URL, username=CYRUS_USERNAME, password=CYRUS_PASSWORD)


@pytest_asyncio.fixture
async def async_calendar_id(async_client):
    calendars = await async_client.get_calendars()
    assert calendars, "Cyrus did not provision any calendars for user1"
    return calendars[0].id


@pytest_asyncio.fixture
async def async_created_event_id(async_client, async_calendar_id):
    event_id = await async_client.create_event(
        async_calendar_id, _minimal_ical("Async Integration Test Event")
    )
    yield event_id
    try:
        await async_client.delete_event(event_id)
    except Exception:
        pass


_stalwart_skip = pytest.mark.skipif(
    not _stalwart_up,
    reason=f"Stalwart Docker not reachable on {STALWART_HOST}:{STALWART_PORT}. "
    "Start it with: cd tests/docker/stalwart && ./start.sh",
)


@pytest.fixture(scope="module")
def stalwart_client():
    return JMAPClient(url=STALWART_JMAP_URL, username=STALWART_USERNAME, password=STALWART_PASSWORD)


@pytest.fixture(scope="module")
def stalwart_calendar_id(stalwart_client):
    calendars = stalwart_client.get_calendars()
    assert calendars, "Stalwart did not return any calendars for user1"
    return calendars[0].id


@pytest.fixture
def stalwart_event_id(stalwart_client, stalwart_calendar_id):
    event_id = stalwart_client.create_event(
        stalwart_calendar_id, _minimal_ical("Stalwart Test Event")
    )
    yield event_id
    try:
        stalwart_client.delete_event(event_id)
    except Exception:
        pass


## Runs a test class against both Cyrus and Stalwart by parametrizing over a
## "server" fixture that indirection fixtures below (event_client, list_client,
## etc.) resolve to each server's own client/calendar_id/event_id fixtures via
## getfixturevalue(). Both servers speak the same synchronous API, so the test
## bodies are identical; only the fixture wiring differs.
_sync_servers = pytest.mark.parametrize(
    "server",
    [
        pytest.param("cyrus", marks=_cyrus_skip),
        pytest.param("stalwart", marks=_stalwart_skip),
    ],
)


@_cyrus_skip
class TestJMAPSessionIntegration:
    def test_session_fetch_returns_api_url(self, session):
        assert session.api_url
        assert session.api_url.startswith("http")

    def test_session_has_account_id(self, session):
        assert session.account_id

    def test_session_has_calendar_capability(self, session):
        assert CALENDAR_CAPABILITY in session.account_capabilities


@pytest.fixture
def list_client(request, server):
    return request.getfixturevalue("client" if server == "cyrus" else "stalwart_client")


@_sync_servers
class TestJMAPCalendarListIntegration:
    def test_list_calendars_returns_list(self, list_client):
        calendars = list_client.get_calendars()
        assert isinstance(calendars, list)

    def test_calendars_have_id_and_name(self, list_client, server):
        calendars = list_client.get_calendars()
        assert len(calendars) >= 1, f"Expected at least one calendar on {server} for user1"
        for cal in calendars:
            assert cal.id, f"Calendar missing id: {cal}"
            assert cal.name, f"Calendar has empty name: {cal}"


@pytest.fixture(scope="module")
def second_client():
    return JMAPClient(url=CYRUS_JMAP_URL, username=CYRUS_USERNAME_2, password=CYRUS_PASSWORD_2)


@pytest.fixture(scope="module")
def stalwart_second_client():
    return JMAPClient(
        url=STALWART_JMAP_URL, username=STALWART_USERNAME_2, password=STALWART_PASSWORD_2
    )


@pytest.fixture
def calendar_management_client(request, server):
    return request.getfixturevalue("client" if server == "cyrus" else "stalwart_client")


@pytest.fixture
def second_account_client(request, server):
    return request.getfixturevalue(
        "second_client" if server == "cyrus" else "stalwart_second_client"
    )


def _own_account_id(server: str, primary: bool) -> str:
    """Resolve the account id a user's own session sees for itself, by
    opening a session as that user and reading it back. Shared by
    :func:`owner_account_id`/:func:`second_account_id`: both need this same
    self-resolution (this client has no Principal/query support yet, so
    there's no other way to look up someone else's account id)."""
    if server == "cyrus":
        url = CYRUS_JMAP_URL
        username = CYRUS_USERNAME if primary else CYRUS_USERNAME_2
        password = CYRUS_PASSWORD if primary else CYRUS_PASSWORD_2
    else:
        url = STALWART_JMAP_URL
        username = STALWART_USERNAME if primary else STALWART_USERNAME_2
        password = STALWART_PASSWORD if primary else STALWART_PASSWORD_2
    session = fetch_session(url, auth=HTTPBasicAuth(username, password))
    return session.account_id


@pytest.fixture
def second_account_id(server):
    """The account id a second user's own session resolves for itself.

    share_calendar() needs a resolved Principal/account id and this client
    has no Principal/query support yet, so the test resolves it the same way
    a caller without Principal support would have to: open a session as the
    target user and read what account id they see for themselves.
    """
    return _own_account_id(server, primary=False)


@pytest.fixture
def owner_account_id(server):
    """The account id calendar_management_client's own session resolves for itself.

    Used to browse the owner's account from the sharee's client after a
    share_calendar() call, since JMAP surfaces a shared calendar under the
    owner's accountId, not the sharee's own primary account.
    """
    return _own_account_id(server, primary=True)


@pytest.fixture
def owner_email(server):
    """The email address to put in ORGANIZER for calendar_management_client's own account."""
    return CYRUS_USERNAME + "@example.com" if server == "cyrus" else STALWART_USERNAME


@pytest.fixture
def second_account_email(server):
    """The email address to put in ATTENDEE, and pass as own_email, for second_account_client."""
    return CYRUS_USERNAME_2 + "@example.com" if server == "cyrus" else STALWART_USERNAME_2


@_sync_servers
class TestJMAPCalendarManagementIntegration:
    def test_create_update_delete_calendar(self, calendar_management_client):
        cal_id = calendar_management_client.create_calendar("Integration Test Calendar")
        assert cal_id

        calendar_management_client.update_calendar(cal_id, name="Renamed Calendar")
        calendars = calendar_management_client.get_calendars()
        renamed = next((c for c in calendars if c.id == cal_id), None)
        assert renamed is not None
        assert renamed.name == "Renamed Calendar"

        calendar_management_client.delete_calendar(cal_id)
        calendars = calendar_management_client.get_calendars()
        assert not any(c.id == cal_id for c in calendars)

    def test_delete_calendar_with_event_raises_calendar_has_event(self, calendar_management_client):
        cal_id = calendar_management_client.create_calendar("Calendar With Event")
        calendar_management_client.create_event(cal_id, _minimal_ical("Blocking Event"))
        try:
            with pytest.raises(JMAPMethodError) as exc_info:
                calendar_management_client.delete_calendar(cal_id)
            # JMAP Calendars draft-29 §10.7.1 defines this as "calendarHasEvent"
            # (singular). Cyrus sends "calendarHasEvents" (plural) instead; both
            # are accepted here since this is a server spelling quirk, not
            # something the client controls.
            assert exc_info.value.error_type in ("calendarHasEvent", "calendarHasEvents")
        finally:
            calendar_management_client.delete_calendar(cal_id, on_destroy_remove_events=True)

    def test_delete_calendar_with_event_and_on_destroy_remove_events(
        self, calendar_management_client
    ):
        cal_id = calendar_management_client.create_calendar("Calendar With Event To Remove")
        calendar_management_client.create_event(cal_id, _minimal_ical("Removable Event"))
        calendar_management_client.delete_calendar(cal_id, on_destroy_remove_events=True)
        calendars = calendar_management_client.get_calendars()
        assert not any(c.id == cal_id for c in calendars)

    def test_get_calendar_subscriptions_returns_own_calendars(self, calendar_management_client):
        # The client's own calendars are subscribed by default.
        subs = calendar_management_client.get_calendar_subscriptions()
        all_calendars = calendar_management_client.get_calendars()
        assert {c.id for c in subs} <= {c.id for c in all_calendars}
        assert all(c.is_subscribed for c in subs)

    def test_set_default_alerts(self, calendar_management_client):
        cal_id = calendar_management_client.create_calendar("Calendar With Default Alerts")
        try:
            alert = {
                "@type": "Alert",
                "trigger": {"@type": "OffsetTrigger", "offset": "-PT10M"},
            }
            calendar_management_client.set_default_alerts(cal_id, alerts_with_time={"a1": alert})
            calendars = calendar_management_client.get_calendars()
            updated = next((c for c in calendars if c.id == cal_id), None)
            assert updated is not None
            assert updated.default_alerts_with_time
        finally:
            calendar_management_client.delete_calendar(cal_id)

    def test_share_calendar_grants_visible_to_second_account(
        self,
        calendar_management_client,
        second_account_client,
        second_account_id,
        owner_account_id,
        server,
    ):
        """A shared calendar lives under the owner's accountId, not the
        sharee's own primary account (JMAP's multi-account model, RFC 8620
        section 2). The sharee's session gains access to the owner's account,
        so they browse it with get_calendars(account_id=owner_account_id)
        rather than their own default get_calendars()."""
        cal_id = calendar_management_client.create_calendar("Shared Integration Calendar")
        try:
            calendar_management_client.share_calendar(
                cal_id, second_account_id, {"mayReadItems": True}
            )
            owner_calendars = calendar_management_client.get_calendars()
            shared_on_owner_side = next((c for c in owner_calendars if c.id == cal_id), None)
            assert shared_on_owner_side is not None
            assert shared_on_owner_side.share_with is not None
            assert (
                shared_on_owner_side.share_with.get(second_account_id, {}).get("mayReadItems")
                is True
            )

            second_view = second_account_client.get_calendars(account_id=owner_account_id)
            shared = next((c for c in second_view if c.id == cal_id), None)
            assert shared is not None, (
                f"{server}: calendar shared with account id {second_account_id!r} was not "
                f"visible to the second account browsing owner account {owner_account_id!r}."
            )
            assert shared.my_rights.get("mayReadItems") is True
        finally:
            calendar_management_client.delete_calendar(cal_id)

    def test_add_event_to_shared_calendar_targets_owner_account(
        self,
        calendar_management_client,
        second_account_client,
        second_account_id,
        owner_account_id,
        server,
    ):
        """get_calendars(account_id=...) binds the requested account onto the
        returned JMAPCalendar, so add_event() on that object creates the
        event under the owner's account rather than the sharee's own. This
        is the fix for a gap where a calendar fetched from someone else's
        account silently routed writes back to the caller's own account."""
        cal_id = calendar_management_client.create_calendar("Write Shared Integration Calendar")
        try:
            calendar_management_client.share_calendar(
                cal_id, second_account_id, {"mayReadItems": True, "mayWriteAll": True}
            )
            second_view = second_account_client.get_calendars(account_id=owner_account_id)
            shared = next((c for c in second_view if c.id == cal_id), None)
            assert shared is not None, (
                f"{server}: calendar not visible to second account for write test."
            )

            event_id = shared.add_event(_minimal_ical("Written By Sharee"))
            assert event_id

            owner_events = calendar_management_client.search_events(calendar_id=cal_id)
            assert any(obj.id == event_id for obj in owner_events), (
                f"{server}: event created via the shared calendar object was not visible "
                f"in the owner's own account; add_event() likely targeted the wrong account."
            )
        finally:
            calendar_management_client.delete_calendar(cal_id, on_destroy_remove_events=True)

    def test_edit_and_delete_event_on_shared_calendar_targets_owner_account(
        self,
        calendar_management_client,
        second_account_client,
        second_account_id,
        owner_account_id,
        server,
    ):
        """save() and delete_event() must route through the calendar's owning
        account too, the same way add_event() does. Both take the object's
        parent._account_id, not the sharee's own session account."""
        cal_id = calendar_management_client.create_calendar("Edit Shared Integration Calendar")
        try:
            calendar_management_client.share_calendar(
                cal_id, second_account_id, {"mayReadItems": True, "mayWriteAll": True}
            )
            event_id = calendar_management_client.create_event(
                cal_id, _minimal_ical("Owner Created Event")
            )

            second_view = second_account_client.get_calendars(account_id=owner_account_id)
            shared = next((c for c in second_view if c.id == cal_id), None)
            assert shared is not None, f"{server}: calendar not visible to second account."

            obj = shared.get_object_by_uid(next(o.data["uid"] for o in shared.search()))
            with obj.edit_icalendar_instance() as cal:
                cal.subcomponents[0]["SUMMARY"] = "Edited By Sharee"
            obj.save()

            updated = calendar_management_client.get_event(event_id)
            assert updated.get_data()["title"] == "Edited By Sharee", (
                f"{server}: edit made via the shared calendar object's save() was not "
                f"visible in the owner's own account; update_event() likely targeted "
                f"the wrong account."
            )

            second_account_client.delete_event(event_id, account_id=owner_account_id)
            with pytest.raises(JMAPMethodError):
                calendar_management_client.get_event(event_id)
        finally:
            calendar_management_client.delete_calendar(cal_id, on_destroy_remove_events=True)


@_sync_servers
class TestJMAPSchedulingIntegration:
    """Being invited to an event does not by itself grant account access to
    the invitee (confirmed live: an invited-but-not-shared participant gets
    accountNotFound browsing the organizer's account). Every test here
    shares the calendar first, the same prerequisite #11's cross-account
    tests already establish, then exercises the scheduling methods on top."""

    def test_send_invite_and_accept(
        self,
        calendar_management_client,
        second_account_client,
        second_account_id,
        owner_account_id,
        owner_email,
        second_account_email,
        server,
    ):
        cal_id = calendar_management_client.create_calendar("Scheduling Integration Calendar")
        try:
            calendar_management_client.share_calendar(cal_id, second_account_id, _SCHEDULING_RIGHTS)
            ical = _invite_ical(owner_email, second_account_email, title="Accept Me")
            event_id = calendar_management_client.send_invite(cal_id, ical)

            second_account_client.accept_invitation(
                event_id, second_account_email, account_id=owner_account_id
            )

            updated = calendar_management_client.get_event(event_id)
            status = _attendee_participation_status(updated, second_account_email)
            assert status == "accepted", (
                f"{server}: accept_invitation did not update participationStatus "
                f"on the organizer's own copy of the event."
            )
        finally:
            calendar_management_client.delete_calendar(cal_id, on_destroy_remove_events=True)

    def test_decline_invitation(
        self,
        calendar_management_client,
        second_account_client,
        second_account_id,
        owner_account_id,
        owner_email,
        second_account_email,
        server,
    ):
        cal_id = calendar_management_client.create_calendar("Decline Integration Calendar")
        try:
            calendar_management_client.share_calendar(cal_id, second_account_id, _SCHEDULING_RIGHTS)
            ical = _invite_ical(owner_email, second_account_email, title="Decline Me")
            event_id = calendar_management_client.send_invite(cal_id, ical)

            second_account_client.decline_invitation(
                event_id, second_account_email, account_id=owner_account_id
            )

            updated = calendar_management_client.get_event(event_id)
            assert _attendee_participation_status(updated, second_account_email) == "declined"
        finally:
            calendar_management_client.delete_calendar(cal_id, on_destroy_remove_events=True)

    def test_tentatively_accept_invitation(
        self,
        calendar_management_client,
        second_account_client,
        second_account_id,
        owner_account_id,
        owner_email,
        second_account_email,
        server,
    ):
        cal_id = calendar_management_client.create_calendar("Tentative Integration Calendar")
        try:
            calendar_management_client.share_calendar(cal_id, second_account_id, _SCHEDULING_RIGHTS)
            ical = _invite_ical(owner_email, second_account_email, title="Maybe Me")
            event_id = calendar_management_client.send_invite(cal_id, ical)

            second_account_client.tentatively_accept(
                event_id, second_account_email, account_id=owner_account_id
            )

            updated = calendar_management_client.get_event(event_id)
            assert _attendee_participation_status(updated, second_account_email) == "tentative"
        finally:
            calendar_management_client.delete_calendar(cal_id, on_destroy_remove_events=True)

    def test_accept_invitation_raises_when_own_email_not_a_participant(
        self, calendar_management_client, server
    ):
        cal_id = calendar_management_client.create_calendar("No Participant Calendar")
        try:
            event_id = calendar_management_client.create_event(cal_id, _minimal_ical("Solo Event"))
            with pytest.raises(JMAPMethodError) as exc_info:
                calendar_management_client.accept_invitation(event_id, "nobody@example.invalid")
            assert exc_info.value.error_type == "notFound"
        finally:
            calendar_management_client.delete_calendar(cal_id, on_destroy_remove_events=True)

    def test_cancellation_from_organizer_removes_event_for_participant(
        self,
        calendar_management_client,
        second_account_client,
        second_account_id,
        owner_account_id,
        owner_email,
        second_account_email,
        server,
    ):
        """delete_event with send_scheduling_messages requests a CANCEL per
        JMAP Calendars section 5.9.2.2; the client has no way to inspect
        iMIP delivery directly, so the observable assertion is that the
        event is actually gone from the organizer's own account after
        cancellation."""
        cal_id = calendar_management_client.create_calendar("Cancellation Integration Calendar")
        try:
            calendar_management_client.share_calendar(cal_id, second_account_id, _SCHEDULING_RIGHTS)
            ical = _invite_ical(owner_email, second_account_email, title="Cancel Me")
            event_id = calendar_management_client.send_invite(cal_id, ical)
            second_account_client.accept_invitation(
                event_id, second_account_email, account_id=owner_account_id
            )

            calendar_management_client.delete_event(event_id, send_scheduling_messages=True)

            with pytest.raises(JMAPMethodError):
                calendar_management_client.get_event(event_id)
        finally:
            calendar_management_client.delete_calendar(cal_id, on_destroy_remove_events=True)

    def test_no_counter_proposal_mechanism(
        self,
        calendar_management_client,
        second_account_client,
        second_account_id,
        owner_account_id,
        owner_email,
        second_account_email,
        server,
    ):
        """Neither RFC 8984 nor draft-ietf-jmap-calendars-29 define an iTIP
        COUNTER equivalent (confirmed: no "counter" occurrence in either
        spec text). A non-origin participant has no dedicated method to
        propose a new time; this proves what the two test servers actually
        do with a direct start/duration update from that account, so the
        absence of a counter_propose() method here is a confirmed protocol
        gap rather than an unimplemented one."""
        cal_id = calendar_management_client.create_calendar("Counter Proposal Calendar")
        try:
            calendar_management_client.share_calendar(cal_id, second_account_id, _SCHEDULING_RIGHTS)
            ical = _invite_ical(owner_email, second_account_email, title="Propose New Time")
            event_id = calendar_management_client.send_invite(cal_id, ical)
            second_account_client.accept_invitation(
                event_id, second_account_email, account_id=owner_account_id
            )

            proposed = _invite_ical(
                owner_email,
                second_account_email,
                title="Propose New Time",
                start=datetime(2030, 6, 1, 15, 0, tzinfo=timezone.utc),
            )
            second_account_client.update_event(event_id, proposed, account_id=owner_account_id)

            updated = calendar_management_client.get_event(event_id)
            assert updated.get_data()["start"] == "2030-06-01T15:00:00", (
                f"{server}: a non-origin participant's direct start update was not applied "
                "as a plain write; no distinct counter-proposal semantics exist to test."
            )
        finally:
            calendar_management_client.delete_calendar(cal_id, on_destroy_remove_events=True)

    def test_sequence_increments_on_substantive_update(self, calendar_management_client, server):
        """JMAP Calendars section 5.9: the server MUST increment sequence when a
        non-per-user property changes and the account is the event's
        origin. The client never sets sequence itself; this proves the
        server does, rather than assuming it from the spec text alone.
        Confirmed live: Cyrus does this; Stalwart does not touch sequence
        on update at all, a real spec deviation, not a test bug."""
        if server == "stalwart":
            pytest.skip("Stalwart does not increment sequence on CalendarEvent/set update")
        cal_id = calendar_management_client.create_calendar("Sequence Integration Calendar")
        try:
            event_id = calendar_management_client.create_event(
                cal_id, _minimal_ical("Sequence Start")
            )
            before = calendar_management_client.get_event(event_id)
            sequence_before = before.get_data().get("sequence", 0)

            calendar_management_client.update_event(
                event_id, _minimal_ical("Sequence Changed Title")
            )

            after = calendar_management_client.get_event(event_id)
            sequence_after = after.get_data().get("sequence", 0)
            assert sequence_after > sequence_before, (
                f"{server}: sequence did not increment after a substantive "
                f"update ({sequence_before} -> {sequence_after})."
            )
        finally:
            calendar_management_client.delete_calendar(cal_id, on_destroy_remove_events=True)


@pytest.fixture
def event_client(request, server):
    return request.getfixturevalue("client" if server == "cyrus" else "stalwart_client")


@pytest.fixture
def event_calendar_id(request, server):
    return request.getfixturevalue("calendar_id" if server == "cyrus" else "stalwart_calendar_id")


@pytest.fixture
def event_created_id(request, server):
    return request.getfixturevalue("created_event_id" if server == "cyrus" else "stalwart_event_id")


@_sync_servers
class TestJMAPEventIntegration:
    def test_event_create_get(self, event_client, event_created_id):
        obj = event_client.get_event(event_created_id)
        assert obj.id == event_created_id
        ical = jscal_to_ical(obj.get_data())
        assert "BEGIN:VCALENDAR" in ical

    def test_event_update(self, event_client, event_created_id):
        event_client.update_event(event_created_id, _minimal_ical("Updated Title"))
        obj = event_client.get_event(event_created_id)
        assert "Updated Title" in jscal_to_ical(obj.get_data())

    def test_event_delete(self, event_client, event_calendar_id):
        event_id = event_client.create_event(event_calendar_id, _minimal_ical("To Be Deleted"))
        event_client.delete_event(event_id)
        with pytest.raises(JMAPMethodError):
            event_client.get_event(event_id)

    def test_event_query_time_range(self, event_client, event_calendar_id):
        title = "Query Range Test Event"
        event_id = event_client.create_event(event_calendar_id, _minimal_ical(title))
        try:
            results = event_client.search_events(
                calendar_id=event_calendar_id,
                start="2026-06-01T00:00:00",
                end="2026-06-02T00:00:00",
            )
            assert len(results) >= 1
            assert any(title in jscal_to_ical(r.get_data()) for r in results)
        finally:
            event_client.delete_event(event_id)

    def test_event_sync(self, server, event_client, event_calendar_id):
        if server == "stalwart":
            pytest.skip("Stalwart integration coverage does not include sync yet")
        token_before = event_client.get_sync_token()
        event_id = event_client.create_event(event_calendar_id, _minimal_ical("Sync Test Event"))
        try:
            added, _modified, _deleted, _new_token = event_client.get_objects_by_sync_token(
                token_before
            )
            assert any("Sync Test Event" in jscal_to_ical(a.get_data()) for a in added)
        finally:
            event_client.delete_event(event_id)

    def test_recurring_event_create_get_update(self, event_client, event_calendar_id, server):
        """Neither test server accepts RFC 8984's recurrenceRules array; both
        want the singular recurrenceRule instead (see ical_to_jscal.py). This
        is the only integration coverage for recurring events at all."""
        ical = _recurring_ical(
            "FREQ=WEEKLY;BYDAY=MO,WE,FR",
            title="Weekly Standup",
            start=datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc),
        )
        event_id = event_client.create_event(event_calendar_id, ical)
        try:
            obj = event_client.get_event(event_id)
            assert "recurrenceRule" in obj.get_data(), (
                f"{server}: created event lost its recurrence rule on read-back."
            )
            assert "RRULE" in jscal_to_ical(obj.get_data())

            updated_ical = ical.replace("Weekly Standup", "Weekly Standup Renamed")
            event_client.update_event(event_id, updated_ical)
            updated = event_client.get_event(event_id)
            assert "recurrenceRule" in updated.get_data(), (
                f"{server}: recurrence rule was lost after an unrelated update."
            )
            assert "Weekly Standup Renamed" in jscal_to_ical(updated.get_data())
        finally:
            event_client.delete_event(event_id)

    def test_ical_roundtrip(self, event_client, event_calendar_id):
        start = datetime(2026, 7, 15, 9, 0, 0, tzinfo=timezone.utc)
        event_id = event_client.create_event(
            event_calendar_id, _minimal_ical("Roundtrip Event", start=start)
        )
        try:
            fetched = jscal_to_ical(event_client.get_event(event_id).get_data())
            assert "Roundtrip Event" in fetched
            assert "20260715" in fetched
        finally:
            event_client.delete_event(event_id)

    def test_attach_property_roundtrip(self, event_client, event_calendar_id, server):
        """ical_to_jscal/jscal_to_ical convert ATTACH <-> links (rel:
        "enclosure"). On Cyrus, the fetched event's ATTACH is lost on the
        way back out, since it doesn't persist rel (see
        JMAPAttachment.is_attachment); Stalwart round-trips correctly.
        """
        ical = _vevent_ical(
            "Attach Roundtrip Event",
            datetime(2026, 7, 20, 9, 0, 0, tzinfo=timezone.utc),
            timedelta(hours=1),
            extra_lines="ATTACH;FMTTYPE=application/pdf:https://example.com/report.pdf\r\n",
        )
        event_id = event_client.create_event(event_calendar_id, ical)
        try:
            fetched = jscal_to_ical(event_client.get_event(event_id).get_data())
            if server == "cyrus":
                assert "ATTACH" not in fetched, (
                    "cyrus: if this now finds ATTACH, Cyrus may have fixed "
                    "the rel persistence bug; update this test accordingly."
                )
            else:
                assert "ATTACH" in fetched
                assert "https://example.com/report.pdf" in fetched
                assert "application/pdf" in fetched
        finally:
            event_client.delete_event(event_id)


@_sync_servers
class TestJMAPFreeBusyIntegration:
    """Confirmed live (both servers, this repo's own verification, not just
    the spec text) that Principal/getAvailability genuinely works on both
    Cyrus and Stalwart, so these tests exercise the real primary path, not
    the fallback. Neither test server lacks the base
    urn:ietf:params:jmap:principals capability. The fallback path's own
    mechanism (expandRecurrences) is proven correct in
    test_fallback_recurring_event_busy_intervals below by calling the
    private fallback helper directly, since there is no live server in this
    project's own test fixtures that actually takes that path by default."""

    def test_get_availability_empty_when_no_events(self, event_client, server):
        session = event_client._get_session()
        availability = event_client.get_availability(
            [session.account_id], "2026-09-19T00:00:00", "2026-09-20T00:00:00"
        )
        assert availability == {session.account_id: []}, f"{server}: expected no busy intervals"

    def test_get_availability_returns_busy_interval_for_event(
        self, event_client, event_calendar_id, server
    ):
        start = datetime(2026, 9, 21, 10, 0, 0, tzinfo=timezone.utc)
        session = event_client._get_session()
        event_id = event_client.create_event(
            event_calendar_id, _minimal_ical("Availability Test Event", start=start)
        )
        try:
            availability = event_client.get_availability(
                [session.account_id], "2026-09-19T00:00:00", "2026-09-26T00:00:00"
            )
            intervals = availability[session.account_id]
            assert len(intervals) == 1, f"{server}: expected exactly one busy interval"
            assert intervals[0].start == "2026-09-21T10:00:00Z"
            assert intervals[0].end == "2026-09-21T11:00:00Z"
        finally:
            event_client.delete_event(event_id)

    def test_get_availability_show_details(self, event_client, event_calendar_id, server):
        """Confirmed live: Cyrus returns full event details by default with
        show_details=True. Stalwart does not, since it additionally
        requires eventProperties to be set to one of its own narrow
        supported set, which get_availability doesn't currently pass; on
        Stalwart, event stays None even with show_details=True. Both
        behaviors are correct per this method's own documented contract."""
        title = "Details Test Event"
        start = datetime(2026, 9, 21, 14, 0, 0, tzinfo=timezone.utc)
        session = event_client._get_session()
        event_id = event_client.create_event(event_calendar_id, _minimal_ical(title, start=start))
        try:
            availability = event_client.get_availability(
                [session.account_id],
                "2026-09-19T00:00:00",
                "2026-09-26T00:00:00",
                show_details=True,
            )
            interval = availability[session.account_id][0]
            if server == "cyrus":
                assert interval.event is not None, f"{server}: expected event details"
                assert interval.event.get_data()["title"] == title
            else:
                assert interval.event is None, f"{server}: expected no event details"
        finally:
            event_client.delete_event(event_id)

    def test_fallback_recurring_event_busy_intervals(self, event_client, event_calendar_id, server):
        """Proves the fallback path's expandRecurrences mechanism directly,
        by calling its private helper: neither test server actually takes
        the fallback path through get_availability's own public entry point
        (both implement Principal/getAvailability), so this is the only way
        to give this mechanism live coverage. Without expandRecurrences,
        a recurring series returns one interval carrying only the master
        occurrence's own start, not the occurrences that actually fall in
        the window. Confirmed live during this feature's own planning."""
        ical = _recurring_ical(
            "FREQ=WEEKLY",
            title="Weekly Fallback Availability Test",
            start=datetime(2026, 9, 21, 9, 0, 0, tzinfo=timezone.utc),
            duration=timedelta(minutes=30),
        )
        session = event_client._get_session()
        event_id = event_client.create_event(event_calendar_id, ical)
        try:
            intervals = event_client._get_availability_via_fallback(
                session.account_id, "2026-09-19T00:00:00", "2026-10-10T00:00:00"
            )
            starts = sorted(i.start for i in intervals)
            assert starts == [
                "2026-09-21T09:00:00Z",
                "2026-09-28T09:00:00Z",
                "2026-10-05T09:00:00Z",
            ], f"{server}: expected one distinct busy interval per weekly occurrence"
        finally:
            event_client.delete_event(event_id)


@_cyrus_skip
class TestAsyncJMAPEventIntegration:
    @pytest.mark.asyncio
    async def test_event_create_get(self, async_client, async_created_event_id):
        obj = await async_client.get_event(async_created_event_id)
        ical = jscal_to_ical(obj.get_data())
        assert "BEGIN:VCALENDAR" in ical
        assert "Async Integration Test Event" in ical

    @pytest.mark.asyncio
    async def test_event_update(self, async_client, async_created_event_id):
        await async_client.update_event(
            async_created_event_id, _minimal_ical("Async Updated Title")
        )
        obj = await async_client.get_event(async_created_event_id)
        assert "Async Updated Title" in jscal_to_ical(obj.get_data())

    @pytest.mark.asyncio
    async def test_event_delete(self, async_client, async_calendar_id):
        event_id = await async_client.create_event(
            async_calendar_id, _minimal_ical("Async To Be Deleted")
        )
        await async_client.delete_event(event_id)
        with pytest.raises(JMAPMethodError):
            await async_client.get_event(event_id)

    @pytest.mark.asyncio
    async def test_event_query_time_range(
        self, async_client, async_calendar_id, async_created_event_id
    ):
        results = await async_client.search_events(
            calendar_id=async_calendar_id,
            start="2026-06-01T00:00:00",
            end="2026-06-02T00:00:00",
        )
        assert len(results) >= 1
        assert any("Async Integration Test Event" in jscal_to_ical(r.get_data()) for r in results)

    @pytest.mark.asyncio
    async def test_event_sync(self, async_client, async_calendar_id):
        token_before = await async_client.get_sync_token()
        event_id = await async_client.create_event(
            async_calendar_id, _minimal_ical("Async Sync Test Event")
        )
        try:
            added, _modified, _deleted, _new_token = await async_client.get_objects_by_sync_token(
                token_before
            )
            assert any("Async Sync Test Event" in jscal_to_ical(a.get_data()) for a in added)
        finally:
            await async_client.delete_event(event_id)

    @pytest.mark.asyncio
    async def test_ical_roundtrip(self, async_client, async_calendar_id):
        start = datetime(2026, 7, 15, 9, 0, 0, tzinfo=timezone.utc)
        event_id = await async_client.create_event(
            async_calendar_id, _minimal_ical("Async Roundtrip Event", start=start)
        )
        try:
            fetched = jscal_to_ical((await async_client.get_event(event_id)).get_data())
            assert "Async Roundtrip Event" in fetched
            assert "20260715" in fetched
        finally:
            await async_client.delete_event(event_id)

    @pytest.mark.asyncio
    async def test_get_availability_returns_busy_interval_for_event(
        self, async_client, async_calendar_id
    ):
        start = datetime(2026, 9, 21, 10, 0, 0, tzinfo=timezone.utc)
        session = await async_client._get_session()
        event_id = await async_client.create_event(
            async_calendar_id, _minimal_ical("Async Availability Test Event", start=start)
        )
        try:
            availability = await async_client.get_availability(
                [session.account_id], "2026-09-19T00:00:00", "2026-09-26T00:00:00"
            )
            intervals = availability[session.account_id]
            assert len(intervals) == 1
            assert intervals[0].start == "2026-09-21T10:00:00Z"
            assert intervals[0].end == "2026-09-21T11:00:00Z"
        finally:
            await async_client.delete_event(event_id)


@_sync_servers
class TestAttachmentIntegration:
    """Confirmed live on both servers: uploadUrl/downloadUrl are always
    present, upload/download round-trip byte-for-byte, and the calendars
    draft's blobId-on-Link extension isn't persisted by either server
    (attach_to_event uses href instead, see its docstring). The Cyrus
    rel: "enclosure" persistence bug (see JMAPAttachment.is_attachment)
    is exercised directly in test_get_event_attachments_finds_attachment.
    """

    def test_upload_download_roundtrip(self, event_client):
        data = b"hello attachment test"
        blob_id = event_client.upload_attachment(data, "text/plain")
        assert blob_id
        downloaded = event_client.download_attachment(blob_id, "text/plain", "test.txt")
        assert downloaded == data

    def test_download_tolerates_omitted_type_and_name(self, event_client):
        """Confirmed live: both servers resolve a blob by blobId alone; an
        omitted type/name only affects the response's own Content-Type/
        Content-Disposition headers, not whether the download succeeds."""
        data = b"omitted type and name"
        blob_id = event_client.upload_attachment(data, "text/plain")
        assert event_client.download_attachment(blob_id) == data

    def test_download_unknown_blob_id_raises(self, event_client):
        with pytest.raises(requests.HTTPError):
            event_client.download_attachment("does-not-exist-" + str(uuid.uuid4()))

    def test_attach_to_event_persists_href(self, event_client, event_calendar_id, server):
        data = b"png-like binary content for attachment test"
        event_id = event_client.create_event(
            event_calendar_id, _minimal_ical("Attachment Test Event")
        )
        try:
            blob_id = event_client.upload_attachment(data, "application/octet-stream")
            event_client.attach_to_event(event_id, blob_id, "test.bin", "application/octet-stream")

            links = event_client.get_event(event_id).get_data().get("links", {})
            assert len(links) == 1, f"{server}: expected exactly one link"
            (link,) = links.values()
            assert link["title"] == "test.bin"
            assert link["contentType"] == "application/octet-stream"
            assert link["href"]

            downloaded = event_client.download_attachment(
                blob_id, "application/octet-stream", "test.bin"
            )
            assert downloaded == data
        finally:
            event_client.delete_event(event_id)

    def test_get_event_attachments_finds_attachment(self, event_client, event_calendar_id, server):
        """Cyrus doesn't reliably persist rel: "enclosure" (see
        JMAPAttachment.is_attachment), so it finds nothing here even though
        attach_to_event succeeded; Stalwart finds the attachment."""
        data = b"attachment content for get_event_attachments test"
        event_id = event_client.create_event(
            event_calendar_id, _minimal_ical("Attachment Filter Test Event")
        )
        try:
            blob_id = event_client.upload_attachment(data, "application/octet-stream")
            event_client.attach_to_event(event_id, blob_id, "test.bin", "application/octet-stream")
            attachments = event_client.get_event_attachments(event_id)
            if server == "cyrus":
                assert attachments == [], (
                    "cyrus: if this now finds the attachment, Cyrus may have "
                    "fixed the rel persistence bug; update this test and "
                    "JMAPAttachment.is_attachment's docstring accordingly."
                )
            else:
                assert len(attachments) == 1, f"{server}: expected exactly one attachment"
                assert attachments[0].title == "test.bin"
                assert attachments[0].content_type == "application/octet-stream"
                assert attachments[0].href
        finally:
            event_client.delete_event(event_id)

    def test_get_event_attachments_empty_for_event_with_no_links(
        self, event_client, event_created_id
    ):
        assert event_client.get_event_attachments(event_created_id) == []


@_cyrus_skip
class TestAsyncAttachmentIntegration:
    @pytest.mark.asyncio
    async def test_upload_download_roundtrip(self, async_client):
        data = b"hello async attachment test"
        blob_id = await async_client.upload_attachment(data, "text/plain")
        assert blob_id
        downloaded = await async_client.download_attachment(blob_id, "text/plain", "test.txt")
        assert downloaded == data

    @pytest.mark.asyncio
    async def test_attach_to_event_persists_href(self, async_client, async_calendar_id):
        """Cyrus-only class; asserts on the raw links data rather than
        get_event_attachments, see test_get_event_attachments_empty_on_cyrus_due_to_rel_bug."""
        data = b"async binary content for attachment test"
        event_id = await async_client.create_event(
            async_calendar_id, _minimal_ical("Async Attachment Test Event")
        )
        try:
            blob_id = await async_client.upload_attachment(data, "application/octet-stream")
            await async_client.attach_to_event(
                event_id, blob_id, "test.bin", "application/octet-stream"
            )

            links = (await async_client.get_event(event_id)).get_data().get("links", {})
            assert len(links) == 1
            (link,) = links.values()
            assert link["href"]
            assert link["title"] == "test.bin"

            downloaded = await async_client.download_attachment(
                blob_id, "application/octet-stream", "test.bin"
            )
            assert downloaded == data
        finally:
            await async_client.delete_event(event_id)

    @pytest.mark.asyncio
    async def test_get_event_attachments_empty_on_cyrus_due_to_rel_bug(
        self, async_client, async_calendar_id
    ):
        """See TestAttachmentIntegration.test_get_event_attachments_finds_attachment."""
        event_id = await async_client.create_event(
            async_calendar_id, _minimal_ical("Async Attachment Filter Test Event")
        )
        try:
            blob_id = await async_client.upload_attachment(b"data", "application/octet-stream")
            await async_client.attach_to_event(
                event_id, blob_id, "test.bin", "application/octet-stream"
            )
            assert await async_client.get_event_attachments(event_id) == []
        finally:
            await async_client.delete_event(event_id)


@pytest.fixture
def address_book_id(event_client, server):
    address_books = event_client.get_address_books()
    assert address_books, f"{server} did not return any address books"
    return address_books[0].id


@_sync_servers
class TestContactsIntegration:
    """Confirmed live that both Cyrus and Stalwart advertise
    urn:ietf:params:jmap:contacts under the same account already used for
    calendars, so no graceful-fallback path is exercised here; that path is
    covered only by the mocked unit tests, the same way get_availability's
    own fallback-path unit test covers a scenario neither live server
    actually exhibits either.

    ContactCard/set isn't part of this client's public API (get_address_books/
    search_contacts are read-only), so test contacts are seeded with a raw
    _request() call, the same JMAP wire call the client itself would send.
    """

    def _create_contact(self, event_client, address_book_id, card):
        """Seed a ContactCard via a raw ContactCard/set call. @type and
        version are RFC 9553 sections 2.1.1/2.1.2 mandatory Card properties;
        confirmed live that Cyrus rejects a create missing either with
        invalidProperties, while Stalwart accepts the create either way."""
        session = event_client._get_session()
        full_card = {
            "@type": "Card",
            "version": "1.0",
            **card,
            "addressBookIds": {address_book_id: True},
        }
        call = (
            "ContactCard/set",
            {"accountId": session.account_id, "create": {"new-0": full_card}},
            "contact-set-0",
        )
        responses = event_client._request([call], using=_CONTACTS_USING)
        return _JMAPClientBase._parse_create_response(
            responses, session.api_url, "ContactCard/set", parse_set_response
        )

    def _destroy_contact(self, event_client, contact_id):
        session = event_client._get_session()
        call = (
            "ContactCard/set",
            {"accountId": session.account_id, "destroy": [contact_id]},
            "contact-destroy-0",
        )
        responses = event_client._request([call], using=_CONTACTS_USING)
        _JMAPClientBase._parse_delete_response(
            responses, session.api_url, "ContactCard/set", parse_set_response, contact_id
        )

    def test_session_has_contacts_capability(self, event_client):
        session = event_client._get_session()
        assert CONTACTS_CAPABILITY in session.account_capabilities

    def test_get_address_books_returns_list(self, event_client, server):
        address_books = event_client.get_address_books()
        assert isinstance(address_books, list)
        assert len(address_books) >= 1, f"{server}: expected at least one address book"

    def test_get_address_books_exactly_one_is_default(self, event_client, server):
        address_books = event_client.get_address_books()
        defaults = [ab for ab in address_books if ab.is_default]
        assert len(defaults) == 1, f"{server}: expected exactly one default address book"

    def test_search_contacts_by_email(self, event_client, address_book_id, server):
        contact_id = self._create_contact(
            event_client,
            address_book_id,
            {
                "name": {"full": "Search By Email Test"},
                "emails": {"e1": {"address": "search-email-test@example.com"}},
            },
        )
        try:
            results = event_client.search_contacts(email="search-email-test")
            assert any(c.id == contact_id for c in results), (
                f"{server}: expected to find the seeded contact by email substring"
            )
        finally:
            self._destroy_contact(event_client, contact_id)

    def test_search_contacts_by_text(self, event_client, address_book_id, server):
        """Confirmed live: Cyrus matches the text filter as a substring
        against a card's name, not exact-only. Stalwart v0.16.21 does not
        match it against the name at all, only against the email address
        (see search_contacts's own docstring); a text search for any part
        of the name here finds nothing there."""
        contact_id = self._create_contact(
            event_client,
            address_book_id,
            {
                "name": {"full": "Search By Text Test"},
                "emails": {"e1": {"address": "search-text-test@example.com"}},
            },
        )
        try:
            results = event_client.search_contacts(text="By Text")
            found = any(c.id == contact_id for c in results)
            if server == "cyrus":
                assert found, (
                    f"{server}: expected to find the seeded contact by a substring of its name text"
                )
            else:
                assert not found, (
                    f"{server}: if this now finds the contact, Stalwart may have "
                    "started matching text against the name; update this test and "
                    "search_contacts's own docstring accordingly."
                )
        finally:
            self._destroy_contact(event_client, contact_id)

    def test_search_contacts_no_filter_returns_seeded_contact(
        self, event_client, address_book_id, server
    ):
        contact_id = self._create_contact(
            event_client,
            address_book_id,
            {"name": {"full": "Unfiltered Search Test"}},
        )
        try:
            results = event_client.search_contacts()
            assert any(c.id == contact_id for c in results), (
                f"{server}: expected the seeded contact in an unfiltered search"
            )
        finally:
            self._destroy_contact(event_client, contact_id)

    def test_contact_uid(self, event_client, address_book_id, server):
        """Stalwart's ContactCard/get never returns uid at all, contrary to
        RFC 9553 treating it as mandatory; Cyrus does return it. A future
        Stalwart fix silently changing this should be caught here."""
        contact_id = self._create_contact(
            event_client,
            address_book_id,
            {
                "name": {"full": "Contact Uid Test"},
                "emails": {"e1": {"address": "contact-uid-test@example.com"}},
            },
        )
        try:
            # email, not text: see test_search_contacts_by_text.
            results = event_client.search_contacts(email="contact-uid-test")
            (contact,) = [c for c in results if c.id == contact_id]
            if server == "stalwart":
                assert contact.uid is None
            else:
                assert contact.uid is not None
        finally:
            self._destroy_contact(event_client, contact_id)
