# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JSCalendar to iCalendar conversion (:rfc:`8984` to :rfc:`5545`).

Public API:
    jscal_to_ical(jscal: dict) -> str

Accepts a raw JSCalendar CalendarEvent dict (as returned by CalendarEvent/get
or produced by ical_to_jscal). Returns a VCALENDAR string.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.request import urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import icalendar
from icalendar import vCalAddress, vText
from icalendar.cal.component_factory import ComponentFactory

from calendaring_jmap.constants import (
    LINK_REL_ENCLOSURE,
    LOCAL_DATETIME_FORMAT,
    PARTICIPATION_STATUS_ACCEPTED,
    PARTICIPATION_STATUS_DECLINED,
    PARTICIPATION_STATUS_DELEGATED,
    PARTICIPATION_STATUS_NEEDS_ACTION,
    PARTICIPATION_STATUS_TENTATIVE,
    UTC_DATETIME_FORMAT,
)
from calendaring_jmap.convert._fixup import fixup
from calendaring_jmap.convert._utils import _duration_to_timedelta

_PRIVACY_TO_CLASS = {
    "private": "PRIVATE",
    "secret": "CONFIDENTIAL",
}

# RFC 8984 section 1.4.9 (PatchObject): a pointer like "keywords/urgent" sets
# or (if null) removes just that one key of the map at "keywords", relative
# to whatever the patched object already has there. Confirmed live: Cyrus
# returns a recurrenceOverrides patch in this flattened, per-key form when
# the master event already has its own value for that property (there is
# something to diff against); when the master has none, Cyrus sends the
# override's whole map under the literal key instead, since there is
# nothing to flatten relative to. This converter itself only ever sends a
# patch as a single whole-map replacement, e.g. {"keywords": {...}}, never
# per-key pointers, so a patch read back from Cyrus needs either shape
# resolved into the single whole-map key
# _add_keywords/_add_location/_add_virtual_locations_to_component/
# _add_participants_to_component/_add_alerts_to_component already expect, or
# the whole property is silently dropped (the literal "keywords" key is
# never present in a flattened patch).
_RECURRENCE_OVERRIDE_MAP_PROPERTIES = (
    "participants",
    "locations",
    "virtualLocations",
    "alerts",
    "keywords",
)


def _resolve_flattened_map_patches(patch: dict, jscal: dict) -> dict:
    """Return ``patch`` with any flattened ``"X/subkey"`` pointers for a
    map-typed recurrence-override property resolved into a single literal
    ``"X"`` whole-map key, merged onto the master event's own ``jscal[X]``
    map. ``patch`` itself is never mutated.

    No-op (returns ``patch`` unchanged, not a copy) when ``patch`` has no
    flattened keys, which is the common case for a patch this converter
    produced itself (:func:`~calendaring_jmap.convert.ical_to_jscal.ical_to_jscal`
    only ever emits whole-map replacements, never per-key pointers).

    Only resolves one level of nesting (``"X/subkey"``), matching what
    Cyrus has actually been observed to send. A deeper pointer into a
    participant or alert's own properties (``"participants/p1/roles"``,
    legal per RFC 8984 section 1.4.9 but not something either live test
    server produces) is not resolved and is left in ``patch`` unchanged.
    """
    prefixes = {f"{prop}/" for prop in _RECURRENCE_OVERRIDE_MAP_PROPERTIES}
    flattened_keys = [k for k in patch if any(k.startswith(p) for p in prefixes)]
    if not flattened_keys:
        return patch

    resolved = {k: v for k, v in patch.items() if k not in flattened_keys}
    for prop in _RECURRENCE_OVERRIDE_MAP_PROPERTIES:
        own_keys = [k for k in flattened_keys if k.startswith(f"{prop}/")]
        if not own_keys:
            continue
        merged = dict(jscal.get(prop) or {})
        for key in own_keys:
            subkey = key[len(prop) + 1 :]
            value = patch[key]
            if value is None:
                merged.pop(subkey, None)
            else:
                merged[subkey] = value
        resolved[prop] = merged
    return resolved


