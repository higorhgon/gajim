# This file is part of Gajim.
#
# SPDX-License-Identifier: GPL-3.0-only

# XEP-0096: SI File Transfer (Stream Initiation)
# Enables receiving files from legacy clients (e.g. Pidgin) that do not
# support Jingle File Transfer (XEP-0234).

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

import nbxmpp
from nbxmpp.namespaces import Namespace
from nbxmpp.protocol import Iq
from nbxmpp.protocol import JID
from nbxmpp.structs import IqProperties
from nbxmpp.structs import StanzaHandler

from gajim.common import app
from gajim.common import events as ged_events
from gajim.common import types
from gajim.common.file_props import FilesProp
from gajim.common.glib_idlequeue import GLibIdleQueue
from gajim.common.modules.base import BaseModule
from gajim.common.socks5 import SocksQueue
from gajim.common.storage.archive import models as mod
from gajim.common.storage.archive.const import ChatDirection
from gajim.common.storage.archive.const import MessageState
from gajim.common.storage.archive.const import MessageType
from gajim.common.util.datetime import utc_now

NS_SI = "http://jabber.org/protocol/si"
NS_SI_FT = "http://jabber.org/protocol/si/profile/file-transfer"
NS_FEATURE_NEG = "http://jabber.org/protocol/feature-neg"

log = logging.getLogger("gajim.c.m.si")


