from flask import Flask, request, jsonify, send_from_directory
import requests
import os

app = Flask(__name__)

API_BASE = "https://sports.bzzoiro.com/api"
TIMEOUT = 25


def api_get(path, api_key):
    headers = {
        "Authorization": f"Token {api_key}",
        "Accept": "application/json",
    }
    r = requests.get(API_BASE + path, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def results(data):
    if isinstance(data, dict):
        return data.get("results", [])
    return data if isinstance(data, list) else []


def paginate(path, api_key):
    """Read all pages returned by Bzzoiro, while keeping a safety limit."""
    out = []
    url = path
    for _ in range(10):
        data = api_get(url, api_key)
        page = results(data)
        out.extend(page)

        if not isinstance(data, dict) or not data.get("next"):
            break

        nxt = data["next"]
        # Bzzoiro normally returns an absolute next URL.
        if nxt.startswith("https://sports.bzzoiro.com"):
            url = nxt.replace("https://sports.bzzoiro.com/api", "", 1)
            if not url.startswith("/"):
                url = "/" + url
        elif nxt.startswith("/api/"):
            url = nxt[4:]
        else:
            url = nxt

    return out


def n(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def league_name(event):
    league = event.get("league")
    if isinstance(league, dict):
        return str(league.get("name") or league.get("short_name") or "Necunoscută")
    if league:
        return str(league)

    for key in ("league_name", "competition_name", "tournament_name"):
        if event.get(key):
            return str(event[key])

    return "Necunoscută"


def event_id(event):
    return event.get("id") or event.get("event_id") or event.get("match_id")


def event_teams(event):
    home = event.get("home_team")
    away = event.get("away_team")

    if isinstance(home, dict):
        home = home.get("name") or home.get("short_name")
    if isinstance(away, dict):
        away = away.get("name") or away.get("short_name")

    return str(home or "?"), str(away or "?")


def event_odds(event):
    """Exact Bzzoiro fields confirmed by the diagnostic."""
    return {
        "1": n(event.get("odds_home")),
        "X": n(event.get("odds_draw")),
        "2": n(event.get("odds_away")),
        "gg_yes": n(event.get("odds_btts_yes")),
        "gg_no": n(event.get("odds_btts_no")),
        "over_0.5": n(event.get("odds_over_05")),
        "under_0.5": n(event.get("odds_under_05")),
        "over_1.5": n(event.get("odds_over_15")),
        "under_1.5": n(event.get("odds_under_15")),
        "over_2.5": n(event.get("odds_over_25")),
        "under_2.5": n(event.get("odds_under_25")),
        "over_3.5": n(event.get("odds_over_35")),
        "under_3.5": n(event.get("odds_under_35")),
        "over_4.5": n(event.get("odds_over_45")),
        "under_4.5": n(event.get("odds_under_45")),
    }


def prediction_id(pred):
    event = pred.get("event")
    if isinstance(event, dict):
        return event.get("id") or event.get("event_id") or event.get("match_id")
    return pred.get("event_id") or pred.get("match_id")


def make_options(pred):
    """
    Exact probability fields confirmed by the Bzzoiro diagnostic.
    Values are percentages (e.g. 56.8 = 56.8%).
    """
    options = []

    def add(market, pick, prob_key, odds_key):
        p = n(pred.get(prob_key))
        if p is not None:
            options.append((market, pick, p, odds_key))

    # 1X2
    add("1X2", "1", "prob_home_win", "1")
    add("1X2", "X", "prob_draw", "X")
    add("1X2", "2", "prob_away_win", "2")

    # GG / BTTS
    add("GG", "GG Da", "prob_btts_yes", "gg_yes")
    p_btts = n(pred.get("prob_btts_yes"))
    if p_btts is not None:
        options.append(("GG", "GG Nu", 100.0 - p_btts, "gg_no"))

    # Over/Under. Bzzoiro exposes over probability; under is its complement.
    for line in ("0.5", "1.5", "2.5", "3.5", "4.5"):
        key = line.replace(".", "")
        over_key = f"prob_over_{key}"
        p_over = n(pred.get(over_key))

        if p_over is not None:
            options.append(("Peste/Sub goluri", f"Peste {line}", p_over, f"over_{line}"))
            options.append(("Peste/Sub goluri", f"Sub {line}", 100.0 - p_over, f"under_{line}"))

    return options


@app.get("/")
def index():
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

    date_from = q.get("date_from")
    date_to = q.get("date_to")

    market = str(q.get("market", "1x2"))
    selection = str(q.get("selection", "all"))
    line = str(q.get("line", "2.5"))
    league_filter = str(q.get("league", "all"))

    min_prob = float(q.get("min_prob", 55))
    min_edge = float(q.get("min_edge", 5))
    odds_min = float(q.get("odds_min", 1.50))
    odds_max = float(q.get("odds_max", 3.00))
    max_picks = int(q.get("max_picks", 10))

    try:
        event_path = f"/events/?date_from={date_from}&date_to={date_to}&limit=200"
        prediction_path = f"/predictions/?date_from={date_from}&date_to={date_to}&limit=200"

        events = paginate(event_path, api_key)
        predictions = paginate(prediction_path, api_key)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        return jsonify({"error": f"Bzzoiro HTTP {status}"}), status
    except Exception as e:
        return jsonify({"error": f"Eroare API: {e}"}), 502

    # Index prediction by event ID. The diagnostic confirmed that predictions
    # contain a nested event object with the same Bzzoiro event id.
    pred_by_event = {}
    for p in predictions:
        pid = prediction_id(p)
        if pid is not None:
            pred_by_event[str(pid)] = p

    leagues = sorted({
        league_name(e)
        for e in events
        if league_name(e) != "Necunoscută"
    })

    candidates = []
    matched = 0
    with_probabilities = 0
    with_odds = 0
    tested = 0

    for event in events:
        # Only upcoming/not-started events are useful for pre-match value.
        status = str(event.get("status") or "").lower()
        if status not in ("", "notstarted", "scheduled", "timed", "upcoming"):
            continue

        lg = league_name(event)
        if league_filter != "all" and lg != league_filter:
            continue

        p = pred_by_event.get(str(event_id(event)))
        if not p:
            continue

        matched += 1

        options = make_options(p)
        if options:
            with_probabilities += 1

        eo = event_odds(event)
        if any(v is not None for v in eo.values()):
            with_odds += 1

        home, away = event_teams(event)
        kickoff = event.get("event_date") or event.get("kickoff") or event.get("start_time") or ""

        for mk, pick, prob, odds_key in options:
            tested += 1

            # Market tab
            if market == "1x2" and mk != "1X2":
                continue
            if market == "gg" and mk != "GG":
                continue
            if market == "goals" and mk != "Peste/Sub goluri":
                continue

            # Goal line
            if market == "goals" and not pick.endswith(line):
                continue

            # Selection
            if selection != "all":
                if market == "1x2":
                    expected = {"home": "1", "draw": "X", "away": "2"}.get(selection)
                    if expected and pick != expected:
                        continue
                elif market == "gg":
                    expected = "GG Da" if selection == "yes" else "GG Nu"
                    if pick != expected:
                        continue
                elif market == "goals":
                    expected = "Peste" if selection == "over" else "Sub"
                    if not pick.startswith(expected + " "):
                        continue

            odd = eo.get(odds_key)
            if prob is None or odd is None or odd <= 1:
                continue

            implied = 100.0 / odd
            edge = prob - implied

            if prob < min_prob:
                continue
            if odd < odds_min or odd > odds_max:
                continue
            if edge < min_edge:
                continue

            candidates.append({
                "home": home,
                "away": away,
                "league": lg,
                "market": mk,
                "pick": pick,
                "prob": round(prob, 2),
                "odds": round(odd, 3),
                "implied": round(implied, 2),
                "edge": round(edge, 2),
                "kickoff": kickoff,
            })

    candidates.sort(key=lambda x: x["edge"], reverse=True)

    return jsonify({
        "candidates": candidates[:max_picks],
        "events": len(events),
        "predictions": len(predictions),
        "leagues": leagues,
        "matched": matched,
        "with_probabilities": with_probabilities,
        "with_odds": with_odds,
        "tested": tested,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
