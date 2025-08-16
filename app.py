from flask import Flask, render_template, jsonify, make_response

app = Flask(__name__)

# Mock state for demo
draft_state = {"on_the_clock": "Matt"}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/draft_state')
def draft_state_route():
    resp = make_response(jsonify(draft_state))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp

if __name__ == "__main__":
    app.run(debug=True)
