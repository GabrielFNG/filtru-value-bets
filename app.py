from flask import Flask, request, jsonify, send_from_directory
import requests, os, re

app = Flask(__name__)
BASE_V2 = "https://sports.bzzoiro.com/api/v2"
BASE_PUBLIC = "https://sports.bzzoiro.com/api"

def bzz_get(base, path, key):
    r = requests.get(
        base + path,
        headers={"Authorization": "Token " + key, "Accept": "application/json"},
        timeout=25,
    )
    r.raise_for_status()
    return r.json()

def get(path, key):
    try:
        return bzz_get(BASE_V2, path, key)
    except requests.HTTPError as e:
        # Some free/public endpoints are exposed under /api rather than /api/v2.
        if e.response is not None and e.response.status_code in (400, 401, 403, 404, 405):
            return bzz_get(BASE_PUBLIC, path, key)
        raise

def first(d, *keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None

def pct(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x * 100 if 0 <= x <= 1 else x

def number(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

def norm(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").strip().lower()).strip("_")

def walk(obj, max_depth=4, depth=0):
    if depth > max_depth:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k), v
            yield from walk(v, max_depth, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:30]:
            yield from walk(v, max_depth, depth + 1)

def find_value(obj, aliases):
    aliases = {norm(x) for x in aliases}
    for k, v in walk(obj):
        if norm(k) in aliases and not isinstance(v, (dict, list)):
            return v
    return None

def find_text(obj, aliases):
    v = find_value(obj, aliases)
    return str(v).strip() if v is not None else None

def event_id(e):
    return first(e, "id", "event_id", "match_id", "fixture_id")

def team_name(x):
    if isinstance(x, dict):
        return first(x, "name", "short_name", "title") or "?"
    return str(x or "?")

def event_teams(e):
    return (
        team_name(first(e, "home_team", "home", "home_name")),
        team_name(first(e, "away_team", "away", "away_name")),
    )

def league_name(e):
    v = first(
        e,
        "league_name", "competition_name", "tournament_name",
        "league", "competition", "tournament", "championship"
    )
    if isinstance(v, dict):
        v = first(v, "name", "title", "short_name")
    if v:
        return str(v)
    # Try common nested containers.
    for container_key in ("league", "competition", "tournament"):
        obj = e.get(container_key) if isinstance(e, dict) else None
        if isinstance(obj, dict):
            v = first(obj, "name", "title", "short_name")
            if v:
                return str(v)
    return "Necunoscută"

def event_odds(e):
    r = {
        "home": None, "draw": None, "away": None,
        "gg_yes": None, "gg_no": None
    }
    for line in ("0.5", "1.5", "2.5", "3.5", "4.5"):
        r[f"over_{line.replace('.', '_')}"] = None
        r[f"under_{line.replace('.', '_')}"] = None

    # Direct event fields.
    r["home"] = number(first(e, "odds_home", "home_odds", "home_price"))
    r["draw"] = number(first(e, "odds_draw", "draw_odds", "draw_price"))
    r["away"] = number(first(e, "odds_away", "away_odds", "away_price"))
    r["gg_yes"] = number(first(e, "odds_btts_yes", "odds_gg_yes", "btts_yes_odds"))
    r["gg_no"] = number(first(e, "odds_btts_no", "odds_gg_no", "btts_no_odds"))

    o = first(e, "odds", "consensus_odds", "prices") or {}
    mw = first(o, "match_winner", "match_result", "1x2", "winner") or {}
    bb = first(o, "btts", "gg", "both_teams_to_score") or {}
    ou = first(o, "over_under", "total_goals", "goals") or {}

    r["home"] = r["home"] or number(first(mw, "home", "HOME", "1"))
    r["draw"] = r["draw"] or number(first(mw, "draw", "DRAW", "x", "X"))
    r["away"] = r["away"] or number(first(mw, "away", "AWAY", "2"))
    r["gg_yes"] = r["gg_yes"] or number(first(bb, "yes", "YES", "btts_yes", "gg_yes"))
    r["gg_no"] = r["gg_no"] or number(first(bb, "no", "NO", "btts_no", "gg_no"))

    if isinstance(ou, dict):
        for line in ("0.5", "1.5", "2.5", "3.5", "4.5"):
            tag = line.replace(".", "")
            key = line.replace(".", "_")
            r[f"over_{key}"] = number(first(ou, f"over_{tag}", f"over_{line}", f"over{tag}", f"over{line}"))
            r[f"under_{key}"] = number(first(ou, f"under_{tag}", f"under_{line}", f"under{tag}", f"under{line}"))
    return r

def prediction_for_event(preds, eid):
    if eid is None:
        return None
    for p in preds:
        ids = [
            first(p, "event_id", "match_id", "fixture_id"),
            first(p.get("event", {}) if isinstance(p.get("event"), dict) else {}, "id"),
            first(p.get("match", {}) if isinstance(p.get("match"), dict) else {}, "id"),
        ]
        if any(x is not None and str(x) == str(eid) for x in ids):
            return p
    return None

def market_container(p, names):
    m = p.get("markets") if isinstance(p, dict) else None
    if isinstance(m, dict):
        for n in names:
            x = m.get(n)
            if isinstance(x, dict):
                return x
    for n in names:
        x = p.get(n) if isinstance(p, dict) else None
        if isinstance(x, dict):
            return x
    return {}

def pred1x2(p):
    x = market_container(p, ("match_result", "match_winner", "1x2", "winner"))
    ph = pct(first(x, "prob_home", "home_win_prob", "home_probability", "home_prob", "prob_1", "prob1"))
    pd = pct(first(x, "prob_draw", "draw_prob", "draw_probability", "draw_prob", "prob_x", "probX"))
    pa = pct(first(x, "prob_away", "away_win_prob", "away_probability", "away_prob", "prob_2", "prob2"))

    if ph is None: ph = pct(find_value(p, ("prob_home", "home_win_prob", "home_probability", "home_prob", "prob_1", "prob1")))
    if pd is None: pd = pct(find_value(p, ("prob_draw", "draw_prob", "draw_probability", "draw_prob", "prob_x", "probx")))
    if pa is None: pa = pct(find_value(p, ("prob_away", "away_win_prob", "away_probability", "away_prob", "prob_2", "prob2")))

    pick = first(x, "predicted", "predicted_result", "prediction", "pick", "result")
    if pick is None:
        pick = find_text(p, ("predicted", "predicted_result", "prediction", "pick", "result"))
    pick = str(pick or "").strip().lower()

    mapping = {
        "1": "home", "x": "draw", "2": "away",
        "home": "home", "home_win": "home", "home team": "home",
        "draw": "draw", "tie": "draw",
        "away": "away", "away_win": "away", "away team": "away",
    }
    pick = mapping.get(pick)
    vals = [("home", ph), ("draw", pd), ("away", pa)]
    vals = [v for v in vals if v[1] is not None]
    if pick is None and vals:
        pick = max(vals, key=lambda z: z[1])[0]

    if pick not in ("home", "draw", "away"):
        return None

    prob = {"home": ph, "draw": pd, "away": pa}.get(pick)
    # If the API publishes a single confidence with a predicted result, use it.
    if prob is None:
        conf = first(x, "confidence", "probability", "prob")
        if conf is None:
            conf = find_value(p, ("confidence", "probability"))
        prob = pct(conf)
    return pick, prob

def predgg(p):
    x = market_container(p, ("btts", "gg", "both_teams_to_score"))
    py = pct(first(x, "prob_yes", "prob_gg", "yes_prob", "btts_yes_prob", "probability_yes", "prob_yes_btts"))
    pn = pct(first(x, "prob_no", "prob_not_gg", "no_prob", "btts_no_prob", "probability_no", "prob_no_btts"))
    if py is None:
        py = pct(find_value(p, ("prob_yes", "prob_gg", "yes_prob", "btts_yes_prob", "probability_yes")))
    if pn is None:
        pn = pct(find_value(p, ("prob_no", "prob_not_gg", "no_prob", "btts_no_prob", "probability_no")))

    pick = first(x, "predicted", "prediction", "pick", "result")
    if pick is None:
        pick = find_text(p, ("btts_prediction", "gg_prediction", "predicted_btts", "predicted"))
    s = str(pick or "").strip().lower()
    if s in ("no", "false", "0", "nu", "gg nu", "btts no"):
        prob = pn
        if prob is None:
            conf = first(x, "confidence", "probability", "prob")
            prob = pct(conf)
        return "no", prob

    prob = py
    if prob is None:
        conf = first(x, "confidence", "probability", "prob")
        if conf is None:
            conf = find_value(p, ("btts_confidence", "gg_confidence", "confidence"))
        prob = pct(conf)
    return "yes", prob

def goal_candidates(p):
    out = []
    # First handle nested structures such as total_goals: {"2.5": {...}}.
    g = market_container(p, ("total_goals", "goals", "over_under"))
    if isinstance(g, dict):
        for line in ("0.5", "1.5", "2.5", "3.5", "4.5"):
            x = g.get(line) or g.get(float(line))
            if isinstance(x, dict):
                po = pct(first(x, "prob_over", "over_prob", "prob_peste", "probability_over", "prob"))
                pu = pct(first(x, "prob_under", "under_prob", "prob_sub", "probability_under"))
                if po is not None: out.append((line, "over", po))
                if pu is not None: out.append((line, "under", pu))

    # Then flattened/common aliases.
    for line in ("0.5", "1.5", "2.5", "3.5", "4.5"):
        tag = line.replace(".", "_")
        tag2 = line.replace(".", "")
        po = find_value(p, (
            f"prob_over_{tag}", f"over_{tag}_prob", f"prob_over_{tag2}",
            f"over_{tag2}_prob", f"over_{tag}_probability", f"over_{tag2}_probability"
        ))
        pu = find_value(p, (
            f"prob_under_{tag}", f"under_{tag}_prob", f"prob_under_{tag2}",
            f"under_{tag2}_prob", f"under_{tag}_probability", f"under_{tag2}_probability"
        ))
        if po is not None:
            out.append((line, "over", pct(po)))
        if pu is not None:
            out.append((line, "under", pct(pu)))

    # Avoid duplicate line/side pairs.
    seen, clean = set(), []
    for item in out:
        if item[:2] not in seen:
            seen.add(item[:2]); clean.append(item)
    return clean

@app.get("/")
def index():
    return send_from_directory(app.root_path, "index.html")

@app.post("/api/search")
def search():
    key = request.headers.get("Authorization", "").replace("Token ", "").strip()
    if not key:
        return jsonify({"error": "Lipsește API Key"}), 401

    q = request.get_json(force=True) or {}
    df, dt = q.get("date_from"), q.get("date_to")
    market = q.get("market", "1x2")
    selection = q.get("selection", "all")
    line_filter = str(q.get("line", "2.5"))
    league_filter = str(q.get("league", "all"))
    minp = float(q.get("min_prob", 55))
    mine = float(q.get("min_edge", 5))
    omin = float(q.get("odds_min", 1.5))
    omax = float(q.get("odds_max", 3))
    maxp = int(q.get("max_picks", 10))

    ev = get(f"/events/?date_from={df}&date_to={dt}&limit=200", key)
    pr = get(f"/predictions/?date_from={df}&date_to={dt}&limit=200", key)

    events = ev.get("results", ev if isinstance(ev, list) else [])
    preds = pr.get("results", pr if isinstance(pr, list) else [])

    leagues = sorted({league_name(e) for e in events if league_name(e) != "Necunoscută"})

    cand = []
    for e in events:
        lg = league_name(e)
        if league_filter != "all" and lg != league_filter:
            continue

        p = prediction_for_event(preds, event_id(e))
        if not p:
            continue

        eo = event_odds(e)
        base = []

        if market in ("1x2", "all"):
            x = pred1x2(p)
            if x:
                pick, prob = x
                odd = eo.get(pick)
                allowed = selection == "all" or selection == pick
                if odd and prob is not None and allowed and minp <= prob <= 100 and omin <= odd <= omax:
                    base.append(("1X2", {"home": "1", "draw": "X", "away": "2"}[pick], prob, odd))

        if market in ("gg", "all"):
            x = predgg(p)
            if x and x[1] is not None:
                odd = eo.get("gg_yes" if x[0] == "yes" else "gg_no")
                allowed = selection == "all" or selection == x[0]
                if odd and allowed and minp <= x[1] <= 100 and omin <= odd <= omax:
                    base.append(("GG", "GG Da" if x[0] == "yes" else "GG Nu", x[1], odd))

        if market in ("goals", "all"):
            for line, side, prob in goal_candidates(p):
                if market == "goals":
                    if line != line_filter:
                        continue
                    if selection != "all" and side != selection:
                        continue
                k = f"{'over' if side == 'over' else 'under'}_{line.replace('.', '_')}"
                odd = eo.get(k)
                if odd and prob is not None and minp <= prob <= 100 and omin <= odd <= omax:
                    base.append(
                        (f"Peste/Sub {line}", "Peste " + line if side == "over" else "Sub " + line, prob, odd)
                    )

        home, away = event_teams(e)
        kickoff = first(e, "start_time", "kickoff", "event_date", "date", "scheduled_at") or ""
        for mk, pick, prob, odd in base:
            implied = 100 / odd
            edge = prob - implied
            if edge >= mine:
                cand.append({
                    "home": home, "away": away, "league": lg,
                    "market": mk, "pick": pick, "prob": prob, "odds": odd,
                    "implied": implied, "edge": edge, "kickoff": kickoff
                })

    cand.sort(key=lambda x: x["edge"], reverse=True)
    return jsonify({
        "candidates": cand[:maxp],
        "events": len(events),
        "predictions": len(preds),
        "leagues": leagues
    })

@app.get("/health")
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
