# This file is part of Gajim.
#
# SPDX-License-Identifier: GPL-3.0-only

# Minimal GLib-based replacement for nbxmpp's removed IdleObject/IdleQueue.
# Allows socks5.py (XEP-0065 SOCKS5 Bytestreams) to integrate with GLib's
# event loop — the same loop used by GTK and nbxmpp 7.x.

from __future__ import annotations

import logging

from gi.repository import GLib

log = logging.getLogger("gajim.c.glib_idlequeue")


class IdleObject:
    """Base class for objects monitored by GLibIdleQueue."""

    fd: int = -1

    def __init__(self) -> None:
        pass

    def pollin(self) -> None:
        pass

    def pollout(self) -> None:
        pass

    def pollend(self) -> None:
        pass

    def read_timeout(self) -> None:
        pass


class GLibIdleQueue:
    """
    Drop-in replacement for nbxmpp's removed IdleQueue.

    Maps socks5.py's plug_idle/unplug_idle/set_read_timeout calls to
    GLib.io_add_watch / GLib.source_remove / GLib.timeout_add_seconds so
    that raw POSIX socket I/O works inside GLib's main loop.

    plug_idle(obj, write_notify, read_notify) — socks5.py convention:
      (True, False)  → notify when writable  → calls obj.pollout()
      (False, True)  → notify when readable  → calls obj.pollin()
      (False, False) → unplug all watchers
    HUP/ERR conditions on any fd always call obj.pollend().
    """

    def __init__(self) -> None:
        self._channels: dict[int, GLib.IOChannel] = {}
        self._write_sids: dict[int, int] = {}
        self._read_sids: dict[int, int] = {}
        self._timeout_sids: dict[int, int] = {}
        self._objects: dict[int, IdleObject] = {}

    def _channel(self, fd: int) -> GLib.IOChannel:
        if fd not in self._channels:
            ch = GLib.IOChannel.unix_new(fd)
            ch.set_encoding(None)
            ch.set_buffered(False)
            ch.set_close_on_unref(False)
            self._channels[fd] = ch
        return self._channels[fd]

    def plug_idle(
        self, obj: IdleObject, write_notify: bool, read_notify: bool
    ) -> None:
        fd = obj.fd
        if fd < 0:
            return
        self.unplug_idle(fd)
        if not write_notify and not read_notify:
            return
        self._objects[fd] = obj

        if write_notify:
            ch = self._channel(fd)
            cond = GLib.IO_OUT | GLib.IO_ERR

            def _on_write(
                channel: GLib.IOChannel,
                condition: GLib.IOCondition,
                _obj: IdleObject = obj,
            ) -> bool:
                if condition & (GLib.IO_ERR | GLib.IO_HUP):
                    _obj.pollend()
                else:
                    _obj.pollout()
                return True

            self._write_sids[fd] = GLib.io_add_watch(ch, cond, _on_write)

        if read_notify:
            ch = self._channel(fd)
            cond = GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR

            def _on_read(
                channel: GLib.IOChannel,
                condition: GLib.IOCondition,
                _obj: IdleObject = obj,
            ) -> bool:
                if condition & (GLib.IO_ERR | GLib.IO_HUP):
                    _obj.pollend()
                else:
                    _obj.pollin()
                return True

            self._read_sids[fd] = GLib.io_add_watch(ch, cond, _on_read)

    def unplug_idle(self, fd: int) -> None:
        for d in (self._read_sids, self._write_sids):
            sid = d.pop(fd, None)
            if sid is not None:
                try:
                    GLib.source_remove(sid)
                except Exception:
                    pass
        self._objects.pop(fd, None)
        self._channels.pop(fd, None)

    def set_read_timeout(self, fd: int, seconds: int) -> None:
        self.remove_timeout(fd)
        obj = self._objects.get(fd)
        if obj is None:
            return

        def _timeout(_obj: IdleObject = obj, _fd: int = fd) -> bool:
            self._timeout_sids.pop(_fd, None)
            _obj.read_timeout()
            return False

        self._timeout_sids[fd] = GLib.timeout_add_seconds(seconds, _timeout)

    def remove_timeout(self, fd: int) -> None:
        sid = self._timeout_sids.pop(fd, None)
        if sid is not None:
            try:
                GLib.source_remove(sid)
            except Exception:
                pass
