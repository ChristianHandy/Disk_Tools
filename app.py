#!/usr/bin/env python3
"""app.py – Flask‑basierte Festplattenverwaltung

Features
========
* Übersicht aller angeschlossenen Platten (rote Zeile, wenn SMART‑Status = BAD)
* Suche nach Seriennummer (zeigt auch nicht angeschlossene Platten)
* Hersteller (vendor) & frei editierbarer Aufbewahrungsort (location)
* SMART‑Refresh & **SMART‑Import** (Report‑Upload → Zuordnung über Seriennummer)
* Asynchrone Bad‑Blocks‑Validator‑ und Format‑Jobs (mit Fortschritt & Zeitstempel)
"""

import os, json, subprocess, sqlite3, sys, threading, re, datetime as dt, datetime as dt
from pathlib import Path
from flask import Flask, render_template_string, request, redirect, url_for, flash
from werkzeug.utils import secure_filename

DB_FILE = Path(__file__).with_suffix('.db')
UPLOAD_DIR = Path(__file__).with_name('uploads'); UPLOAD_DIR.mkdir(exist_ok=True)
app = Flask(__name__); app.secret_key = 'CHANGE_ME'
ALLOWED_EXT = {'txt','log'}

# --------------------------- Utils ----------------------------------------

def run(cmd):
    res = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if res.returncode:
        raise RuntimeError(res.stdout)
    return res.stdout


def get_db():
    conn = sqlite3.connect(DB_FILE); conn.row_factory = sqlite3.Row; return conn


def ensure_column(table, col, ddl):
    with get_db() as db:
        cols = {c[1] for c in db.execute(f'PRAGMA table_info({table})')}
        if col not in cols:
            db.execute(f'ALTER TABLE {table} ADD COLUMN {ddl}')


