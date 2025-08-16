
import os
from flask import Flask, render_template, jsonify, make_response

app = Flask(__name__)

# Simple demo endpoint + draft_state with no-cache headers
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/draft_state")
def draft_state():
    from datetime import datetime
    data = {"on_the_clock": "Matt", "ts": datetime.utcnow().isoformat()+"Z"}
    resp = make_response(jsonify(data))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp

if __name__ == "__main__":
    # IMPORTANT for Render: bind to 0.0.0.0 and use the provided $PORT
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
