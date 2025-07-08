import psutil
import os

addon_meta = {
    "name": "diskusage",
    "html": r"""
{% extends 'base.html' %}
{% block title %}Festplattenfüllstand – {{ device }}{% endblock %}
{% block content %}
<div class='container mt-4'>
  <h1>💾 Festplattenfüllstand</h1>
  {% if usage.path != "nicht gefunden" %}
  <ul>
    <li><strong>Gerät:</strong> /dev/{{ device }}</li>
    <li><strong>Mountpoint:</strong> {{ usage.path }}</li>
    <li><strong>Gesamt:</strong> {{ usage.total }} GB</li>
    <li><strong>Verwendet:</strong> {{ usage.used }} GB</li>
    <li><strong>Frei:</strong> {{ usage.free }} GB</li>
    <li><strong>Belegung:</strong> {{ usage.percent }}%</li>
  </ul>
  <div style="width: 300px; border: 1px solid #000;">
    <div style="width: {{ usage.percent }}%; background: green; color: white; text-align: center;">
      {{ usage.percent }}%
    </div>
  </div>
  {% else %}
    <p>❌ Kein Mountpoint für /dev/{{ device }} gefunden.</p>
  {% endif %}
  <a href='/' class='btn btn-secondary mt-3'>Zurück</a>
</div>
{% endblock %}
"""
}

def get_usage(device):
    dev_path = f"/dev/{device}"
    for part in psutil.disk_partitions(all=True):
        if part.device == dev_path:
            try:
                usage = psutil.disk_usage(part.mountpoint)
                return {
                    "path": part.mountpoint,
                    "total": round(usage.total / (2**30), 2),
                    "used": round(usage.used / (2**30), 2),
                    "free": round(usage.free / (2**30), 2),
                    "percent": round(usage.percent, 2)
                }
            except Exception as e:
                print(f"[diskusage] Fehler bei psutil.disk_usage: {e}")
                break
    return {
        "path": "nicht gefunden",
        "total": 0,
        "used": 0,
        "free": 0,
        "percent": 0
    }

def register(app, core):
    app.jinja_env.globals['usage'] = lambda device: get_usage(device)
    print("[diskusage] Plugin (psutil-basiert) erfolgreich geladen.")

