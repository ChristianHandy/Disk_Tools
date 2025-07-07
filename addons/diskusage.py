# addons/diskusage.py
from flask import render_template
import subprocess

plugin_name = "diskusage"
plugin_title = "Festplattennutzung"

def register(app, disktool_core):
    @app.route(f'/addons/{plugin_name}/<device>')
    def diskusage_page(device):
        usage = get_disk_usage(device)
        return render_template(f'addons/{plugin_name}.html', device=device, usage=usage)

def get_disk_usage(device):
    try:
        output = subprocess.check_output(['df', '-h', f'/dev/{device}'], stderr=subprocess.STDOUT, text=True)
        return output
    except subprocess.CalledProcessError as e:
        return f"Fehler beim Abrufen der Nutzung:\n{e.output}"

