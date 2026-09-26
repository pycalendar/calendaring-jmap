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

from calendaring_jmap._http import requests as _http_requests

_JMAP_URL = "http://localhost:8802/.well-known/jmap"
_API_URL = "http://localhost:8802/jmap/api"
_USERNAME = "user1"
_PASSWORD = "x"

from calendaring_jmap.error import (
    _DEFAULT_ERROR_TYPE,
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
        assert e.error_type == _DEFAULT_ERROR_TYPE

    def test_default_error_type_is_a_real_rfc_8620_type(self):
        """`_DEFAULT_ERROR_TYPE` used to be "serverError", which does not
        appear anywhere in RFC 8620 section 3.6.2's error type list. Pins
        it to one of the real generic types so this can't regress."""
        assert _DEFAULT_ERROR_TYPE == "serverFail"

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

    def test_falls_back_to_standalone_hierarchy_when_caldav_missing(self, monkeypatch):
        """calendaring_jmap.error subclasses caldav's DAVError/AuthorizationError
        when caldav is importable, and falls back to its own standalone base
        classes otherwise (see the module docstring). caldav is installed in
        this dev/CI environment, so that fallback branch never runs unless
        simulated here, matching _http.py's own module-reload pattern for the
        same kind of optional-import branch."""
        import importlib
        import sys

        import calendaring_jmap.error as error_mod

        original_dict = dict(error_mod.__dict__)
        for mod_name in list(sys.modules):
            if mod_name == "caldav" or mod_name.startswith("caldav."):
                monkeypatch.delitem(sys.modules, mod_name, raising=False)
        monkeypatch.setitem(sys.modules, "caldav", None)
        try:
            importlib.reload(error_mod)
            assert error_mod._CaldavDAVError.__bases__ == (Exception,)
            assert issubclass(error_mod._CaldavAuthorizationError, error_mod._CaldavDAVError)
            assert issubclass(error_mod.JMAPBaseError, error_mod._CaldavDAVError)
            # Still a fully working exception hierarchy on its own terms.
            e = error_mod.JMAPAuthError()
            assert isinstance(e, error_mod._CaldavAuthorizationError)
            assert e.error_type == "forbidden"
        finally:
            error_mod.__dict__.clear()
            error_mod.__dict__.update(original_dict)


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


def _make_mock_blob_response(status_code=200, json_data=None, content=None):
    """Mock a raw HTTP response for blob upload/download, distinct from
    _make_mock_response's JMAP methodCalls/methodResponses shape. Shared by
    _MockedBlobClientMixin (sync) and TestAsyncJMAPClient (async)."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    if json_data is not None:
        mock_resp.json.return_value = json_data
    mock_resp.content = content
    if status_code < 400:
        mock_resp.raise_for_status = MagicMock()
    else:
        ## Real raise_for_status() raises HTTPError, not a plain Exception.
        mock_resp.raise_for_status = MagicMock(
            side_effect=_http_requests.HTTPError(f"HTTP {status_code}")
        )
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

    def test_parses_upload_and_download_url(self):
        data = dict(_SESSION_JSON)
        data["uploadUrl"] = "http://localhost:8802/jmap/upload/{accountId}/"
        data["downloadUrl"] = (
            "http://localhost:8802/jmap/download/{accountId}/{blobId}/{name}?accept={type}"
        )
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(data)
            session = fetch_session(_JMAP_URL, auth=None)
        assert session.upload_url == "http://localhost:8802/jmap/upload/{accountId}/"
        assert session.download_url == (
            "http://localhost:8802/jmap/download/{accountId}/{blobId}/{name}?accept={type}"
        )

    def test_upload_and_download_url_default_to_none(self):
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(_SESSION_JSON)
            session = fetch_session(_JMAP_URL, auth=None)
        assert session.upload_url is None
        assert session.download_url is None

    def test_empty_string_upload_and_download_url_become_none(self):
        """A server returning "" instead of omitting the key must not slip
        past _require_blob_url's "is None" check downstream as if it were
        a real URL."""
        data = dict(_SESSION_JSON)
        data["uploadUrl"] = ""
        data["downloadUrl"] = ""
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(data)
            session = fetch_session(_JMAP_URL, auth=None)
        assert session.upload_url is None
        assert session.download_url is None

    def test_resolves_relative_upload_and_download_url(self):
        """Confirmed live against Cyrus: uploadUrl/downloadUrl can be a
        relative path, same as apiUrl (see test_parses_api_url's sibling
        rewrite test), and must be resolved the same way."""
        data = dict(_SESSION_JSON)
        data["uploadUrl"] = "/jmap/upload/{accountId}/"
        data["downloadUrl"] = "/jmap/download/{accountId}/{blobId}/{name}?accept={type}"
        with patch("calendaring_jmap.session.requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(data)
            session = fetch_session(_JMAP_URL, auth=None)
        assert session.upload_url == "http://localhost:8802/jmap/upload/{accountId}/"
        assert session.download_url == (
            "http://localhost:8802/jmap/download/{accountId}/{blobId}/{name}?accept={type}"
        )


class TestExpandUriTemplate:
    def test_expands_single_variable(self):
        from calendaring_jmap.session import _expand_uri_template

        assert _expand_uri_template("/upload/{accountId}/", {"accountId": "u1"}) == "/upload/u1/"

    def test_expands_multiple_variables(self):
        from calendaring_jmap.session import _expand_uri_template

        result = _expand_uri_template(
            "/download/{accountId}/{blobId}/{name}?accept={type}",
            {"accountId": "u1", "blobId": "G123", "name": "test.txt", "type": "text/plain"},
        )
        assert result == "/download/u1/G123/test.txt?accept=text%2Fplain"

    def test_percent_encodes_reserved_characters(self):
        from calendaring_jmap.session import _expand_uri_template

        assert _expand_uri_template("{v}", {"v": "Hello World!"}) == "Hello%20World%21"

    def test_leaves_unmapped_variable_unexpanded(self):
        from calendaring_jmap.session import _expand_uri_template

        assert _expand_uri_template("{known}/{unknown}", {"known": "x"}) == "x/{unknown}"

    def test_empty_string_value_expands_to_empty(self):
        from calendaring_jmap.session import _expand_uri_template

        assert _expand_uri_template("/x/{v}/y", {"v": ""}) == "/x//y"


from datetime import datetime, timezone
from typing import Literal

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
    "timeZone": "Europe/Berlin",
    "shareWith": {"principal1": {"mayReadItems": True, "mayShare": False}},
    "defaultAlertsWithTime": {"alert1": {"@type": "Alert", "trigger": {"@type": "OffsetTrigger"}}},
    "defaultAlertsWithoutTime": {
        "alert2": {"@type": "Alert", "trigger": {"@type": "OffsetTrigger"}}
    },
}

_CALENDAR_JSON_MINIMAL = {
    "id": "cal2",
    "name": "Work",
}


class TestJMAPCalendar:
    _MINIMAL_ICAL = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        "UID:test@example.com\r\nSUMMARY:Test\r\n"
        "DTSTART:20260115T090000Z\r\nDTEND:20260115T100000Z\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )

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
        assert cal.time_zone == "Europe/Berlin"
        assert cal.share_with == {"principal1": {"mayReadItems": True, "mayShare": False}}
        assert cal.default_alerts_with_time == {
            "alert1": {"@type": "Alert", "trigger": {"@type": "OffsetTrigger"}}
        }
        assert cal.default_alerts_without_time == {
            "alert2": {"@type": "Alert", "trigger": {"@type": "OffsetTrigger"}}
        }

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
        assert cal.time_zone is None
        assert cal.share_with is None
        assert cal.default_alerts_with_time is None
        assert cal.default_alerts_without_time is None

    def test_to_jmap_includes_new_optional_fields_when_set(self):
        cal = JMAPCalendar.from_jmap(_CALENDAR_JSON_FULL)
        d = cal.to_jmap()
        assert d["timeZone"] == "Europe/Berlin"
        assert d["shareWith"] == {"principal1": {"mayReadItems": True, "mayShare": False}}
        assert "defaultAlertsWithTime" in d
        assert "defaultAlertsWithoutTime" in d

    def test_to_jmap_omits_new_optional_fields_when_unset(self):
        cal = JMAPCalendar.from_jmap(_CALENDAR_JSON_MINIMAL)
        d = cal.to_jmap()
        assert "timeZone" not in d
        assert "shareWith" not in d
        assert "defaultAlertsWithTime" not in d
        assert "defaultAlertsWithoutTime" not in d

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

    def _set_response(self, created=None, notCreated=None):
        return _set_response(
            "CalendarEvent/set",
            "ev-set-create-0",
            created=created or {},
            updated={},
            destroyed=[],
            notCreated=notCreated or {},
            notUpdated={},
            notDestroyed={},
        )

    def _capturing_calendar(self, monkeypatch, resp, calendar_id="cal1"):
        client = JMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        client._session_cache = Session(api_url=_API_URL, account_id=_USERNAME, state="state-abc")
        captured: dict = {}

        def capturing_post(*args, **kwargs):
            captured["json"] = kwargs.get("json", {})
            return _make_mock_response(resp)

        mock_http = MagicMock()
        mock_http.post.side_effect = capturing_post
        client._http_session = mock_http
        cal = JMAPCalendar(id=calendar_id, name="Test")
        cal._client = client
        cal._is_async = False
        return cal, captured

    def test_calendar_search_returns_ical_list(self, monkeypatch):
        event2 = {**self._RAW_EVENT, "id": "ev2", "title": "Standup"}
        resp = _query_get_response([self._RAW_EVENT, event2])
        cal = _make_calendar_with_client(monkeypatch, resp)
        results = cal.search()
        assert len(results) == 2
        assert all(isinstance(r, JMAPCalendarObject) for r in results)
        assert all(r.parent is cal for r in results)
        assert results[0].id == "ev1"

    def test_calendar_search_passes_calendar_id_filter(self, monkeypatch):
        resp = _query_get_response([self._RAW_EVENT])
        cal, captured = self._capturing_calendar(monkeypatch, resp, calendar_id="my-cal")
        cal.search()
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["inCalendar"] == "my-cal"

    def test_calendar_search_with_date_range(self, monkeypatch):
        resp = _query_get_response([self._RAW_EVENT])
        cal, captured = self._capturing_calendar(monkeypatch, resp)
        cal.search(start="2026-01-01T00:00:00", end="2026-12-31T23:59:59")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["after"] == "2026-01-01T00:00:00"
        assert query_args["filter"]["before"] == "2026-12-31T23:59:59"

    def test_calendar_search_datetime_converted_to_utcdate(self, monkeypatch):
        """Gate finding 4.6: datetime.isoformat() produced wrong format for
        JMAP UTCDate. Naive datetimes produce no Z, aware non-UTC produce
        +HH:MM offset; JMAP requires ...Z (UTC, no microseconds)."""
        import datetime as _dt

        resp = _query_get_response([self._RAW_EVENT])
        cal, captured = self._capturing_calendar(monkeypatch, resp)
        tz_plus2 = _dt.timezone(_dt.timedelta(hours=2))
        start_aware = datetime(2026, 6, 1, 12, 0, 0, tzinfo=tz_plus2)  # +02:00 noon = UTC 10:00
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
        resp = _query_get_response([self._RAW_EVENT])
        cal, captured = self._capturing_calendar(monkeypatch, resp)
        # Should not raise an error even with legacy/unknown parameters
        cal.search(event=True, todo=False, unknown_param="value")
        query_args = captured["json"]["methodCalls"][0][1]
        # Should only contain the calendar filter, no unknown params
        assert query_args["filter"] == {"inCalendar": cal.id}

    def test_calendar_search_with_text(self, monkeypatch):
        resp = _query_get_response([self._RAW_EVENT])
        cal, captured = self._capturing_calendar(monkeypatch, resp)
        cal.search(text="standup")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["text"] == "standup"

    def test_calendar_get_object_by_uid_found(self, monkeypatch):
        resp = _query_get_response([self._RAW_EVENT])
        cal = _make_calendar_with_client(monkeypatch, resp)
        result = cal.get_object_by_uid("test-uid@example.com")
        assert isinstance(result, JMAPCalendarObject)
        assert result.id == "ev1"
        assert result.get_data()["title"] == "Staff Meeting"
        assert result.parent is cal

    def test_calendar_get_object_by_uid_not_found(self, monkeypatch):
        resp = _query_get_response([self._RAW_EVENT])
        cal = _make_calendar_with_client(monkeypatch, resp)
        with pytest.raises(JMAPMethodError):
            cal.get_object_by_uid("nonexistent-uid@example.com")

    def test_calendar_add_event_delegates_to_create_event(self, monkeypatch):
        resp = self._set_response(created={"new-0": {"id": "sv-cal-1"}})
        cal, captured = self._capturing_calendar(monkeypatch, resp, calendar_id="my-calendar")
        cal.add_event(self._MINIMAL_ICAL)
        create_args = captured["json"]["methodCalls"][0][1]
        event_payload = create_args["create"]["new-0"]
        assert event_payload.get("calendarIds") == {"my-calendar": True}

    def test_calendar_add_event_uses_calendar_account_id_when_shared(self, monkeypatch):
        resp = self._set_response(created={"new-0": {"id": "sv-cal-1"}})
        cal, captured = self._capturing_calendar(monkeypatch, resp, calendar_id="shared-cal")
        cal._account_id = "owner-account"
        cal.add_event(self._MINIMAL_ICAL)
        create_args = captured["json"]["methodCalls"][0][1]
        assert create_args["accountId"] == "owner-account"

    def test_calendar_search_uses_calendar_account_id_when_shared(self, monkeypatch):
        resp = _query_get_response([self._RAW_EVENT])
        cal, captured = self._capturing_calendar(monkeypatch, resp, calendar_id="shared-cal")
        cal._account_id = "owner-account"
        cal.search()
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["accountId"] == "owner-account"

    def test_calendar_search_naive_datetime_treated_as_utc(self, monkeypatch):
        resp = _query_get_response([self._RAW_EVENT])
        cal, captured = self._capturing_calendar(monkeypatch, resp)
        naive_start = datetime(2026, 6, 1, 12, 0, 0)
        cal.search(start=naive_start)
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["after"] == "2026-06-01T12:00:00Z"

    def test_calendar_search_dispatches_to_async_when_async_backed(self):
        mock_client = MagicMock()
        mock_client._search = AsyncMock(return_value=["result"])
        cal: JMAPCalendar[Literal[True]] = JMAPCalendar(id="cal1", name="Test")
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
        cal: JMAPCalendar[Literal[True]] = JMAPCalendar(id="cal1", name="Test")
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
        cal: JMAPCalendar[Literal[True]] = JMAPCalendar(id="cal1", name="Test")
        cal._client = mock_client
        cal._is_async = True
        import asyncio

        result = asyncio.run(cal.get_object_by_uid("some-uid"))
        assert result == "obj"
        mock_client._get_object_by_uid.assert_awaited_once_with(
            "some-uid", calendar_id="cal1", parent=cal, account_id=None
        )

    def test_calendar_add_event_dispatches_to_async_when_async_backed(self):
        mock_client = MagicMock()
        mock_client.create_event = AsyncMock(return_value="ev-new-id")
        cal: JMAPCalendar[Literal[True]] = JMAPCalendar(id="cal1", name="Test")
        cal._client = mock_client
        cal._is_async = True
        import asyncio

        result = asyncio.run(cal.add_event("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"))
        assert result == "ev-new-id"
        mock_client.create_event.assert_awaited_once_with(
            "cal1", "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", account_id=None
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
        assert call_args.kwargs["account_id"] is None

    def test_save_uses_parent_account_id_when_shared(self):
        mock_client = MagicMock()
        mock_parent = JMAPCalendar(id="cal1", name="Test")
        mock_parent._client = mock_client
        mock_parent._account_id = "owner-account"

        obj = JMAPCalendarObject(data=_MINIMAL_JSCAL_DICT, parent=mock_parent)
        obj.save()

        mock_client.update_event.assert_called_once()
        assert mock_client.update_event.call_args.kwargs["account_id"] == "owner-account"

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


from calendaring_jmap.objects.busy_interval import BusyInterval


class TestBusyInterval:
    def test_from_jmap_full(self):
        data = {
            "utcStart": "2026-09-21T10:00:00Z",
            "utcEnd": "2026-09-21T11:00:00Z",
            "busyStatus": "tentative",
            "event": {"id": "ev1", "title": "Busy Block"},
            "accountId": "user1",
        }
        interval = BusyInterval.from_jmap(data)
        assert interval.start == "2026-09-21T10:00:00Z"
        assert interval.end == "2026-09-21T11:00:00Z"
        assert interval.busy_status == "tentative"
        assert isinstance(interval.event, JMAPCalendarObject)
        assert interval.event.get_data() == {"id": "ev1", "title": "Busy Block"}
        assert interval.event.parent is None

    def test_from_jmap_event_none(self):
        data = {
            "utcStart": "2026-09-21T10:00:00Z",
            "utcEnd": "2026-09-21T11:00:00Z",
            "event": None,
        }
        interval = BusyInterval.from_jmap(data)
        assert interval.event is None

    def test_from_jmap_busy_status_defaults_to_unavailable(self):
        data = {"utcStart": "2026-09-21T10:00:00Z", "utcEnd": "2026-09-21T11:00:00Z"}
        interval = BusyInterval.from_jmap(data)
        assert interval.busy_status == "unavailable"

    def test_from_jmap_ignores_unknown_keys(self):
        data = {
            "utcStart": "2026-09-21T10:00:00Z",
            "utcEnd": "2026-09-21T11:00:00Z",
            "someNewProperty": "value",
        }
        interval = BusyInterval.from_jmap(data)
        assert interval.start == "2026-09-21T10:00:00Z"


from calendaring_jmap.objects.attachment import JMAPAttachment


class TestJMAPAttachment:
    def test_from_jmap_full(self):
        data = {
            "@type": "Link",
            "href": "http://example.com/download/G123/test.txt",
            "rel": "enclosure",
            "title": "test.txt",
            "contentType": "text/plain",
            "size": 21,
        }
        attachment = JMAPAttachment.from_jmap("link1", data)
        assert attachment.link_id == "link1"
        assert attachment.href == "http://example.com/download/G123/test.txt"
        assert attachment.title == "test.txt"
        assert attachment.content_type == "text/plain"
        assert attachment.size == 21
        assert attachment.blob_id is None

    def test_from_jmap_with_blob_id(self):
        data = {"@type": "Link", "blobId": "G123", "rel": "enclosure"}
        attachment = JMAPAttachment.from_jmap("link1", data)
        assert attachment.blob_id == "G123"
        assert attachment.href is None

    def test_from_jmap_ignores_unknown_keys(self):
        data = {"href": "http://x", "rel": "enclosure", "cid": "unused-by-this-client"}
        attachment = JMAPAttachment.from_jmap("link1", data)
        assert attachment.href == "http://x"

    def test_is_attachment_true_for_enclosure_rel(self):
        assert JMAPAttachment.is_attachment({"rel": "enclosure"}) is True

    def test_is_attachment_false_for_other_rel(self):
        assert JMAPAttachment.is_attachment({"rel": "describedby"}) is False

    def test_is_attachment_false_when_rel_absent(self):
        assert JMAPAttachment.is_attachment({"href": "http://x"}) is False


from calendaring_jmap.objects.contact import JMAPAddressBook, JMAPContact

_ADDRESS_BOOK_JSON_FULL = {
    "id": "ab1",
    "name": "Personal",
    "description": "My personal address book",
    "sortOrder": 1,
    "isDefault": True,
    "isSubscribed": True,
    "shareWith": {"principal1": {"mayRead": True, "mayWrite": False}},
    "myRights": {"mayRead": True, "mayWrite": True, "mayShare": True, "mayDelete": True},
}

_ADDRESS_BOOK_JSON_MINIMAL = {
    "id": "ab2",
    "name": "Work",
}

_CONTACT_JSON_FULL = {
    "id": "c1",
    "uid": "contact-uid-1",
    "addressBookIds": {"ab1": True},
    "kind": "individual",
    "name": {"full": "Alice Example"},
    "emails": {
        "e1": {"address": "alice-work@example.com", "pref": 2},
        "e2": {"address": "alice@example.com", "pref": 1},
    },
}

_CONTACT_JSON_MINIMAL = {
    "id": "c2",
}


class TestJMAPAddressBook:
    def test_from_jmap_full(self):
        ab = JMAPAddressBook.from_jmap(_ADDRESS_BOOK_JSON_FULL)
        assert ab.id == "ab1"
        assert ab.name == "Personal"
        assert ab.description == "My personal address book"
        assert ab.sort_order == 1
        assert ab.is_default is True
        assert ab.is_subscribed is True
        assert ab.share_with == {"principal1": {"mayRead": True, "mayWrite": False}}
        assert ab.my_rights == {
            "mayRead": True,
            "mayWrite": True,
            "mayShare": True,
            "mayDelete": True,
        }

    def test_from_jmap_minimal_uses_defaults(self):
        ab = JMAPAddressBook.from_jmap(_ADDRESS_BOOK_JSON_MINIMAL)
        assert ab.id == "ab2"
        assert ab.name == "Work"
        assert ab.description is None
        assert ab.sort_order == 0
        assert ab.is_default is False
        assert ab.is_subscribed is True
        assert ab.share_with is None
        assert ab.my_rights == {}

    def test_from_jmap_raises_when_id_missing(self):
        with pytest.raises(KeyError):
            JMAPAddressBook.from_jmap({"name": "No Id"})

    def test_from_jmap_raises_when_name_missing(self):
        with pytest.raises(KeyError):
            JMAPAddressBook.from_jmap({"id": "ab3"})

    def test_from_jmap_share_with_empty_dict_stays_empty_dict(self):
        # Confirmed live that Stalwart returns {} for an unshared address
        # book, not null; this must not collapse to None.
        ab = JMAPAddressBook.from_jmap({"id": "ab4", "name": "Unshared", "shareWith": {}})
        assert ab.share_with == {}


class TestJMAPContact:
    def test_from_jmap_full(self):
        contact = JMAPContact.from_jmap(_CONTACT_JSON_FULL)
        assert contact.id == "c1"
        assert contact.uid == "contact-uid-1"
        assert contact.address_book_ids == {"ab1": True}
        assert contact.kind == "individual"
        assert contact.name == {"full": "Alice Example"}
        assert contact.emails == {
            "e1": {"address": "alice-work@example.com", "pref": 2},
            "e2": {"address": "alice@example.com", "pref": 1},
        }

    def test_from_jmap_minimal_uses_defaults(self):
        contact = JMAPContact.from_jmap(_CONTACT_JSON_MINIMAL)
        assert contact.id == "c2"
        # Confirmed live that Stalwart's ContactCard/get never returns uid
        # at all, even when explicitly requested via properties, contrary
        # to RFC 9553 treating it as mandatory; unlike id, a missing uid
        # must not raise, or every Stalwart contact would break.
        assert contact.uid is None
        assert contact.address_book_ids == {}
        assert contact.kind == "individual"
        assert contact.name is None
        assert contact.emails is None

    def test_from_jmap_raises_when_id_missing(self):
        with pytest.raises(KeyError):
            JMAPContact.from_jmap({"uid": "no-id"})

    def test_display_name_prefers_full(self):
        contact = JMAPContact.from_jmap(_CONTACT_JSON_FULL)
        assert contact.display_name() == "Alice Example"

    def test_display_name_falls_back_to_components(self):
        contact = JMAPContact(
            id="c4",
            name={
                "components": [
                    {"kind": "given", "value": "Bob"},
                    {"kind": "surname", "value": "Builder"},
                ],
                "isOrdered": True,
            },
        )
        assert contact.display_name() == "Bob Builder"

    def test_display_name_falls_back_to_components_when_full_is_empty_string(self):
        contact = JMAPContact(
            id="c4b",
            name={
                "full": "",
                "components": [
                    {"kind": "given", "value": "Bob"},
                    {"kind": "surname", "value": "Builder"},
                ],
                "isOrdered": True,
            },
        )
        assert contact.display_name() == "Bob Builder"

    def test_display_name_uses_separator_component_value(self):
        # RFC 9553 section 2.2.1.1: a separator component's own value gives
        # guidance on what to insert between the surrounding components; a
        # hyphenated name must stay hyphenated, not collapse to a space.
        contact = JMAPContact(
            id="c5",
            name={
                "components": [
                    {"kind": "given", "value": "Jean"},
                    {"kind": "separator", "value": "-"},
                    {"kind": "surname", "value": "Paul"},
                ],
                "isOrdered": True,
            },
        )
        assert contact.display_name() == "Jean-Paul"

    def test_display_name_empty_string_separator_joins_with_nothing(self):
        # RFC 9553 section 2.2.1.1 explicitly allows an empty separator
        # value; it must be distinguished from "no separator at this
        # position" (which falls back to defaultSeparator/space), not
        # collapsed to it by a truthiness check.
        contact = JMAPContact(
            id="c5e",
            name={
                "components": [
                    {"kind": "given", "value": "Jean"},
                    {"kind": "separator", "value": ""},
                    {"kind": "surname", "value": "Paul"},
                ],
                "isOrdered": True,
            },
        )
        assert contact.display_name() == "JeanPaul"

    def test_display_name_uses_default_separator_without_explicit_one(self):
        contact = JMAPContact(
            id="c5b",
            name={
                "components": [
                    {"kind": "surname", "value": "Pau Shou Chang"},
                    {"kind": "given", "value": "Robert"},
                ],
                "isOrdered": True,
                "defaultSeparator": ", ",
            },
        )
        assert contact.display_name() == "Pau Shou Chang, Robert"

    def test_display_name_unordered_space_joins_without_separators(self):
        # RFC 9553 section 2.2.1.1: an unordered Name's components property
        # MUST NOT contain a "separator" component at all.
        contact = JMAPContact(
            id="c5d",
            name={
                "components": [
                    {"kind": "given", "value": "Bob"},
                    {"kind": "surname", "value": "Builder"},
                ],
                "isOrdered": False,
            },
        )
        assert contact.display_name() == "Bob Builder"

    def test_display_name_none_when_name_absent(self):
        contact = JMAPContact.from_jmap(_CONTACT_JSON_MINIMAL)
        assert contact.display_name() is None

    def test_display_name_none_when_neither_full_nor_components(self):
        contact = JMAPContact(id="c6", name={})
        assert contact.display_name() is None

    def test_display_name_none_when_name_present_but_empty_full_and_components(self):
        # A non-empty name dict (unlike the {} case above, which already
        # short-circuits earlier) with neither full nor components set.
        contact = JMAPContact(id="c6b", name={"full": None, "components": None})
        assert contact.display_name() is None

    def test_primary_email_picks_lowest_pref(self):
        # _CONTACT_JSON_FULL's "e2" entry has pref 1 (most preferred);
        # "e1" has pref 2.
        contact = JMAPContact.from_jmap(_CONTACT_JSON_FULL)
        assert contact.primary_email() == "alice@example.com"

    def test_primary_email_treats_missing_pref_as_least_preferred(self):
        contact = JMAPContact(
            id="c7",
            emails={
                "e1": {"address": "no-pref@example.com"},
                "e2": {"address": "preferred@example.com", "pref": 1},
            },
        )
        assert contact.primary_email() == "preferred@example.com"

    def test_primary_email_treats_explicit_null_pref_as_least_preferred(self):
        # RFC-invalid (pref has no null in its own type), but must not
        # crash: min()'s key function can't compare None with an int.
        contact = JMAPContact(
            id="c7b",
            emails={
                "e1": {"address": "null-pref@example.com", "pref": None},
                "e2": {"address": "preferred@example.com", "pref": 1},
            },
        )
        assert contact.primary_email() == "preferred@example.com"

    def test_primary_email_none_when_no_emails(self):
        contact = JMAPContact.from_jmap(_CONTACT_JSON_MINIMAL)
        assert contact.primary_email() is None


from calendaring_jmap._methods.calendar import (
    build_calendar_get,
    build_calendar_set_create,
    build_calendar_set_destroy,
    build_calendar_set_update,
    parse_calendar_get,
    parse_calendar_set,
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

    def test_build_calendar_set_create_structure(self):
        method, args, call_id = build_calendar_set_create("u1", {"new-0": {"name": "Work"}})
        assert method == "Calendar/set"
        assert args["accountId"] == "u1"
        assert args["create"] == {"new-0": {"name": "Work"}}
        assert isinstance(call_id, str)

    def test_build_calendar_set_update_structure(self):
        method, args, call_id = build_calendar_set_update("u1", {"cal1": {"name": "Renamed"}})
        assert method == "Calendar/set"
        assert args["accountId"] == "u1"
        assert args["update"] == {"cal1": {"name": "Renamed"}}

    def test_build_calendar_set_destroy_structure(self):
        method, args, call_id = build_calendar_set_destroy("u1", ["cal1", "cal2"])
        assert method == "Calendar/set"
        assert args["accountId"] == "u1"
        assert args["destroy"] == ["cal1", "cal2"]
        assert args["onDestroyRemoveEvents"] is False

    def test_build_calendar_set_destroy_on_destroy_remove_events(self):
        _, args, _ = build_calendar_set_destroy("u1", ["cal1"], on_destroy_remove_events=True)
        assert args["onDestroyRemoveEvents"] is True

    def test_parse_calendar_set_returns_created(self):
        created, updated, destroyed, not_created, not_updated, not_destroyed = parse_calendar_set(
            {"created": {"new-0": {"id": "srv1"}}}
        )
        assert created == {"new-0": {"id": "srv1"}}
        assert updated == {}
        assert destroyed == []
        assert not_created == {}
        assert not_updated == {}
        assert not_destroyed == {}

    def test_parse_calendar_set_returns_not_destroyed_with_calendar_has_event(self):
        _, _, _, _, _, not_destroyed = parse_calendar_set(
            {"notDestroyed": {"cal1": {"type": "calendarHasEvent"}}}
        )
        assert not_destroyed == {"cal1": {"type": "calendarHasEvent"}}


from calendaring_jmap._methods.contact import (
    build_address_book_get,
    build_contact_get_by_query_result,
    build_contact_query,
    parse_address_book_get,
    parse_contact_get,
)


class TestContactMethodBuilders:
    def test_build_address_book_get_structure(self):
        method, args, call_id = build_address_book_get("u1")
        assert method == "AddressBook/get"
        assert args["accountId"] == "u1"
        assert args["ids"] is None
        assert isinstance(call_id, str)

    def test_build_address_book_get_with_ids(self):
        _, args, _ = build_address_book_get("u1", ids=["ab1", "ab2"])
        assert args["ids"] == ["ab1", "ab2"]

    def test_build_address_book_get_with_properties(self):
        _, args, _ = build_address_book_get("u1", properties=["id", "name"])
        assert args["properties"] == ["id", "name"]

    def test_parse_address_book_get_returns_address_books(self):
        response_args = {"list": [_ADDRESS_BOOK_JSON_FULL, _ADDRESS_BOOK_JSON_MINIMAL]}
        books = parse_address_book_get(response_args)
        assert len(books) == 2
        assert isinstance(books[0], JMAPAddressBook)
        assert books[0].id == "ab1"
        assert books[1].id == "ab2"

    def test_parse_address_book_get_missing_list_key(self):
        assert parse_address_book_get({}) == []

    def test_build_contact_query_structure(self):
        method, args, call_id = build_contact_query("u1")
        assert method == "ContactCard/query"
        assert args["accountId"] == "u1"
        assert args["position"] == 0
        assert "filter" not in args
        assert isinstance(call_id, str)

    def test_build_contact_query_with_filter(self):
        _, args, _ = build_contact_query("u1", filter_condition={"email": "alice@example.com"})
        assert args["filter"] == {"email": "alice@example.com"}

    def test_build_contact_query_with_sort_position_limit(self):
        _, args, _ = build_contact_query("u1", sort=[{"property": "name"}], position=5, limit=10)
        assert args["sort"] == [{"property": "name"}]
        assert args["position"] == 5
        assert args["limit"] == 10

    def test_build_contact_get_by_query_result_structure(self):
        method, args, call_id = build_contact_get_by_query_result("u1")
        assert method == "ContactCard/get"
        assert args["accountId"] == "u1"
        assert "properties" not in args
        assert isinstance(call_id, str)

    def test_build_contact_get_by_query_result_references_query_call(self):
        query_method, _, query_call_id = build_contact_query("u1")
        _, args, _ = build_contact_get_by_query_result("u1")
        assert args["#ids"] == {
            "resultOf": query_call_id,
            "name": query_method,
            "path": "/ids",
        }

    def test_build_contact_get_by_query_result_with_properties(self):
        _, args, _ = build_contact_get_by_query_result("u1", properties=["id", "emails"])
        assert args["properties"] == ["id", "emails"]

    def test_parse_contact_get_returns_contacts(self):
        response_args = {"list": [_CONTACT_JSON_FULL, _CONTACT_JSON_MINIMAL]}
        contacts = parse_contact_get(response_args)
        assert len(contacts) == 2
        assert isinstance(contacts[0], JMAPContact)
        assert contacts[0].id == "c1"
        assert contacts[1].id == "c2"

    def test_parse_contact_get_missing_list_key(self):
        assert parse_contact_get({}) == []


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


def _make_client() -> JMAPClient:
    """Return a JMAPClient with a mocked Session cache, no HTTP session set up
    yet. Shared by every sync test helper that needs a bare mocked client
    (:func:`_make_client_with_mocked_session`, ``_MockedClientMixin``)."""
    client = JMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
    client._session_cache = Session(api_url=_API_URL, account_id=_USERNAME, state="state-abc")
    return client


def _make_client_with_mocked_session(monkeypatch, api_response_json):
    """Return a JMAPClient whose HTTP calls are fully mocked."""
    client = _make_client()
    mock_http = MagicMock()
    mock_http.post.return_value = _make_mock_response(api_response_json)
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

    def test_get_calendars_binds_own_account_id_by_default(self, monkeypatch):
        client = _make_client_with_mocked_session(monkeypatch, _CALENDAR_GET_RESPONSE)
        cals = client.get_calendars()
        assert cals[0]._account_id == _USERNAME

    def test_get_calendars_binds_requested_account_id(self, monkeypatch):
        client = _make_client_with_mocked_session(monkeypatch, _CALENDAR_GET_RESPONSE)
        cals = client.get_calendars(account_id="owner-account")
        assert cals[0]._account_id == "owner-account"

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


from calendaring_jmap._methods.event import parse_event_set
from calendaring_jmap._methods.task import parse_task_set
from calendaring_jmap.client import _JMAPClientBase

#: A response entry for a method name no test here is looking for. Shared by
#: every "skips/raises without a matching response in the batch" test below
#: and in the get_availability test classes further down the file.
_UNRELATED_RESPONSE: tuple[str, dict, str] = ("Calendar/changes", {}, "unrelated-0")


class TestJMAPClientBaseParsers:
    """Direct unit tests for the response-fallthrough branches of each
    _parse_* helper: what happens when the expected method name never
    appears in the responses list (e.g. a batched call whose relevant
    method was dropped or reordered by the server)."""

    def test_parse_get_calendars_returns_empty_list_without_match(self):
        assert _JMAPClientBase._parse_get_calendars([], client=None, is_async=False) == []

    _SET_METHODS_BY_OBJECT = {
        "CalendarEvent/set": parse_event_set,
        "Calendar/set": parse_calendar_set,
        "Task/set": parse_task_set,
    }

    def test_parse_create_response_raises_without_match(self):
        for set_method, parse_set in self._SET_METHODS_BY_OBJECT.items():
            with pytest.raises(JMAPMethodError, match=f"No {set_method} response"):
                _JMAPClientBase._parse_create_response([], _API_URL, set_method, parse_set)

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
            [_UNRELATED_RESPONSE, matching], api_url=_API_URL, event_id="ev1"
        )
        assert result.id == "ev1"

    def test_parse_update_response_raises_without_match(self):
        for set_method, parse_set in self._SET_METHODS_BY_OBJECT.items():
            with pytest.raises(JMAPMethodError, match=f"No {set_method} response"):
                _JMAPClientBase._parse_update_response([], _API_URL, set_method, parse_set, "id1")

    def test_parse_search_response_returns_empty_list_without_match(self):
        assert _JMAPClientBase._parse_search_response([], parent=None) == []

    def test_parse_get_sync_token_response_raises_without_match(self):
        with pytest.raises(JMAPMethodError, match="No CalendarEvent/get response"):
            _JMAPClientBase._parse_get_sync_token_response([], api_url=_API_URL)

    def test_parse_delete_response_raises_without_match(self):
        for set_method, parse_set in self._SET_METHODS_BY_OBJECT.items():
            with pytest.raises(JMAPMethodError, match=f"No {set_method} response"):
                _JMAPClientBase._parse_delete_response([], _API_URL, set_method, parse_set, "id1")

    def test_parse_get_task_lists_response_returns_empty_list_without_match(self):
        assert _JMAPClientBase._parse_get_task_lists_response([]) == []

    def test_parse_get_address_books_returns_empty_list_without_match(self):
        assert _JMAPClientBase._parse_get_address_books([]) == []

    def test_parse_search_contacts_response_returns_empty_list_without_match(self):
        assert _JMAPClientBase._parse_search_contacts_response([]) == []

    def test_parse_get_task_response_raises_without_match(self):
        with pytest.raises(JMAPMethodError, match="No Task/get response"):
            _JMAPClientBase._parse_get_task_response([], api_url=_API_URL, task_id="t1")

    def test_parse_event_changes_response_without_match_returns_empty_defaults(self):
        result = _JMAPClientBase._parse_event_changes_response([], api_url=_API_URL)
        assert result == ([], [], [], "")

    def test_unsupported_null_keys_returns_none_without_match(self):
        assert (
            _JMAPClientBase._unsupported_null_keys(
                [_UNRELATED_RESPONSE], "ev1", patch={}, nulled=frozenset()
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
                [_UNRELATED_RESPONSE, matching],
                "ev1",
                patch={"title": "x"},
                nulled=frozenset(),
            )
            is None
        )

    def test_parse_get_calendars_skips_unrelated_responses_in_batch(self):
        assert (
            _JMAPClientBase._parse_get_calendars([_UNRELATED_RESPONSE], client=None, is_async=False)
            == []
        )

    def test_parse_create_response_skips_unrelated_responses_in_batch(self):
        for set_method, parse_set in self._SET_METHODS_BY_OBJECT.items():
            with pytest.raises(JMAPMethodError, match=f"No {set_method} response"):
                _JMAPClientBase._parse_create_response(
                    [_UNRELATED_RESPONSE], _API_URL, set_method, parse_set
                )

    def test_parse_update_response_skips_unrelated_responses_in_batch(self):
        for set_method, parse_set in self._SET_METHODS_BY_OBJECT.items():
            with pytest.raises(JMAPMethodError, match=f"No {set_method} response"):
                _JMAPClientBase._parse_update_response(
                    [_UNRELATED_RESPONSE], _API_URL, set_method, parse_set, "id1"
                )

    def test_parse_search_response_skips_unrelated_responses_in_batch(self):
        assert _JMAPClientBase._parse_search_response([_UNRELATED_RESPONSE], parent=None) == []

    def test_parse_get_sync_token_response_skips_unrelated_responses_in_batch(self):
        with pytest.raises(JMAPMethodError, match="No CalendarEvent/get response"):
            _JMAPClientBase._parse_get_sync_token_response([_UNRELATED_RESPONSE], api_url=_API_URL)

    def test_parse_event_changes_response_skips_unrelated_responses_in_batch(self):
        result = _JMAPClientBase._parse_event_changes_response(
            [("Task/set", {}, "unrelated-0")], api_url=_API_URL
        )
        assert result == ([], [], [], "")

    def test_parse_delete_response_skips_unrelated_responses_in_batch(self):
        for set_method, parse_set in self._SET_METHODS_BY_OBJECT.items():
            with pytest.raises(JMAPMethodError, match=f"No {set_method} response"):
                _JMAPClientBase._parse_delete_response(
                    [_UNRELATED_RESPONSE], _API_URL, set_method, parse_set, "id1"
                )

    def test_parse_get_task_lists_response_skips_unrelated_responses_in_batch(self):
        assert _JMAPClientBase._parse_get_task_lists_response([_UNRELATED_RESPONSE]) == []

    def test_parse_get_address_books_skips_unrelated_responses_in_batch(self):
        assert _JMAPClientBase._parse_get_address_books([_UNRELATED_RESPONSE]) == []

    def test_parse_search_contacts_response_skips_unrelated_responses_in_batch(self):
        assert _JMAPClientBase._parse_search_contacts_response([_UNRELATED_RESPONSE]) == []

    def test_first_matching_list_returns_parser_result_for_matching_response(self):
        matching = ("Widget/get", {"list": ["a", "b"]}, "c1")
        result = _JMAPClientBase._first_matching_list(
            [_UNRELATED_RESPONSE, matching], "Widget/get", lambda args: args["list"]
        )
        assert result == ["a", "b"]

    def test_parse_get_task_response_skips_unrelated_responses_in_batch(self):
        with pytest.raises(JMAPMethodError, match="No Task/get response"):
            _JMAPClientBase._parse_get_task_response(
                [_UNRELATED_RESPONSE], api_url=_API_URL, task_id="t1"
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
            [_UNRELATED_RESPONSE, get_response], ["ev1"], [], [], "new-state"
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
        assert params is not None
        assert params["url"] == _JMAP_URL
        assert params["username"] == _USERNAME

    def test_yaml_file_drops_keys_outside_conn_keys(self, monkeypatch, tmp_path):
        for var in ("JMAP_URL", "JMAP_USERNAME", "JMAP_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        config_file = tmp_path / "calendar.yaml"
        config_file.write_text(f"url: {_JMAP_URL}\nirrelevant_key: something\n")
        params = get_connection_params(config_file=config_file)
        assert params is not None
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
        assert params is not None
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
        assert params is not None
        assert params["url"] == _JMAP_URL


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
)
from calendaring_jmap._methods.task import (
    build_task_get,
    build_task_list_get,
    build_task_set_create,
    build_task_set_destroy,
    build_task_set_update,
    parse_task_list_get,
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
        assert "expandRecurrences" not in args
        assert "timeZone" not in args

    def test_build_event_query_with_expand_recurrences(self):
        _, args, _ = build_event_query("u1", expand_recurrences=True)
        assert args["expandRecurrences"] is True

    def test_build_event_query_expand_recurrences_false_omits_key(self):
        _, args, _ = build_event_query("u1", expand_recurrences=False)
        assert "expandRecurrences" not in args

    def test_build_event_query_with_time_zone(self):
        _, args, _ = build_event_query("u1", time_zone="America/New_York")
        assert args["timeZone"] == "America/New_York"

    def test_build_event_get_by_query_result_structure(self):
        method, args, call_id = build_event_get_by_query_result("u1")
        assert method == "CalendarEvent/get"
        assert args["accountId"] == "u1"
        assert isinstance(call_id, str)

    def test_build_event_get_by_query_result_references_query_call(self):
        query_method, _, query_call_id = build_event_query("u1")
        _, args, _ = build_event_get_by_query_result("u1")
        assert args["#ids"] == {
            "resultOf": query_call_id,
            "name": query_method,
            "path": "/ids",
        }

    def test_build_event_get_by_query_result_with_properties(self):
        _, args, _ = build_event_get_by_query_result("u1", properties=["start", "duration"])
        assert args["properties"] == ["start", "duration"]

    def test_build_event_get_by_query_result_no_properties_key_when_not_set(self):
        _, args, _ = build_event_get_by_query_result("u1")
        assert "properties" not in args

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
        assert args["sendSchedulingMessages"] is False

    def test_build_event_set_create_with_scheduling_messages(self):
        _, args, _ = build_event_set_create("u1", {"new-1": {}}, send_scheduling_messages=True)
        assert args["sendSchedulingMessages"] is True

    def test_build_event_set_update_structure(self):
        method, args, call_id = build_event_set_update("u1", {"ev1": {"title": "Updated title"}})
        assert method == "CalendarEvent/set"
        assert args["update"] == {"ev1": {"title": "Updated title"}}
        assert args["sendSchedulingMessages"] is False

    def test_build_event_set_update_with_scheduling_messages(self):
        _, args, _ = build_event_set_update("u1", {"ev1": {}}, send_scheduling_messages=True)
        assert args["sendSchedulingMessages"] is True

    def test_build_event_set_destroy_structure(self):
        method, args, call_id = build_event_set_destroy("u1", ["ev1", "ev2"])
        assert method == "CalendarEvent/set"
        assert args["destroy"] == ["ev1", "ev2"]
        assert args["sendSchedulingMessages"] is False

    def test_build_event_set_destroy_with_scheduling_messages(self):
        _, args, _ = build_event_set_destroy("u1", ["ev1"], send_scheduling_messages=True)
        assert args["sendSchedulingMessages"] is True

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


from calendaring_jmap._methods.principal import build_get_availability, parse_get_availability


class TestPrincipalMethodBuilders:
    def test_build_get_availability_structure(self):
        method, args, call_id = build_get_availability(
            "principal1", "2024-01-01T00:00:00Z", "2024-01-08T00:00:00Z"
        )
        assert method == "Principal/getAvailability"
        assert args["id"] == "principal1"
        assert args["utcStart"] == "2024-01-01T00:00:00Z"
        assert args["utcEnd"] == "2024-01-08T00:00:00Z"
        assert isinstance(call_id, str)

    def test_build_get_availability_has_no_account_id(self):
        # Confirmed live against Cyrus: accountId in this method's own args
        # dict fails with invalidArguments, even with a correct value.
        _, args, _ = build_get_availability(
            "principal1", "2024-01-01T00:00:00Z", "2024-01-08T00:00:00Z"
        )
        assert "accountId" not in args

    def test_build_get_availability_no_optional_keys_when_not_set(self):
        _, args, _ = build_get_availability(
            "principal1", "2024-01-01T00:00:00Z", "2024-01-08T00:00:00Z"
        )
        assert "showDetails" not in args
        assert "eventProperties" not in args

    def test_build_get_availability_with_show_details(self):
        _, args, _ = build_get_availability(
            "principal1", "2024-01-01T00:00:00Z", "2024-01-08T00:00:00Z", show_details=True
        )
        assert args["showDetails"] is True

    def test_build_get_availability_show_details_false_omits_key(self):
        _, args, _ = build_get_availability(
            "principal1", "2024-01-01T00:00:00Z", "2024-01-08T00:00:00Z", show_details=False
        )
        assert "showDetails" not in args

    def test_build_get_availability_with_event_properties(self):
        _, args, _ = build_get_availability(
            "principal1",
            "2024-01-01T00:00:00Z",
            "2024-01-08T00:00:00Z",
            event_properties=["id", "title"],
        )
        assert args["eventProperties"] == ["id", "title"]

    def test_parse_get_availability_returns_busy_periods(self):
        response_args = {
            "list": [
                {
                    "utcStart": "2024-01-01T09:00:00Z",
                    "utcEnd": "2024-01-01T10:00:00Z",
                    "busyStatus": "confirmed",
                    "event": None,
                    "accountId": None,
                }
            ]
        }
        periods = parse_get_availability(response_args)
        assert len(periods) == 1
        assert periods[0]["busyStatus"] == "confirmed"

    def test_parse_get_availability_empty_list(self):
        assert parse_get_availability({"list": []}) == []

    def test_parse_get_availability_missing_list_key(self):
        assert parse_get_availability({}) == []


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


class TestAsList:
    def test_none_becomes_empty_list(self):
        from calendaring_jmap.convert.ical_to_jscal import _as_list

        assert _as_list(None) == []

    def test_single_value_becomes_one_item_list(self):
        from calendaring_jmap.convert.ical_to_jscal import _as_list

        assert _as_list("x") == ["x"]

    def test_list_passes_through_unchanged(self):
        from calendaring_jmap.convert.ical_to_jscal import _as_list

        assert _as_list(["a", "b"]) == ["a", "b"]


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

    def test_version_is_2_0(self):
        """RFC 8984 has no version property; jscalendarbis adds it and
        registers "1.0" for RFC 8984-conformant data, "2.0" for its own.
        This converter's output is actually RFC 8984-shaped ("1.0" would be
        strictly accurate), but it sends "2.0" since the test servers this
        project runs against require it. See ical_to_jscal's inline comment
        for the full reasoning."""
        ical = _make_ical("DTSTART:20240615T100000Z\r\nDURATION:PT1H\r\nSUMMARY:Test Event\r\n")
        result = ical_to_jscal(ical)
        assert result["version"] == "2.0"

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

    def test_windows_tzid_is_normalized_to_iana(self):
        """Outlook emits Windows timezone names; RFC 8984 requires an IANA
        name in timeZone, so the raw TZID param must be resolved, not
        passed through."""
        ical = _make_ical(
            "DTSTART;TZID=Eastern Standard Time:20240615T100000\r\n"
            "DURATION:PT1H\r\nSUMMARY:Windows TZ Event\r\n"
        )
        result = ical_to_jscal(ical)
        assert result["timeZone"] == "America/New_York"

    def test_globally_unique_tzid_is_normalized_to_iana(self):
        """Evolution/Mozilla Lightning-style vendor-prefixed TZIDs
        (RFC 5545 section 3.2.19) must resolve to their IANA suffix."""
        ical = _make_ical(
            "DTSTART;TZID=/freeassociation.sourceforge.net/Europe/Berlin:20240615T100000\r\n"
            "DURATION:PT1H\r\nSUMMARY:Globally Unique TZID Event\r\n"
        )
        result = ical_to_jscal(ical)
        assert result["timeZone"] == "Europe/Berlin"

    def test_custom_vtimezone_tzid_passes_through(self):
        """A TZID naming the event's own embedded VTIMEZONE (not a known
        IANA/Windows name) isn't something tzid_from_dt can normalize
        further, so it passes through unchanged."""
        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//Test//EN\r\n"
            "BEGIN:VTIMEZONE\r\nTZID:Custom/MyOffice\r\n"
            "BEGIN:STANDARD\r\nDTSTART:19701101T020000\r\n"
            "TZOFFSETFROM:-0400\r\nTZOFFSETTO:-0500\r\nEND:STANDARD\r\n"
            "END:VTIMEZONE\r\n"
            "BEGIN:VEVENT\r\nUID:custom-tz@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
            "DTSTART;TZID=Custom/MyOffice:20240615T100000\r\n"
            "DURATION:PT1H\r\nSUMMARY:Custom VTIMEZONE Event\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        result = ical_to_jscal(ical)
        assert result["timeZone"] == "Custom/MyOffice"

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
        # Two separate CATEGORIES lines: icalendar returns a list of vCategory objects
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
        assert "recurrenceRule" in result
        rule = result["recurrenceRule"]
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
        nday = result["recurrenceRule"]["byDay"][0]
        assert nday["day"] == "mo"
        assert nday["nthOfPeriod"] == 2

    def test_rrule_all_by_components(self):
        ical = _make_ical(
            "DTSTART:20240617T140000Z\r\nDURATION:PT1H\r\nSUMMARY:Complex\r\n"
            "RRULE:FREQ=YEARLY;BYMONTH=6;BYMONTHDAY=15;BYYEARDAY=166;"
            "BYWEEKNO=24;BYHOUR=14;BYMINUTE=30;BYSECOND=15;BYSETPOS=1\r\n"
        )
        result = ical_to_jscal(ical)
        rule = result["recurrenceRule"]
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

    def test_attach_uri_form(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\n"
            "SUMMARY:Attach Event\r\n"
            "ATTACH:https://example.com/foo.pdf\r\n"
        )
        result = ical_to_jscal(ical)
        assert "links" in result
        link = next(iter(result["links"].values()))
        assert link["@type"] == "Link"
        assert link["rel"] == "enclosure"
        assert link["href"] == "https://example.com/foo.pdf"
        assert "contentType" not in link

    def test_attach_binary_form(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\n"
            "SUMMARY:Attach Event\r\n"
            "ATTACH;FMTTYPE=image/png;ENCODING=BASE64;VALUE=BINARY:iVBORw0KGgo=\r\n"
        )
        result = ical_to_jscal(ical)
        link = next(iter(result["links"].values()))
        assert link["rel"] == "enclosure"
        assert link["href"] == "data:image/png;base64,iVBORw0KGgo="
        assert link["contentType"] == "image/png"

    def test_attach_binary_form_without_fmttype(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\n"
            "SUMMARY:Attach Event\r\n"
            "ATTACH;ENCODING=BASE64;VALUE=BINARY:iVBORw0KGgo=\r\n"
        )
        result = ical_to_jscal(ical)
        link = next(iter(result["links"].values()))
        assert link["href"] == "data:;base64,iVBORw0KGgo="
        assert "contentType" not in link

    def test_multiple_attach_properties(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\n"
            "SUMMARY:Multi Attach Event\r\n"
            "ATTACH:https://example.com/foo.pdf\r\n"
            "ATTACH:https://example.com/bar.pdf\r\n"
        )
        result = ical_to_jscal(ical)
        assert len(result["links"]) == 2
        hrefs = {link["href"] for link in result["links"].values()}
        assert hrefs == {"https://example.com/foo.pdf", "https://example.com/bar.pdf"}

    def test_no_attach_omits_links(self):
        ical = _make_ical("DTSTART:20240615T100000Z\r\nSUMMARY:No Attach Event\r\n")
        result = ical_to_jscal(ical)
        assert "links" not in result

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

    def test_organizer_attendee_use_calendar_address_not_send_to(self):
        # Cyrus rejects CalendarEvent/set outright if any Participant has a
        # sendTo property, and separately requires calendarAddress, which
        # RFC 8984 does not define here at all. Verified live against Cyrus.
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\n"
            "SUMMARY:Meeting\r\n"
            "ORGANIZER:mailto:alice@example.com\r\n"
            "ATTENDEE:mailto:bob@example.com\r\n"
        )
        result = ical_to_jscal(ical)
        for participant in result["participants"].values():
            assert "sendTo" not in participant
            assert participant["calendarAddress"] == f"mailto:{participant['email']}"

    def test_non_mailto_attendee_keeps_calendar_address_without_fake_email(self):
        # CAL-ADDRESS is a URI (RFC 5545 3.3.3), not required to use
        # mailto:. RFC 8984's Participant.email is an RFC 5322 addr-spec,
        # so a non-mailto URI must not be written there.
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Meeting\r\nATTENDEE:sip:alice@example.com\r\n"
        )
        result = ical_to_jscal(ical)
        attendee = next(iter(result["participants"].values()))
        assert attendee["calendarAddress"] == "sip:alice@example.com"
        assert "email" not in attendee

    def test_bare_attendee_address_without_scheme_is_treated_as_email(self):
        # CAL-ADDRESS is technically required to be a URI, but some
        # real-world calendar data omits the mailto: scheme entirely.
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Meeting\r\nATTENDEE:alice@example.com\r\n"
        )
        result = ical_to_jscal(ical)
        attendee = next(iter(result["participants"].values()))
        assert attendee["calendarAddress"] == "mailto:alice@example.com"
        assert attendee["email"] == "alice@example.com"

    def test_non_mailto_organizer_keeps_calendar_address_without_fake_email(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Meeting\r\nORGANIZER:sip:bob@example.com\r\n"
        )
        result = ical_to_jscal(ical)
        organizer = next(iter(result["participants"].values()))
        assert organizer["calendarAddress"] == "sip:bob@example.com"
        assert "email" not in organizer

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

    def test_valarm_without_trigger_is_skipped(self):
        # TRIGGER is mandatory (RFC 5545 section 3.6.6, RFC 8984 section
        # 4.5.2); a VALARM missing it is skipped rather than emitted as a
        # spec-invalid Alert with no "trigger" key.
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:No Trigger\r\n"
            "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Reminder\r\nEND:VALARM\r\n"
        )
        result = ical_to_jscal(ical)
        assert "alerts" not in result

    def test_valarm_without_trigger_does_not_drop_other_valid_alarms(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nSUMMARY:Mixed Alarms\r\n"
            "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:No Trigger\r\nEND:VALARM\r\n"
            "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT15M\r\nEND:VALARM\r\n"
        )
        result = ical_to_jscal(ical)
        assert len(result["alerts"]) == 1
        alert = next(iter(result["alerts"].values()))
        assert alert["trigger"] == "-PT15M"

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

    def test_rrule_multiple_lines_keeps_only_the_first(self, caplog):
        # Only the first RRULE is kept when a VEVENT has more than one; see
        # the comment in ical_to_jscal.py next to "recurrenceRule" for why
        # (neither test server this repo targets accepts RFC 8984's actual
        # RecurrenceRule[] array, only a single object). This is a real data
        # loss case, so it must be logged, not silent.
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nDURATION:PT1H\r\nSUMMARY:Multi RRULE\r\n"
            "RRULE:FREQ=DAILY\r\nRRULE:FREQ=WEEKLY\r\n"
        )
        with caplog.at_level("WARNING"):
            result = ical_to_jscal(ical)
        assert result["recurrenceRule"]["frequency"] == "daily"
        assert "only the first is kept" in caplog.text

    def test_exrule_multiple_lines_keeps_only_the_first(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nDURATION:PT1H\r\nSUMMARY:Multi EXRULE\r\n"
            "RRULE:FREQ=DAILY\r\nEXRULE:FREQ=DAILY;BYDAY=SU\r\nEXRULE:FREQ=DAILY;BYDAY=SA\r\n"
        )
        result = ical_to_jscal(ical)
        assert result["excludedRecurrenceRule"]["byDay"][0]["day"] == "su"

    def test_valarm_trigger_neither_duration_nor_datetime(self):
        from calendaring_jmap.convert.ical_to_jscal import _valarm_to_alert

        alarm = MagicMock()
        alarm.get.side_effect = lambda key, default=None: {
            "ACTION": "DISPLAY",
            "TRIGGER": MagicMock(dt="not a duration or datetime"),
            "DESCRIPTION": None,
        }.get(key, default)
        converted = _valarm_to_alert(alarm)
        assert converted is not None
        _, alert = converted
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

    def test_missing_uid_raises(self):
        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
            "DTSTAMP:20240101T000000Z\r\nDTSTART:20240615T100000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        with pytest.raises(ValueError, match="missing the mandatory UID"):
            ical_to_jscal(ical)

    def test_missing_dtstart_raises(self):
        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
            "UID:no-dtstart@example.com\r\nDTSTAMP:20240101T000000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        with pytest.raises(ValueError, match="missing the mandatory DTSTART"):
            ical_to_jscal(ical)

    def test_dtend_before_dtstart_raises(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nDTEND:20240615T090000Z\r\n",
            uid="negative-duration@example.com",
        )
        with pytest.raises(ValueError, match="negative duration"):
            ical_to_jscal(ical)

    def test_mismatched_dtstart_dtend_value_types_raises(self):
        ical = _make_ical(
            "DTSTART;VALUE=DATE:20240615\r\nDTEND:20240615T090000Z\r\n",
            uid="mismatched-types@example.com",
        )
        with pytest.raises(ValueError, match="mismatched DTSTART/DTEND value types"):
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
        assert result["excludedRecurrenceRule"]["frequency"] == "daily"

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

    def test_link_enclosure_uri_form(self):
        jscal = _minimal_jscal(
            links={
                "l1": {
                    "@type": "Link",
                    "href": "https://example.com/foo.pdf",
                    "rel": "enclosure",
                }
            }
        )
        result = jscal_to_ical(jscal)
        assert "ATTACH:https://example.com/foo.pdf" in result

    def test_link_enclosure_data_url_becomes_binary_attach(self):
        jscal = _minimal_jscal(
            links={
                "l1": {
                    "@type": "Link",
                    "href": "data:image/png;base64,iVBORw0KGgo=",
                    "rel": "enclosure",
                }
            }
        )
        result = jscal_to_ical(jscal)
        assert "ENCODING=BASE64" in result
        assert "VALUE=BINARY" in result
        assert "FMTTYPE=image/png" in result
        assert "iVBORw0KGgo=" in result

    def test_link_enclosure_without_href_not_converted(self):
        jscal = _minimal_jscal(links={"l1": {"@type": "Link", "rel": "enclosure"}})
        result = jscal_to_ical(jscal)
        assert "ATTACH" not in result

    def test_link_non_enclosure_rel_not_converted(self):
        jscal = _minimal_jscal(
            links={
                "l1": {
                    "@type": "Link",
                    "href": "https://example.com/conference",
                    "rel": "describedby",
                }
            }
        )
        result = jscal_to_ical(jscal)
        assert "ATTACH" not in result

    def test_no_links_omits_attach(self):
        jscal = _minimal_jscal()
        result = jscal_to_ical(jscal)
        assert "ATTACH" not in result

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

    def test_participant_falls_back_to_calendar_address_without_send_to(self):
        # ical_to_jscal no longer emits sendTo (see the matching ical_to_jscal
        # test); the reverse direction must still round-trip a participant
        # that only has calendarAddress, no sendTo, no email.
        jscal = _minimal_jscal(
            participants={
                "p1": {
                    "roles": {"attendee": True},
                    "calendarAddress": "mailto:carol@example.com",
                }
            }
        )
        result = jscal_to_ical(jscal)
        # Long ATTENDEE lines get folded per RFC 5545, so join continuation
        # lines back together before checking for the address.
        assert "mailto:carol@example.com" in result.replace("\n ", "")

    def test_non_mailto_calendar_address_is_not_double_wrapped(self):
        # calendarAddress can carry a non-mailto URI scheme (e.g. sip:);
        # it must round-trip as-is, not get a spurious mailto: prepended.
        jscal = _minimal_jscal(
            participants={
                "p1": {
                    "roles": {"attendee": True},
                    "calendarAddress": "sip:alice@example.com",
                }
            }
        )
        result = jscal_to_ical(jscal)
        joined = result.replace("\n ", "")
        assert "sip:alice@example.com" in joined
        assert "mailto:sip:alice@example.com" not in joined

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

    def test_missing_uid_raises(self):
        jscal = {
            "title": "No UID",
            "start": "2024-06-15T10:00:00",
            "timeZone": "Europe/Berlin",
            "duration": "PT1H",
        }
        with pytest.raises(ValueError, match="missing the mandatory 'uid'"):
            jscal_to_ical(jscal)

    def test_empty_uid_raises(self):
        jscal = _minimal_jscal(uid="")
        with pytest.raises(ValueError, match="missing the mandatory 'uid'"):
            jscal_to_ical(jscal)

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

    def test_recurrence_rules_array_wins_over_singular_when_both_present(self):
        # A spec-compliant server could send both keys; the array is the
        # real RFC 8984 type and can carry more than one rule, so it must
        # not be silently discarded in favor of the singular key.
        jscal = _minimal_jscal(
            recurrenceRule={"@type": "RecurrenceRule", "frequency": "daily"},
            recurrenceRules=[
                {"@type": "RecurrenceRule", "frequency": "weekly"},
                {"@type": "RecurrenceRule", "frequency": "monthly"},
            ],
        )
        result = jscal_to_ical(jscal)
        assert "RRULE:FREQ=WEEKLY" in result
        assert "RRULE:FREQ=MONTHLY" in result
        assert "FREQ=DAILY" not in result

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
        """ical to jscal to ical, then parse back and check."""
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
        assert "recurrenceRule" in ctx["jscal"]
        assert ctx["jscal"]["recurrenceRule"]["frequency"] == "weekly"
        assert "RRULE" in ctx["ical"]

    def test_non_mailto_attendee_round_trip(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\nDURATION:PT1H\r\nSUMMARY:SIP Attendee\r\n"
            "ATTENDEE:sip:alice@example.com\r\n"
        )
        ctx = self._key_fields_survive(ical)
        attendee = str(ctx["event"]["ATTENDEE"])
        assert attendee == "sip:alice@example.com"

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

    def test_with_attach_round_trip(self):
        ical = _make_ical(
            "DTSTART:20240615T100000Z\r\n"
            "DURATION:PT1H\r\n"
            "SUMMARY:Attach Event\r\n"
            "ATTACH;FMTTYPE=application/pdf:https://example.com/report.pdf\r\n"
        )
        ctx = self._key_fields_survive(ical)
        assert "links" in ctx["jscal"]
        link = next(iter(ctx["jscal"]["links"].values()))
        assert link["href"] == "https://example.com/report.pdf"
        assert link["rel"] == "enclosure"
        assert link["contentType"] == "application/pdf"
        assert "ATTACH" in ctx["ical"]
        assert "https://example.com/report.pdf" in ctx["ical"]

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


def _set_response(method_name: str, call_id: str, **kwargs) -> dict:
    """A minimal ``<Object>/set`` response envelope: one methodResponse
    wrapping whatever ``created``/``updated``/``notCreated``/etc. kwargs
    the test supplies verbatim. Shared by every mocked-client test class,
    sync (via ``_MockedClientMixin``) and async (``TestAsyncJMAPClient``) alike."""
    return {"methodResponses": [[method_name, kwargs, call_id]]}


def _get_response(method_name: str, call_id: str, items: list[dict]) -> dict:
    """A minimal ``<Object>/get`` response envelope: one methodResponse
    with ``list``/``notFound``. Shared the same way as :func:`_set_response`."""
    return {
        "methodResponses": [
            [method_name, {"accountId": _USERNAME, "list": items, "notFound": []}, call_id]
        ]
    }


def _session_with_capabilities(account_capabilities, server_capabilities=None, state="state-abc"):
    """Build a ``Session`` for a capability-gated code path test (free/busy,
    contacts). ``server_capabilities`` defaults to the same keys as
    ``account_capabilities``: every existing caller already means "this
    account, on a server that supports this capability at all", so this
    keeps their meaning unchanged. Pass it explicitly to test the
    server-wide-unsupported case specifically (distinct from this account
    not having it on a server that otherwise does)."""
    return Session(
        api_url=_API_URL,
        account_id=_USERNAME,
        state=state,
        account_capabilities=account_capabilities,
        server_capabilities=(
            server_capabilities if server_capabilities is not None else account_capabilities
        ),
    )


class _MockedClientMixin:
    """Shared client/response mocking for tests that drive JMAPClient
    through a mocked ``_http_session`` rather than real HTTP calls."""

    def _make_mock(self, resp_json):
        return _make_mock_response(resp_json)

    def _make_client(self):
        return _make_client()

    def _mock_http(self, client, response=None, side_effect=None):
        mock_http = MagicMock()
        if side_effect is not None:
            mock_http.post.side_effect = side_effect
        elif response is not None:
            mock_http.post.return_value = response
        client._http_session = mock_http
        return mock_http

    def _capturing_client_for(self, client, resp) -> dict:
        """Like :meth:`_capturing_client`, but for an already-built ``client``."""
        captured: dict = {}

        def capturing_post(*args, **kwargs):
            captured["json"] = kwargs.get("json", {})
            return self._make_mock(resp)

        self._mock_http(client, side_effect=capturing_post)
        return captured

    def _capturing_client(self, monkeypatch, resp):
        """Return (client, captured) where captured["json"] is set on each POST."""
        client = self._make_client()
        captured = self._capturing_client_for(client, resp)
        return client, captured

    def _client_with_capabilities(self, account_capabilities, server_capabilities=None):
        client = JMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        client._session_cache = _session_with_capabilities(
            account_capabilities, server_capabilities
        )
        return client


def _query_get_response(
    items: list[dict],
    query_method: str = "CalendarEvent/query",
    query_call_id: str = "ev-query-0",
    get_method: str = "CalendarEvent/get",
    get_call_id: str = "ev-get-1",
) -> dict:
    """Batched [<query_method>, <get_method>] response envelope, ids derived
    from ``items``. Shared by every test that exercises a search-shaped
    call (``TestJMAPCalendar``, ``TestJMAPClientEvents``, ``TestJMAPClientContacts``
    and its async sibling)."""
    query_args: dict = {
        "ids": [i["id"] for i in items],
        "queryState": "qs-1",
        "total": len(items),
    }
    return {
        "methodResponses": [
            [query_method, query_args, query_call_id],
            [get_method, {"accountId": _USERNAME, "list": items, "notFound": []}, get_call_id],
        ]
    }


def _participant_event(
    raw_event: dict, own_email: str = "me@example.com", participant_id: str = "p1"
) -> dict:
    """Build a raw event dict with two participants: ``own_email`` as an
    attendee under ``participant_id``, plus a fixed organizer. Shared by the
    sync and async scheduling tests, which otherwise wrap it in different
    response-envelope helpers (``_get_response``/``_event_get_resp``)."""
    return {
        **raw_event,
        "participants": {
            participant_id: {
                "@type": "Participant",
                "email": own_email,
                "roles": {"attendee": True},
            },
            "p-organizer": {
                "@type": "Participant",
                "email": "organizer@example.com",
                "roles": {"owner": True},
            },
        },
    }


class TestJMAPClientEvents(_MockedClientMixin):
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
        return _set_response("CalendarEvent/set", "ev-set-create-0", **kwargs)

    def _get_response(self, items):
        return _get_response("CalendarEvent/get", "ev-get-0", items)

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

    def test_get_event_uses_given_account_id(self, monkeypatch):
        client, captured = self._capturing_client(monkeypatch, self._get_response([]))
        with pytest.raises(JMAPMethodError):
            client.get_event("ev1", account_id="owner-account")
        get_args = captured["json"]["methodCalls"][0][1]
        assert get_args["accountId"] == "owner-account"

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
        # RFC 8620 section 3.3: absent keys in a PatchObject preserve the server value.
        # To actually delete a property the patch must set it to null.
        # An ical-to-jscal conversion that omits LOCATION/DESCRIPTION must send
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
        # update_event must drop each reported null-cleanup key and retry
        # until the update succeeds, never failing on harmless cleanup.
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
            reject("recurrenceRule"),
            reject("excludedRecurrenceRule"),
            self._set_response(updated={"ev1": None}),
        ]
        client, captured = self._sequence_client(responses)
        client.update_event("ev1", self._MINIMAL_ICAL)

        assert len(captured["patches"]) == 3
        # First attempt nulled both recurrence keys; the final accepted patch dropped them.
        assert captured["patches"][0]["recurrenceRule"] is None
        assert "recurrenceRule" not in captured["patches"][2]
        assert "excludedRecurrenceRule" not in captured["patches"][2]

    def test_update_event_does_not_drop_explicitly_set_property(self, monkeypatch):
        # If the rejected property was actually assigned a value by the client
        # (not null-cleanup), the rejection is genuine and must surface, no retry.
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

    def test_update_event_uses_given_account_id(self, monkeypatch):
        resp = self._set_response(updated={"ev1": None})
        client, captured = self._capturing_client(monkeypatch, resp)
        client.update_event("ev1", self._MINIMAL_ICAL, account_id="owner-account")
        update_args = captured["json"]["methodCalls"][0][1]
        assert update_args["accountId"] == "owner-account"

    def test_delete_event_uses_given_account_id(self, monkeypatch):
        resp = self._set_response(destroyed=["ev1"])
        client, captured = self._capturing_client(monkeypatch, resp)
        client.delete_event("ev1", account_id="owner-account")
        destroy_args = captured["json"]["methodCalls"][0][1]
        assert destroy_args["accountId"] == "owner-account"

    def test_delete_event_sends_scheduling_messages(self, monkeypatch):
        resp = self._set_response(destroyed=["ev1"])
        client, captured = self._capturing_client(monkeypatch, resp)
        client.delete_event("ev1", send_scheduling_messages=True)
        destroy_args = captured["json"]["methodCalls"][0][1]
        assert destroy_args["sendSchedulingMessages"] is True

    def test_send_invite_returns_server_id(self, monkeypatch):
        resp = self._set_response(created={"new-0": {"id": "sv-1"}})
        client = _make_client_with_mocked_session(monkeypatch, resp)
        event_id = client.send_invite("cal1", self._MINIMAL_ICAL)
        assert event_id == "sv-1"

    def test_send_invite_sends_scheduling_messages(self, monkeypatch):
        resp = self._set_response(created={"new-0": {"id": "sv-1"}})
        client, captured = self._capturing_client(monkeypatch, resp)
        client.send_invite("cal1", self._MINIMAL_ICAL)
        create_args = captured["json"]["methodCalls"][0][1]
        assert create_args["sendSchedulingMessages"] is True

    def test_send_invite_raises_no_supported_schedule_methods(self, monkeypatch):
        resp = self._set_response(notCreated={"new-0": {"type": "noSupportedScheduleMethods"}})
        client = _make_client_with_mocked_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            client.send_invite("cal1", self._MINIMAL_ICAL)
        assert exc_info.value.error_type == "noSupportedScheduleMethods"

    def _participant_event_response(self, own_email="me@example.com", participant_id="p1"):
        return self._get_response([_participant_event(self._RAW_EVENT, own_email, participant_id)])

    def test_find_own_participant_id_matches_case_insensitively(self, monkeypatch):
        resp = self._participant_event_response(own_email="Me@Example.com")
        client = _make_client_with_mocked_session(monkeypatch, resp)
        participant_id = client._find_own_participant_id("ev1", "me@example.com")
        assert participant_id == "p1"

    def test_find_own_participant_id_raises_when_no_match(self, monkeypatch):
        resp = self._participant_event_response()
        client = _make_client_with_mocked_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            client._find_own_participant_id("ev1", "stranger@example.com")
        assert exc_info.value.error_type == "notFound"

    def test_find_own_participant_id_falls_back_to_calendar_address(self, monkeypatch):
        # A participant with no "email" (a spec-compliant server, or one set
        # by another client) must still be matchable via calendarAddress.
        resp = self._get_response(
            [
                {
                    **self._RAW_EVENT,
                    "participants": {
                        "p1": {
                            "@type": "Participant",
                            "calendarAddress": "mailto:me@example.com",
                            "roles": {"attendee": True},
                        },
                    },
                }
            ]
        )
        client = _make_client_with_mocked_session(monkeypatch, resp)
        participant_id = client._find_own_participant_id("ev1", "me@example.com")
        assert participant_id == "p1"

    def test_find_own_participant_id_raises_not_null_email_and_calendar_address(self, monkeypatch):
        # dict.get(key, default) only substitutes the default when the key
        # is absent, not when it is present and explicitly null. A
        # participant shaped this way must still raise notFound cleanly,
        # not crash with an AttributeError from calling str methods on None.
        resp = self._get_response(
            [
                {
                    **self._RAW_EVENT,
                    "participants": {
                        "p1": {
                            "@type": "Participant",
                            "email": "",
                            "calendarAddress": None,
                            "roles": {"attendee": True},
                        },
                    },
                }
            ]
        )
        client = _make_client_with_mocked_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            client._find_own_participant_id("ev1", "me@example.com")
        assert exc_info.value.error_type == "notFound"

    def test_find_own_participant_id_matches_non_mailto_calendar_address(self, monkeypatch):
        # A participant converted from a non-mailto CAL-ADDRESS (e.g. sip:)
        # has no "email" at all; matching must still work via calendarAddress.
        resp = self._get_response(
            [
                {
                    **self._RAW_EVENT,
                    "participants": {
                        "p1": {
                            "@type": "Participant",
                            "calendarAddress": "sip:alice@example.com",
                            "roles": {"attendee": True},
                        },
                    },
                }
            ]
        )
        client = _make_client_with_mocked_session(monkeypatch, resp)
        participant_id = client._find_own_participant_id("ev1", "sip:alice@example.com")
        assert participant_id == "p1"

    def _accept_sequence_client(self, monkeypatch, own_email="me@example.com", set_response=None):
        """Return (client, captured) where the first POST answers CalendarEvent/get
        with a participant event and the second answers CalendarEvent/set."""
        get_resp = self._participant_event_response(own_email=own_email)
        set_resp = (
            set_response if set_response is not None else self._set_response(updated={"ev1": None})
        )
        responses = iter([get_resp, set_resp])
        captured: dict = {"payloads": []}
        client = JMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        client._session_cache = Session(api_url=_API_URL, account_id=_USERNAME, state="state-abc")

        def post(*args, **kwargs):
            captured["payloads"].append(kwargs.get("json"))
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = next(responses)
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_http = MagicMock()
        mock_http.post.side_effect = post
        client._http_session = mock_http
        return client, captured

    def test_accept_invitation_patches_own_participant(self, monkeypatch):
        client, captured = self._accept_sequence_client(monkeypatch)
        client.accept_invitation("ev1", "me@example.com")
        update_args = captured["payloads"][1]["methodCalls"][0][1]
        assert update_args["update"]["ev1"] == {"participants/p1/participationStatus": "accepted"}
        assert update_args["sendSchedulingMessages"] is True

    def test_accept_invitation_fetches_only_participants(self, monkeypatch):
        client, captured = self._accept_sequence_client(monkeypatch)
        client.accept_invitation("ev1", "me@example.com")
        get_args = captured["payloads"][0]["methodCalls"][0][1]
        assert get_args["properties"] == ["participants"]

    def test_decline_invitation_patches_own_participant(self, monkeypatch):
        client, captured = self._accept_sequence_client(monkeypatch)
        client.decline_invitation("ev1", "me@example.com")
        update_args = captured["payloads"][1]["methodCalls"][0][1]
        assert update_args["update"]["ev1"] == {"participants/p1/participationStatus": "declined"}

    def test_tentatively_accept_patches_own_participant(self, monkeypatch):
        client, captured = self._accept_sequence_client(monkeypatch)
        client.tentatively_accept("ev1", "me@example.com")
        update_args = captured["payloads"][1]["methodCalls"][0][1]
        assert update_args["update"]["ev1"] == {"participants/p1/participationStatus": "tentative"}

    def test_accept_invitation_raises_when_no_matching_participant(self, monkeypatch):
        resp = self._participant_event_response()
        client = _make_client_with_mocked_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            client.accept_invitation("ev1", "stranger@example.com")
        assert exc_info.value.error_type == "notFound"

    def test_accept_invitation_raises_on_server_rejection(self, monkeypatch):
        rejection = self._set_response(notUpdated={"ev1": {"type": "forbidden"}})
        client, _ = self._accept_sequence_client(monkeypatch, set_response=rejection)
        with pytest.raises(JMAPMethodError) as exc_info:
            client.accept_invitation("ev1", "me@example.com")
        assert exc_info.value.error_type == "forbidden"

    def test_search_events_returns_ical_list(self, monkeypatch):
        event2 = {**self._RAW_EVENT, "id": "ev2", "title": "Standup"}
        resp = _query_get_response([self._RAW_EVENT, event2])
        client = _make_client_with_mocked_session(monkeypatch, resp)
        results = client.search_events()
        assert len(results) == 2
        assert all(isinstance(r, JMAPCalendarObject) for r in results)
        assert all(r.parent is None for r in results)

    def test_search_events_empty_result(self, monkeypatch):
        resp = _query_get_response([])
        client = _make_client_with_mocked_session(monkeypatch, resp)
        assert client.search_events() == []

    def test_search_events_passes_calendar_id_filter(self, monkeypatch):
        resp = _query_get_response([self._RAW_EVENT])
        client, captured = self._capturing_client(monkeypatch, resp)
        client.search_events(calendar_id="my-cal")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["inCalendar"] == "my-cal"

    def test_search_events_passes_date_range_filter(self, monkeypatch):
        resp = _query_get_response([self._RAW_EVENT])
        client, captured = self._capturing_client(monkeypatch, resp)
        client.search_events(start="2024-01-01T00:00:00", end="2024-12-31T23:59:59")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["after"] == "2024-01-01T00:00:00"
        assert query_args["filter"]["before"] == "2024-12-31T23:59:59"

    def test_search_events_passes_text_filter(self, monkeypatch):
        resp = _query_get_response([self._RAW_EVENT])
        client, captured = self._capturing_client(monkeypatch, resp)
        client.search_events(text="standup")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"]["text"] == "standup"

    def test_search_events_no_filter_when_no_args(self, monkeypatch):
        resp = _query_get_response([self._RAW_EVENT])
        client, captured = self._capturing_client(monkeypatch, resp)
        client.search_events()
        query_args = captured["json"]["methodCalls"][0][1]
        assert "filter" not in query_args


class _MockedBlobClientMixin:
    """Shared client/response mocking for the raw-HTTP blob upload/download
    methods. Distinct from _MockedClientMixin: those tests mock a JMAP
    methodCalls/methodResponses envelope, but blob upload/download are plain
    HTTP POST/GET with a flat JSON body or raw bytes, so the mocked
    ``_http_session`` here is wired directly instead."""

    def _make_client(self, upload_url=None, download_url=None):
        client = JMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        client._session_cache = Session(
            api_url=_API_URL,
            account_id=_USERNAME,
            state="state-abc",
            upload_url=upload_url,
            download_url=download_url,
        )
        return client

    def _blob_client(self):
        return self._make_client(
            upload_url="/jmap/upload/{accountId}/",
            download_url="/jmap/download/{accountId}/{blobId}/{name}?accept={type}",
        )


class TestJMAPClientAttachments(_MockedBlobClientMixin):
    def test_upload_attachment_posts_bytes_and_returns_blob_id(self):
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.post.return_value = _make_mock_blob_response(
            json_data={"accountId": _USERNAME, "blobId": "G123", "type": "text/plain", "size": 5}
        )
        client._http_session = mock_http
        blob_id = client.upload_attachment(b"hello", "text/plain")
        assert blob_id == "G123"
        call_args = mock_http.post.call_args
        assert call_args.args[0] == f"/jmap/upload/{_USERNAME}/"
        assert call_args.kwargs["data"] == b"hello"
        assert call_args.kwargs["headers"]["Content-Type"] == "text/plain"

    def test_upload_attachment_raises_capability_error_when_no_upload_url(self):
        client = self._make_client()
        with pytest.raises(JMAPCapabilityError):
            client.upload_attachment(b"hello", "text/plain")

    def test_upload_attachment_raises_auth_error_on_401(self):
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.post.return_value = _make_mock_blob_response(status_code=401)
        client._http_session = mock_http
        with pytest.raises(JMAPAuthError):
            client.upload_attachment(b"hello", "text/plain")

    def test_download_attachment_returns_bytes(self):
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.get.return_value = _make_mock_blob_response(content=b"hello")
        client._http_session = mock_http
        data = client.download_attachment("G123", "text/plain", "test.txt")
        assert data == b"hello"
        url = mock_http.get.call_args.args[0]
        assert url == f"/jmap/download/{_USERNAME}/G123/test.txt?accept=text%2Fplain"

    def test_download_attachment_overrides_session_default_accept_header(self):
        """The persistent HTTP session defaults to Accept: application/json
        for JMAP method calls; a blob download's response body is arbitrary
        binary data, so that default must not leak onto this request."""
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.get.return_value = _make_mock_blob_response(content=b"hello")
        client._http_session = mock_http
        client.download_attachment("G123", "text/plain", "test.txt")
        assert mock_http.get.call_args.kwargs["headers"]["Accept"] == "*/*"

    def test_download_attachment_omitted_type_and_name_expand_to_empty(self):
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.get.return_value = _make_mock_blob_response(content=b"hello")
        client._http_session = mock_http
        client.download_attachment("G123")
        url = mock_http.get.call_args.args[0]
        assert url == f"/jmap/download/{_USERNAME}/G123/?accept="

    def test_download_attachment_coalesces_none_content_to_empty_bytes(self):
        """requests.Response.content is typed bytes but can genuinely be
        None (status_code == 0 or raw is None); must not leak past this
        method's own bytes-returning contract."""
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.get.return_value = _make_mock_blob_response(content=None)
        client._http_session = mock_http
        assert client.download_attachment("G123") == b""

    def test_download_attachment_raises_capability_error_when_no_download_url(self):
        client = self._make_client()
        with pytest.raises(JMAPCapabilityError):
            client.download_attachment("G123")

    def test_download_attachment_raises_on_404(self):
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.get.return_value = _make_mock_blob_response(status_code=404)
        client._http_session = mock_http
        with pytest.raises(_http_requests.HTTPError, match="HTTP 404"):
            client.download_attachment("unknown-blob")

    def test_download_attachment_raises_on_403(self):
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.get.return_value = _make_mock_blob_response(status_code=403)
        client._http_session = mock_http
        with pytest.raises(JMAPAuthError):
            client.download_attachment("G123")

    def test_attach_to_event_uses_href_not_blob_id(self, monkeypatch):
        client, captured = self._attach_sequence_client(monkeypatch)
        client.attach_to_event("ev1", "G123", "test.txt", "text/plain")
        update_args = captured["payloads"][1]["methodCalls"][0][1]
        (patch,) = update_args["update"]["ev1"]["links"].values()
        assert "href" in patch
        assert "blobId" not in patch
        assert patch["href"] == f"/jmap/download/{_USERNAME}/G123/test.txt?accept=text%2Fplain"
        assert patch["rel"] == "enclosure"
        assert patch["title"] == "test.txt"
        assert patch["contentType"] == "text/plain"

    def test_attach_to_event_fetches_only_links(self, monkeypatch):
        client, captured = self._attach_sequence_client(monkeypatch)
        client.attach_to_event("ev1", "G123", "test.txt", "text/plain")
        get_args = captured["payloads"][0]["methodCalls"][0][1]
        assert get_args["properties"] == ["links"]

    def test_attach_to_event_preserves_existing_links(self, monkeypatch):
        existing = {"other-l1": {"@type": "Link", "href": "http://x", "rel": "describedby"}}
        client, captured = self._attach_sequence_client(monkeypatch, existing_links=existing)
        client.attach_to_event("ev1", "G123", "test.txt", "text/plain")
        update_args = captured["payloads"][1]["methodCalls"][0][1]
        links = update_args["update"]["ev1"]["links"]
        assert links["other-l1"] == existing["other-l1"]
        assert len(links) == 2

    def _attach_sequence_client(self, monkeypatch, existing_links=None):
        """Return (client, captured) where the first POST answers
        CalendarEvent/get with the event's current links, and the second
        answers CalendarEvent/set. Mirrors _accept_sequence_client's
        get-then-set pattern for a client whose Session also carries
        upload_url/download_url."""
        get_resp = _get_response(
            "CalendarEvent/get", "ev-get-0", [{"id": "ev1", "links": existing_links or {}}]
        )
        set_resp = _set_response("CalendarEvent/set", "ev-set-update-0", updated={"ev1": None})
        responses = iter([get_resp, set_resp])
        captured: dict = {"payloads": []}
        client = self._blob_client()

        def post(*args, **kwargs):
            captured["payloads"].append(kwargs.get("json"))
            return _make_mock_response(next(responses))

        mock_http = MagicMock()
        mock_http.post.side_effect = post
        client._http_session = mock_http
        return client, captured

    def test_get_event_attachments_filters_to_enclosure_links(self, monkeypatch):
        event = {
            "id": "ev1",
            "links": {
                "l1": {"@type": "Link", "href": "http://x/1", "rel": "enclosure", "title": "a.txt"},
                "l2": {"@type": "Link", "href": "http://x/2", "rel": "describedby"},
            },
        }
        resp = _get_response("CalendarEvent/get", "ev-get-0", [event])
        client = _make_client_with_mocked_session(monkeypatch, resp)
        attachments = client.get_event_attachments("ev1")
        assert len(attachments) == 1
        assert attachments[0].link_id == "l1"
        assert attachments[0].title == "a.txt"

    def test_get_event_attachments_empty_when_no_links(self, monkeypatch):
        event = {"id": "ev1"}
        resp = _get_response("CalendarEvent/get", "ev-get-0", [event])
        client = _make_client_with_mocked_session(monkeypatch, resp)
        assert client.get_event_attachments("ev1") == []

    def test_get_event_attachments_empty_when_links_is_explicitly_null(self, monkeypatch):
        """Neither Cyrus nor Stalwart sends "links": null in practice (both
        omit the key entirely, confirmed live), but a malformed or future
        response doing so must not crash get_event_attachments."""
        event = {"id": "ev1", "links": None}
        resp = _get_response("CalendarEvent/get", "ev-get-0", [event])
        client = _make_client_with_mocked_session(monkeypatch, resp)
        assert client.get_event_attachments("ev1") == []

    def test_attach_to_event_when_existing_links_is_explicitly_null(self, monkeypatch):
        get_resp = _get_response("CalendarEvent/get", "ev-get-0", [{"id": "ev1", "links": None}])
        set_resp = _set_response("CalendarEvent/set", "ev-set-update-0", updated={"ev1": None})
        responses = iter([get_resp, set_resp])
        captured: dict = {"payloads": []}
        client = _make_client()

        def post(*args, **kwargs):
            captured["payloads"].append(kwargs.get("json"))
            return _make_mock_response(next(responses))

        mock_http = MagicMock()
        mock_http.post.side_effect = post
        client._http_session = mock_http
        client._session_cache.upload_url = "/jmap/upload/{accountId}/"
        client._session_cache.download_url = (
            "/jmap/download/{accountId}/{blobId}/{name}?accept={type}"
        )
        client.attach_to_event("ev1", "G123", "test.txt", "text/plain")
        update_args = captured["payloads"][1]["methodCalls"][0][1]
        assert len(update_args["update"]["ev1"]["links"]) == 1


