from flask import Flask, request, jsonify, send_from_directory
import requests, os, re

app = Flask(__name__)
BASES = ["https://sports.bzzoiro.com/api/v2", "https://sports.bzzoiro.com/api"]

def api_get(path, key):
    headers={"Authorization":"Token "+key,"Accept":"application/json"}
    last=None
    for base in BASES:
        try:
            r=requests.get(base+path,headers=headers,timeout=25)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            last=e
            if e.response is None or e.response.status_code not in (400,401,403,404,405):
                raise
    raise last or RuntimeError("Bzzoiro API nu a răspuns.")

def first(d,*keys):
    if not isinstance(d,dict): return None
    for k in keys:
        if k in d and d[k] is not None: return d[k]
    return None

def num(x):
    try: return float(x)
    except: return None

def pct(x):
    x=num(x)
    if x is None: return None
    return x*100 if 0<=x<=1 else x

def norm(x):
    return re.sub(r"[^a-z0-9]+","_",str(x or "").lower()).strip("_")

def flatten(x,prefix=""):
    if isinstance(x,dict):
        for k,v in x.items():
            p=f"{prefix}.{k}" if prefix else str(k)
            yield p,v
            yield from flatten(v,p)
    elif isinstance(x,list):
        for i,v in enumerate(x[:60]):
            yield from flatten(v,f"{prefix}[{i}]")

def values_for(obj,names):
    wanted={norm(n) for n in names}; out=[]
    for path,v in flatten(obj):
        if not isinstance(v,(dict,list)) and norm(path.split(".")[-1]) in wanted:
            out.append(v)
    return out

def any_num(obj,names):
    for v in values_for(obj,names):
        x=num(v)
        if x is not None: return x
    return None

def any_prob(obj,names):
    for v in values_for(obj,names):
        x=pct(v)
        if x is not None: return x
    return None

def team(x):
    if isinstance(x,dict): return str(first(x,"name","short_name","title") or "?")
    return str(x or "?")

def teams(e):
    return team(first(e,"home_team","home","home_name")),team(first(e,"away_team","away","away_name"))

def eid(e):
    return first(e,"id","event_id","match_id","fixture_id")

def league(e):
    v=first(e,"league_name","competition_name","tournament_name","league","competition","tournament","championship")
    if isinstance(v,dict): v=first(v,"name","short_name","title")
    if v: return str(v)
    lid=first(e,"league_id","competition_id","tournament_id")
    return f"Liga #{lid}" if lid is not None else "Necunoscută"

def odds(e):
    r={}
    r["home"]=num(first(e,"odds_home","home_odds","home_price"))
    r["draw"]=num(first(e,"odds_draw","draw_odds","draw_price"))
    r["away"]=num(first(e,"odds_away","away_odds","away_price"))
    r["gg_yes"]=num(first(e,"odds_btts_yes","btts_yes_odds","odds_gg_yes"))
    r["gg_no"]=num(first(e,"odds_btts_no","btts_no_odds","odds_gg_no"))
    for ln in ("0.5","1.5","2.5","3.5","4.5"):
        tag=ln.replace(".","")
        key=ln.replace(".","_")
        r["over_"+key]=num(first(e,"odds_over_"+tag,"odds_over_"+ln,"over_"+tag+"_odds"))
        r["under_"+key]=num(first(e,"odds_under_"+tag,"odds_under_"+ln,"under_"+tag+"_odds"))
        r["dc_1x_"+key]=None;r["dc_x2_"+key]=None;r["dc_12_"+key]=None
    o=first(e,"odds","consensus_odds","prices") or {}
    mw=first(o,"match_winner","match_result","1x2","winner") or {}
    bb=first(o,"btts","gg","both_teams_to_score") or {}
    ou=first(o,"over_under","total_goals","goals") or {}
    dc=first(o,"double_chance","double_chance_ft","doublechance") or {}
    r["home"]=r["home"] or num(first(mw,"home","HOME","1"))
    r["draw"]=r["draw"] or num(first(mw,"draw","DRAW","x","X"))
    r["away"]=r["away"] or num(first(mw,"away","AWAY","2"))
    r["gg_yes"]=r["gg_yes"] or num(first(bb,"yes","YES","btts_yes","gg_yes"))
    r["gg_no"]=r["gg_no"] or num(first(bb,"no","NO","btts_no","gg_no"))
    r["dc_1x"]=num(first(dc,"1x","1X","home_or_draw","home_draw","1_x"))
    r["dc_x2"]=num(first(dc,"x2","X2","draw_or_away","draw_away","x_2"))
    r["dc_12"]=num(first(dc,"12","1_2","home_or_away","home_away"))
    if isinstance(ou,dict):
        for ln in ("0.5","1.5","2.5","3.5","4.5"):
            tag=ln.replace(".","");key=ln.replace(".","_")
            r["over_"+key]=r["over_"+key] or num(first(ou,"over_"+tag,"over_"+ln,"over"+tag,"over"+ln))
            r["under_"+key]=r["under_"+key] or num(first(ou,"under_"+tag,"under_"+ln,"under"+tag,"under"+ln))
    return r

