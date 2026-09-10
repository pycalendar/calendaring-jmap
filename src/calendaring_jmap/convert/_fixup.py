# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Fixups for iCalendar data that doesn't fully comply with RFC 5545.

Servers of any protocol, JMAP or CalDAV, can hand back iCalendar that
doesn't quite follow the spec: a missing DTSTAMP, a COMPLETED given as a
date instead of a datetime, a duplicated DTSTAMP, trailing whitespace that
breaks folding. None of this is protocol-specific; it's a property of the
calendar data itself, so calendaring-jmap fixes it up the same way
regardless of which protocol delivered it.

All logic here works on the ical string, not on parsed icalendar objects,
since broken data can cause the parse itself to fail.
"""

import datetime
import logging
import re

log = logging.getLogger("calendaring_jmap")

## Rate-limits the "data was modified" warning so a feed with many broken
## events doesn't flood the log.
_fixup_warning_count = 0


def _to_normal_str(text: str | bytes | None) -> str | None:
    """Decode bytes to str if needed, and normalize line endings to ``\\n``."""
    if text is None:
        return text
    if not isinstance(text, str):
        text = text.decode("utf-8")
    return text.replace("\r\n", "\n")


class _DiscardDuplicateLines:
    """Drops duplicate DTSTAMP/DURATION/DTEND/DUE lines within one component.

    Called line by line, in order, over the whole ical string. State resets
    at each ``BEGIN:V...`` line since duplication is scoped to one component.
    """

    def __init__(self) -> None:
        self.stamped = 0
        self.ended = 0

    def __call__(self, line: str) -> bool:
        if line.startswith("BEGIN:V"):
            self.stamped = 0
            self.ended = 0
        elif re.match("(DURATION|DTEND|DUE)[:;]", line):
            if self.ended:
                return False
            self.ended += 1
        elif re.match("DTSTAMP[:;]", line):
            if self.stamped:
                return False
            self.stamped += 1
        return True


def fixup(event: str | bytes) -> str:
    """Fix up iCalendar data that doesn't fully comply with RFC 5545.

    Applies, in order:

    1. ``COMPLETED`` given as a bare date is given a time (noon UTC),
       since the RFC requires a datetime.
    2. A ``CREATED`` timestamp at the epoch of year 1 (rather than absent)
       is moved to the Unix epoch instead, since some parsers choke on
       dates that old.
    3. Duplicated ``DTSTAMP`` lines within one component are dropped,
       keeping the first.
    4. Trailing whitespace is stripped, except where it's part of a folded
       continuation (RFC 5545 §3.1 folds at 75 octets, which can land right
       after a space that belongs to the value).
    5. A missing ``DTSTAMP`` (mandatory per the RFC) is added, generated at
       fixup time.
    6. Both ``DURATION`` and ``DTEND``/``DUE`` set on the same component
       (mutually exclusive per the RFC): the one that comes later in the
       component is dropped.

    Args:
        event: the iCalendar data, as returned by the server.

    Returns:
        The fixed-up iCalendar string. Data that was already compliant is
        returned unchanged.
    """
    text = _to_normal_str(event)
    if not text.endswith("\n"):
        text = text + "\n"

    # 1. COMPLETED as a bare date -> add a time.
    fixed = re.sub(r"COMPLETED(?:;VALUE=DATE)?:(\d+)(?=\s)", r"COMPLETED:\g<1>T120000Z", text)

    # 2. CREATED at year-1 epoch -> Unix epoch.
    fixed = re.sub("CREATED:00001231T000000Z", "CREATED:19700101T000000Z", fixed)
    fixed = re.sub(r"\\+(['\"])", r"\1", fixed)

    # 4. Trailing whitespace, except on a line continued by a fold.
    fixed = re.sub(r"[ \t]+$(?!\n[ \t])", "", fixed, flags=re.MULTILINE)

    # 5. Missing DTSTAMP -> add one.
    if "\nDTSTAMP:" not in fixed:
        if "\nEND" not in fixed:
            log.warning("fixup(): truncated iCalendar data (no END: line), skipping DTSTAMP fixup")
            return fixed
        dtstamp = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fixed = re.sub("(\nEND:(VTODO|VEVENT|VJOURNAL))", f"\nDTSTAMP:{dtstamp}\\1", fixed)

    # 3 and 6: drop duplicate DTSTAMP / extra DURATION-or-DTEND-DUE.
    fixed2 = "\n".join(filter(_DiscardDuplicateLines(), fixed.strip().split("\n"))) + "\n"

    if fixed2 != text:
        global _fixup_warning_count
        _fixup_warning_count += 1

        def _is_power_of_two(n: int) -> bool:
            return not (n & (n - 1))

        log_fn = log.warning if _is_power_of_two(_fixup_warning_count) else log.debug
        log_fn(
            "iCalendar data was modified to comply with RFC 5545 "
            "(the source server's data was not fully spec-compliant; "
            f"this is rate-limited, count={_fixup_warning_count})"
        )

    return fixed2
