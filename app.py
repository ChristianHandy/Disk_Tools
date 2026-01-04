from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, after_this_request, session
import disktool_core
from addon_loader import AddonManager
import os
import threading
from functools import wraps

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = 'CHANGE_ME'
# Admin credentials (change in production)
app.config['ADMIN_USER'] = 'admin'
app.config['ADMIN_PASS'] = 'CHANGE_ME'

# Addon-System laden
addon_mgr = AddonManager(app, disktool_core)
app.addon_mgr = addon_mgr
addon_mgr.load_addons()

# Template-Funktion für HTML-Erweiterungen
@app.context_processor
def inject_hooks():
    return dict(hook=lambda name, *args, **kwargs: addon_mgr.render_hooks(name, *args, **kwargs))

# simple login_required decorator
def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return wrapped

@app.route('/login', methods=['GET', 'POST'])
def login():
    next_url = request.args.get('next') or url_for('index')
    if request.method == 'POST':
        user = request.form.get('username')
        pwd = request.form.get('password')
        if user == app.config['ADMIN_USER'] and pwd == app.config['ADMIN_PASS']:
            session['logged_in'] = True
            flash('Eingeloggt')
            return redirect(next_url)
        else:
            flash('Ungültige Zugangsdaten')
    return render_template('login.html', next=next_url)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('Ausgeloggt')
    return redirect(url_for('index'))

@app.route('/')
def index():
    disktool_core.sync_disks()
    q = request.args.get('q','')
    disks = disktool_core.get_disk_list(q)
    return render_template('index.html', disks=disks, auto=disktool_core.auto_enabled)

@app.route('/toggle_auto')
@login_required
def toggle_auto():
    disktool_core.auto_enabled = not disktool_core.auto_enabled
    flash(f"Automatik {'AN' if disktool_core.auto_enabled else 'AUS'}")
    return redirect(url_for('index'))

@app.route('/format/<device>', methods=['GET','POST'])
@login_required
def format_route(device):
    if request.method == 'POST':
        fs = request.form.get('fs','ext4')
        op_id = disktool_core.start_format(device, fs)
        flash(f'Format-Task {op_id} gestartet für {device}')
        return redirect(url_for('task_status', op_id=op_id))
    return render_template('format.html', device=device)

@app.route('/smart/start/<device>/<mode>')
@login_required
def smart_start_route(device, mode):
    if mode not in {'short','long'}:
        flash('Ungültiger SMART-Typ')
        return redirect(url_for('index'))
    disktool_core.start_smart(device, mode)
    flash(f'SMART {mode} gestartet für {device}')
    return redirect(url_for('index'))

@app.route('/smart/view/<device>')
def smart_view_route(device):
    report = disktool_core.view_smart(device)
    return render_template('smart_view.html', device=device, report=report)

@app.route('/validate/<device>')
def validate_route(device):
    blocks, bad = disktool_core.validate_blocks(device)
    return render_template('validate.html', device=device, blocks=blocks, bad_blocks=bad)

@app.route('/history')
def history():
    ops, smart = disktool_core.fetch_history_data()
    return render_template('history.html', ops=ops, smart=smart)

@app.route('/clear_history')
@login_required
def clear_history():
    disktool_core.clear_history()
    flash('Historie geleert')
    return redirect(url_for('history'))

@app.route('/dashboard')
def dashboard():
    stats = disktool_core.get_dashboard_data()
    return render_template('dashboard.html', **stats)

@app.route('/export-smart')
def export_smart():
    csv_path = disktool_core.export_smart_data()
    return send_file(csv_path, as_attachment=True)

@app.route('/import-smart', methods=['GET','POST'])
@login_required
def import_smart():
    if request.method == 'POST':
        f = request.files['file']
        device = request.form.get('device', 'UNKNOWN')
        disktool_core.import_smart_data(f, device)
        flash('SMART-Daten importiert')
        return redirect(url_for('history'))
    return render_template('import.html')

@app.route('/task/status/api/<int:op_id>')
def task_status_api(op_id):
    status, progress = disktool_core.get_task_status(op_id)
    return jsonify(status=status, progress=progress)

@app.route('/task/status/<int:op_id>')
def task_status(op_id):
    action = disktool_core.get_task_action(op_id)
    return render_template('task_status.html', op_id=op_id, action=action)

@app.route('/task/stop/<int:op_id>')
@login_required
def stop_task(op_id):
    disktool_core.stop_task(op_id)
    flash(f'Task {op_id} gestoppt')
    return redirect(url_for('history'))

@app.route('/addons/<plugin>/<device>')
def render_plugin_page(plugin, device):
    return render_template(f'addons/{plugin}.html', device=device)

# Simple remote management UI
@app.route('/remotes', methods=['GET', 'POST'])
@login_required
def remotes():
    if request.method == 'POST':
        name = request.form.get('name')
        host = request.form.get('host')
        port = int(request.form.get('port', 22))
        disktool_core.add_remote(name, host, port)
        flash('Remote hinzugefügt')
        return redirect(url_for('remotes'))
    rems = disktool_core.list_remotes()
    return render_template('remotes.html', remotes=rems)

@app.route('/remotes/delete/<int:rid>')
@login_required
def remotes_delete(rid):
    disktool_core.remove_remote(rid)
    flash('Remote entfernt')
    return redirect(url_for('remotes'))

if __name__ == '__main__':
    disktool_core.init_db()
    threading.Thread(target=disktool_core.auto_mode_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=True)
