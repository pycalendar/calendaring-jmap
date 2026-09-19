# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""JMAP capability URNs and JSCalendar wire-value constants.

All JMAP capability strings and JSCalendar enum values used across the
package are defined here so they are never duplicated. Every other module
should import from this file.
"""

#: Core JMAP capability (RFC 8620) — required in every ``using`` declaration.
CORE_CAPABILITY = "urn:ietf:params:jmap:core"

#: JMAP Calendars capability (draft-ietf-jmap-calendars) — required to use
#: any Calendar/CalendarEvent method.
CALENDAR_CAPABILITY = "urn:ietf:params:jmap:calendars"

#: JMAP Tasks capability (draft-ietf-jmap-tasks) — required to use any
#: TaskList/Task method. No known public server implements this draft.
TASK_CAPABILITY = "urn:ietf:params:jmap:tasks"

# Participant.participationStatus values (RFC 8984 §4.4.6). Default is
# PARTICIPATION_STATUS_NEEDS_ACTION when the property is absent.
PARTICIPATION_STATUS_NEEDS_ACTION = "needs-action"
PARTICIPATION_STATUS_ACCEPTED = "accepted"
PARTICIPATION_STATUS_DECLINED = "declined"
PARTICIPATION_STATUS_TENTATIVE = "tentative"
PARTICIPATION_STATUS_DELEGATED = "delegated"
