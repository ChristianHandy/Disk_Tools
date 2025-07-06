import os
import importlib.util
import traceback
import shutil
from pathlib import Path
import textwrap


class AddonManager:
    def __init__(self, app, core, hookpoints=None):
        self.app = app
        self.core = core
        self.hooks = hookpoints or {}
        self.css_files = []
        self.status = []

    def load_addons(self, addon_dir='addons', template_target='templates/addons'):
        os.makedirs(template_target, exist_ok=True)
        for fname in os.listdir(addon_dir):
            if not fname.endswith('.py'):
                continue
            fpath = os.path.join(addon_dir, fname)
            modname = fname[:-3]
            plugin_status = {"name": modname, "status": "error", "error": "", "file": fname}
            try:
                spec = importlib.util.spec_from_file_location(modname, fpath)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)

                name = mod.addon_meta["name"] if hasattr(mod, "addon_meta") and "name" in mod.addon_meta else modname
                plugin_status["name"] = name

                # HTML-Integration
                if hasattr(mod, "addon_meta") and "html" in mod.addon_meta:
                    html_content = mod.addon_meta["html"]
                    html_path = Path(template_target) / f"{modname}.html"
                    with open(html_path, "w", encoding="utf-8") as f:
                        f.write(textwrap.dedent(html_content).strip())

                    # Device-Button automatisch als Hook registrieren
                    def make_button(plugin_name):
                        return lambda dev: f'<a class="btn btn-sm btn-outline-secondary" href="/addons/{plugin_name}/{dev}">{plugin_name}</a>'
                    self.hooks.setdefault("device_buttons", []).append(make_button(modname))

                if hasattr(mod, "addon_meta"):
                    meta = mod.addon_meta
                    if "html_hooks" in meta:
                        for hookname, func in meta["html_hooks"].items():
                            self.hooks.setdefault(hookname, []).append(func)
                    if "css" in meta:
                        self.css_files.append(meta["css"])

                if hasattr(mod, "register"):
                    mod.register(self.app, self.core)

                plugin_status["status"] = "ok"

            except Exception as e:
                plugin_status["error"] = traceback.format_exc(limit=3)
            self.status.append(plugin_status)

    def render_hooks(self, hookname, *args, **kwargs):
        html = []
        for func in self.hooks.get(hookname, []):
            try:
                html.append(func(*args, **kwargs))
            except Exception as e:
                html.append(f"<!-- Hook {hookname} Fehler: {e} -->")
        return " ".join(html)
