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
reachable — no failure, no noise.
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

from calendaring_jmap import AsyncJMAPClient, JMAPClient
from calendaring_jmap.constants import CALENDAR_CAPABILITY
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
    reason=f"Cyrus Docker not reachable on {CYRUS_HOST}:{CYRUS_PORT} — "
    "start it with: docker-compose -f tests/docker/cyrus/docker-compose.yml up -d",
)


def _minimal_ical(title: str = "Test Event", start: datetime | None = None) -> str:
    if start is None:
        start = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
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
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )


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
    reason=f"Stalwart Docker not reachable on {STALWART_HOST}:{STALWART_PORT} — "
    "start it with: cd tests/docker/stalwart && ./start.sh",
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


@pytest.fixture
def second_account_id(server):
    """The account id a second user's own session resolves for itself.

    share_calendar() needs a resolved Principal/account id and this client
    has no Principal/query support yet, so the test resolves it the same way
    a caller without Principal support would have to: open a session as the
    target user and read what account id they see for themselves.
    """
    if server == "cyrus":
        session = fetch_session(
            CYRUS_JMAP_URL, auth=HTTPBasicAuth(CYRUS_USERNAME_2, CYRUS_PASSWORD_2)
        )
    else:
        session = fetch_session(
            STALWART_JMAP_URL, auth=HTTPBasicAuth(STALWART_USERNAME_2, STALWART_PASSWORD_2)
        )
    return session.account_id


@pytest.fixture
def owner_account_id(server):
    """The account id calendar_management_client's own session resolves for itself.

    Used to browse the owner's account from the sharee's client after a
    share_calendar() call, since JMAP surfaces a shared calendar under the
    owner's accountId, not the sharee's own primary account.
    """
    if server == "cyrus":
        session = fetch_session(CYRUS_JMAP_URL, auth=HTTPBasicAuth(CYRUS_USERNAME, CYRUS_PASSWORD))
    else:
        session = fetch_session(
            STALWART_JMAP_URL, auth=HTTPBasicAuth(STALWART_USERNAME, STALWART_PASSWORD)
        )
    return session.account_id


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
        §2). The sharee's session gains access to the owner's account, so
        they browse it with get_calendars(account_id=owner_account_id)
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
