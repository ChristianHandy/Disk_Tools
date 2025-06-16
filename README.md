🇩🇪 Deutscher Teil
1. Voraussetzungen & Installation

    Systemanforderungen

        Linux mit Python 3.8+

        Root-Zugriff für Laufwerksbefehle

        Installierte Tools: lsblk, smartctl, wipefs, mkfs.ext4, mkfs.xfs, mkfs.vfat, dd, badblocks

        Optional: Nginx oder Apache für HTTPS

    Umgebung einrichten

sudo apt update
sudo apt install python3 python3-venv python3-pip nginx certbot

Quellcode auschecken

git clone https://<repo-url>/disktool.git
cd disktool

Virtuelle Umgebung & Abhängigkeiten

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

Datenbank initialisieren

    sudo ./app.py   # startet das Skript; init_db() erzeugt die SQLite-Datei

2. Programmstart & HTTPS (Let’s Encrypt)

    Certbot-Renew-Hook anlegen

sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh > /dev/null <<'EOF'
#!/bin/bash
set -e
nginx -t && systemctl reload nginx
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

App mit SSL-Context starten (optional, bei eigenem Flask-HTTPS)

    export SSL_CERT=/etc/letsencrypt/live/yourdomain/fullchain.pem
    export SSL_KEY=/etc/letsencrypt/live/yourdomain/privkey.pem
    sudo python3 app.py --ssl-context "$SSL_CERT,$SSL_KEY"

    Nginx-Proxy konfigurieren

        Proxy-Pass zu http://127.0.0.1:5000

        SSL-Terminierung in Nginx

3. Funktionen & Bedienung
Bereich	Beschreibung
Startseite	Übersicht aller angeschlossenen Festplatten, Suchfilter.
SMART Test	Kurz-/Lang-Test starten, Report anzeigen.
Formatieren	Ext4/XFS/FAT32; lang/kurz, mit Fortschritt im Hintergrund.
Validator	Prüft erste 256 Blöcke, zeigt grüne/rote Kästchen.
Historie	Listet alle Operationen und SMART-Verläufe, mit Stop-Button.
Dashboard	Anzahl Platten, fehlerhafte SMART-Status, laufende Tasks.
Automatik	Erkennt neu verbundene Platten und startet Format+SMART.
Mount	Mount-Dialog für ausgewähltes Gerät.
Export/Import SMART	CSV-Export und Upload für externe Reports.
Toggle Auto	Schalter in Navbar: Automatik ein/aus. Popup bei Aktionen.
Beispiel-Workflow

    Suche

        Filter in Suchleiste nach Gerätekennung oder Modell.

    SMART Test

        Klick auf „SMART Test“ → löst Kurz-Test aus, Flash-Meldung + Popup.

    Formatieren

        Klick „Format“, wähle FS, „Start“ → Fortschritts-Page mit Live-Bar.

    Validator

        Klick „Validate“ → Block-Darstellung in Farbe.

    Automatik

        Klick auf „Auto: AN“ in der Navbar → ab jetzt beobachtet, neue Platten werden formatiert und SMART-getestet.

🇬🇧 English Part
1. Prerequisites & Installation

    Requirements

        Linux with Python 3.8+

        Root privileges for disk operations

        Installed utilities: lsblk, smartctl, wipefs, mkfs.ext4, mkfs.xfs, mkfs.vfat, dd, badblocks

        Optional: Nginx or Apache for HTTPS

    Environment Setup

sudo apt update
sudo apt install python3 python3-venv python3-pip nginx certbot

Clone & Enter Repo

git clone https://<repo-url>/disktool.git
cd disktool

Virtualenv & Dependencies

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

Initialize Database

    sudo ./app.py   # init_db() creates the SQLite file

2. Launch & HTTPS (Let’s Encrypt)

    Certbot Deploy Hook

sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh > /dev/null <<'EOF'
#!/bin/bash
set -e
nginx -t && systemctl reload nginx
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

Start App with SSL (optional)

    export SSL_CERT=/etc/letsencrypt/live/yourdomain/fullchain.pem
    export SSL_KEY=/etc/letsencrypt/live/yourdomain/privkey.pem
    sudo python3 app.py --ssl-context "$SSL_CERT,$SSL_KEY"

    Nginx Proxy Configuration

        Proxy pass to http://127.0.0.1:5000

        SSL termination in Nginx

3. Features & Usage
Section	Description
Home	Lists all connected disks, with search filter.
SMART Test	Launch short/long tests, view full report.
Formatting	Ext4/XFS/FAT32 formats in background with progress bar.
Validator	Checks first 256 blocks, shows green/red squares.
History	Operation log + SMART history, with Stop task button.
Dashboard	Total disks, bad SMART counts, running tasks.
Automatic	Detects new disks, runs format + SMART automatically.
Mount	Mount dialog for selected disk.
Export/Import	CSV export of SMART, upload of reports.
Toggle Auto	Navbar button to enable/disable auto mode, shows popup.
Example Workflow

    Search

        Filter disks by name or model in search box.

    SMART Test

        Click “SMART Test” → triggers a short test, shows flash message & popup.

    Formatting

        Click “Format”, choose filesystem, “Start” → live progress page.

    Validator

        Click “Validate” → colored block grid.

    Automatic Mode

        Toggle “Auto: ON” → new disks get formatted and SMART-tested immediately.

Mit dieser Dokumentation können Sie das Disk-Management-Tool vollständig einrichten, betreiben und automatisieren.
With this guide, you can fully install, operate, and automate the disk management application.
