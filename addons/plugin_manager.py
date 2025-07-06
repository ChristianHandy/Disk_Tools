from flask import Blueprint, render_template, jsonify, current_app

blueprint = Blueprint('plugin_manager', __name__, url_prefix='/pluginmanager')

addon_meta = {
    "name": "Plugin Manager",
    "html_hooks": {}
}

def register(app, core):
    app.register_blueprint(blueprint)

@blueprint.route('/')
def plugin_manager_index():
    mgr = getattr(current_app, 'addon_mgr', None)
    return render_template('plugin_manager.html', plugins=mgr.status if mgr else [])

@blueprint.route('/status.json')
def plugin_manager_json():
    mgr = getattr(current_app, 'addon_mgr', None)
    return jsonify(mgr.status if mgr else [])