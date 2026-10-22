# This file is part of Gajim.
#
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

from typing import Literal
from typing import NamedTuple

import unicodedata

from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Gtk
from nbxmpp import JID

from gajim.common import app
from gajim.common.const import AvatarSize
from gajim.common.i18n import _
from gajim.common.modules.contacts import BareContact
from gajim.common.modules.contacts import GroupchatContact
from gajim.common.types import ChatContactT

from gajim.gtk.chat_list_row import ChatListRow
from gajim.gtk.util.misc import scroll_to_listbox_row


class SearchCandidate(NamedTuple):
    account: str
    jid: JID
    name: str
    type: Literal["chat", "groupchat", "pm"]
    workspace_id: str | None
    timestamp: float
    contact: BareContact | GroupchatContact | None
    positions: list[int]


def _normalize(text: str) -> tuple[str, list[int]]:
    """Casefold and strip accents. The returned list maps every character of the
    normalized text to its index in `text`.
    """

    normalized: list[str] = []
    index_map: list[int] = []
    for index, char in enumerate(text):
        decomposed = unicodedata.normalize("NFKD", char.casefold())
        for part in decomposed:
            if unicodedata.combining(part):
                continue
            normalized.append(part)
            index_map.append(index)
    return "".join(normalized), index_map


def _find_token(token: str, text: str) -> tuple[int, list[int]] | None:
    """Return (score, positions in `text`) for one query word, or None."""

    word_starts = [
        i
        for i, char in enumerate(text)
        if char.isalnum() and (i == 0 or not text[i - 1].isalnum())
    ]

    for start in word_starts:
        if text.startswith(token, start):
            return 3, list(range(start, start + len(token)))

    start = text.find(token)
    if start != -1:
        return 2, list(range(start, start + len(token)))

    if len(token) >= 3:
        positions: list[int] = []
        cursor = 0
        for char in token:
            found = text.find(char, cursor)
            if found == -1:
                return None
            positions.append(found)
            cursor = found + 1
        return 1, positions
    return None


def fuzzy_match(query: str, text: str) -> tuple[int, list[int]]:
    """Return (score, matched character indices of `text`), score 0 means no match.

    Every word of the query has to match somewhere in the text, regardless of
    the order ("silva mar" finds "Maria Silva"). A word matches as
    prefix of a text word, as substring, or (for words with 3+ characters) as
    scattered characters.
    """

    tokens, _ = _normalize(query)
    tokens_list = tokens.split()
    if not tokens_list:
        return 0, []

    normalized_text, index_map = _normalize(text)

    total = 0
    positions: set[int] = set()
    for token in tokens_list:
        found = _find_token(token, normalized_text)
        if found is None:
            return 0, []
        score, token_positions = found
        total += score
        positions.update(index_map[i] for i in token_positions)
    return total, sorted(positions)


def fuzzy_score(query: str, text: str) -> int:
    return fuzzy_match(query, text)[0]


def get_search_name(contact: ChatContactT, name: str) -> str:
    """Name as shown in the results: "Nickname (Real name)" if they differ."""

    if isinstance(contact, BareContact) and name == contact.name:
        # Same convention as the "Edit display name" dialog
        roster_name = contact.roster_name
        if roster_name and roster_name.casefold() != name.casefold():
            return f"{name} ({roster_name})"
    return name


def highlight_markup(text: str, positions: list[int]) -> str:
    marked = set(positions)
    parts: list[str] = []
    for index, char in enumerate(text):
        escaped = GLib.markup_escape_text(char)
        if index in marked:
            parts.append(f'<span weight="bold" foreground="#7fd962">{escaped}</span>')
        else:
            parts.append(escaped)
    return "".join(parts)