def _add_status(component, status: str | None) -> None:
    """Add a ``STATUS`` property to ``component`` from a JSCalendar
    ``status`` value, if it maps to one. Shared by the master event and
    each recurrence override's child VEVENT in :func:`jscal_to_ical`."""
    if status:
        ical_status = _STATUS_JSCAL_TO_ICAL.get(status)
        if ical_status:
            component.add("status", ical_status)


def _add_free_busy_status(component, free_busy: str | None) -> None:
    """Add a ``TRANSP`` property to ``component`` from a JSCalendar
    ``freeBusyStatus`` value, if it resolves to something other than the
    default ``OPAQUE``. Shared by the master event and each recurrence
    override's child VEVENT in :func:`jscal_to_ical`."""
    transp = _FREE_BUSY_TO_TRANSP.get(free_busy, "OPAQUE") if free_busy else "OPAQUE"
    if transp != "OPAQUE":
        component.add("transp", transp)


def _add_keywords(component, keywords: dict | None) -> None:
    """Add a ``CATEGORIES`` property to ``component`` from a JSCalendar
    ``keywords`` map, if non-empty. Shared by the master event and each
    recurrence override's child VEVENT in :func:`jscal_to_ical`."""
    keywords = keywords or {}
    if keywords:
        cats = _keywords_to_categories(keywords)
        if cats:
            component.add("categories", cats)


def _add_location(component, locations: dict | None) -> None:
    """Add a ``LOCATION`` property to ``component`` from a JSCalendar
    ``locations`` map, if it resolves to a name. Shared by the master
    event and each recurrence override's child VEVENT in
    :func:`jscal_to_ical`."""
    locations = locations or {}
    if locations:
        loc_name = _locations_to_location(locations)
        if loc_name:
            component.add("location", loc_name)


def _virtual_location_to_conference(vloc: dict):
    """Build an ``icalendar.vUri`` for a ``CONFERENCE`` property from a
    JSCalendar VirtualLocation dict.

    Per draft-ietf-calext-jscalendar-icalendar section 3.7: ``uri``
    (mandatory per :rfc:`8984#section-4.2.6`) converts to the property
    value, ``name`` to the ``LABEL`` parameter (:rfc:`7986#section-6.4`),
    and ``features`` to the ``FEATURE`` parameter (:rfc:`7986#section-6.3`)
    as a list of uppercased names, matching the enum values RFC 7986 itself
    defines in all caps; ``icalendar`` itself comma-joins a list-valued
    parameter when serializing.

    ``description``/``descriptionContentType`` are handled separately: the
    draft converts them to a ``VCONFERENCE`` component's own
    ``DESCRIPTION``/``STYLED-DESCRIPTION``, not a ``CONFERENCE`` parameter,
    so :func:`_description_to_vconference` builds that sibling component.
    """
    uri = vloc.get("uri", "")
    conf = icalendar.vUri(uri)
    conf.params["VALUE"] = "URI"
    name = vloc.get("name")
    if name:
        conf.params["LABEL"] = vText(name)
    features = vloc.get("features") or {}
    if features:
        conf.params["FEATURE"] = [f.upper() for f, v in features.items() if v]
    return conf


_VCONFERENCE_CLASS = ComponentFactory().get_component_class("VCONFERENCE")


def _description_to_vconference(uri: str, description: str, content_type: str | None):
    """Build a VCONFERENCE component carrying a VirtualLocation's
    description, correlated to its sibling CONFERENCE property by URI.

    Per draft-ietf-calext-jscalendar-icalendar section 2.2.3. Writes
    STYLED-DESCRIPTION (carrying descriptionContentType via FMTTYPE) when
    a content type is known, plain DESCRIPTION otherwise.
    """
    vconf = _VCONFERENCE_CLASS()
    vconf.add("uri", icalendar.vUri(uri))
    if content_type:
        vconf.add(
            "styled-description",
            icalendar.vText(description),
            parameters={"VALUE": "TEXT", "FMTTYPE": content_type},
        )
    else:
        vconf.add("description", description)
    return vconf


def _add_virtual_locations_to_component(component, virtual_locations: dict) -> None:
    """Add a ``CONFERENCE`` property to ``component`` for every entry in a
    JSCalendar virtualLocations map, plus a sibling ``VCONFERENCE``
    component when an entry has ``description`` set. Shared by the master
    event and each recurrence override's child VEVENT in
    :func:`jscal_to_ical`."""
    for vloc in virtual_locations.values():
        if vloc.get("uri"):
            component.add("conference", _virtual_location_to_conference(vloc))
            description = vloc.get("description")
            if description:
                component.add_component(
                    _description_to_vconference(
                        vloc["uri"], description, vloc.get("descriptionContentType")
                    )
                )


