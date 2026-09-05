from flask import Flask, request, jsonify, send_from_directory
import requests, os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

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
        # v1 history is page-number paginated. Pull the recent archive window
        # where selected matches can realistically be, then match by event id.
        now=datetime.now(timezone.utc)
        date_from=(now-timedelta(days=30)).date().isoformat()
        date_to=now.date().isoformat()
        events=all_results(f"/events/?upcoming=false&date_from={date_from}&date_to={date_to}",key)
        predictions=all_results(f"/predictions/?upcoming=false&date_from={date_from}&date_to={date_to}",key)
    except requests.HTTPError as e:
        s=e.response.status_code if e.response is not None else 502
        return jsonify({"error":f"Bzzoiro HTTP {s}"}),s
    except Exception as e:return jsonify({"error":f"Eroare API: {e}"}),502
    out={}
    pred_by_event={}
    for p in predictions:
        i=event_id_from_prediction(p)
        if i is not None: pred_by_event[str(i)]=p
    for e in events:
        i=eid(e)
        if i is None or str(i) not in ids: continue
        h,a=score_from_event(e)
        status=str(e.get("status") or "").lower()
        if h is None or a is None:
            pp=pred_by_event.get(str(i))
            if pp:
                h,a=score_from_prediction(pp)
                if not status: status=str(pp.get("status") or "").lower()
        out[str(i)]={"status":status,"home_score":h,"away_score":a}
    # If an event is present only inside a historical prediction, still return its score.
    for i in ids:
        if i in out: continue
        pp=pred_by_event.get(i)
        if pp:
            h,a=score_from_prediction(pp)
            if h is not None and a is not None:
                out[i]={"status":str(pp.get("status") or "finished").lower(),"home_score":h,"away_score":a}
    return jsonify({"results":out})


