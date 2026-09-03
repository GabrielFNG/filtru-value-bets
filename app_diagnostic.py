from flask import Flask, request, jsonify, send_from_directory
import requests
import re

app = Flask(__name__)

BASE = "https://sports.bzzoiro.com"
TIMEOUT = 20

def clean(obj):
    """Remove anything that could contain credentials."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if any(x in kl for x in [
                "token", "api_key", "apikey", "authorization",
                "secret", "password", "credential"
            ]):
                out[k] = "***REMOVED***"
            else:
                out[k] = clean(v)
        return out
    if isinstance(obj, list):
        return [clean(x) for x in obj[:10]]
    return obj

def get_json(path, api_key):
    headers = {"Authorization": f"Token {api_key}"}
    r = requests.get(BASE + path, headers=headers, timeout=TIMEOUT)
    try:
        data = r.json()
    except Exception:
        data = {"text": r.text[:3000]}
    return r.status_code, clean(data)

@app.route("/")
def home():
    return send_from_directory(app.root_path, "index.html")

@app.route("/api/diagnostic", methods=["POST"])
def diagnostic():
    body = request.get_json(silent=True) or {}
    api_key = str(body.get("api_key", "")).strip()

    if not api_key:
        return jsonify({"error": "Lipsește cheia API."}), 400

    result = {
        "message": "Diagnostic Bzzoiro — cheia nu este inclusă în rezultat.",
        "endpoints": {}
    }

    for name, path in [
        ("events", "/api/events/?limit=5"),
        ("predictions", "/api/predictions/?limit=10"),
    ]:
        try:
            status, data = get_json(path, api_key)
            entry = {"http_status": status}

            if isinstance(data, dict):
                entry["top_level_keys"] = list(data.keys())
                results = data.get("results")
                if isinstance(results, list):
                    entry["count_in_results"] = len(results)
                    entry["sample"] = results[:3]
                else:
                    entry["sample"] = data
            else:
                entry["sample"] = data

            result["endpoints"][name] = entry
        except Exception as e:
            result["endpoints"][name] = {"error": str(e)}

    return jsonify(result)

@app.route("/api/health")
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
