import psutil

addon_meta = {
    "name": "diskdebug",
    "html": r"""
{% extends 'base.html' %}
{% block title %}Festplatten-Debug{% endblock %}
{% block content %}
<div class="container mt-4">
  <h1>🧪 Geräte & Mountpoints</h1>
  <ul>
    {% for entry in entries %}
    <li>
      <strong>{{ entry.device }}</strong> →
      Mountpoint: <code>{{ entry.mountpoint }}</code>,
      FSType: {{ entry.fstype }}
    </li>
    {% endfor %}
  </ul>
  <a href="/" class="btn btn-secondary mt-3">Zurück</a>
</div>
{% endblock %}
"""
}

def register(app, core):
    app.jinja_env.globals['entries'] = psutil.disk_partitions(all=True)
    print("[diskdebug] Plugin erfolgreich geladen.")

