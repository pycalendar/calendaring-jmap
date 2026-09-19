# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
iCalendar → JSCalendar conversion (RFC 5545 → RFC 8984).

Public API:
    ical_to_jscal(ical_str, calendar_id=None) -> dict

The output dict is a raw JSCalendar CalendarEvent object suitable for passing
directly to CalendarEvent/set.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta

import icalendar
from icalendar.timezone.tzid import tzid_from_dt

from calendaring_jmap.constants import (
    PARTICIPATION_STATUS_ACCEPTED,
    PARTICIPATION_STATUS_DECLINED,
    PARTICIPATION_STATUS_DELEGATED,
    PARTICIPATION_STATUS_NEEDS_ACTION,
    PARTICIPATION_STATUS_TENTATIVE,
)
from calendaring_jmap.convert._fixup import fixup
from calendaring_jmap.convert._utils import _format_local_dt, _timedelta_to_duration

log = logging.getLogger("calendaring_jmap")

# RFC 5545 STATUS -> RFC 8984 status; module-level constant (cf. _CLASS_MAP etc.)
_STATUS_ICAL_TO_JSCAL = {
    "CONFIRMED": "confirmed",
    "TENTATIVE": "tentative",
    "CANCELLED": "cancelled",
}

_CLASS_MAP = {
    "PRIVATE": "private",
    "CONFIDENTIAL": "secret",
}

_PARTSTAT_MAP = {
    "NEEDS-ACTION": PARTICIPATION_STATUS_NEEDS_ACTION,
    "ACCEPTED": PARTICIPATION_STATUS_ACCEPTED,
    "DECLINED": PARTICIPATION_STATUS_DECLINED,
    "TENTATIVE": PARTICIPATION_STATUS_TENTATIVE,
    "DELEGATED": PARTICIPATION_STATUS_DELEGATED,
}

_CUTYPE_MAP = {
    "INDIVIDUAL": "individual",
    "GROUP": "group",
    "RESOURCE": "resource",
    "ROOM": "room",
}

_BYDAY_ABBR = {"SU", "MO", "TU", "WE", "TH", "FR", "SA"}


def _prop_date_or_datetime(prop) -> datetime | date:
    """Return a vDDDTypes property's ``.dt``, narrowed to ``date | datetime``.

    ``Component.__getitem__`` is typed as a large union of every possible
    property value class, most of which have no ``.dt`` attribute at all;
    ``getattr`` sidesteps that fan-out, and the isinstance check narrows the
    result for callers that need a concrete date/datetime to operate on.
    """
    dt = getattr(prop, "dt", None)
    if not isinstance(dt, datetime | date):
        raise ValueError(f"Expected a date or datetime property value, got {dt!r}")
    return dt


def _prop_timedelta(prop) -> timedelta:
    """Return a vDDDTypes property's ``.dt``, narrowed to ``timedelta``.

    See :func:`_prop_date_or_datetime` for why ``getattr`` is used here.
    """
    dt = getattr(prop, "dt", None)
    if not isinstance(dt, timedelta):
        raise ValueError(f"Expected a timedelta property value, got {dt!r}")
    return dt


def _dtstart_to_jscal(dtstart_prop) -> tuple[str, str | None, bool]:
    """Extract JSCalendar start, timeZone, showWithoutTime from a DTSTART property.

    Returns:
        (start_str, time_zone, show_without_time)
    """
    dt = dtstart_prop.dt

    if isinstance(dt, date) and not isinstance(dt, datetime):
        # VALUE=DATE — all-day event
        return f"{dt.isoformat()}T00:00:00", None, True

    if dt.tzinfo is not None and dt.utcoffset() == timedelta(0):
        # UTC — JSCalendar start is LocalDateTime; express via timeZone="Etc/UTC"
        return dt.strftime("%Y-%m-%dT%H:%M:%S"), "Etc/UTC", False

    if dt.tzinfo is not None:
        # Timezone-aware. RFC 8984 requires an IANA name; a raw TZID param
        # can be a Windows or vendor-prefixed name instead, so resolve via
        # tzid_from_dt() rather than using the param string directly.
        tz_str = tzid_from_dt(dt)
        return dt.strftime("%Y-%m-%dT%H:%M:%S"), tz_str, False

    # Floating (no timezone)
    return dt.strftime("%Y-%m-%dT%H:%M:%S"), None, False


