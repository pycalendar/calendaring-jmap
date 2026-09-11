# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""JMAP capability URN constants.

All JMAP capability strings are defined here so they are never duplicated
across the package. Every other module should import from this file.
"""

#: Core JMAP capability (RFC 8620) — required in every ``using`` declaration.
CORE_CAPABILITY = "urn:ietf:params:jmap:core"

#: JMAP Calendars capability (draft-ietf-jmap-calendars) — required to use
#: any Calendar/CalendarEvent method.
CALENDAR_CAPABILITY = "urn:ietf:params:jmap:calendars"

#: JMAP Tasks capability (draft-ietf-jmap-tasks) — required to use any
#: TaskList/Task method. No known public server implements this draft.
TASK_CAPABILITY = "urn:ietf:params:jmap:tasks"
