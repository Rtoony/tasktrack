"""WSGI entrypoint for TaskTrack.

Gunicorn loads this module and looks for `app`. We invoke create_app()
to configure the module-level Flask singleton; the `app` exported here
is the same instance routes are registered against in app.py, just
with secret_key + cookie config + DB_PATH applied.
"""
from app import create_app

app = create_app()
