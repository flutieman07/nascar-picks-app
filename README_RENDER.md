Render port/listen fix
======================

Why you saw "no open ports detected":
- On Render, your web service MUST listen on 0.0.0.0 and the platform-provided $PORT.
- If Flask defaults to 127.0.0.1 or you don't use $PORT, Render can't detect an open port.

How to deploy this:
1) Commit these files to your repo.
2) In Render's service:
   - Type: Web Service
   - Build Command: (leave default or 'pip install -r requirements.txt')
   - Start Command (recommended):  gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
     (Alternatively, if you run 'python app.py', make sure app.py calls app.run(host="0.0.0.0", port=int(os.environ["PORT"])) )
3) Redeploy. In logs you should see gunicorn starting and Render reporting a healthy port.

Troubleshooting:
- If you still see port errors, verify the Start Command is set to the gunicorn line above.
- Make sure your service is a Web Service (not a Background Worker).
- Ensure the process doesn't exit immediately (no syntax errors, no 'python -m flask run' without host/port).
