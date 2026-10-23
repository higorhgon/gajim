# This file is part of Gajim.
#
# SPDX-License-Identifier: GPL-3.0-only

# Sends a file through a SOCKS5 bytestream proxy (XEP-0065), as Pidgin does.
# The receiver connects to the proxy as well, so neither side needs an open
# port, only outgoing connections to the server.

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import logging

from gi.repository import Gio
from gi.repository import GLib

log = logging.getLogger("gajim.c.socks5_proxy")

CONNECT_TIMEOUT = 30
CHUNK_SIZE = 64 * 1024
# After the last byte, how long to wait for the receiver to close the
# connection. SI has no receipt, the close is the only sign the file arrived.
PEER_CLOSE_TIMEOUT = 60


class Socks5ProxySender:
    def __init__(
        self,
        host: str,
        port: int,
        dst_addr: str,
        path: Path,
        on_connected: Callable[[], None],
        on_progress: Callable[[int], None],
        on_finished: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> None:
        self._host = host
        self._port = port
        # SHA1(SID + initiator JID + target JID), identifies the transfer
        self._dst_addr = dst_addr.encode()
        self._path = path
        self._on_connected = on_connected
        self._on_progress = on_progress
        self._on_finished = on_finished
        self._on_error = on_error

        self._cancellable = Gio.Cancellable()
        self._conn: Gio.SocketConnection | None = None
        self._file = None
        self._sent = 0
        self._done = False
        self._close_timeout_id: int | None = None

    def start(self) -> None:
        log.info("Connecting to proxy %s:%s", self._host, self._port)
        client = Gio.SocketClient(timeout=CONNECT_TIMEOUT)
        address = Gio.NetworkAddress.new(self._host, self._port)
        client.connect_async(address, self._cancellable, self._on_socket_connected)

    def send_file(self) -> None:
        """Start streaming, once the proxy activated the bytestream"""
        try:
            self._file = self._path.open("rb")
        except OSError as error:
            self._fail(f"Could not open file: {error}")
            return
        self._send_next_chunk()

    def cancel(self) -> None:
        if self._done:
            return
        self._done = True
        self._cancellable.cancel()
        self._close()

    def _on_socket_connected(
        self, client: Gio.SocketClient, result: Gio.AsyncResult
    ) -> None:
        try:
            self._conn = client.connect_finish(result)
        except GLib.Error as error:
            self._fail(f"Could not connect to proxy: {error.message}")
            return

        # Greeting: version 5, one method, no authentication
        self._write(b"\x05\x01\x00", lambda: self._read(2, self._on_method_selected))

    def _on_method_selected(self, data: bytes) -> None:
        if data != b"\x05\x00":
            self._fail("Proxy refused the SOCKS5 greeting")
            return

        # CONNECT to the hash as domain name, port 0
        request = (
            b"\x05\x01\x00\x03"
            + bytes([len(self._dst_addr)])
            + self._dst_addr
            + b"\x00\x00"
        )
        self._write(request, lambda: self._read(5, self._on_reply_head))

    def _on_reply_head(self, data: bytes) -> None:
        if data[0] != 0x05 or data[1] != 0x00:
            self._fail(f"Proxy refused the connection (code {data[1]})")
            return

        # The first byte of the bound address was already read
        address_type = data[3]
        if address_type == 0x03:
            remaining = data[4] + 2
        elif address_type == 0x01:
            remaining = 3 + 2
        elif address_type == 0x04:
            remaining = 15 + 2
        else:
            self._fail(f"Unknown address type in proxy reply: {address_type}")
            return

        self._read(remaining, lambda _data: self._on_connected())

    def _send_next_chunk(self) -> None:
        if self._done:
            return

        assert self._file is not None
        try:
            chunk = self._file.read(CHUNK_SIZE)
        except OSError as error:
            self._fail(f"Could not read file: {error}")
            return

        if not chunk:
            self._await_peer_close()
            return

        def _on_chunk_sent() -> None:
            self._sent += len(chunk)
            self._on_progress(self._sent)
            self._send_next_chunk()

        self._write(chunk, _on_chunk_sent)

    def _await_peer_close(self) -> None:
        """
        Everything is in the send buffer, but not necessarily at the receiver.
        Stop writing and wait for the connection to be closed: cleanly by a
        receiver that read the whole file, with a reset by one that gave up.
        The proxy sits in between, so a failure may still look like a clean
        close.
        """
        assert self._conn is not None
        if self._file is not None:
            self._file.close()
            self._file = None

        try:
            self._conn.get_socket().shutdown(False, True)
        except GLib.Error as error:
            # Fails only once the connection was reset, a clean close of the
            # receiver still allows it
            self._fail(f"Receiver aborted the transfer: {error.message}")
            return

        self._close_timeout_id = GLib.timeout_add_seconds(
            PEER_CLOSE_TIMEOUT, self._on_peer_close_timeout
        )
        self._wait_for_close()

    def _wait_for_close(self) -> None:
        assert self._conn is not None
        stream = self._conn.get_input_stream()

        def _on_read(stream: Gio.InputStream, result: Gio.AsyncResult) -> None:
            try:
                data = stream.read_bytes_finish(result).get_data() or b""
            except GLib.Error as error:
                if self._done:
                    return
                self._fail(f"Receiver aborted the transfer: {error.message}")
                return

            if data:
                # The receiver should not send anything, keep waiting
                self._wait_for_close()
                return
            log.info("Receiver closed the connection, file transferred")
            self._finish()

        stream.read_bytes_async(1, GLib.PRIORITY_DEFAULT, self._cancellable, _on_read)

    def _on_peer_close_timeout(self) -> bool:
        self._close_timeout_id = None
        # The receiver keeps the connection open, it got the bytes at least
        log.info("Receiver did not close the connection, assuming success")
        self._finish()
        return GLib.SOURCE_REMOVE

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        # Ends the read still waiting for the close, after a timeout
        self._cancellable.cancel()
        self._close()
        self._on_finished()

    def _write(self, data: bytes, callback: Callable[[], None]) -> None:
        assert self._conn is not None
        stream = self._conn.get_output_stream()

        def _on_written(stream: Gio.OutputStream, result: Gio.AsyncResult) -> None:
            try:
                written = stream.write_bytes_finish(result)
            except GLib.Error as error:
                self._fail(f"Connection to proxy lost: {error.message}")
                return

            if written < len(data):
                self._write(data[written:], callback)
                return
            callback()

        stream.write_bytes_async(
            GLib.Bytes.new(data), GLib.PRIORITY_DEFAULT, self._cancellable, _on_written
        )

    def _read(
        self, size: int, callback: Callable[[bytes], None], received: bytes = b""
    ) -> None:
        assert self._conn is not None
        stream = self._conn.get_input_stream()

        def _on_read(stream: Gio.InputStream, result: Gio.AsyncResult) -> None:
            try:
                data = stream.read_bytes_finish(result).get_data() or b""
            except GLib.Error as error:
                self._fail(f"Connection to proxy lost: {error.message}")
                return

            if not data:
                self._fail("Proxy closed the connection")
                return

            data = received + data
            if len(data) < size:
                self._read(size, callback, data)
                return
            callback(data)

        stream.read_bytes_async(
            size - len(received), GLib.PRIORITY_DEFAULT, self._cancellable, _on_read
        )

    def _fail(self, message: str) -> None:
        if self._done:
            return
        log.warning("Proxy transfer failed: %s", message)
        self._done = True
        self._close()
        self._on_error(message)

    def _close(self) -> None:
        if self._close_timeout_id is not None:
            GLib.source_remove(self._close_timeout_id)
            self._close_timeout_id = None
        if self._file is not None:
            self._file.close()
            self._file = None
        if self._conn is not None:
            self._conn.close_async(GLib.PRIORITY_DEFAULT, None, None)
            self._conn = None
