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

STALWART_HOST = "localhost"
STALWART_PORT = 8809
STALWART_JMAP_URL = f"http://{STALWART_HOST}:{STALWART_PORT}/.well-known/jmap"
STALWART_USERNAME = "testuser@example.org"
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

    def test_event_query_time_range(self, server, event_client, event_calendar_id):
        title = "Query Range Test Event"
        event_id = event_client.create_event(event_calendar_id, _minimal_ical(title))
        try:
            # Stalwart does not support the inCalendars filter; query without calendar_id.
            search_calendar_id = None if server == "stalwart" else event_calendar_id
            results = event_client.search_events(
                calendar_id=search_calendar_id,
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