_FREE_BUSY_TO_TRANSP = {
    "free": "TRANSPARENT",
    "busy": "OPAQUE",
}

_PARTSTAT_MAP = {
    PARTICIPATION_STATUS_NEEDS_ACTION: "NEEDS-ACTION",
    PARTICIPATION_STATUS_ACCEPTED: "ACCEPTED",
    PARTICIPATION_STATUS_DECLINED: "DECLINED",
    PARTICIPATION_STATUS_TENTATIVE: "TENTATIVE",
    PARTICIPATION_STATUS_DELEGATED: "DELEGATED",
}

_KIND_TO_CUTYPE = {
    "individual": "INDIVIDUAL",
    "group": "GROUP",
    "resource": "RESOURCE",
    "room": "ROOM",
}
# RFC 8984 status -> RFC 5545 STATUS; module-level constant (cf. _KIND_TO_CUTYPE etc.)
_STATUS_JSCAL_TO_ICAL = {
    "confirmed": "CONFIRMED",
    "tentative": "TENTATIVE",
    "cancelled": "CANCELLED",
}


def _start_to_dtstart(
    component: icalendar.Event,
    start_str: str,
    time_zone: str | None,
    show_without_time: bool,
) -> None:
    """Add a DTSTART property to component from JSCalendar start fields.

    Handles four cases:
    - All-day (showWithoutTime): VALUE=DATE
    - UTC (start ends with Z): UTC DATETIME
    - Timezone-aware: DATETIME;TZID=...
    - Floating (no timeZone, no Z): plain DATETIME
    """
    if show_without_time:
        dt = date.fromisoformat(start_str[:10])
        component.add("dtstart", dt)
        return

    if start_str.endswith("Z"):
        dt = datetime.strptime(start_str, UTC_DATETIME_FORMAT).replace(tzinfo=timezone.utc)
        component.add("dtstart", dt)
        return

    dt_naive = datetime.strptime(start_str[:19], LOCAL_DATETIME_FORMAT)

    if time_zone:
        try:
            tz = ZoneInfo(time_zone)
            dt = dt_naive.replace(tzinfo=tz)
            component.add("dtstart", dt)
        except ZoneInfoNotFoundError:
            # Non-IANA TZID (e.g. "Eastern Standard Time"): pass through as-is
            # so the consuming calendar client can resolve it.
            dtstart = icalendar.vDatetime(dt_naive)
            dtstart.params["TZID"] = time_zone
            component.add("dtstart", dtstart)
    else:
        component.add("dtstart", dt_naive)


def _recurrence_rules(jscal: dict, singular_key: str, plural_key: str) -> list[dict]:
    """Return the RecurrenceRule dicts for ``singular_key``/``plural_key`` on an Event.

    :rfc:`8984#section-4.3.3` defines the plural key as an array; both test
    servers this repo targets send the singular key instead (see the matching
    comment in ical_to_jscal.py). Prefer the plural array when present, since it is the
    actual spec type and can hold more than one rule; fall back to the
    singular key for these servers' shape.
    """
    plural_rules = jscal.get(plural_key)
    if plural_rules:
        return plural_rules
    single_rule = jscal.get(singular_key)
    return [single_rule] if single_rule else []


