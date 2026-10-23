# This file is part of Gajim.
#
# SPDX-License-Identifier: GPL-3.0-only

# XEP-0096: SI File Transfer (Stream Initiation)
# Enables sending files to and receiving files from legacy clients
# (e.g. Pidgin) that do not support Jingle File Transfer (XEP-0234).

from __future__ import annotations

from typing import Any

import hashlib
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
from gajim.common import configpaths
from gajim.common import events as ged_events
from gajim.common import types
from gajim.common.file_props import FileProp
from gajim.common.file_props import FilesProp
from gajim.common.glib_idlequeue import GLibIdleQueue
from gajim.common.modules.base import BaseModule
from gajim.common.modules.bytestream import set_connection_method
from gajim.common.socks5 import SocksQueue
from gajim.common.socks5_proxy import Socks5ProxySender
from gajim.common.storage.archive import models as mod
from gajim.common.storage.archive.const import ChatDirection
from gajim.common.storage.archive.const import MessageState
from gajim.common.storage.archive.const import MessageType
from gajim.common.util.datetime import utc_now
from gajim.common.util.filesystem import sanitize_filename
from gajim.common.util.preview import guess_mime_type

NS_SI = "http://jabber.org/protocol/si"
NS_SI_FT = "http://jabber.org/protocol/si/profile/file-transfer"
NS_FEATURE_NEG = "http://jabber.org/protocol/feature-neg"

