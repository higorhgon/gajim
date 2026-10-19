# This file is part of Gajim.
#
# SPDX-License-Identifier: GPL-3.0-only

# Selects text across messages, by dragging from one message to another or
# by Shift+clicking a second message to select whole messages.
#
# GtkLabel keeps a selection only while it owns the primary clipboard, so
# selecting text in one message clears the selection of every other one.
# This draws the selection with Pango attributes instead, and copies it
# with Ctrl+C.

from __future__ import annotations

from typing import Protocol

import logging
from dataclasses import dataclass
from datetime import datetime

from gi.repository import Adw
from gi.repository import Gdk
from gi.repository import GLib
from gi.repository import Graphene
from gi.repository import Gtk
from gi.repository import Pango

from gajim.common import app

log = logging.getLogger("gajim.gtk.conversation.text_selection")

# Distance from the top or bottom edge which scrolls the view while dragging
AUTOSCROLL_MARGIN = 40
AUTOSCROLL_INTERVAL = 30


class SelectableRow(Protocol):
    name: str
    timestamp: datetime

    def get_text(self) -> str: ...

    def get_text_labels(self) -> list[Gtk.Label]: ...


@dataclass(frozen=True)
class Position:
    row: Gtk.ListBoxRow
    # Index into the row's text labels, and character offset in that label.
    # Rows without text (e.g. images) have offset 0 before and 1 after them.
    label: int
    offset: int

    def key(self) -> tuple[int, int, int]:
        return (self.row.get_index(), self.label, self.offset)


def _get_labels(row: Gtk.ListBoxRow) -> list[Gtk.Label]:
    return row.get_text_labels()  # pyright: ignore


def _is_selectable(row: Gtk.ListBoxRow | None) -> bool:
    return row is not None and hasattr(row, "get_text_labels")