@app.post("/api/debug_search")
def debug_search():
    key=request.headers.get("Authorization","").replace("Token ","").strip()
    if not key:
        return jsonify({"error":"Lipsește API Key"}),401
    q=request.get_json(silent=True) or {}
    try:
        days=int(q.get("days",0))
        ro_now=datetime.now(ZoneInfo("Europe/Bucharest"))
        if days<0:
            ws=ro_now+timedelta(days=days); we=ro_now
        elif days==0:
            ws=ro_now.replace(hour=0,minute=0,second=0,microsecond=0); we=ws+timedelta(days=1)
        else:
            ws=ro_now; we=ro_now+timedelta(days=days)

        # Same legacy API path as the known-working v12.5 search.
        if days<0:
            af=(ws-timedelta(days=1)).astimezone(timezone.utc).date().isoformat()
            at=(we+timedelta(days=1)).astimezone(timezone.utc).date().isoformat()
            ep=f"/events/?upcoming=false&date_from={af}&date_to={at}"
            pp=f"/predictions/?upcoming=false&date_from={af}&date_to={at}"
        elif days==0:
            af=(ws-timedelta(days=1)).astimezone(timezone.utc).date().isoformat()
            at=(we+timedelta(days=1)).astimezone(timezone.utc).date().isoformat()
            ep=f"/events/?upcoming=false&date_from={af}&date_to={at}"
            pp=f"/predictions/?upcoming=false&date_from={af}&date_to={at}"
        else:
            af=(ws-timedelta(days=1)).astimezone(timezone.utc).date().isoformat()
            at=(we+timedelta(days=1)).astimezone(timezone.utc).date().isoformat()
            ep=f"/events/?upcoming=true&date_from={af}&date_to={at}"
            pp=f"/predictions/?upcoming=true&date_from={af}&date_to={at}"

        events=all_results(ep,key)
        predictions=all_results(pp,key)

        event_ids={str(eid(e)) for e in events if eid(e) is not None}
        pred_ids={str(event_id_from_prediction(p)) for p in predictions if event_id_from_prediction(p) is not None}

        # Count exact time-window events.
        in_window=0
        for e in events:
            d=dt(e)
            if d and ws <= d.astimezone(ZoneInfo("Europe/Bucharest")) < we:
                in_window+=1

        # Count prediction records with usable model/odds fields.
        field_stats={"pred_total":len(predictions),"with_event_id":0,
                     "with_prob":0,"with_odds":0,"with_both":0}
        samples=[]
        for p in predictions[:20]:
            pid=event_id_from_prediction(p)
            if pid is not None: field_stats["with_event_id"]+=1
            probs=[p.get(k) for k in (
                "prob_home_win","prob_draw","prob_away_win","prob_btts_yes",
                "prob_over_15","prob_over_25","prob_over_35")]
            ev=p.get("event") if isinstance(p.get("event"),dict) else {}
            odds=[ev.get(k) for k in (
                "odds_home","odds_draw","odds_away","odds_btts_yes",
                "odds_btts_no","odds_over_15","odds_under_15",
                "odds_over_25","odds_under_25","odds_over_35","odds_under_35")]
            hp=any(num(x) is not None for x in probs)
            ho=any(num(x) is not None and num(x)>0 for x in odds)
            if hp: field_stats["with_prob"]+=1
            if ho: field_stats["with_odds"]+=1
            if hp and ho: field_stats["with_both"]+=1
            if len(samples)<5:
                samples.append({
                    "id":p.get("id"),"event_id":pid,
                    "event_date":ev.get("event_date"),
                    "home":ev.get("home_team") or ev.get("home_name"),
                    "away":ev.get("away_team") or ev.get("away_name"),
                    "prob_home_win":p.get("prob_home_win"),
                    "prob_draw":p.get("prob_draw"),
                    "prob_away_win":p.get("prob_away_win"),
                    "odds_home":ev.get("odds_home"),
                    "odds_draw":ev.get("odds_draw"),
                    "odds_away":ev.get("odds_away")
                })

        return jsonify({
            "days":days,"window_start":ws.isoformat(),"window_end":we.isoformat(),
            "api_from":af,"api_to":at,
            "events":len(events),"predictions":len(predictions),
            "event_ids":len(event_ids),"prediction_event_ids":len(pred_ids),
            "overlap":len(event_ids & pred_ids),
            "events_in_exact_window":in_window,
            "field_stats":field_stats,"samples":samples
        })
    except Exception as e:
        return jsonify({"error":type(e).__name__+": "+str(e)}),502

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
        # v1 is the endpoint currently used by the app. Important: v1 uses
        # page-number pagination (not limit/offset) and supports date_from/date_to.
        # For historical searches, request the relevant calendar dates directly
        # instead of accidentally reading only the oldest 200 records.
        if days < 0:
            utc_now = datetime.now(timezone.utc)
            hist_start = utc_now + timedelta(days=days)
            hist_end = utc_now
            date_from = hist_start.date().isoformat()
            date_to = hist_end.date().isoformat()
            event_path = f"/events/?upcoming=false&date_from={date_from}&date_to={date_to}"
            pred_path = f"/predictions/?upcoming=false&date_from={date_from}&date_to={date_to}"
        else:
            event_path = "/events/?limit=200"
            pred_path = "/predictions/?limit=200"
        events=all_results(event_path,key)
        predictions=all_results(pred_path,key)
        # Historical prediction records can contain their own embedded event.
        # Merge those embedded events so past matches are not lost if the
        # generic events endpoint only exposes a recent slice.
        if days < 0:
            by_embedded={}
            for p in predictions:
                pe=p.get("event")
                if isinstance(pe,dict) and eid(pe) is not None:
                    by_embedded[str(eid(pe))]=pe
            merged={str(eid(e)):e for e in events if eid(e) is not None}
            for k,e in by_embedded.items():
                if k not in merged: merged[k]=e
            events=list(merged.values())
    except requests.HTTPError as e:
        s=e.response.status_code if e.response is not None else 502
        return jsonify({"error":f"Bzzoiro HTTP {s}"}),s
    except Exception as e:return jsonify({"error":f"Eroare API: {e}"}),502

    now=datetime.now(timezone.utc)
    # Date window rules:
    # 0  = calendar day today (Romania time), 00:00-23:59:59
    # >0 = next N*24 hours from the moment of search
    # <0 = previous N*24 hours from the moment of search
    if days == 0:
        local_now=now.astimezone(ZoneInfo("Europe/Bucharest"))
        local_start=local_now.replace(hour=0,minute=0,second=0,microsecond=0)
        local_end=local_start+timedelta(days=1)
        start=local_start.astimezone(timezone.utc)
        end=local_end.astimezone(timezone.utc)
    elif days > 0:
        start=now
        end=now+timedelta(days=days)
    else:
        start=now+timedelta(days=days)
        end=now

    ev=[]
    for e in events:
        d=dt(e)
        if not d or not (start <= d < end):
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
