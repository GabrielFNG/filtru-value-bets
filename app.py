from flask import Flask, request, jsonify, send_from_directory
import requests, os
from datetime import datetime, timezone, timedelta

app = Flask(__name__)
API_BASE = "https://sports.bzzoiro.com/api"
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

@app.post("/api/search")
def search():
    key=request.headers.get("Authorization","").replace("Token ","").strip()
    if not key:return jsonify({"error":"Lipsește API Key"}),401
    q=request.get_json(silent=True) or {}
    try:
        days=max(0,int(q.get("days",1)))
        min_prob=float(q.get("min_prob",0)); min_edge=float(q.get("min_edge",0))
        omin=float(q.get("odds_min",1)); omax=float(q.get("odds_max",100))
        maxp=max(1,int(q.get("max_picks",30)))
    except:return jsonify({"error":"Filtre numerice invalide"}),400

    mode=str(q.get("mode","filtered"))
    if mode not in ("filtered","all_edges"): mode="filtered"
    mf=str(q.get("market","all")); sf=str(q.get("selection","all"))
    lf=str(q.get("line","2.5")); league_filter=str(q.get("league","all"))

    try:
        events=all_results("/events/?limit=200",key)
        predictions=all_results("/predictions/?limit=200",key)
    except requests.HTTPError as e:
        s=e.response.status_code if e.response is not None else 502
        return jsonify({"error":f"Bzzoiro HTTP {s}"}),s
    except Exception as e:return jsonify({"error":f"Eroare API: {e}"}),502

    now=datetime.now(timezone.utc); end=now+timedelta(days=days)
    ev=[]
    for e in events:
        d=dt(e); status=str(e.get("status") or "").lower()
        if d and now <= d <= end and status in ("","notstarted","scheduled","timed","upcoming"):
            ev.append(e)

    # ALL predictions are retained per event. Nothing is reduced to ps[0].
    by={}
    for p in predictions:
        pid=event_id_from_prediction(p)
        if pid is not None: by.setdefault(str(pid),[]).append(p)

    leagues=sorted({league(e) for e in ev if league(e)!="Necunoscută"})
    candidates=[]; matched=withprob=withodds=tested=0

    for e in ev:
        lg=league(e)
        if league_filter!="all" and lg!=league_filter: continue
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

                h,a=teams(e)
                candidates.append({
                    "home":h,"away":a,"league":lg,"market":mk,"pick":pick,
                    "prob":round(prob,2),"odds":round(odd,2),
                    "implied":round(implied,2),"edge":round(edge,2),
                    "kickoff":e.get("event_date","")
                })
        if event_has_prob: withprob+=1

    # Sort by highest value first and apply Max. selecții only in filtered mode.
    candidates.sort(key=lambda x:(x["edge"],x["prob"]),reverse=True)
    if mode=="filtered":
        candidates=candidates[:maxp]

    return jsonify({
        "candidates":candidates,
        "events":len(ev),"predictions":len(predictions),"leagues":leagues,
        "matched":matched,"with_probabilities":withprob,
        "with_odds":withodds,"tested":tested,
        "mode":"ALL_EDGES" if mode=="all_edges" else "FILTERED",
        "filters_applied":(["days","league","market","selection","goal_line"] if mode=="all_edges" else ["days","league","market","selection","goal_line","min_prob","min_edge","odds_min","odds_max","max_picks"])
    })

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",8080)))