class SI(BaseModule):
    def __init__(self, con: types.Client) -> None:
        BaseModule.__init__(self, con)
        self._log.info("SI module initialized for account %s", self._account)

        if not hasattr(app, "idlequeue") or app.idlequeue is None:
            app.idlequeue = GLibIdleQueue()

        if not hasattr(app, "socks5queue") or app.socks5queue is None:
            app.socks5queue = SocksQueue(
                app.idlequeue,
                complete_transfer_cb=self._complete_transfer_cb,
                progress_transfer_cb=self._progress_transfer_cb,
                error_cb=self._error_cb,
            )

        self._pending: dict[str, dict] = {}

        self.handlers = [
            StanzaHandler(
                name="iq",
                typ="set",
                callback=self._debug_all_iq_sets,
                priority=1,
            ),
            StanzaHandler(
                name="iq",
                typ="set",
                ns=NS_SI,
                callback=self._on_si_request,
            ),
        ]

    def _complete_transfer_cb(self, account: str, file_props: object) -> None:
        self._log.info(
            "File transfer complete: account=%s name=%s",
            account,
            getattr(file_props, "name", "?"),
        )
        app.ged.raise_event(
            ged_events.FileCompleted(
                account=account,
                file_props=file_props,
                jid=getattr(file_props, "sender", ""),
            )
        )

    def _progress_transfer_cb(self, account: str, file_props: object) -> None:
        app.ged.raise_event(ged_events.FileProgress(file_props=file_props))

    def _error_cb(self, msg: str, err: str) -> None:
        self._log.warning("File transfer error: %s %s", msg, err)

    def _debug_all_iq_sets(
        self, _con: types.NBXMPPClient, iq_obj: Iq, _properties: IqProperties
    ) -> None:
        self._log.info(
            "DEBUG IQ set arrived: from=%s props=%s id=%s",
            iq_obj.getFrom(),
            iq_obj.getProperties(),
            iq_obj.getAttr("id"),
        )

    def _on_si_request(
        self, _con: types.NBXMPPClient, iq_obj: Iq, _properties: IqProperties
    ) -> None:
        self._log.debug(
            "SI _on_si_request called: from=%s id=%s",
            iq_obj.getFrom(),
            iq_obj.getAttr("id"),
        )

        from_jid = str(iq_obj.getFrom())
        iq_id = iq_obj.getAttr("id")
        si = iq_obj.getTag("si", namespace=NS_SI)

        if si is None:
            self._log.warning(
                "SI IQ from %s has no <si> child (props=%s)",
                from_jid,
                iq_obj.getProperties(),
            )
            return

        sid = si.getAttr("id")
        if not sid:
            self._log.warning("SI offer without id from %s", from_jid)
            return

        profile = si.getAttr("profile")
        if profile != NS_SI_FT:
            self._log.warning("Unknown SI profile %s from %s", profile, from_jid)
            self._reject_si(from_jid, iq_id, "bad-profile")
            raise nbxmpp.NodeProcessed

        file_tag = si.getTag("file", namespace=NS_SI_FT)
        if file_tag is None:
            self._log.warning("SI offer missing <file> tag from %s", from_jid)
            self._reject_si(from_jid, iq_id, "bad-profile")
            raise nbxmpp.NodeProcessed

        filename = file_tag.getAttr("name") or "file"
        filename = os.path.basename(filename)
        size_str = file_tag.getAttr("size")
        try:
            size = int(size_str) if size_str else 0
        except (ValueError, TypeError):
            size = 0

        chosen_transport = self._select_transport(si)
        if chosen_transport is None:
            self._log.warning("No supported transport in SI offer from %s", from_jid)
            self._reject_si(from_jid, iq_id, "no-valid-streams")
            raise nbxmpp.NodeProcessed

        own_jid = str(self._con.get_own_jid())

        fp = FilesProp.getNewFileProp(self._account, sid)
        fp.type_ = "r"
        fp.sid = sid
        fp.name = filename
        fp.size = size
        fp.sender = from_jid
        fp.receiver = own_jid
        fp.session_type = "si"
        fp.transport_sid = sid
        fp.file_name = self._get_save_path(filename, sid)

        sender_bare = JID.from_string(from_jid).new_as_bare()

        ft_data = mod.FileTransfer(
            state=1,
            name=filename,
            size=size,
            source=[mod.JingleFT(type="jingleft", sid=sid)],
        )
        ft_data.path = fp.file_name
        message_data = mod.Message(
            account_=self._account,
            remote_jid_=sender_bare,
            resource=JID.from_string(from_jid).resource,
            type=MessageType.CHAT,
            direction=ChatDirection.INCOMING,
            timestamp=utc_now(),
            state=MessageState.ACKNOWLEDGED,
            id=str(uuid.uuid4()),
            filetransfers=[ft_data],
        )
        try:
            app.storage.archive.insert_object(message_data)
        except Exception as exc:
            self._log.warning("Failed to store SI transfer in archive: %s", exc)

        app.ged.raise_event(
            ged_events.FileRequestReceivedEvent(
                conn=self._con,
                stanza=iq_obj,
                id_=iq_id,
                fjid=from_jid,
                account=self._account,
                jid=sender_bare,
                file_props=fp,
            )
        )

        self._pending[sid] = {
            "from_jid": from_jid,
            "iq_id": iq_id,
            "transport": chosen_transport,
        }

        self._log.info(
            "SI offer pending user confirmation: from=%s name=%s size=%s sid=%s transport=%s",
            from_jid,
            filename,
            size,
            sid,
            chosen_transport,
        )

        raise nbxmpp.NodeProcessed

    def _select_transport(self, si: nbxmpp.Node) -> str | None:
        feature = si.getTag("feature", namespace=NS_FEATURE_NEG)
        if feature is None:
            return None
        x = feature.getTag("x", namespace="jabber:x:data")
        if x is None:
            return None

        offered: list[str] = []
        for field in x.getTags("field"):
            if field.getAttr("var") == "stream-method":
                for option in field.getTags("option"):
                    val = option.getTag("value")
                    if val is not None:
                        offered.append(val.getData())

        self._log.debug("SI offered transports: %s", offered)

        # SOCKS5 is fastest (direct TCP on LAN); IBB is reliable fallback.
        if Namespace.BYTESTREAM in offered:
            return Namespace.BYTESTREAM
        if Namespace.IBB in offered:
            return Namespace.IBB
        return None

    def _accept_si(
        self, to: str, iq_id: str, _sid: str, transport: str
    ) -> None:
        result = nbxmpp.Iq(typ="result", to=to, frm=str(self._con.get_own_jid()))
        result.setID(iq_id)
        si = result.addChild("si", namespace=NS_SI)
        feature = si.addChild("feature", namespace=NS_FEATURE_NEG)
        x = feature.addChild("x", namespace="jabber:x:data", attrs={"type": "submit"})
        field = x.addChild("field", attrs={"var": "stream-method"})
        field.addChild("value").setData(transport)
        self._log.debug("Sending SI result IQ to %s: %s", to, result)
        self._con.connection.send(result)
        self._log.info("SI accepted (transport=%s) to %s", transport, to)

    def _reject_si(self, to: str, iq_id: str, reason: str) -> None:
        error_iq = nbxmpp.Iq(typ="error", to=to)
        error_iq.setID(iq_id)
        error = error_iq.addChild("error", attrs={"code": "403", "type": "cancel"})
        error.addChild("forbidden", namespace="urn:ietf:params:xml:ns:xmpp-stanzas")
        self._log.debug("Sending SI reject IQ to %s: %s", to, error_iq)
        self._con.connection.send(error_iq)
        self._log.info("SI rejected (reason=%s) to %s", reason, to)

    def accept_transfer(self, sid: str) -> None:
        pending = self._pending.pop(sid, None)
        if pending is None:
            self._log.warning("accept_transfer: unknown sid %s", sid)
            return
        self._accept_si(pending["from_jid"], pending["iq_id"], sid, pending["transport"])

    def reject_transfer(self, sid: str) -> None:
        pending = self._pending.pop(sid, None)
        if pending is not None:
            self._reject_si(pending["from_jid"], pending["iq_id"], "user-reject")
        fp = FilesProp.getFilePropBySid(sid)
        if fp is not None:
            fp.stopped = True
            if getattr(fp, "fp", None) is not None:
                try:
                    fp.fp.close()
                except Exception:
                    pass

    def cancel_transfer(self, sid: str) -> None:
        fp = FilesProp.getFilePropBySid(sid)
        if fp is None:
            return
        fp.stopped = True
        if getattr(fp, "connected", False):
            fp.connected = False
            if getattr(fp, "fp", None) is not None:
                try:
                    fp.fp.close()
                except Exception:
                    pass
            disconnect_cb = getattr(fp, "disconnect_cb", None)
            if disconnect_cb is not None:
                try:
                    disconnect_cb()
                except Exception:
                    pass
            else:
                try:
                    self._nbxmpp("IBB").send_close(
                        fp.sender, fp.transport_sid, callback=lambda _t: None
                    )
                except Exception:
                    pass
        elif sid in self._pending:
            self.reject_transfer(sid)

    @staticmethod
    def _get_save_path(filename: str, sid: str) -> str:
        save_dir = app.settings.get("last_save_dir")
        if not save_dir:
            save_dir = str(Path.home() / "Downloads")
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(save_dir, filename)
        if os.path.exists(path):
            base, ext = os.path.splitext(filename)
            path = os.path.join(save_dir, f"{base}_{sid[:8]}{ext}")
        return path
