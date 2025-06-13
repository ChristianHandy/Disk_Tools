#!/usr/bin/env python3
"""app.py – Vollständige Flask-Web-Oberfläche zur Festplattenverwaltung"""

import os
import json
import csv
import subprocess
import sqlite3
import threading
import re
from datetime import datetime
from pathlib import Path
from flask import (Flask, render_template_string, request, redirect,
                   url_for, flash, jsonify, send_file)

app = Flask(__name__)
app.secret_key = 'CHANGE_ME'
DB_FILE = Path(__file__).with_suffix('.db')
UPLOAD_DIR = Path(__file__).parent / 'uploads'
UPLOAD_DIR.mkdir(exist_ok=True)

auto_enabled = False  # Toggles automatische Formatierung/SMART
AUTO_SKIP_DEVICE = 'mmcblk0'  # Beispiel-Systemlaufwerk, überspringen

# --- Datenbank ---
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as db:
        db.executescript('''
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
''')
        cols = {c[1] for c in db.execute("PRAGMA table_info(disks)")}
        if 'serial' not in cols:
            db.execute("ALTER TABLE disks ADD COLUMN serial TEXT")

# --- Systembefehle ---
def run(cmd):
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return res.stdout

# --- Festplatten-Synchronisation ---
def ls_disks():
    data = json.loads(run(['lsblk', '-J', '-d', '-o', 'NAME,SIZE,MODEL,TYPE']))
    return [d for d in data.get('blockdevices', []) if d.get('type') == 'disk']


def get_serial(dev):
    try:
        info = run(['smartctl', '-i', f'/dev/{dev}'])
        for l in info.splitlines():
            if 'Serial Number' in l:
                return l.split(':', 1)[1].strip()
    except:
        pass
    return None


def sync_disks():
    global auto_enabled
    now = datetime.utcnow().isoformat()
    new_devices = []
    with get_db() as db:
        db.execute('UPDATE disks SET present=0')
        for d in ls_disks():
            serial = get_serial(d['name'])
            db.execute(
                'INSERT OR REPLACE INTO disks(device,serial,model,size,present,first_seen) '
                'VALUES (?,?,?,?,1,COALESCE((SELECT first_seen FROM disks WHERE device=?),CURRENT_TIMESTAMP))',
                (d['name'], serial, d['model'], d['size'], d['name'])
            )
        rows = db.execute('SELECT device, first_seen FROM disks WHERE first_seen >= ?', (now,)).fetchall()
        for r in rows:
            if r['device'] == AUTO_SKIP_DEVICE or r['device'].startswith('nvme'):
                continue
            new_devices.append(r['device'])
    if auto_enabled:
        for dev in new_devices:
            start_format(dev, 'ext4')
            start_smart(dev, 'short')

# --- Operationen loggen ---
def log_op(device, action):
    with get_db() as db:
        cur = db.execute(
            'INSERT INTO operations(device,action,status,progress) VALUES (?,?,?,0)',
            (device, action, 'RUNNING')
        )
        return cur.lastrowid


def update_op(op_id, status=None, progress=None):
    sets, vals = [], []
    if status:
        sets.append('status=?'); vals.append(status)
    if progress is not None:
        sets.append('progress=?'); vals.append(progress)
    if sets:
        vals.append(op_id)
        with get_db() as db:
            db.execute(f"UPDATE operations SET {','.join(sets)} WHERE id=?", vals)

# --- Formatierung ---
def format_worker(device, fs, op_id):
    path = f'/dev/{device}'
    try:
        run(['wipefs', '-a', path])
        cmdmap = {'ext4': ['mkfs.ext4', '-F'], 'xfs': ['mkfs.xfs', '-f'], 'fat32': ['mkfs.vfat', '-F', '32']}
        run(cmdmap[fs] + [path])
        update_op(op_id, 'OK', 100)
    except:
        update_op(op_id, 'FAIL', 0)