def _rrule_to_jscal(rrule_prop, tzinfo=None) -> dict:
    """Convert an iCalendar RRULE property to a JSCalendar RecurrenceRule dict.

    Always emits @type, interval, rscale, skip, firstDayOfWeek to match the
    fields Cyrus returns — makes round-trip comparison predictable.

    ``tzinfo`` is the event's timezone; ``until`` is a LocalDateTime in that
    zone, so a UTC ``UNTIL`` off the wire has to be converted, not truncated.
    """
    rule: dict = {
        "@type": "RecurrenceRule",
        "rscale": "gregorian",
        "skip": "omit",
    }

    freq_list = rrule_prop.get("FREQ", [])
    if not freq_list:
        raise ValueError(f"RRULE is missing required FREQ component: {rrule_prop!r}")
    rule["frequency"] = freq_list[0].lower()

    interval_list = rrule_prop.get("INTERVAL", [])
    rule["interval"] = int(interval_list[0]) if interval_list else 1

    wkst_list = rrule_prop.get("WKST", [])
    rule["firstDayOfWeek"] = wkst_list[0].lower() if wkst_list else "mo"

    count_list = rrule_prop.get("COUNT", [])
    if count_list:
        rule["count"] = int(count_list[0])

    until_list = rrule_prop.get("UNTIL", [])
    if until_list:
        rule["until"] = _format_local_dt(until_list[0], tzinfo)

    byday_list = rrule_prop.get("BYDAY", [])
    if byday_list:
        by_day = []
        for item in byday_list:
            s = str(item)
            day_abbr = s.lstrip("+-0123456789")
            nth_str = s[: len(s) - len(day_abbr)]
            nday: dict = {"@type": "NDay", "day": day_abbr.lower()}
            if nth_str:
                nday["nthOfPeriod"] = int(nth_str)
            by_day.append(nday)
        rule["byDay"] = by_day

    bymonth_list = rrule_prop.get("BYMONTH", [])
    if bymonth_list:
        rule["byMonth"] = [str(m) for m in bymonth_list]

    bymonthday = rrule_prop.get("BYMONTHDAY", [])
    if bymonthday:
        rule["byMonthDay"] = [int(d) for d in bymonthday]

    byyearday = rrule_prop.get("BYYEARDAY", [])
    if byyearday:
        rule["byYearDay"] = [int(d) for d in byyearday]

    byweekno = rrule_prop.get("BYWEEKNO", [])
    if byweekno:
        rule["byWeekNo"] = [int(n) for n in byweekno]

    byhour = rrule_prop.get("BYHOUR", [])
    if byhour:
        rule["byHour"] = [int(h) for h in byhour]
    byminute = rrule_prop.get("BYMINUTE", [])
    if byminute:
        rule["byMinute"] = [int(m) for m in byminute]
    bysecond = rrule_prop.get("BYSECOND", [])
    if bysecond:
        rule["bySecond"] = [int(s) for s in bysecond]

    bysetpos = rrule_prop.get("BYSETPOS", [])
    if bysetpos:
        rule["bySetPosition"] = [int(p) for p in bysetpos]

    return rule


def _first_recurrence_rule(master: dict, ical_key: str, tzinfo=None) -> dict | None:
    """Return the JSCalendar RecurrenceRule for the first ``ical_key`` line on ``master``.

    ``ical_key`` is ``"RRULE"`` or ``"EXRULE"``. Logs a warning if more than
    one line is present; see the comment above this function's call sites
    for why only the first is kept.
    """
    rules = master.get(ical_key)
    if rules is None:
        return None
    if not isinstance(rules, list):
        rules = [rules]
    if len(rules) > 1:
        log.warning(
            "ical_to_jscal(): VEVENT has %d %s lines, only the first is kept "
            "(neither test server this repo targets accepts more than one)",
            len(rules),
            ical_key,
        )
    return _rrule_to_jscal(rules[0], tzinfo)


def _exdate_to_overrides(exdate_prop, tzinfo=None) -> dict:
    """Convert an EXDATE property (single or list) to recurrenceOverrides entries.

    ``tzinfo`` is the event's timezone — see :func:`_format_local_dt`.

    Returns:
        Dict mapping LocalDateTime/UTCDateTime string → {"excluded": True}
    """
    # EXDATE may be a single vDDDLists or a list of them
    if not isinstance(exdate_prop, list):
        exdate_prop = [exdate_prop]

    overrides: dict = {}
    for ex in exdate_prop:
        dts = getattr(ex, "dts", [ex])
        for dt_prop in dts:
            dt = getattr(dt_prop, "dt", dt_prop)
            overrides[_format_local_dt(dt, tzinfo)] = {"excluded": True}
    return overrides


