# This file is part of Gajim.
#
# SPDX-License-Identifier: GPL-3.0-only

# Reads the ufw configuration to tell whether incoming connections to a port
# are blocked. ufw keeps its rules in world readable files, so no root access
# is needed. Other firewalls (firewalld, plain nftables) are not checked.

from __future__ import annotations

import ipaddress
import logging
from pathlib import Path

log = logging.getLogger("gajim.c.u.firewall")

UFW_CONF = Path("/etc/ufw/ufw.conf")
UFW_DEFAULTS = Path("/etc/default/ufw")
UFW_USER_RULES = Path("/etc/ufw/user.rules")


def _read_setting(path: Path, name: str) -> str | None:
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return None


def _port_matches(port_spec: str, port: int) -> bool:
    # "any", "28011", "6000:6007" or a comma separated list of these
    if port_spec == "any":
        return True
    for part in port_spec.split(","):
        if ":" in part:
            start, end = part.split(":", 1)
            if int(start) <= port <= int(end):
                return True
        elif int(part) == port:
            return True
    return False


def _source_matches(source: str, local_address: str | None) -> bool:
    # Peers are expected in the same network as the local address. Without a
    # local address a rule limited to some addresses still counts.
    if local_address is None:
        return True
    try:
        network = ipaddress.ip_network(source, strict=False)
        address = ipaddress.ip_address(local_address)
    except ValueError:
        return True
    if network.version != address.version:
        return False
    return address in network


def _rule_action(
    line: str, port: int, protocol: str, local_address: str | None
) -> str | None:
    # ### tuple ### <action> <proto> <dport> <dst> <sport> <src> [<dapp> <sapp>]
    #     <direction> [comment=...]
    fields = line.removeprefix("### tuple ###").split()
    if len(fields) < 7:
        return None

    fields = [field for field in fields if not field.startswith("comment=")]
    action, rule_protocol, dport = fields[0], fields[1], fields[2]
    source = fields[5]
    direction = fields[-1]
    if not direction.startswith("in"):
        return None
    if rule_protocol not in ("any", protocol):
        return None
    if not _port_matches(dport, port):
        return None
    if not _source_matches(source, local_address):
        return None
    # Logging variants are e.g. "allow_log" or "allow_log-all"
    return action.split("_", 1)[0]


def is_incoming_port_blocked(
    port: int, protocol: str = "tcp", local_address: str | None = None
) -> bool | None:
    """
    True if ufw drops incoming connections to the port, False if they are
    let through, None if that could not be determined.

    The address of the peer is not known in advance. A rule limited to some
    addresses counts if they include local_address, the peer is expected in
    the same network.
    """

    try:
        if not UFW_CONF.exists():
            return False
        if _read_setting(UFW_CONF, "ENABLED") != "yes":
            return False

        for line in UFW_USER_RULES.read_text().splitlines():
            if not line.startswith("### tuple ###"):
                continue
            action = _rule_action(line, port, protocol, local_address)
            if action is None:
                continue
            # Rules are evaluated in order, the first match wins
            return action in ("deny", "reject")

        policy = _read_setting(UFW_DEFAULTS, "DEFAULT_INPUT_POLICY")
    except (OSError, ValueError) as error:
        log.info("Could not read the ufw configuration: %s", error)
        return None

    if policy is None:
        return None
    return policy.upper() in ("DROP", "REJECT")
