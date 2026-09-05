from flask import Flask, request, jsonify, send_from_directory
import requests, os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

app = Flask(__name__)
API_BASE = "https://sports.bzzoiro.com/api"
API_V2 = "https://sports.bzzoiro.com/api/v2"
TIMEOUT = 25

def get(path, key):
    r = requests.get(API_BASE + path,
        headers={"Authorization": f"Token {key}", "Accept":"application/json"},
        timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def all_results(path, key, pages=10):
    out=[]; nxt=path
    for _ in range(pages):
        data=get(nxt,key)
        out += data.get("results",[]) if isinstance(data,dict) else (data if isinstance(data,list) else [])
        if not isinstance(data,dict) or not data.get("next"): break
        nxt=data["next"]
        for prefix in ("https://sports.bzzoiro.com/api","https://sports.bzzoiro.com"):
            if nxt.startswith(prefix):
                nxt=nxt[len(prefix):]; break
    return out


def get_v2(path, key):
    r=requests.get(API_V2 + path,
        headers={"Authorization": f"Token {key}", "Accept":"application/json"},
        timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def v2_results(path, key):
    out=[]; nxt=path
    for _ in range(20):
        data=get_v2(nxt,key)
        out += data.get("results",[]) if isinstance(data,dict) else (data if isinstance(data,list) else [])
        if not isinstance(data,dict) or not data.get("next"): break
        nxt=data["next"]
        for prefix in (API_V2, "https://sports.bzzoiro.com"):
            if nxt.startswith(prefix):
                nxt=nxt[len(prefix):]
                break
    return out

def normalize_v2_event(e, odds_map=None):
    x=dict(e or {})
    x["id"]=eid(e)
    x["event_date"]=e.get("event_date") or e.get("kickoff") or e.get("start_time") or e.get("date")
    if "home_team" not in x:
        x["home_team"]=e.get("home") or e.get("home_name")
    if "away_team" not in x:
        x["away_team"]=e.get("away") or e.get("away_name")
    if "league" not in x:
        x["league"]=e.get("competition") or e.get("competition_name") or e.get("league_name")
    # v2 score fields are already home_score / away_score on event objects.
    if odds_map and str(x["id"]) in odds_map:
        o=odds_map[str(x["id"])]
        x.update(o)
    return x

def v2_odds_map(rows):
    out={}
    for r in rows:
        event_id=r.get("event_id") or r.get("match_id")
        if event_id is None:
            continue
        oid=str(event_id)
        market=str(r.get("market") or "").lower()
        outcome=str(r.get("outcome") or "").lower()
        odd=num(r.get("decimal_odds") if r.get("decimal_odds") is not None else r.get("odds"))
        if odd is None:
            continue
        out.setdefault(oid,{})
        if market=="1x2":
            if outcome=="home": out[oid]["odds_home"]=odd
            elif outcome=="draw": out[oid]["odds_draw"]=odd
            elif outcome=="away": out[oid]["odds_away"]=odd
        elif market=="btts":
            if outcome=="yes": out[oid]["odds_btts_yes"]=odd
            elif outcome=="no": out[oid]["odds_btts_no"]=odd
        elif market in ("over_under_05","over_under_15","over_under_25","over_under_35","over_under_45"):
            line=market.replace("over_under_","")
            if outcome=="over": out[oid]["odds_over_"+line]=odd
            elif outcome=="under": out[oid]["odds_under_"+line]=odd
    return out

def normalize_v2_prediction(p):
    x=dict(p or {})
    ev=p.get("event")
    if isinstance(ev,dict):
        x["event"]=normalize_v2_event(ev)

    m=p.get("markets") or {}
    mr=m.get("match_result") or {}
    ou=m.get("over_under") or {}
    b=m.get("btts") or {}

    if "prob_home_win" not in x: x["prob_home_win"]=mr.get("prob_home")
    if "prob_draw" not in x: x["prob_draw"]=mr.get("prob_draw")
    if "prob_away_win" not in x: x["prob_away_win"]=mr.get("prob_away")
    if "prob_over_15" not in x: x["prob_over_15"]=ou.get("prob_over_15")
    if "prob_over_25" not in x: x["prob_over_25"]=ou.get("prob_over_25")
    if "prob_over_35" not in x: x["prob_over_35"]=ou.get("prob_over_35")
    if "prob_btts_yes" not in x: x["prob_btts_yes"]=b.get("prob_yes")

    # Keep the event's final score available to the existing result parser.
    if isinstance(ev,dict):
        for k in ("home_score","away_score","status"):
            if k in ev:
                x[k]=ev[k]
    return x

def fetch_history_window(key, start_ro, end_ro):
    # Keep the stable v1 endpoints that already return embedded odds and
    # prediction probabilities. Query a small UTC calendar buffer, then filter
    # precisely in Romania time in /api/search.
    api_from=(start_ro-timedelta(days=1)).astimezone(timezone.utc).date().isoformat()
    api_to=(end_ro+timedelta(days=1)).astimezone(timezone.utc).date().isoformat()
    events=all_results(f"/events/?upcoming={'false' if start_ro < datetime.now(ZoneInfo('Europe/Bucharest')) else 'true'}&date_from={api_from}&date_to={api_to}",key)
    predictions=all_results(f"/predictions/?upcoming={'false' if start_ro < datetime.now(ZoneInfo('Europe/Bucharest')) else 'true'}&date_from={api_from}&date_to={api_to}",key)

    # Historical prediction records can contain the complete event object.
    by_event={str(eid(e)):e for e in events if eid(e) is not None}
    for p in predictions:
        pe=p.get("event")
        if isinstance(pe,dict) and eid(pe) is not None:
            pid=str(eid(pe))
            if pid not in by_event:
                by_event[pid]=pe
    return list(by_event.values()), predictions


def num(x):
    try: return float(x)
    except: return None

def eid(x):
    if not isinstance(x,dict): return None
    return x.get("id") or x.get("event_id") or x.get("match_id")

def event_id_from_prediction(p):
    e=p.get("event")
    return eid(e) if isinstance(e,dict) else (p.get("event_id") or p.get("match_id"))

def teams(e):
    h=e.get("home_team"); a=e.get("away_team")
    if isinstance(h,dict): h=h.get("name") or h.get("short_name")
    if isinstance(a,dict): a=a.get("name") or a.get("short_name")
    return str(h or "?"),str(a or "?")

def league(e):
    x=e.get("league")
    if isinstance(x,dict): return str(x.get("name") or x.get("short_name") or "Necunoscută")
    return str(x or e.get("league_name") or e.get("competition_name") or "Necunoscută")

def dt(e):
    s=e.get("event_date") or e.get("kickoff") or e.get("start_time")
    if not s:return None
    try:
        d=datetime.fromisoformat(str(s).replace("Z","+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except:return None

def odds(e):
    return {
      "1":num(e.get("odds_home")),"X":num(e.get("odds_draw")),"2":num(e.get("odds_away")),
      "gg_yes":num(e.get("odds_btts_yes")),"gg_no":num(e.get("odds_btts_no")),
      "over_0.5":num(e.get("odds_over_05")),"under_0.5":num(e.get("odds_under_05")),
      "over_1.5":num(e.get("odds_over_15")),"under_1.5":num(e.get("odds_under_15")),
      "over_2.5":num(e.get("odds_over_25")),"under_2.5":num(e.get("odds_under_25")),
      "over_3.5":num(e.get("odds_over_35")),"under_3.5":num(e.get("odds_under_35")),
      "over_4.5":num(e.get("odds_over_45")),"under_4.5":num(e.get("odds_under_45"))
    }

def options(p):
    out=[]
    def add(m,pick,key,ok):
        v=num(p.get(key))
        if v is not None: out.append((m,pick,v,ok))
    add("1X2","1","prob_home_win","1")
    add("1X2","X","prob_draw","X")
    add("1X2","2","prob_away_win","2")
    b=num(p.get("prob_btts_yes"))
    if b is not None:
        out += [("GG","GG Da",b,"gg_yes"),("GG","GG Nu",100-b,"gg_no")]
    for line in ("0.5","1.5","2.5","3.5","4.5"):
        key=line.replace(".","")
        v=num(p.get("prob_over_"+key))
        if v is not None:
            out += [("Peste/Sub goluri","Peste "+line,v,"over_"+line),
                    ("Peste/Sub goluri","Sub "+line,100-v,"under_"+line)]
    return out

@app.get("/")
def home(): return send_from_directory(app.root_path,"index.html")

@app.get("/health")
def health(): return jsonify({"ok":True})




def score_from_prediction(p):
    pairs=[
        (p.get("home_score"),p.get("away_score")),
        (p.get("score_home"),p.get("score_away")),
        (p.get("home_team_score"),p.get("away_team_score")),
        (p.get("goals_home"),p.get("goals_away")),
    ]
    sc=p.get("score")
    if isinstance(sc,dict):
        pairs += [(sc.get("home"),sc.get("away")),(sc.get("home_score"),sc.get("away_score"))]
    for h,a in pairs:
        hn,an=num(h),num(a)
        if hn is not None and an is not None:
            return int(hn),int(an)
    return None,None

def score_from_event(e):
    pairs=[
        (e.get("home_score"),e.get("away_score")),
        (e.get("score_home"),e.get("score_away")),
        (e.get("home_team_score"),e.get("away_team_score")),
        (e.get("goals_home"),e.get("goals_away")),
    ]
    sc=e.get("score")
    if isinstance(sc,dict):
        pairs += [(sc.get("home"),sc.get("away")),(sc.get("home_score"),sc.get("away_score"))]
    for h,a in pairs:
        hn,an=num(h),num(a)
        if hn is not None and an is not None:
            return int(hn),int(an)
    return None,None

@app.post("/api/check_results")
def check_results():
    key=request.headers.get("Authorization","").replace("Token ","").strip()
    if not key:return jsonify({"error":"Lipsește API Key"}),401
    q=request.get_json(silent=True) or {}
    ids={str(x) for x in (q.get("event_ids") or [])}
    if not ids:return jsonify({"results":{}})
    try:
        now=datetime.now(ZoneInfo("Europe/Bucharest"))
        date_from=(now-timedelta(days=30)).astimezone(timezone.utc).date().isoformat()
        date_to=(now+timedelta(days=1)).astimezone(timezone.utc).date().isoformat()
        events=all_results(f"/events/?upcoming=false&date_from={date_from}&date_to={date_to}",key)
        predictions=all_results(f"/predictions/?upcoming=false&date_from={date_from}&date_to={date_to}",key)
    except requests.HTTPError as e:
        st=e.response.status_code if e.response is not None else 502
        return jsonify({"error":f"Bzzoiro HTTP {st}"}),st
    except Exception as e:
        return jsonify({"error":f"Eroare API: {e}"}),502

    out={}
    by_event={str(eid(e)):e for e in events if eid(e) is not None}
    for p in predictions:
        pe=p.get("event")
        if isinstance(pe,dict) and eid(pe) is not None and str(eid(pe)) not in by_event:
            by_event[str(eid(pe))]=pe

    for sid in ids:
        e=by_event.get(sid)
        if not e:
            continue
        h,a=score_from_event(e)
        if h is not None and a is not None:
            out[sid]={
                "status":str(e.get("status") or "finished").lower(),
                "home_score":h,
                "away_score":a
            }
    return jsonify({"results":out})

@app.post("/api/search")
def search():
    key=request.headers.get("Authorization","").replace("Token ","").strip()
    if not key:return jsonify({"error":"Lipsește API Key"}),401
    q=request.get_json(silent=True) or {}
    try:
        days=int(q.get("days",1))
        min_prob=float(q.get("min_prob",0)); min_edge=float(q.get("min_edge",0))
        omin=float(q.get("odds_min",1)); omax=float(q.get("odds_max",100))
        maxp=max(1,int(q.get("max_picks",30)))
    except:return jsonify({"error":"Filtre numerice invalide"}),400

    mode=str(q.get("mode","filtered"))
    if mode not in ("filtered","all_edges"): mode="filtered"
    mf=str(q.get("market","all")); sf=str(q.get("selection","all"))
    lf=str(q.get("line","2.5")); league_filter=str(q.get("league","all")); team_filter=str(q.get("team","all"))

    try:
        # Active BSD v2: date_from/date_to are supported directly and pagination
        # is limit/offset. We still apply the exact Romania-time window locally.
        ro_now=datetime.now(ZoneInfo("Europe/Bucharest"))
        if days < 0:
            window_start=ro_now+timedelta(days=days)
            window_end=ro_now
        elif days == 0:
            window_start=ro_now.replace(hour=0,minute=0,second=0,microsecond=0)
            window_end=window_start+timedelta(days=1)
        else:
            window_start=ro_now
            window_end=ro_now+timedelta(days=days)

        events,predictions=fetch_history_window(key,window_start,window_end)
    except requests.HTTPError as e:
        s=e.response.status_code if e.response is not None else 502
        return jsonify({"error":f"Bzzoiro HTTP {s}"}),s
    except Exception as e:return jsonify({"error":f"Eroare API: {e}"}),502

    start=window_start
    end=window_end
    ev=[]
    for e in events:
        d=dt(e)
        if not d:
            continue
        d_ro=d.astimezone(ZoneInfo("Europe/Bucharest"))
        if not (start <= d_ro < end):
            continue
        status=str(e.get("status") or "").lower()
        if days < 0:
            # For historical windows keep finished/unknown events; the result
            # checker will use the actual score when available.
            ev.append(e)
        else:
            if status in ("","notstarted","scheduled","timed","upcoming"):
                ev.append(e)

    # ALL predictions are retained per event. Nothing is reduced to ps[0].
    by={}
    for p in predictions:
        pid=event_id_from_prediction(p)
        if pid is not None: by.setdefault(str(pid),[]).append(p)

    leagues=sorted({league(e) for e in ev if league(e)!="Necunoscută"})
    team_names=sorted({t for e in ev for t in teams(e) if t and t!="?"})
    teams_by_league={}
    for e in ev:
        lg=league(e)
        if lg=="Necunoscută": continue
        teams_by_league.setdefault(lg,set()).update(t for t in teams(e) if t and t!="?")
    teams_by_league={k:sorted(v) for k,v in teams_by_league.items()}
    candidates=[]; matched=withprob=withodds=tested=0

    for e in ev:
        lg=league(e)
        h,a=teams(e)
        if league_filter!="all" and lg!=league_filter: continue
        if team_filter!="all" and team_filter not in (h,a): continue
        ps=by.get(str(eid(e)),[])
        if not ps: continue
        matched+=1
        eo=odds(e)
        if any(v is not None for v in eo.values()): withodds+=1

        # Analyze EVERY prediction belonging to this event.
        # IMPORTANT: do not deduplicate by market/pick/odds.
        # Bzzoiro can return multiple prediction records for the same event,
        # and the user wants all prediction records to be evaluated.
        event_has_prob=False
        for p in ps:
            ops=options(p)
            if ops: event_has_prob=True
            for mk,pick,prob,okey in ops:
                # Every prediction record is evaluated independently.
                tested+=1

                if mf!="all":
                    if mf=="1x2" and mk!="1X2": continue
                    if mf=="gg" and mk!="GG": continue
                    if mf=="goals" and mk!="Peste/Sub goluri": continue
                if mk=="Peste/Sub goluri" and mf=="goals" and not pick.endswith(lf): continue

                if sf!="all":
                    if mk=="1X2" and {"home":"1","draw":"X","away":"2"}.get(sf) not in (None,pick): continue
                    if mk=="GG" and {"yes":"GG Da","no":"GG Nu"}.get(sf) not in (None,pick): continue
                    if mk=="Peste/Sub goluri":
                        wanted={"over":"Peste","under":"Sub"}.get(sf)
                        if wanted and not pick.startswith(wanted+" "): continue

                odd=eo.get(okey)
                if odd is None or odd<=1: continue

                implied=100/odd
                edge=prob-implied

                # v10: in normal Value Bets mode all numeric filters are active.
                # In ALL_EDGES mode they are intentionally bypassed so the full
                # calculable edge distribution can still be inspected.
                if mode=="filtered":
                    if prob < min_prob: continue
                    if edge < min_edge: continue
                    if odd < omin or odd > omax: continue

                candidates.append({
                    "home":h,"away":a,"league":lg,"market":mk,"pick":pick,
                    "prob":round(prob,2),"odds":round(odd,2),
                    "implied":round(implied,2),"edge":round(edge,2),
                    "kickoff":e.get("event_date","") ,
                    "event_id":eid(e),
                    "status":e.get("status","")
                })
        if event_has_prob: withprob+=1

    # Sort by highest value first and apply Max. selecții only in filtered mode.
    candidates.sort(key=lambda x:(x["edge"],x["prob"]),reverse=True)
    if mode=="filtered":
        candidates=candidates[:maxp]

    return jsonify({
        "candidates":candidates,
        "events":len(ev),"predictions":len(predictions),"leagues":leagues,"teams":team_names,"teams_by_league":teams_by_league,
        "matched":matched,"with_probabilities":withprob,
        "with_odds":withodds,"tested":tested,
        "mode":"ALL_EDGES" if mode=="all_edges" else "FILTERED",
        "filters_applied":(["days","league","team","market","selection","goal_line"] if mode=="all_edges" else ["days","league","market","selection","goal_line","min_prob","min_edge","odds_min","odds_max","max_picks"])
    })

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",8080)))
