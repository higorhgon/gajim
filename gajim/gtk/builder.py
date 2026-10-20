# This file is part of Gajim.
#
# SPDX-License-Identifier: GPL-3.0-only

from typing import Any

import functools
import logging
import xml.etree.ElementTree as ET

from gi.repository import Gtk

from gajim.common import configpaths
from gajim.common import i18n
from gajim.common.i18n import _

log = logging.getLogger("gajim.gtk")


class GajimBuilder:
    filename = ""

    def __init__(
        self,
        filename: str | None = None,
        instance: Any = None,
        widgets: list[str] | None = None,
        domain: str | None = None,
        gettext_: Any | None = None,
    ) -> None:
        if filename is None:
            filename = self.filename

        self._builder = Gtk.Builder(instance)

        if domain is None:
            domain = i18n.DOMAIN
        self._builder.set_translation_domain(domain)

        if gettext_ is None:
            gettext_ = _

        xml_text = self._load_string_from_filename(filename, gettext_)

        log.debug("Load ui file: %s", filename)

        if widgets is not None:
            self._builder.add_objects_from_string(xml_text, widgets)
        else:
            self._builder.add_from_string(xml_text)

    @staticmethod
    @functools.cache
    def _load_string_from_filename(filename: str, gettext_: Any) -> str:
        file_path = str(configpaths.get("GUI") / filename)

        # Translate here rather than in GTK, which works around non working
        # translation on Windows and shows the app's name as Omajim
        tree = ET.parse(file_path)
        for node in tree.findall(".//*[@translatable='yes']"):
            node.text = i18n.rebrand(gettext_(node.text)) if node.text else ""
            del node.attrib["translatable"]

        return ET.tostring(tree.getroot(), encoding="unicode", method="xml")

    def __getattr__(self, name: str) -> Any:
        try:
            return getattr(self._builder, name)
        except AttributeError:
            return self._builder.get_object(name)

    def get(self, name: str) -> Any:
        return self._builder.get_object(name)


def get_builder(
    file_name: str,
    instance: Any = None,
    widgets: list[str] | None = None,
) -> GajimBuilder:
    return GajimBuilder(file_name, instance, widgets)