def _jscal_rrule_to_rrule(rule: dict, time_zone: str | None = None) -> dict:
    """Convert a JSCalendar RecurrenceRule dict to an iCalendar vRecur-compatible dict.

    Strips @type and NDay @type fields, which the icalendar library rejects.
    Returns a plain dict suitable for icalendar.vRecur.

    ``time_zone`` is the event's IANA time zone.  The JSCalendar ``until`` is a
    LocalDateTime in that zone; :rfc:`5545#section-3.3.10` requires the
    iCalendar UNTIL to be UTC whenever DTSTART is a TZID or UTC date-time, so
    a non-Z ``until`` is converted back to UTC here.
    """
    freq = rule.get("frequency", "").upper()
    if not freq:
        return {}

    ical_rule: dict = {"FREQ": freq}

    interval = rule.get("interval")
    if interval and interval != 1:
        ical_rule["INTERVAL"] = interval

    count = rule.get("count")
    if count is not None:
        ical_rule["COUNT"] = count

    until = rule.get("until")
    if until:
        if until.endswith("Z"):
            ical_rule["UNTIL"] = datetime.strptime(until, UTC_DATETIME_FORMAT).replace(
                tzinfo=timezone.utc
            )
        elif time_zone:
            # RFC 5545 section 3.3.10: a TZID/UTC DTSTART requires a UTC UNTIL.  The
            # JSCalendar until is LocalDateTime in the event timeZone; convert
            # it back to UTC so the emitted UNTIL carries the Z suffix.
            naive = datetime.strptime(until[:19], LOCAL_DATETIME_FORMAT)
            try:
                ical_rule["UNTIL"] = naive.replace(tzinfo=ZoneInfo(time_zone)).astimezone(
                    timezone.utc
                )
            except ZoneInfoNotFoundError:
                ical_rule["UNTIL"] = naive
        else:
            ical_rule["UNTIL"] = datetime.strptime(until[:19], LOCAL_DATETIME_FORMAT)

    by_day = rule.get("byDay", [])
    if by_day:
        byday_strs = []
        for nday in by_day:
            day = nday.get("day", "").upper()
            nth = nday.get("nthOfPeriod")
            if nth:
                byday_strs.append(f"{nth}{day}")
            else:
                byday_strs.append(day)
        ical_rule["BYDAY"] = byday_strs

    by_month = rule.get("byMonth", [])
    if by_month:
        ical_rule["BYMONTH"] = [
            m if isinstance(m, int) else int(str(m).rstrip("L")) for m in by_month
        ]

    by_month_day = rule.get("byMonthDay", [])
    if by_month_day:
        ical_rule["BYMONTHDAY"] = by_month_day

    by_year_day = rule.get("byYearDay", [])
    if by_year_day:
        ical_rule["BYYEARDAY"] = by_year_day

    by_week_no = rule.get("byWeekNo", [])
    if by_week_no:
        ical_rule["BYWEEKNO"] = by_week_no

    by_hour = rule.get("byHour", [])
    if by_hour:
        ical_rule["BYHOUR"] = by_hour

    by_minute = rule.get("byMinute", [])
    if by_minute:
        ical_rule["BYMINUTE"] = by_minute

    by_second = rule.get("bySecond", [])
    if by_second:
        ical_rule["BYSECOND"] = by_second

    by_set_pos = rule.get("bySetPosition", [])
    if by_set_pos:
        ical_rule["BYSETPOS"] = by_set_pos

    first_day = rule.get("firstDayOfWeek")
    if first_day and first_day != "mo":
        ical_rule["WKST"] = first_day.upper()

    return ical_rule


def _participant_imip(p: dict) -> str:
    send_to = p.get("sendTo", {})
    imip = (
        send_to.get("imip")
        or send_to.get("other")
        or p.get("calendarAddress")
        or p.get("email", "")
    )
    # calendarAddress (and sendTo.other) can already carry a non-mailto
    # scheme (e.g. sip:); only bare addresses need mailto: added.
    if imip and ":" not in imip:
        imip = f"mailto:{imip}"
    return imip


def _participant_to_organizer(p: dict) -> vCalAddress | None:
    """Build a vCalAddress for ORGANIZER, or None if this participant is not an organizer."""
    roles = p.get("roles", {})
    if not (roles.get("owner") or roles.get("organizer")):
        return None

    addr = vCalAddress(_participant_imip(p))
    name = p.get("name")
    if name:
        addr.params["CN"] = vText(name)
    return addr