class ChatSearchRow(Gtk.ListBoxRow):
    def __init__(self, candidate: SearchCandidate, subtitle: str) -> None:
        Gtk.ListBoxRow.__init__(self)
        self.candidate = candidate

        # Same look as the rows of the chat list
        self.add_css_class("chatlist-row")

        box = Gtk.Box(spacing=12, margin_top=2, margin_bottom=2)
        box.set_margin_end(6)

        avatar = Gtk.Image()
        avatar.set_pixel_size(AvatarSize.ROSTER)
        if candidate.contact is not None:
            texture = candidate.contact.get_avatar(
                AvatarSize.ROSTER, self.get_scale_factor()
            )
            avatar.set_from_paintable(texture)
        box.append(avatar)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        name_label = Gtk.Label(xalign=0)
        name_label.set_markup(highlight_markup(candidate.name, candidate.positions))
        name_label.set_ellipsize(3)  # Pango.EllipsizeMode.END
        labels.append(name_label)

        subtitle_label = Gtk.Label(label=subtitle, xalign=0)
        subtitle_label.set_ellipsize(3)
        subtitle_label.add_css_class("dim-label")
        subtitle_label.add_css_class("caption")
        labels.append(subtitle_label)

        labels.set_hexpand(True)
        box.append(labels)
        self.set_child(box)


class ChatSearchResults(Gtk.ListBox):
    """Search results over the chats of all workspaces and the contacts
    which do not have a chat yet.
    """

    __gtype_name__ = "ChatSearchResults"
    __gsignals__ = {
        "chat-activated": (GObject.SignalFlags.RUN_LAST, None, (str, object)),
        "new-chat-activated": (GObject.SignalFlags.RUN_LAST, None, (str, object)),
    }

    def __init__(self) -> None:
        Gtk.ListBox.__init__(self)
        self.add_css_class("chatlist")
        self.add_css_class("chat-search-results")
        self.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.set_activate_on_single_click(True)
        self.connect("row-activated", self._on_row_activated)

    def set_query(self, query: str, chat_rows: list[ChatListRow]) -> None:
        scored: list[tuple[int, int, float, SearchCandidate]] = []

        open_chats: set[tuple[str, JID]] = set()
        for row in chat_rows:
            open_chats.add((row.account, row.jid))
            name = get_search_name(row.contact, row.contact_name)
            score, positions = fuzzy_match(query, name)
            if score == 0:
                continue
            candidate = SearchCandidate(
                row.account,
                row.jid,
                name,
                row.type,
                row.workspace_id,
                row.timestamp,
                row.contact,  # pyright: ignore
                positions,
            )
            scored.append((0, score, row.timestamp, candidate))

        for account in app.settings.get_active_accounts():
            client = app.get_client(account)
            contacts_module = client.get_module("Contacts")
            for jid, _data in client.get_module("Roster").iter():
                if (account, jid) in open_chats:
                    continue

                contact = contacts_module.get_contact(jid)
                if not isinstance(contact, BareContact):
                    continue

                name = get_search_name(contact, contact.name)
                score, positions = fuzzy_match(query, name)
                if score == 0:
                    continue
                candidate = SearchCandidate(
                    account, jid, name, "chat", None, 0, contact, positions
                )
                # Contacts without a chat are listed after existing chats
                scored.append((1, score, 0, candidate))

        scored.sort(key=lambda item: (item[0], -item[1], -item[2], item[3].name))

        self.remove_all()
        multiple_accounts = len(app.settings.get_active_accounts()) > 1
        for _group, _score, _ts, candidate in scored:
            if candidate.workspace_id is not None:
                subtitle = app.settings.get_workspace_setting(
                    candidate.workspace_id, "name"
                )
            else:
                subtitle = _("Start a new conversation")

            if multiple_accounts:
                label = app.get_account_label(candidate.account)
                subtitle = f"{subtitle} · {label}"
            self.append(ChatSearchRow(candidate, subtitle))

        self.select_first()

    def select_first(self) -> None:
        row = self.get_row_at_index(0)
        if row is not None:
            self.select_row(row)

    def move_selection(self, step: int) -> None:
        """Moves the highlighted result"""
        row = self.get_selected_row()
        index = 0 if row is None else row.get_index() + step
        new_row = self.get_row_at_index(index)
        if new_row is None:
            return

        self.select_row(new_row)
        scroll_to_listbox_row(self, new_row)

    def activate_selected(self) -> bool:
        row = self.get_selected_row()
        if row is None:
            return False
        self.emit("row-activated", row)
        return True

    def _on_row_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        assert isinstance(row, ChatSearchRow)
        candidate = row.candidate
        if candidate.workspace_id is not None:
            self.emit("chat-activated", candidate.account, candidate.jid)
        else:
            self.emit("new-chat-activated", candidate.account, candidate.jid)