def _availability_response(periods):
    """Shared by the sync and async get_availability tests."""
    return {
        "methodResponses": [
            ["Principal/getAvailability", {"list": periods}, "principal-getavailability-0"]
        ]
    }


def _error_response(error_type: str, call_id: str) -> dict:
    """A minimal ``error`` methodResponse envelope. Shared by every helper
    that builds one for a specific method call id."""
    return {"methodResponses": [["error", {"type": error_type}, call_id]]}


def _availability_error_response(error_type):
    """Shared by the sync and async get_availability tests."""
    return _error_response(error_type, "principal-getavailability-0")


class TestBusyIntervalFallbackConversion:
    """Direct unit tests for the fallback path's LocalDateTime -> UTCDateTime
    conversion, ensuring BusyInterval.start/.end have the same Z-suffixed
    format the primary path (Principal/getAvailability) already returns,
    regardless of which JSCalendar start shape (Etc/UTC, IANA timeZone,
    floating) a fetched event happens to use."""

    def test_etc_utc_timezone_passes_through_as_z_suffixed(self):
        result = _JMAPClientBase._jscal_start_to_utc_datetime("2026-09-21T10:00:00", "Etc/UTC")
        assert result == "2026-09-21T10:00:00Z"

    def test_iana_timezone_is_converted_to_utc(self):
        # America/New_York is UTC-4 (EDT) in September.
        result = _JMAPClientBase._jscal_start_to_utc_datetime(
            "2026-09-21T10:00:00", "America/New_York"
        )
        assert result == "2026-09-21T14:00:00Z"

    def test_floating_time_is_treated_as_utc(self):
        result = _JMAPClientBase._jscal_start_to_utc_datetime("2026-09-21T10:00:00", None)
        assert result == "2026-09-21T10:00:00Z"

    def test_already_z_suffixed_passes_through_unchanged(self):
        result = _JMAPClientBase._jscal_start_to_utc_datetime("2026-09-21T10:00:00Z", None)
        assert result == "2026-09-21T10:00:00Z"

    def test_non_iana_tzid_falls_back_to_treated_as_utc(self):
        result = _JMAPClientBase._jscal_start_to_utc_datetime(
            "2026-09-21T10:00:00", "Eastern Standard Time"
        )
        assert result == "2026-09-21T10:00:00Z"

    def test_busy_intervals_from_events_converts_timezone_aware_event(self):
        events = [
            {
                "start": "2026-09-21T10:00:00",
                "duration": "PT1H",
                "timeZone": "America/New_York",
                "freeBusyStatus": "busy",
            }
        ]
        intervals = _JMAPClientBase._busy_intervals_from_events(events)
        assert intervals[0].start == "2026-09-21T14:00:00Z"
        assert intervals[0].end == "2026-09-21T15:00:00Z"

    def test_busy_intervals_from_events_does_not_double_convert_z_suffixed_start(self):
        """A Z-suffixed start alongside a timeZone is a malformed combination
        this project's own iCal-to-JSCalendar writer never produces (UTC is
        always start + timeZone="Etc/UTC" there, see ical_to_jscal.py), but a
        server response is untrusted input: end must not have timeZone
        applied a second time on top of an already-UTC start."""
        events = [
            {
                "start": "2026-09-21T10:00:00Z",
                "duration": "PT1H",
                "timeZone": "America/New_York",
                "freeBusyStatus": "busy",
            }
        ]
        intervals = _JMAPClientBase._busy_intervals_from_events(events)
        assert intervals[0].start == "2026-09-21T10:00:00Z"
        assert intervals[0].end == "2026-09-21T11:00:00Z"