def _participant_to_attendee(p: dict) -> vCalAddress | None:
    """Build a vCalAddress for ATTENDEE, or None if participant is purely an organizer."""
    roles = p.get("roles", {})
    has_attendee_role = any(
        roles.get(r) for r in ("attendee", "chair", "informational", "optional")
    )
    if not has_attendee_role and (roles.get("owner") or roles.get("organizer")):
        return None

    addr = vCalAddress(_participant_imip(p))
    name = p.get("name")
    if name:
        addr.params["CN"] = vText(name)

    partstat = p.get("participationStatus")
    if partstat:
        addr.params["PARTSTAT"] = _PARTSTAT_MAP.get(partstat, partstat.upper())
    else:
        addr.params["PARTSTAT"] = "NEEDS-ACTION"

    if p.get("expectReply"):
        addr.params["RSVP"] = "TRUE"

    kind = p.get("kind")
    if kind:
        addr.params["CUTYPE"] = _KIND_TO_CUTYPE.get(kind, kind.upper())

    if roles.get("chair"):
        addr.params["ROLE"] = "CHAIR"
    elif roles.get("attendee") or has_attendee_role:
        addr.params["ROLE"] = "REQ-PARTICIPANT"

    return addr


def _add_participants_to_component(component, participants: dict) -> None:
    """Add ``ORGANIZER``/``ATTENDEE`` properties to ``component`` (a VEVENT
    or child VEVENT) from a JSCalendar participants map.

    Shared by the master event and each recurrence override's child VEVENT
    in :func:`jscal_to_ical`: at most one ``ORGANIZER`` is added (the first
    participant whose role resolves to one via
    :func:`_participant_to_organizer`), and every participant with an
    attendee-shaped role becomes an ``ATTENDEE``.
    """
    organizer_added = False
    for p in participants.values():
        org = _participant_to_organizer(p)
        if org and not organizer_added:
            component.add("organizer", org)
            organizer_added = True
        att = _participant_to_attendee(p)
        if att is not None:
            component.add("attendee", att)


def _alert_to_valarm(alert: dict) -> icalendar.Alarm:
    """Convert a JSCalendar Alert dict to an icalendar.Alarm component.

    ``trigger`` is an OffsetTrigger or AbsoluteTrigger object, not a bare
    string (:rfc:`8984#section-4.5.2`): ``{"@type": "OffsetTrigger",
    "offset": "-PT15M", "relativeTo": "start"}`` or ``{"@type":
    "AbsoluteTrigger", "when": "..."}``.
    """
    alarm = icalendar.Alarm()
    action = alert.get("action", "display").upper()
    alarm.add("action", action)

    trigger = alert.get("trigger") or {}
    trigger_type = trigger.get("@type")
    if trigger_type == "AbsoluteTrigger":
        when = trigger.get("when", "")
        try:
            dt = datetime.strptime(when, UTC_DATETIME_FORMAT).replace(tzinfo=timezone.utc)
            alarm.add("trigger", dt)
        except ValueError:
            alarm.add("trigger", timedelta(0))
    elif trigger_type == "OffsetTrigger":
        offset = trigger.get("offset", "")
        try:
            td = _duration_to_timedelta(offset)
            ical_trigger = icalendar.vDuration(td)
            if trigger.get("relativeTo") == "end":
                ical_trigger.params["RELATED"] = "END"
            alarm.add("trigger", ical_trigger)
        except ValueError:
            alarm.add("trigger", timedelta(0))
    else:
        alarm.add("trigger", timedelta(0))

    description = alert.get("description")
    if description:
        alarm.add("description", description)
    elif action == "DISPLAY":
        alarm.add("description", "Reminder")

    return alarm


def _add_alerts_to_component(component, alerts: dict) -> None:
    """Add a ``VALARM`` subcomponent to ``component`` for every entry in a
    JSCalendar alerts map. Shared by the master event and each recurrence
    override's child VEVENT in :func:`jscal_to_ical`."""
    for alert in alerts.values():
        alarm = _alert_to_valarm(alert)
        component.add_component(alarm)


