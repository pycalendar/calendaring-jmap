``ical_to_jscal`` now raises ``ValueError`` when the master VEVENT is missing ``UID`` or ``DTSTART`` (both mandatory per RFC 5545), instead of a bare, undocumented ``KeyError``.
