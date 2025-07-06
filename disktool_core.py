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
        """)
        # falls später Spalten hinzugefügt wurden:
        cols = {c[1] for c in db.execute("PRAGMA table_info(disks)")}
        if 'serial' not in cols:
            db.execute("ALTER TABLE disks ADD COLUMN serial TEXT")

def run(cmd):
    """Führt einen Shell-Befehl aus und gibt den gesamten Output zurück."""
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return res.stdout

# --- Festplatten-Funktionen ---
def ls_disks():
    """Liest alle physischen Disks mit lsblk aus und gibt eine Liste von Devices zurück."""
    output = run(['lsblk', '-J', '-d', '-o', 'NAME,SIZE,MODEL,TYPE'])
    data = json.loads(output)
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
    now = datetime.utcnow().isoformat()
    new_devices = []
    with get_db() as db:
        db.execute('UPDATE disks SET present = 0')
        for d in ls_disks():
            serial = get_serial(d['name'])
            db.execute(
                '''INSERT OR REPLACE INTO disks(device, serial, model, size, present, first_seen)
                   VALUES (?, ?, ?, ?, 1,
                           COALESCE((SELECT first_seen FROM disks WHERE device=?), CURRENT_TIMESTAMP))''',
                (d['name'], serial, d['model'], d['size'], d['name'])
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
    """Prüft die ersten Blöcke eines Geräts mit badblocks und markiert fehlerhafte Blöcke."""
    size = int(run(['blockdev', '--getsize64', f'/dev/{device}']).strip())
    count = size // 4096
    blocks = list(range(min(count, 256)))
    bad_blocks = []
    for b in blocks:
        try:
            run(['badblocks', '-b', '4096', '-o', str(b), f'/dev/{device}'])
        except Exception:
            bad_blocks.append(b)
    return blocks, bad_blocks

# --- Hilfsfunktionen für UI/DB-Abfragen (für Flask-Routen) ---
def get_disk_list(filter_str=''):
    """Gibt die Liste der aktuellen Disks aus der DB zurück, optional gefiltert nach Device/Modell."""
    with get_db() as db:
        if filter_str:
            pattern = f"%{filter_str}%"
            disks = db.execute("SELECT * FROM disks WHERE present=1 AND (device LIKE ? OR model LIKE ?)",
                               (pattern, pattern)).fetchall()
        else:
            disks = db.execute("SELECT * FROM disks WHERE present=1").fetchall()
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
        # Beispiel: Laufzeit pro Gerät (hier als 'n/a' mangels echter Messung)
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
    # Datei speichern
    filename = file_storage.filename or "smart_upload.txt"
    path = UPLOAD_DIR / filename
    file_storage.save(path)
    text = path.read_text()
    # Werte extrahieren (Temperatur & Health)
    m = re.search(r'Temperature_Celsius.*\s(\d+)', text)
    temp = int(m.group(1)) if m else None
    health = 'BAD' if 'FAILING_NOW' in text else 'GOOD'
    # In DB eintragen
    with get_db() as db:
        db.execute("INSERT INTO smart_history(device, serial, temp, health) VALUES (?, ?, ?, ?)",
                   (device, None, temp, health))

def get_task_status(op_id):
    """Liest Status und Fortschritt eines Tasks aus der DB (für API-Ausgabe)."""
    row = get_db().execute("SELECT status, progress FROM operations WHERE id=?", (op_id,)).fetchone()
    return (row['status'], row['progress']) if row else (None, None)

def get_task_action(op_id):
    """Liefert den Aktionstyp eines Tasks (z.B. 'FORMAT_ext4') zur Anzeige."""
    row = get_db().execute("SELECT action FROM operations WHERE id=?", (op_id,)).fetchone()
    return row['action'] if row else None

def stop_task(op_id):
    """Markiert einen laufenden Task als gestoppt."""
    with get_db() as db:
        db.execute("UPDATE operations SET status='STOPPED' WHERE id=?", (op_id,))

# Hintergrund-Thread Funktion für Auto-Sync
def auto_mode_worker():
    import time
    while True:
        time.sleep(10)  # alle 10 Sekunden prüfen
        if auto_enabled:
            sync_disks()