# FileProp.error when the receiver declined the offer
ERROR_DECLINED = -13

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

    def _complete_transfer_cb(self, account: str, file_props: FileProp) -> None:
        # SOCKS5 reports the end of a transfer the same way for success
        # and failure, only FileProp.error tells them apart
        peer = file_props.receiver if file_props.type_ == "s" else file_props.sender
        if file_props.error:
            self._log.warning(
                "File transfer failed: account=%s name=%s error=%s",
                account,
                file_props.name,
                file_props.error,
            )
            app.ged.raise_event(
                ged_events.FileError(account=account, file_props=file_props, jid=peer)
            )
            return

        self._log.info(
            "File transfer complete: account=%s name=%s", account, file_props.name
        )
        file_props.completed = True
        app.ged.raise_event(
            ged_events.FileCompleted(account=account, file_props=file_props, jid=peer)
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
        message_id = self._store_transfer(fp, sender_bare, ChatDirection.INCOMING)

        app.ged.raise_event(
            ged_events.FileRequestReceivedEvent(
                conn=self._con,
                stanza=iq_obj,
                id_=iq_id,
                fjid=from_jid,
                account=self._account,
                jid=sender_bare,
                file_props=fp,
                message_id=message_id,
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

    def is_pending(self, sid: str) -> bool:
        return sid in self._pending

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
                peer = fp.receiver if fp.type_ == "s" else fp.sender
                try:
                    self._nbxmpp("IBB").send_close(
                        peer, fp.transport_sid, callback=lambda _t: None
                    )
                except Exception:
                    pass
        elif sid in self._pending:
            self.reject_transfer(sid)
        elif fp.type_ == "s":
            # Offer not answered yet, or waiting for the receiver to connect
            self._con.get_module("Bytestream").remove_transfer(fp)

    def send_via_proxy(self, fp: FileProp, proxy: dict[str, Any]) -> None:
        """
        The receiver could not reach us and connected to the server's proxy,
        connect there as well and stream the file through it
        """
        assert fp.sha_str is not None
        assert fp.file_name is not None
        self._log.info("Sending %s through proxy %s", fp.name, proxy["jid"])

        def _on_connected() -> None:
            iq = nbxmpp.Iq(typ="set", to=proxy["jid"], queryNS=Namespace.BYTESTREAM)
            query = iq.getQuery()
            query.setAttr("sid", fp.sid)
            query.setTagData("activate", fp.receiver)
            self._con.connection.send_stanza(iq, callback=_on_activated)

        def _on_activated(_client: types.NBXMPPClient, stanza: Iq) -> None:
            if fp.stopped:
                sender.cancel()
                return
            if not nbxmpp.isResultNode(stanza):
                sender.cancel()
                _on_error(f"Proxy did not activate the transfer: {stanza.getError()}")
                return
            fp.started = True
            fp.received_len = 0
            sender.send_file()

        def _on_progress(sent: int) -> None:
            fp.received_len = sent
            app.ged.raise_event(ged_events.FileProgress(file_props=fp))

        def _on_finished() -> None:
            self._log.info("File sent through proxy: %s", fp.name)
            fp.completed = True
            fp.connected = False
            app.ged.raise_event(
                ged_events.FileCompleted(
                    account=self._account, file_props=fp, jid=fp.receiver
                )
            )

        def _on_error(message: str) -> None:
            if fp.stopped:
                return
            fp.connected = False
            if fp.started:
                # The transfer was running, the receiver gave up
                self._log.warning("Proxy transfer of %s failed: %s", fp.name, message)
                fp.error = -1
                app.ged.raise_event(
                    ged_events.FileError(
                        account=self._account, file_props=fp, jid=fp.receiver
                    )
                )
                return
            app.ged.raise_event(
                ged_events.FileSendError(
                    account=self._account,
                    file_props=fp,
                    jid=JID.from_string(fp.receiver).bare,
                    error_msg=message,
                )
            )

        sender = Socks5ProxySender(
            proxy["host"],
            int(proxy["port"]),
            fp.sha_str,
            Path(fp.file_name),
            on_connected=_on_connected,
            on_progress=_on_progress,
            on_finished=_on_finished,
            on_error=_on_error,
        )
        fp.connected = True
        fp.disconnect_cb = sender.cancel
        sender.start()

    def send_file(self, jid: JID, path: Path) -> None:
        """
        Offer a file to a resource of a contact, the transfer starts once
        the contact accepts it
        """
        sid = str(uuid.uuid4())
        own_jid = str(self._con.get_own_jid())

        fp = FilesProp.getNewFileProp(self._account, sid)
        fp.type_ = "s"
        fp.sid = sid
        fp.transport_sid = sid
        fp.session_type = "si"
        fp.name = path.name
        fp.file_name = str(path)
        fp.size = path.stat().st_size
        fp.mime_type = guess_mime_type(path)
        fp.sender = own_jid
        fp.receiver = str(jid)

        iq = nbxmpp.Iq(typ="set", to=str(jid))
        si = iq.addChild(
            "si",
            namespace=NS_SI,
            attrs={"id": sid, "mime-type": fp.mime_type, "profile": NS_SI_FT},
        )
        si.addChild(
            "file", namespace=NS_SI_FT, attrs={"name": fp.name, "size": str(fp.size)}
        )
        feature = si.addChild("feature", namespace=NS_FEATURE_NEG)
        x = feature.addChild("x", namespace="jabber:x:data", attrs={"type": "form"})
        field = x.addChild(
            "field", attrs={"var": "stream-method", "type": "list-single"}
        )
        for method in (Namespace.BYTESTREAM, Namespace.IBB):
            field.addChild("option").addChild("value").setData(method)

        self._con.connection.send_stanza(
            iq, callback=self._on_offer_response, user_data={"sid": sid}
        )
        self._log.info(
            "SI offer sent: to=%s name=%s size=%s sid=%s", jid, fp.name, fp.size, sid
        )

        self._store_transfer(fp, jid.new_as_bare(), ChatDirection.OUTGOING)
        app.ged.raise_event(
            ged_events.FileRequestSent(
                account=self._account, file_props=fp, jid=jid.new_as_bare()
            )
        )

    def _on_offer_response(
        self, _client: types.NBXMPPClient, stanza: Iq, sid: str
    ) -> None:
        fp = FilesProp.getFileProp(self._account, sid)
        if fp is None or fp.stopped:
            return

        bare_jid = JID.from_string(fp.receiver).bare
        if not nbxmpp.isResultNode(stanza):
            # Pidgin answers a declined offer with <forbidden/>
            condition = stanza.getError()
            self._log.info("SI offer %s refused: %s", sid, condition)
            fp.error = ERROR_DECLINED if condition == "forbidden" else -4
            app.ged.raise_event(
                ged_events.FileRequestError(
                    conn=self._con,
                    file_props=fp,
                    jid=bare_jid,
                    error_msg=stanza.getErrorMsg() or "",
                )
            )
            return

        method = self._get_chosen_method(stanza)
        self._log.info("SI offer %s accepted, stream method: %s", sid, method)
        if method == Namespace.BYTESTREAM:
            self._con.get_module("Bytestream").send_socks5_info(fp, use_upnp=False)
            if fp.direct_blocked:
                set_connection_method(fp, "proxy")
            elif fp.proxyhosts:
                set_connection_method(fp, "direct+proxy")
            else:
                set_connection_method(fp, "direct")
        elif method == Namespace.IBB:
            set_connection_method(fp, "ibb")
            # Closed by the IBB module once the transfer ends
            file = open(fp.file_name, "rb")  # noqa: SIM115
            self._con.get_module("IBB").send_open(fp.receiver, sid, file)
        else:
            fp.error = -4
            app.ged.raise_event(
                ged_events.FileRequestError(
                    conn=self._con,
                    file_props=fp,
                    jid=bare_jid,
                    error_msg=f"Unsupported stream method: {method}",
                )
            )

    @staticmethod
    def _get_chosen_method(stanza: Iq) -> str | None:
        si = stanza.getTag("si", namespace=NS_SI)
        if si is None:
            return None
        feature = si.getTag("feature", namespace=NS_FEATURE_NEG)
        if feature is None:
            return None
        x = feature.getTag("x", namespace="jabber:x:data")
        if x is None:
            return None
        for field in x.getTags("field"):
            if field.getAttr("var") == "stream-method":
                value = field.getTag("value")
                if value is not None:
                    return value.getData()
        return None

    def _store_transfer(
        self, fp: FileProp, remote_jid: JID, direction: ChatDirection
    ) -> str:
        resource = None
        if direction == ChatDirection.INCOMING:
            resource = JID.from_string(fp.sender).resource

        ft_data = mod.FileTransfer(
            state=1,
            name=fp.name,
            size=fp.size,
            source=[mod.JingleFT(type="jingleft", sid=fp.sid)],
        )
        ft_data.path = fp.file_name
        message_data = mod.Message(
            account_=self._account,
            remote_jid_=remote_jid,
            resource=resource,
            type=MessageType.CHAT,
            direction=direction,
            timestamp=utc_now(),
            state=MessageState.ACKNOWLEDGED,
            id=str(uuid.uuid4()),
            filetransfers=[ft_data],
        )
        try:
            app.storage.archive.insert_object(message_data)
        except Exception as exc:
            self._log.warning("Failed to store SI transfer in archive: %s", exc)
        return message_data.id

    @staticmethod
    def _get_save_path(filename: str, sid: str) -> str:
        # Same folder and naming scheme as files downloaded from the server
        # (see get_image_paths), so both offer the same "Save as" afterwards
        save_dir = configpaths.get("DOWNLOADS")
        save_dir.mkdir(parents=True, exist_ok=True)
        path = Path(sanitize_filename(filename))
        name_hash = hashlib.sha1(sid.encode()).hexdigest()
        return str(save_dir / f"{path.stem}_{name_hash}{path.suffix}")
