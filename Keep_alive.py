import os
from threading import Thread

def run_keepalive():
    from flask import Flask
    app = Flask("keepalive")

    @app.route("/")
    def home():
        return "ok"

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if os.environ.get("RUN_KEEPALIVE", "1") == "1":
    Thread(target=run_keepalive, daemon=True).start()
