# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JMAP event attachment object.

Wraps a JSCalendar Link (:rfc:`8984#section-1.4.11`) whose ``rel`` marks it
as an attachment, as returned in a CalendarEvent's ``links`` property.
"""

from __future__ import annotations

from dataclasses import dataclass

from calendaring_jmap.constants import LINK_REL_ENCLOSURE


@dataclass
class JMAPAttachment:
    """An event attachment, backed by a JSCalendar Link object.

    Attributes:
        link_id: The key under which this Link appears in the event's
            ``links`` map.
        href: URL from which the attachment can be fetched, or ``None``
            if the Link carries a ``blobId`` instead (a calendars-draft
            extension this client does not itself write, see
            :meth:`~calendaring_jmap.client.JMAPClient.attach_to_event`).
        blob_id: The referenced blob's id, when the server returns one.
        title: Human-readable description of the attachment.
        content_type: Media type of the attachment, if known.
        size: Size in octets, if known.
    """

    link_id: str
    href: str | None = None
    blob_id: str | None = None
    title: str | None = None
    content_type: str | None = None
    size: int | None = None

    @classmethod
    def from_jmap(cls, link_id: str, data: dict) -> JMAPAttachment:
        """Construct a JMAPAttachment from a raw JSCalendar Link dict.

        Unknown keys in ``data`` are silently ignored so that forward
        compatibility is maintained as the spec evolves.
        """
        return cls(
            link_id=link_id,
            href=data.get("href"),
            blob_id=data.get("blobId"),
            title=data.get("title"),
            content_type=data.get("contentType"),
            size=data.get("size"),
        )

    @staticmethod
    def is_attachment(link_data: dict) -> bool:
        """Return whether a raw JSCalendar Link dict represents an attachment.

        A Link is an attachment when its ``rel`` is ``"enclosure"``
        (:rfc:`8984#section-1.4.11`); other ``links`` entries (e.g. a
        conference URL) are not.

        Known Cyrus limitation, confirmed live: Cyrus (3.13.7-545-gc04cece19)
        accepts a Link with ``rel: "enclosure"`` with no error, but does not
        reliably persist it (other ``rel`` values round-trip fine, isolating
        this to that one value). An attachment written by
        :meth:`~calendaring_jmap.client.JMAPClient.attach_to_event` may
        therefore not be classified as one by this method when read back
        from Cyrus. Stalwart has no such issue.
        """
        return link_data.get("rel") == LINK_REL_ENCLOSURE
