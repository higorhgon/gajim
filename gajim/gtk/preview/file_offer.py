# This file is part of Gajim.
#
# SPDX-License-Identifier: GPL-3.0-only

# Card for files offered by contacts via SI (XEP-0096). It mirrors
# PreviewWidget, so these files look and behave like the ones downloaded
# from the server.

from __future__ import annotations

from typing import Any

import logging
import sys
from pathlib import Path

from gi.repository import Gdk
from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Gtk

from gajim.common import app
from gajim.common import configpaths
from gajim.common.i18n import _
from gajim.common.util.preview import get_icon_for_mime_type
from gajim.common.util.preview import get_size_and_mime_type
from gajim.common.util.preview import guess_mime_type
from gajim.common.util.preview import is_audio
from gajim.common.util.preview import is_image
from gajim.common.util.preview import is_video

from gajim.gtk.preview.audio import AudioPreviewWidget
from gajim.gtk.preview.file_control_buttons import FileControlButtons
from gajim.gtk.preview.image import ImagePreviewWidget
from gajim.gtk.util.classes import SignalManager
from gajim.gtk.util.misc import get_ui_string

log = logging.getLogger("gajim.gtk.preview.file_offer")


@Gtk.Template.from_string(string=get_ui_string("preview/file_offer.ui"))
class FileOfferWidget(Gtk.Box, SignalManager):
    __gtype_name__ = "FileOfferWidget"

    __gsignals__ = {
        "download": (GObject.SignalFlags.RUN_LAST, None, ()),
        "reject": (GObject.SignalFlags.RUN_LAST, None, ()),
        "cancel": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    _stack: Gtk.Stack = Gtk.Template.Child()
    _icon_button: Gtk.Button = Gtk.Template.Child()
    _mime_image: Gtk.Image = Gtk.Template.Child()
    _progress_box: Gtk.Box = Gtk.Template.Child()
    _progressbar: Gtk.ProgressBar = Gtk.Template.Child()
    _progress_text: Gtk.Label = Gtk.Template.Child()
    _cancel_download_button: Gtk.Button = Gtk.Template.Child()
    _info_message_label: Gtk.Label = Gtk.Template.Child()
    _file_control_buttons: FileControlButtons = Gtk.Template.Child()
    _download_button: Gtk.Button = Gtk.Template.Child()
    _reject_button: Gtk.Button = Gtk.Template.Child()

    def __init__(self, file_name: str, file_size: int, path: Path) -> None:
        Gtk.Box.__init__(self)
        SignalManager.__init__(self)

        self._filename = file_name
        self._file_size = file_size
        self._orig_path = path
        self._mime_type = guess_mime_type(file_name)
        self._pulse_id: int | None = None
        self._completed = False
        self._widget = None

        # Same naming scheme as the thumbnails of server downloads
        thumbnail_size = app.settings.get("preview_size")
        self._thumb_path = (
            configpaths.get("DOWNLOADS_THUMB")
            / f"{path.stem}_thumb_{thumbnail_size}.png"
        )

        self._connect(self._download_button, "clicked", self._on_download_clicked)
        self._connect(self._reject_button, "clicked", self._on_reject_clicked)
        self._connect(
            self._cancel_download_button, "clicked", self._on_cancel_clicked
        )
        self._connect(self._icon_button, "clicked", self._on_icon_clicked)

        pointer_cursor = Gdk.Cursor.new_from_name("pointer")
        for button in (
            self._icon_button,
            self._download_button,
            self._reject_button,
            self._cancel_download_button,
        ):
            button.set_cursor(pointer_cursor)

        self._mime_image.set_from_gicon(get_icon_for_mime_type(self._mime_type))
        self._file_control_buttons.set_file_name(file_name)
        self._file_control_buttons.set_file_size(file_size)
        self._set_info_message(None)

    def do_unroot(self) -> None:
        self._stop_pulse()
        self._disconnect_all()
        if isinstance(self._widget, AudioPreviewWidget):
            self._widget.run_destroy()
        Gtk.Box.do_unroot(self)

    def show_offer(self, available: bool = True, message: str | None = None) -> None:
        self._set_buttons(download=available)
        self._set_info_message(message)

    def show_waiting(self, message: str) -> None:
        self._set_buttons(cancel=True)
        self._set_info_message(message)
        self._progress_text.set_label("")
        self._progressbar.set_fraction(0)
        self._progress_box.set_visible(True)
        if self._pulse_id is None:
            self._pulse_id = GLib.timeout_add(100, self._pulse)

    def update_progress(self, fraction: float) -> None:
        self._stop_pulse()
        self._set_buttons(cancel=True)
        self._set_info_message(None)
        self._progress_box.set_visible(True)
        self._progress_text.set_label(f"{int(fraction * 100)} %")
        self._progressbar.set_fraction(fraction)

    def show_error(self, message: str) -> None:
        self._set_buttons()
        self._set_info_message(message)

    def show_completed(self) -> None:
        if self._completed:
            return

        if not self._orig_path.exists():
            self.show_error(_("File not found"))
            return

        self._completed = True
        self._set_buttons()
        self._set_info_message(None)
        self._mime_type, self._file_size = get_size_and_mime_type(self._orig_path)
        self._mime_image.set_from_gicon(get_icon_for_mime_type(self._mime_type))
        self._file_control_buttons.set_file_size(self._file_size)
        self._file_control_buttons.set_path(self._orig_path)

        widget = None
        if is_video(self._mime_type) and sys.platform == "darwin":
            # https://dev.gajim.org/gajim/gajim/-/issues/12625
            pass

        elif is_image(self._mime_type) or is_video(self._mime_type):
            widget = ImagePreviewWidget(
                self._filename,
                self._file_size,
                self._mime_type,
                self._orig_path,
                self._thumb_path,
            )

        elif is_audio(self._mime_type):
            assert app.audio_player is not None
            widget = AudioPreviewWidget(
                app.audio_player,
                self._filename,
                self._file_size,
                self._orig_path,
            )

        if widget is None:
            return

        self._connect(widget, "display-error", self._on_display_error)
        self._stack.set_size_request(-1, -1)
        self._stack.add_named(widget, "widget")
        self._stack.set_visible_child_name("widget")
        self._widget = widget

    def _set_buttons(
        self, download: bool = False, cancel: bool = False
    ) -> None:
        self._download_button.set_visible(download)
        self._reject_button.set_visible(download)
        self._cancel_download_button.set_visible(cancel)
        self._cancel_download_button.set_sensitive(cancel)
        if not cancel:
            self._stop_pulse()
            self._progress_box.set_visible(False)

    def _set_info_message(self, message: str | None) -> None:
        self._info_message_label.set_text(message or "")
        self._info_message_label.set_tooltip_text(message or "")
        self._info_message_label.set_visible(message is not None)

    def _pulse(self) -> bool:
        self._progressbar.pulse()
        return GLib.SOURCE_CONTINUE

    def _stop_pulse(self) -> None:
        if self._pulse_id is not None:
            GLib.source_remove(self._pulse_id)
            self._pulse_id = None

    def _on_display_error(self, _widget: Any) -> None:
        self._stack.set_visible_child_name("preview")

    def _on_download_clicked(self, _button: Gtk.Button) -> None:
        self.emit("download")

    def _on_reject_clicked(self, _button: Gtk.Button) -> None:
        self.emit("reject")

    def _on_cancel_clicked(self, button: Gtk.Button) -> None:
        button.set_sensitive(False)
        self.emit("cancel")

    def _on_icon_clicked(self, _button: Gtk.Button) -> None:
        if self._completed:
            app.app.activate_action(
                "open-file", GLib.Variant("s", str(self._orig_path))
            )
        elif self._download_button.get_visible():
            self.emit("download")