class TestJMAPClientFreeBusy(_MockedClientMixin):
    _PRINCIPALS_CAPS = {"urn:ietf:params:jmap:principals": {"currentUserPrincipalId": "user1"}}

    def _fallback_query_get_response(self, events: list[dict]) -> dict:
        """Batched [CalendarEvent/query, CalendarEvent/get] response for the
        availability fallback path specifically. Deliberately not
        :func:`_query_get_response`: fallback events carry only
        ``start``/``duration``/``freeBusyStatus`` (what
        ``_busy_intervals_from_events`` reads), no ``id``, so a real
        ``ids`` list can't be derived from them the way the shared helper
        does for full event objects; the query call's own ``ids`` value is
        never read by the code under test here."""
        return {
            "methodResponses": [
                ["CalendarEvent/query", {"ids": []}, "ev-query-0"],
                [
                    "CalendarEvent/get",
                    {"accountId": _USERNAME, "list": events, "notFound": []},
                    "ev-get-1",
                ],
            ]
        }

    def test_get_availability_uses_principal_getavailability_when_base_capability_present(
        self, monkeypatch
    ):
        period = {
            "utcStart": "2026-09-21T10:00:00Z",
            "utcEnd": "2026-09-21T11:00:00Z",
            "busyStatus": "confirmed",
            "event": None,
        }
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        self._mock_http(client, response=self._make_mock(_availability_response([period])))
        result = client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")
        assert result["user1"][0].start == "2026-09-21T10:00:00Z"
        assert result["user1"][0].busy_status == "confirmed"

    def test_get_availability_sends_principals_capability_and_utc_suffixed_dates(self, monkeypatch):
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        captured = self._capturing_client_for(client, _availability_response([]))
        client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")
        assert "urn:ietf:params:jmap:principals" in captured["json"]["using"]
        call_args = captured["json"]["methodCalls"][0][1]
        assert call_args["utcStart"] == "2026-09-21T00:00:00Z"
        assert call_args["utcEnd"] == "2026-09-22T00:00:00Z"
        assert "accountId" not in call_args

    def test_get_availability_skips_to_fallback_when_no_principals_capability(self, monkeypatch):
        client = self._client_with_capabilities({})
        self._mock_http(client, response=self._make_mock(self._fallback_query_get_response([])))
        result = client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")
        assert result == {"user1": []}

    def test_get_availability_falls_back_on_unknown_method(self, monkeypatch):
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        responses = iter(
            [_availability_error_response("unknownMethod"), self._fallback_query_get_response([])]
        )
        self._mock_http(client, side_effect=lambda *a, **k: self._make_mock(next(responses)))
        result = client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")
        assert result == {"user1": []}

    def test_get_availability_falls_back_on_account_not_supported_by_method(self, monkeypatch):
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        responses = iter(
            [
                _availability_error_response("accountNotSupportedByMethod"),
                self._fallback_query_get_response([]),
            ]
        )
        self._mock_http(client, side_effect=lambda *a, **k: self._make_mock(next(responses)))
        result = client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")
        assert result == {"user1": []}

    def test_get_availability_propagates_real_errors_without_falling_back(self, monkeypatch):
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        self._mock_http(client, response=self._make_mock(_availability_error_response("forbidden")))
        with pytest.raises(JMAPMethodError) as exc_info:
            client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")
        assert exc_info.value.error_type == "forbidden"

    def test_get_availability_resolves_own_principal_id_from_session(self, monkeypatch):
        caps = {"urn:ietf:params:jmap:principals": {"currentUserPrincipalId": "principal-xyz"}}
        client = self._client_with_capabilities(caps)
        captured = self._capturing_client_for(client, _availability_response([]))
        client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")
        assert captured["json"]["methodCalls"][0][1]["id"] == "principal-xyz"

    def test_get_availability_multiple_account_ids_returns_dict(self, monkeypatch):
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        self._mock_http(client, response=self._make_mock(_availability_response([])))
        result = client.get_availability(
            ["user1", "user2"], "2026-09-21T00:00:00", "2026-09-22T00:00:00"
        )
        assert set(result.keys()) == {"user1", "user2"}

    def test_get_availability_only_session_account_uses_principal_path(self, monkeypatch):
        """Principal/getAvailability never carries an accountId (see
        build_get_availability), so it can only ever report on the
        session's own account ("user1" here). A second, different
        account_id must always go through the fallback, which does scope
        to an explicit accountId, rather than silently returning "user1"'s
        own data again under a different key."""
        principal_period = {
            "utcStart": "2026-09-21T10:00:00Z",
            "utcEnd": "2026-09-21T11:00:00Z",
            "busyStatus": "confirmed",
            "event": None,
        }
        fallback_event = {
            "start": "2026-09-21T14:00:00",
            "duration": "PT1H",
            "freeBusyStatus": "busy",
        }
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)

        def side_effect(*args, **kwargs):
            method_calls = kwargs["json"]["methodCalls"]
            if method_calls[0][0] == "Principal/getAvailability":
                return self._make_mock(_availability_response([principal_period]))
            return self._make_mock(self._fallback_query_get_response([fallback_event]))

        self._mock_http(client, side_effect=side_effect)
        result = client.get_availability(
            ["user1", "user2"], "2026-09-21T00:00:00", "2026-09-22T00:00:00"
        )
        assert result["user1"][0].start == "2026-09-21T10:00:00Z"
        assert result["user2"][0].start == "2026-09-21T14:00:00Z"

    def test_get_availability_fallback_uses_expand_recurrences(self, monkeypatch):
        client = self._client_with_capabilities({})
        captured = self._capturing_client_for(client, self._fallback_query_get_response([]))
        client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["expandRecurrences"] is True
        assert query_args["filter"]["after"] == "2026-09-21T00:00:00"
        assert query_args["filter"]["before"] == "2026-09-22T00:00:00"

    def test_get_availability_fallback_computes_intervals_from_events(self, monkeypatch):
        events = [
            {"start": "2026-09-21T10:00:00", "duration": "PT1H", "freeBusyStatus": "busy"},
            {"start": "2026-09-22T10:00:00", "duration": "PT30M", "freeBusyStatus": "free"},
        ]
        client = self._client_with_capabilities({})
        self._mock_http(client, response=self._make_mock(self._fallback_query_get_response(events)))
        result = client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-23T00:00:00")
        assert len(result["user1"]) == 1
        assert result["user1"][0].start == "2026-09-21T10:00:00Z"
        assert result["user1"][0].end == "2026-09-21T11:00:00Z"

    def test_get_availability_falls_back_when_capability_present_but_no_principal_id(
        self, monkeypatch
    ):
        # Base capability present, but the account's own accountCapabilities
        # entry has no currentUserPrincipalId, so there is nothing to call
        # Principal/getAvailability with. This must go straight to the
        # fallback rather than raising or calling with a None id.
        caps = {"urn:ietf:params:jmap:principals": {}}
        client = self._client_with_capabilities(caps)
        self._mock_http(client, response=self._make_mock(self._fallback_query_get_response([])))
        result = client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")
        assert result == {"user1": []}

    def test_get_availability_via_principal_skips_unrelated_responses_in_batch(self, monkeypatch):
        period = {
            "utcStart": "2026-09-21T10:00:00Z",
            "utcEnd": "2026-09-21T11:00:00Z",
            "busyStatus": "confirmed",
            "event": None,
        }
        resp = {
            "methodResponses": [
                _UNRELATED_RESPONSE,
                ["Principal/getAvailability", {"list": [period]}, "c1"],
            ]
        }
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        self._mock_http(client, response=self._make_mock(resp))
        result = client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")
        assert result["user1"][0].busy_status == "confirmed"

    def test_get_availability_via_principal_raises_without_matching_response(self, monkeypatch):
        resp = {"methodResponses": [_UNRELATED_RESPONSE]}
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        self._mock_http(client, response=self._make_mock(resp))
        with pytest.raises(JMAPMethodError, match="No Principal/getAvailability response"):
            client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")


