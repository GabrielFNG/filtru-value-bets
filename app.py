from flask import Flask, request, jsonify, send_from_directory
import requests, os, math

app=Flask(__name__, static_folder="static")
BASE="https://sports.bzzoiro.com/api/v2"

def get(path, key):
    r=requests.get(BASE+path, headers={"Authorization":"Token "+key,"Accept":"application/json"}, timeout=20)
    r.raise_for_status()
    return r.json()

def pct(x):
    if x is None: return None
    x=float(x); return x*100 if x<=1 else x

def first(d,*keys):
    for k in keys:
        if isinstance(d,dict) and d.get(k) is not None: return d[k]
    return None

def event_odds(e):
    o=e.get("odds") or e.get("consensus_odds") or {}
    r={"home":None,"draw":None,"away":None,"gg_yes":None,"gg_no":None}
    r["home"]=first(e,"odds_home")
    r["draw"]=first(e,"odds_draw")
    r["away"]=first(e,"odds_away")
    r["gg_yes"]=first(e,"odds_btts_yes","odds_gg_yes")
    r["gg_no"]=first(e,"odds_btts_no","odds_gg_no")
    mw=o.get("match_winner") or o.get("match_result") or o.get("1x2") or {}
    r["home"]=r["home"] or first(mw,"home","HOME")
    r["draw"]=r["draw"] or first(mw,"draw","DRAW")
    r["away"]=r["away"] or first(mw,"away","AWAY")
    bb=o.get("btts") or o.get("gg") or {}
    r["gg_yes"]=r["gg_yes"] or first(bb,"yes","YES")
    r["gg_no"]=r["gg_no"] or first(bb,"no","NO")
    return {k: float(v) if v is not None else None for k,v in r.items()}

def pred1x2(p):
    m=p.get("markets") or {}
    x=m.get("match_result") or m.get("match_winner") or p.get("match_result") or p
    ph=pct(first(x,"prob_home","home_win_prob"))
    pd=pct(first(x,"prob_draw","draw_prob"))
    pa=pct(first(x,"prob_away","away_win_prob"))
    pick=str(first(x,"predicted","predicted_result","prediction","pick") or "").lower()
    if pick in ("1","x","2"): pick={"1":"home","x":"draw","2":"away"}[pick]
    if pick not in ("home","draw","away"):
        vals=[("home",ph),("draw",pd),("away",pa)]
        vals=[v for v in vals if v[1] is not None]
        if not vals:return None
        pick=max(vals,key=lambda z:z[1])[0]
    return pick,{"home":ph,"draw":pd,"away":pa}[pick]

def predgg(p):
    m=p.get("markets") or {}
    x=m.get("btts") or m.get("gg") or m.get("both_teams_to_score") or p.get("btts") or p.get("gg") or {}
    py=pct(first(x,"prob_yes","prob_gg","probability","prob"))
    pn=pct(first(x,"prob_no","prob_not_gg"))
    pick=str(first(x,"predicted","prediction","pick") or "yes").lower()
    if pick in ("no","false","0"): return ("no",pn)
    return ("yes",py)

def goal_candidates(p):
    m=p.get("markets") or {}
    g=m.get("total_goals") or m.get("goals") or m.get("over_under") or p.get("total_goals") or p.get("goals") or {}
    out=[]
    for line in ("0.5","1.5","2.5","3.5","4.5"):
        x=g.get(line) or g.get(float(line)) if isinstance(g,dict) else None
        if isinstance(x,dict):
            po=pct(first(x,"prob_over","over_prob","prob_peste"))
            pu=pct(first(x,"prob_under","under_prob","prob_sub"))
            if po is not None: out.append((line,"over",po))
            if pu is not None: out.append((line,"under",pu))
    return out

@app.get("/")
def index(): return send_from_directory("static","index.html")

@app.post("/api/search")
def search():
    key=request.headers.get("Authorization","").replace("Token ","").strip()
    if not key:return jsonify({"error":"Lipsește API Key"}),401
    q=request.get_json(force=True); df=q["date_from"]; dt=q["date_to"]
    market=q.get("market","1x2"); minp=float(q.get("min_prob",55)); mine=float(q.get("min_edge",5))
    omin=float(q.get("odds_min",1.5)); omax=float(q.get("odds_max",3)); maxp=int(q.get("max_picks",10))
    ev=get(f"/events/?date_from={df}&date_to={dt}&limit=200",key); pr=get(f"/predictions/?date_from={df}&date_to={dt}&limit=200",key)
    events=ev.get("results",ev if isinstance(ev,list) else []); preds=pr.get("results",pr if isinstance(pr,list) else [])
    pm={str(p.get("event_id",p.get("event",{}).get("id",p.get("match_id")))):p for p in preds}
    cand=[]
    for e in events:
        p=pm.get(str(e.get("id")))
        if not p: continue
        eo=event_odds(e)
        base=[]
        if market in ("1x2","all"):
            x=pred1x2(p)
            if x:
                pick,prob=x; odd=eo.get(pick)
                if odd and minp<=prob and omin<=odd<=omax:
                    base.append(("1X2",{"home":"1","draw":"X","away":"2"}[pick],prob,odd))
        if market in ("gg","all"):
            x=predgg(p)
            if x and x[1] is not None:
                odd=eo.get("gg_yes" if x[0]=="yes" else "gg_no")
                if odd and minp<=x[1] and omin<=odd<=omax: base.append(("GG","GG Da" if x[0]=="yes" else "GG Nu",x[1],odd))
        if market in ("goals","all"):
            for line,side,prob in goal_candidates(p):
                if market=="goals" and line!=str(q.get("line","2.5")): continue
                if market=="goals" and q.get("selection","all")!="all" and side!=q.get("selection"): continue
                # Odds for goals may be exposed in event market data; support common compact fields.
                k=f"{'over' if side=='over' else 'under'}_{line.replace('.','_')}"
                odd=eo.get(k)
                if odd and minp<=prob and omin<=odd<=omax: base.append((f"Peste/Sub {line}", "Peste "+line if side=="over" else "Sub "+line,prob,odd))
        for mk,pick,prob,odd in base:
            implied=100/odd; edge=prob-implied
            if edge>=mine:
                home=e.get("home_team",e.get("home","?")); away=e.get("away_team",e.get("away","?"))
                if isinstance(home,dict): home=home.get("name","?")
                if isinstance(away,dict): away=away.get("name","?")
                cand.append({"home":home,"away":away,"market":mk,"pick":pick,"prob":prob,"odds":odd,"implied":implied,"edge":edge,"kickoff":e.get("start_time",e.get("kickoff",e.get("event_date","")))})
    cand.sort(key=lambda x:x["edge"],reverse=True)
    return jsonify({"candidates":cand[:maxp],"events":len(events),"predictions":len(preds)})

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",8080)))
