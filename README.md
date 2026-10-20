[[_TOC_]]

## Gajim

A fully-featured XMPP chat client.

Gajim aims to be an easy to use and fully-featured XMPP client.
Just chat with your friends or family, easily share pictures and 
thoughts or discuss the news with your groups.

## Este fork (Omajim)

Fork do Gajim 2.6.0, exibido como **Omajim**, com ícone no estilo dos apps
do Omarchy e ícone de bandeja feito com glifos de chat da Nerd Font, que
segue a cor do tema e muda de desenho conforme o status.
Abre pelo lançador ou pelos comandos `omajim` e `gajim`. Se já existir uma
pasta `~/.config/gajim`, as contas e o histórico continuam nela e em
`~/.local/share/gajim`; numa instalação nova, ficam em `~/.config/omajim` e
`~/.local/share/omajim`. Para os contatos, o cliente se identifica como
Omajim.

Inclui:

- envio e recebimento de arquivos com clientes que usam SI (XEP-0096), como o
  Pidgin, por conexão direta na rede local ou pelo servidor (IBB);
- tema que segue o tema atual do [Omarchy](https://omarchy.org) e troca junto
  com ele;
- no Omarchy, um widget dentro da bandeja no lugar do ícone dela: glifo da
  Nerd Font na cor do tema, bolinha com a cor do status, clique para mostrar a
  janela e clique direito para o menu, com o status num submenu. O Omajim instala o plugin
  `omajim.status` em `~/.config/omarchy/plugins` na primeira vez que abre e o
  mantém atualizado; para tirá-lo, use `omarchy plugin remove omajim.status`
  e mostre o ícone da bandeja de novo pelo gerenciador da bandeja;
- seleção de texto entre mensagens, arrastando de uma mensagem a outra;
- Shift+clique para selecionar mensagens inteiras: clique numa mensagem e dê
  Shift+clique em outra para entrar no modo "Selecionar Mensagens" com o
  intervalo marcado; dentro do modo, Shift+clique numa mensagem marcada a
  desmarca.

### Instalação no Arch Linux

```sh
cd arch && makepkg -si
```

O pacote `omajim-git` substitui o pacote `gajim` oficial (e o antigo
`gajim-higorhgon-git`).

### Firewall para o envio direto

No envio direto (menu "Enviar Arquivo Diretamente…"), quem recebe se conecta
no computador de quem envia, na porta TCP 28011. Com o `ufw` ativo, como no
Omarchy, essa porta vem bloqueada e o envio direto falha. Libere a porta na
máquina que envia, de preferência só para a rede local (troque a faixa pela
da sua rede):

```sh
sudo ufw allow from 192.168.0.0/24 to any port 28011 proto tcp
```

Para descobrir a faixa da rede, veja o endereço da sua interface com
`ip -4 addr`. A porta pode ser trocada no Editor de Configuração Avançada
(`file_transfers_port`); nesse caso, libere a porta escolhida.

Receber arquivos não exige liberar nada: quem recebe é que se conecta em quem
envia.

## Requirements

### Runtime Requirements

- [libadwaita](https://gitlab.gnome.org/GNOME/libadwaita) (>=1.7.0)
- [cairo](https://gitlab.freedesktop.org/cairo/cairo) (>=1.16.0)
- [cryptography](https://pypi.org/project/cryptography/) (>=43.0.0)
- [css-parser](https://pypi.org/project/css-parser/)
- [emoji](https://pypi.org/project/emoji/) (>=2.6.0)
- [GLib](https://gitlab.gnome.org/GNOME/glib) (>=2.80.0)
- [Gtk4](https://gitlab.gnome.org/GNOME/gtk) (>=4.17.5)
- [GtkSourceView5](https://gitlab.gnome.org/GNOME/gtksourceview)
- [GStreamer](https://gitlab.freedesktop.org/gstreamer/gstreamer)
- [gst-plugins-base](https://gitlab.freedesktop.org/gstreamer/gstreamer/-/tree/main/subprojects/gst-plugins-base)
- [keyring](https://pypi.org/project/keyring/)
- [nbxmpp](https://pypi.org/project/nbxmpp/) (>=7.4.0)
- [omemo-dr](https://dev.gajim.org/gajim/omemo-dr) (>=1.2.0)
- [packaging](https://pypi.org/project/packaging/)
- [httpx](https://pypi.org/project/httpx/)
- [h2](https://pypi.org/project/h2/)
- [Pango](https://gitlab.gnome.org/GNOME/pango) (>=1.50.0)
- [Pillow](https://pypi.org/project/Pillow/) (>=9.1.0)
- [precis_i18n](https://pypi.org/project/precis-i18n/)
- [pysequoia](https://pypi.org/project/pysequoia/) (>=0.1.33)
- [pycairo](https://pypi.org/project/pycairo/)
- [PyGObject](https://pypi.org/project/PyGObject/) (>=3.53.0)
- [Python](https://www.python.org/) (>=3.12)
- [qrcode](https://pypi.org/project/qrcode/) (>=7.3.1)
- [socksio](https://pypi.org/project/socksio/)
- [SQLAlchemy](https://pypi.org/project/SQLAlchemy/) (>=2.0.0)
- [sqlite](https://www.sqlite.org/) (>=3.35.0)
- [truststore](https://pypi.org/project/truststore/)
- [pystray](https://github.com/moses-palmer/pystray) (Only on Windows)
- [PyWinRT](https://github.com/pywinrt/pywinrt) (Only on Windows)
- [windows-toasts](https://github.com/DatGuy1/Windows-Toasts) (Only on Windows)

### Optional Runtime Requirements

- D-Bus running to have gajim-remote working
- [sentry-sdk](https://pypi.org/project/sentry-sdk/) for Sentry error reporting to dev.gajim.org (users decide whether to send reports or not)
- [libspelling](https://gitlab.gnome.org/GNOME/libspelling) and hunspell-LANG where lang is your locale eg. en, fr etc
- [libsecret](https://gitlab.gnome.org/GNOME/libsecret/) for GNOME Keyring or KDE support as password storage
- [GUPnP-IGD](https://gitlab.gnome.org/GNOME/gupnp) for better NAT traversing
- [NetworkManager](https://gitlab.freedesktop.org/NetworkManager/NetworkManager) for network lose detection
- [GeoClue](https://gitlab.freedesktop.org/geoclue/geoclue) for sharing your location
- [distro](https://pypi.org/project/distro/) to get accurate information about the used operating system (name, version)

#### Rich Previews (Images and Voice Messages)

- [gst-libav](https://gitlab.freedesktop.org/gstreamer/gstreamer/-/tree/main/subprojects/gst-libav)
- [gst-plugins-good](https://gitlab.freedesktop.org/gstreamer/gstreamer/-/tree/main/subprojects/gst-plugins-good)
- [gst-plugins-bad](https://gitlab.freedesktop.org/gstreamer/gstreamer/-/tree/main/subprojects/gst-plugins-bad)
- [gst-plugins-rs](https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs)

### Build Requirements

- [setuptools](https://pypi.org/project/setuptools/) (>=65.0.0)
- [gettext](https://savannah.gnu.org/projects/gettext/)

To build Gajim a PEP517 build frontend like pip (https://pip.pypa.io/en/stable/) or build (https://pypa-build.readthedocs.io/en/stable/) must be used.

The build frontend takes care of installing all python build requirements. Beware `gettext` is not a python library and cannot be installed by the build frontend.

## Building

### Building the metadata files and translation

```bash
$ uv run ./make.py build -h

usage: make.py build [-h] [--dist {unix,flatpak,flatpak-nightly,win}]
```

Afterwards metadata files can be found in `dist/metadata` folder. 

### Building the wheel

#### Using `build`

```bash
python -m build -w
```

#### Using `uv`

```bash
uv build
```

## Installing

```bash
uv pip install dist/name_of_wheel.whl
```

### Installing the metadata files (Unix only)

```bash
$ uv run ./make.py install -h

usage: make.py install [-h] [--dist {unix,flatpak,flatpak-nightly}] [--prefix PREFIX]

options:
  -h, --help            show this help message and exit
  --dist {unix,flatpak,flatpak-nightly}
                        Distribution
  --prefix PREFIX       The path prefix for installation (e.g. "/usr")
```

## Tests

- `uv run pytest`
- `uv run pytest ./test/gtk/gui_file.py` (for testing GUI files)

## Packages and install instructions

### Packages

- [Arch Linux](https://www.archlinux.org/packages/extra/any/gajim/)
- [Debian](https://packages.debian.org/stable/gajim)
- [Fedora](https://packages.fedoraproject.org/pkgs/gajim/)
- [FreeBSD](https://www.freshports.org/net-im/gajim/)
- [openSUSE](https://software.opensuse.org/package/gajim)
- [Ubuntu](https://packages.ubuntu.com/gajim)

### Flatpak

see [README](./flatpak/README.md)

### Snapshots

- [Daily Linux](https://www.gajim.org/downloads/snap/)
- [Daily Windows](https://gajim.org/downloads/snap/win)

### Mac

see [Wiki](https://dev.gajim.org/gajim/gajim/-/wikis/help/Gajim-on-macOS)

## Developing

The easiest way to run the development version is to use [uv](https://docs.astral.sh/uv/)

    uv run ./launch.py --user-profile dev

### Windows

see [README](./win/README.md)

## Miscellaneous

### Debugging

Execute gajim with `--verbose`

### Links

- [FAQ](https://dev.gajim.org/gajim/gajim/wikis/help/gajimfaq)
- [Wiki](https://dev.gajim.org/gajim/gajim/wikis/home)

That is all, **enjoy**!

(C) 2003-2026
The Gajim Team
[https://gajim.org](https://gajim.org)

We use original art and parts of sounds and other art from Psi, Gossip, Gnomebaker, Gaim
and some icons from various gnome-icons (mostly Dropline Etiquette) we found at art.gnome.org.
If you think we're violating a license please inform us. Thank you.