def _contact_query_get_response(contacts: list[dict]) -> dict:
    """Batched [ContactCard/query, ContactCard/get] response envelope,
    matching the shape _build_contact_search_calls produces. Shared by
    the sync and async search_contacts tests."""
    return _query_get_response(
        contacts,
        query_method="ContactCard/query",
        query_call_id="contact-query-0",
        get_method="ContactCard/get",
        get_call_id="contact-get-1",
    )


def _contact_error_response(error_type: str) -> dict:
    """Shared by the sync and async search_contacts tests."""
    return _error_response(error_type, "contact-query-0")


_CONTACTS_CAPS = {"urn:ietf:params:jmap:contacts": {"mayCreateAddressBook": True}}


def _address_book_get_response(items):
    """Shared by the sync and async get_address_books tests."""
    return _get_response("AddressBook/get", "ab-get-0", items)


class TestJMAPClientContacts(_MockedClientMixin):
    def test_get_address_books_returns_address_books(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        self._mock_http(
            client,
            response=self._make_mock(_address_book_get_response([_ADDRESS_BOOK_JSON_FULL])),
        )
        books = client.get_address_books()
        assert len(books) == 1
        assert books[0].id == "ab1"

    def test_get_address_books_sends_contacts_capability(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_client_for(client, _address_book_get_response([]))
        client.get_address_books()
        assert "urn:ietf:params:jmap:contacts" in captured["json"]["using"]
        assert CALENDAR_CAPABILITY not in captured["json"]["using"]

    def test_get_address_books_returns_empty_list_without_capability(self, monkeypatch):
        client = self._client_with_capabilities({})
        mock_http = self._mock_http(client, response=self._make_mock({}))
        assert client.get_address_books() == []
        mock_http.post.assert_not_called()

    def test_get_address_books_logs_warning_without_capability(self, monkeypatch, caplog):
        client = self._client_with_capabilities({})
        self._mock_http(client, response=self._make_mock({}))
        with caplog.at_level("WARNING"):
            client.get_address_books()
        assert "urn:ietf:params:jmap:contacts" in caplog.text

    def test_get_address_books_uses_passed_account_id(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_client_for(client, _address_book_get_response([]))
        client.get_address_books(account_id="other-account")
        assert captured["json"]["methodCalls"][0][1]["accountId"] == "other-account"

    def test_get_address_books_other_account_ignores_own_capability_gap(self, monkeypatch):
        # See _can_skip_contacts_request: a foreign account is never judged
        # by the session's own account_capabilities.
        client = self._client_with_capabilities({}, server_capabilities=_CONTACTS_CAPS)
        mock_http = self._mock_http(
            client, response=self._make_mock(_address_book_get_response([]))
        )
        client.get_address_books(account_id="other-account")
        mock_http.post.assert_called_once()

    def test_get_address_books_skips_any_account_without_server_capability(self, monkeypatch):
        # See _can_skip_contacts_request: server_capabilities applies to
        # every account, unlike account_capabilities.
        client = self._client_with_capabilities({}, server_capabilities={})
        mock_http = self._mock_http(client, response=self._make_mock({}))
        assert client.get_address_books(account_id="other-account") == []
        mock_http.post.assert_not_called()

    def test_search_contacts_returns_contacts(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        self._mock_http(
            client,
            response=self._make_mock(_contact_query_get_response([_CONTACT_JSON_FULL])),
        )
        contacts = client.search_contacts()
        assert len(contacts) == 1
        assert contacts[0].id == "c1"

    def test_search_contacts_sends_contacts_capability(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_client_for(client, _contact_query_get_response([]))
        client.search_contacts()
        assert "urn:ietf:params:jmap:contacts" in captured["json"]["using"]

    def test_search_contacts_uses_passed_account_id(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_client_for(client, _contact_query_get_response([]))
        client.search_contacts(account_id="other-account")
        assert captured["json"]["methodCalls"][0][1]["accountId"] == "other-account"
        assert CALENDAR_CAPABILITY not in captured["json"]["using"]

    def test_search_contacts_builds_text_filter(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_client_for(client, _contact_query_get_response([]))
        client.search_contacts(text="Alice")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"] == {"text": "Alice"}

    def test_search_contacts_builds_email_filter(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_client_for(client, _contact_query_get_response([]))
        client.search_contacts(email="alice@example.com")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"] == {"email": "alice@example.com"}

    def test_search_contacts_builds_combined_filter(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_client_for(client, _contact_query_get_response([]))
        client.search_contacts(text="Alice", email="alice@example.com")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"] == {"text": "Alice", "email": "alice@example.com"}

    def test_search_contacts_no_filter_when_no_args(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_client_for(client, _contact_query_get_response([]))
        client.search_contacts()
        query_args = captured["json"]["methodCalls"][0][1]
        assert "filter" not in query_args

    def test_search_contacts_raises_when_account_lacks_capability(self, monkeypatch):
        # _CONTACTS_USING always sends the capability in "using", so a real
        # server that supports Contacts but not for this account rejects
        # the call at the method level, not the request level: confirmed
        # live that this is accountNotSupportedByMethod, not unknownMethod.
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        resp = _contact_error_response("accountNotSupportedByMethod")
        self._mock_http(client, response=self._make_mock(resp))
        with pytest.raises(JMAPMethodError) as exc_info:
            client.search_contacts()
        assert exc_info.value.error_type == "accountNotSupportedByMethod"

    def test_search_contacts_raises_http_error_when_server_lacks_capability(self, monkeypatch):
        # A server with no Contacts support at all rejects the whole
        # request (unknownCapability, HTTP 400), before any methodResponses
        # array exists: confirmed live this never reaches a JMAPMethodError.
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        mock_resp = self._make_mock({})
        mock_resp.status_code = 400
        mock_resp.raise_for_status = MagicMock(side_effect=_http_requests.HTTPError("HTTP 400"))
        self._mock_http(client, response=mock_resp)
        with pytest.raises(_http_requests.HTTPError):
            client.search_contacts()


class TestJMAPClientCalendars(_MockedClientMixin):
    def _set_response(self, **kwargs):
        return _set_response("Calendar/set", "cal-set-create-0", **kwargs)

    def _get_response(self, items):
        return _get_response("Calendar/get", "cal-get-0", items)

    def test_create_calendar_returns_server_id(self, monkeypatch):
        resp = self._set_response(created={"new-0": {"id": "cal-new-1"}})
        client = _make_client_with_mocked_session(monkeypatch, resp)
        cal_id = client.create_calendar("Work")
        assert cal_id == "cal-new-1"

    def test_create_calendar_raises_on_failure(self, monkeypatch):
        resp = self._set_response(
            notCreated={"new-0": {"type": "invalidProperties", "properties": ["name"]}}
        )
        client = _make_client_with_mocked_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            client.create_calendar("")
        assert exc_info.value.error_type == "invalidProperties"

    def test_create_calendar_raises_on_malformed_response(self, monkeypatch):
        resp = self._set_response(created={}, notCreated={})
        client = _make_client_with_mocked_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError):
            client.create_calendar("Work")

    def test_create_calendar_passes_color_and_timezone(self, monkeypatch):
        resp = self._set_response(created={"new-0": {"id": "cal-new-2"}})
        client, captured = self._capturing_client(monkeypatch, resp)
        client.create_calendar("Work", color="#3a86ff", timezone="Europe/Berlin")
        create_args = captured["json"]["methodCalls"][0][1]
        cal_payload = create_args["create"]["new-0"]
        assert cal_payload == {"name": "Work", "color": "#3a86ff", "timeZone": "Europe/Berlin"}

    def test_update_calendar_success(self, monkeypatch):
        resp = self._set_response(updated={"cal1": None})
        client = _make_client_with_mocked_session(monkeypatch, resp)
        client.update_calendar("cal1", name="Renamed")

    def test_update_calendar_raises_on_failure(self, monkeypatch):
        resp = self._set_response(notUpdated={"cal1": {"type": "notFound"}})
        client = _make_client_with_mocked_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            client.update_calendar("cal1", name="Renamed")
        assert exc_info.value.error_type == "notFound"

    def test_update_calendar_only_patches_given_fields(self, monkeypatch):
        resp = self._set_response(updated={"cal1": None})
        client, captured = self._capturing_client(monkeypatch, resp)
        client.update_calendar("cal1", color="#ff0000")
        update_args = captured["json"]["methodCalls"][0][1]
        assert update_args["update"]["cal1"] == {"color": "#ff0000"}

    def test_delete_calendar_success(self, monkeypatch):
        resp = self._set_response(destroyed=["cal1"])
        client = _make_client_with_mocked_session(monkeypatch, resp)
        client.delete_calendar("cal1")

    def test_delete_calendar_raises_calendar_has_event(self, monkeypatch):
        resp = self._set_response(notDestroyed={"cal1": {"type": "calendarHasEvent"}})
        client = _make_client_with_mocked_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            client.delete_calendar("cal1")
        assert exc_info.value.error_type == "calendarHasEvent"

    def test_delete_calendar_passes_on_destroy_remove_events(self, monkeypatch):
        resp = self._set_response(destroyed=["cal1"])
        client, captured = self._capturing_client(monkeypatch, resp)
        client.delete_calendar("cal1", on_destroy_remove_events=True)
        destroy_args = captured["json"]["methodCalls"][0][1]
        assert destroy_args["onDestroyRemoveEvents"] is True

    def test_share_calendar_patches_keyed_path(self, monkeypatch):
        resp = self._set_response(updated={"cal1": None})
        client, captured = self._capturing_client(monkeypatch, resp)
        client.share_calendar("cal1", "principal1", {"mayReadItems": True})
        update_args = captured["json"]["methodCalls"][0][1]
        assert update_args["update"]["cal1"] == {"shareWith/principal1": {"mayReadItems": True}}

    def test_share_calendar_raises_on_forbidden(self, monkeypatch):
        resp = self._set_response(notUpdated={"cal1": {"type": "forbidden"}})
        client = _make_client_with_mocked_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            client.share_calendar("cal1", "principal1", {"mayReadItems": True})
        assert exc_info.value.error_type == "forbidden"

    def test_get_calendar_subscriptions_filters_unsubscribed(self, monkeypatch):
        resp = self._get_response(
            [
                {"id": "cal1", "name": "Subscribed", "isSubscribed": True},
                {"id": "cal2", "name": "NotSubscribed", "isSubscribed": False},
            ]
        )
        client = _make_client_with_mocked_session(monkeypatch, resp)
        subs = client.get_calendar_subscriptions()
        assert [cal.id for cal in subs] == ["cal1"]

    def test_set_default_alerts_with_time_only(self, monkeypatch):
        resp = self._set_response(updated={"cal1": None})
        client, captured = self._capturing_client(monkeypatch, resp)
        alert = {"@type": "Alert", "trigger": {"@type": "OffsetTrigger"}}
        client.set_default_alerts("cal1", alerts_with_time={"a1": alert})
        update_args = captured["json"]["methodCalls"][0][1]
        assert update_args["update"]["cal1"] == {"defaultAlertsWithTime": {"a1": alert}}

    def test_set_default_alerts_both(self, monkeypatch):
        resp = self._set_response(updated={"cal1": None})
        client, captured = self._capturing_client(monkeypatch, resp)
        alert = {"@type": "Alert", "trigger": {"@type": "OffsetTrigger"}}
        client.set_default_alerts(
            "cal1", alerts_with_time={"a1": alert}, alerts_without_time={"a2": alert}
        )
        update_args = captured["json"]["methodCalls"][0][1]
        assert update_args["update"]["cal1"] == {
            "defaultAlertsWithTime": {"a1": alert},
            "defaultAlertsWithoutTime": {"a2": alert},
        }

    def test_set_default_alerts_raises_on_failure(self, monkeypatch):
        resp = self._set_response(notUpdated={"cal1": {"type": "notFound"}})
        client = _make_client_with_mocked_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            client.set_default_alerts("cal1", alerts_with_time={})
        assert exc_info.value.error_type == "notFound"

    def test_update_calendar_patch_uses_given_account_id(self, monkeypatch):
        """update_calendar/delete_calendar/set_default_alerts all route through
        _update_calendar_patch, so one direct test on it covers all three."""
        resp = self._set_response(updated={"cal1": None})
        client, captured = self._capturing_client(monkeypatch, resp)
        client._update_calendar_patch("cal1", {"name": "Renamed"}, account_id="owner-account")
        update_args = captured["json"]["methodCalls"][0][1]
        assert update_args["accountId"] == "owner-account"

    def test_delete_calendar_uses_given_account_id(self, monkeypatch):
        resp = self._set_response(destroyed=["cal1"])
        client, captured = self._capturing_client(monkeypatch, resp)
        client.delete_calendar("cal1", account_id="owner-account")
        destroy_args = captured["json"]["methodCalls"][0][1]
        assert destroy_args["accountId"] == "owner-account"

    def test_share_calendar_uses_owning_account_id(self, monkeypatch):
        resp = self._set_response(updated={"cal1": None})
        client, captured = self._capturing_client(monkeypatch, resp)
        client.share_calendar(
            "cal1", "principal1", {"mayReadItems": True}, owning_account_id="owner-account"
        )
        update_args = captured["json"]["methodCalls"][0][1]
        assert update_args["accountId"] == "owner-account"


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
        """Gate finding 4.7: newState from /changes was discarded into _.
        Callers had no way to chain sync calls without a separate
        get_sync_token() round-trip, creating a race window where
        intervening changes would be silently missed."""
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
        return _set_response("Task/set", "task-set-create-0", **kwargs)

    def _get_response(self, items):
        return _get_response("Task/get", "task-get-0", items)

    def _tasklist_response(self, items):
        return _get_response("TaskList/get", "tasklist-get-0", items)

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
        """Gate finding 1.13: create_task must raise JMAPMethodError (not
        KeyError) when the server returns a Task/set response with an empty
        'created' dict and no 'notCreated' entry."""
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
        return _make_mock_response(resp_json)

    def _patch_async_session(self, monkeypatch, resp_json):
        mock_resp = self._make_mock_response(resp_json)
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.post = AsyncMock(return_value=mock_resp)
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        return mock_http

    def _calendar_get_resp(self, items):
        return _get_response("Calendar/get", "cal-get-0", items)

    def _calendar_set_resp(self, **kwargs):
        return _set_response("Calendar/set", "cal-set-0", **kwargs)

    def _event_set_resp(self, **kwargs):
        return _set_response("CalendarEvent/set", "ev-set-0", **kwargs)

    def _event_get_resp(self, items):
        return _get_response("CalendarEvent/get", "ev-get-0", items)

    def _query_get_resp(self, items):
        return _query_get_response(items)

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
        return _set_response("Task/set", "task-set-0", **kwargs)

    def _task_get_resp(self, items):
        return _get_response("Task/get", "task-get-0", items)

    def _tasklist_resp(self, items):
        return _get_response("TaskList/get", "tasklist-get-0", items)

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
    async def test_get_calendars_binds_own_account_id_by_default(self, monkeypatch):
        cal = {"id": "cal1", "name": "Personal", "isSubscribed": True, "myRights": {}}
        self._patch_async_session(monkeypatch, self._calendar_get_resp([cal]))
        result = await self._make_client().get_calendars()
        assert result[0]._account_id == _USERNAME

    @pytest.mark.asyncio
    async def test_get_calendars_binds_requested_account_id(self, monkeypatch):
        cal = {"id": "cal1", "name": "Personal", "isSubscribed": True, "myRights": {}}
        self._patch_async_session(monkeypatch, self._calendar_get_resp([cal]))
        result = await self._make_client().get_calendars(account_id="owner-account")
        assert result[0]._account_id == "owner-account"

    @pytest.mark.asyncio
    async def test_create_calendar_returns_server_id(self, monkeypatch):
        resp = self._calendar_set_resp(created={"new-0": {"id": "cal-async-1"}})
        self._patch_async_session(monkeypatch, resp)
        cal_id = await self._make_client().create_calendar("Work")
        assert cal_id == "cal-async-1"

    @pytest.mark.asyncio
    async def test_create_calendar_raises_on_failure(self, monkeypatch):
        resp = self._calendar_set_resp(
            notCreated={"new-0": {"type": "invalidProperties", "properties": ["name"]}}
        )
        self._patch_async_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client().create_calendar("")
        assert exc_info.value.error_type == "invalidProperties"

    @pytest.mark.asyncio
    async def test_create_calendar_raises_on_malformed_response(self, monkeypatch):
        resp = self._calendar_set_resp(created={}, notCreated={})
        self._patch_async_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError):
            await self._make_client().create_calendar("Work")

    @pytest.mark.asyncio
    async def test_update_calendar_success(self, monkeypatch):
        resp = self._calendar_set_resp(updated={"cal1": None})
        self._patch_async_session(monkeypatch, resp)
        await self._make_client().update_calendar("cal1", name="Renamed")

    @pytest.mark.asyncio
    async def test_update_calendar_raises_on_failure(self, monkeypatch):
        resp = self._calendar_set_resp(notUpdated={"cal1": {"type": "notFound"}})
        self._patch_async_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client().update_calendar("cal1", name="Renamed")
        assert exc_info.value.error_type == "notFound"

    @pytest.mark.asyncio
    async def test_delete_calendar_success(self, monkeypatch):
        resp = self._calendar_set_resp(destroyed=["cal1"])
        self._patch_async_session(monkeypatch, resp)
        await self._make_client().delete_calendar("cal1")

    @pytest.mark.asyncio
    async def test_delete_calendar_raises_calendar_has_event(self, monkeypatch):
        resp = self._calendar_set_resp(notDestroyed={"cal1": {"type": "calendarHasEvent"}})
        self._patch_async_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client().delete_calendar("cal1")
        assert exc_info.value.error_type == "calendarHasEvent"

    @pytest.mark.asyncio
    async def test_share_calendar_success(self, monkeypatch):
        resp = self._calendar_set_resp(updated={"cal1": None})
        self._patch_async_session(monkeypatch, resp)
        await self._make_client().share_calendar("cal1", "principal1", {"mayReadItems": True})

    @pytest.mark.asyncio
    async def test_share_calendar_raises_on_forbidden(self, monkeypatch):
        resp = self._calendar_set_resp(notUpdated={"cal1": {"type": "forbidden"}})
        self._patch_async_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client().share_calendar("cal1", "principal1", {"mayReadItems": True})
        assert exc_info.value.error_type == "forbidden"

    @pytest.mark.asyncio
    async def test_get_calendar_subscriptions_filters_unsubscribed(self, monkeypatch):
        resp = self._calendar_get_resp(
            [
                {"id": "cal1", "name": "Subscribed", "isSubscribed": True},
                {"id": "cal2", "name": "NotSubscribed", "isSubscribed": False},
            ]
        )
        self._patch_async_session(monkeypatch, resp)
        subs = await self._make_client().get_calendar_subscriptions()
        assert [cal.id for cal in subs] == ["cal1"]

    @pytest.mark.asyncio
    async def test_set_default_alerts_success(self, monkeypatch):
        resp = self._calendar_set_resp(updated={"cal1": None})
        self._patch_async_session(monkeypatch, resp)
        alert = {"@type": "Alert", "trigger": {"@type": "OffsetTrigger"}}
        await self._make_client().set_default_alerts("cal1", alerts_with_time={"a1": alert})

    @pytest.mark.asyncio
    async def test_set_default_alerts_raises_on_failure(self, monkeypatch):
        resp = self._calendar_set_resp(notUpdated={"cal1": {"type": "notFound"}})
        self._patch_async_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client().set_default_alerts("cal1", alerts_with_time={})
        assert exc_info.value.error_type == "notFound"

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
    async def test_get_event_uses_given_account_id(self, monkeypatch):
        mock_http = self._patch_async_session(monkeypatch, self._event_get_resp([]))
        with pytest.raises(JMAPMethodError):
            await self._make_client().get_event("ev-async-1", account_id="owner-account")
        get_args = mock_http.post.call_args.kwargs["json"]["methodCalls"][0][1]
        assert get_args["accountId"] == "owner-account"

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
    async def test_update_event_uses_given_account_id(self, monkeypatch):
        resp = self._event_set_resp(updated={"ev-async-1": None}, notUpdated={})
        mock_http = self._patch_async_session(monkeypatch, resp)
        await self._make_client().update_event(
            "ev-async-1", self._MINIMAL_ICAL, account_id="owner-account"
        )
        update_args = mock_http.post.call_args.kwargs["json"]["methodCalls"][0][1]
        assert update_args["accountId"] == "owner-account"

    @pytest.mark.asyncio
    async def test_delete_event_success(self, monkeypatch):
        resp = self._event_set_resp(destroyed=["ev-async-1"], notDestroyed={})
        self._patch_async_session(monkeypatch, resp)
        await self._make_client().delete_event("ev-async-1")

    @pytest.mark.asyncio
    async def test_delete_event_uses_given_account_id(self, monkeypatch):
        resp = self._event_set_resp(destroyed=["ev-async-1"], notDestroyed={})
        mock_http = self._patch_async_session(monkeypatch, resp)
        await self._make_client().delete_event("ev-async-1", account_id="owner-account")
        destroy_args = mock_http.post.call_args.kwargs["json"]["methodCalls"][0][1]
        assert destroy_args["accountId"] == "owner-account"

    @pytest.mark.asyncio
    async def test_delete_event_sends_scheduling_messages(self, monkeypatch):
        resp = self._event_set_resp(destroyed=["ev-async-1"], notDestroyed={})
        mock_http = self._patch_async_session(monkeypatch, resp)
        await self._make_client().delete_event("ev-async-1", send_scheduling_messages=True)
        destroy_args = mock_http.post.call_args.kwargs["json"]["methodCalls"][0][1]
        assert destroy_args["sendSchedulingMessages"] is True

    @pytest.mark.asyncio
    async def test_delete_event_raises_on_failure(self, monkeypatch):
        resp = self._event_set_resp(destroyed=[], notDestroyed={"ev-async-1": {"type": "notFound"}})
        self._patch_async_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client().delete_event("ev-async-1")
        assert exc_info.value.error_type == "notFound"

    @pytest.mark.asyncio
    async def test_send_invite_returns_id(self, monkeypatch):
        resp = self._event_set_resp(created={"new-0": {"id": "sv-async-1"}}, notCreated={})
        self._patch_async_session(monkeypatch, resp)
        event_id = await self._make_client().send_invite("cal1", self._MINIMAL_ICAL)
        assert event_id == "sv-async-1"

    @pytest.mark.asyncio
    async def test_send_invite_sends_scheduling_messages(self, monkeypatch):
        resp = self._event_set_resp(created={"new-0": {"id": "sv-async-1"}}, notCreated={})
        mock_http = self._patch_async_session(monkeypatch, resp)
        await self._make_client().send_invite("cal1", self._MINIMAL_ICAL)
        create_args = mock_http.post.call_args.kwargs["json"]["methodCalls"][0][1]
        assert create_args["sendSchedulingMessages"] is True

    def _participant_event_resp(self, own_email="me@example.com", participant_id="p1"):
        return self._event_get_resp(
            [_participant_event(self._RAW_EVENT, own_email, participant_id)]
        )

    def _patch_accept_sequence(self, monkeypatch, own_email="me@example.com", set_response=None):
        get_resp = self._participant_event_resp(own_email=own_email)
        set_resp = (
            set_response
            if set_response is not None
            else self._event_set_resp(updated={"ev-async-1": None})
        )
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.post = AsyncMock(
            side_effect=[
                self._make_mock_response(get_resp),
                self._make_mock_response(set_resp),
            ]
        )
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        return mock_http

    @pytest.mark.asyncio
    async def test_find_own_participant_id_matches_case_insensitively(self, monkeypatch):
        self._patch_async_session(
            monkeypatch, self._participant_event_resp(own_email="Me@Example.com")
        )
        participant_id = await self._make_client()._find_own_participant_id(
            "ev-async-1", "me@example.com"
        )
        assert participant_id == "p1"

    @pytest.mark.asyncio
    async def test_find_own_participant_id_raises_when_no_match(self, monkeypatch):
        self._patch_async_session(monkeypatch, self._participant_event_resp())
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client()._find_own_participant_id("ev-async-1", "stranger@example.com")
        assert exc_info.value.error_type == "notFound"

    @pytest.mark.asyncio
    async def test_find_own_participant_id_falls_back_to_calendar_address(self, monkeypatch):
        resp = self._event_get_resp(
            [
                {
                    **self._RAW_EVENT,
                    "participants": {
                        "p1": {
                            "@type": "Participant",
                            "calendarAddress": "mailto:me@example.com",
                            "roles": {"attendee": True},
                        },
                    },
                }
            ]
        )
        self._patch_async_session(monkeypatch, resp)
        participant_id = await self._make_client()._find_own_participant_id(
            "ev-async-1", "me@example.com"
        )
        assert participant_id == "p1"

    @pytest.mark.asyncio
    async def test_accept_invitation_patches_own_participant(self, monkeypatch):
        mock_http = self._patch_accept_sequence(monkeypatch)
        await self._make_client().accept_invitation("ev-async-1", "me@example.com")
        update_args = mock_http.post.call_args_list[1].kwargs["json"]["methodCalls"][0][1]
        assert update_args["update"]["ev-async-1"] == {
            "participants/p1/participationStatus": "accepted"
        }
        assert update_args["sendSchedulingMessages"] is True

    @pytest.mark.asyncio
    async def test_accept_invitation_fetches_only_participants(self, monkeypatch):
        mock_http = self._patch_accept_sequence(monkeypatch)
        await self._make_client().accept_invitation("ev-async-1", "me@example.com")
        get_args = mock_http.post.call_args_list[0].kwargs["json"]["methodCalls"][0][1]
        assert get_args["properties"] == ["participants"]

    @pytest.mark.asyncio
    async def test_decline_invitation_patches_own_participant(self, monkeypatch):
        mock_http = self._patch_accept_sequence(monkeypatch)
        await self._make_client().decline_invitation("ev-async-1", "me@example.com")
        update_args = mock_http.post.call_args_list[1].kwargs["json"]["methodCalls"][0][1]
        assert update_args["update"]["ev-async-1"] == {
            "participants/p1/participationStatus": "declined"
        }

    @pytest.mark.asyncio
    async def test_tentatively_accept_patches_own_participant(self, monkeypatch):
        mock_http = self._patch_accept_sequence(monkeypatch)
        await self._make_client().tentatively_accept("ev-async-1", "me@example.com")
        update_args = mock_http.post.call_args_list[1].kwargs["json"]["methodCalls"][0][1]
        assert update_args["update"]["ev-async-1"] == {
            "participants/p1/participationStatus": "tentative"
        }

    @pytest.mark.asyncio
    async def test_accept_invitation_raises_when_no_matching_participant(self, monkeypatch):
        self._patch_async_session(monkeypatch, self._participant_event_resp())
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client().accept_invitation("ev-async-1", "stranger@example.com")
        assert exc_info.value.error_type == "notFound"

    @pytest.mark.asyncio
    async def test_accept_invitation_raises_on_server_rejection(self, monkeypatch):
        rejection = self._event_set_resp(notUpdated={"ev-async-1": {"type": "forbidden"}})
        self._patch_accept_sequence(monkeypatch, set_response=rejection)
        with pytest.raises(JMAPMethodError) as exc_info:
            await self._make_client().accept_invitation("ev-async-1", "me@example.com")
        assert exc_info.value.error_type == "forbidden"

    def _blob_client(self):
        client = AsyncJMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        client._session_cache = Session(
            api_url=_API_URL,
            account_id=_USERNAME,
            state="state-async",
            upload_url="/jmap/upload/{accountId}/",
            download_url="/jmap/download/{accountId}/{blobId}/{name}?accept={type}",
        )
        return client

    @pytest.mark.asyncio
    async def test_upload_attachment_returns_blob_id(self):
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.post = AsyncMock(
            return_value=_make_mock_blob_response(
                json_data={
                    "accountId": _USERNAME,
                    "blobId": "G123",
                    "type": "text/plain",
                    "size": 5,
                }
            )
        )
        client._http_session = mock_http
        blob_id = await client.upload_attachment(b"hello", "text/plain")
        assert blob_id == "G123"
        call_args = mock_http.post.call_args
        assert call_args.args[0] == f"/jmap/upload/{_USERNAME}/"
        assert call_args.kwargs["data"] == b"hello"
        assert call_args.kwargs["headers"]["Content-Type"] == "text/plain"

    @pytest.mark.asyncio
    async def test_upload_attachment_raises_capability_error_when_no_upload_url(self):
        client = AsyncJMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        client._session_cache = Session(api_url=_API_URL, account_id=_USERNAME, state="state-async")
        with pytest.raises(JMAPCapabilityError):
            await client.upload_attachment(b"hello", "text/plain")

    @pytest.mark.asyncio
    async def test_upload_attachment_raises_auth_error_on_401(self):
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=_make_mock_blob_response(status_code=401))
        client._http_session = mock_http
        with pytest.raises(JMAPAuthError):
            await client.upload_attachment(b"hello", "text/plain")

    @pytest.mark.asyncio
    async def test_download_attachment_returns_bytes(self):
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=_make_mock_blob_response(content=b"hello"))
        client._http_session = mock_http
        data = await client.download_attachment("G123", "text/plain", "test.txt")
        assert data == b"hello"
        url = mock_http.get.call_args.args[0]
        assert url == f"/jmap/download/{_USERNAME}/G123/test.txt?accept=text%2Fplain"

    @pytest.mark.asyncio
    async def test_download_attachment_omitted_type_and_name_expand_to_empty(self):
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=_make_mock_blob_response(content=b"hello"))
        client._http_session = mock_http
        await client.download_attachment("G123")
        url = mock_http.get.call_args.args[0]
        assert url == f"/jmap/download/{_USERNAME}/G123/?accept="

    @pytest.mark.asyncio
    async def test_download_attachment_overrides_session_default_accept_header(self):
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=_make_mock_blob_response(content=b"hello"))
        client._http_session = mock_http
        await client.download_attachment("G123", "text/plain", "test.txt")
        assert mock_http.get.call_args.kwargs["headers"]["Accept"] == "*/*"

    @pytest.mark.asyncio
    async def test_download_attachment_coalesces_none_content_to_empty_bytes(self):
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=_make_mock_blob_response(content=None))
        client._http_session = mock_http
        data = await client.download_attachment("G123")
        assert data == b""

    @pytest.mark.asyncio
    async def test_download_attachment_raises_capability_error_when_no_download_url(self):
        client = AsyncJMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        client._session_cache = Session(api_url=_API_URL, account_id=_USERNAME, state="state-async")
        with pytest.raises(JMAPCapabilityError):
            await client.download_attachment("G123")

    @pytest.mark.asyncio
    async def test_download_attachment_raises_on_404(self):
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=_make_mock_blob_response(status_code=404))
        client._http_session = mock_http
        with pytest.raises(_http_requests.HTTPError, match="HTTP 404"):
            await client.download_attachment("unknown-blob")

    @pytest.mark.asyncio
    async def test_download_attachment_raises_on_403(self):
        client = self._blob_client()
        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=_make_mock_blob_response(status_code=403))
        client._http_session = mock_http
        with pytest.raises(JMAPAuthError):
            await client.download_attachment("G123")

    @pytest.mark.asyncio
    async def test_attach_to_event_uses_href_not_blob_id(self, monkeypatch):
        client = self._blob_client()
        get_resp = self._event_get_resp([{"id": "ev-async-1", "links": {}}])
        set_resp = self._event_set_resp(updated={"ev-async-1": None})
        mock_http = MagicMock()
        mock_http.post = AsyncMock(
            side_effect=[
                _make_mock_blob_response(json_data=get_resp),
                _make_mock_blob_response(json_data=set_resp),
            ]
        )
        client._http_session = mock_http
        await client.attach_to_event("ev-async-1", "G123", "test.txt", "text/plain")
        update_args = mock_http.post.call_args_list[1].kwargs["json"]["methodCalls"][0][1]
        (patch,) = update_args["update"]["ev-async-1"]["links"].values()
        assert "href" in patch
        assert "blobId" not in patch
        assert patch["href"] == f"/jmap/download/{_USERNAME}/G123/test.txt?accept=text%2Fplain"
        assert patch["rel"] == "enclosure"
        assert patch["title"] == "test.txt"
        assert patch["contentType"] == "text/plain"

    @pytest.mark.asyncio
    async def test_attach_to_event_fetches_only_links(self, monkeypatch):
        client = self._blob_client()
        get_resp = self._event_get_resp([{"id": "ev-async-1", "links": {}}])
        set_resp = self._event_set_resp(updated={"ev-async-1": None})
        mock_http = MagicMock()
        mock_http.post = AsyncMock(
            side_effect=[
                _make_mock_blob_response(json_data=get_resp),
                _make_mock_blob_response(json_data=set_resp),
            ]
        )
        client._http_session = mock_http
        await client.attach_to_event("ev-async-1", "G123", "test.txt", "text/plain")
        get_args = mock_http.post.call_args_list[0].kwargs["json"]["methodCalls"][0][1]
        assert get_args["properties"] == ["links"]

    @pytest.mark.asyncio
    async def test_attach_to_event_preserves_existing_links(self, monkeypatch):
        existing = {"other-l1": {"@type": "Link", "href": "http://x", "rel": "describedby"}}
        client = self._blob_client()
        get_resp = self._event_get_resp([{"id": "ev-async-1", "links": existing}])
        set_resp = self._event_set_resp(updated={"ev-async-1": None})
        mock_http = MagicMock()
        mock_http.post = AsyncMock(
            side_effect=[
                _make_mock_blob_response(json_data=get_resp),
                _make_mock_blob_response(json_data=set_resp),
            ]
        )
        client._http_session = mock_http
        await client.attach_to_event("ev-async-1", "G123", "test.txt", "text/plain")
        update_args = mock_http.post.call_args_list[1].kwargs["json"]["methodCalls"][0][1]
        links = update_args["update"]["ev-async-1"]["links"]
        assert links["other-l1"] == existing["other-l1"]
        assert len(links) == 2

    @pytest.mark.asyncio
    async def test_get_event_attachments_filters_to_enclosure_links(self, monkeypatch):
        event = {
            "id": "ev-async-1",
            "links": {
                "l1": {"@type": "Link", "href": "http://x/1", "rel": "enclosure", "title": "a.txt"},
                "l2": {"@type": "Link", "href": "http://x/2", "rel": "describedby"},
            },
        }
        self._patch_async_session(monkeypatch, self._event_get_resp([event]))
        attachments = await self._make_client().get_event_attachments("ev-async-1")
        assert len(attachments) == 1
        assert attachments[0].link_id == "l1"
        assert attachments[0].title == "a.txt"
        assert attachments[0].href == "http://x/1"

    @pytest.mark.asyncio
    async def test_get_event_attachments_empty_when_no_links(self, monkeypatch):
        event = {"id": "ev-async-1"}
        self._patch_async_session(monkeypatch, self._event_get_resp([event]))
        assert await self._make_client().get_event_attachments("ev-async-1") == []

    @pytest.mark.asyncio
    async def test_get_event_attachments_empty_when_links_is_explicitly_null(self, monkeypatch):
        event = {"id": "ev-async-1", "links": None}
        self._patch_async_session(monkeypatch, self._event_get_resp([event]))
        assert await self._make_client().get_event_attachments("ev-async-1") == []

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
        assert query_args["filter"]["inCalendar"] == "my-cal"

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

    _PRINCIPALS_CAPS = {"urn:ietf:params:jmap:principals": {"currentUserPrincipalId": "user1"}}

    def _client_with_capabilities(self, account_capabilities, server_capabilities=None):
        client = AsyncJMAPClient(url=_JMAP_URL, username=_USERNAME, password=_PASSWORD)
        client._session_cache = _session_with_capabilities(
            account_capabilities, server_capabilities, state="state-async"
        )
        return client

    def _capturing_async_session_for(self, monkeypatch, client, resp_json):
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
        return captured

    @pytest.mark.asyncio
    async def test_get_availability_uses_principal_getavailability_when_base_capability_present(
        self, monkeypatch
    ):
        period = {
            "utcStart": "2026-09-21T10:00:00Z",
            "utcEnd": "2026-09-21T11:00:00Z",
            "busyStatus": "confirmed",
            "event": None,
        }
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        self._patch_async_session(monkeypatch, _availability_response([period]))
        result = await client.get_availability(
            ["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00"
        )
        assert result["user1"][0].start == "2026-09-21T10:00:00Z"
        assert result["user1"][0].busy_status == "confirmed"

    @pytest.mark.asyncio
    async def test_get_availability_sends_principals_capability_and_utc_suffixed_dates(
        self, monkeypatch
    ):
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        captured = self._capturing_async_session_for(
            monkeypatch, client, _availability_response([])
        )
        await client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")
        assert "urn:ietf:params:jmap:principals" in captured["json"]["using"]
        call_args = captured["json"]["methodCalls"][0][1]
        assert call_args["utcStart"] == "2026-09-21T00:00:00Z"
        assert call_args["utcEnd"] == "2026-09-22T00:00:00Z"
        assert "accountId" not in call_args

    @pytest.mark.asyncio
    async def test_get_availability_skips_to_fallback_when_no_principals_capability(
        self, monkeypatch
    ):
        client = self._client_with_capabilities({})
        self._patch_async_session(monkeypatch, self._query_get_resp([]))
        result = await client.get_availability(
            ["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00"
        )
        assert result == {"user1": []}

    @pytest.mark.asyncio
    async def test_get_availability_falls_back_on_unknown_method(self, monkeypatch):
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        responses = iter([_availability_error_response("unknownMethod"), self._query_get_resp([])])
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)

        async def side_effect(*args, **kwargs):
            return self._make_mock_response(next(responses))

        mock_http.post = side_effect
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        result = await client.get_availability(
            ["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00"
        )
        assert result == {"user1": []}

    @pytest.mark.asyncio
    async def test_get_availability_falls_back_on_account_not_supported_by_method(
        self, monkeypatch
    ):
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        responses = iter(
            [
                _availability_error_response("accountNotSupportedByMethod"),
                self._query_get_resp([]),
            ]
        )
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)

        async def side_effect(*args, **kwargs):
            return self._make_mock_response(next(responses))

        mock_http.post = side_effect
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        result = await client.get_availability(
            ["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00"
        )
        assert result == {"user1": []}

    @pytest.mark.asyncio
    async def test_get_availability_propagates_real_errors_without_falling_back(self, monkeypatch):
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        self._patch_async_session(monkeypatch, _availability_error_response("forbidden"))
        with pytest.raises(JMAPMethodError) as exc_info:
            await client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")
        assert exc_info.value.error_type == "forbidden"

    @pytest.mark.asyncio
    async def test_get_availability_resolves_own_principal_id_from_session(self, monkeypatch):
        caps = {"urn:ietf:params:jmap:principals": {"currentUserPrincipalId": "principal-xyz"}}
        client = self._client_with_capabilities(caps)
        captured = self._capturing_async_session_for(
            monkeypatch, client, _availability_response([])
        )
        await client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")
        assert captured["json"]["methodCalls"][0][1]["id"] == "principal-xyz"

    @pytest.mark.asyncio
    async def test_get_availability_multiple_account_ids_returns_dict(self, monkeypatch):
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        self._patch_async_session(monkeypatch, _availability_response([]))
        result = await client.get_availability(
            ["user1", "user2"], "2026-09-21T00:00:00", "2026-09-22T00:00:00"
        )
        assert set(result.keys()) == {"user1", "user2"}

    @pytest.mark.asyncio
    async def test_get_availability_only_session_account_uses_principal_path(self, monkeypatch):
        """See the sync mirror of this test for the full rationale: only
        the session's own account ("user1") can use Principal/getAvailability;
        a different account_id must always use the fallback."""
        principal_period = {
            "utcStart": "2026-09-21T10:00:00Z",
            "utcEnd": "2026-09-21T11:00:00Z",
            "busyStatus": "confirmed",
            "event": None,
        }
        fallback_event = {
            "id": "ev1",
            "start": "2026-09-21T14:00:00",
            "duration": "PT1H",
            "freeBusyStatus": "busy",
        }
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)

        async def side_effect(*args, **kwargs):
            method_calls = kwargs["json"]["methodCalls"]
            if method_calls[0][0] == "Principal/getAvailability":
                return self._make_mock_response(_availability_response([principal_period]))
            return self._make_mock_response(self._query_get_resp([fallback_event]))

        mock_http.post = side_effect
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        result = await client.get_availability(
            ["user1", "user2"], "2026-09-21T00:00:00", "2026-09-22T00:00:00"
        )
        assert result["user1"][0].start == "2026-09-21T10:00:00Z"
        assert result["user2"][0].start == "2026-09-21T14:00:00Z"

    @pytest.mark.asyncio
    async def test_get_availability_fallback_uses_expand_recurrences(self, monkeypatch):
        client = self._client_with_capabilities({})
        captured = self._capturing_async_session_for(monkeypatch, client, self._query_get_resp([]))
        await client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["expandRecurrences"] is True
        assert query_args["filter"]["after"] == "2026-09-21T00:00:00"
        assert query_args["filter"]["before"] == "2026-09-22T00:00:00"

    @pytest.mark.asyncio
    async def test_get_availability_fallback_computes_intervals_from_events(self, monkeypatch):
        events = [
            {
                "id": "ev1",
                "start": "2026-09-21T10:00:00",
                "duration": "PT1H",
                "freeBusyStatus": "busy",
            },
            {
                "id": "ev2",
                "start": "2026-09-22T10:00:00",
                "duration": "PT30M",
                "freeBusyStatus": "free",
            },
        ]
        client = self._client_with_capabilities({})
        self._patch_async_session(monkeypatch, self._query_get_resp(events))
        result = await client.get_availability(
            ["user1"], "2026-09-21T00:00:00", "2026-09-23T00:00:00"
        )
        assert len(result["user1"]) == 1
        assert result["user1"][0].start == "2026-09-21T10:00:00Z"
        assert result["user1"][0].end == "2026-09-21T11:00:00Z"

    @pytest.mark.asyncio
    async def test_get_availability_falls_back_when_capability_present_but_no_principal_id(
        self, monkeypatch
    ):
        # Same case as the sync mirror: base capability present, but no
        # currentUserPrincipalId to call Principal/getAvailability with.
        caps = {"urn:ietf:params:jmap:principals": {}}
        client = self._client_with_capabilities(caps)
        self._patch_async_session(monkeypatch, self._query_get_resp([]))
        result = await client.get_availability(
            ["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00"
        )
        assert result == {"user1": []}

    @pytest.mark.asyncio
    async def test_get_availability_via_principal_skips_unrelated_responses_in_batch(
        self, monkeypatch
    ):
        period = {
            "utcStart": "2026-09-21T10:00:00Z",
            "utcEnd": "2026-09-21T11:00:00Z",
            "busyStatus": "confirmed",
            "event": None,
        }
        resp = {
            "methodResponses": [
                _UNRELATED_RESPONSE,
                ["Principal/getAvailability", {"list": [period]}, "c1"],
            ]
        }
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        self._patch_async_session(monkeypatch, resp)
        result = await client.get_availability(
            ["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00"
        )
        assert result["user1"][0].busy_status == "confirmed"

    @pytest.mark.asyncio
    async def test_get_availability_via_principal_raises_without_matching_response(
        self, monkeypatch
    ):
        resp = {"methodResponses": [_UNRELATED_RESPONSE]}
        client = self._client_with_capabilities(self._PRINCIPALS_CAPS)
        self._patch_async_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError, match="No Principal/getAvailability response"):
            await client.get_availability(["user1"], "2026-09-21T00:00:00", "2026-09-22T00:00:00")

    @pytest.mark.asyncio
    async def test_get_address_books_returns_address_books(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        self._patch_async_session(
            monkeypatch, _address_book_get_response([_ADDRESS_BOOK_JSON_FULL])
        )
        books = await client.get_address_books()
        assert len(books) == 1
        assert books[0].id == "ab1"

    @pytest.mark.asyncio
    async def test_get_address_books_sends_contacts_capability(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_async_session_for(
            monkeypatch, client, _address_book_get_response([])
        )
        await client.get_address_books()
        assert "urn:ietf:params:jmap:contacts" in captured["json"]["using"]
        assert CALENDAR_CAPABILITY not in captured["json"]["using"]

    @pytest.mark.asyncio
    async def test_get_address_books_returns_empty_list_without_capability(self, monkeypatch):
        client = self._client_with_capabilities({})
        mock_http = self._patch_async_session(monkeypatch, {})
        assert await client.get_address_books() == []
        mock_http.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_address_books_logs_warning_without_capability(self, monkeypatch, caplog):
        client = self._client_with_capabilities({})
        self._patch_async_session(monkeypatch, {})
        with caplog.at_level("WARNING"):
            await client.get_address_books()
        assert "urn:ietf:params:jmap:contacts" in caplog.text

    @pytest.mark.asyncio
    async def test_get_address_books_uses_passed_account_id(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_async_session_for(
            monkeypatch, client, _address_book_get_response([])
        )
        await client.get_address_books(account_id="other-account")
        assert captured["json"]["methodCalls"][0][1]["accountId"] == "other-account"

    @pytest.mark.asyncio
    async def test_get_address_books_other_account_ignores_own_capability_gap(self, monkeypatch):
        client = self._client_with_capabilities({}, server_capabilities=_CONTACTS_CAPS)
        mock_http = self._patch_async_session(monkeypatch, _address_book_get_response([]))
        await client.get_address_books(account_id="other-account")
        mock_http.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_address_books_skips_any_account_without_server_capability(self, monkeypatch):
        client = self._client_with_capabilities({}, server_capabilities={})
        mock_http = self._patch_async_session(monkeypatch, {})
        assert await client.get_address_books(account_id="other-account") == []
        mock_http.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_contacts_returns_contacts(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        self._patch_async_session(monkeypatch, _contact_query_get_response([_CONTACT_JSON_FULL]))
        contacts = await client.search_contacts()
        assert len(contacts) == 1
        assert contacts[0].id == "c1"

    @pytest.mark.asyncio
    async def test_search_contacts_sends_contacts_capability(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_async_session_for(
            monkeypatch, client, _contact_query_get_response([])
        )
        await client.search_contacts()
        assert "urn:ietf:params:jmap:contacts" in captured["json"]["using"]
        assert CALENDAR_CAPABILITY not in captured["json"]["using"]

    @pytest.mark.asyncio
    async def test_search_contacts_uses_passed_account_id(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_async_session_for(
            monkeypatch, client, _contact_query_get_response([])
        )
        await client.search_contacts(account_id="other-account")
        assert captured["json"]["methodCalls"][0][1]["accountId"] == "other-account"

    @pytest.mark.asyncio
    async def test_search_contacts_builds_text_filter(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_async_session_for(
            monkeypatch, client, _contact_query_get_response([])
        )
        await client.search_contacts(text="Alice")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"] == {"text": "Alice"}

    @pytest.mark.asyncio
    async def test_search_contacts_builds_email_filter(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_async_session_for(
            monkeypatch, client, _contact_query_get_response([])
        )
        await client.search_contacts(email="alice@example.com")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"] == {"email": "alice@example.com"}

    @pytest.mark.asyncio
    async def test_search_contacts_builds_combined_filter(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_async_session_for(
            monkeypatch, client, _contact_query_get_response([])
        )
        await client.search_contacts(text="Alice", email="alice@example.com")
        query_args = captured["json"]["methodCalls"][0][1]
        assert query_args["filter"] == {"text": "Alice", "email": "alice@example.com"}

    @pytest.mark.asyncio
    async def test_search_contacts_no_filter_when_no_args(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        captured = self._capturing_async_session_for(
            monkeypatch, client, _contact_query_get_response([])
        )
        await client.search_contacts()
        query_args = captured["json"]["methodCalls"][0][1]
        assert "filter" not in query_args

    @pytest.mark.asyncio
    async def test_search_contacts_raises_when_account_lacks_capability(self, monkeypatch):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        resp = _contact_error_response("accountNotSupportedByMethod")
        self._patch_async_session(monkeypatch, resp)
        with pytest.raises(JMAPMethodError) as exc_info:
            await client.search_contacts()
        assert exc_info.value.error_type == "accountNotSupportedByMethod"

    @pytest.mark.asyncio
    async def test_search_contacts_raises_http_error_when_server_lacks_capability(
        self, monkeypatch
    ):
        client = self._client_with_capabilities(_CONTACTS_CAPS)
        mock_resp = self._make_mock_response({})
        mock_resp.status_code = 400
        mock_resp.raise_for_status = MagicMock(side_effect=_http_requests.HTTPError("HTTP 400"))
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.post = AsyncMock(return_value=mock_resp)
        monkeypatch.setattr("calendaring_jmap.async_client.AsyncSession", lambda: mock_http)
        with pytest.raises(_http_requests.HTTPError):
            await client.search_contacts()

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
            reject("recurrenceRule"),
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
        assert patches_seen[0]["recurrenceRule"] is None
        assert "recurrenceRule" not in patches_seen[1]


class TestOverrideWithoutStartUsesOccurrenceTime:
    """RFC 8984 section 4.3.5: override child VEVENT must use occurrence
    time as DTSTART, not master start."""

    def test_title_only_override_dtstart_equals_occurrence(self):
        # Master: 2024-06-17T09:00:00Z (UTC), weekly recurrence.
        # Override for 2024-06-24T09:00:00Z changes only title, no "start" in patch.
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
        from calendaring_jmap.convert.ical_to_jscal import _prop_date_or_datetime

        child_dtstart = _prop_date_or_datetime(child["DTSTART"])
        if hasattr(child_dtstart, "utctimetuple"):
            import datetime as _dt

            assert child_dtstart == _dt.datetime(2024, 6, 24, 9, 0, 0, tzinfo=_dt.timezone.utc)
        else:
            assert str(child_dtstart) == "2024-06-24"


class TestExdateValueType:
    """Gate finding 4.2: EXDATE value type must match DTSTART (TZID or DATE, not floating)."""

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
        # The icalendar library may or may not emit explicit VALUE=DATE; either form is acceptable.
        assert "EXDATE" in result
        assert "20240624" in result
        assert "20240624T" not in result  # must not be a datetime


class TestStatusMapping:
    """Gate finding 4.4: STATUS must be mapped in both ical-to-jscal and
    jscal-to-ical directions."""

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
        assert jscal["recurrenceRule"]["until"] == "2024-06-30T09:00:00"

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
        assert jscal["recurrenceRule"]["until"] == "2024-06-30T09:00:00"
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
        assert jscal["recurrenceRule"]["until"] == "2024-06-30T07:00:00"


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