def start_format(device, fs):
    op = log_op(device, f'FORMAT_{fs}')
    threading.Thread(target=format_worker, args=(device, fs, op), daemon=True).start()
    return op

# --- SMART Tests ---
def start_smart(device, mode):
    run(['smartctl', '-t', mode, f'/dev/{device}'])
    log_op(device, f'SMART_{mode.upper()}')


def view_smart(device):
    out = run(['smartctl', '-a', f'/dev/{device}'])
    m = re.search(r'Temperature_Celsius.*\s(\d+)', out)
    temp = int(m.group(1)) if m else None
    health = 'BAD' if 'FAILING_NOW' in out else 'GOOD'
    with get_db() as db:
        db.execute('INSERT INTO smart_history(device,serial,temp,health) VALUES (?,?,?,?)',
                   (device, None, temp, health))
    return out

# --- Validator ---
def validate_blocks(device):
    size = int(run(['blockdev', '--getsize64', f'/dev/{device}']).strip())
    cnt = size // 4096
    blocks = list(range(min(cnt, 256)))
    bad = []
    for b in blocks:
        try:
            run(['badblocks', '-b', '4096', '-o', str(b), f'/dev/{device}'])
        except:
            bad.append(b)
    return blocks, bad

# --- Templates ---
INDEX_TPL = '''<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Festplattenverwaltung</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<nav class="navbar navbar-expand-lg navbar-dark bg-dark">
  <div class="container-fluid">
    <a class="navbar-brand" href="{{ url_for('index') }}">DiskTool</a>
    <a class="btn btn-sm btn-secondary ms-3" href="{{ url_for('toggle_auto') }}">Auto: {{ 'AN' if auto else 'AUS' }}</a>
  </div>
</nav>
<div class="container py-4">
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <script>
        alert("{{ messages[0] }}");
      </script>
    {% endif %}
  {% endwith %}
  <h1 class="mb-4">Festplattenübersicht</h1>
  <form class="row mb-4" method="get">
    <div class="col-md-4">
      <input name="q" class="form-control" placeholder="Suche Gerät/Modell" value="{{ request.args.get('q','') }}">
    </div>
    <div class="col-auto">
      <button class="btn btn-primary">Suchen</button>
      <a href="{{ url_for('index') }}" class="btn btn-secondary">Reset</a>
    </div>
  </form>
  <table class="table table-hover table-striped">
    <thead class="table-dark">
      <tr><th>Gerät</th><th>Modell</th><th>Größe</th><th>Aktionen</th></tr>
    </thead>
    <tbody>
    {% for d in disks %}
      <tr>
        <td>{{ d.device }}</td>
        <td>{{ d.model }}</td>
        <td>{{ d.size }}</td>
        <td>
          <a class="btn btn-sm btn-outline-primary" href="{{ url_for('smart_start_route', device=d.device, mode='short') }}">SMART Test</a>
          <a class="btn btn-sm btn-info" href="{{ url_for('smart_view_route', device=d.device) }}">SMART</a>
          <a class="btn btn-sm btn-warning" href="{{ url_for('validate_route', device=d.device) }}">Validate</a>
          <a class="btn btn-sm btn-danger" href="{{ url_for('format_route', device=d.device) }}">Format</a>
        </td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  <div class="mt-4">
    <a href="{{ url_for('history') }}" class="btn btn-secondary">Historie</a>
    <a href="{{ url_for('dashboard') }}" class="btn btn-secondary">Dashboard</a>
    <a href="{{ url_for('export_smart') }}" class="btn btn-secondary">Export SMART</a>
    <a href="{{ url_for('import_smart') }}" class="btn btn-secondary">Import SMART</a>
  </div>
</div>
</body>
</html>'''

