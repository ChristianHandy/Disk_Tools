import os, json, csv, sqlite3, subprocess, threading, re
from datetime import datetime
from pathlib import Path

# Globale Pfade und Variablen
DB_FILE = Path(__file__).with_suffix('.db')
UPLOAD_DIR = Path(__file__).parent / 'uploads'
UPLOAD_DIR.mkdir(exist_ok=True)
auto_enabled = False
AUTO_SKIP_DEVICE = 'mmcblk0'  # z.B. Systemlaufwerk, das bei Auto-Sync ignoriert wird

def get_db():
    """Stellt eine DB-Verbindung her und liefert das Connection-Objekt zurück."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialisiert die SQLite-Datenbank und erforderliche Tabellen, falls noch nicht vorhanden."""
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS disks(
          device TEXT PRIMARY KEY,
          serial TEXT,
          model TEXT,
          size TEXT,
          present INTEGER DEFAULT 1,
          first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS operations(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          device TEXT,
          action TEXT,
          status TEXT,
          progress INTEGER,
          ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS smart_history(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          device TEXT,
          serial TEXT,
          temp INTEGER,
          health TEXT,
          ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS remotes(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT,
          host TEXT,
          port INTEGER DEFAULT 22,
          enabled INTEGER DEFAULT 1,
          added_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        # falls später Spalten hinzugefügt wurden:
        cols = {c[1] for c in db.execute("PRAGMA table_info(disks)")}
        if 'serial' not in cols:
            try:
                db.execute("ALTER TABLE disks ADD COLUMN serial TEXT")
            except Exception:
                pass

def run(cmd):
    """Führt einen Shell-Befehl aus und gibt den gesamten Output zurück."""
    # cmd is a list
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return res.stdout
    except Exception as e:
        return str(e)

# --- Festplatten-Funktionen ---
def ls_disks():
    """Liest alle physischen Disks mit lsblk aus und gibt eine Liste von Devices zurück."""
    output = run(['lsblk', '-J', '-d', '-o', 'NAME,SIZE,MODEL,TYPE'])
    try:
        data = json.loads(output)
    except Exception:
        return []
    # Nur 'disk'-Geräte betrachten
    return [d for d in data.get('blockdevices', []) if d.get('type') == 'disk']

def get_serial(dev):
    """Ermittelt die Seriennummer eines Geräts via smartctl, falls verfügbar."""
    try:
        info = run(['smartctl', '-i', f'/dev/{dev}'])
        for line in info.splitlines():
            if 'Serial Number' in line:
                return line.split(':', 1)[1].strip()
    except Exception:
        pass
    return None

def sync_disks():
    """Synchronisiert die aktuelle Geräteliste in die Datenbank.
       Setzt 'present' für alle alten Geräte auf 0 und fügt neue ein.
       Startet bei Auto-Modus ggf. automatische Aufgaben (Format, SMART)."""
    global auto_enabled
    # Use SQLite-compatible timestamp format for comparison
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    new_devices = []
    with get_db() as db:
        db.execute('UPDATE disks SET present = 0')
        for d in ls_disks():
            serial = get_serial(d['name'])
            db.execute(
                '''INSERT OR REPLACE INTO disks(device, serial, model, size, present, first_seen)
                   VALUES (?, ?, ?, ?, 1,
                           COALESCE((SELECT first_seen FROM disks WHERE device=?), CURRENT_TIMESTAMP))''',
                (d['name'], serial, d.get('model'), d.get('size'), d['name'])
            )
        # Finde neu hinzugekommene Devices (first_seen >= now)
        rows = db.execute('SELECT device FROM disks WHERE first_seen >= ?', (now,)).fetchall()
        for r in rows:
            dev = r['device']
            if dev == AUTO_SKIP_DEVICE or dev.startswith('nvme'):
                continue  # Systemlaufwerke oder NVMe ggf. überspringen
            new_devices.append(dev)
    # Falls Auto-Format/SMART aktiviert ist, entsprechende Tasks starten
    if auto_enabled:
        for dev in new_devices:
            start_format(dev, 'ext4')
            start_smart(dev, 'short')

# --- Operations-Logging in DB ---
def log_op(device, action):
    """Erzeugt einen neuen Eintrag in der Operations-Tabelle und gibt die ID zurück."""
    with get_db() as db:
        cur = db.execute('INSERT INTO operations(device, action, status, progress) VALUES (?, ?, ?, 0)',
                         (device, action, 'RUNNING'))
        return cur.lastrowid

def update_op(op_id, status=None, progress=None):
    """Aktualisiert Status/Progress eines laufenden Operations-Eintrags."""
    sets = []
    vals = []
    if status:
        sets.append('status=?'); vals.append(status)
    if progress is not None:
        sets.append('progress=?'); vals.append(progress)
    if not sets:
        return  # nichts zu updaten
    vals.append(op_id)
    with get_db() as db:
        db.execute(f"UPDATE operations SET {','.join(sets)} WHERE id=?", vals)

# --- Langlaufende Tasks (Formatierung, SMART-Test) ---
def format_worker(device, fs, op_id):
    """Führt die Formatierung eines Geräts aus (Hintergrund-Thread)."""
    path = f'/dev/{device}'
    try:
        run(['wipefs', '-a', path])
        cmd_map = {'ext4': ['mkfs.ext4', '-F'], 'xfs': ['mkfs.xfs', '-f'], 'fat32': ['mkfs.vfat', '-F', '32']}
        if fs not in cmd_map:
            raise ValueError('Unknown fs')
        run(cmd_map[fs] + [path])
        update_op(op_id, status='OK', progress=100)
    except Exception:
        update_op(op_id, status='FAIL', progress=0)

def start_format(device, fs):
    """Startet einen Formatierungsthread für device mit Dateisystem fs."""
    op_id = log_op(device, f'FORMAT_{fs}')
    threading.Thread(target=format_worker, args=(device, fs, op_id), daemon=True).start()
    return op_id

def start_smart(device, mode):
    """Startet einen SMART-Test (kurz/lang) für device."""
    run(['smartctl', '-t', mode, f'/dev/{device}'])
    log_op(device, f'SMART_{mode.upper()}')

def view_smart(device):
    """Liest SMART-Report via smartctl und loggt Temperatur/Health in die History."""
    out = run(['smartctl', '-a', f'/dev/{device}'])
    m = re.search(r'Temperature_Celsius.*\s(\d+)', out)
    temp = int(m.group(1)) if m else None
    health = 'BAD' if 'FAILING_NOW' in out else 'GOOD'
    with get_db() as db:
        db.execute('INSERT INTO smart_history(device, serial, temp, health) VALUES (?, ?, ?, ?)',
                   (device, None, temp, health))
    return out

def validate_blocks(device):
    """Prüft die ersten Blöcke eines Geräts mit direkten Leseversuchen und markiert fehlerhafte Blöcke."""
    # Versuche, die ersten N Blöcke (z.B. 256) zu lesen und sammle fehlerhafte Indices
    max_blocks = 256
    bad_blocks = []
    blocks = []
    # Bestimme Gerätgröße in Bytes
    try:
        size_out = run(['blockdev', '--getsize64', f'/dev/{device}']).strip()
        size = int(size_out)
    except Exception:
        size = None
    if size:
        count = size // 4096
        blocks = list(range(min(count, max_blocks)))
    else:
        blocks = list(range(max_blocks))

    for b in blocks:
        # Lese einen Block an Position b (offset = b*4096) mittels dd with count=1
        offset = b * 4096
        try:
            # dd will return non-zero on read errors; capture output
            res = subprocess.run(['dd', 'if=' + f'/dev/{device}', 'bs=4096', 'count=1', 'skip=' + str(b)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode != 0:
                bad_blocks.append(b)
        except Exception:
            bad_blocks.append(b)
    return blocks, bad_blocks

# --- Hilfsfunktionen für UI/DB-Abfragen (für Flask-Routen) ---
def _parse_df_usage():
    """Parst `df -h` und versucht, Usage-Prozent pro /dev/<name> zurückzugeben."""
    out = run(['df', '-h'])
    usage = {}
    try:
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 6:
                continue
            dev = parts[0]
            perc = parts[4]
            # nur /dev/... Einträge
            if dev.startswith('/dev/'):
                # extrahiere Geräteshortname, z.B. /dev/sda1 -> sda
                name = os.path.basename(dev)
                # strip partition numbers for simple match: sda1 -> sda
                m = re.match(r'([a-zA-Z0-9]+)', name)
                if m:
                    base = re.match(r'([a-zA-Z]+)', name)
                    # fallback: use name as key
                    key = base.group(1) if base else name
                    usage[key] = perc
    except Exception:
        pass
    return usage


def get_disk_list(filter_str=''):
    """Gibt die Liste der aktuellen Disks aus der DB zurück, optional gefiltert nach Device/Modell.
       Liefert eine Liste von dicts inkl. 'usage' Feld (falls verfügbar)."""
    with get_db() as db:
        if filter_str:
            pattern = f"%{filter_str}%"
            rows = db.execute("SELECT * FROM disks WHERE present=1 AND (device LIKE ? OR model LIKE ?)",
                               (pattern, pattern)).fetchall()
        else:
            rows = db.execute("SELECT * FROM disks WHERE present=1").fetchall()
    usage_map = _parse_df_usage()
    disks = []
    for r in rows:
        d = dict(r)
        # try to find usage for this device name
        dev = d.get('device')
        u = usage_map.get(dev)
        # if not found, also try stripping numeric suffix
        if not u:
            base = re.match(r'([a-zA-Z]+)', dev)
            if base:
                u = usage_map.get(base.group(1))
        d['usage'] = u
        disks.append(d)
    return disks


def fetch_history_data():
    """Liest die Verlaufsdaten (operations und smart_history) aus der Datenbank."""
    with get_db() as db:
        ops = db.execute("SELECT * FROM operations ORDER BY ts DESC").fetchall()
        smart = db.execute("SELECT * FROM smart_history ORDER BY ts DESC").fetchall()
    return ops, smart


def clear_history():
    """Löscht alle Einträge aus operations- und smart_history-Tabellen."""
    with get_db() as db:
        db.execute("DELETE FROM operations")
        db.execute("DELETE FROM smart_history")


def get_dashboard_data():
    """Erstellt eine Zusammenfassung für das Dashboard (Anzahlen, etc.)."""
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) FROM disks").fetchone()[0]
        bad = db.execute("SELECT COUNT(*) FROM smart_history WHERE health='BAD'").fetchone()[0]
        running = db.execute("SELECT COUNT(*) FROM operations WHERE status='RUNNING'").fetchone()[0]
        runtimes = []
        for row in db.execute("SELECT device, MIN(ts) AS first_ts FROM operations GROUP BY device").fetchall():
            runtimes.append({'device': row['device'], 'runtime': 'n/a'})
    return {'total': total, 'bad': bad, 'running': running, 'runtimes': runtimes}


def export_smart_data():
    """Exportiert die SMART-Historie in eine CSV-Datei im uploads/ Ordner und gibt den Dateipfad zurück."""
    path = UPLOAD_DIR / 'smart.csv'
    with get_db() as db, open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['id', 'device', 'serial', 'temp', 'health', 'ts'])
        for row in db.execute("SELECT * FROM smart_history").fetchall():
            writer.writerow(tuple(row))
    return path


def import_smart_data(file_storage, device='UNKNOWN'):
    """Importiert einen SMART-Bericht aus einer hochgeladenen Datei in die smart_history Tabelle."""
    filename = file_storage.filename or "smart_upload.txt"
    path = UPLOAD_DIR / filename
    file_storage.save(path)
    text = path.read_text()
    m = re.search(r'Temperature_Celsius.*\s(\d+)', text)
    temp = int(m.group(1)) if m else None
    health = 'BAD' if 'FAILING_NOW' in text else 'GOOD'
    with get_db() as db:
        db.execute("INSERT INTO smart_history(device, serial, temp, health) VALUES (?, ?, ?, ?)",
                   (device, None, temp, health))

# --- Remote management helpers ---
def add_remote(name, host, port=22, enabled=1):
    with get_db() as db:
        db.execute("INSERT INTO remotes(name, host, port, enabled) VALUES (?, ?, ?, ?)",
                   (name, host, port, enabled))

def list_remotes():
    with get_db() as db:
        return db.execute("SELECT * FROM remotes ORDER BY added_ts DESC").fetchall()

def remove_remote(remote_id):
    with get_db() as db:
        db.execute("DELETE FROM remotes WHERE id=?", (remote_id,))

# Task helpers (existing): get_task_status, get_task_action, stop_task, auto_mode_worker

def get_task_status(op_id):
    row = get_db().execute("SELECT status, progress FROM operations WHERE id=?)", (op_id,)).fetchone()
    return (row['status'], row['progress']) if row else (None, None)

def get_task_action(op_id):
    row = get_db().execute("SELECT action FROM operations WHERE id=?", (op_id,)).fetchone()
    return row['action'] if row else None

def stop_task(op_id):
    with get_db() as db:
        db.execute("UPDATE operations SET status='STOPPED' WHERE id=?", (op_id,))

# Hintergrund-Thread Funktion für Auto-Sync
def auto_mode_worker():
    import time
    while True:
        time.sleep(10)  # alle 10 Sekunden prüfen
        if auto_enabled:
            sync_disks()
