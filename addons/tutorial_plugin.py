# -----------------------------------------
# 📦 Tutorial Plugin für das DiskTool System
# -----------------------------------------
# Dieses Plugin demonstriert, wie ein vollständiges Addon aufgebaut ist.
# Es enthält:
#  - addon_meta mit eingebettetem HTML
#  - automatische Template-Generierung
#  - einen Button für jede Festplatte, der auf eine eigene Ansicht verweist
#  - Nutzung von base.html und Übergabe von "device" ins Template

addon_meta = {
    "name": "tutorial_plugin",

    # 🔽 Eingebettetes HTML-Template – wird automatisch extrahiert
    "html": """
    {% extends 'base.html' %}
    {% block title %}Tutorial Plugin – {{ device }}{% endblock %}
    {% block content %}
    <div class='container mt-4'>
      <h1>Tutorial Plugin</h1>
      <p>Dies ist eine Beispielseite, die du mit dem <code>tutorial_plugin</code> erzeugt hast.</p>
      <p>Aktuelles Gerät: <strong>{{ device }}</strong></p>
      <hr>
      <h5>🔧 Hinweise:</h5>
      <ul>
        <li>Dieses HTML stammt aus dem Python-Code in <code>tutorial_plugin.py</code>.</li>
        <li>Wird automatisch extrahiert und gespeichert unter <code>templates/addons/tutorial_plugin.html</code>.</li>
        <li>Ein Button erscheint automatisch hinter jeder Festplatte.</li>
        <li>Die Seite nutzt <code>base.html</code> als Grundlage.</li>
      </ul>
      <a href='/' class='btn btn-secondary mt-3'>Zurück</a>
    </div>
    {% endblock %}
    """
}

# Diese Funktion wird beim Laden des Plugins aufgerufen
def register(app, core):
    print("[tutorial_plugin] wurde erfolgreich geladen.")
