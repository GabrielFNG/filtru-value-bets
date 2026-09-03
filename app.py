from flask import Flask, request, jsonify, send_from_directory
import requests, os, re
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)
BASE = "https://sports.bzzoiro.com/api/v2"
PUBLIC = "https://sports.bzzoiro.com/api"

def api_get(path, key):
    headers = {"Authorization": "Token " + key, "Accept": "application/json"}
    for base in (BASE, PUBLIC):
        try:
            r = requests.get(base + path, headers=headers, timeout=20)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code not in (400,401,403,404,405):
                raise
    raise RuntimeError("Bzzoiro API nu a răspuns.")

def first(d, *keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None

def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def prob(v):
    v = num(v)
    if v is None:
        return None
    return v * 100 if 0 <= v <= 1 else v

def norm(v):
    return re.sub(r"[^a-z0-9]+", "_", str(v or "").lower()).strip("_")

def flatten(obj, prefix=""):
    if isinstance(obj, dict):
        for k,v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            yield p, v
            yield from flatten(v, p)
    elif isinstance(obj, list):
        for i,v in enumerate(obj[:50]):
            yield from flatten(v, f"{prefix}[{i}]")

def find_keys(obj, wanted):
    wanted = {norm(x) for x in wanted}
    out = []
    for path,v in flatten(obj):
        if norm(path.split(".")[-1]) in wanted and not isinstance(v,(dict,list)):
            out.append(v)
    return out

def find_any(obj, keys):
    vals = find_keys(obj, keys)
    return vals[0] if vals else None

def team(v):
    if isinstance(v,dict):
        return str(first(v,"name","short_name","title") or "?")
    return str(v or "?")

def teams(e):
    return team(first(e,"home_team","home","home_name")), team(first(e,"away_team","away","away_name"))

def league(e):
    v = first(e,"league_name","competition_name","tournament_name","league","competition","tournament")
    if isinstance(v,dict):
        v = first(v,"name","title","short_name")
    return str(v or "Necunoscută")

def eid(e):
    return first(e,"id","event_id","match_id","fixture_id")

def odds(e):
    r = {}
    r["home"] = num(first(e,"odds_home","home_odds","home_price"))
    r["draw"] = num(first(e,"odds_draw","draw_odds","draw_price"))
    r["away"] = num(first(e,"odds_away","away_odds","away_price"))
    r["gg_yes"] = num(first(e,"odds_btts_yes","btts_yes_odds","odds_gg_yes"))
    r["gg_no"] = num(first(e,"odds_btts_no","btts_no_odds","odds_gg_no"))
    for line in ("0.5","1.5","2.5","3.5","4.5"):
        tag=line.replace(".","")
        r["over_"+line.replace(".","_")] = num(first(e,"odds_over_"+tag,"odds_over_"+line,"over_"+tag+"_odds"))
        r["under_"+line.replace(".","_")] = num(first(e,"odds_under_"+tag,"odds_under_"+line,"under_"+tag+"_odds"))
    o=first(e,"odds","consensus_odds","prices") or {}
    mw=first(o,"match_winner","match_result","1x2","winner") or {}
    bb=first(o,"btts","gg","both_teams_to_score") or {}
    ou=first(o,"over_under","total_goals","goals") or {}
    r["home"]=r["home"] or num(first(mw,"home","HOME","1"))
    r["draw"]=r["draw"] or num(first(mw,"draw","DRAW","x","X"))
    r["away"]=r["away"] or num(first(mw,"away","AWAY","2"))
    r["gg_yes"]=r["gg_yes"] or num(first(bb,"yes","YES","btts_yes","gg_yes"))
    r["gg_no"]=r["gg_no"] or num(first(bb,"no","NO","btts_no","gg_no"))
    if isinstance(ou,dict):
        for line in ("0.5","1.5","2.5","3.5","4.5"):
            tag=line.replace(".","")
            key=line.replace(".","_")
            r["over_"+key]=r["over_"+key] or num(first(ou,"over_"+tag,"over_"+line,"over"+tag,"over"+line))
            r["under_"+key]=r["under_"+key] or num(first(ou,"under_"+tag,"under_"+line,"under"+tag,"under"+line))
    return r

def prediction_match(p,e):
    ids=[]
    for k in ("event_id","match_id","fixture_id","id"):
        v=first(p,k)
        if v is not None: ids.append(str(v))
    for k in ("event","match","fixture"):
        x=p.get(k) if isinstance(p,dict) else None
        if isinstance(x,dict):
            v=first(x,"id","event_id","match_id")
            if v is not None: ids.append(str(v))
    return str(eid(e)) in ids

def team_match(p,e):
    h,a=teams(e)
    vals=[]
    for k,v in flatten(p):
        lk=k.lower()
        if isinstance(v,str) and ("home_team" in lk or lk.endswith(".home") or "home_name" in lk):
            vals.append(v.lower())
        if isinstance(v,str) and ("away_team" in lk or lk.endswith(".away") or "away_name" in lk):
            vals.append(v.lower())
    text=" ".join(vals)
    return h.lower() in text and a.lower() in text

def get_pred(preds,e):
    for p in preds:
        if isinstance(p,dict) and prediction_match(p,e):
            return p
    for p in preds:
        if isinstance(p,dict) and team_match(p,e):
            return p
    return None

def all_prob_values(p, aliases):
    vals=[]
    for v in find_keys(p,aliases):
        x=prob(v)
        if x is not None: vals.append(x)
    return vals[0] if vals else None

def market_probs(p):
    # BSD documents the forecast as 1X2 / O-U / BTTS with probabilities 0-1.
    home=all_prob_values(p,("home_win_prob","home_probability","home_prob","prob_home","prob_1","home_win_probability"))
    draw=all_prob_values(p,("draw_prob","draw_probability","prob_draw","prob_x"))
    away=all_prob_values(p,("away_win_prob","away_probability","away_prob","prob_away","prob_2","away_win_probability"))
    yes=all_prob_values(p,("btts_yes_prob","yes_prob","prob_yes","prob_btts_yes","btts_probability_yes"))
    no=all_prob_values(p,("btts_no_prob","no_prob","prob_no","prob_btts_no","btts_probability_no"))
    out={"1":home,"X":draw,"2":away,"GG Da":yes,"GG Nu":no}
    for line in ("0.5","1.5","2.5","3.5","4.5"):
        tag=line.replace(".","")
        out["Peste "+line]=all_prob_values(p,(f"over_{tag}_prob",f"over_{line}_prob",f"prob_over_{tag}",f"prob_over_{line}",f"over_{tag}_probability",f"over_{line}_probability"))
        out["Sub "+line]=all_prob_values(p,(f"under_{tag}_prob",f"under_{line}_prob",f"prob_under_{tag}",f"prob_under_{line}",f"under_{tag}_probability",f"under_{line}_probability"))
    return out

def event_detail(e,key):
    i=eid(e)
    if i is None: return {}
    try:
        d=api_get(f"/events/{i}/",key)
        return d if isinstance(d,dict) else {}
    except Exception:
        return {}

def enrich_prediction(p,e,key):
    # If the list prediction is sparse, the event-detail endpoint contains the
    # same forecast/odds information used on BSD match pages.
    mp=market_probs(p or {})
    if any(v is not None for v in mp.values()):
        return p or {}
    d=event_detail(e,key)
    return d or p or {}

@app.get("/")
def index():
    return send_from_directory(app.root_path,"index.html")

@app.get("/health")
def health():
    return jsonify({"ok":True})

@app.post("/api/search")
def search():
    key=request.headers.get("Authorization","").replace("Token ","").strip()
    if not key: return jsonify({"error":"Lipsește API Key"}),401
    q=request.get_json(force=True) or {}
    df=q.get("date_from"); dt=q.get("date_to")
    market=q.get("market","1x2"); selection=q.get("selection","all")
    line=str(q.get("line","2.5")); league_filter=str(q.get("league","all"))
    minp=float(q.get("min_prob",55)); mine=float(q.get("min_edge",5))
    omin=float(q.get("odds_min",1.5)); omax=float(q.get("odds_max",3))
    maxp=int(q.get("max_picks",10))

    ev=api_get(f"/events/?date_from={df}&date_to={dt}&limit=200",key)
    pr=api_get(f"/predictions/?date_from={df}&date_to={dt}&limit=200",key)
    events=ev.get("results",ev if isinstance(ev,list) else [])
    preds=pr.get("results",pr if isinstance(pr,list) else [])
    leagues=sorted({league(e) for e in events if league(e)!="Necunoscută"})

    candidates=[]
    matched=0; with_probs=0; with_odds=0; tested=0

    for e in events:
        lg=league(e)
        if league_filter!="all" and lg!=league_filter: continue
        p=get_pred(preds,e)
        if not p: continue
        matched+=1
        p=enrich_prediction(p,e,key)
        probs=market_probs(p)
        eo=odds(e)
        # Also accept odds found in event detail if list event lacks them.
        if not any(v is not None for v in eo.values()):
            eo=odds(event_detail(e,key))
        if any(v is not None for v in probs.values()): with_probs+=1
        if any(v is not None for v in eo.values()): with_odds+=1

        options=[]
        if market in ("1x2","all"):
            for pick,k in (("1","home"),("X","draw"),("2","away")):
                if selection!="all" and selection!=({"1":"home","X":"draw","2":"away"}[pick]): continue
                options.append(("1X2",pick,probs.get(pick),eo.get(k)))
        if market in ("gg","all"):
            for pick,k,sel in (("GG Da","gg_yes","yes"),("GG Nu","gg_no","no")):
                if selection!="all" and selection!=sel: continue
                options.append(("GG",pick,probs.get(pick),eo.get(k)))
        if market in ("goals","all"):
            lines=[line] if market=="goals" else ["0.5","1.5","2.5","3.5","4.5"]
            for ln in lines:
                for side in ("Peste","Sub"):
                    if selection!="all" and selection!=("over" if side=="Peste" else "under"): continue
                    k=("over_" if side=="Peste" else "under_")+ln.replace(".","_")
                    options.append((f"{side}/{'' if False else ''}goluri {ln}",f"{side} {ln}",probs.get(f"{side} {ln}"),eo.get(k)))
        for mk,pick,pp,odd in options:
            tested+=1
            if pp is None or odd is None or odd<=0: continue
            if not (minp<=pp<=100 and omin<=odd<=omax): continue
            edge=pp-(100/odd)
            if edge>=mine:
                home,away=teams(e)
                kickoff=first(e,"start_time","kickoff","event_date","date","scheduled_at") or ""
                candidates.append({"home":home,"away":away,"league":lg,"market":mk,"pick":pick,"prob":pp,"odds":odd,"implied":100/odd,"edge":edge,"kickoff":kickoff})
    candidates.sort(key=lambda x:x["edge"],reverse=True)
    return jsonify({"candidates":candidates[:maxp],"events":len(events),"predictions":len(preds),"leagues":leagues,
                    "matched":matched,"with_probs":with_probs,"with_odds":with_odds,"tested":tested})

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",8080)))
