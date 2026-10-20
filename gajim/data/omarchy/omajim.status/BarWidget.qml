import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import Quickshell.Services.SystemTray
import qs.Commons
import qs.Ui

// Omajim's tray item, drawn as a bar icon: a Nerd Font glyph in the bar's
// own color with a dot in the color of the status, like the notification
// badge. Installed and kept up to date by Omajim itself.
BarWidget {
  id: root
  moduleName: "omajim.status"

  readonly property string trayId: "org.gajim.Gajim"

  readonly property var trayItem: {
    var values = SystemTray.items.values
    for (var i = 0; i < values.length; i++) {
      if (String(values[i].id) === trayId) return values[i]
    }
    return null
  }

  // Omajim names its tray icons omajim-status-<status>-symbolic
  readonly property string iconState: {
    if (!trayItem) return ""
    var name = String(trayItem.icon || "").split("?")[0]
    var match = name.match(/-status-(.+?)(-symbolic)?$/)
    return match ? match[1] : ""
  }
  readonly property bool unread: iconState === "message-new"
  // The tray icon shows new messages instead of the status, so keep the
  // last status for the dot
  property string status: "offline"
  onIconStateChanged: if (iconState !== "" && !unread) status = iconState

  // Status colors from the theme's colors.toml; Color has no green or yellow
  property var themeColors: ({})

  function themeColor(name, fallback) {
    var value = themeColors[name]
    return value ? value : fallback
  }

  readonly property color statusColor: {
    switch (status) {
      case "online":
      case "chat": return themeColor("green", "#a6e3a1")
      case "away": return themeColor("yellow", "#f9e2af")
      case "xa": return themeColor("orange", themeColor("yellow", "#f9e2af"))
      case "dnd":
      case "error": return themeColor("red", Color.urgent)
      case "connecting": return themeColor("blue", Color.accent)
      default: return Color.muted
    }
  }

  readonly property string statusLabel: {
    switch (status) {
      case "online": return "Disponível"
      case "chat": return "Livre para conversar"
      case "away": return "Ausente"
      case "xa": return "Ausente por muito tempo"
      case "dnd": return "Não perturbe"
      case "connecting": return "Conectando"
      case "error": return "Erro de conexão"
      default: return "Offline"
    }
  }

  FileView {
    id: colorsFile
    path: Quickshell.env("HOME") + "/.local/state/omarchy/current/theme/colors.toml"
    printErrors: false
    onLoaded: {
      var colors = {}
      var lines = text().split("\n")
      for (var i = 0; i < lines.length; i++) {
        var kv = lines[i].match(/^\s*([A-Za-z0-9_]+)\s*=\s*"(#[0-9A-Fa-f]{6,8})"/)
        if (kv) colors[kv[1]] = kv[2]
      }
      root.themeColors = colors
    }
  }

  // Theme switches reach the shell over IPC, not through the file
  Connections {
    target: Color
    function onForegroundChanged() { colorsFile.reload() }
    function onAccentChanged() { colorsFile.reload() }
  }

  visible: trayItem !== null
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.unread ? "󰭺" : "󰭹"
    dimmed: root.status === "offline"
    active: root.unread
    activeColor: Color.accent
    tooltipText: root.unread ? "Omajim · novas mensagens" : "Omajim · " + root.statusLabel
    onPressed: function(b) {
      if (!root.trayItem) return
      if (b === Qt.RightButton) root.menuOpen = !root.menuOpen
      else root.trayItem.activate()
    }
  }

  Rectangle {
    anchors.right: button.right
    anchors.rightMargin: Style.space(3)
    anchors.top: button.top
    anchors.topMargin: Style.space(5)
    width: Style.space(6)
    height: width
    radius: width / 2
    color: root.statusColor
  }

  // Omajim's tray menu, with its submenus (Status) laid out inline
  property bool menuOpen: false

  function close() { menuOpen = false }

  QsMenuOpener {
    id: menuOpener
    menu: root.trayItem ? root.trayItem.menu : null
  }

  component MenuRow: Item {
    id: row
    required property var entry
    property int indent: 0

    width: parent ? parent.width : 0
    implicitHeight: entry.isSeparator ? Style.space(11) : Style.space(30)
    opacity: entry.enabled ? 1.0 : 0.45

    Rectangle {
      visible: row.entry.isSeparator
      anchors.left: parent.left
      anchors.leftMargin: Style.space(10)
      anchors.right: parent.right
      anchors.rightMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      height: 1
      color: Color.popups.border
      opacity: 0.45
    }

    Rectangle {
      visible: !row.entry.isSeparator
      anchors.fill: parent
      radius: Math.max(2, Style.cornerRadius)
      color: rowMouse.containsMouse && row.entry.enabled ? Style.hoverFillFor(button.foreground, button.foreground) : "transparent"
    }

    Text {
      visible: !row.entry.isSeparator
      anchors.verticalCenter: parent.verticalCenter
      anchors.left: parent.left
      anchors.leftMargin: Style.space(10 + row.indent)
      anchors.right: parent.right
      anchors.rightMargin: Style.space(10)
      textFormat: Text.PlainText
      text: (row.entry.checkState === Qt.Checked ? "  " : "") + String(row.entry.text || "")
      color: button.foreground
      font.family: button.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
    }

    MouseArea {
      id: rowMouse
      anchors.fill: parent
      hoverEnabled: true
      enabled: !row.entry.isSeparator && row.entry.enabled
      cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
      onClicked: {
        row.entry.triggered()
        root.close()
      }
    }
  }

  PopupCard {
    id: menuPopup
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.menuOpen
    padding: Style.space(8)
    borderColor: Qt.rgba(button.foreground.r, button.foreground.g, button.foreground.b, 0.45)
    contentWidth: menuPopup.fittedContentWidth(Style.space(232))
    contentHeight: menuPopup.fittedContentHeight(menuColumn.implicitHeight, Style.space(480))

    Flickable {
      anchors.fill: parent
      contentWidth: width
      contentHeight: menuColumn.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      interactive: contentHeight > height

      ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

      Column {
        id: menuColumn
        width: parent.width

        Repeater {
          model: menuOpener.children

          delegate: Column {
            id: group
            required property var modelData
            width: menuColumn.width

            MenuRow {
              entry: group.modelData
              // A submenu's own row is its heading
              enabled: !group.modelData.hasChildren
            }

            QsMenuOpener {
              id: subOpener
              menu: group.modelData.hasChildren ? group.modelData : null
            }

            Repeater {
              model: group.modelData.hasChildren ? subOpener.children : []

              delegate: MenuRow {
                required property var modelData
                entry: modelData
                indent: 12
              }
            }
          }
        }
      }
    }
  }
}