def _link_to_attach(link: dict):
    """Convert a JSCalendar Link dict to an icalendar ATTACH value.

    Only converts a Link whose ``rel`` is ``"enclosure"``
    (``constants.LINK_REL_ENCLOSURE``); other ``rel`` values are left alone
    (not emitted as ATTACH, IMAGE, or LINK), matching
    :func:`~calendaring_jmap.convert.ical_to_jscal._attach_to_link`'s
    narrower-than-the-draft scope in the other direction. Returns ``None``
    for a Link this function doesn't convert.

    A ``data:`` URL ``href`` (RFC 2397, produced by the same function for a
    binary-form ATTACH) converts back to inline ``ENCODING=BASE64;
    VALUE=BINARY`` using the stdlib's own RFC 2397 support
    (:func:`urllib.request.urlopen`), rather than re-implementing data URL
    parsing by hand. Any other ``href`` converts to a plain URI-form ATTACH.

    See :meth:`JMAPAttachment.is_attachment
    <calendaring_jmap.objects.attachment.JMAPAttachment.is_attachment>` for
    a known Cyrus limitation affecting the ``rel`` this function filters on.
    """
    if link.get("rel") != LINK_REL_ENCLOSURE:
        return None
    href = link.get("href")
    if not href:
        return None
    content_type = link.get("contentType")
    if href.startswith("data:"):
        with urlopen(href) as resp:
            data = resp.read()
            ## get_content_type() always returns a truthy default
            ## ("text/plain") when the data URL has no media type, so this
            ## is never empty.
            resolved_type = content_type or resp.headers.get_content_type()
        params = {"ENCODING": "BASE64", "VALUE": "BINARY", "FMTTYPE": str(resolved_type)}
        return icalendar.vBinary(data, params=params)
    attach = icalendar.vUri(href)
    if content_type:
        attach.params["FMTTYPE"] = str(content_type)
    return attach


def _link_to_url(link: dict):
    """Convert a JSCalendar Link dict to an icalendar URL property value.

    Only converts a Link with no ``rel`` set at all; a Link with any
    ``rel`` (including ``"enclosure"``) is left to :func:`_link_to_attach`
    or a future rel-specific emitter, matching :func:`_link_to_attach`'s
    own narrower-than-the-draft precedent.
    """
    if link.get("rel"):
        return None
    href = link.get("href")
    if not href:
        return None
    return icalendar.vUri(href)


def _keywords_to_categories(keywords: dict) -> list[str]:
    """Convert JSCalendar keywords map to a list of CATEGORIES strings."""
    return [k for k, v in keywords.items() if v]


def _locations_to_location(locations: dict) -> str | None:
    """Extract the first location name from a JSCalendar locations map."""
    for loc in locations.values():
        name = loc.get("name")
        if name:
            return str(name)
    return None


