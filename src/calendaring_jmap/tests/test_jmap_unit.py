# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit tests for the calendaring_jmap package.

Rule: zero network calls, zero Docker dependency, all tests are fast.
External HTTP is mocked via unittest.mock wherever needed.
"""

import warnings
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from niquests.auth import HTTPBasicAuth
except ImportError:
    from requests.auth import HTTPBasicAuth  # type: ignore[assignment,no-redef]

_JMAP_URL = "http://localhost:8802/.well-known/jmap"
_API_URL = "http://localhost:8802/jmap/api"
_USERNAME = "user1"
_PASSWORD = "x"

from calendaring_jmap.error import (
    JMAPAuthError,
    JMAPBaseError,
    JMAPCapabilityError,
    JMAPError,
    JMAPMethodError,
)


class TestHTTPLibrarySelection:
    def test_require_async_session_raises_when_niquests_absent(self, monkeypatch):
        import calendaring_jmap._http as _http_mod

        monkeypatch.setattr(_http_mod, "AsyncSession", None)
        monkeypatch.setattr(_http_mod, "USE_NIQUESTS", False)
        with pytest.raises(ImportError, match="not installed"):
            _http_mod.require_async_session()

    def test_require_async_session_raises_with_old_niquests_message(self, monkeypatch):
        import calendaring_jmap._http as _http_mod

        monkeypatch.setattr(_http_mod, "AsyncSession", None)
        monkeypatch.setattr(_http_mod, "USE_NIQUESTS", True)
        with pytest.raises(ImportError, match="old niquests install"):
            _http_mod.require_async_session()

    def test_require_async_session_returns_session_when_present(self, monkeypatch):
        import calendaring_jmap._http as _http_mod

        sentinel = object()
        monkeypatch.setattr(_http_mod, "AsyncSession", sentinel)
        assert _http_mod.require_async_session() is sentinel

    @staticmethod
    @contextmanager
    def _reloaded_http_module():
        """Reload calendaring_jmap._http under whatever sys.modules/import
        patches the caller has already set up, then restore its original
        __dict__ afterwards. client.py and friends imported names out of
        this module at collection time; restoring the same module object's
        contents (rather than leaving the reloaded replacement in sys.modules)
        keeps those already-bound references consistent with isinstance
        checks elsewhere in the suite."""
        import importlib

        import calendaring_jmap._http as _http_mod

        original_dict = dict(_http_mod.__dict__)
        try:
            importlib.reload(_http_mod)
            yield _http_mod
        finally:
            _http_mod.__dict__.clear()
            _http_mod.__dict__.update(original_dict)

    def test_module_reload_falls_back_to_requests_when_niquests_missing(self, monkeypatch):
        import sys

        for mod_name in list(sys.modules):
            if mod_name == "niquests" or mod_name.startswith("niquests."):
                monkeypatch.delitem(sys.modules, mod_name, raising=False)
        monkeypatch.setitem(sys.modules, "niquests", None)

        with self._reloaded_http_module() as reloaded:
            assert reloaded.USE_NIQUESTS is False
            assert reloaded.USE_REQUESTS is True
            assert reloaded.AsyncSession is None

    def test_module_reload_raises_when_neither_library_installed(self, monkeypatch):
        import sys

        for mod_name in list(sys.modules):
            if mod_name.split(".")[0] in ("niquests", "requests"):
                monkeypatch.delitem(sys.modules, mod_name, raising=False)
        monkeypatch.setitem(sys.modules, "niquests", None)
        monkeypatch.setitem(sys.modules, "requests", None)

        with pytest.raises(ImportError, match="needs an HTTP library"):
            with self._reloaded_http_module():
                pass

    def test_module_reload_old_niquests_without_async_session(self, monkeypatch):
        import sys
        import types

        fake_niquests = types.ModuleType("niquests")
        fake_niquests.Session = MagicMock()

        fake_auth = types.ModuleType("niquests.auth")
        fake_auth.AuthBase = object
        fake_auth.HTTPBasicAuth = object
        fake_niquests.auth = fake_auth

        for mod_name in list(sys.modules):
            if mod_name == "niquests" or mod_name.startswith("niquests."):
                monkeypatch.delitem(sys.modules, mod_name, raising=False)
        monkeypatch.setitem(sys.modules, "niquests", fake_niquests)
        monkeypatch.setitem(sys.modules, "niquests.auth", fake_auth)

        with self._reloaded_http_module() as reloaded:
            assert reloaded.USE_NIQUESTS is True
            assert reloaded.AsyncSession is None
            with pytest.raises(ImportError, match="old niquests install"):
                reloaded.require_async_session()

    def test_http_bearer_auth_not_equal_to_different_password(self):
        from calendaring_jmap._http import HTTPBearerAuth

        a = HTTPBearerAuth("token1")
        b = HTTPBearerAuth("token2")
        assert a != b

    def test_http_bearer_auth_not_equal_to_non_bearer_object(self):
        from calendaring_jmap._http import HTTPBearerAuth

        a = HTTPBearerAuth("token1")
        assert a != object()

    def test_http_bearer_auth_call_sets_authorization_header(self):
        from calendaring_jmap._http import HTTPBearerAuth

        auth = HTTPBearerAuth("secret-token")
        request = MagicMock()
        request.headers = {}
        result = auth(request)
        assert result.headers["Authorization"] == "Bearer secret-token"
        assert result is request


class TestJMAPErrorHierarchy:
    def test_jmap_error_is_base_error(self):
        assert issubclass(JMAPError, JMAPBaseError)

    def test_jmap_capability_error_is_jmap_error(self):
        assert issubclass(JMAPCapabilityError, JMAPError)

    def test_jmap_auth_error_is_jmap_error(self):
        assert issubclass(JMAPAuthError, JMAPError)

    def test_jmap_method_error_is_jmap_error(self):
        assert issubclass(JMAPMethodError, JMAPError)

    def test_jmap_base_error_str_contains_url_and_reason(self):
        e = JMAPBaseError(url="http://example.com", reason="something broke")
        s = str(e)
        assert "http://example.com" in s
        assert "something broke" in s

    def test_jmap_error_default_error_type(self):
        e = JMAPError()
        assert e.error_type == "serverError"

    def test_jmap_error_custom_error_type(self):
        e = JMAPError(error_type="unknownMethod")
        assert e.error_type == "unknownMethod"

    def test_jmap_error_str_contains_type(self):
        e = JMAPError(url="http://example.com", reason="boom", error_type="invalidArguments")
        s = str(e)
        assert "invalidArguments" in s
        assert "boom" in s
        assert "http://example.com" in s

    def test_jmap_capability_error_default_type(self):
        e = JMAPCapabilityError()
        assert e.error_type == "capabilityNotSupported"

    def test_jmap_auth_error_default_type(self):
        e = JMAPAuthError()
        assert e.error_type == "forbidden"

    def test_jmap_method_error_custom_type(self):
        e = JMAPMethodError(error_type="stateMismatch", reason="state changed")
        assert e.error_type == "stateMismatch"
        assert e.reason == "state changed"

    def test_jmap_error_catchable_as_base_error(self):
        with pytest.raises(JMAPBaseError):
            raise JMAPMethodError(error_type="notFound")

    def test_jmap_auth_error_catchable_as_jmap_error(self):
        with pytest.raises(JMAPError):
            raise JMAPAuthError()


from calendaring_jmap.constants import CALENDAR_CAPABILITY, TASK_CAPABILITY
from calendaring_jmap.session import Session, fetch_session

# Minimal valid Session JSON fixture
_SESSION_JSON = {
    "apiUrl": _API_URL,
    "state": "state-abc",
    "capabilities": {
        "urn:ietf:params:jmap:core": {"maxCallsInRequest": 32},
        CALENDAR_CAPABILITY: {},
    },
    "accounts": {
        _USERNAME: {
            "name": f"{_USERNAME}@example.com",
            "isPersonalAccount": True,
            "accountCapabilities": {
                CALENDAR_CAPABILITY: {},
            },
        }
    },
}


def _make_mock_response(json_data, status_code=200):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


class TestFetchSession:
    def test_parses_api_url(self):
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(_SESSION_JSON)
            session = fetch_session(_JMAP_URL, auth=None)
        assert session.api_url == _API_URL

    def test_parses_account_id(self):
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(_SESSION_JSON)
            session = fetch_session(_JMAP_URL, auth=None)
        assert session.account_id == _USERNAME

    def test_parses_state(self):
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(_SESSION_JSON)
            session = fetch_session(_JMAP_URL, auth=None)
        assert session.state == "state-abc"

    def test_parses_account_capabilities(self):
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(_SESSION_JSON)
            session = fetch_session(_JMAP_URL, auth=None)
        assert CALENDAR_CAPABILITY in session.account_capabilities

    def test_raw_is_full_response(self):
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(_SESSION_JSON)
            session = fetch_session(_JMAP_URL, auth=None)
        assert session.raw == _SESSION_JSON

    def test_raises_auth_error_on_401(self):
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response({}, status_code=401)
            with pytest.raises(JMAPAuthError):
                fetch_session(_JMAP_URL, auth=None)

    def test_raises_auth_error_on_403(self):
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response({}, status_code=403)
            with pytest.raises(JMAPAuthError):
                fetch_session(_JMAP_URL, auth=None)

    def test_raises_capability_error_when_no_calendar_account(self):
        data = dict(_SESSION_JSON)
        data["accounts"] = {
            _USERNAME: {
                "name": f"{_USERNAME}@example.com",
                "isPersonalAccount": True,
                "accountCapabilities": {
                    "urn:ietf:params:jmap:mail": {},  # no calendars
                },
            }
        }
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(data)
            with pytest.raises(JMAPCapabilityError):
                fetch_session(_JMAP_URL, auth=None)

    def test_raises_capability_error_when_no_accounts(self):
        data = dict(_SESSION_JSON)
        data["accounts"] = {}
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(data)
            with pytest.raises(JMAPCapabilityError):
                fetch_session(_JMAP_URL, auth=None)

    def test_raises_capability_error_when_missing_api_url(self):
        data = dict(_SESSION_JSON)
        del data["apiUrl"]
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(data)
            with pytest.raises(JMAPCapabilityError):
                fetch_session(_JMAP_URL, auth=None)

    def test_picks_first_calendar_capable_account(self):
        data = dict(_SESSION_JSON)
        data["accounts"] = {
            "user_mail_only": {
                "name": "mailonly@example.com",
                "isPersonalAccount": True,
                "accountCapabilities": {"urn:ietf:params:jmap:mail": {}},
            },
            "user_calendar": {
                "name": "calendar@example.com",
                "isPersonalAccount": True,
                "accountCapabilities": {CALENDAR_CAPABILITY: {}},
            },
        }
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(data)
            session = fetch_session(_JMAP_URL, auth=None)
        assert session.account_id == "user_calendar"

    def test_uses_primary_accounts_entry_for_calendar_capability(self):
        data = dict(_SESSION_JSON)
        data["accounts"] = {
            "user_secondary": {
                "name": "secondary@example.com",
                "isPersonalAccount": False,
                "accountCapabilities": {CALENDAR_CAPABILITY: {}},
            },
            "user_primary": {
                "name": "primary@example.com",
                "isPersonalAccount": True,
                "accountCapabilities": {CALENDAR_CAPABILITY: {}},
            },
        }
        data["primaryAccounts"] = {CALENDAR_CAPABILITY: "user_primary"}
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(data)
            session = fetch_session(_JMAP_URL, auth=None)
        assert session.account_id == "user_primary"

    def test_primary_accounts_entry_without_calendar_capability_falls_back(self):
        data = dict(_SESSION_JSON)
        data["accounts"] = {
            "user_no_calendar": {
                "name": "nocal@example.com",
                "isPersonalAccount": True,
                "accountCapabilities": {"urn:ietf:params:jmap:mail": {}},
            },
            "user_fallback": {
                "name": "fallback@example.com",
                "isPersonalAccount": True,
                "accountCapabilities": {CALENDAR_CAPABILITY: {}},
            },
        }
        data["primaryAccounts"] = {CALENDAR_CAPABILITY: "user_no_calendar"}
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(data)
            session = fetch_session(_JMAP_URL, auth=None)
        assert session.account_id == "user_fallback"

    def test_rewrites_api_url_scheme_and_port_to_match_session_host(self):
        data = dict(_SESSION_JSON)
        data["apiUrl"] = "https://localhost:9999/jmap/api"
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(data)
            session = fetch_session(_JMAP_URL, auth=None)
        assert session.api_url == "http://localhost:8802/jmap/api"

    @pytest.mark.asyncio
    async def test_async_fetch_session_parses_api_url(self, monkeypatch):
        from calendaring_jmap.session import async_fetch_session

        mock_resp = _make_mock_response(_SESSION_JSON)
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.get = AsyncMock(return_value=mock_resp)
        monkeypatch.setattr("calendaring_jmap.session.AsyncSession", lambda: mock_http)
        session = await async_fetch_session(_JMAP_URL, auth=None)
        assert session.api_url == _API_URL
        assert session.account_id == _USERNAME

    @pytest.mark.asyncio
    async def test_async_fetch_session_raises_auth_error_on_401(self, monkeypatch):
        from calendaring_jmap.session import async_fetch_session

        mock_resp = _make_mock_response({}, status_code=401)
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.get = AsyncMock(return_value=mock_resp)
        monkeypatch.setattr("calendaring_jmap.session.AsyncSession", lambda: mock_http)
        with pytest.raises(JMAPAuthError):
            await async_fetch_session(_JMAP_URL, auth=None)


from datetime import datetime, timezone

from calendaring_jmap.objects.calendar import JMAPCalendar
from calendaring_jmap.objects.calendar_object import JMAPCalendarObject

_CALENDAR_JSON_FULL = {
    "id": "cal1",
    "name": "Personal",
    "description": "My personal calendar",
    "color": "#3a86ff",
    "isSubscribed": True,
    "myRights": {"mayReadItems": True, "mayAddItems": True},
    "sortOrder": 1,
    "isVisible": True,
}

_CALENDAR_JSON_MINIMAL = {
    "id": "cal2",
    "name": "Work",
}


class TestJMAPCalendar:
    def test_from_jmap_full(self):
        cal = JMAPCalendar.from_jmap(_CALENDAR_JSON_FULL)
        assert cal.id == "cal1"
        assert cal.name == "Personal"
        assert cal.description == "My personal calendar"
        assert cal.color == "#3a86ff"
        assert cal.is_subscribed is True
        assert cal.my_rights == {"mayReadItems": True, "mayAddItems": True}
        assert cal.sort_order == 1
        assert cal.is_visible is True

    def test_from_jmap_minimal_uses_defaults(self):
        cal = JMAPCalendar.from_jmap(_CALENDAR_JSON_MINIMAL)
        assert cal.id == "cal2"
        assert cal.name == "Work"
        assert cal.description is None
        assert cal.color is None
        assert cal.is_subscribed is True
        assert cal.my_rights == {}
        assert cal.sort_order == 0
        assert cal.is_visible is True

    def test_to_jmap_includes_required_fields(self):
        cal = JMAPCalendar.from_jmap(_CALENDAR_JSON_MINIMAL)
        d = cal.to_jmap()
        assert d["name"] == "Work"
        assert "isSubscribed" in d

    def test_to_jmap_excludes_server_set_fields(self):
        cal = JMAPCalendar.from_jmap(_CALENDAR_JSON_FULL)
        d = cal.to_jmap()
        assert "id" not in d
        assert "myRights" not in d

    def test_to_jmap_omits_none_optional_fields(self):
        cal = JMAPCalendar.from_jmap(_CALENDAR_JSON_MINIMAL)
        d = cal.to_jmap()
        assert "description" not in d
        assert "color" not in d

    def test_to_jmap_includes_optional_when_set(self):
        cal = JMAPCalendar.from_jmap(_CALENDAR_JSON_FULL)
        d = cal.to_jmap()
        assert d["description"] == "My personal calendar"
        assert d["color"] == "#3a86ff"

    def test_from_jmap_ignores_unknown_keys(self):
        data = dict(_CALENDAR_JSON_FULL)
        data["unknownFutureField"] = "something"
        cal = JMAPCalendar.from_jmap(data)
        assert cal.id == "cal1"

    def test_from_jmap_raises_when_name_missing(self):
        with pytest.raises(KeyError):
            JMAPCalendar.from_jmap({"id": "cal3"})

    _RAW_EVENT = {
        "id": "ev1",
        "uid": "test-uid@example.com",
        "calendarIds": {"cal1": True},
        "title": "Staff Meeting",
        "start": "2026-01-15T09:00:00",
        "duration": "PT1H",
    }

    def _query_get_response(self, items):
        return {
            "methodResponses": [
                [
                    "CalendarEvent/query",
                    {"ids": [i["id"] for i in items], "queryState": "qs-1", "total": len(items)},
                    "ev-query-0",
                ],
                [
                    "CalendarEvent/get",
                    {"accountId": _USERNAME, "list": items, "notFound": []},
                    "ev-get-1",
                ],
            ]
        }

    def _set_response(self, created=None, notCreated=None):
        return {
            "methodResponses": [
                [
                    "CalendarEvent/set",
                    {
                        "accountId": _USERNAME,
                        "created": created or {},
                        "updated": {},
                        "destroyed": [],
                        "notCreated": notCreated or {},
                        "notUpdated": {},
                        "notDestroyed": {},
                    },
                    "ev-set-create-0",
                ]
            ]
        }

    def _capturing_calendar(self, monkeypatch, resp, calendar_id="cal1"):
        captured = {}
        client = JMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        client._session_cache = Session(api_url=_API_URL, account_id=_USERNAME, state="state-abc")

        def capturing_post(*args, **kwargs):
            captured["json"] = kwargs.get("json", {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = resp
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_http = MagicMock()
        mock_http.post.side_effect = capturing_post
        client._http_session = mock_http
        cal = JMAPCalendar(id=calendar_id, name="Test")
        cal._client = client
        cal._is_async = False
        return cal, captured

    def test_calendar_search_returns_ical_list(self, monkeypatch):
        event2 = {**self._RAW_EVENT, "id": "ev2", "title": "Standup"}
        resp = self._query_get_response([self._RAW_EVENT, event2])
        cal = _make_calendar_with_client(monkeypatch, resp)
        results = cal.search()
        assert len(results) == 2
        assert all(isinstance(r, JMAPCalendarObject) for r in results)
        assert all(r.parent is cal for r in results)
        assert results[0].id == "ev1"

    def test_calendar_search_passes_calendar_id_filter(self, monkeypatch):
        resp = self._query_get_response([self._RAW_EVENT])
        cal, captured = self._capturing_calendar(monkeypatch, resp, calendar_id="my-cal")
        cal.search()
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["inCalendars"] == ["my-cal"]

    def test_calendar_search_with_date_range(self, monkeypatch):
        resp = self._query_get_response([self._RAW_EVENT])
        cal, captured = self._capturing_calendar(monkeypatch, resp)
        cal.search(start="2026-01-01T00:00:00", end="2026-12-31T23:59:59")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["after"] == "2026-01-01T00:00:00"
        assert query_args["filter"]["before"] == "2026-12-31T23:59:59"

    def test_calendar_search_datetime_converted_to_utcdate(self, monkeypatch):
        """§4.6: datetime.isoformat() produced wrong format for JMAP UTCDate.
        Naive datetimes produce no Z, aware non-UTC produce +HH:MM offset;
        JMAP requires ...Z (UTC, no microseconds)."""
        import datetime as _dt

        resp = self._query_get_response([self._RAW_EVENT])
        cal, captured = self._capturing_calendar(monkeypatch, resp)
        tz_plus2 = _dt.timezone(_dt.timedelta(hours=2))
        start_aware = datetime(2026, 6, 1, 12, 0, 0, tzinfo=tz_plus2)  # +02:00 noon → UTC 10:00
        end_utc = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        cal.search(start=start_aware, end=end_utc)
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["after"] == "2026-06-01T10:00:00Z", (
            f"Expected UTC Z-format, got {query_args['filter']['after']!r}"
        )
        assert query_args["filter"]["before"] == "2026-12-31T23:59:59Z", (
            f"Expected UTC Z-format, got {query_args['filter']['before']!r}"
        )

    def test_calendar_search_ignores_unknown_params(self, monkeypatch):
        """Verify that unknown search parameters are silently ignored."""
        resp = self._query_get_response([self._RAW_EVENT])
        cal, captured = self._capturing_calendar(monkeypatch, resp)
        # Should not raise an error even with legacy/unknown parameters
        cal.search(event=True, todo=False, unknown_param="value")
        query_args = captured["json"]["methodCalls"][0][1]
        # Should only contain the calendar filter, no unknown params
        assert query_args["filter"] == {"inCalendars": [cal.id]}

    def test_calendar_search_with_text(self, monkeypatch):
        resp = self._query_get_response([self._RAW_EVENT])
        cal, captured = self._capturing_calendar(monkeypatch, resp)
        cal.search(text="standup")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["text"] == "standup"

    def test_calendar_get_object_by_uid_found(self, monkeypatch):
        resp = self._query_get_response([self._RAW_EVENT])
        cal = _make_calendar_with_client(monkeypatch, resp)
        result = cal.get_object_by_uid("test-uid@example.com")
        assert isinstance(result, JMAPCalendarObject)
        assert result.id == "ev1"
        assert result.get_data()["title"] == "Staff Meeting"
        assert result.parent is cal

    def test_calendar_get_object_by_uid_not_found(self, monkeypatch):
        resp = self._query_get_response([self._RAW_EVENT])
        cal = _make_calendar_with_client(monkeypatch, resp)
        with pytest.raises(JMAPMethodError):
            cal.get_object_by_uid("nonexistent-uid@example.com")

    def test_calendar_add_event_delegates_to_create_event(self, monkeypatch):
        _MINIMAL_ICAL = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
            "UID:test@example.com\r\nSUMMARY:Test\r\n"
            "DTSTART:20260115T090000Z\r\nDTEND:20260115T100000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        resp = self._set_response(created={"new-0": {"id": "sv-cal-1"}})
        cal, captured = self._capturing_calendar(monkeypatch, resp, calendar_id="my-calendar")
        cal.add_event(_MINIMAL_ICAL)
        create_args = captured["json"]["methodCalls"][0][1]
        event_payload = create_args["create"]["new-0"]
        assert event_payload.get("calendarIds") == {"my-calendar": True}

    def test_calendar_search_naive_datetime_treated_as_utc(self, monkeypatch):
        resp = self._query_get_response([self._RAW_EVENT])
        cal, captured = self._capturing_calendar(monkeypatch, resp)
        naive_start = datetime(2026, 6, 1, 12, 0, 0)
        cal.search(start=naive_start)
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["after"] == "2026-06-01T12:00:00Z"

    def test_calendar_search_dispatches_to_async_when_async_backed(self):
        mock_client = MagicMock()
        mock_client._search = AsyncMock(return_value=["result"])
        cal = JMAPCalendar(id="cal1", name="Test")
        cal._client = mock_client
        cal._is_async = True
        coro = cal.search(
            text="standup",
            start=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            end=datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc),
        )
        import asyncio

        result = asyncio.run(coro)
        assert result == ["result"]
        mock_client._search.assert_awaited_once()
        call_kwargs = mock_client._search.call_args.kwargs
        assert call_kwargs["text"] == "standup"
        assert call_kwargs["start"] == "2026-06-01T12:00:00Z"
        assert call_kwargs["end"] == "2026-06-02T12:00:00Z"

    def test_calendar_search_async_passes_through_string_dates_unconverted(self):
        mock_client = MagicMock()
        mock_client._search = AsyncMock(return_value=[])
        cal = JMAPCalendar(id="cal1", name="Test")
        cal._client = mock_client
        cal._is_async = True
        import asyncio

        asyncio.run(cal.search(start="2026-06-01T12:00:00", end="2026-06-02T12:00:00"))
        call_kwargs = mock_client._search.call_args.kwargs
        assert call_kwargs["start"] == "2026-06-01T12:00:00"
        assert call_kwargs["end"] == "2026-06-02T12:00:00"

    def test_calendar_get_object_by_uid_dispatches_to_async_when_async_backed(self):
        mock_client = MagicMock()
        mock_client._get_object_by_uid = AsyncMock(return_value="obj")
        cal = JMAPCalendar(id="cal1", name="Test")
        cal._client = mock_client
        cal._is_async = True
        import asyncio

        result = asyncio.run(cal.get_object_by_uid("some-uid"))
        assert result == "obj"
        mock_client._get_object_by_uid.assert_awaited_once_with(
            "some-uid", calendar_id="cal1", parent=cal
        )

    def test_calendar_add_event_dispatches_to_async_when_async_backed(self):
        mock_client = MagicMock()
        mock_client.create_event = AsyncMock(return_value="ev-new-id")
        cal = JMAPCalendar(id="cal1", name="Test")
        cal._client = mock_client
        cal._is_async = True
        import asyncio

        result = asyncio.run(cal.add_event("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"))
        assert result == "ev-new-id"
        mock_client.create_event.assert_awaited_once_with(
            "cal1", "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
        )


_MINIMAL_JSCAL_DICT = {
    "id": "ev-obj-1",
    "uid": "obj-uid@example.com",
    "calendarIds": {"cal1": True},
    "title": "Object Test Event",
    "start": "2026-03-01T10:00:00",
    "timeZone": "Europe/Berlin",
    "duration": "PT1H",
}


class TestJMAPCalendarObject:
    def test_id_from_data(self):
        obj = JMAPCalendarObject(data=_MINIMAL_JSCAL_DICT, parent=None)
        assert obj.id == "ev-obj-1"

    def test_get_data_returns_dict(self):
        obj = JMAPCalendarObject(data=_MINIMAL_JSCAL_DICT, parent=None)
        assert obj.get_data() is _MINIMAL_JSCAL_DICT

    def test_get_icalendar_instance_returns_calendar(self):
        import icalendar

        obj = JMAPCalendarObject(data=_MINIMAL_JSCAL_DICT, parent=None)
        cal = obj.get_icalendar_instance()
        assert isinstance(cal, icalendar.Calendar)

    def test_get_icalendar_instance_is_cached(self):
        obj = JMAPCalendarObject(data=_MINIMAL_JSCAL_DICT, parent=None)
        assert obj.get_icalendar_instance() is obj.get_icalendar_instance()

    def test_edit_icalendar_instance_yields_calendar(self):
        import icalendar

        obj = JMAPCalendarObject(data=_MINIMAL_JSCAL_DICT, parent=None)
        with obj.edit_icalendar_instance() as cal:
            assert isinstance(cal, icalendar.Calendar)

    def test_save_calls_update_event(self):
        mock_client = MagicMock()
        mock_parent = JMAPCalendar(id="cal1", name="Test")
        mock_parent._client = mock_client

        obj = JMAPCalendarObject(data=_MINIMAL_JSCAL_DICT, parent=mock_parent)
        with obj.edit_icalendar_instance():
            pass
        obj.save()

        mock_client.update_event.assert_called_once()
        call_args = mock_client.update_event.call_args
        assert call_args[0][0] == "ev-obj-1"
        assert isinstance(call_args[0][1], str)

    def test_save_raises_without_parent(self):
        obj = JMAPCalendarObject(data=_MINIMAL_JSCAL_DICT, parent=None)
        with pytest.raises(JMAPMethodError, match="no parent calendar"):
            obj.save()

    def test_save_raises_for_async_parent(self):
        mock_parent = MagicMock()
        mock_parent._is_async = True
        obj = JMAPCalendarObject(data=_MINIMAL_JSCAL_DICT, parent=mock_parent)
        with pytest.raises(RuntimeError):
            obj.save()


from calendaring_jmap._methods.calendar import (
    build_calendar_changes,
    build_calendar_get,
    parse_calendar_get,
)


class TestCalendarMethodBuilders:
    def test_build_calendar_get_structure(self):
        method, args, call_id = build_calendar_get("u1")
        assert method == "Calendar/get"
        assert args["accountId"] == "u1"
        assert args["ids"] is None
        assert isinstance(call_id, str)

    def test_build_calendar_get_with_ids(self):
        _, args, _ = build_calendar_get("u1", ids=["cal1", "cal2"])
        assert args["ids"] == ["cal1", "cal2"]

    def test_build_calendar_get_with_properties(self):
        _, args, _ = build_calendar_get("u1", properties=["id", "name"])
        assert args["properties"] == ["id", "name"]

    def test_build_calendar_get_no_properties_key_when_not_set(self):
        _, args, _ = build_calendar_get("u1")
        assert "properties" not in args

    def test_parse_calendar_get_returns_calendars(self):
        response_args = {"list": [_CALENDAR_JSON_FULL, _CALENDAR_JSON_MINIMAL]}
        cals = parse_calendar_get(response_args)
        assert len(cals) == 2
        assert isinstance(cals[0], JMAPCalendar)
        assert cals[0].id == "cal1"
        assert cals[1].id == "cal2"

    def test_parse_calendar_get_empty_list(self):
        cals = parse_calendar_get({"list": []})
        assert cals == []

    def test_parse_calendar_get_missing_list_key(self):
        cals = parse_calendar_get({})
        assert cals == []

    def test_build_calendar_changes_structure(self):
        method, args, call_id = build_calendar_changes("u1", "state-abc")
        assert method == "Calendar/changes"
        assert args["accountId"] == "u1"
        assert args["sinceState"] == "state-abc"
        assert isinstance(call_id, str)


from calendaring_jmap.client import JMAPClient

_CALENDAR_GET_RESPONSE = {
    "methodResponses": [
        [
            "Calendar/get",
            {
                "accountId": _USERNAME,
                "state": "cal-state-1",
                "list": [_CALENDAR_JSON_FULL, _CALENDAR_JSON_MINIMAL],
                "notFound": [],
            },
            "cal-get-0",
        ]
    ]
}


def _make_client_with_mocked_session(monkeypatch, api_response_json):
    """Return a JMAPClient whose HTTP calls are fully mocked."""
    client = JMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
    client._session_cache = Session(
        api_url=_API_URL,
        account_id=_USERNAME,
        state="state-abc",
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = api_response_json
    mock_resp.raise_for_status = MagicMock()
    mock_http = MagicMock()
    mock_http.post.return_value = mock_resp
    client._http_session = mock_http
    return client


def _make_calendar_with_client(monkeypatch, api_response_json, calendar_id="cal1"):
    """Return a JMAPCalendar backed by a fully mocked JMAPClient."""
    client = _make_client_with_mocked_session(monkeypatch, api_response_json)
    cal = JMAPCalendar(id=calendar_id, name="Test")
    cal._client = client
    cal._is_async = False
    return cal


class TestJMAPClient:
    def test_context_manager(self):
        with JMAPClient(url="http://x", username="u", password="p") as client:
            assert isinstance(client, JMAPClient)

    def test_context_manager_closes_http_session(self):
        mock_close = MagicMock()
        mock_http = MagicMock()
        mock_http.close = mock_close
        with patch("calendaring_jmap.client.requests.Session", return_value=mock_http):
            client = JMAPClient(url="http://x", username="u", password="p")
            with client:
                assert client._http_session is mock_http
            mock_close.assert_called_once()
            assert client._http_session is None

    def test_http_session_reused_across_requests(self, monkeypatch):
        client = JMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        client._session_cache = Session(api_url=_API_URL, account_id=_USERNAME, state="s")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"methodResponses": []}
        mock_resp.raise_for_status = MagicMock()
        with patch("calendaring_jmap.client.requests.Session") as MockSession:
            mock_sess = MagicMock()
            mock_sess.post.return_value = mock_resp
            MockSession.return_value = mock_sess
            client._request([("Calendar/get", {}, "c0")])
            client._request([("Calendar/get", {}, "c1")])
        MockSession.assert_called_once()
        assert mock_sess.post.call_count == 2

    def test_build_auth_basic_when_username_given(self):
        client = JMAPClient(url="http://x", username="u", password="p")
        assert isinstance(client._auth, HTTPBasicAuth)

    def test_build_auth_bearer_when_no_username(self):
        from calendaring_jmap._http import HTTPBearerAuth

        client = JMAPClient(url="http://x", password="token")
        assert isinstance(client._auth, HTTPBearerAuth)

    def test_build_auth_raises_when_no_credentials(self):
        with pytest.raises(JMAPAuthError):
            JMAPClient(url="http://x")

    def test_build_auth_explicit_bearer_type(self):
        from calendaring_jmap._http import HTTPBearerAuth

        client = JMAPClient(url="http://x", username="u", password="token", auth_type="bearer")
        assert isinstance(client._auth, HTTPBearerAuth)

    def test_build_auth_unsupported_type_raises(self):
        with pytest.raises(JMAPAuthError):
            JMAPClient(url="http://x", username="u", password="p", auth_type="digest")

    def test_build_auth_basic_without_username_raises(self):
        with pytest.raises(JMAPAuthError):
            JMAPClient(url="http://x", password="p", auth_type="basic")

    def test_build_auth_basic_without_password_raises(self):
        with pytest.raises(JMAPAuthError):
            JMAPClient(url="http://x", username="u", auth_type="basic")

    def test_build_auth_bearer_without_token_raises(self):
        with pytest.raises(JMAPAuthError):
            JMAPClient(url="http://x", username="u", auth_type="bearer")

    def test_get_calendars_returns_list(self, monkeypatch):
        client = _make_client_with_mocked_session(monkeypatch, _CALENDAR_GET_RESPONSE)
        cals = client.get_calendars()
        assert len(cals) == 2
        assert isinstance(cals[0], JMAPCalendar)
        assert cals[0].id == "cal1"
        assert cals[1].id == "cal2"

    def test_get_calendars_empty_response(self, monkeypatch):
        empty_response = {
            "methodResponses": [
                ["Calendar/get", {"accountId": _USERNAME, "state": "s1", "list": []}, "c0"]
            ]
        }
        client = _make_client_with_mocked_session(monkeypatch, empty_response)
        assert client.get_calendars() == []

    def test_request_raises_auth_error_on_401(self, monkeypatch):
        client = JMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        client._session_cache = Session(api_url=_API_URL, account_id=_USERNAME, state="s")

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status = MagicMock()
        mock_http = MagicMock()
        mock_http.post.return_value = mock_resp
        client._http_session = mock_http

        with pytest.raises(JMAPAuthError):
            client._request([("Calendar/get", {"accountId": _USERNAME, "ids": None}, "c0")])

    def test_request_raises_method_error_on_error_response(self, monkeypatch):
        error_response = {"methodResponses": [["error", {"type": "unknownMethod"}, "c0"]]}
        client = _make_client_with_mocked_session(monkeypatch, error_response)
        with pytest.raises(JMAPMethodError) as exc_info:
            client._request([("Calendar/get", {"accountId": _USERNAME}, "c0")])
        assert exc_info.value.error_type == "unknownMethod"

    def test_prebuilt_auth_object_takes_precedence(self):
        from calendaring_jmap._http import HTTPBearerAuth

        prebuilt = HTTPBearerAuth("prebuilt-token")
        client = JMAPClient(url="http://x", username="u", password="p", auth=prebuilt)
        assert client._auth is prebuilt


from calendaring_jmap.client import _JMAPClientBase


class TestJMAPClientBaseParsers:
    """Direct unit tests for the response-fallthrough branches of each
    _parse_* helper: what happens when the expected method name never
    appears in the responses list (e.g. a batched call whose relevant
    method was dropped or reordered by the server)."""

    def test_parse_get_calendars_returns_empty_list_without_match(self):
        assert _JMAPClientBase._parse_get_calendars([], client=None, is_async=False) == []

    def test_parse_create_event_response_raises_without_match(self):
        with pytest.raises(JMAPMethodError, match="No CalendarEvent/set response"):
            _JMAPClientBase._parse_create_event_response([], api_url=_API_URL)

    def test_parse_get_event_response_raises_without_match(self):
        with pytest.raises(JMAPMethodError, match="No CalendarEvent/get response"):
            _JMAPClientBase._parse_get_event_response([], api_url=_API_URL, event_id="ev1")

    def test_parse_get_event_response_skips_unrelated_responses_in_batch(self):
        raw_event = {
            "id": "ev1",
            "uid": "u@example.com",
            "title": "T",
            "start": "2024-01-01T00:00:00",
        }
        matching = ("CalendarEvent/get", {"list": [raw_event], "notFound": []}, "c1")
        result = _JMAPClientBase._parse_get_event_response(
            [self._UNRELATED_RESPONSE, matching], api_url=_API_URL, event_id="ev1"
        )
        assert result.id == "ev1"

    def test_parse_update_event_response_raises_without_match(self):
        with pytest.raises(JMAPMethodError, match="No CalendarEvent/set response"):
            _JMAPClientBase._parse_update_event_response([], api_url=_API_URL, event_id="ev1")

    def test_parse_search_response_returns_empty_list_without_match(self):
        assert _JMAPClientBase._parse_search_response([], parent=None) == []

    def test_parse_get_sync_token_response_raises_without_match(self):
        with pytest.raises(JMAPMethodError, match="No CalendarEvent/get response"):
            _JMAPClientBase._parse_get_sync_token_response([], api_url=_API_URL)

    def test_parse_delete_event_response_raises_without_match(self):
        with pytest.raises(JMAPMethodError, match="No CalendarEvent/set response"):
            _JMAPClientBase._parse_delete_event_response([], api_url=_API_URL, event_id="ev1")

    def test_parse_get_task_lists_response_returns_empty_list_without_match(self):
        assert _JMAPClientBase._parse_get_task_lists_response([]) == []

    def test_parse_create_task_response_raises_without_match(self):
        with pytest.raises(JMAPMethodError, match="No Task/set response"):
            _JMAPClientBase._parse_create_task_response([], api_url=_API_URL)

    def test_parse_get_task_response_raises_without_match(self):
        with pytest.raises(JMAPMethodError, match="No Task/get response"):
            _JMAPClientBase._parse_get_task_response([], api_url=_API_URL, task_id="t1")

    def test_parse_update_task_response_raises_without_match(self):
        with pytest.raises(JMAPMethodError, match="No Task/set response"):
            _JMAPClientBase._parse_update_task_response([], api_url=_API_URL, task_id="t1")

    def test_parse_delete_task_response_raises_without_match(self):
        with pytest.raises(JMAPMethodError, match="No Task/set response"):
            _JMAPClientBase._parse_delete_task_response([], api_url=_API_URL, task_id="t1")

    def test_parse_event_changes_response_without_match_returns_empty_defaults(self):
        result = _JMAPClientBase._parse_event_changes_response([], api_url=_API_URL)
        assert result == ([], [], [], "")

    _UNRELATED_RESPONSE: tuple[str, dict, str] = ("Calendar/changes", {}, "unrelated-0")

    def test_unsupported_null_keys_returns_none_without_match(self):
        assert (
            _JMAPClientBase._unsupported_null_keys(
                [self._UNRELATED_RESPONSE], "ev1", patch={}, nulled=frozenset()
            )
            is None
        )

    def test_unsupported_null_keys_skips_unrelated_responses_in_batch(self):
        matching = (
            "CalendarEvent/set",
            {"notUpdated": {"ev1": {"type": "invalidProperties", "properties": ["title"]}}},
            "c1",
        )
        assert (
            _JMAPClientBase._unsupported_null_keys(
                [self._UNRELATED_RESPONSE, matching],
                "ev1",
                patch={"title": "x"},
                nulled=frozenset(),
            )
            is None
        )

    def test_parse_get_calendars_skips_unrelated_responses_in_batch(self):
        assert (
            _JMAPClientBase._parse_get_calendars(
                [self._UNRELATED_RESPONSE], client=None, is_async=False
            )
            == []
        )

    def test_parse_create_event_response_skips_unrelated_responses_in_batch(self):
        with pytest.raises(JMAPMethodError, match="No CalendarEvent/set response"):
            _JMAPClientBase._parse_create_event_response(
                [self._UNRELATED_RESPONSE], api_url=_API_URL
            )

    def test_parse_update_event_response_skips_unrelated_responses_in_batch(self):
        with pytest.raises(JMAPMethodError, match="No CalendarEvent/set response"):
            _JMAPClientBase._parse_update_event_response(
                [self._UNRELATED_RESPONSE], api_url=_API_URL, event_id="ev1"
            )

    def test_parse_search_response_skips_unrelated_responses_in_batch(self):
        assert _JMAPClientBase._parse_search_response([self._UNRELATED_RESPONSE], parent=None) == []

    def test_parse_get_sync_token_response_skips_unrelated_responses_in_batch(self):
        with pytest.raises(JMAPMethodError, match="No CalendarEvent/get response"):
            _JMAPClientBase._parse_get_sync_token_response(
                [self._UNRELATED_RESPONSE], api_url=_API_URL
            )

    def test_parse_event_changes_response_skips_unrelated_responses_in_batch(self):
        result = _JMAPClientBase._parse_event_changes_response(
            [("Task/set", {}, "unrelated-0")], api_url=_API_URL
        )
        assert result == ([], [], [], "")

    def test_parse_delete_event_response_skips_unrelated_responses_in_batch(self):
        with pytest.raises(JMAPMethodError, match="No CalendarEvent/set response"):
            _JMAPClientBase._parse_delete_event_response(
                [self._UNRELATED_RESPONSE], api_url=_API_URL, event_id="ev1"
            )

    def test_parse_get_task_lists_response_skips_unrelated_responses_in_batch(self):
        assert _JMAPClientBase._parse_get_task_lists_response([self._UNRELATED_RESPONSE]) == []

    def test_parse_create_task_response_skips_unrelated_responses_in_batch(self):
        with pytest.raises(JMAPMethodError, match="No Task/set response"):
            _JMAPClientBase._parse_create_task_response(
                [self._UNRELATED_RESPONSE], api_url=_API_URL
            )

    def test_parse_get_task_response_skips_unrelated_responses_in_batch(self):
        with pytest.raises(JMAPMethodError, match="No Task/get response"):
            _JMAPClientBase._parse_get_task_response(
                [self._UNRELATED_RESPONSE], api_url=_API_URL, task_id="t1"
            )

    def test_parse_update_task_response_skips_unrelated_responses_in_batch(self):
        with pytest.raises(JMAPMethodError, match="No Task/set response"):
            _JMAPClientBase._parse_update_task_response(
                [self._UNRELATED_RESPONSE], api_url=_API_URL, task_id="t1"
            )

    def test_parse_delete_task_response_skips_unrelated_responses_in_batch(self):
        with pytest.raises(JMAPMethodError, match="No Task/set response"):
            _JMAPClientBase._parse_delete_task_response(
                [self._UNRELATED_RESPONSE], api_url=_API_URL, task_id="t1"
            )

    def test_assemble_sync_token_result_skips_unrelated_responses_in_batch(self):
        raw_event = {
            "id": "ev1",
            "uid": "u1@example.com",
            "title": "T",
            "start": "2024-01-01T00:00:00",
        }
        get_response = ("CalendarEvent/get", {"list": [raw_event], "notFound": []}, "c1")
        added, modified, deleted, token = _JMAPClientBase._assemble_sync_token_result(
            [self._UNRELATED_RESPONSE, get_response], ["ev1"], [], [], "new-state"
        )
        assert len(added) == 1
        assert added[0].id == "ev1"
        assert modified == [] and deleted == [] and token == "new-state"

    def test_client_del_swallows_close_exception(self):
        client = JMAPClient(url="http://x", username="u", password="p")
        client.close = MagicMock(side_effect=RuntimeError("interpreter shutting down"))
        client.__del__()  # must not raise

    def test_get_session_fetches_and_caches_on_first_call(self, monkeypatch):
        client = JMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        fetched = Session(api_url=_API_URL, account_id=_USERNAME, state="fetched-state")
        mock_fetch = MagicMock(return_value=fetched)
        monkeypatch.setattr("calendaring_jmap.client.fetch_session", mock_fetch)
        session = client._get_session()
        assert session is fetched
        assert client._session_cache is fetched
        client._get_session()
        mock_fetch.assert_called_once()


from calendaring_jmap import get_jmap_client


class TestGetJMAPClient:
    def test_returns_client_with_explicit_params(self):
        client = get_jmap_client(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        assert isinstance(client, JMAPClient)
        assert client.url == _JMAP_URL

    def test_returns_none_when_no_config(self, monkeypatch, tmp_path):
        for var in ("JMAP_URL", "JMAP_USERNAME", "JMAP_PASSWORD", "JMAP_CONFIG_FILE"):
            monkeypatch.delenv(var, raising=False)
        # Point at a config file location that doesn't exist, so a real
        # ~/.config/calendaring-jmap/calendar.yaml on the test runner's
        # machine can't leak into the result.
        client = get_jmap_client(config_file=tmp_path / "no-such-file.yaml")
        assert client is None

    def test_env_vars_resolve_a_client(self, monkeypatch):
        monkeypatch.setenv("JMAP_URL", _JMAP_URL)
        monkeypatch.setenv("JMAP_USERNAME", _USERNAME)
        monkeypatch.setenv("JMAP_PASSWORD", _PASSWORD)
        client = get_jmap_client()
        assert isinstance(client, JMAPClient)
        assert client.url == _JMAP_URL

    def test_strips_unknown_keys(self, monkeypatch):
        client = get_jmap_client(
            url=_JMAP_URL,
            username=_USERNAME,
            password=_PASSWORD,
            some_unrelated_kwarg=True,
        )
        assert isinstance(client, JMAPClient)
        assert not hasattr(client, "some_unrelated_kwarg")


from calendaring_jmap import get_async_jmap_client


class TestGetAsyncJMAPClient:
    def test_returns_client_with_explicit_params(self):
        client = get_async_jmap_client(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        assert isinstance(client, AsyncJMAPClient)
        assert client.url == _JMAP_URL

    def test_returns_none_when_no_config(self, monkeypatch, tmp_path):
        for var in ("JMAP_URL", "JMAP_USERNAME", "JMAP_PASSWORD", "JMAP_CONFIG_FILE"):
            monkeypatch.delenv(var, raising=False)
        client = get_async_jmap_client(config_file=tmp_path / "no-such-file.yaml")
        assert client is None

    def test_strips_unknown_keys(self):
        client = get_async_jmap_client(
            url=_JMAP_URL,
            username=_USERNAME,
            password=_PASSWORD,
            some_unrelated_kwarg=True,
        )
        assert isinstance(client, AsyncJMAPClient)
        assert not hasattr(client, "some_unrelated_kwarg")


from calendaring_jmap._config import get_connection_params


class TestGetConnectionParamsConfigFile:
    def test_reads_url_from_yaml_file(self, monkeypatch, tmp_path):
        for var in ("JMAP_URL", "JMAP_USERNAME", "JMAP_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        config_file = tmp_path / "calendar.yaml"
        config_file.write_text(f"url: {_JMAP_URL}\nusername: {_USERNAME}\n")
        params = get_connection_params(config_file=config_file)
        assert params["url"] == _JMAP_URL
        assert params["username"] == _USERNAME

    def test_yaml_file_drops_keys_outside_conn_keys(self, monkeypatch, tmp_path):
        for var in ("JMAP_URL", "JMAP_USERNAME", "JMAP_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        config_file = tmp_path / "calendar.yaml"
        config_file.write_text(f"url: {_JMAP_URL}\nirrelevant_key: something\n")
        params = get_connection_params(config_file=config_file)
        assert "irrelevant_key" not in params

    def test_yaml_file_non_dict_top_level_is_ignored(self, monkeypatch, tmp_path):
        for var in ("JMAP_URL", "JMAP_USERNAME", "JMAP_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        config_file = tmp_path / "calendar.yaml"
        config_file.write_text("- just\n- a\n- list\n")
        params = get_connection_params(config_file=config_file)
        assert params is None

    def test_explicit_and_env_override_file_config(self, monkeypatch, tmp_path):
        monkeypatch.delenv("JMAP_URL", raising=False)
        monkeypatch.setenv("JMAP_USERNAME", "env-user")
        config_file = tmp_path / "calendar.yaml"
        config_file.write_text(f"url: {_JMAP_URL}\nusername: file-user\npassword: file-pass\n")
        params = get_connection_params(config_file=config_file, password="explicit-pass")
        assert params["url"] == _JMAP_URL
        assert params["username"] == "env-user"
        assert params["password"] == "explicit-pass"

    def test_config_file_env_var_used_when_no_explicit_path(self, monkeypatch, tmp_path):
        for var in ("JMAP_URL", "JMAP_USERNAME", "JMAP_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        config_file = tmp_path / "calendar.yaml"
        config_file.write_text(f"url: {_JMAP_URL}\n")
        monkeypatch.setenv("JMAP_CONFIG_FILE", str(config_file))
        params = get_connection_params()
        assert params["url"] == _JMAP_URL


from calendaring_jmap._methods.event import (
    build_event_changes,
    build_event_get,
    build_event_query,
    build_event_query_changes,
    build_event_set_create,
    build_event_set_destroy,
    build_event_set_update,
    parse_event_changes,
    parse_event_get,
    parse_event_query,
    parse_event_set,
)
from calendaring_jmap._methods.task import (
    build_task_get,
    build_task_list_get,
    build_task_set_create,
    build_task_set_destroy,
    build_task_set_update,
    parse_task_get,
    parse_task_list_get,
    parse_task_set,
)


class TestEventMethodBuilders:
    def test_build_event_get_structure(self):
        method, args, call_id = build_event_get("u1")
        assert method == "CalendarEvent/get"
        assert args["accountId"] == "u1"
        assert args["ids"] is None
        assert isinstance(call_id, str)

    def test_build_event_get_with_ids(self):
        _, args, _ = build_event_get("u1", ids=["ev1", "ev2"])
        assert args["ids"] == ["ev1", "ev2"]

    def test_build_event_get_with_properties(self):
        _, args, _ = build_event_get("u1", properties=["id", "title", "start"])
        assert args["properties"] == ["id", "title", "start"]

    def test_build_event_get_no_properties_key_when_not_set(self):
        _, args, _ = build_event_get("u1")
        assert "properties" not in args

    def test_parse_event_get_returns_events(self):
        event_dict = {
            "id": "ev1",
            "uid": "abc@example.com",
            "calendarIds": {"cal1": True},
            "title": "Test",
            "start": "2024-01-01T09:00:00",
        }
        response_args = {"list": [event_dict]}
        events = parse_event_get(response_args)
        assert len(events) == 1
        assert isinstance(events[0], dict)
        assert events[0]["id"] == "ev1"

    def test_parse_event_get_empty_list(self):
        assert parse_event_get({"list": []}) == []

    def test_parse_event_get_missing_list_key(self):
        assert parse_event_get({}) == []

    def test_build_event_changes_structure(self):
        method, args, call_id = build_event_changes("u1", "state-abc")
        assert method == "CalendarEvent/changes"
        assert args["accountId"] == "u1"
        assert args["sinceState"] == "state-abc"
        assert isinstance(call_id, str)

    def test_build_event_changes_with_max_changes(self):
        _, args, _ = build_event_changes("u1", "state-abc", max_changes=50)
        assert args["maxChanges"] == 50

    def test_build_event_changes_no_max_changes_key_when_not_set(self):
        _, args, _ = build_event_changes("u1", "state-abc")
        assert "maxChanges" not in args

    def test_build_event_query_structure(self):
        method, args, call_id = build_event_query("u1")
        assert method == "CalendarEvent/query"
        assert args["accountId"] == "u1"
        assert args["position"] == 0
        assert isinstance(call_id, str)

    def test_build_event_query_with_filter(self):
        f = {"after": "2024-01-01T00:00:00Z", "before": "2024-12-31T23:59:59Z"}
        _, args, _ = build_event_query("u1", filter_condition=f)
        assert args["filter"] == f

    def test_build_event_query_with_sort(self):
        s = [{"property": "start", "isAscending": True}]
        _, args, _ = build_event_query("u1", sort=s)
        assert args["sort"] == s

    def test_build_event_query_with_limit(self):
        _, args, _ = build_event_query("u1", limit=100)
        assert args["limit"] == 100

    def test_build_event_query_no_optional_keys_when_not_set(self):
        _, args, _ = build_event_query("u1")
        assert "filter" not in args
        assert "sort" not in args
        assert "limit" not in args

    def test_parse_event_query_returns_ids_state_total(self):
        response_args = {
            "ids": ["ev1", "ev2", "ev3"],
            "queryState": "qstate-1",
            "total": 10,
        }
        ids, query_state, total = parse_event_query(response_args)
        assert ids == ["ev1", "ev2", "ev3"]
        assert query_state == "qstate-1"
        assert total == 10

    def test_parse_event_query_total_defaults_to_ids_length(self):
        response_args = {"ids": ["ev1", "ev2"], "queryState": "q1"}
        ids, _, total = parse_event_query(response_args)
        assert total == 2

    def test_parse_event_query_empty_response(self):
        ids, query_state, total = parse_event_query({})
        assert ids == []
        assert query_state == ""
        assert total == 0

    def test_build_event_query_changes_structure(self):
        method, args, call_id = build_event_query_changes("u1", "qstate-1")
        assert method == "CalendarEvent/queryChanges"
        assert args["accountId"] == "u1"
        assert args["sinceQueryState"] == "qstate-1"
        assert isinstance(call_id, str)

    def test_build_event_query_changes_with_filter_and_sort(self):
        f = {"calendarIds": {"cal1": True}}
        s = [{"property": "start", "isAscending": True}]
        _, args, _ = build_event_query_changes("u1", "qstate-1", filter_condition=f, sort=s)
        assert args["filter"] == f
        assert args["sort"] == s

    def test_build_event_query_changes_with_max_changes(self):
        _, args, _ = build_event_query_changes("u1", "qstate-1", max_changes=25)
        assert args["maxChanges"] == 25

    def test_build_event_set_create_structure(self):
        ev = {
            "uid": "abc@example.com",
            "calendarIds": {"cal1": True},
            "title": "Test",
            "start": "2024-01-01T09:00:00",
        }
        method, args, call_id = build_event_set_create("u1", {"new-1": ev})
        assert method == "CalendarEvent/set"
        assert "create" in args
        assert "new-1" in args["create"]
        assert "id" not in args["create"]["new-1"]

    def test_build_event_set_update_structure(self):
        method, args, call_id = build_event_set_update("u1", {"ev1": {"title": "Updated title"}})
        assert method == "CalendarEvent/set"
        assert args["update"] == {"ev1": {"title": "Updated title"}}

    def test_build_event_set_destroy_structure(self):
        method, args, call_id = build_event_set_destroy("u1", ["ev1", "ev2"])
        assert method == "CalendarEvent/set"
        assert args["destroy"] == ["ev1", "ev2"]

    def test_parse_event_set_created(self):
        response_args = {
            "created": {"new-1": {"id": "server-ev-99", "uid": "def456@example.com"}},
            "updated": None,
            "destroyed": None,
        }
        created, updated, destroyed, not_created, not_updated, not_destroyed = parse_event_set(
            response_args
        )
        assert created["new-1"]["id"] == "server-ev-99"
        assert updated == {}
        assert destroyed == []
        assert not_created == {}
        assert not_updated == {}
        assert not_destroyed == {}

    def test_parse_event_set_destroyed(self):
        response_args = {"created": None, "updated": None, "destroyed": ["ev1", "ev2"]}
        created, updated, destroyed, not_created, not_updated, not_destroyed = parse_event_set(
            response_args
        )
        assert created == {}
        assert updated == {}
        assert destroyed == ["ev1", "ev2"]
        assert not_created == {}

    def test_parse_event_set_empty_response(self):
        created, updated, destroyed, not_created, not_updated, not_destroyed = parse_event_set({})
        assert created == {}
        assert updated == {}
        assert destroyed == []
        assert not_created == {}
        assert not_updated == {}
        assert not_destroyed == {}

    def test_parse_event_set_partial_failure(self):
        # notCreated/notUpdated/notDestroyed carry SetError objects for failed operations
        response_args = {
            "created": {"new-1": {"id": "server-ev-99"}},
            "notCreated": {"new-2": {"type": "invalidArguments", "description": "bad uid"}},
            "notDestroyed": {"ev-old": {"type": "notFound"}},
        }
        created, updated, destroyed, not_created, not_updated, not_destroyed = parse_event_set(
            response_args
        )
        assert "new-1" in created
        assert not_created["new-2"]["type"] == "invalidArguments"
        assert not_destroyed["ev-old"]["type"] == "notFound"


from datetime import date, timedelta

import icalendar as _icalendar

from calendaring_jmap.convert import ical_to_jscal, jscal_to_ical
from calendaring_jmap.convert._utils import (
    _duration_to_timedelta,
    _format_local_dt,
    _timedelta_to_duration,
)


def _make_ical(extra_lines: str = "", uid: str = "test-uid@example.com") -> str:
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//Test//EN\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        "DTSTAMP:20240101T000000Z\r\n" + extra_lines + "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )


def _minimal_jscal(**kwargs) -> dict:
    base = {
        "uid": "test-uid@example.com",
        "title": "Test Event",
        "start": "2024-06-15T10:00:00",
        "timeZone": "Europe/Berlin",
        "duration": "PT1H",
    }
    base.update(kwargs)
    return base


class TestUtils:
    def test_timedelta_to_duration_hours(self):
        assert _timedelta_to_duration(timedelta(hours=1, minutes=30)) == "PT1H30M"

    def test_timedelta_to_duration_days(self):
        assert _timedelta_to_duration(timedelta(days=1)) == "P1D"

    def test_timedelta_to_duration_mixed(self):
        assert _timedelta_to_duration(timedelta(days=1, hours=2)) == "P1DT2H"

    def test_timedelta_to_duration_zero(self):
        assert _timedelta_to_duration(timedelta(0)) == "P0D"

    def test_timedelta_to_duration_negative(self):
        assert _timedelta_to_duration(timedelta(seconds=-900)) == "-PT15M"

    def test_duration_to_timedelta_hours(self):
        assert _duration_to_timedelta("PT1H30M") == timedelta(hours=1, minutes=30)

    def test_duration_to_timedelta_days(self):
        assert _duration_to_timedelta("P1D") == timedelta(days=1)

    def test_duration_to_timedelta_zero(self):
        assert _duration_to_timedelta("P0D") == timedelta(0)

    def test_duration_to_timedelta_negative(self):
        assert _duration_to_timedelta("-PT15M") == timedelta(seconds=-900)

    def test_duration_round_trip(self):
        td = timedelta(days=2, hours=3, minutes=45, seconds=30)
        assert _duration_to_timedelta(_timedelta_to_duration(td)) == td

    def test_format_local_dt_utc(self):
        # RFC 8984: LocalDateTime slots (override keys, RRULE until) must not carry Z suffix.
        dt = datetime(2024, 6, 15, 9, 0, 0, tzinfo=timezone.utc)
        assert _format_local_dt(dt) == "2024-06-15T09:00:00"

    def test_format_local_dt_naive(self):
        dt = datetime(2024, 6, 15, 9, 0, 0)
        assert _format_local_dt(dt) == "2024-06-15T09:00:00"

    def test_format_local_dt_date(self):
        d = date(2024, 6, 15)
        assert _format_local_dt(d) == "2024-06-15T00:00:00"

    def test_duration_to_timedelta_explicit_plus_sign(self):
        assert _duration_to_timedelta("+PT1H") == timedelta(hours=1)

    def test_duration_to_timedelta_raises_without_p_prefix(self):
        with pytest.raises(ValueError, match="Invalid duration string"):
            _duration_to_timedelta("garbage")

    def test_duration_to_timedelta_weeks_only(self):
        assert _duration_to_timedelta("P2W") == timedelta(weeks=2)

    def test_duration_to_timedelta_weeks_and_days(self):
        assert _duration_to_timedelta("P1W3D") == timedelta(weeks=1, days=3)


class TestFixup:
    def test_to_normal_str_passes_through_none(self):
        from calendaring_jmap.convert._fixup import _to_normal_str

        assert _to_normal_str(None) is None

    def test_fixup_decodes_bytes_input(self):
        from calendaring_jmap.convert._fixup import fixup

        ical_bytes = _make_ical("DTSTART:20240615T100000Z\r\nSUMMARY:Bytes Event\r\n").encode(
            "utf-8"
        )
        result = fixup(ical_bytes)
        assert "SUMMARY:Bytes Event" in result

    def test_fixup_drops_duplicate_dtstamp(self):
        from calendaring_jmap.convert._fixup import fixup

        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
            "UID:dup-dtstamp@example.com\r\n"
            "DTSTAMP:20240101T000000Z\r\n"
            "DTSTAMP:20240102T000000Z\r\n"
            "DTSTART:20240615T100000Z\r\nSUMMARY:Dup Stamp\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        result = fixup(ical)
        assert result.count("DTSTAMP:") == 1
        assert "DTSTAMP:20240101T000000Z" in result

    def test_fixup_drops_duplicate_dtend(self):
        from calendaring_jmap.convert._fixup import fixup

        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
            "UID:dup-dtend@example.com\r\n"
            "DTSTAMP:20240101T000000Z\r\n"
            "DTSTART:20240615T100000Z\r\n"
            "DTEND:20240615T110000Z\r\n"
            "DTEND:20240615T120000Z\r\n"
            "SUMMARY:Dup End\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        result = fixup(ical)
        assert result.count("DTEND:") == 1
        assert "DTEND:20240615T110000Z" in result

    def test_fixup_truncated_data_without_end_line_skips_dtstamp_fixup(self, caplog):
        from calendaring_jmap.convert._fixup import fixup

        truncated = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:trunc@example.com\r\n"
        result = fixup(truncated)
        assert "DTSTAMP:" not in result


class TestIcalToJscal:
    def test_minimal_event(self):
        ical = _make_ical("DTSTART:20240615T100000Z\r\nDURATION:PT1H\r\nSUMMARY:Test Event\r\n")
        result = ical_to_jscal(ical)
        assert result["@type"] == "Event"
        assert result["uid"] == "test-uid@example.com"
        assert result["title"] == "Test Event"
        assert result["start"] == "2024-06-15T10:00:00"
        assert result["timeZone"] == "Etc/UTC"
        assert result["duration"] == "PT1H"

    def test_all_day_event(self):
        ical = _make_ical(
            "DTSTART;VALUE=DATE:20240615\r\nDTEND;VALUE=DATE:20240616\r\nSUMMARY:All Day\r\n"
        )
        result = ical_to_jscal(ical)
        assert result["start"] == "2024-06-15T00:00:00"
        assert result["showWithoutTime"] is True
        assert "timeZone" not in result
        assert result["duration"] == "P1D"

    def test_timezone_aware_event(self):
        ical = _make_ical(
            "DTSTART;TZID=America/New_York:20240615T100000\r\nDURATION:PT1H\r\nSUMMARY:TZ Event\r\n"
        )
        result = ical_to_jscal(ical)
        assert result["start"] == "2024-06-15T10:00:00"
        assert result["timeZone"] == "America/New_York"
        assert "showWithoutTime" not in result

    def test_prop_date_or_datetime_rejects_non_date_value(self):
        from calendaring_jmap.convert.ical_to_jscal import _prop_date_or_datetime

        with pytest.raises(ValueError, match="Expected a date or datetime property value"):
            _prop_date_or_datetime(MagicMock(dt="not a date"))

    def test_prop_timedelta_rejects_non_timedelta_value(self):
        from calendaring_jmap.convert.ical_to_jscal import _prop_timedelta

        with pytest.raises(ValueError, match="Expected a timedelta property value"):
            _prop_timedelta(MagicMock(dt="not a timedelta"))

    def test_utc_event(self):
        ical = _make_ical("DTSTART:20240615T100000Z\r\nDURATION:PT30M\r\nSUMMARY:UTC Event\r\n")
        result = ical_to_jscal(ical)
        assert result["start"] == "2024-06-15T10:00:00"
        assert result["timeZone"] == "Etc/UTC"

    def test_duration_from_dtend(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nDTEND:20240615T113000Z\r\nSUMMARY:DTEND Event\r\n"
        )
        result = ical_to_jscal(ical)
        assert result["duration"] == "PT1H30M"

    def test_duration_explicit(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nDURATION:P1DT2H\r\nSUMMARY:Duration Event\r\n"
        )
        result = ical_to_jscal(ical)
        assert result["duration"] == "P1DT2H"

    def test_duration_zero_when_missing(self):
        ical = _make_ical("DTSTART:20240615T100000Z\r\nSUMMARY:No Duration\r\n")
        result = ical_to_jscal(ical)
        assert result["duration"] == "P0D"

    def test_categories_to_keywords(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Cat Event\r\nCATEGORIES:work,standup\r\n"
        )
        result = ical_to_jscal(ical)
        assert "keywords" in result
        assert result["keywords"].get("work") is True
        assert result["keywords"].get("standup") is True

    def test_categories_multiple_lines(self):
        # Two separate CATEGORIES lines — icalendar returns a list of vCategory objects
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Cat Event\r\n"
            "CATEGORIES:Work\r\nCATEGORIES:Standup\r\n"
        )
        result = ical_to_jscal(ical)
        assert "keywords" in result
        assert result["keywords"].get("Work") is True
        assert result["keywords"].get("Standup") is True

    def test_location_string(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Located Event\r\nLOCATION:Conference Room A\r\n"
        )
        result = ical_to_jscal(ical)
        assert "locations" in result
        locs = result["locations"]
        assert len(locs) == 1
        first_loc = next(iter(locs.values()))
        assert first_loc["name"] == "Conference Room A"

    def test_priority(self):
        ical = _make_ical("DTSTART:20240615T100000Z\r\nSUMMARY:Priority Event\r\nPRIORITY:5\r\n")
        result = ical_to_jscal(ical)
        assert result["priority"] == 5

    def test_class_private(self):
        ical = _make_ical("DTSTART:20240615T100000Z\r\nSUMMARY:Private Event\r\nCLASS:PRIVATE\r\n")
        result = ical_to_jscal(ical)
        assert result["privacy"] == "private"

    def test_class_confidential(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Confidential Event\r\nCLASS:CONFIDENTIAL\r\n"
        )
        result = ical_to_jscal(ical)
        assert result["privacy"] == "secret"

    def test_transp_transparent(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Free Event\r\nTRANSP:TRANSPARENT\r\n"
        )
        result = ical_to_jscal(ical)
        assert result["freeBusyStatus"] == "free"

    def test_rrule_weekly(self):
        ical = _make_ical(
            "DTSTART;TZID=Europe/Berlin:20240617T140000\r\n"
            "DURATION:PT1H\r\n"
            "SUMMARY:Team Meeting\r\n"
            "RRULE:FREQ=WEEKLY;BYDAY=MO,WE\r\n"
        )
        result = ical_to_jscal(ical)
        assert "recurrenceRules" in result
        rule = result["recurrenceRules"][0]
        assert rule["@type"] == "RecurrenceRule"
        assert rule["frequency"] == "weekly"
        assert rule["interval"] == 1
        assert rule["rscale"] == "gregorian"
        days = [d["day"] for d in rule["byDay"]]
        assert "mo" in days
        assert "we" in days

    def test_rrule_byday_with_nth_of_period(self):
        ical = _make_ical(
            "DTSTART:20240617T140000Z\r\nDURATION:PT1H\r\nSUMMARY:Monthly\r\n"
            "RRULE:FREQ=MONTHLY;BYDAY=2MO\r\n"
        )
        result = ical_to_jscal(ical)
        nday = result["recurrenceRules"][0]["byDay"][0]
        assert nday["day"] == "mo"
        assert nday["nthOfPeriod"] == 2

    def test_rrule_all_by_components(self):
        ical = _make_ical(
            "DTSTART:20240617T140000Z\r\nDURATION:PT1H\r\nSUMMARY:Complex\r\n"
            "RRULE:FREQ=YEARLY;BYMONTH=6;BYMONTHDAY=15;BYYEARDAY=166;"
            "BYWEEKNO=24;BYHOUR=14;BYMINUTE=30;BYSECOND=15;BYSETPOS=1\r\n"
        )
        result = ical_to_jscal(ical)
        rule = result["recurrenceRules"][0]
        assert rule["byMonth"] == ["6"]
        assert rule["byMonthDay"] == [15]
        assert rule["byYearDay"] == [166]
        assert rule["byWeekNo"] == [24]
        assert rule["byHour"] == [14]
        assert rule["byMinute"] == [30]
        assert rule["bySecond"] == [15]
        assert rule["bySetPosition"] == [1]

    def test_exdate(self):
        ical = _make_ical(
            "DTSTART;TZID=Europe/Berlin:20240617T140000\r\n"
            "DURATION:PT1H\r\n"
            "SUMMARY:Recurring\r\n"
            "RRULE:FREQ=WEEKLY\r\n"
            "EXDATE;TZID=Europe/Berlin:20240624T140000\r\n"
        )
        result = ical_to_jscal(ical)
        assert "recurrenceOverrides" in result
        overrides = result["recurrenceOverrides"]
        assert any(v == {"excluded": True} for v in overrides.values())

    def test_valarm_relative(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\n"
            "SUMMARY:Alarm Event\r\n"
            "BEGIN:VALARM\r\n"
            "ACTION:DISPLAY\r\n"
            "TRIGGER:-PT15M\r\n"
            "DESCRIPTION:Reminder\r\n"
            "END:VALARM\r\n"
        )
        result = ical_to_jscal(ical)
        assert "alerts" in result
        alert = next(iter(result["alerts"].values()))
        assert alert["trigger"] == "-PT15M"
        assert alert["action"] == "display"

    def test_valarm_absolute(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\n"
            "SUMMARY:Abs Alarm Event\r\n"
            "BEGIN:VALARM\r\n"
            "ACTION:DISPLAY\r\n"
            "TRIGGER;VALUE=DATE-TIME:20240615T093000Z\r\n"
            "DESCRIPTION:Reminder\r\n"
            "END:VALARM\r\n"
        )
        result = ical_to_jscal(ical)
        assert "alerts" in result
        alert = next(iter(result["alerts"].values()))
        assert alert["trigger"].endswith("Z")

    def test_valarm_related_end(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\n"
            "SUMMARY:End Alarm Event\r\n"
            "BEGIN:VALARM\r\n"
            "ACTION:DISPLAY\r\n"
            "TRIGGER;RELATED=END:-PT5M\r\n"
            "END:VALARM\r\n"
        )
        result = ical_to_jscal(ical)
        alert = next(iter(result["alerts"].values()))
        assert alert["trigger"] == "-PT5M"
        assert alert.get("relativeTo") == "end"

    def test_organizer_attendee(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\n"
            "SUMMARY:Meeting\r\n"
            "ORGANIZER;CN=Alice:mailto:alice@example.com\r\n"
            "ATTENDEE;CN=Bob;PARTSTAT=ACCEPTED:mailto:bob@example.com\r\n"
        )
        result = ical_to_jscal(ical)
        assert "participants" in result
        participants = result["participants"]
        # Find organizer
        organizer = next(
            (p for p in participants.values() if p.get("roles", {}).get("owner")), None
        )
        assert organizer is not None
        assert organizer["roles"].get("organizer") is True
        # Find attendee
        attendee = next(
            (p for p in participants.values() if p.get("roles", {}).get("attendee")), None
        )
        assert attendee is not None

    def test_attendee_partstat(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\n"
            "SUMMARY:Meeting\r\n"
            "ATTENDEE;PARTSTAT=DECLINED:mailto:bob@example.com\r\n"
        )
        result = ical_to_jscal(ical)
        attendee = next(iter(result["participants"].values()))
        assert attendee["participationStatus"] == "declined"

    def test_calendar_id_set(self):
        ical = _make_ical("DTSTART:20240615T100000Z\r\nSUMMARY:Cal Event\r\n")
        result = ical_to_jscal(ical, calendar_id="Default")
        assert result["calendarIds"] == {"Default": True}

    def test_no_calendar_id_omits_key(self):
        ical = _make_ical("DTSTART:20240615T100000Z\r\nSUMMARY:No Cal\r\n")
        result = ical_to_jscal(ical)
        assert "calendarIds" not in result

    def test_floating_datetime(self):
        ical = _make_ical("DTSTART:20240615T100000\r\nDURATION:PT1H\r\nSUMMARY:Floating\r\n")
        result = ical_to_jscal(ical)
        assert result["start"] == "2024-06-15T10:00:00"
        assert "timeZone" not in result
        assert result.get("showWithoutTime") is not True

    def test_recurrence_id_child_vevent(self):
        ical = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:recur-uid@example.com\r\n"
            "DTSTAMP:20240101T000000Z\r\n"
            "DTSTART:20240617T140000Z\r\n"
            "DURATION:PT1H\r\n"
            "SUMMARY:Weekly Meeting\r\n"
            "RRULE:FREQ=WEEKLY\r\n"
            "END:VEVENT\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:recur-uid@example.com\r\n"
            "DTSTAMP:20240101T000000Z\r\n"
            "RECURRENCE-ID:20240624T140000Z\r\n"
            "DTSTART:20240624T160000Z\r\n"
            "DURATION:PT2H\r\n"
            "SUMMARY:Rescheduled Meeting\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        result = ical_to_jscal(ical)
        assert "recurrenceOverrides" in result
        overrides = result["recurrenceOverrides"]
        assert len(overrides) == 1
        key = next(iter(overrides))
        patch = overrides[key]
        assert isinstance(patch, dict)
        assert patch.get("excluded") is not True
        assert patch.get("title") == "Rescheduled Meeting"

    def test_color_and_sequence(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Colored\r\nCOLOR:red\r\nSEQUENCE:3\r\n"
        )
        result = ical_to_jscal(ical)
        assert result.get("color") == "red"
        assert result.get("sequence") == 3

    def test_rrule_missing_freq_raises(self):
        ical = _make_ical("DTSTART:20240615T100000Z\r\nSUMMARY:Bad RRULE\r\nRRULE:INTERVAL=2\r\n")
        with pytest.raises((ValueError, Exception)):
            ical_to_jscal(ical)

    def test_organizer_without_cn(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Meeting\r\nORGANIZER:mailto:alice@example.com\r\n"
        )
        result = ical_to_jscal(ical)
        organizer = next(iter(result["participants"].values()))
        assert "name" not in organizer
        assert organizer["email"] == "alice@example.com"

    def test_attendee_without_partstat(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Meeting\r\nATTENDEE:mailto:bob@example.com\r\n"
        )
        result = ical_to_jscal(ical)
        attendee = next(iter(result["participants"].values()))
        assert "participationStatus" not in attendee

    def test_attendee_rsvp_true(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Meeting\r\n"
            "ATTENDEE;RSVP=TRUE:mailto:bob@example.com\r\n"
        )
        result = ical_to_jscal(ical)
        attendee = next(iter(result["participants"].values()))
        assert attendee["expectReply"] is True

    def test_attendee_cutype_room(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Meeting\r\n"
            "ATTENDEE;CUTYPE=ROOM:mailto:room1@example.com\r\n"
        )
        result = ical_to_jscal(ical)
        attendee = next(iter(result["participants"].values()))
        assert attendee["kind"] == "room"

    def test_attendee_role_chair(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Meeting\r\n"
            "ATTENDEE;ROLE=CHAIR:mailto:chair@example.com\r\n"
        )
        result = ical_to_jscal(ical)
        attendee = next(iter(result["participants"].values()))
        assert attendee["roles"]["chair"] is True

    def test_multiple_attendees(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Meeting\r\n"
            "ATTENDEE:mailto:bob@example.com\r\n"
            "ATTENDEE:mailto:carol@example.com\r\n"
        )
        result = ical_to_jscal(ical)
        emails = {p["email"] for p in result["participants"].values()}
        assert emails == {"bob@example.com", "carol@example.com"}

    def test_valarm_without_trigger(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:No Trigger\r\n"
            "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Reminder\r\nEND:VALARM\r\n"
        )
        result = ical_to_jscal(ical)
        alert = next(iter(result["alerts"].values()))
        assert "trigger" not in alert
        assert alert["description"] == "Reminder"

    def test_exdate_single_value_not_list(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nDURATION:PT1H\r\nSUMMARY:Recurring\r\n"
            "RRULE:FREQ=DAILY\r\nEXDATE:20240616T100000Z\r\n"
        )
        result = ical_to_jscal(ical)
        assert "recurrenceOverrides" in result
        assert len(result["recurrenceOverrides"]) == 1

    def test_exdate_multiple_lines_already_a_list(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nDURATION:PT1H\r\nSUMMARY:Recurring\r\n"
            "RRULE:FREQ=DAILY\r\nEXDATE:20240616T100000Z\r\nEXDATE:20240617T100000Z\r\n"
        )
        result = ical_to_jscal(ical)
        assert len(result["recurrenceOverrides"]) == 2

    def test_rrule_multiple_lines_already_a_list(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nDURATION:PT1H\r\nSUMMARY:Multi RRULE\r\n"
            "RRULE:FREQ=DAILY\r\nRRULE:FREQ=WEEKLY\r\n"
        )
        result = ical_to_jscal(ical)
        freqs = {r["frequency"] for r in result["recurrenceRules"]}
        assert freqs == {"daily", "weekly"}

    def test_exrule_multiple_lines_already_a_list(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nDURATION:PT1H\r\nSUMMARY:Multi EXRULE\r\n"
            "RRULE:FREQ=DAILY\r\nEXRULE:FREQ=DAILY;BYDAY=SU\r\nEXRULE:FREQ=DAILY;BYDAY=SA\r\n"
        )
        result = ical_to_jscal(ical)
        assert len(result["excludedRecurrenceRules"]) == 2

    def test_valarm_trigger_neither_duration_nor_datetime(self):
        from calendaring_jmap.convert.ical_to_jscal import _valarm_to_alert

        alarm = MagicMock()
        alarm.get.side_effect = lambda key, default=None: {
            "ACTION": "DISPLAY",
            "TRIGGER": MagicMock(dt="not a duration or datetime"),
            "DESCRIPTION": None,
        }.get(key, default)
        _, alert = _valarm_to_alert(alarm)
        assert "trigger" not in alert

    def test_categories_property_present_but_empty_omits_keywords_key(self, monkeypatch):
        import sys

        ical_to_jscal_module = sys.modules["calendaring_jmap.convert.ical_to_jscal"]
        monkeypatch.setattr(ical_to_jscal_module, "_categories_to_keywords", lambda prop: {})
        ical = _make_ical("DTSTART:20240615T100000Z\r\nSUMMARY:Empty Cats\r\nCATEGORIES:work\r\n")
        result = ical_to_jscal_module.ical_to_jscal(ical)
        assert "keywords" not in result

    def test_non_event_subcomponent_is_skipped(self):
        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//Test//EN\r\n"
            "BEGIN:VTIMEZONE\r\nTZID:Europe/Berlin\r\nEND:VTIMEZONE\r\n"
            "BEGIN:VEVENT\r\nUID:tz-skip@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
            "DTSTART:20240615T100000Z\r\nSUMMARY:With Timezone Component\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        result = ical_to_jscal(ical)
        assert result["title"] == "With Timezone Component"

    def test_second_master_vevent_without_recurrence_id_is_ignored(self):
        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\nUID:first@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
            "DTSTART:20240615T100000Z\r\nSUMMARY:First\r\n"
            "END:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:second@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
            "DTSTART:20240616T100000Z\r\nSUMMARY:Second\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        result = ical_to_jscal(ical)
        assert result["title"] == "First"

    def test_no_vevent_component_raises(self):
        ical = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//Test//EN\r\nEND:VCALENDAR\r\n"
        with pytest.raises(ValueError, match="No VEVENT component found"):
            ical_to_jscal(ical)

    def test_description(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Has Desc\r\nDESCRIPTION:Some notes here\r\n"
        )
        result = ical_to_jscal(ical)
        assert result["description"] == "Some notes here"

    def test_priority_zero_is_omitted(self):
        ical = _make_ical("DTSTART:20240615T100000Z\r\nSUMMARY:No Priority\r\nPRIORITY:0\r\n")
        result = ical_to_jscal(ical)
        assert "priority" not in result

    def test_class_unrecognized_value_omits_privacy(self):
        ical = _make_ical("DTSTART:20240615T100000Z\r\nSUMMARY:Public\r\nCLASS:PUBLIC\r\n")
        result = ical_to_jscal(ical)
        assert "privacy" not in result

    def test_status_unrecognized_value_omits_status(self):
        ical = _make_ical("DTSTART:20240615T100000Z\r\nSUMMARY:Draft\r\nSTATUS:X-DRAFT\r\n")
        result = ical_to_jscal(ical)
        assert "status" not in result

    def test_exrule_single_value_not_list(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nDURATION:PT1H\r\nSUMMARY:Excluded\r\n"
            "RRULE:FREQ=DAILY\r\nEXRULE:FREQ=DAILY;BYDAY=SU\r\n"
        )
        result = ical_to_jscal(ical)
        assert len(result["excludedRecurrenceRules"]) == 1
        assert result["excludedRecurrenceRules"][0]["frequency"] == "daily"

    def test_recurrence_override_with_no_changed_fields_is_empty_patch(self):
        # Override VEVENT carries only RECURRENCE-ID + SUMMARY matching the
        # master: no DTSTART/DURATION/DESCRIPTION override, so the patch has
        # nothing to record beyond the occurrence key itself.
        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\nUID:same-uid@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
            "DTSTART:20240617T140000Z\r\nDURATION:PT1H\r\nSUMMARY:Weekly\r\n"
            "RRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:same-uid@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
            "RECURRENCE-ID:20240624T140000Z\r\nSUMMARY:Weekly\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        result = ical_to_jscal(ical)
        override = next(iter(result["recurrenceOverrides"].values()))
        assert override == {}

    def test_recurrence_override_with_changed_start_and_description(self):
        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\nUID:full-uid@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
            "DTSTART:20240617T140000Z\r\nDURATION:PT1H\r\nSUMMARY:Weekly\r\n"
            "DESCRIPTION:Master notes\r\nRRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:full-uid@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
            "RECURRENCE-ID:20240624T140000Z\r\nDTSTART:20240624T160000Z\r\n"
            "DESCRIPTION:Override notes\r\nSUMMARY:Weekly\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        result = ical_to_jscal(ical)
        override = next(iter(result["recurrenceOverrides"].values()))
        assert override["start"] == "2024-06-24T16:00:00"
        assert override["description"] == "Override notes"
        assert "duration" not in override

    def test_recurrence_override_dtstart_matching_master_is_not_in_patch(self):
        # Override explicitly repeats a DTSTART that converts to the same
        # JSCalendar start string as the master: not a real change, so it
        # must not appear in the patch.
        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\nUID:samestart-uid@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
            "DTSTART:20240617T140000Z\r\nDURATION:PT1H\r\nSUMMARY:Weekly\r\n"
            "RRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:samestart-uid@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
            "RECURRENCE-ID:20240624T140000Z\r\nDTSTART:20240617T140000Z\r\n"
            "SUMMARY:Renamed\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        result = ical_to_jscal(ical)
        override = next(iter(result["recurrenceOverrides"].values()))
        assert "start" not in override
        assert override["title"] == "Renamed"

    def test_recurrence_override_duration_matching_master_is_not_in_patch(self):
        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\nUID:samedur-uid@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
            "DTSTART:20240617T140000Z\r\nDURATION:PT1H\r\nSUMMARY:Weekly\r\n"
            "RRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:samedur-uid@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
            "RECURRENCE-ID:20240624T140000Z\r\nDURATION:PT1H\r\n"
            "SUMMARY:Renamed\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        result = ical_to_jscal(ical)
        override = next(iter(result["recurrenceOverrides"].values()))
        assert "duration" not in override
        assert override["title"] == "Renamed"

    def test_recurrence_override_with_changed_duration(self):
        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\nUID:dur-uid@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
            "DTSTART:20240617T140000Z\r\nDURATION:PT1H\r\nSUMMARY:Weekly\r\n"
            "RRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:dur-uid@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
            "RECURRENCE-ID:20240624T140000Z\r\nDURATION:PT2H\r\n"
            "SUMMARY:Weekly\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        result = ical_to_jscal(ical)
        override = next(iter(result["recurrenceOverrides"].values()))
        assert override == {"duration": "PT2H"}

    def test_categories_bare_text_fallback(self):
        from icalendar.prop import vText

        from calendaring_jmap.convert.ical_to_jscal import _categories_to_keywords

        assert _categories_to_keywords(vText("work, standup")) == {"work": True, "standup": True}

    def test_categories_list_with_non_vcategory_item(self):
        from icalendar.prop import vText

        from calendaring_jmap.convert.ical_to_jscal import _categories_to_keywords

        assert _categories_to_keywords([vText("solo")]) == {"solo": True}


class TestJscalToIcal:
    def test_minimal_event(self):
        jscal = _minimal_jscal()
        result = jscal_to_ical(jscal)
        assert "BEGIN:VCALENDAR" in result
        assert "BEGIN:VEVENT" in result
        assert "SUMMARY:Test Event" in result
        assert "UID:test-uid@example.com" in result

    def test_all_day_event(self):
        jscal = _minimal_jscal(
            start="2024-06-15T00:00:00",
            showWithoutTime=True,
            duration="P1D",
        )
        del jscal["timeZone"]
        result = jscal_to_ical(jscal)
        assert "DTSTART;VALUE=DATE:20240615" in result

    def test_timezone_aware_event(self):
        jscal = _minimal_jscal(start="2024-06-15T10:00:00", timeZone="Europe/Berlin")
        result = jscal_to_ical(jscal)
        assert "DTSTART;TZID=Europe/Berlin:" in result

    def test_utc_event(self):
        jscal = _minimal_jscal(start="2024-06-15T10:00:00Z")
        del jscal["timeZone"]
        result = jscal_to_ical(jscal)
        assert "20240615T100000Z" in result

    def test_duration(self):
        jscal = _minimal_jscal(duration="PT2H30M")
        result = jscal_to_ical(jscal)
        assert "DURATION:PT2H30M" in result

    def test_keywords_to_categories(self):
        jscal = _minimal_jscal(keywords={"work": True, "standup": True})
        result = jscal_to_ical(jscal)
        assert "CATEGORIES" in result
        assert "work" in result or "standup" in result

    def test_location(self):
        jscal = _minimal_jscal(locations={"loc1": {"name": "Room A"}})
        result = jscal_to_ical(jscal)
        assert "LOCATION:Room A" in result

    def test_priority(self):
        jscal = _minimal_jscal(priority=5)
        result = jscal_to_ical(jscal)
        assert "PRIORITY:5" in result

    def test_privacy_private(self):
        jscal = _minimal_jscal(privacy="private")
        result = jscal_to_ical(jscal)
        assert "CLASS:PRIVATE" in result

    def test_privacy_secret(self):
        jscal = _minimal_jscal(privacy="secret")
        result = jscal_to_ical(jscal)
        assert "CLASS:CONFIDENTIAL" in result

    def test_free_busy_free(self):
        jscal = _minimal_jscal(freeBusyStatus="free")
        result = jscal_to_ical(jscal)
        assert "TRANSP:TRANSPARENT" in result

    def test_rrule(self):
        jscal = _minimal_jscal(
            recurrenceRules=[
                {
                    "@type": "RecurrenceRule",
                    "frequency": "weekly",
                    "interval": 1,
                    "byDay": [{"@type": "NDay", "day": "mo"}],
                    "rscale": "gregorian",
                    "skip": "omit",
                    "firstDayOfWeek": "mo",
                }
            ]
        )
        result = jscal_to_ical(jscal)
        assert "RRULE" in result
        assert "FREQ=WEEKLY" in result
        assert "BYDAY=MO" in result

    def test_exdate_from_overrides(self):
        jscal = _minimal_jscal(
            recurrenceRules=[{"frequency": "weekly", "@type": "RecurrenceRule"}],
            recurrenceOverrides={"2024-06-22T10:00:00": {"excluded": True}},
        )
        result = jscal_to_ical(jscal)
        assert "EXDATE" in result

    def test_alert_relative(self):
        jscal = _minimal_jscal(alerts={"al1": {"trigger": "-PT15M", "action": "display"}})
        result = jscal_to_ical(jscal)
        assert "BEGIN:VALARM" in result
        assert "TRIGGER:-PT15M" in result

    def test_alert_related_end(self):
        jscal = _minimal_jscal(
            alerts={"al1": {"trigger": "-PT5M", "action": "display", "relativeTo": "end"}}
        )
        result = jscal_to_ical(jscal)
        assert "RELATED=END" in result
        assert "-PT5M" in result

    def test_participants_organizer(self):
        jscal = _minimal_jscal(
            participants={
                "p1": {
                    "roles": {"owner": True, "organizer": True},
                    "name": "Alice",
                    "email": "alice@example.com",
                    "sendTo": {"imip": "mailto:alice@example.com"},
                }
            }
        )
        result = jscal_to_ical(jscal)
        assert "ORGANIZER" in result
        assert "alice@example.com" in result

    def test_sequence_emitted(self):
        result = jscal_to_ical(_minimal_jscal(sequence=5))
        assert "SEQUENCE:5" in result

    def test_color_emitted(self):
        result = jscal_to_ical(_minimal_jscal(color="blue"))
        assert "COLOR:blue" in result

    def test_start_non_iana_tzid_falls_back_to_raw_tzid_param(self):
        jscal = _minimal_jscal(start="2024-06-15T10:00:00", timeZone="Eastern Standard Time")
        result = jscal_to_ical(jscal)
        assert "TZID=Eastern Standard Time" in result

    def test_rrule_missing_frequency_omits_rrule(self):
        jscal = _minimal_jscal(recurrenceRules=[{"@type": "RecurrenceRule"}])
        result = jscal_to_ical(jscal)
        assert "RRULE" not in result

    def test_rrule_interval_emitted_when_not_one(self):
        jscal = _minimal_jscal(
            recurrenceRules=[{"@type": "RecurrenceRule", "frequency": "daily", "interval": 3}]
        )
        result = jscal_to_ical(jscal)
        assert "INTERVAL=3" in result

    def test_rrule_count_emitted(self):
        jscal = _minimal_jscal(
            recurrenceRules=[{"@type": "RecurrenceRule", "frequency": "daily", "count": 5}]
        )
        result = jscal_to_ical(jscal)
        assert "COUNT=5" in result

    def test_rrule_until_utc_z_suffix(self):
        jscal = _minimal_jscal(
            recurrenceRules=[
                {"@type": "RecurrenceRule", "frequency": "daily", "until": "2024-07-01T12:00:00Z"}
            ]
        )
        del jscal["timeZone"]
        result = jscal_to_ical(jscal)
        assert "UNTIL=20240701T120000Z" in result

    def test_rrule_until_no_time_zone_emits_floating(self):
        jscal = {
            "uid": "no-tz-until@example.com",
            "title": "No TZ",
            "start": "2024-06-15T10:00:00",
            "duration": "PT1H",
            "recurrenceRules": [
                {"@type": "RecurrenceRule", "frequency": "daily", "until": "2024-07-01T12:00:00"}
            ],
        }
        result = jscal_to_ical(jscal)
        assert "UNTIL=20240701T120000" in result
        assert "UNTIL=20240701T120000Z" not in result

    def test_rrule_until_non_iana_time_zone_passthrough(self):
        jscal = _minimal_jscal(
            timeZone="Eastern Standard Time",
            recurrenceRules=[
                {"@type": "RecurrenceRule", "frequency": "daily", "until": "2024-07-01T12:00:00"}
            ],
        )
        result = jscal_to_ical(jscal)
        assert "UNTIL=20240701T120000" in result

    def test_rrule_by_month_by_month_day_by_year_day_by_week_no(self):
        jscal = _minimal_jscal(
            recurrenceRules=[
                {
                    "@type": "RecurrenceRule",
                    "frequency": "yearly",
                    "byMonth": ["6"],
                    "byMonthDay": [15],
                    "byYearDay": [166],
                    "byWeekNo": [24],
                    "byHour": [14],
                    "byMinute": [30],
                    "bySecond": [15],
                    "bySetPosition": [1],
                }
            ]
        )
        result = jscal_to_ical(jscal)
        unfolded = result.replace("\r\n ", "").replace("\n ", "")
        assert "BYMONTH=6" in unfolded
        assert "BYMONTHDAY=15" in unfolded
        assert "BYYEARDAY=166" in unfolded
        assert "BYWEEKNO=24" in unfolded
        assert "BYHOUR=14" in unfolded
        assert "BYMINUTE=30" in unfolded
        assert "BYSECOND=15" in unfolded
        assert "BYSETPOS=1" in unfolded

    def test_rrule_first_day_of_week_non_default_emitted(self):
        jscal = _minimal_jscal(
            recurrenceRules=[
                {"@type": "RecurrenceRule", "frequency": "weekly", "firstDayOfWeek": "su"}
            ]
        )
        result = jscal_to_ical(jscal)
        assert "WKST=SU" in result

    def test_participant_imip_gets_mailto_prefix_when_missing(self):
        jscal = _minimal_jscal(
            participants={
                "p1": {
                    "roles": {"attendee": True},
                    "email": "bob@example.com",
                    "sendTo": {"other": "bob@example.com"},
                }
            }
        )
        result = jscal_to_ical(jscal)
        assert "mailto:bob@example.com" in result

    def test_organizer_without_name_has_no_cn(self):
        jscal = _minimal_jscal(
            participants={
                "p1": {
                    "roles": {"owner": True, "organizer": True},
                    "email": "alice@example.com",
                    "sendTo": {"imip": "mailto:alice@example.com"},
                }
            }
        )
        result = jscal_to_ical(jscal)
        assert "CN=" not in result

    def test_attendee_without_name_has_no_cn(self):
        jscal = _minimal_jscal(
            participants={
                "p1": {
                    "roles": {"attendee": True},
                    "email": "bob@example.com",
                    "sendTo": {"imip": "mailto:bob@example.com"},
                }
            }
        )
        result = jscal_to_ical(jscal)
        assert "CN=" not in result

    def test_attendee_without_partstat_defaults_needs_action(self):
        jscal = _minimal_jscal(
            participants={
                "p1": {
                    "roles": {"attendee": True},
                    "email": "bob@example.com",
                    "sendTo": {"imip": "mailto:bob@example.com"},
                }
            }
        )
        result = jscal_to_ical(jscal)
        assert "PARTSTAT=NEEDS-ACTION" in result

    def test_attendee_expect_reply_sets_rsvp(self):
        jscal = _minimal_jscal(
            participants={
                "p1": {
                    "roles": {"attendee": True},
                    "email": "bob@example.com",
                    "sendTo": {"imip": "mailto:bob@example.com"},
                    "expectReply": True,
                }
            }
        )
        result = jscal_to_ical(jscal)
        assert "RSVP=TRUE" in result

    def test_attendee_kind_sets_cutype(self):
        jscal = _minimal_jscal(
            participants={
                "p1": {
                    "roles": {"attendee": True},
                    "email": "room1@example.com",
                    "sendTo": {"imip": "mailto:room1@example.com"},
                    "kind": "room",
                }
            }
        )
        result = jscal_to_ical(jscal)
        assert "CUTYPE=ROOM" in result

    def test_attendee_chair_role_sets_role_chair(self):
        jscal = _minimal_jscal(
            participants={
                "p1": {
                    "roles": {"chair": True},
                    "email": "chair@example.com",
                    "sendTo": {"imip": "mailto:chair@example.com"},
                }
            }
        )
        result = jscal_to_ical(jscal)
        assert "ROLE=CHAIR" in result

    def test_participant_with_empty_roles_still_emitted_as_attendee(self):
        # A participant with no roles set is not purely an organizer/owner,
        # so _participant_to_attendee treats it as an attendee by default.
        jscal = _minimal_jscal(
            participants={
                "p1": {
                    "roles": {},
                    "email": "ghost@example.com",
                    "sendTo": {"imip": "mailto:ghost@example.com"},
                }
            }
        )
        result = jscal_to_ical(jscal)
        assert "ATTENDEE" in result
        assert "ghost@example.com" in result
        assert "ORGANIZER" not in result

    def test_rrule_byday_with_nth_of_period(self):
        jscal = _minimal_jscal(
            recurrenceRules=[
                {
                    "@type": "RecurrenceRule",
                    "frequency": "monthly",
                    "byDay": [{"@type": "NDay", "day": "mo", "nthOfPeriod": 2}],
                }
            ]
        )
        result = jscal_to_ical(jscal)
        assert "BYDAY=2MO" in result

    def test_alert_absolute_utc_trigger(self):
        jscal = _minimal_jscal(
            alerts={"al1": {"trigger": "2024-06-15T09:30:00Z", "action": "display"}}
        )
        result = jscal_to_ical(jscal)
        assert "TRIGGER:20240615T093000Z" in result

    def test_alert_malformed_absolute_trigger_falls_back_to_zero(self):
        jscal = _minimal_jscal(
            alerts={"al1": {"trigger": "2024-99-99T00:00:00Z", "action": "display"}}
        )
        result = jscal_to_ical(jscal)
        assert "TRIGGER" in result

    def test_alert_malformed_relative_trigger_falls_back_to_zero(self):
        jscal = _minimal_jscal(alerts={"al1": {"trigger": "not-a-duration", "action": "display"}})
        result = jscal_to_ical(jscal)
        assert "TRIGGER" in result

    def test_alert_missing_trigger_defaults_to_zero(self):
        jscal = _minimal_jscal(alerts={"al1": {"action": "display"}})
        result = jscal_to_ical(jscal)
        assert "TRIGGER" in result

    def test_alert_non_display_action_without_description_omits_reminder_text(self):
        jscal = _minimal_jscal(alerts={"al1": {"trigger": "-PT15M", "action": "email"}})
        result = jscal_to_ical(jscal)
        assert "DESCRIPTION:Reminder" not in result

    def test_locations_present_but_without_name_omits_location(self):
        jscal = _minimal_jscal(locations={"loc1": {}})
        result = jscal_to_ical(jscal)
        assert "LOCATION" not in result

    def test_missing_uid_omits_uid_line(self):
        jscal = {
            "title": "No UID",
            "start": "2024-06-15T10:00:00",
            "timeZone": "Europe/Berlin",
            "duration": "PT1H",
        }
        result = jscal_to_ical(jscal)
        assert "UID:" not in result

    def test_missing_start_omits_dtstart(self):
        jscal = {"uid": "no-start@example.com", "title": "No Start", "duration": "PT1H"}
        result = jscal_to_ical(jscal)
        assert "DTSTART" not in result

    def test_description_emitted(self):
        jscal = _minimal_jscal(description="Some notes")
        result = jscal_to_ical(jscal)
        assert "DESCRIPTION:Some notes" in result

    def test_privacy_unrecognized_value_omits_class(self):
        jscal = _minimal_jscal(privacy="public")
        result = jscal_to_ical(jscal)
        assert "CLASS" not in result

    def test_keywords_present_but_all_falsy_omits_categories(self):
        jscal = _minimal_jscal(keywords={"work": False})
        result = jscal_to_ical(jscal)
        assert "CATEGORIES" not in result

    def test_status_unrecognized_value_omits_status(self):
        jscal = _minimal_jscal(status="x-draft")
        result = jscal_to_ical(jscal)
        assert "STATUS" not in result

    def test_recurrence_rule_without_frequency_is_skipped(self):
        jscal = _minimal_jscal(
            recurrenceRules=[
                {"@type": "RecurrenceRule", "frequency": "weekly"},
                {"@type": "RecurrenceRule"},
            ]
        )
        result = jscal_to_ical(jscal)
        assert result.count("RRULE") == 1

    def test_recurrence_override_key_non_iana_time_zone_passthrough(self):
        jscal = _minimal_jscal(
            timeZone="Eastern Standard Time",
            recurrenceRules=[{"@type": "RecurrenceRule", "frequency": "weekly"}],
            recurrenceOverrides={"2024-06-22T10:00:00": {"title": "Moved"}},
        )
        result = jscal_to_ical(jscal)
        assert result.count("BEGIN:VEVENT") == 2
        assert "Moved" in result

    def test_recurrence_override_duration_defaults_to_master_duration(self):
        jscal = _minimal_jscal(
            start="2024-06-17T14:00:00Z",
            recurrenceRules=[{"@type": "RecurrenceRule", "frequency": "weekly"}],
            recurrenceOverrides={"2024-06-24T14:00:00Z": {"title": "Renamed Only"}},
        )
        del jscal["timeZone"]
        result = jscal_to_ical(jscal)
        assert result.count("DURATION:PT1H") == 2

    def test_recurrence_override_title_defaults_to_master_title(self):
        jscal = _minimal_jscal(
            start="2024-06-17T14:00:00Z",
            recurrenceRules=[{"@type": "RecurrenceRule", "frequency": "weekly"}],
            recurrenceOverrides={"2024-06-24T14:00:00Z": {"start": "2024-06-24T16:00:00Z"}},
        )
        del jscal["timeZone"]
        result = jscal_to_ical(jscal)
        assert result.count("SUMMARY:Test Event") == 2

    def test_excluded_recurrence_rule_without_frequency_is_skipped(self):
        jscal = _minimal_jscal(
            excludedRecurrenceRules=[
                {"@type": "RecurrenceRule", "frequency": "weekly"},
                {"@type": "RecurrenceRule"},
            ]
        )
        result = jscal_to_ical(jscal)
        assert result.count("EXRULE") == 1

    def test_recurrence_override_floating_key_no_timezone_no_allday(self):
        jscal = {
            "uid": "floating-override@example.com",
            "title": "Floating Master",
            "start": "2024-06-17T14:00:00",
            "duration": "PT1H",
            "recurrenceRules": [{"@type": "RecurrenceRule", "frequency": "weekly"}],
            "recurrenceOverrides": {"2024-06-24T14:00:00": {"title": "Floating Override"}},
        }
        result = jscal_to_ical(jscal)
        assert result.count("BEGIN:VEVENT") == 2
        assert "Floating Override" in result

    def test_recurrence_override_with_explicitly_empty_start_uses_no_dtstart_on_child(self):
        jscal = _minimal_jscal(
            start="2024-06-17T14:00:00Z",
            recurrenceRules=[{"@type": "RecurrenceRule", "frequency": "weekly"}],
            recurrenceOverrides={"2024-06-24T14:00:00Z": {"start": "", "title": "No Start Child"}},
        )
        del jscal["timeZone"]
        result = jscal_to_ical(jscal)
        events = result.split("BEGIN:VEVENT")
        child_block = events[2]
        assert "DTSTART" not in child_block

    def test_recurrence_override_with_p0d_duration_omits_child_duration(self):
        jscal = _minimal_jscal(
            start="2024-06-17T14:00:00Z",
            recurrenceRules=[{"@type": "RecurrenceRule", "frequency": "weekly"}],
            recurrenceOverrides={
                "2024-06-24T14:00:00Z": {"duration": "P0D", "title": "Zero Duration Child"}
            },
        )
        del jscal["timeZone"]
        result = jscal_to_ical(jscal)
        events = result.split("BEGIN:VEVENT")
        child_block = events[2]
        assert "DURATION" not in child_block

    def test_recurrence_override_with_explicitly_empty_title_omits_child_summary(self):
        jscal = {
            "uid": "no-title-master@example.com",
            "title": "",
            "start": "2024-06-17T14:00:00Z",
            "duration": "PT1H",
            "recurrenceRules": [{"@type": "RecurrenceRule", "frequency": "weekly"}],
            "recurrenceOverrides": {"2024-06-24T14:00:00Z": {"title": ""}},
        }
        result = jscal_to_ical(jscal)
        events = result.split("BEGIN:VEVENT")
        child_block = events[2]
        assert "SUMMARY" not in child_block

    def test_recurrence_override_description_defaults_to_master_description(self):
        jscal = _minimal_jscal(
            start="2024-06-17T14:00:00Z",
            description="Master notes",
            recurrenceRules=[{"@type": "RecurrenceRule", "frequency": "weekly"}],
            recurrenceOverrides={"2024-06-24T14:00:00Z": {"start": "2024-06-24T16:00:00Z"}},
        )
        del jscal["timeZone"]
        result = jscal_to_ical(jscal)
        assert result.count("DESCRIPTION:Master notes") == 2

    def test_exrule_from_excluded_recurrence_rules(self):
        jscal = _minimal_jscal(
            recurrenceRules=[{"@type": "RecurrenceRule", "frequency": "weekly"}],
            excludedRecurrenceRules=[
                {"@type": "RecurrenceRule", "frequency": "weekly", "byDay": [{"day": "mo"}]}
            ],
        )
        assert "EXRULE" in jscal_to_ical(jscal)

    def test_recurrence_override_patch_becomes_child_vevent(self):
        jscal = _minimal_jscal(
            start="2024-06-17T14:00:00Z",
            recurrenceRules=[{"@type": "RecurrenceRule", "frequency": "weekly"}],
            recurrenceOverrides={
                "2024-06-24T14:00:00Z": {"title": "Rescheduled", "start": "2024-06-24T16:00:00Z"}
            },
        )
        del jscal["timeZone"]
        result = jscal_to_ical(jscal)
        assert result.count("BEGIN:VEVENT") == 2
        assert "RECURRENCE-ID" in result
        assert "Rescheduled" in result

    def test_floating_datetime_emitted(self):
        jscal = {
            "uid": "float-uid@example.com",
            "title": "Floating",
            "start": "2024-06-15T10:00:00",
            "duration": "PT1H",
        }
        result = jscal_to_ical(jscal)
        assert "DTSTART:20240615T100000" in result
        assert "TZID" not in result


class TestRoundTrip:
    def _key_fields_survive(self, original_ical: str) -> dict:
        """ical → jscal → ical → parse back and check."""
        jscal = ical_to_jscal(original_ical)
        round_tripped = jscal_to_ical(jscal)
        cal = _icalendar.Calendar.from_ical(round_tripped)
        event = next(c for c in cal.subcomponents if isinstance(c, _icalendar.Event))
        return {"jscal": jscal, "ical": round_tripped, "event": event}

    def test_basic_event_round_trip(self):
        ical = _make_ical("DTSTART:20240615T100000Z\r\nDURATION:PT1H\r\nSUMMARY:Basic Event\r\n")
        ctx = self._key_fields_survive(ical)
        assert str(ctx["event"]["SUMMARY"]) == "Basic Event"
        assert ctx["jscal"]["title"] == "Basic Event"
        assert ctx["jscal"]["duration"] == "PT1H"

    def test_all_day_round_trip(self):
        ical = _make_ical(
            "DTSTART;VALUE=DATE:20240615\r\nDTEND;VALUE=DATE:20240616\r\nSUMMARY:All Day Event\r\n"
        )
        ctx = self._key_fields_survive(ical)
        assert ctx["jscal"]["showWithoutTime"] is True
        assert ctx["jscal"]["duration"] == "P1D"

    def test_recurring_event_round_trip(self):
        ical = _make_ical(
            "DTSTART;TZID=Europe/Berlin:20240617T140000\r\n"
            "DURATION:PT1H\r\n"
            "SUMMARY:Weekly\r\n"
            "RRULE:FREQ=WEEKLY;COUNT=4\r\n"
        )
        ctx = self._key_fields_survive(ical)
        assert "recurrenceRules" in ctx["jscal"]
        assert ctx["jscal"]["recurrenceRules"][0]["frequency"] == "weekly"
        assert "RRULE" in ctx["ical"]

    def test_with_alert_round_trip(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\n"
            "DURATION:PT1H\r\n"
            "SUMMARY:Alert Event\r\n"
            "BEGIN:VALARM\r\n"
            "ACTION:DISPLAY\r\n"
            "TRIGGER:-PT15M\r\n"
            "DESCRIPTION:Reminder\r\n"
            "END:VALARM\r\n"
        )
        ctx = self._key_fields_survive(ical)
        assert "alerts" in ctx["jscal"]
        alert = next(iter(ctx["jscal"]["alerts"].values()))
        assert alert["trigger"] == "-PT15M"
        assert "BEGIN:VALARM" in ctx["ical"]

    def test_with_attendees_round_trip(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\n"
            "DURATION:PT1H\r\n"
            "SUMMARY:Meeting\r\n"
            "ORGANIZER;CN=Alice:mailto:alice@example.com\r\n"
            "ATTENDEE;CN=Bob;PARTSTAT=ACCEPTED:mailto:bob@example.com\r\n"
        )
        ctx = self._key_fields_survive(ical)
        assert "participants" in ctx["jscal"]
        assert len(ctx["jscal"]["participants"]) >= 1
        assert "alice@example.com" in ctx["ical"] or "ORGANIZER" in ctx["ical"]


class TestJMAPClientEvents:
    _MINIMAL_ICAL = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:test-uid-123@example.com\r\n"
        "DTSTART:20240615T090000Z\r\n"
        "SUMMARY:Test Event\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )

    _RAW_EVENT = {
        "id": "ev1",
        "uid": "test-uid@example.com",
        "calendarIds": {"cal1": True},
        "title": "Staff Meeting",
        "start": "2024-06-15T09:00:00",
        "duration": "PT1H",
    }

    def _set_response(self, **kwargs):
        return {"methodResponses": [["CalendarEvent/set", kwargs, "ev-set-create-0"]]}

    def _get_response(self, items):
        return {
            "methodResponses": [
                [
                    "CalendarEvent/get",
                    {"accountId": _USERNAME, "list": items, "notFound": []},
                    "ev-get-0",
                ]
            ]
        }

    def test_create_event_returns_server_id(self, monkeypatch):
        resp = self._set_response(created={"new-0": {"id": "sv-1"}})
        client = _make_client_with_mocked_session(monkeypatch, resp)
        event_id = client.create_event("cal1", self._MINIMAL_ICAL)
        assert event_id == "sv-1"

    def test_create_event_raises_on_failure(self, monkeypatch):
        resp = self._set_response(
            notCreated={"new-0": {"type": "invalidArguments", "description": "bad"}}
        )
        client = _make_client_with_mocked_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            client.create_event("cal1", self._MINIMAL_ICAL)
        assert exc_info.value.error_type == "invalidArguments"

    def test_create_event_raises_on_malformed_response(self, monkeypatch):
        resp = self._set_response(created={}, notCreated={})
        client = _make_client_with_mocked_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError):
            client.create_event("cal1", self._MINIMAL_ICAL)

    def test_create_event_passes_calendar_id(self, monkeypatch):
        resp = self._set_response(created={"new-0": {"id": "sv-2"}})
        client, captured = self._capturing_client(monkeypatch, resp)
        client.create_event("my-calendar", self._MINIMAL_ICAL)

        method_calls = captured["json"]["methodCalls"]
        create_args = method_calls[0][1]
        event_payload = create_args["create"]["new-0"]
        assert event_payload.get("calendarIds") == {"my-calendar": True}

    def test_get_event_returns_ical(self, monkeypatch):
        raw_event = {
            "id": "ev1",
            "uid": "test-uid@example.com",
            "calendarIds": {"cal1": True},
            "title": "Staff Meeting",
            "start": "2024-06-15T09:00:00Z",
            "duration": "PT1H",
        }
        client = _make_client_with_mocked_session(monkeypatch, self._get_response([raw_event]))
        result = client.get_event("ev1")
        assert isinstance(result, JMAPCalendarObject)
        assert result.id == "ev1"
        assert result.get_data()["title"] == "Staff Meeting"
        assert result.parent is None

    def test_get_event_raises_on_not_found(self, monkeypatch):
        client = _make_client_with_mocked_session(monkeypatch, self._get_response([]))
        with pytest.raises(JMAPMethodError) as exc_info:
            client.get_event("missing-id")
        assert exc_info.value.error_type == "notFound"

    def test_update_event_success(self, monkeypatch):
        resp = self._set_response(updated={"ev1": None})
        client = _make_client_with_mocked_session(monkeypatch, resp)
        client.update_event("ev1", self._MINIMAL_ICAL)

    def test_update_event_raises_on_failure(self, monkeypatch):
        resp = self._set_response(notUpdated={"ev1": {"type": "notFound"}})
        client = _make_client_with_mocked_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            client.update_event("ev1", self._MINIMAL_ICAL)
        assert exc_info.value.error_type == "notFound"

    def test_update_event_drops_uid_from_patch(self, monkeypatch):
        resp = self._set_response(updated={"ev1": None})
        client, captured = self._capturing_client(monkeypatch, resp)
        client.update_event("ev1", self._MINIMAL_ICAL)

        method_calls = captured["json"]["methodCalls"]
        update_args = method_calls[0][1]
        patch = update_args["update"]["ev1"]
        assert "uid" not in patch

    def test_update_event_nulls_removed_optional_properties(self, monkeypatch):
        # RFC 8620 §3.3: absent keys in a PatchObject preserve the server value.
        # To actually delete a property the patch must set it to null.
        # An ical → jscal conversion that omits LOCATION/DESCRIPTION must send
        # {"locations": null, "description": null, ...} so the server removes them.
        _ICAL_WITHOUT_LOCATION = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:loc-uid@example.com\r\n"
            "DTSTART:20240615T090000Z\r\n"
            "SUMMARY:Event without Location\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        resp = self._set_response(updated={"ev1": None})
        client, captured = self._capturing_client(monkeypatch, resp)
        # First, pretend the event had a location (we don't need to call create; just update)
        client.update_event("ev1", _ICAL_WITHOUT_LOCATION)
        patch = captured["json"]["methodCalls"][0][1]["update"]["ev1"]
        # The patch must contain explicit null for 'locations' to remove it from the server
        assert "locations" in patch
        assert patch["locations"] is None

    def _sequence_client(self, responses):
        """Return (client, captured) replaying ``responses`` POST-by-POST.

        ``captured["patches"]`` collects the ``update`` patch dict sent on each
        CalendarEvent/set POST, in order.
        """
        captured: dict = {"patches": []}
        client = JMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        client._session_cache = Session(api_url=_API_URL, account_id=_USERNAME, state="state-abc")
        seq = iter(responses)

        def post(*args, **kwargs):
            body = kwargs.get("json", {})
            update = body["methodCalls"][0][1].get("update")
            if update:
                captured["patches"].append(dict(update["ev1"]))  # copy: caller mutates in place
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = next(seq)
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_http = MagicMock()
        mock_http.post.side_effect = post
        client._http_session = mock_http
        return client, captured

    def test_update_event_retries_dropping_server_rejected_null_keys(self, monkeypatch):
        # A server (e.g. Stalwart) rejects null-clearing of recurrence properties
        # it does not support, reporting one offending property per response.
        # update_event must drop each reported null-cleanup key and retry until
        # the update succeeds — never failing on harmless cleanup.
        def reject(prop):
            return self._set_response(
                notUpdated={
                    "ev1": {
                        "type": "invalidProperties",
                        "description": "Invalid property.",
                        "properties": [prop],
                    }
                }
            )

        responses = [
            reject("recurrenceRules"),
            reject("excludedRecurrenceRules"),
            self._set_response(updated={"ev1": None}),
        ]
        client, captured = self._sequence_client(responses)
        client.update_event("ev1", self._MINIMAL_ICAL)

        assert len(captured["patches"]) == 3
        # First attempt nulled both recurrence keys; the final accepted patch dropped them.
        assert captured["patches"][0]["recurrenceRules"] is None
        assert "recurrenceRules" not in captured["patches"][2]
        assert "excludedRecurrenceRules" not in captured["patches"][2]

    def test_update_event_does_not_drop_explicitly_set_property(self, monkeypatch):
        # If the rejected property was actually assigned a value by the client
        # (not null-cleanup), the rejection is genuine and must surface — no retry.
        resp = self._set_response(
            notUpdated={
                "ev1": {
                    "type": "invalidProperties",
                    "description": "Invalid property.",
                    "properties": ["title"],
                }
            }
        )
        client, captured = self._sequence_client([resp])
        with pytest.raises(JMAPMethodError) as exc_info:
            client.update_event("ev1", self._MINIMAL_ICAL)
        assert exc_info.value.error_type == "invalidProperties"
        assert len(captured["patches"]) == 1  # no retry

    def test_delete_event_success(self, monkeypatch):
        resp = self._set_response(destroyed=["ev1"])
        client = _make_client_with_mocked_session(monkeypatch, resp)
        client.delete_event("ev1")

    def test_delete_event_raises_on_failure(self, monkeypatch):
        resp = self._set_response(notDestroyed={"ev1": {"type": "notFound"}})
        client = _make_client_with_mocked_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            client.delete_event("ev1")
        assert exc_info.value.error_type == "notFound"

    def _capturing_client(self, monkeypatch, resp):
        """Return (client, captured) where captured["json"] is set on each POST."""
        captured = {}
        client = JMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        client._session_cache = Session(api_url=_API_URL, account_id=_USERNAME, state="state-abc")

        def capturing_post(*args, **kwargs):
            captured["json"] = kwargs.get("json", {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = resp
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_http = MagicMock()
        mock_http.post.side_effect = capturing_post
        client._http_session = mock_http
        return client, captured

    def _query_get_response(self, items):
        return {
            "methodResponses": [
                [
                    "CalendarEvent/query",
                    {"ids": [i["id"] for i in items], "queryState": "qs-1", "total": len(items)},
                    "ev-query-0",
                ],
                [
                    "CalendarEvent/get",
                    {"accountId": _USERNAME, "list": items, "notFound": []},
                    "ev-get-1",
                ],
            ]
        }

    def test_search_events_returns_ical_list(self, monkeypatch):
        event2 = {**self._RAW_EVENT, "id": "ev2", "title": "Standup"}
        resp = self._query_get_response([self._RAW_EVENT, event2])
        client = _make_client_with_mocked_session(monkeypatch, resp)
        results = client.search_events()
        assert len(results) == 2
        assert all(isinstance(r, JMAPCalendarObject) for r in results)
        assert all(r.parent is None for r in results)

    def test_search_events_empty_result(self, monkeypatch):
        resp = self._query_get_response([])
        client = _make_client_with_mocked_session(monkeypatch, resp)
        assert client.search_events() == []

    def test_search_events_passes_calendar_id_filter(self, monkeypatch):
        resp = self._query_get_response([self._RAW_EVENT])
        client, captured = self._capturing_client(monkeypatch, resp)
        client.search_events(calendar_id="my-cal")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["inCalendars"] == ["my-cal"]

    def test_search_events_passes_date_range_filter(self, monkeypatch):
        resp = self._query_get_response([self._RAW_EVENT])
        client, captured = self._capturing_client(monkeypatch, resp)
        client.search_events(start="2024-01-01T00:00:00", end="2024-12-31T23:59:59")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["after"] == "2024-01-01T00:00:00"
        assert query_args["filter"]["before"] == "2024-12-31T23:59:59"

    def test_search_events_passes_text_filter(self, monkeypatch):
        resp = self._query_get_response([self._RAW_EVENT])
        client, captured = self._capturing_client(monkeypatch, resp)
        client.search_events(text="standup")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["text"] == "standup"

    def test_search_events_no_filter_when_no_args(self, monkeypatch):
        resp = self._query_get_response([self._RAW_EVENT])
        client, captured = self._capturing_client(monkeypatch, resp)
        client.search_events()
        query_args = captured["json"]["methodCalls"][0][1]
        assert "filter" not in query_args


class _MockedClientMixin:
    """Shared client/response mocking for tests that drive JMAPClient
    through a mocked ``_http_session`` rather than real HTTP calls."""

    def _make_mock(self, resp_json):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = resp_json
        m.raise_for_status = MagicMock()
        return m

    def _make_client(self):
        client = JMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        client._session_cache = Session(api_url=_API_URL, account_id=_USERNAME, state="state-abc")
        return client

    def _mock_http(self, client, response=None, side_effect=None):
        mock_http = MagicMock()
        if side_effect is not None:
            mock_http.post.side_effect = side_effect
        elif response is not None:
            mock_http.post.return_value = response
        client._http_session = mock_http
        return mock_http


class TestJMAPClientSync(_MockedClientMixin):
    _RAW_EVENT = {
        "id": "ev1",
        "uid": "test-uid@example.com",
        "calendarIds": {"cal1": True},
        "title": "Staff Meeting",
        "start": "2026-01-15T09:00:00",
        "duration": "PT1H",
    }

    def _changes_resp(
        self,
        created=None,
        updated=None,
        destroyed=None,
        old_state="state-1",
        new_state="state-2",
        has_more=False,
    ):
        return {
            "methodResponses": [
                [
                    "CalendarEvent/changes",
                    {
                        "accountId": _USERNAME,
                        "oldState": old_state,
                        "newState": new_state,
                        "hasMoreChanges": has_more,
                        "created": created or [],
                        "updated": updated or [],
                        "destroyed": destroyed or [],
                    },
                    "ev-changes-0",
                ]
            ]
        }

    def _get_resp_with_state(self, items, state="state-2"):
        return {
            "methodResponses": [
                [
                    "CalendarEvent/get",
                    {"accountId": _USERNAME, "state": state, "list": items, "notFound": []},
                    "ev-get-0",
                ]
            ]
        }

    def test_get_sync_token_returns_state(self):
        resp = self._get_resp_with_state([], state="tok-1")
        client = self._make_client()
        self._mock_http(client, self._make_mock(resp))
        assert client.get_sync_token() == "tok-1"

    def test_get_sync_token_sends_empty_ids(self):
        captured = {}
        resp = self._get_resp_with_state([])

        def capturing_post(*args, **kwargs):
            captured["json"] = kwargs.get("json", {})
            return self._make_mock(resp)

        client = self._make_client()
        self._mock_http(client, side_effect=capturing_post)
        client.get_sync_token()
        assert captured["json"]["methodCalls"][0][1]["ids"] == []

    def test_get_objects_no_changes(self):
        resp = self._changes_resp()
        client = self._make_client()
        self._mock_http(client, self._make_mock(resp))
        added, modified, deleted, _ = client.get_objects_by_sync_token("state-1")
        assert added == [] and modified == [] and deleted == []

    def test_get_objects_deleted_returns_ids(self):
        resp = self._changes_resp(destroyed=["ev1"])
        client = self._make_client()
        self._mock_http(client, self._make_mock(resp))
        added, modified, deleted, _ = client.get_objects_by_sync_token("state-1")
        assert deleted == ["ev1"] and added == [] and modified == []

    def test_get_objects_added_returns_ical(self):
        changes_resp = self._changes_resp(created=["ev1"])
        get_resp = self._get_resp_with_state([self._RAW_EVENT])
        client = self._make_client()
        self._mock_http(
            client,
            side_effect=[self._make_mock(changes_resp), self._make_mock(get_resp)],
        )
        added, modified, deleted, _ = client.get_objects_by_sync_token("state-1")
        assert len(added) == 1
        assert isinstance(added[0], JMAPCalendarObject)
        assert added[0].id == "ev1"
        assert modified == [] and deleted == []

    def test_get_objects_modified_returns_ical(self):
        changes_resp = self._changes_resp(updated=["ev1"])
        get_resp = self._get_resp_with_state([self._RAW_EVENT])
        client = self._make_client()
        self._mock_http(
            client,
            side_effect=[self._make_mock(changes_resp), self._make_mock(get_resp)],
        )
        added, modified, deleted, _ = client.get_objects_by_sync_token("state-1")
        assert len(modified) == 1
        assert isinstance(modified[0], JMAPCalendarObject)
        assert modified[0].id == "ev1"
        assert added == [] and deleted == []

    def test_get_objects_has_more_raises(self):
        resp = self._changes_resp(created=["ev1"], has_more=True)
        client = self._make_client()
        self._mock_http(client, self._make_mock(resp))
        with pytest.raises(JMAPMethodError) as exc_info:
            client.get_objects_by_sync_token("state-1")
        assert exc_info.value.error_type == "serverPartialFail"

    def test_get_objects_returns_new_sync_token(self):
        """§4.7: newState from /changes was discarded into _.  Callers had no
        way to chain sync calls without a separate get_sync_token() round-trip,
        creating a race window where intervening changes would be silently missed."""
        resp = self._changes_resp(new_state="state-99")
        client = self._make_client()
        self._mock_http(client, self._make_mock(resp))
        result = client.get_objects_by_sync_token("state-1")
        assert len(result) == 4, "expected 4-tuple (added, modified, deleted, new_sync_token)"
        added, modified, deleted, new_token = result
        assert new_token == "state-99"
        assert added == [] and modified == [] and deleted == []

    def test_parse_event_changes_all_fields(self):
        resp_args = {
            "oldState": "s1",
            "newState": "s2",
            "hasMoreChanges": True,
            "created": ["ev1"],
            "updated": ["ev2"],
            "destroyed": ["ev3"],
        }
        old, new, has_more, created, updated, destroyed = parse_event_changes(resp_args)
        assert old == "s1"
        assert new == "s2"
        assert has_more is True
        assert created == ["ev1"]
        assert updated == ["ev2"]
        assert destroyed == ["ev3"]


class TestTaskMethodBuilders:
    def test_build_task_list_get_structure(self):
        method, args, call_id = build_task_list_get("u1")
        assert method == "TaskList/get"
        assert args["accountId"] == "u1"
        assert args["ids"] is None
        assert call_id == "tasklist-get-0"

    def test_build_task_get_structure(self):
        method, args, call_id = build_task_get("u1")
        assert method == "Task/get"
        assert args["accountId"] == "u1"
        assert args["ids"] is None
        assert call_id == "task-get-0"

    def test_build_task_get_with_ids(self):
        _, args, _ = build_task_get("u1", ids=["t1", "t2"])
        assert args["ids"] == ["t1", "t2"]

    def test_build_task_get_with_properties(self):
        _, args, _ = build_task_get("u1", properties=["id", "title"])
        assert args["properties"] == ["id", "title"]

    def test_build_task_list_get_with_properties(self):
        _, args, _ = build_task_list_get("u1", properties=["id", "name"])
        assert args["properties"] == ["id", "name"]

    def test_build_task_set_create_structure(self):
        task = {"@type": "Task", "uid": "uid-1", "taskListId": "tl1", "title": "Buy milk"}
        method, args, call_id = build_task_set_create("acct1", {"new-0": task})
        assert method == "Task/set"
        assert "create" in args
        assert "@type" in args["create"]["new-0"]
        assert call_id == "task-set-create-0"

    def test_build_task_set_update_structure(self):
        method, args, call_id = build_task_set_update("acct1", {"t1": {"title": "New"}})
        assert method == "Task/set"
        assert args["update"] == {"t1": {"title": "New"}}
        assert call_id == "task-set-update-0"

    def test_build_task_set_destroy_structure(self):
        method, args, call_id = build_task_set_destroy("acct1", ["t1"])
        assert method == "Task/set"
        assert args["destroy"] == ["t1"]
        assert call_id == "task-set-destroy-0"

    def test_parse_task_list_get_returns_tasklists(self):
        resp_args = {"list": [{"id": "tl1", "name": "Work"}, {"id": "tl2", "name": "Home"}]}
        results = parse_task_list_get(resp_args)
        assert len(results) == 2
        assert all(isinstance(r, dict) for r in results)
        assert results[0]["name"] == "Work"

    def test_parse_task_get_returns_tasks(self):
        resp_args = {
            "list": [
                {"id": "t1", "uid": "uid-1", "taskListId": "tl1", "title": "Buy milk"},
                {"id": "t2", "uid": "uid-2", "taskListId": "tl1", "title": "Call dentist"},
            ]
        }
        results = parse_task_get(resp_args)
        assert len(results) == 2
        assert all(isinstance(r, dict) for r in results)
        assert results[0]["title"] == "Buy milk"

    def test_parse_task_set_all_fields(self):
        resp_args = {
            "created": {"new-0": {"id": "t1"}},
            "updated": {"t2": None},
            "destroyed": ["t3"],
            "notCreated": {"new-1": {"type": "invalidArguments"}},
            "notUpdated": {},
            "notDestroyed": {},
        }
        created, updated, destroyed, not_created, not_updated, not_destroyed = parse_task_set(
            resp_args
        )
        assert created == {"new-0": {"id": "t1"}}
        assert destroyed == ["t3"]
        assert not_created == {"new-1": {"type": "invalidArguments"}}


class TestJMAPClientTasks(_MockedClientMixin):
    _MINIMAL_TASK = {
        "id": "task1",
        "uid": "uid-task-1@example.com",
        "taskListId": "tl1",
        "title": "Buy groceries",
        "percentComplete": 0,
        "progress": "needs-action",
        "priority": 0,
    }

    _MINIMAL_TASKLIST = {
        "id": "tl1",
        "name": "My Tasks",
    }

    def _set_response(self, **kwargs):
        return {"methodResponses": [["Task/set", kwargs, "task-set-create-0"]]}

    def _get_response(self, items):
        return {
            "methodResponses": [
                [
                    "Task/get",
                    {"accountId": _USERNAME, "list": items, "notFound": []},
                    "task-get-0",
                ]
            ]
        }

    def _tasklist_response(self, items):
        return {
            "methodResponses": [
                [
                    "TaskList/get",
                    {"accountId": _USERNAME, "list": items, "notFound": []},
                    "tasklist-get-0",
                ]
            ]
        }

    def test_get_task_lists_returns_list(self):
        resp = self._tasklist_response([self._MINIMAL_TASKLIST])
        client = self._make_client()
        self._mock_http(client, self._make_mock(resp))
        result = client.get_task_lists()
        assert len(result) == 1
        assert isinstance(result[0], dict)
        assert result[0]["name"] == "My Tasks"

    def test_create_task_returns_server_id(self):
        resp = self._set_response(created={"new-0": {"id": "sv-task-1"}})
        client = self._make_client()
        self._mock_http(client, self._make_mock(resp))
        task_id = client.create_task("tl1", "Buy groceries")
        assert task_id == "sv-task-1"

    def test_create_task_passes_task_list_id(self):
        captured = {}
        resp = self._set_response(created={"new-0": {"id": "sv-task-1"}})

        def capturing_post(*args, **kwargs):
            captured["json"] = kwargs.get("json", {})
            return self._make_mock(resp)

        client = self._make_client()
        self._mock_http(client, side_effect=capturing_post)
        client.create_task("my-list", "Test Task")
        create_args = captured["json"]["methodCalls"][0][1]
        assert create_args["create"]["new-0"]["taskListId"] == "my-list"

    def test_create_task_raises_on_failure(self):
        resp = self._set_response(notCreated={"new-0": {"type": "invalidArguments"}})
        client = self._make_client()
        self._mock_http(client, self._make_mock(resp))
        with pytest.raises(JMAPMethodError) as exc_info:
            client.create_task("tl1", "Test")
        assert exc_info.value.error_type == "invalidArguments"

    def test_create_task_raises_jmap_error_when_created_is_empty(self):
        """§1.13: create_task must raise JMAPMethodError (not KeyError) when the server
        returns a Task/set response with an empty 'created' dict and no 'notCreated' entry."""
        resp = self._set_response(created={}, notCreated={})
        client = self._make_client()
        self._mock_http(client, self._make_mock(resp))
        with pytest.raises(JMAPMethodError):
            client.create_task("tl1", "Test")

    def test_get_task_returns_task_object(self):
        resp = self._get_response([self._MINIMAL_TASK])
        client = self._make_client()
        self._mock_http(client, self._make_mock(resp))
        task = client.get_task("task1")
        assert isinstance(task, dict)
        assert task["id"] == "task1"

    def test_get_task_raises_on_not_found(self):
        resp = self._get_response([])
        client = self._make_client()
        self._mock_http(client, self._make_mock(resp))
        with pytest.raises(JMAPMethodError) as exc_info:
            client.get_task("missing")
        assert exc_info.value.error_type == "notFound"

    def test_update_task_success(self):
        resp = self._set_response(updated={"task1": None})
        client = self._make_client()
        self._mock_http(client, self._make_mock(resp))
        client.update_task("task1", {"title": "Updated"})

    def test_update_task_raises_on_failure(self):
        resp = self._set_response(notUpdated={"task1": {"type": "notFound"}})
        client = self._make_client()
        self._mock_http(client, self._make_mock(resp))
        with pytest.raises(JMAPMethodError) as exc_info:
            client.update_task("task1", {"title": "X"})
        assert exc_info.value.error_type == "notFound"

    def test_delete_task_success(self):
        resp = self._set_response(destroyed=["task1"])
        client = self._make_client()
        self._mock_http(client, self._make_mock(resp))
        client.delete_task("task1")

    def test_delete_task_raises_on_failure(self):
        resp = self._set_response(notDestroyed={"task1": {"type": "notFound"}})
        client = self._make_client()
        self._mock_http(client, self._make_mock(resp))
        with pytest.raises(JMAPMethodError) as exc_info:
            client.delete_task("task1")
        assert exc_info.value.error_type == "notFound"

    def test_task_requests_use_task_capability(self):
        captured = {}
        resp = self._tasklist_response([self._MINIMAL_TASKLIST])

        def capturing_post(*args, **kwargs):
            captured["json"] = kwargs.get("json", {})
            return self._make_mock(resp)

        client = self._make_client()
        self._mock_http(client, side_effect=capturing_post)
        client.get_task_lists()
        assert TASK_CAPABILITY in captured["json"]["using"]
        assert CALENDAR_CAPABILITY not in captured["json"]["using"]


from calendaring_jmap.async_client import AsyncJMAPClient


class TestAsyncJMAPClient:
    _MINIMAL_ICAL = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "BEGIN:VEVENT",
            "UID:async-test-uid@example.com",
            "SUMMARY:Async Test Event",
            "DTSTART:20260101T100000Z",
            "DTEND:20260101T110000Z",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )

    _RAW_EVENT = {
        "id": "ev-async-1",
        "uid": "async-test-uid@example.com",
        "calendarIds": {"cal1": True},
        "title": "Async Test Event",
        "start": "2026-01-01T10:00:00",
        "duration": "PT1H",
    }

    _MINIMAL_TASK = {
        "id": "task-async-1",
        "uid": "uid-async-task@example.com",
        "taskListId": "tl1",
        "title": "Async Task",
        "percentComplete": 0,
        "progress": "needs-action",
        "priority": 0,
    }

    _MINIMAL_TASKLIST = {"id": "tl1", "name": "Async Tasks"}

    def _make_client(self):
        client = AsyncJMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        client._session_cache = Session(api_url=_API_URL, account_id=_USERNAME, state="state-async")
        return client

    def _make_mock_response(self, resp_json):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = resp_json
        m.raise_for_status = MagicMock()
        return m

    def _patch_async_session(self, monkeypatch, resp_json):
        mock_resp = self._make_mock_response(resp_json)
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.post = AsyncMock(return_value=mock_resp)
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        return mock_http

    def _calendar_get_resp(self, items):
        return {
            "methodResponses": [
                [
                    "Calendar/get",
                    {"accountId": _USERNAME, "list": items, "notFound": []},
                    "cal-get-0",
                ]
            ]
        }

    def _event_set_resp(self, **kwargs):
        return {"methodResponses": [["CalendarEvent/set", kwargs, "ev-set-0"]]}

    def _event_get_resp(self, items):
        return {
            "methodResponses": [
                [
                    "CalendarEvent/get",
                    {"accountId": _USERNAME, "list": items, "notFound": []},
                    "ev-get-0",
                ]
            ]
        }

    def _query_get_resp(self, items):
        return {
            "methodResponses": [
                [
                    "CalendarEvent/query",
                    {"ids": [i["id"] for i in items], "queryState": "qs-1", "total": len(items)},
                    "ev-query-0",
                ],
                [
                    "CalendarEvent/get",
                    {"accountId": _USERNAME, "list": items, "notFound": []},
                    "ev-get-1",
                ],
            ]
        }

    def _changes_resp(self, created=None, updated=None, destroyed=None, has_more=False):
        return {
            "methodResponses": [
                [
                    "CalendarEvent/changes",
                    {
                        "accountId": _USERNAME,
                        "oldState": "state-1",
                        "newState": "state-2",
                        "hasMoreChanges": has_more,
                        "created": created or [],
                        "updated": updated or [],
                        "destroyed": destroyed or [],
                    },
                    "ev-changes-0",
                ]
            ]
        }

    def _task_set_resp(self, **kwargs):
        return {"methodResponses": [["Task/set", kwargs, "task-set-0"]]}

    def _task_get_resp(self, items):
        return {
            "methodResponses": [
                [
                    "Task/get",
                    {"accountId": _USERNAME, "list": items, "notFound": []},
                    "task-get-0",
                ]
            ]
        }

    def _tasklist_resp(self, items):
        return {
            "methodResponses": [
                [
                    "TaskList/get",
                    {"accountId": _USERNAME, "list": items, "notFound": []},
                    "tasklist-get-0",
                ]
            ]
        }

    @pytest.mark.asyncio
    async def test_context_manager(self):
        async with AsyncJMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD) as client:
            assert isinstance(client, AsyncJMAPClient)

    @pytest.mark.asyncio
    async def test_context_manager_closes_http_session(self, monkeypatch):
        mock_close = AsyncMock()
        mock_http = MagicMock()
        mock_http.close = mock_close
        mock_http.headers = MagicMock()
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        client = AsyncJMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        async with client:
            assert client._http_session is mock_http
        mock_close.assert_called_once()
        assert client._http_session is None

    @pytest.mark.asyncio
    async def test_http_session_reused_across_requests(self, monkeypatch):
        client = self._make_client()
        mock_resp = self._make_mock_response({"methodResponses": []})
        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http.headers = MagicMock()
        with patch("calendaring_jmap.async_client.AsyncSession") as MockAsyncSession:
            MockAsyncSession.return_value = mock_http
            await client._request([("Calendar/get", {}, "c0")])
            await client._request([("Calendar/get", {}, "c1")])
        MockAsyncSession.assert_called_once()
        assert mock_http.post.call_count == 2

    @pytest.mark.asyncio
    async def test_get_calendars_returns_list(self, monkeypatch):
        cal = {"id": "cal1", "name": "Personal", "isSubscribed": True, "myRights": {}}
        self._patch_async_session(monkeypatch, self._calendar_get_resp([cal]))
        result = await self._make_client().get_calendars()
        assert len(result) == 1
        assert isinstance(result[0], JMAPCalendar)
        assert result[0].name == "Personal"

    @pytest.mark.asyncio
    async def test_create_event_returns_id(self, monkeypatch):
        resp = self._event_set_resp(created={"new-0": {"id": "ev-new-1"}}, notCreated={})
        self._patch_async_session(monkeypatch, resp)
        event_id = await self._make_client().create_event("cal1", self._MINIMAL_ICAL)
        assert event_id == "ev-new-1"

    @pytest.mark.asyncio
    async def test_create_event_raises_on_failure(self, monkeypatch):
        resp = self._event_set_resp(created={}, notCreated={"new-0": {"type": "invalidArguments"}})
        self._patch_async_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client().create_event("cal1", self._MINIMAL_ICAL)
        assert exc_info.value.error_type == "invalidArguments"

    @pytest.mark.asyncio
    async def test_get_event_returns_ical(self, monkeypatch):
        self._patch_async_session(monkeypatch, self._event_get_resp([self._RAW_EVENT]))
        result = await self._make_client().get_event("ev-async-1")
        assert isinstance(result, JMAPCalendarObject)
        assert result.id == "ev-async-1"
        assert result.get_data()["title"] == "Async Test Event"
        assert result.parent is None

    @pytest.mark.asyncio
    async def test_get_event_raises_on_not_found(self, monkeypatch):
        self._patch_async_session(monkeypatch, self._event_get_resp([]))
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client().get_event("missing")
        assert exc_info.value.error_type == "notFound"

    @pytest.mark.asyncio
    async def test_update_event_success(self, monkeypatch):
        resp = self._event_set_resp(updated={"ev-async-1": None}, notUpdated={})
        self._patch_async_session(monkeypatch, resp)
        await self._make_client().update_event("ev-async-1", self._MINIMAL_ICAL)

    @pytest.mark.asyncio
    async def test_update_event_raises_on_failure(self, monkeypatch):
        resp = self._event_set_resp(updated={}, notUpdated={"ev-async-1": {"type": "notFound"}})
        self._patch_async_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client().update_event("ev-async-1", self._MINIMAL_ICAL)
        assert exc_info.value.error_type == "notFound"

    @pytest.mark.asyncio
    async def test_delete_event_success(self, monkeypatch):
        resp = self._event_set_resp(destroyed=["ev-async-1"], notDestroyed={})
        self._patch_async_session(monkeypatch, resp)
        await self._make_client().delete_event("ev-async-1")

    @pytest.mark.asyncio
    async def test_delete_event_raises_on_failure(self, monkeypatch):
        resp = self._event_set_resp(destroyed=[], notDestroyed={"ev-async-1": {"type": "notFound"}})
        self._patch_async_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client().delete_event("ev-async-1")
        assert exc_info.value.error_type == "notFound"

    @pytest.mark.asyncio
    async def test_search_events_returns_ical_list(self, monkeypatch):
        event2 = {**self._RAW_EVENT, "id": "ev-async-2", "title": "Another"}
        self._patch_async_session(monkeypatch, self._query_get_resp([self._RAW_EVENT, event2]))
        results = await self._make_client().search_events()
        assert len(results) == 2
        assert all(isinstance(r, JMAPCalendarObject) for r in results)
        assert all(r.parent is None for r in results)

    @pytest.mark.asyncio
    async def test_search_events_empty_result(self, monkeypatch):
        self._patch_async_session(monkeypatch, self._query_get_resp([]))
        assert await self._make_client().search_events() == []

    @pytest.mark.asyncio
    async def test_get_sync_token_returns_state(self, monkeypatch):
        resp = {
            "methodResponses": [
                [
                    "CalendarEvent/get",
                    {"accountId": _USERNAME, "state": "tok-async-1", "list": [], "notFound": []},
                    "ev-get-0",
                ]
            ]
        }
        self._patch_async_session(monkeypatch, resp)
        token = await self._make_client().get_sync_token()
        assert token == "tok-async-1"

    @pytest.mark.asyncio
    async def test_get_objects_no_changes(self, monkeypatch):
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.post = AsyncMock(return_value=self._make_mock_response(self._changes_resp()))
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        added, modified, deleted, _ = await self._make_client().get_objects_by_sync_token("state-1")
        assert added == [] and modified == [] and deleted == []

    @pytest.mark.asyncio
    async def test_get_objects_deleted_returns_ids(self, monkeypatch):
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.post = AsyncMock(
            return_value=self._make_mock_response(self._changes_resp(destroyed=["ev1"]))
        )
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        added, modified, deleted, _ = await self._make_client().get_objects_by_sync_token("state-1")
        assert deleted == ["ev1"] and added == [] and modified == []

    @pytest.mark.asyncio
    async def test_get_objects_added_returns_ical(self, monkeypatch):
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.post = AsyncMock(
            side_effect=[
                self._make_mock_response(self._changes_resp(created=["ev-async-1"])),
                self._make_mock_response(self._event_get_resp([self._RAW_EVENT])),
            ]
        )
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        added, modified, deleted, _ = await self._make_client().get_objects_by_sync_token("state-1")
        assert len(added) == 1
        assert isinstance(added[0], JMAPCalendarObject)
        assert added[0].id == "ev-async-1"
        assert modified == [] and deleted == []

    @pytest.mark.asyncio
    async def test_get_task_lists_returns_list(self, monkeypatch):
        self._patch_async_session(monkeypatch, self._tasklist_resp([self._MINIMAL_TASKLIST]))
        result = await self._make_client().get_task_lists()
        assert len(result) == 1
        assert isinstance(result[0], dict)
        assert result[0]["name"] == "Async Tasks"

    @pytest.mark.asyncio
    async def test_create_task_returns_id(self, monkeypatch):
        resp = self._task_set_resp(created={"new-0": {"id": "task-new-1"}}, notCreated={})
        self._patch_async_session(monkeypatch, resp)
        task_id = await self._make_client().create_task("tl1", "Async Task")
        assert task_id == "task-new-1"

    @pytest.mark.asyncio
    async def test_get_task_returns_task(self, monkeypatch):
        self._patch_async_session(monkeypatch, self._task_get_resp([self._MINIMAL_TASK]))
        result = await self._make_client().get_task("task-async-1")
        assert isinstance(result, dict)
        assert result["id"] == "task-async-1"

    @pytest.mark.asyncio
    async def test_get_task_raises_on_not_found(self, monkeypatch):
        self._patch_async_session(monkeypatch, self._task_get_resp([]))
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client().get_task("missing")
        assert exc_info.value.error_type == "notFound"

    @pytest.mark.asyncio
    async def test_update_task_success(self, monkeypatch):
        resp = self._task_set_resp(updated={"task-async-1": None}, notUpdated={})
        self._patch_async_session(monkeypatch, resp)
        await self._make_client().update_task("task-async-1", {"title": "Updated"})

    @pytest.mark.asyncio
    async def test_update_task_raises_on_failure(self, monkeypatch):
        resp = self._task_set_resp(updated={}, notUpdated={"task-async-1": {"type": "notFound"}})
        self._patch_async_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client().update_task("task-async-1", {"title": "X"})
        assert exc_info.value.error_type == "notFound"

    @pytest.mark.asyncio
    async def test_delete_task_success(self, monkeypatch):
        resp = self._task_set_resp(destroyed=["task-async-1"], notDestroyed={})
        self._patch_async_session(monkeypatch, resp)
        await self._make_client().delete_task("task-async-1")

    @pytest.mark.asyncio
    async def test_delete_task_raises_on_failure(self, monkeypatch):
        resp = self._task_set_resp(
            destroyed=[], notDestroyed={"task-async-1": {"type": "notFound"}}
        )
        self._patch_async_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client().delete_task("task-async-1")
        assert exc_info.value.error_type == "notFound"

    @pytest.mark.asyncio
    async def test_task_requests_use_task_capability(self, monkeypatch):
        captured = {}
        mock_resp = self._make_mock_response(self._tasklist_resp([self._MINIMAL_TASKLIST]))
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)

        async def capturing_post(*args, **kwargs):
            captured["json"] = kwargs.get("json", {})
            return mock_resp

        mock_http.post = capturing_post
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        await self._make_client().get_task_lists()
        assert TASK_CAPABILITY in captured["json"]["using"]
        assert CALENDAR_CAPABILITY not in captured["json"]["using"]

    def _capturing_async_session(self, monkeypatch, resp_json):
        captured = {}
        mock_resp = self._make_mock_response(resp_json)
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)

        async def capturing_post(*args, **kwargs):
            captured["json"] = kwargs.get("json", {})
            return mock_resp

        mock_http.post = capturing_post
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        return self._make_client(), captured

    @pytest.mark.asyncio
    async def test_create_event_passes_calendar_id(self, monkeypatch):
        resp = self._event_set_resp(created={"new-0": {"id": "ev-new-1"}}, notCreated={})
        client, captured = self._capturing_async_session(monkeypatch, resp)
        await client.create_event("my-cal", self._MINIMAL_ICAL)
        create_args = captured["json"]["methodCalls"][0][1]
        new_event = create_args["create"]["new-0"]
        assert "my-cal" in new_event.get("calendarIds", {})

    @pytest.mark.asyncio
    async def test_update_event_drops_uid_from_patch(self, monkeypatch):
        resp = self._event_set_resp(updated={"ev-async-1": None}, notUpdated={})
        client, captured = self._capturing_async_session(monkeypatch, resp)
        await client.update_event("ev-async-1", self._MINIMAL_ICAL)
        update_args = captured["json"]["methodCalls"][0][1]
        patch = update_args["update"]["ev-async-1"]
        assert "uid" not in patch

    @pytest.mark.asyncio
    async def test_search_events_passes_calendar_id_filter(self, monkeypatch):
        client, captured = self._capturing_async_session(
            monkeypatch, self._query_get_resp([self._RAW_EVENT])
        )
        await client.search_events(calendar_id="my-cal")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["inCalendars"] == ["my-cal"]

    @pytest.mark.asyncio
    async def test_search_events_passes_date_range_filter(self, monkeypatch):
        client, captured = self._capturing_async_session(
            monkeypatch, self._query_get_resp([self._RAW_EVENT])
        )
        await client.search_events(start="2026-01-01T00:00:00", end="2026-12-31T23:59:59")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["after"] == "2026-01-01T00:00:00"
        assert query_args["filter"]["before"] == "2026-12-31T23:59:59"

    @pytest.mark.asyncio
    async def test_search_events_passes_text_filter(self, monkeypatch):
        client, captured = self._capturing_async_session(
            monkeypatch, self._query_get_resp([self._RAW_EVENT])
        )
        await client.search_events(text="standup")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["text"] == "standup"

    @pytest.mark.asyncio
    async def test_search_events_no_filter_when_no_args(self, monkeypatch):
        client, captured = self._capturing_async_session(
            monkeypatch, self._query_get_resp([self._RAW_EVENT])
        )
        await client.search_events()
        query_args = captured["json"]["methodCalls"][0][1]
        assert "filter" not in query_args

    @pytest.mark.asyncio
    async def test_get_sync_token_sends_empty_ids(self, monkeypatch):
        resp = {
            "methodResponses": [
                [
                    "CalendarEvent/get",
                    {"accountId": _USERNAME, "state": "tok-1", "list": [], "notFound": []},
                    "ev-get-0",
                ]
            ]
        }
        client, captured = self._capturing_async_session(monkeypatch, resp)
        await client.get_sync_token()
        assert captured["json"]["methodCalls"][0][1]["ids"] == []

    @pytest.mark.asyncio
    async def test_get_objects_modified_returns_ical(self, monkeypatch):
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.post = AsyncMock(
            side_effect=[
                self._make_mock_response(self._changes_resp(updated=["ev-async-1"])),
                self._make_mock_response(self._event_get_resp([self._RAW_EVENT])),
            ]
        )
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        added, modified, deleted, _ = await self._make_client().get_objects_by_sync_token("state-1")
        assert len(modified) == 1
        assert isinstance(modified[0], JMAPCalendarObject)
        assert modified[0].id == "ev-async-1"
        assert added == [] and deleted == []

    @pytest.mark.asyncio
    async def test_create_task_passes_task_list_id(self, monkeypatch):
        resp = self._task_set_resp(created={"new-0": {"id": "task-new-1"}}, notCreated={})
        client, captured = self._capturing_async_session(monkeypatch, resp)
        await client.create_task("tl-target", "My Task")
        create_args = captured["json"]["methodCalls"][0][1]
        new_task = create_args["create"]["new-0"]
        assert new_task["taskListId"] == "tl-target"

    @pytest.mark.asyncio
    async def test_request_raises_auth_error_on_401(self, monkeypatch):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status = MagicMock()
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.post = AsyncMock(return_value=mock_resp)
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        with pytest.raises(JMAPAuthError):
            await self._make_client()._request([("Calendar/get", {"accountId": _USERNAME}, "c0")])

    @pytest.mark.asyncio
    async def test_request_raises_method_error_on_error_response(self, monkeypatch):
        error_response = {"methodResponses": [["error", {"type": "unknownMethod"}, "c0"]]}
        self._patch_async_session(monkeypatch, error_response)
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client()._request([("Calendar/get", {"accountId": _USERNAME}, "c0")])
        assert exc_info.value.error_type == "unknownMethod"

    @pytest.mark.asyncio
    async def test_get_session_fetches_and_caches_on_first_call(self, monkeypatch):
        client = AsyncJMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        fetched = Session(api_url=_API_URL, account_id=_USERNAME, state="fetched-state")
        mock_fetch = AsyncMock(return_value=fetched)
        monkeypatch.setattr("calendaring_jmap.async_client.async_fetch_session", mock_fetch)
        session = await client._get_session()
        assert session is fetched
        assert client._session_cache is fetched
        await client._get_session()
        mock_fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_object_by_uid_found(self, monkeypatch):
        client, _ = self._capturing_async_session(
            monkeypatch, self._query_get_resp([self._RAW_EVENT])
        )
        result = await client._get_object_by_uid("async-test-uid@example.com")
        assert isinstance(result, JMAPCalendarObject)
        assert result.id == "ev-async-1"

    @pytest.mark.asyncio
    async def test_get_object_by_uid_not_found(self, monkeypatch):
        client, _ = self._capturing_async_session(
            monkeypatch, self._query_get_resp([self._RAW_EVENT])
        )
        with pytest.raises(JMAPMethodError, match="No calendar object found with UID"):
            await client._get_object_by_uid("no-such-uid@example.com")

    @pytest.mark.asyncio
    async def test_update_event_retries_dropping_server_rejected_null_keys(self, monkeypatch):
        def reject(prop):
            return self._event_set_resp(
                notUpdated={
                    "ev-async-1": {
                        "type": "invalidProperties",
                        "description": "Invalid property.",
                        "properties": [prop],
                    }
                }
            )

        responses = [
            reject("recurrenceRules"),
            self._event_set_resp(updated={"ev-async-1": None}, notUpdated={}),
        ]
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        patches_seen = []

        async def post(*args, **kwargs):
            body = kwargs.get("json", {})
            update = body["methodCalls"][0][1].get("update")
            patches_seen.append(dict(update["ev-async-1"]))
            return self._make_mock_response(responses.pop(0))

        mock_http.post = post
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        await self._make_client().update_event("ev-async-1", self._MINIMAL_ICAL)
        assert len(patches_seen) == 2
        assert patches_seen[0]["recurrenceRules"] is None
        assert "recurrenceRules" not in patches_seen[1]


class TestOverrideWithoutStartUsesOccurrenceTime:
    """§4.1: override child VEVENT must use occurrence time as DTSTART, not master start."""

    def test_title_only_override_dtstart_equals_occurrence(self):
        # Master: 2024-06-17T09:00:00Z (UTC), weekly recurrence.
        # Override for 2024-06-24T09:00:00Z changes only title — no "start" in patch.
        # Child DTSTART must be 20240624T090000Z, not 20240617T090000Z.
        jscal = {
            "uid": "override-dtstart@example.com",
            "title": "Master Title",
            "start": "2024-06-17T09:00:00Z",
            "duration": "PT1H",
            "recurrenceRules": [{"@type": "RecurrenceRule", "frequency": "weekly"}],
            "recurrenceOverrides": {
                "2024-06-24T09:00:00Z": {"title": "Changed Title"},
            },
        }
        result = jscal_to_ical(jscal)
        import icalendar as _ic

        cal = _ic.Calendar.from_ical(result)
        events = [c for c in cal.subcomponents if isinstance(c, _ic.Event)]
        assert len(events) == 2
        child = next(e for e in events if e.get("RECURRENCE-ID") is not None)
        # DTSTART of the child must match its own occurrence, not the master start
        child_dtstart = child["DTSTART"].dt
        if hasattr(child_dtstart, "utctimetuple"):
            import datetime as _dt

            assert child_dtstart == _dt.datetime(2024, 6, 24, 9, 0, 0, tzinfo=_dt.timezone.utc)
        else:
            assert str(child_dtstart) == "2024-06-24"


class TestExdateValueType:
    """§4.2: EXDATE value type must match DTSTART (TZID or DATE, not floating)."""

    def test_exdate_for_tzid_event_has_tzid_param(self):
        # A TZID-anchored event's excluded override must produce EXDATE with TZID,
        # not a floating EXDATE (which per RFC 5545 won't match the instance).
        jscal = _minimal_jscal(
            start="2024-06-17T14:00:00",
            timeZone="Europe/Berlin",
            recurrenceRules=[{"@type": "RecurrenceRule", "frequency": "weekly"}],
            recurrenceOverrides={"2024-06-24T14:00:00": {"excluded": True}},
        )
        result = jscal_to_ical(jscal)
        # Must have TZID on EXDATE; a plain EXDATE:... without TZID is a floating datetime
        assert "EXDATE;TZID=Europe/Berlin:" in result

    def test_exdate_for_allday_event_is_date_value(self):
        jscal = {
            "uid": "allday-exdate@example.com",
            "title": "All Day Recurring",
            "start": "2024-06-17T00:00:00",
            "showWithoutTime": True,
            "duration": "P1D",
            "recurrenceRules": [{"@type": "RecurrenceRule", "frequency": "weekly"}],
            "recurrenceOverrides": {"2024-06-24T00:00:00": {"excluded": True}},
        }
        result = jscal_to_ical(jscal)
        # All-day EXDATE must be a DATE value (8-digit YYYYMMDD, not YYYYMMDDTHHMMSS datetime).
        # The icalendar library may or may not emit explicit VALUE=DATE — either form is acceptable.
        assert "EXDATE" in result
        assert "20240624" in result
        assert "20240624T" not in result  # must not be a datetime


class TestStatusMapping:
    """§4.4: STATUS must be mapped in both ical→jscal and jscal→ical directions."""

    def test_ical_status_cancelled_to_jscal(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Cancelled Meeting\r\nSTATUS:CANCELLED\r\n"
        )
        result = ical_to_jscal(ical)
        assert result.get("status") == "cancelled"

    def test_ical_status_tentative_to_jscal(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Tentative Meeting\r\nSTATUS:TENTATIVE\r\n"
        )
        result = ical_to_jscal(ical)
        assert result.get("status") == "tentative"

    def test_ical_status_confirmed_to_jscal(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Confirmed Meeting\r\nSTATUS:CONFIRMED\r\n"
        )
        result = ical_to_jscal(ical)
        assert result.get("status") == "confirmed"

    def test_ical_no_status_omits_jscal_status(self):
        ical = _make_ical("DTSTART:20240615T100000Z\r\nSUMMARY:No Status\r\n")
        result = ical_to_jscal(ical)
        assert "status" not in result

    def test_jscal_status_cancelled_to_ical(self):
        result = jscal_to_ical(_minimal_jscal(status="cancelled"))
        assert "STATUS:CANCELLED" in result

    def test_jscal_status_tentative_to_ical(self):
        result = jscal_to_ical(_minimal_jscal(status="tentative"))
        assert "STATUS:TENTATIVE" in result

    def test_jscal_status_confirmed_to_ical(self):
        result = jscal_to_ical(_minimal_jscal(status="confirmed"))
        assert "STATUS:CONFIRMED" in result

    def test_jscal_no_status_omits_ical_status(self):
        result = jscal_to_ical(_minimal_jscal())
        assert "STATUS:" not in result

    def test_status_cancelled_round_trips(self):
        original = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Cancelled\r\nSTATUS:CANCELLED\r\n"
        )
        jscal = ical_to_jscal(original)
        assert jscal.get("status") == "cancelled"
        round_tripped = jscal_to_ical(jscal)
        assert "STATUS:CANCELLED" in round_tripped


class TestLocalDateTimeIsEventLocal:
    """Gate finding F3: RFC 8984 LocalDateTime slots (RRULE ``until``,
    ``recurrenceOverrides`` keys) are expressed in the event's own timezone.
    ``_format_local_dt()`` merely dropped the tzinfo, so a UTC value coming
    off the wire was off by the UTC offset -- and the resulting floating
    ``UNTIL`` against a TZID ``DTSTART`` is forbidden by RFC 5545 3.3.10."""

    ICAL_HEAD = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//Test//EN\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:tz-local@example.com\r\n"
        "DTSTAMP:20240101T000000Z\r\n"
        "DTSTART;TZID=Europe/Berlin:20240615T090000\r\n"
        "DURATION:PT1H\r\n"
    )

    def _convert(self, extra: str) -> dict:
        return ical_to_jscal(self.ICAL_HEAD + extra + "END:VEVENT\r\nEND:VCALENDAR\r\n")

    def test_until_is_converted_to_event_timezone(self):
        # 2024-06-30T07:00:00Z is 09:00 in Europe/Berlin (CEST, UTC+2).
        jscal = self._convert("RRULE:FREQ=WEEKLY;UNTIL=20240630T070000Z\r\n")
        assert jscal["recurrenceRules"][0]["until"] == "2024-06-30T09:00:00"

    def test_exdate_key_is_converted_to_event_timezone(self):
        jscal = self._convert("RRULE:FREQ=WEEKLY\r\nEXDATE;VALUE=DATE-TIME:20240622T070000Z\r\n")
        assert "2024-06-22T09:00:00" in jscal["recurrenceOverrides"]

    def test_until_round_trips_back_to_utc(self):
        """The other half of the same rule: RFC 5545 3.3.10 requires a UTC UNTIL
        whenever DTSTART carries a TZID, so the LocalDateTime ``until`` has to be
        converted back -- not merely parsed as naive -- on the way out.  Raised in
        review of https://github.com/python-caldav/caldav/pull/688 ("the round-trip
        back through jscal_to_ical produces UNTIL=20240701T120000 with no Z
        suffix, which RFC 5545 3.3.10 forbids for TZID events")."""
        jscal = self._convert("RRULE:FREQ=WEEKLY;UNTIL=20240630T070000Z\r\n")
        assert jscal["recurrenceRules"][0]["until"] == "2024-06-30T09:00:00"
        ical = jscal_to_ical(jscal)
        assert "DTSTART;TZID=Europe/Berlin:20240615T090000" in ical
        assert "UNTIL=20240630T070000Z" in ical, (
            "a TZID DTSTART requires a UTC UNTIL; got a floating one"
        )

    def test_recurrence_id_key_is_converted_to_event_timezone(self):
        ical = (
            self.ICAL_HEAD + "RRULE:FREQ=WEEKLY\r\n"
            "END:VEVENT\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:tz-local@example.com\r\n"
            "DTSTAMP:20240101T000000Z\r\n"
            "RECURRENCE-ID:20240622T070000Z\r\n"
            "DTSTART;TZID=Europe/Berlin:20240622T100000\r\n"
            "SUMMARY:Moved\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        jscal = ical_to_jscal(ical)
        assert "2024-06-22T09:00:00" in jscal["recurrenceOverrides"]

    def test_naive_dtstart_leaves_utc_until_alone(self):
        """A floating DTSTART has no timezone to convert into; the value is
        passed through rather than guessed at."""
        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\nUID:floating@example.com\r\n"
            "DTSTAMP:20240101T000000Z\r\n"
            "DTSTART:20240615T090000\r\nDURATION:PT1H\r\n"
            "RRULE:FREQ=WEEKLY;UNTIL=20240630T070000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        jscal = ical_to_jscal(ical)
        assert jscal["recurrenceRules"][0]["until"] == "2024-06-30T07:00:00"


class TestJMAPSessionRelease:
    """Gate finding F9: the persistent HTTP session was released only by
    ``__exit__``/``__aexit__``.  The documented Quick Start does not use
    ``with``, so a client built that way leaked its connection pool with no
    way to release it short of dropping the object and hoping."""

    def _make_client(self):
        from calendaring_jmap.client import JMAPClient

        return JMAPClient(url="https://jmap.example.com/.well-known/jmap", password="token")

    def test_close_releases_the_session(self):
        client = self._make_client()
        session = client._get_http_session()
        assert session is not None
        client.close()
        assert client._http_session is None

    def test_close_is_idempotent(self):
        client = self._make_client()
        client._get_http_session()
        client.close()
        client.close()
        assert client._http_session is None

    def test_context_manager_still_releases_the_session(self):
        with self._make_client() as client:
            client._get_http_session()
        assert client._http_session is None

    def _make_async_client(self):
        from calendaring_jmap.async_client import AsyncJMAPClient

        return AsyncJMAPClient(url="https://jmap.example.com/.well-known/jmap", password="token")

    @pytest.mark.asyncio
    async def test_aclose_releases_the_session(self):
        client = self._make_async_client()
        client._get_http_session()
        await client.aclose()
        assert client._http_session is None

    @pytest.mark.asyncio
    async def test_async_context_manager_still_releases_the_session(self):
        async with self._make_async_client() as client:
            client._get_http_session()
        assert client._http_session is None

    @pytest.mark.asyncio
    async def test_aclose_is_idempotent(self):
        client = self._make_async_client()
        client._get_http_session()
        await client.aclose()
        await client.aclose()
        assert client._http_session is None

    def test_del_warns_when_session_left_open(self):
        client = self._make_async_client()
        client._http_session = MagicMock()
        with pytest.warns(ResourceWarning, match="garbage collected with an open HTTP"):
            client.__del__()

    def test_del_does_not_warn_when_session_already_closed(self):
        client = self._make_async_client()
        client._http_session = None
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            client.__del__()

    def test_del_suppresses_exception_from_warn(self, monkeypatch):
        client = self._make_async_client()
        client._http_session = MagicMock()
        monkeypatch.setattr(
            "calendaring_jmap.async_client.warnings.warn",
            MagicMock(side_effect=RuntimeError("warnings machinery torn down")),
        )
        client.__del__()  # must not raise