def same_event(p,e):
    ids=[]
    for k in ("event_id","match_id","fixture_id"):
        v=first(p,k)
        if v is not None: ids.append(str(v))
    for k in ("event","match","fixture"):
        x=p.get(k) if isinstance(p,dict) else None
        if isinstance(x,dict):
            v=first(x,"id","event_id","match_id")
            if v is not None: ids.append(str(v))
    if str(eid(e)) in ids: return True
    h,a=teams(e)
    txt=" ".join(str(v) for path,v in flatten(p) if isinstance(v,str) and ("home_team" in path.lower() or "away_team" in path.lower()))
    return h.lower() in txt.lower() and a.lower() in txt.lower()

def prediction_for(preds,e):
    for p in preds:
        if isinstance(p,dict) and same_event(p,e): return p
    return None

def prediction_text(p):
    vals=[]
    for k in ("predicted_result","prediction","predicted","pick","result","tip","call"):
        v=first(p,k)
        if isinstance(v,str): vals.append(v)
    # nested prediction object
    for k in ("prediction","forecast","predictions"):
        x=p.get(k) if isinstance(p,dict) else None
        if isinstance(x,dict):
            for kk in ("result","predicted_result","prediction","pick","label","text"):
                v=first(x,kk)
                if isinstance(v,str): vals.append(v)
    return " ".join(vals).strip()

def confidence(p):
    v=any_prob(p,("confidence","probability","prob","model_probability","prediction_probability"))
    return v

def selected_options(p,e):
    text=prediction_text(p).lower()
    conf=confidence(p)
    home,away=teams(e)
    opts=[]
    # Over / Under calls published by BSD, e.g. "Over 1.5 goals".
    m=re.search(r"\b(over|under)\s*(0\.5|1\.5|2\.5|3\.5|4\.5)",text)
    if m:
        side,ln=m.group(1),m.group(2)
        opts.append(("Peste/Sub goluri",("Peste " if side=="over" else "Sub ")+ln,conf,
                     ("over_" if side=="over" else "under_")+ln.replace(".","_")))
    # BTTS / GG.
    if re.search(r"\b(btts|both teams to score|gg)\b",text):
        is_no=bool(re.search(r"\b(no|not|nu)\b",text))
        opts.append(("GG","GG Nu" if is_no else "GG Da",conf,"gg_no" if is_no else "gg_yes"))
    # Double chance calls shown by BSD: "team to win or draw", "avoid defeat".
    if "win or draw" in text or "home or draw" in text:
        who="home"
        if away.lower() in text and home.lower() not in text: who="away"
        if who=="home": opts.append(("Șansă dublă","1X",conf,"dc_1x"))
        else: opts.append(("Șansă dublă","X2",conf,"dc_x2"))
    elif "avoid defeat" in text or "not lose" in text:
        who="home"
        if away.lower() in text and home.lower() not in text: who="away"
        opts.append(("Șansă dublă","1X" if who=="home" else "X2",conf,"dc_1x" if who=="home" else "dc_x2"))
    # Pure 1X2 calls.
    else:
        if re.search(r"\b(draw|tie|egal)\b",text):
            opts.append(("1X2","X",conf,"draw"))
        elif re.search(r"\b(home|home team|gazde)\b",text) or (home.lower() in text and "win" in text):
            opts.append(("1X2","1",conf,"home"))
        elif re.search(r"\b(away|away team|oaspeți|oaspeti)\b",text) or (away.lower() in text and "win" in text):
            opts.append(("1X2","2",conf,"away"))
    # If prediction has no readable text, use explicit probability fields.
    if not opts:
        ph=any_prob(p,("home_win_prob","home_probability","home_prob","prob_home","prob_1"))
        pd=any_prob(p,("draw_prob","draw_probability","draw_prob","prob_draw","prob_x"))
        pa=any_prob(p,("away_win_prob","away_probability","away_prob","prob_away","prob_2"))
        if ph is not None: opts.append(("1X2","1",ph,"home"))
        if pd is not None: opts.append(("1X2","X",pd,"draw"))
        if pa is not None: opts.append(("1X2","2",pa,"away"))
        # Flattened O/U probabilities if present.
        for ln in ("0.5","1.5","2.5","3.5","4.5"):
            tag=ln.replace(".","")
            po=any_prob(p,(f"over_{tag}_prob",f"prob_over_{tag}",f"over_{ln}_prob",f"prob_over_{ln}"))
            pu=any_prob(p,(f"under_{tag}_prob",f"prob_under_{tag}",f"under_{ln}_prob",f"prob_under_{ln}"))
            if po is not None: opts.append(("Peste/Sub goluri","Peste "+ln,po,"over_"+ln.replace(".","_")))
            if pu is not None: opts.append(("Peste/Sub goluri","Sub "+ln,pu,"under_"+ln.replace(".","_")))
    return opts

