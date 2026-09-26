# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JMAP Contacts objects (:rfc:`9610`).

AddressBook and ContactCard, as returned by ``AddressBook/get`` and
``ContactCard/get``. ContactCard properties follow JSContact (:rfc:`9553`).

Rights maps (``share_with``, ``my_rights``) and the raw JSContact ``name``/
``emails`` dicts are all kept as untyped dicts rather than typed
sub-objects, matching :class:`~calendaring_jmap.objects.calendar.JMAPCalendar`'s
own ``share_with``/``my_rights`` convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Sort key for an EmailAddress with no explicit ``pref`` (:rfc:`9553#section-1.5.3`
#: caps the real range at 1-100; one past that sorts after every explicit
#: value, matching "no preference set" being least preferred of all).
_UNSET_PREF_SORT_KEY = 101


@dataclass
class JMAPAddressBook:
    """A JMAP AddressBook object (:rfc:`9610#section-2`).

    Attributes:
        id: Server-assigned identifier.
        name: Display name, non-empty, at most 255 UTF-8 octets.
        description: Optional longer description.
        sort_order: Hint for display ordering (lower = first). Default 0.
        is_default: True for at most one address book in the account
            (:rfc:`9610#section-2` says this SHOULD be true for exactly
            one, but only MUST NOT be true for more than one), server-set.
        is_subscribed: Whether the user is subscribed to this address book.
        share_with: Map of Principal id (:rfc:`9670#section-2`) to a dict of
            AddressBookRights (``mayRead``, ``mayWrite``, ``mayShare``,
            ``mayDelete``). ``None`` if unshared or the server lacks
            :rfc:`9670` support (:rfc:`9610#section-2`), confirmed live on
            Cyrus. Confirmed live that Stalwart returns ``{}`` here instead
            when unshared, not ``None``: a caller checking "is this
            shared" should treat the value as falsy
            (``not address_book.share_with``), not test for ``is None``
            specifically.
        my_rights: AddressBookRights dict for the current user, server-set.
    """

    id: str
    name: str
    description: str | None = None
    sort_order: int = 0
    is_default: bool = False
    is_subscribed: bool = True
    share_with: dict | None = None
    my_rights: dict = field(default_factory=dict)

    @classmethod
    def from_jmap(cls, data: dict) -> JMAPAddressBook:
        """Construct a JMAPAddressBook from a raw JMAP AddressBook JSON dict.

        Unknown keys in ``data`` are silently ignored so that forward
        compatibility is maintained as the spec evolves.
        """
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description"),
            sort_order=data.get("sortOrder", 0),
            is_default=data.get("isDefault", False),
            is_subscribed=data.get("isSubscribed", True),
            share_with=data.get("shareWith"),
            my_rights=data.get("myRights", {}),
        )


@dataclass
class JMAPContact:
    """A JMAP ContactCard object (:rfc:`9610#section-3`), a JSContact Card
    (:rfc:`9553#section-2`) plus JMAP's own ``id``/``addressBookIds``.

    Attributes:
        id: Server-assigned identifier. May differ from ``uid``.
        uid: The card's own JSContact uid (:rfc:`9553#section-2.1.9`).
            RFC 9553 treats this as mandatory on every Card, but confirmed
            live that Stalwart's ``ContactCard/get`` never returns it at
            all, even when explicitly requested via ``properties``. Unlike
            ``id``, this is not required here: a strict-missing check would
            raise on every Stalwart contact. Cyrus does return it.
        address_book_ids: Map of AddressBook id to ``True``; a card belongs
            to at least one address book.
        kind: JSContact Card kind (:rfc:`9553#section-2.1.4`): one of
            ``"individual"`` (default), ``"group"``, ``"org"``,
            ``"location"``, ``"device"``, ``"application"``.
        name: Raw JSContact Name dict (:rfc:`9553#section-2.2.1`), or
            ``None``. Its own shape (``full`` vs. ``components``) is read
            through :meth:`display_name`, not by exposing typed fields here.
        emails: Raw JSContact EmailAddress map (:rfc:`9553#section-2.3.1`):
            ``{id: {"address": str, "contexts": {...}, "pref": int, ...}}``,
            or ``None``.
    """

    id: str
    uid: str | None = None
    address_book_ids: dict = field(default_factory=dict)
    kind: str = "individual"
    name: dict | None = None
    emails: dict | None = None

    @classmethod
    def from_jmap(cls, data: dict) -> JMAPContact:
        """Construct a JMAPContact from a raw JMAP ContactCard JSON dict.

        Unknown keys (including JSContact properties this client doesn't
        model, e.g. ``organizations``, ``addresses``, ``notes``) are
        silently ignored for forward compatibility.
        """
        return cls(
            id=data["id"],
            uid=data.get("uid"),
            address_book_ids=data.get("addressBookIds", {}),
            kind=data.get("kind", "individual"),
            name=data.get("name"),
            emails=data.get("emails"),
        )

    def display_name(self) -> str | None:
        """Best-effort human-readable name.

        :rfc:`9553#section-2.2.1.1`: a Name object has either ``full`` or
        ``components`` present, not necessarily both. Prefers ``full``;
        falls back to ``components`` otherwise. When ``isOrdered`` is true,
        a ``"separator"`` component's own ``value`` (or ``defaultSeparator``
        if there's no explicit one at that position, or a single space if
        neither is set) is inserted between the surrounding non-separator
        components, per the RFC's own guidance, e.g. ``given="Jean"``,
        ``separator="-"``, ``surname="Paul"`` joins to ``"Jean-Paul"``, not
        ``"Jean Paul"``. When ``isOrdered`` is false, the RFC forbids any
        ``"separator"`` component at all, so components are simply
        space-joined in list order. Returns ``None`` if neither ``full``
        nor ``components`` is present.
        """
        if not self.name:
            return None
        full = self.name.get("full")
        if full:
            return str(full)
        components = self.name.get("components")
        if not components:
            return None
        if not self.name.get("isOrdered"):
            return " ".join(str(c["value"]) for c in components if c.get("kind") != "separator")
        default_separator = self.name.get("defaultSeparator", " ")
        parts: list[str] = []
        pending_separator: str | None = None
        for component in components:
            if component.get("kind") == "separator":
                pending_separator = str(component["value"])
                continue
            if parts:
                parts.append(
                    pending_separator if pending_separator is not None else default_separator
                )
            parts.append(str(component["value"]))
            pending_separator = None
        return "".join(parts)

    def primary_email(self) -> str | None:
        """Return one email address for this contact, or ``None``.

        Picks the entry with the lowest ``pref`` (:rfc:`9553#section-1.5.3`:
        1 is most preferred, 100 least; an entry with no ``pref`` at all,
        or an explicit ``null``, is treated as least preferred of all).
        """
        if not self.emails:
            return None
        best = min(self.emails.values(), key=lambda e: e.get("pref") or _UNSET_PREF_SORT_KEY)
        return best.get("address")