def _cal_address_to_imip_and_email(addr: str) -> tuple[str, str | None]:
    """Split a CAL-ADDRESS value into its ``calendarAddress`` URI and, if it is one, its bare email.

    ORGANIZER/ATTENDEE values are URIs (RFC 5545 §3.3.3) and are not
    required to use the ``mailto:`` scheme (e.g. ``sip:alice@example.com``).
    RFC 8984's Participant ``email`` property is specifically an
    RFC 5322 addr-spec, not an arbitrary URI, so a non-mailto address is
    kept as-is for ``calendarAddress`` (not double-wrapped in a spurious
    ``mailto:``) and ``email`` is left unset rather than populated with a
    value that isn't actually an email address.
    """
    if addr.startswith("mailto:"):
        return addr, addr.removeprefix("mailto:")
    if ":" in addr:
        # Some other URI scheme (sip:, etc.); pass through as-is.
        return addr, None
    return f"mailto:{addr}", addr


def _organizer_to_participant(organizer) -> tuple[str, dict]:
    """Convert an ORGANIZER property to a (participant_id, Participant dict) tuple."""
    imip, email = _cal_address_to_imip_and_email(str(organizer))
    pid = str(uuid.uuid4())
    p: dict = {
        "roles": {"owner": True, "organizer": True},
        # RFC 8984's own Participant object has no calendarAddress property;
        # it belongs to JMAP Calendars' ParticipantIdentity/Principal objects
        # instead, and sendTo (a map of delivery method to URI) is what
        # RFC 8984 actually defines for this. Cyrus's CalendarEvent/set
        # rejects sendTo outright with invalidProperties on both create and
        # update, and separately requires calendarAddress, which RFC 8984
        # does not define here at all. Verified live against a running Cyrus
        # container: sendTo alone rejected, calendarAddress alone accepted,
        # both present still rejected for sendTo. calendarAddress is set to
        # sendTo's would-be "imip" value, its natural equivalent per
        # ParticipantIdentity's own definition. sendTo itself is optional in
        # RFC 8984, so omitting it is a real compatibility tradeoff, not a
        # spec violation; Stalwart accepts calendarAddress-only fine too.
        "calendarAddress": imip,
    }
    cn = organizer.params.get("CN")
    if cn:
        p["name"] = str(cn)
    if email is not None:
        p["email"] = email
    return pid, p


def _attendee_to_participant(attendee) -> tuple[str, dict]:
    """Convert an ATTENDEE property to a (participant_id, Participant dict) tuple."""
    imip, email = _cal_address_to_imip_and_email(str(attendee))
    pid = str(uuid.uuid4())
    p: dict = {
        "roles": {"attendee": True},
        # See _organizer_to_participant for why calendarAddress is set
        # instead of sendTo.
        "calendarAddress": imip,
    }
    if email is not None:
        p["email"] = email
    cn = attendee.params.get("CN")
    if cn:
        p["name"] = str(cn)

    partstat = attendee.params.get("PARTSTAT")
    if partstat:
        p["participationStatus"] = _PARTSTAT_MAP.get(partstat.upper(), partstat.lower())

    rsvp = attendee.params.get("RSVP", "")
    if str(rsvp).upper() == "TRUE":
        p["expectReply"] = True

    cutype = attendee.params.get("CUTYPE")
    if cutype:
        p["kind"] = _CUTYPE_MAP.get(cutype.upper(), cutype.lower())

    role = attendee.params.get("ROLE")
    if role and role.upper() == "CHAIR":
        p["roles"]["chair"] = True

    return pid, p


def _valarm_to_alert(alarm) -> tuple[str, dict]:
    """Convert a VALARM component to a (alert_id, Alert dict) tuple.

    Trigger is emitted as a plain SignedDuration string (e.g. "-PT15M") or
    UTCDateTime string per the JSCalendar Alert spec (RFC 8984 §4.5.2).
    """
    alert_id = str(uuid.uuid4())
    action = str(alarm.get("ACTION", "display")).lower()
    alert: dict = {"action": action}

    trigger_prop = alarm.get("TRIGGER")
    if trigger_prop is not None:
        trigger_val = trigger_prop.dt
        if isinstance(trigger_val, timedelta):
            # Relative trigger — convert to SignedDuration string
            alert["trigger"] = _timedelta_to_duration(trigger_val)
            if str(trigger_prop.params.get("RELATED", "START")).upper() == "END":
                alert["relativeTo"] = "end"
        elif isinstance(trigger_val, datetime):
            # Absolute trigger — UTCDateTime string
            alert["trigger"] = trigger_val.strftime("%Y-%m-%dT%H:%M:%SZ")

    description = alarm.get("DESCRIPTION")
    if description:
        alert["description"] = str(description)

    return alert_id, alert