@app.get("/")
def index():
    return send_from_directory(app.root_path,"index.html")

@app.get("/health")
def health(): return jsonify({"ok":True})

@app.post("/api/search")
def search():
    key=request.headers.get("Authorization","").replace("Token ","").strip()
    if not key: return jsonify({"error":"Lipsește API Key"}),401
    q=request.get_json(force=True) or {}
    df,dt=q.get("date_from"),q.get("date_to")
    market=q.get("market","1x2"); selection=q.get("selection","all")
    line=str(q.get("line","2.5")); league_filter=str(q.get("league","all"))
    minp=float(q.get("min_prob",55)); mine=float(q.get("min_edge",5))
    omin=float(q.get("odds_min",1.5)); omax=float(q.get("odds_max",3)); maxp=int(q.get("max_picks",10))

    ev=api_get(f"/events/?date_from={df}&date_to={dt}&limit=200",key)
    pr=api_get(f"/predictions/?date_from={df}&date_to={dt}&limit=200",key)
    events=ev.get("results",ev if isinstance(ev,list) else [])
    preds=pr.get("results",pr if isinstance(pr,list) else [])
    leagues=sorted({league(e) for e in events if league(e)!="Necunoscută"})

    candidates=[];matched=0;readable=0;with_odds=0;tested=0
    for e in events:
        lg=league(e)
        if league_filter!="all" and lg!=league_filter: continue
        p=prediction_for(preds,e)
        if not p: continue
        matched+=1
        opts=selected_options(p,e)
        if opts: readable+=1
        eo=odds(e)
        if any(v is not None for v in eo.values()): with_odds+=1
        for mk,pick,pp,okey in opts:
            tested+=1
            if market=="1x2" and mk!="1X2": continue
            if market=="gg" and mk!="GG": continue
            if market=="goals" and mk!="Peste/Sub goluri": continue
            if market=="goals" and not pick.endswith(line): continue
            if selection!="all":
                if market=="1x2" and selection != {"1":"home","X":"draw","2":"away"}.get(pick): continue
                if market=="gg" and selection != ("yes" if pick=="GG Da" else "no"): continue
                if market=="goals" and selection != ("over" if pick.startswith("Peste") else "under"): continue
            odd=eo.get(okey)
            if pp is None or odd is None or odd<=0: continue
            if not (minp<=pp<=100 and omin<=odd<=omax): continue
            edge=pp-(100/odd)
            if edge>=mine:
                h,a=teams(e)
                candidates.append({"home":h,"away":a,"league":lg,"market":mk,"pick":pick,"prob":pp,"odds":odd,"implied":100/odd,"edge":edge,
                                  "kickoff":first(e,"start_time","kickoff","event_date","date","scheduled_at") or ""})
    candidates.sort(key=lambda x:x["edge"],reverse=True)
    return jsonify({"candidates":candidates[:maxp],"events":len(events),"predictions":len(preds),"leagues":leagues,
                    "matched":matched,"readable_predictions":readable,"with_odds":with_odds,"tested":tested})

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",8080)))