FORMAT_TPL = '''<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Formatieren {{ device }}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<nav class="navbar navbar-expand-lg navbar-dark bg-dark">
  <div class="container-fluid">
    <a class="navbar-brand" href="{{ url_for('index') }}">DiskTool</a>
  </div>
</nav>
<div class="container py-4">
  <h1 class="mb-4">Formatieren /dev/{{ device }}</h1>
  <form method="post" class="row g-3">
    <div class="col-auto">
      <select name="fs" class="form-select">
        <option value="ext4">ext4</option>
        <option value="xfs">xfs</option>
        <option value="fat32">fat32</option>
      </select>
    </div>
    <div class="col-auto">
      <button class="btn btn-danger">Start</button>
    </div>
  </form>
</div>
</body>
</html>'''

SMART_VIEW_TPL = '''<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>SMART Report {{ device }}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<nav class="navbar navbar-expand-lg navbar-dark bg-dark">
  <div class="container-fluid">
    <a class="navbar-brand" href="{{ url_for('index') }}">DiskTool</a>
  </div>
</nav>
<div class="container py-4">
  <h2 class="mb-4">SMART Report für {{ device }}</h2>
  <pre>{{ report }}</pre>
</div>
</body>
</html>'''

VALIDATE_TPL = '''<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Validate {{ device }}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>.block{width:8px;height:8px;display:inline-block;margin:1px}.good{background:green}.bad{background:red}</style>
</head>
<body class="bg-light">
<nav class="navbar navbar-expand-lg navbar-dark bg-dark">
  <div class="container-fluid">
    <a class="navbar-brand" href="{{ url_for('index') }}">DiskTool</a>
  </div>
</nav>
<div class="container py-4">
  <h2 class="mb-4">Validate für {{ device }}</h2>
  <div>
    {% for b in blocks %}
      <div class="block {% if b in bad_blocks %}bad{% else %}good{% endif %}"></div>
      {% if loop.index % 50 == 0 %}<br>{% endif %}
    {% endfor %}
  </div>
</div>
</body>
</html>'''

HISTORY_TPL = '''<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Historie</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<nav class="navbar navbar-expand-lg navbar-dark bg-dark">
  <div class="container-fluid">
    <a class="navbar-brand" href="{{ url_for('index') }}">DiskTool</a>
    <a class="btn btn-sm btn-danger ms-3" href="{{ url_for('clear_history') }}">Leere Historie</a>
  </div>
</nav>
<div class="container py-4">
  <h1 class="mb-4">Historie</h1>
  <h2>Operationen</h2>
  <table class="table table-striped">
    <thead>
      <tr><th>ID</th><th>Gerät</th><th>Aktion</th><th>Status</th><th>Fortschritt</th><th>Zeit</th><th>Stop</th><th>Status</th></tr>
    </thead>
    <tbody>
      {% for o in ops %}
      <tr>
        <td>{{ o.id }}</td>
        <td>{{ o.device }}</td>
        <td>{{ o.action }}</td>
        <td>{{ o.status }}</td>
        <td>{{ o.progress }}%</td>
        <td>{{ o.ts }}</td>
        <td>{% if o.status=='RUNNING' %}<a class="btn btn-sm btn-danger" href="{{ url_for('stop_task', op_id=o.id) }}">Stop</a>{% endif %}</td>
        <td>{% if o.status=='RUNNING' %}<a class="btn btn-sm btn-primary" href="{{ url_for('task_status', op_id=o.id) }}">Status</a>{% endif %}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  <h2 class="mt-4">SMART-Verlauf</h2>
  <table class="table table-striped">
    <thead><tr><th>ID</th><th>Gerät</th><th>Temp</th><th>Health</th><th>Zeit</th></tr></thead>
    <tbody>
      {% for s in smart %}
      <tr><td>{{ s.id }}</td><td>{{ s.device }}</td><td>{{ s.temp }}</td><td>{{ s.health }}</td><td>{{ s.ts }}</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
</body>
</html>'''

