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
    // nf-fa-message; new messages show in the accent color
    text: "\uf27a"
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

  // Omajim's tray menu, drawn like the tray's own: QsMenuEntry.display()
  // needs a QApplication the shell doesn't have, so submenus (Status) open
  // inside the card, each level with its own live opener
  property bool menuOpen: false
  property var submenuStack: []
  readonly property int submenuDepth: submenuStack.length
  readonly property string currentTitle: submenuDepth > 0 ? submenuStack[submenuDepth - 1].title : ""
  readonly property var currentChildren: submenuDepth > 0
    ? submenuStack[submenuDepth - 1].opener.children
    : menuOpener.children

  // Swapping levels puts another row under a cursor that hasn't moved, so
  // ignore row clicks for a beat after each change
  property bool menuLevelSettling: false

  function close() { menuOpen = false }

  function settleMenuLevel() {
    menuLevelSettling = true
    menuSettleTimer.restart()
  }

  function resetMenu() {
    menuLevelSettling = false
    menuSettleTimer.stop()
    menuFlick.contentY = 0
    // Deepest first: a child opener's entry belongs to its parent's model
    var openers = submenuStack
    submenuStack = []
    for (var i = openers.length - 1; i >= 0; i--) openers[i].opener.destroy()
  }

  function enterSubmenu(entry, title) {
    var opener = submenuOpenerComponent.createObject(root, { menu: entry })
    if (!opener) return
    var stack = submenuStack.slice()
    stack.push({ opener: opener, title: title })
    submenuStack = stack
    settleMenuLevel()
  }

  function leaveSubmenu() {
    if (submenuStack.length === 0) return
    var stack = submenuStack.slice()
    var top = stack.pop()
    submenuStack = stack
    top.opener.destroy()
    settleMenuLevel()
  }

  QsMenuOpener {
    id: menuOpener
    menu: root.trayItem ? root.trayItem.menu : null
  }

  Component {
    id: submenuOpenerComponent
    QsMenuOpener {}
  }

  Timer {
    id: menuSettleTimer
    interval: 250
    onTriggered: root.menuLevelSettling = false
  }

  PopupCard {
    id: menuPopup
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.menuOpen
    // Reset once the fade out is over, not mid-fade
    onVisibleChanged: if (!visible) root.resetMenu()
    padding: Style.space(8)
    borderColor: Qt.rgba(button.foreground.r, button.foreground.g, button.foreground.b, 0.45)
    contentWidth: menuPopup.fittedContentWidth(Style.space(232))
    contentHeight: menuPopup.fittedContentHeight(menuHeaderHeight + menuColumn.implicitHeight, Style.space(420))

    readonly property int menuHeaderHeight: menuHeader.visible ? menuHeader.implicitHeight : 0

    Column {
      id: menuLayout
      anchors.fill: parent
      spacing: 0

      // Inside a submenu: its name, and the way back out
      Column {
        id: menuHeader
        visible: root.submenuDepth > 0
        width: menuLayout.width
        spacing: 0

        Item {
          width: menuHeader.width
          implicitHeight: Style.space(30)

          Rectangle {
            anchors.fill: parent
            radius: Math.max(2, Style.cornerRadius)
            color: backMouse.containsMouse ? Style.hoverFillFor(button.foreground, button.foreground) : "transparent"
          }

          Text {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            width: Style.space(22)
            horizontalAlignment: Text.AlignHCenter
            textFormat: Text.PlainText
            text: I18n.rightToLeft ? "›" : "‹"
            color: button.foreground
            font.family: button.fontFamily
            font.pixelSize: Style.font.bodySmall
          }

          Text {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: Style.space(28)
            anchors.right: parent.right
            anchors.rightMargin: Style.space(10)
            textFormat: Text.PlainText
            text: root.currentTitle
            color: button.foreground
            font.family: button.fontFamily
            font.pixelSize: Style.font.bodySmall
            elide: Text.ElideRight
          }

          MouseArea {
            id: backMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: {
              if (root.menuLevelSettling) return
              menuFlick.contentY = 0
              root.leaveSubmenu()
            }
          }
        }

        Item {
          width: menuHeader.width
          implicitHeight: Style.space(11)

          Rectangle {
            anchors.left: parent.left
            anchors.leftMargin: Style.space(10)
            anchors.right: parent.right
            anchors.rightMargin: Style.space(10)
            anchors.verticalCenter: parent.verticalCenter
            height: 1
            color: Color.popups.border
            opacity: 0.45
          }
        }
      }

      Flickable {
        id: menuFlick
        width: menuLayout.width
        height: menuLayout.height - menuPopup.menuHeaderHeight
        contentWidth: width
        contentHeight: menuColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height

        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: menuColumn
          width: menuFlick.width
          spacing: 0

          Repeater {
            model: root.currentChildren

            delegate: Item {
              id: menuRow
              required property var modelData

              readonly property string rowText: String(modelData.text || "")

              width: menuColumn.width
              implicitHeight: modelData.isSeparator ? Style.space(11) : Style.space(30)
              opacity: modelData.enabled ? 1.0 : 0.45

              Rectangle {
                visible: menuRow.modelData.isSeparator
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
                visible: !menuRow.modelData.isSeparator
                anchors.fill: parent
                radius: Math.max(2, Style.cornerRadius)
                color: rowMouse.containsMouse && menuRow.modelData.enabled ? Style.hoverFillFor(button.foreground, button.foreground) : "transparent"
              }

              Text {
                visible: !menuRow.modelData.isSeparator && menuRow.modelData.buttonType !== QsMenuButtonType.None
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left
                width: Style.space(22)
                horizontalAlignment: Text.AlignHCenter
                textFormat: Text.PlainText
                text: menuRow.modelData.checkState === Qt.Checked ? "" : ""
                color: button.foreground
                font.family: button.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Text {
                visible: !menuRow.modelData.isSeparator
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left
                anchors.leftMargin: Style.space(28)
                anchors.right: submenuGlyph.left
                anchors.rightMargin: Style.space(8)
                textFormat: Text.PlainText
                text: menuRow.rowText
                color: button.foreground
                font.family: button.fontFamily
                font.pixelSize: Style.font.bodySmall
                elide: Text.ElideRight
              }

              Text {
                id: submenuGlyph
                visible: !menuRow.modelData.isSeparator && menuRow.modelData.hasChildren
                anchors.verticalCenter: parent.verticalCenter
                anchors.right: parent.right
                anchors.rightMargin: Style.space(10)
                textFormat: Text.PlainText
                text: I18n.rightToLeft ? "‹" : "›"
                color: button.foreground
                font.family: button.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              MouseArea {
                id: rowMouse
                anchors.fill: parent
                hoverEnabled: true
                enabled: !menuRow.modelData.isSeparator && menuRow.modelData.enabled
                cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: {
                  if (root.menuLevelSettling) return
                  if (menuRow.modelData.hasChildren) {
                    // Before the swap, which destroys this delegate
                    menuFlick.contentY = 0
                    root.enterSubmenu(menuRow.modelData, menuRow.rowText)
                  } else {
                    menuRow.modelData.triggered()
                    root.close()
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