def jscal_to_ical(jscal: dict) -> str:
    """Convert a JSCalendar CalendarEvent dict to an iCalendar VCALENDAR string.

    Handles the full set of fields supported by ``ical_to_jscal`` for round-trip
    fidelity. ``recurrenceOverrides`` entries with ``excluded: true`` become
    EXDATE properties; patch dicts become child VEVENTs with RECURRENCE-ID.

    Args:
        jscal: A raw JSCalendar CalendarEvent dict as returned by ``CalendarEvent/get``.

    Returns:
        An iCalendar VCALENDAR string, normalised by :func:`~calendaring_jmap.convert._fixup.fixup`.

    Raises:
        ValueError: If ``uid`` is absent or empty (mandatory per
            :rfc:`8984#section-4.1.2`; a server response should always
            include it, but emitting an ``UID``-less VEVENT, invalid per
            :rfc:`5545`, would be a worse failure mode than raising here).
    """
    cal = icalendar.Calendar()
    cal.add("prodid", "-//calendaring-jmap//JMAP//EN")
    cal.add("version", "2.0")

    event = icalendar.Event()

    uid = jscal.get("uid", "")
    if not uid:
        raise ValueError("JSCalendar event is missing the mandatory 'uid' property")
    event.add("uid", uid)
    event.add("dtstamp", datetime.now(tz=timezone.utc))

    sequence = jscal.get("sequence", 0)
    if sequence:
        event.add("sequence", sequence)

    start_str = jscal.get("start", "")
    time_zone = jscal.get("timeZone")
    show_without_time = jscal.get("showWithoutTime", False)
    if start_str:
        _start_to_dtstart(event, start_str, time_zone, show_without_time)

    duration_str = jscal.get("duration", "P0D")
    if duration_str and duration_str != "P0D":
        td = _duration_to_timedelta(duration_str)
        event.add("duration", td)

    title = jscal.get("title", "")
    if title:
        event.add("summary", title)

    description = jscal.get("description")
    if description:
        event.add("description", description)

    priority = jscal.get("priority", 0)
    if priority:
        event.add("priority", priority)

    privacy = jscal.get("privacy")
    if privacy:
        cls = _PRIVACY_TO_CLASS.get(privacy)
        if cls:
            event.add("class", cls)

    _add_free_busy_status(event, jscal.get("freeBusyStatus", "busy"))

    color = jscal.get("color")
    if color:
        event.add("color", color)

    _add_keywords(event, jscal.get("keywords"))
    _add_location(event, jscal.get("locations"))
    _add_virtual_locations_to_component(event, jscal.get("virtualLocations") or {})
    _add_status(event, jscal.get("status"))

    for rule in _recurrence_rules(jscal, "recurrenceRule", "recurrenceRules"):
        ical_rule = _jscal_rrule_to_rrule(rule, time_zone)
        if ical_rule:
            event.add("rrule", ical_rule)

    for rule in _recurrence_rules(jscal, "excludedRecurrenceRule", "excludedRecurrenceRules"):
        ical_rule = _jscal_rrule_to_rrule(rule, time_zone)
        if ical_rule:
            event.add("exrule", ical_rule)

    exdates: list[datetime | date] = []
    child_events: list[icalendar.Event] = []

    for override_key, patch in (jscal.get("recurrenceOverrides") or {}).items():
        if override_key.endswith("Z"):
            rid_dt: datetime | date = datetime.strptime(override_key, UTC_DATETIME_FORMAT).replace(
                tzinfo=timezone.utc
            )
        elif show_without_time:
            rid_dt = date.fromisoformat(override_key[:10])
        elif time_zone:
            try:
                rid_dt = datetime.strptime(override_key[:19], LOCAL_DATETIME_FORMAT).replace(
                    tzinfo=ZoneInfo(time_zone)
                )
            except ZoneInfoNotFoundError:
                rid_dt = datetime.strptime(override_key[:19], LOCAL_DATETIME_FORMAT)
        else:
            rid_dt = datetime.strptime(override_key[:19], LOCAL_DATETIME_FORMAT)

        if patch is None or (isinstance(patch, dict) and patch.get("excluded")):
            exdates.append(rid_dt)
        else:
            patch = _resolve_flattened_map_patches(patch, jscal)
            child = icalendar.Event()
            child.add("uid", uid)
            child.add("dtstamp", datetime.now(tz=timezone.utc))
            child.add("recurrence-id", rid_dt)
            # Default child start to the occurrence time (override key), not the master start.
            child_start = patch.get("start", override_key)
            child_tz = patch.get("timeZone", time_zone)
            child_swt = patch.get("showWithoutTime", show_without_time)
            if child_start:
                _start_to_dtstart(child, child_start, child_tz, child_swt)
            child_dur = patch.get("duration", duration_str)
            if child_dur and child_dur != "P0D":
                child.add("duration", _duration_to_timedelta(child_dur))
            child_title = patch.get("title", title)
            if child_title:
                child.add("summary", child_title)
            child_desc = patch.get("description", description)
            if child_desc:
                child.add("description", child_desc)

            if "status" in patch:
                _add_status(child, patch["status"])

            if "freeBusyStatus" in patch:
                _add_free_busy_status(child, patch["freeBusyStatus"])

            if "keywords" in patch:
                _add_keywords(child, patch["keywords"])

            if "locations" in patch:
                _add_location(child, patch["locations"])

            if "virtualLocations" in patch:
                _add_virtual_locations_to_component(child, patch["virtualLocations"] or {})

            if "participants" in patch:
                _add_participants_to_component(child, patch["participants"] or {})

            if "alerts" in patch:
                _add_alerts_to_component(child, patch["alerts"] or {})

            child_events.append(child)

    if exdates:
        for exdate_dt in exdates:
            event.add("exdate", exdate_dt)

    _add_participants_to_component(event, jscal.get("participants") or {})
    _add_alerts_to_component(event, jscal.get("alerts") or {})

    url_written = False
    for link in (jscal.get("links") or {}).values():
        attach = _link_to_attach(link)
        if attach is not None:
            event.add("attach", attach)
        elif not url_written:
            url = _link_to_url(link)
            if url is not None:
                event.add("url", url)
                url_written = True

    cal.add_component(event)

    for child in child_events:
        cal.add_component(child)

    raw = cal.to_ical().decode("utf-8")
    return fixup(raw)