DASHBOARD_TPL = '''<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Dashboard</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<nav class="navbar navbar-expand-lg navbar-dark bg-dark">
  <div class="container-fluid">
    <a class="navbar-brand" href="{{ url_for('index') }}">DiskTool</a>
  </div>
</nav>
<div class="container py-4">
  <h1>Dashboard</h1>
  <div class="row mb-4">
    <div class="col"><div class="card p-3"><h3>Gesamtplatten</h3><p>{{ total }}</p></div></div>
    <div class="col"><div class="card p-3"><h3>Schlechte Platten</h3><p>{{ bad }}</p></div></div>
    <div class="col"><div class="card p-3"><h3>Laufende Tasks</h3><p>{{ running }}</p></div></div>
  </div>
  <h2>Uptime der Festplatten</h2>
  <table class="table table-striped"><thead><tr><th>Gerät</th><th>Laufzeit</th></tr></thead><tbody>
    {% for r in runtimes %}
    <tr><td>{{ r.device }}</td><td>{{ r.runtime }}</td></tr>
    {% endfor %}
  </tbody></table>
</div>
</body>
</html>'''

IMPORT_TPL = '''<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Import SMART</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<nav class="navbar navbar-expand-lg navbar-dark bg-dark">
  <div class="container-fluid">
    <a class="navbar-brand" href="{{ url_for('index') }}">DiskTool</a>
  </div>
</nav>
<div class="container py-4">
  <h1 class="mb-4">Import SMART Report</h1>
  <form method="post" enctype="multipart/form-data"><input class="form-control mb-3" type="file" name="file" accept=".txt,.log"><button class="btn btn-primary">Upload</button></form>
</div>
</body>
</html>'''

TASK_STATUS_TPL = '''<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Task Status {{ op_id }}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <script>
    async function poll() {
      const r = await fetch('{{ url_for("task_status_api", op_id=op_id) }}'); const d = await r.json();
      const bar = document.getElementById('bar'); bar.style.width = d.progress + '%'; bar.innerText = d.progress + '%';
      document.getElementById('status').innerText = 'Status: ' + d.status;
      if (d.status === 'RUNNING') setTimeout(poll, 1000);
    }
    window.addEventListener('load', poll);
  </script>
</head>
<body class="bg-light">
<div class="container py-4">
  <h2>Task {{ op_id }}: {{ action }}</h2>
  <div class="progress" style="height:30px"><div id="bar" class="progress-bar" style="width:0%">0%</div></div>
  <p id="status" class="mt-2">Status: RUNNING</p>
  <a href="{{ url_for('history') }}" class="btn btn-secondary mt-3">Zurück</a>
</div>
</body>
</html>'''

# --- Routen ---
@app.route('/')
def index():
    sync_disks()
    q = request.args.get('q','')
    with get_db() as db:
        if q:
            disks = db.execute('SELECT * FROM disks WHERE present=1 AND (device LIKE ? OR model LIKE ?)',
                                (f'%{q}%', f'%{q}%')).fetchall()
        else:
            disks = db.execute('SELECT * FROM disks WHERE present=1').fetchall()
    return render_template_string(INDEX_TPL, disks=disks, auto=auto_enabled)

@app.route('/toggle_auto')
def toggle_auto():
    global auto_enabled
    auto_enabled = not auto_enabled
    flash(f"Automatik {'AN' if auto_enabled else 'AUS'}")
    return redirect(url_for('index'))

@app.route('/format/<device>', methods=['GET','POST'])
def format_route(device):
    if request.method == 'POST':
        fs = request.form.get('fs','ext4')
        op_id = start_format(device, fs)
        flash(f'Format-Task {op_id} gestartet für {device}')
        return redirect(url_for('task_status', op_id=op_id))
    return render_template_string(FORMAT_TPL, device=device)

@app.route('/smart/start/<device>/<mode>')
def smart_start_route(device, mode):
    if mode not in {'short','long'}:
        flash('Ungültiger SMART-Typ')
        return redirect(url_for('index'))
    start_smart(device, mode)
    flash(f'SMART {mode} gestartet für {device}')
    return redirect(url_for('index'))