def init_db():
    with get_db() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS disks(
          device TEXT PRIMARY KEY,
          serial TEXT,
          model  TEXT,
          size   TEXT,
          present INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS operations(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          device TEXT,
          action TEXT,
          status TEXT,
          progress INTEGER DEFAULT 0,
          output TEXT,
          ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );''')
    for c,d in [
        ('vendor','TEXT'),('location','TEXT'),('smart_status','TEXT'),('last_smart','TEXT'),('last_smart_ts','TIMESTAMP'),
        ('last_format_ts','TIMESTAMP'),('last_validate_ts','TIMESTAMP'),('validate_status','TEXT')]:
        ensure_column('disks',c,f"{c} {d}")

# --------------------------- Disk sync ------------------------------------

def ls_disks():
    data=json.loads(run(['lsblk','-J','-d','-o','NAME,SIZE,MODEL,TYPE']))
    return [d for d in data['blockdevices'] if d['type']=='disk']


def vendor_from_model(model):
    return model.split()[0] if model else None


def smart_serial(dev):
    try:
        for l in run(['smartctl','-i',f'/dev/{dev}']).splitlines():
            if 'Serial Number' in l:
                return l.split(':',1)[1].strip()
    except Exception:
        pass
    return None


def sync_disks():
    with get_db() as db:
        db.execute('UPDATE disks SET present=0')
        for d in ls_disks():
            s=smart_serial(d['name']); v=vendor_from_model(d['model'])
            db.execute('INSERT OR IGNORE INTO disks(device,serial,model,size,vendor) VALUES (?,?,?,?,?)', (d['name'],s,d['model'],d['size'],v))
            db.execute('UPDATE disks SET model=?,size=?,vendor=?,present=1 WHERE device=?', (d['model'],d['size'],v,d['name']))

# --------------------------- SMART helpers --------------------------------

def smart_health(text):
    t=text.upper(); return 'BAD' if 'FAILING_NOW' in t or 'SELF-ASSESSMENT: FAILED' in t else 'GOOD'


def refresh_smart(dev):
    rep=run(['smartctl','-a',f'/dev/{dev}']); status=smart_health(rep)
    with get_db() as db:
        db.execute('UPDATE disks SET smart_status=?,last_smart=?,last_smart_ts=CURRENT_TIMESTAMP WHERE device=?',(status,rep,dev))


def import_smart(text):
    serial=None; model=None
    for l in text.splitlines():
        if 'Serial Number' in l:
            serial=l.split(':',1)[1].strip()
        if 'Device Model' in l or 'Product' in l:
            model=l.split(':',1)[1].strip()
    if not serial:
        return False,'Serial not found'
    with get_db() as db:
        row=db.execute('SELECT device FROM disks WHERE serial=?',(serial,)).fetchone()
        if row:
            status=smart_health(text)
            db.execute('UPDATE disks SET smart_status=?,last_smart=?,last_smart_ts=CURRENT_TIMESTAMP,model=COALESCE(model,?),vendor=COALESCE(vendor,?) WHERE serial=?',
                       (status,text,model,vendor_from_model(model),serial))
            return True,f'Updated {row["device"]}'
        else:
            db.execute('INSERT INTO disks(device,serial,model,size,vendor,present,last_smart,last_smart_ts,smart_status) VALUES (?,?,?,?,?,?,?,?,?)',
                       (serial,serial,model,'?',vendor_from_model(model),0,text,dt.datetime.utcnow(),'GOOD'))
            return True,'Inserted new disk record'

# --------------------------- Async Jobs -----------------------------------

def log_op(device,action):
    with get_db() as db:
        cur=db.execute('INSERT INTO operations(device,action,status) VALUES (?,?,?)',(device,action,'RUNNING'))
        return cur.lastrowid

def upd_op(op,**k):
    sets,vals=[],[]
    for col in ['progress','status','output']:
        if col in k and k[col] is not None:
            sets.append(f'{col}=?'); vals.append(k[col])
    if sets:
        vals.append(op)
        with get_db() as db:
            db.execute(f'UPDATE operations SET {", ".join(sets)} WHERE id=?',vals)


def validator_worker(dev,op):
    proc=subprocess.Popen(['badblocks','-sv','-b','4096',f'/dev/{dev}'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    regex=re.compile(r'(\d+\.\d+)%'); res='OK'
    for l in proc.stdout:
        if m:=regex.search(l): upd_op(op,progress=int(float(m.group(1))))
        if 'bad blocks' in l and '%' in l:
            res='FAIL' if int(l.split()[4]) else 'OK'
    upd_op(op,progress=100,status='DONE')
    with get_db() as db:
        db.execute('UPDATE disks SET last_validate_ts=CURRENT_TIMESTAMP,validate_status=? WHERE device=?',(res,dev))


def start_validator(dev):
    op=log_op(dev,'VALIDATE'); threading.Thread(target=validator_worker,args=(dev,op),daemon=True).start()


def quick_format(dev,fs):
    run({'ext4':['mkfs.ext4','-F'],'xfs':['mkfs.xfs','-f'],'fat32':['mkfs.vfat','-F','32']}[fs]+[dev])


def format_worker(dev,fs):
    op=log_op(dev,f'FORMAT_{fs}')
    try:
        run(['wipefs','-a',dev]); quick_format(dev,fs)
        upd_op(op,progress=100,status='OK')
        with get_db() as db:
            db.execute('UPDATE disks SET last_format_ts=CURRENT_TIMESTAMP WHERE device=?',(dev,))
    except Exception as e:
        upd_op(op,status='FAIL',output=str(e))


def start_format(dev,fs):
    threading.Thread(target=format_worker,args=(f'/dev/{dev}',fs),daemon=True).start()

# --------------------------- Templates ------------------------------------
SMART_VIEW_TPL="""<pre>{{ smart }}</pre>"""

# simple forms already defined earlier (reuse FORMAT_TPL, LOC_TPL). Add IMPORT form.
IMPORT_TPL="""<!doctype html><html><head><meta charset=utf-8><title>Import SMART</title><link href=https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css rel=stylesheet></head><body class=container py-4><h2>SMART Report importieren</h2><form method=post enctype=multipart/form-data><input type=file name=report class=form-control mb-2 accept=.log,.txt><button class='btn btn-primary'>Upload</button></form></body></html>"""

# index template simpler using f-string later
INDEX_HEAD="""<!doctype html><html><head><meta charset=utf-8><title>Disks</title><link href=https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css rel=stylesheet></head><body class=container py-4>"""
INDEX_TAIL="""</body></html>"""

# --------------------------- Routes --------------------------------------
@app.route('/')
def index():
    sync_disks(); q=request.args.get('q','').lower()
    with get_db() as db:
        rows=db.execute('SELECT * FROM disks WHERE (present=1 OR ?!="") AND serial LIKE ?', (q,'%'+q+'%')).fetchall()
    rows_html=''.join([f"<tr class={'table-danger' if r['smart_status']=='BAD' else ''}><td>{r['device']}</td><td>{r['size']}</td><td>{r['vendor'] or '-'}</td><td>{r['model']}</td><td>{r['serial'] or '-'}</td><td>{r['location'] or '-'} <a href='/location/{r['device']}' class='ms-1 small'>✎</a></td><td>{r['smart_status'] or '-'}</td><td><a class='btn btn-sm btn-info' href='/smart/view/{r['device']}'>View</a> <a class='btn btn-sm btn-outline-info' href='/smart/refresh/{r['device']}'>Refresh</a> <a class='btn btn-sm btn-warning' href='/validator/start/{r['device']}'>Validate</a> <a class='btn btn-sm btn-danger' href='/format/{r['device']}'>Format</a></td></tr>" for r in rows])
    body=f"{INDEX_HEAD}<h1>Festplatten</h1><form class='row mb-3' method=get><div class='col-auto'><input name=q value='{q}' class='form-control' placeholder='Seriennr'></div><div class='col-auto'><button class='btn btn-primary'>Suchen</button></div><div class='col-auto'><a href='/smart/import' class='btn btn-secondary'>SMART Import</a></div></form><table class='table table-sm table-striped'><thead><tr><th>Name</th><th>Größe</th><th>Hersteller</th><th>Modell</th><th>Serial</th><th>Ort</th><th>SMART</th><th></th></tr></thead><tbody>{rows_html}</tbody></table>{INDEX_TAIL}"
    return body

@app.route('/smart/import',methods=['GET','POST'])
def smart_import():
    if request.method=='POST':
        file=request.files.get('report')
        if not file or file.filename=='' or file.filename.split('.')[-1].lower() not in ALLOWED_EXT:
            flash('Keine Datei'); return redirect(url_for('smart_import'))
        path=UPLOAD_DIR/secure_filename(file.filename)
        file.save(path)
        ok,msg=import_smart(path.read_text())
        flash(msg)
        return redirect(url_for('index'))
    return IMPORT_TPL

@app.route('/smart/refresh/<device>')
def smart_refresh(device):
    refresh_smart(device); return redirect(url_for('index'))

@app.route('/smart/view/<device>')
def smart_view(device):
    with get_db() as db: row=db.execute('SELECT last_smart FROM disks WHERE device=?',(device,)).fetchone()
    return render_template_string(SMART_VIEW_TPL,smart=row['last_smart'] or 'No data')

@app.route('/validator/start/<device>')
def validator_start(device):
    start_validator(device); return redirect(url_for('index'))

@app.route('/format/<device>',methods=['GET','POST'])
def format_form(device):
    if request.method=='POST':
        if request.form.get('confirm')!='YES': flash('Bestätigung fehlt'); return redirect(url_for('format_form',device=device))
        start_format(device,request.form.get('fs','ext4')); return redirect(url_for('index'))
    return FORMAT_TPL.replace('{{device}}',device)

@app.route('/location/<device>',methods=['GET','POST'])
def set_location(device):
    with get_db() as db:
        if request.method=='POST':
            db.execute('UPDATE disks SET location=? WHERE device=?',(request.form.get('loc','').strip(),device)); return redirect(url_for('index'))
        row=db.execute('SELECT location FROM disks WHERE device=?',(device,)).fetchone()
    return LOC_TPL.replace('{{device}}',device).replace('{{loc or ""}}',row['location'] or '')

# --------------------------- Main ----------------------------------------
if __name__=='__main__':
    init_db(); app.run(debug=True)