class MessageTextSelection:
    def __init__(self, view: Gtk.ScrolledWindow, list_box: Gtk.ListBox) -> None:
        self._view = view
        self._list_box = list_box

        self._active = False
        self._pressed = False
        self._press_position: Position | None = None
        self._start: Position | None = None
        self._end: Position | None = None
        self._click_anchor: Gtk.ListBoxRow | None = None
        self._pointer = (0.0, 0.0)
        self._autoscroll_speed = 0.0
        self._autoscroll_id: int | None = None

        # Label the drag started on, its own selection is turned off
        # while the selection spans several labels
        self._native_label: Gtk.Label | None = None

        # Labels drawn as selected, with the attributes they had before
        self._highlighted: dict[Gtk.Label, Pango.AttrList | None] = {}
        # Selected character range of each label
        self._ranges: dict[Gtk.Label, tuple[int, int]] = {}

        controller = Gtk.EventControllerLegacy()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("event", self._on_event)
        list_box.add_controller(controller)

    @property
    def active(self) -> bool:
        return self._active

    def _on_event(
        self, controller: Gtk.EventControllerLegacy, _event: Gdk.Event | None
    ) -> bool:
        if self._list_box.get_selection_mode() != Gtk.SelectionMode.NONE:
            # Message selection mode handles clicks on its own
            return Gdk.EVENT_PROPAGATE

        # PyGObject passes None for the event argument, GdkEvent being a
        # fundamental type, so ask the controller for it instead
        event = controller.get_current_event()
        if event is None:
            return Gdk.EVENT_PROPAGATE

        event_type = event.get_event_type()
        if event_type == Gdk.EventType.BUTTON_PRESS:
            assert isinstance(event, Gdk.ButtonEvent)
            if event.get_button() != Gdk.BUTTON_PRIMARY:
                return Gdk.EVENT_PROPAGATE
            shift = bool(event.get_modifier_state() & Gdk.ModifierType.SHIFT_MASK)
            return self.press(*self._event_to_view(event), shift)

        if event_type == Gdk.EventType.MOTION_NOTIFY and self._pressed:
            self.motion(*self._event_to_view(event))

        elif event_type == Gdk.EventType.BUTTON_RELEASE and self._pressed:
            self.release()

        return Gdk.EVENT_PROPAGATE

    def _event_to_view(self, event: Gdk.Event) -> tuple[float, float]:
        native = self._view.get_native()
        assert native is not None
        _ok, x, y = event.get_position()
        surface_x, surface_y = native.get_surface_transform()
        point = Graphene.Point().init(x - surface_x, y - surface_y)
        ok, view_point = native.compute_point(self._view, point)
        if not ok:
            return (0.0, 0.0)
        return (view_point.x, view_point.y)

    def press(self, x: float, y: float, shift: bool = False) -> bool:
        position = self._position_at(x, y)

        if shift and position is not None and self._is_in_view(self._click_anchor):
            assert self._click_anchor is not None
            self.select_rows(self._click_anchor, position.row)
            # Keep the label from starting a selection of its own
            return Gdk.EVENT_STOP

        self.clear()
        self._pressed = True
        self._press_position = position
        self._pointer = (x, y)
        if position is not None and self._row_at(x, y) is position.row:
            self._click_anchor = position.row
        return Gdk.EVENT_PROPAGATE

    def motion(self, x: float, y: float) -> None:
        self._pointer = (x, y)
        self._update_drag()
        self._update_autoscroll()

    def release(self) -> None:
        self._pressed = False
        self._stop_autoscroll()
        self._restore_native_label()
        if self._active:
            self._view.get_primary_clipboard().set(self.get_text())

    def select_rows(self, row1: Gtk.ListBoxRow, row2: Gtk.ListBoxRow) -> None:
        first, last = sorted((row1, row2), key=lambda row: row.get_index())
        labels = _get_labels(last) if _is_selectable(last) else []
        end_offset = len(labels[-1].get_text()) if labels else 1

        self.clear()
        self._active = True
        self._set_range(
            Position(first, 0, 0), Position(last, max(len(labels) - 1, 0), end_offset)
        )
        self._view.get_primary_clipboard().set(self.get_text())

    def clear(self) -> None:
        self._restore_native_label()
        for label, attributes in self._highlighted.items():
            label.set_attributes(attributes)
        self._highlighted.clear()
        self._ranges.clear()
        self._active = False
        self._start = None
        self._end = None

    def reset(self) -> None:
        # The rows are about to be removed
        self.clear()
        self._stop_autoscroll()
        self._pressed = False
        self._press_position = None
        self._click_anchor = None

    def _is_in_view(self, row: Gtk.ListBoxRow | None) -> bool:
        return row is not None and row.get_parent() is self._list_box

    def _update_drag(self) -> None:
        if self._press_position is None:
            return

        if not self._is_in_view(self._press_position.row):
            self.reset()
            return

        position = self._position_at(*self._pointer)
        if position is None:
            return

        if not self._active:
            start = self._press_position
            if (position.row, position.label) == (start.row, start.label):
                # Still inside one label, its own selection handles it
                return
            self._active = True
            self._disable_native_label()

        self._set_range(self._press_position, position)

    def _disable_native_label(self) -> None:
        assert self._press_position is not None
        row = self._press_position.row
        if not _is_selectable(row):
            return
        labels = _get_labels(row)
        if not labels:
            return
        label = labels[min(self._press_position.label, len(labels) - 1)]
        if label.get_selectable():
            # Clears the label's selection and ends the drag it started
            label.set_selectable(False)
            self._native_label = label

    def _restore_native_label(self) -> None:
        if self._native_label is not None:
            self._native_label.set_selectable(True)
            self._native_label = None

    def _row_at(self, x: float, y: float) -> Gtk.ListBoxRow | None:
        point = Graphene.Point().init(x, y)
        ok, list_point = self._view.compute_point(self._list_box, point)
        if not ok:
            return None
        return self._list_box.get_row_at_y(int(list_point.y))

    def _position_at(self, x: float, y: float) -> Position | None:
        point = Graphene.Point().init(x, y)
        ok, list_point = self._view.compute_point(self._list_box, point)
        if not ok:
            return None

        row = self._list_box.get_row_at_y(int(list_point.y))
        if row is None:
            if list_point.y < 0:
                row = self._list_box.get_row_at_index(0)
            else:
                last = self._list_box.get_last_child()
                row = last if isinstance(last, Gtk.ListBoxRow) else None
            if row is None:
                return None

        if not _is_selectable(row):
            return self._nearest_position(row)

        labels = _get_labels(row)
        if not labels:
            ok, row_point = self._list_box.compute_point(row, list_point)
            after = ok and row_point.y > row.get_height() / 2
            return Position(row, 0, int(after))

        for index, label in enumerate(labels):
            ok, label_point = self._list_box.compute_point(label, list_point)
            if not ok:
                continue
            if label_point.y < 0:
                # Above this label, between it and the previous one
                return Position(row, index, 0)
            if label_point.y <= label.get_height():
                return Position(row, index, self._offset_at(label, label_point))

        return Position(row, len(labels) - 1, len(labels[-1].get_text()))

    def _nearest_position(self, row: Gtk.ListBoxRow) -> Position | None:
        # Rows without text (dates, status changes…) snap to the start of the
        # next message, or the end of the previous one
        index = row.get_index()
        next_row = self._list_box.get_row_at_index(index + 1)
        while next_row is not None:
            if _is_selectable(next_row):
                return Position(next_row, 0, 0)
            index += 1
            next_row = self._list_box.get_row_at_index(index + 1)

        index = row.get_index()
        while index > 0:
            index -= 1
            prev_row = self._list_box.get_row_at_index(index)
            if _is_selectable(prev_row):
                assert prev_row is not None
                labels = _get_labels(prev_row)
                if not labels:
                    return Position(prev_row, 0, 1)
                return Position(prev_row, len(labels) - 1, len(labels[-1].get_text()))
        return None

    @staticmethod
    def _offset_at(label: Gtk.Label, point: Graphene.Point) -> int:
        layout = label.get_layout()
        layout_x, layout_y = label.get_layout_offsets()
        _inside, index, trailing = layout.xy_to_index(
            int((point.x - layout_x) * Pango.SCALE),
            int((point.y - layout_y) * Pango.SCALE),
        )
        text = label.get_text()
        # Pango works with byte indices into the UTF-8 text
        offset = len(text.encode()[:index].decode(errors="ignore")) + trailing
        return min(offset, len(text))

    def _set_range(self, position1: Position, position2: Position) -> None:
        start, end = sorted((position1, position2), key=Position.key)
        self._start = start
        self._end = end

        start_row = start.row.get_index()
        end_row = end.row.get_index()
        ranges: dict[Gtk.Label, tuple[int, int]] = {}
        for index in range(start_row, end_row + 1):
            row = self._list_box.get_row_at_index(index)
            if not _is_selectable(row):
                continue
            assert row is not None
            for label_index, label in enumerate(_get_labels(row)):
                key = (index, label_index)
                if key < (start_row, start.label) or key > (end_row, end.label):
                    continue
                begin = start.offset if key == (start_row, start.label) else 0
                finish = (
                    end.offset if key == (end_row, end.label) else len(label.get_text())
                )
                if begin < finish:
                    ranges[label] = (begin, finish)

        for label in list(self._highlighted):
            if label not in ranges:
                label.set_attributes(self._highlighted.pop(label))

        for label, (begin, finish) in ranges.items():
            if self._ranges.get(label) != (begin, finish):
                self._highlight(label, begin, finish)

        self._ranges = ranges

    def _highlight(self, label: Gtk.Label, begin: int, finish: int) -> None:
        if label not in self._highlighted:
            self._highlighted[label] = label.get_attributes()

        original = self._highlighted[label]
        attributes = original.copy() if original is not None else Pango.AttrList()

        text = label.get_text()
        start_index = len(text[:begin].encode())
        end_index = len(text[:finish].encode())

        color = Adw.StyleManager.get_default().get_accent_color_rgba()
        background = Pango.attr_background_new(
            int(color.red * 65535), int(color.green * 65535), int(color.blue * 65535)
        )
        alpha = Pango.attr_background_alpha_new(int(0.35 * 65535))
        for attribute in (background, alpha):
            attribute.start_index = start_index
            attribute.end_index = end_index
            attributes.insert(attribute)

        label.set_attributes(attributes)

    def get_text(self) -> str:
        if self._start is None or self._end is None:
            return ""

        start_row = self._start.row.get_index()
        end_row = self._end.row.get_index()
        rows = [
            self._list_box.get_row_at_index(index)
            for index in range(start_row, end_row + 1)
        ]
        rows = [row for row in rows if self._is_row_selected(row)]

        if len(rows) == 1:
            row_text, complete = self._get_row_selection(rows[0])
            if complete:
                return rows[0].get_text()  # pyright: ignore
            return row_text

        format_string = app.settings.get("date_time_format")
        text = ""
        for row in rows:
            assert row is not None
            row_text, complete = self._get_row_selection(row)
            if complete:
                row_text = row.get_text()  # pyright: ignore
            if not row_text:
                continue
            timestamp = row.timestamp.strftime(format_string)  # pyright: ignore
            text += f"{timestamp} - {row.name}:\n{row_text}\n"  # pyright: ignore
        return text

    def _is_row_selected(self, row: Gtk.ListBoxRow | None) -> bool:
        if not _is_selectable(row):
            return False
        assert row is not None
        assert self._start is not None and self._end is not None
        labels = _get_labels(row)
        if labels:
            return any(label in self._ranges for label in labels)
        # Rows without text are selected when the selection covers them
        index = row.get_index()
        return self._start.key() <= (index, 0, 0) and self._end.key() >= (index, 0, 1)

    def _get_row_selection(self, row: Gtk.ListBoxRow) -> tuple[str, bool]:
        # Returns the selected text of a row, and whether all of it is selected
        parts: list[str] = []
        complete = True
        for label in _get_labels(row):
            text = label.get_text()
            begin, finish = self._ranges.get(label, (0, 0))
            if (begin, finish) != (0, len(text)):
                complete = False
            if begin < finish:
                parts.append(text[begin:finish])
        return "\n".join(parts), complete

    def _update_autoscroll(self) -> None:
        if not self._active:
            return

        height = self._view.get_height()
        y = self._pointer[1]
        if y < AUTOSCROLL_MARGIN:
            self._autoscroll_speed = y - AUTOSCROLL_MARGIN
        elif y > height - AUTOSCROLL_MARGIN:
            self._autoscroll_speed = y - (height - AUTOSCROLL_MARGIN)
        else:
            self._stop_autoscroll()
            return

        if self._autoscroll_id is None:
            self._autoscroll_id = GLib.timeout_add(
                AUTOSCROLL_INTERVAL, self._on_autoscroll
            )

    def _on_autoscroll(self) -> bool:
        adjustment = self._view.get_vadjustment()
        value = adjustment.get_value() + self._autoscroll_speed / 2
        upper = adjustment.get_upper() - adjustment.get_page_size()
        adjustment.set_value(max(0, min(value, upper)))
        self._update_drag()
        return GLib.SOURCE_CONTINUE

    def _stop_autoscroll(self) -> None:
        if self._autoscroll_id is not None:
            GLib.source_remove(self._autoscroll_id)
            self._autoscroll_id = None
