# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""JMAP capability URNs and JSCalendar wire-value constants.

All JMAP capability strings and JSCalendar enum values used across the
package are defined here so they are never duplicated. Every other module
should import from this file.
"""

#: Core JMAP capability (:rfc:`8620`). Required in every ``using`` declaration.
CORE_CAPABILITY = "urn:ietf:params:jmap:core"

#: JMAP Calendars capability (draft-ietf-jmap-calendars). Required to use
#: any Calendar/CalendarEvent method.
CALENDAR_CAPABILITY = "urn:ietf:params:jmap:calendars"

#: JMAP Tasks capability (draft-ietf-jmap-tasks). Required to use any
#: TaskList/Task method. No known public server implements this draft.
TASK_CAPABILITY = "urn:ietf:params:jmap:tasks"

#: JMAP Sharing Principal capability (:rfc:`9670#section-1.5.1`). Required to
#: use Principal/* methods, including Principal/getAvailability. This is the
#: capability actually used to detect free/busy support: confirmed live
#: that Cyrus implements Principal/getAvailability without ever advertising
#: the draft's own narrower ``:availability`` sub-capability, so this base
#: capability is the real gate, not that one.
PRINCIPALS_CAPABILITY = "urn:ietf:params:jmap:principals"

#: JMAP Contacts capability (:rfc:`9610`). Required to use AddressBook/ContactCard
#: methods. Confirmed live that both Cyrus and Stalwart advertise this capability,
#: under the same account already used for calendars on both servers.
CONTACTS_CAPABILITY = "urn:ietf:params:jmap:contacts"

# Participant.participationStatus values (:rfc:`8984#section-4.4.6`).
# Default is PARTICIPATION_STATUS_NEEDS_ACTION when the property is absent.
PARTICIPATION_STATUS_NEEDS_ACTION = "needs-action"
PARTICIPATION_STATUS_ACCEPTED = "accepted"
PARTICIPATION_STATUS_DECLINED = "declined"
PARTICIPATION_STATUS_TENTATIVE = "tentative"
PARTICIPATION_STATUS_DELEGATED = "delegated"

# BusyPeriod.busyStatus default (draft-ietf-jmap-calendars section 2.2)
# when the property is absent from the response. The other two enum values
# ("confirmed", "tentative") pass through server responses verbatim and
# have no internal call site of their own, so unlike PARTICIPATION_STATUS_*
# they aren't defined here as constants.
BUSY_STATUS_UNAVAILABLE = "unavailable"

#: Link.rel value (:rfc:`8984#section-1.4.11`) marking a Link as an
#: attachment. The "rel" property itself is defined in RFC 8984, but this
#: specific value originates in the IANA Link Relations registry's
#: original seed list, :rfc:`4287#section-4.2.7.2` (Atom), not RFC 8984
#: itself.
LINK_REL_ENCLOSURE = "enclosure"

#: :meth:`~datetime.datetime.strftime`/:meth:`~datetime.datetime.strptime`
#: format for a JMAP ``UTCDateTime`` string (:rfc:`8984#section-1.4.4`):
#: always UTC, always ``Z``-suffixed, no fractional seconds.
UTC_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: :meth:`~datetime.datetime.strftime`/:meth:`~datetime.datetime.strptime`
#: format for a JMAP ``LocalDateTime`` string (:rfc:`8984#section-1.4.5`):
#: no time zone/offset information, no fractional seconds. Used for
#: JSCalendar ``start``, recurrence override keys, and RRULE ``until``.
LOCAL_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S"