def _location_str_to_jscal(location_str: str) -> dict:
    """Convert a LOCATION string to a JSCalendar locations map entry.

    Returns:
        {"<uuid>": {"name": location_str}}
    """
    return {str(uuid.uuid4()): {"name": location_str}}


def _categories_to_keywords(categories_prop) -> dict:
    """Convert a CATEGORIES property to a JSCalendar keywords map.

    icalendar returns one of three types depending on how CATEGORIES appears:
    - vCategory (single CATEGORIES line, possibly multi-value): access .cats
    - list of vCategory (multiple CATEGORIES lines): flatten .cats from each
    - vText (rare, single bare string value): str() and comma-split
    """
    if hasattr(categories_prop, "cats"):
        values = [str(c) for c in categories_prop.cats]
    elif isinstance(categories_prop, list):
        values = []
        for item in categories_prop:
            if hasattr(item, "cats"):
                values.extend(str(c) for c in item.cats)
            else:
                values.append(str(item))
    else:
        raw = str(categories_prop)
        values = [v.strip() for v in raw.split(",") if v.strip()]

    return {v: True for v in values}


def ical_to_jscal(ical_str: str, calendar_id: str | None = None) -> dict:
    """Convert an iCalendar string to a JSCalendar CalendarEvent dict (RFC 8984).

    Processes the first VEVENT found in the string. Any sibling VEVENTs with a
    RECURRENCE-ID are folded into the ``recurrenceOverrides`` map of the master
    event. EXDATE entries are also added to ``recurrenceOverrides``.

    Args:
        ical_str: A VCALENDAR string (or bare VEVENT — fixup() normalises it).
        calendar_id: If provided, sets ``calendarIds: {calendar_id: true}``
            on the output. Required when the result will be used in
            ``CalendarEvent/set`` (the server needs to know which calendar).

    Returns:
        Raw JSCalendar dict suitable for passing directly to ``CalendarEvent/set``.

    Raises:
        ValueError: If no VEVENT component is found.
    """
    # Normalize iCal string (fixes common server-generated violations)
    fixed = fixup(ical_str)

    cal = icalendar.Calendar.from_ical(fixed)

    # Split subcomponents into master VEVENTs and override VEVENTs
    master: icalendar.Event | None = None
    override_components: list[icalendar.Event] = []

    for component in cal.subcomponents:
        if not isinstance(component, icalendar.Event):
            continue
        if component.get("RECURRENCE-ID") is not None:
            override_components.append(component)
        elif master is None:
            master = component

    if master is None:
        raise ValueError("No VEVENT component found in iCalendar string")

    uid = str(master["UID"])
    summary = master.get("SUMMARY")
    title = str(summary) if summary else ""
    dtstart_prop = master["DTSTART"]
    start, time_zone, show_without_time = _dtstart_to_jscal(dtstart_prop)

    ## The event's own timezone.  Every LocalDateTime slot below (RRULE
    ## until, EXDATE keys, RECURRENCE-ID keys) is expressed in it, so it has
    ## to be known before any of them can be formatted — which is why the
    ## override keys cannot be built in the loop above.
    event_tzinfo = getattr(getattr(dtstart_prop, "dt", None), "tzinfo", None)

    overrides_by_recurrence_id: dict[str, icalendar.Event] = {
        _format_local_dt(
            _prop_date_or_datetime(component["RECURRENCE-ID"]), event_tzinfo
        ): component
        for component in override_components
    }

    dtend_prop = master.get("DTEND")
    if master.get("DURATION"):
        duration = _timedelta_to_duration(_prop_timedelta(master["DURATION"]))
    elif dtend_prop is not None:
        duration = _timedelta_to_duration(
            _prop_date_or_datetime(dtend_prop) - _prop_date_or_datetime(dtstart_prop)
        )
    else:
        duration = "P0D"

    jscal: dict = {
        "@type": "Event",
        # RFC 8984 itself has no "version" property; draft-ietf-calext-
        # jscalendarbis (the 2.0 successor spec) adds it and registers "1.0"
        # for data conforming to RFC 8984, "2.0" for jscalendarbis. This
        # conversion does not emit any 2.0-only properties (COORDINATES,
        # SHOW-WITHOUT-TIME), so "1.0" would be the strictly accurate value.
        # We send "2.0" anyway: Cyrus's current test image rejects "1.0"
        # outright (requires "2.0" on every Event), and this repo's own CI
        # and integration tests run against that same image. Revisit once M4
        # adds real jscalendarbis support, or once older servers matter more
        # than this one.
        "version": "2.0",
        "uid": uid,
        "title": title,
        "start": start,
        "duration": duration,
    }

    if calendar_id is not None:
        jscal["calendarIds"] = {calendar_id: True}

    if time_zone is not None:
        jscal["timeZone"] = time_zone

    if show_without_time:
        jscal["showWithoutTime"] = True

    description = master.get("DESCRIPTION")
    if description:
        jscal["description"] = str(description)

    sequence = master.get("SEQUENCE")
    if sequence is not None:
        jscal["sequence"] = int(sequence)

    priority = master.get("PRIORITY")
    if priority is not None:
        p_int = int(priority)
        if p_int != 0:
            jscal["priority"] = p_int

    cls = master.get("CLASS")
    if cls:
        privacy = _CLASS_MAP.get(str(cls).upper())
        if privacy:
            jscal["privacy"] = privacy

    transp = master.get("TRANSP")
    if transp and str(transp).upper() == "TRANSPARENT":
        jscal["freeBusyStatus"] = "free"

    color = master.get("COLOR")
    if color:
        jscal["color"] = str(color)

    categories = master.get("CATEGORIES")
    if categories is not None:
        kw = _categories_to_keywords(categories)
        if kw:
            jscal["keywords"] = kw

    location = master.get("LOCATION")
    if location:
        jscal["locations"] = _location_str_to_jscal(str(location))

    status = master.get("STATUS")
    if status:
        jscal_status = _STATUS_ICAL_TO_JSCAL.get(str(status).upper())
        if jscal_status:
            jscal["status"] = jscal_status

    participants: dict = {}
    organizer = master.get("ORGANIZER")
    if organizer is not None:
        pid, p = _organizer_to_participant(organizer)
        participants[pid] = p

    # .get() returns a single vCalAddress or a list; normalise to list
    raw_attendees = master.get("ATTENDEE")
    if raw_attendees is None:
        attendees = []
    elif isinstance(raw_attendees, list):
        attendees = raw_attendees
    else:
        attendees = [raw_attendees]
    for attendee in attendees:
        pid, p = _attendee_to_participant(attendee)
        participants[pid] = p

    if participants:
        jscal["participants"] = participants

    # RFC 8984 §4.3.3 defines "recurrenceRules" as RecurrenceRule[], since an
    # event can in principle have more than one RRULE. Neither test server
    # this repo targets actually implements that: Cyrus (pinned prodId
    # "CyrusIMAP.org/Cyrus 3.13.7-545-gc04cece19") rejects the array outright
    # with invalidProperties, and Stalwart's own CalendarEvent/get also
    # returns the older singular "recurrenceRule" key, not the array,
    # confirmed live against both. We follow both servers here, same as the
    # "version" and calendarAddress/sendTo fixes: only the first RRULE is
    # kept if more than one is present, which is a real but rare data-loss
    # case (multiple RRULE lines on one VEVENT are uncommon in practice), so
    # it's logged rather than silent or raised.
    # Full recurrence override fidelity is its own separate M4 milestone item.
    rule = _first_recurrence_rule(master, "RRULE", event_tzinfo)
    if rule is not None:
        jscal["recurrenceRule"] = rule

    exrule = _first_recurrence_rule(master, "EXRULE", event_tzinfo)
    if exrule is not None:
        jscal["excludedRecurrenceRule"] = exrule

    recurrence_overrides: dict = {}

    exdate = master.get("EXDATE")
    if exdate is not None:
        recurrence_overrides.update(_exdate_to_overrides(exdate, event_tzinfo))

    for rid_key, child in overrides_by_recurrence_id.items():
        # Build a patch: only fields that differ from the master
        patch: dict = {}
        child_summary = child.get("SUMMARY")
        if child_summary and str(child_summary) != title:
            patch["title"] = str(child_summary)
        child_start_prop = child.get("DTSTART")
        if child_start_prop:
            child_start, _, _ = _dtstart_to_jscal(child_start_prop)
            if child_start != start:
                patch["start"] = child_start
        child_duration_prop = child.get("DURATION")
        if child_duration_prop:
            child_dur = _timedelta_to_duration(child_duration_prop.dt)
            if child_dur != duration:
                patch["duration"] = child_dur
        child_description = child.get("DESCRIPTION")
        if child_description and str(child_description) != jscal.get("description"):
            patch["description"] = str(child_description)
        recurrence_overrides[rid_key] = patch or {}

    if recurrence_overrides:
        jscal["recurrenceOverrides"] = recurrence_overrides

    alarms = [c for c in master.subcomponents if getattr(c, "name", None) == "VALARM"]
    if alarms:
        alerts: dict = {}
        for alarm in alarms:
            alert_id, alert = _valarm_to_alert(alarm)
            alerts[alert_id] = alert
        jscal["alerts"] = alerts

    return jscal