@app.route('/smart/view/<device>')
def smart_view_route(device):
    report = view_smart(device)
    return render_template_string(SMART_VIEW_TPL, device=device, report=report)

@app.route('/validate/<device>')
def validate_route(device):
    blocks, bad = validate_blocks(device)
    return render_template_string(VALIDATE_TPL, device=device, blocks=blocks, bad_blocks=bad)

@app.route('/history')
def history():
    with get_db() as db:
        ops = db.execute('SELECT * FROM operations ORDER BY ts DESC').fetchall()
        smart = db.execute('SELECT * FROM smart_history ORDER BY ts DESC').fetchall()
    return render_template_string(HISTORY_TPL, ops=ops, smart=smart)

@app.route('/clear_history')
def clear_history():
    with get_db() as db:
        db.execute('DELETE FROM operations')
        db.execute('DELETE FROM smart_history')
    flash('Historie geleert')
    return redirect(url_for('history'))

@app.route('/dashboard')
def dashboard():
    with get_db() as db:
        total = db.execute('SELECT COUNT(*) FROM disks').fetchone()[0]
        bad = db.execute("SELECT COUNT(*) FROM smart_history WHERE health='BAD'").fetchone()[0]
        running = db.execute("SELECT COUNT(*) FROM operations WHERE status='RUNNING'").fetchone()[0]
        # Laufzeitberechnung fiktiv
        runtimes = []
        for row in db.execute('SELECT device, MIN(ts) AS first_ts FROM operations GROUP BY device').fetchall():
            runtimes.append({'device': row['device'], 'runtime': 'n/a'})
    return render_template_string(DASHBOARD_TPL, total=total, bad=bad, running=running, runtimes=runtimes)

@app.route('/export-smart')
def export_smart():
    path = UPLOAD_DIR / 'smart.csv'
    with get_db() as db, open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['id','device','serial','temp','health','ts'])
        for row in db.execute('SELECT * FROM smart_history').fetchall():
            writer.writerow(row)
    return send_file(path, as_attachment=True)

@app.route('/import-smart', methods=['GET','POST'])
def import_smart():
    if request.method == 'POST':
        f = request.files['file']
        p = UPLOAD_DIR / f.filename
        f.save(p)
        txt = p.read_text()
        m = re.search(r'Temperature_Celsius.*\s(\d+)', txt)
        temp = int(m.group(1)) if m else None
        health = 'BAD' if 'FAILING_NOW' in txt else 'GOOD'
        device = request.form.get('device', 'UNKNOWN')
        with get_db() as db:
            db.execute('INSERT INTO smart_history(device,serial,temp,health) VALUES (?,?,?,?)',
                       (device, None, temp, health))
        flash('SMART importiert')
        return redirect(url_for('history'))
    return render_template_string(IMPORT_TPL)

@app.route('/task/status/api/<int:op_id>')
def task_status_api(op_id):
    row = get_db().execute('SELECT status,progress FROM operations WHERE id=?', (op_id,)).fetchone()
    return jsonify(status=row['status'], progress=row['progress'])

@app.route('/task/status/<int:op_id>')
def task_status(op_id):
    row = get_db().execute('SELECT action FROM operations WHERE id=?', (op_id,)).fetchone()
    return render_template_string(TASK_STATUS_TPL, op_id=op_id, action=row['action'])

@app.route('/task/stop/<int:op_id>')
def stop_task(op_id):
    with get_db() as db:
        db.execute("UPDATE operations SET status='STOPPED' WHERE id=?", (op_id,))
    flash(f'Task {op_id} gestoppt')
    return redirect(url_for('history'))

def auto_mode_worker():
    import time
    while True:
        time.sleep(10)
        if auto_enabled:
            sync_disks()

if __name__ == '__main__':
    # Starte den Auto-Mode-Worker im Hintergrund
    threading.Thread(target=auto_mode_worker, daemon=True).start()
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)


