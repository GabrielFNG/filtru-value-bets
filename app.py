from flask import Flask, request, jsonify, send_from_directory
import requests
import os
from datetime import datetime, timezone, timedelta

app = Flask(__name__)

API_BASE = "https://sports.bzzoiro.com/api"
TIMEOUT = 25


def api_get(path, api_key):
    r = requests.get(
        API_BASE + path,
        headers={"Authorization": f"Token {api_key}", "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def get_results(data):
    if isinstance(data, dict):
        return data.get("results", [])
    return data if isinstance(data, list) else []


def get_all(path, api_key, max_pages=10):
    out = []
    next_path = path

    for _ in range(max_pages):
        data = api_get(next_path, api_key)
        out.extend(get_results(data))

        if not isinstance(data, dict) or not data.get("next"):
            break

        nxt = data["next"]
        if nxt.startswith("https://sports.bzzoiro.com/api"):
            next_path = nxt.split("https://sports.bzzoiro.com/api", 1)[1]
        elif nxt.startswith("https://sports.bzzoiro.com"):
            next_path = nxt.split("https://sports.bzzoiro.com", 1)[1]
        elif nxt.startswith("/api/"):
            next_path = nxt[4:]
        else:
            next_path = nxt

    return out


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def eid(obj):
    if not isinstance(obj, dict):
        return None
    return obj.get("id") or obj.get("event_id") or obj.get("match_id")


def teams(e):
    h = e.get("home_team")
    a = e.get("away_team")
    if isinstance(h, dict):
        h = h.get("name") or h.get("short_name")
    if isinstance(a, dict):
        a = a.get("name") or a.get("short_name")
    return str(h or "?"), str(a or "?")


def league(e):
    x = e.get("league")
    if isinstance(x, dict):
        return str(x.get("name") or x.get("short_name") or "Necunoscută")
    return str(x or e.get("league_name") or e.get("competition_name") or "Necunoscută")


def parse_date(value):
    if not value:
        return None
    s = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def odds(e):
    return {
        "1": num(e.get("odds_home")),
        "X": num(e.get("odds_draw")),
        "2": num(e.get("odds_away")),
        "gg_yes": num(e.get("odds_btts_yes")),
        "gg_no": num(e.get("odds_btts_no")),
        "over_0.5": num(e.get("odds_over_05")),
        "under_0.5": num(e.get("odds_under_05")),
        "over_1.5": num(e.get("odds_over_15")),
        "under_1.5": num(e.get("odds_under_15")),
        "over_2.5": num(e.get("odds_over_25")),
        "under_2.5": num(e.get("odds_under_25")),
        "over_3.5": num(e.get("odds_over_35")),
        "under_3.5": num(e.get("odds_under_35")),
        "over_4.5": num(e.get("odds_over_45")),
        "under_4.5": num(e.get("odds_under_45")),
    }


def make_options(p):
    out = []

    def add(market, pick, prob, odd_key):
        pval = num(p.get(prob))
        if pval is not None:
            out.append((market, pick, pval, odd_key))

    add("1X2", "1", "prob_home_win", "1")
    add("1X2", "X", "prob_draw", "X")
    add("1X2", "2", "prob_away_win", "2")

    by = num(p.get("prob_btts_yes"))
    if by is not None:
        out.append(("GG", "GG Da", by, "gg_yes"))
        out.append(("GG", "GG Nu", 100 - by, "gg_no"))

    for line in ("0.5", "1.5", "2.5", "3.5", "4.5"):
        key = line.replace(".", "")
        po = num(p.get("prob_over_" + key))
        if po is not None:
            out.append(("Peste/Sub goluri", f"Peste {line}", po, f"over_{line}"))
            out.append(("Peste/Sub goluri", f"Sub {line}", 100 - po, f"under_{line}"))

    return out


def prediction_event_id(p):
    ev = p.get("event")
    if isinstance(ev, dict):
        return eid(ev)
    return p.get("event_id") or p.get("match_id")


@app.get("/")
def home():
    return send_from_directory(app.root_path, "index.html")


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.post("/api/search")
def search():
    api_key = request.headers.get("Authorization", "").replace("Token ", "").strip()
    if not api_key:
        return jsonify({"error": "Lipsește API Key"}), 401

    q = request.get_json(silent=True) or {}

    try:
        days = max(0, int(q.get("days", 1)))
        min_prob = float(q.get("min_prob", 0))
        min_edge = float(q.get("min_edge", 0))
        odds_min = float(q.get("odds_min", 1))
        odds_max = float(q.get("odds_max", 100))
        max_picks = max(1, int(q.get("max_picks", 30)))
    except (TypeError, ValueError):
        return jsonify({"error": "Filtre numerice invalide"}), 400

    market_filter = str(q.get("market", "all"))
    selection = str(q.get("selection", "all"))
    line_filter = str(q.get("line", "2.5"))
    league_filter = str(q.get("league", "all"))

    # IMPORTANT:
    # Do not use the user's probability/edge/market filters in the API query.
    # Fetch the available set broadly, match it first, then filter locally.
    try:
        events = get_all("/events/?limit=200", api_key)
        predictions = get_all("/predictions/?limit=200", api_key)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        return jsonify({"error": f"Bzzoiro HTTP {status}"}), status
    except Exception as e:
        return jsonify({"error": f"Eroare API: {e}"}), 502

    # Upcoming window is applied locally.
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)

    usable_events = []
    for e in events:
        dt = parse_date(e.get("event_date") or e.get("kickoff") or e.get("start_time"))
        status = str(e.get("status") or "").lower()

        if dt is None:
            continue
        if dt < now or dt > end:
            continue
        if status not in ("", "notstarted", "scheduled", "timed", "upcoming"):
            continue
        usable_events.append(e)

    pred_by_event = {}
    for p in predictions:
        pid = prediction_event_id(p)
        if pid is not None:
            pred_by_event.setdefault(str(pid), []).append(p)

    leagues = sorted({league(e) for e in usable_events if league(e) != "Necunoscută"})

    candidates = []
    matched = 0
    with_prob = 0
    with_odds = 0
    tested = 0

    for e in usable_events:
        lg = league(e)
        if league_filter != "all" and lg != league_filter:
            continue

        ps = pred_by_event.get(str(eid(e)), [])
        if not ps:
            continue

        matched += 1
        eo = odds(e)
        if any(v is not None for v in eo.values()):
            with_odds += 1

        best_p = ps[0]
        options = make_options(best_p)
        if options:
            with_prob += 1

        home, away = teams(e)
        kickoff = e.get("event_date") or ""

        for mk, pick, prob, odds_key in options:
            tested += 1

            # Market
            if market_filter != "all":
                if market_filter == "1x2" and mk != "1X2":
                    continue
                if market_filter == "gg" and mk != "GG":
                    continue
                if market_filter == "goals" and mk != "Peste/Sub goluri":
                    continue

            # Goal line
            if mk == "Peste/Sub goluri" and market_filter == "goals":
                if not pick.endswith(line_filter):
                    continue

            # Selection
            if selection != "all":
                if mk == "1X2":
                    wanted = {"home": "1", "draw": "X", "away": "2"}.get(selection)
                    if wanted and pick != wanted:
                        continue
                elif mk == "GG":
                    wanted = {"yes": "GG Da", "no": "GG Nu"}.get(selection)
                    if wanted and pick != wanted:
                        continue
                elif mk == "Peste/Sub goluri":
                    wanted = {"over": "Peste", "under": "Sub"}.get(selection)
                    if wanted and not pick.startswith(wanted + " "):
                        continue

            odd = eo.get(odds_key)
            if odd is None or odd <= 1:
                continue

            implied = 100 / odd
            edge = prob - implied

            if prob < min_prob:
                continue
            if edge < min_edge:
                continue
            if odd < odds_min or odd > odds_max:
                continue

            candidates.append({
                "home": home,
                "away": away,
                "league": lg,
                "market": mk,
                "pick": pick,
                "prob": round(prob, 2),
                "odds": round(odd, 2),
                "implied": round(implied, 2),
                "edge": round(edge, 2),
                "kickoff": kickoff,
            })

    candidates.sort(key=lambda x: x["edge"], reverse=True)

    return jsonify({
        "candidates": candidates[:max_picks],
        "events": len(usable_events),
        "predictions": len(predictions),
        "leagues": leagues,
        "matched": matched,
        "with_probabilities": with_prob,
        "with_odds": with_odds,
        "tested": tested,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
