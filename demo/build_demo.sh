#!/usr/bin/env bash
# Build the wl demo recording end-to-end, in ONE shot:
#   1. seed a de-identified demo "day" into the throwaway wlt test DB
#   2. generate an asciinema .cast (bright-yellow # comments, fast pacing)
#   3. convert it to a gif with agg
#   4. force the last frame to hold 8s so the closing comments are readable
# Safe: only touches ~/.local/share/wl-test, never the real worklog DB.
# Usage:  bash demo/build_demo.sh    then look at demo/wl_demo.gif
set -euo pipefail
cd "$(dirname "$0")"

python3 <<'PYEOF'
import json, subprocess, os, re, datetime, sqlite3
HOME=os.path.expanduser("~")
DB=f"{HOME}/.local/share/wl-test/test.db"
WL=[f"{HOME}/projects/worklog/.venv/bin/wl","--db",DB]
env=dict(os.environ, WORKLOG_COLOR="always", WORKLOG_WIDTH="88", COLUMNS="88", LINES="40")

def wl(*a): return subprocess.run(WL+list(a),capture_output=True,text=True,env=env).stdout
def addid(*a):
    o=subprocess.run(WL+["add",*a,"-o","json"],capture_output=True,text=True,env=env).stdout
    return str(json.loads(o)["id"])

# fresh throwaway DB
os.system(f"rm -rf {DB}* {HOME}/.local/share/wl-test/test.lancedb 2>/dev/null")
wl("init")
REP=addid("Ship the monthly report","--para","project","-p","A","-t","work")
AI =addid("Learn how AI agents work","--para","project","-p","A","-t","personal")

events=[]; t=[0.0]
def emit(text,dt):
    t[0]+=dt; events.append([round(t[0],3),"o",text.replace("\n","\r\n")])
def say(s): emit(f"\x1b[93m# {s}\x1b[0m\n",0.9)          # bright yellow, fast
# READ=1.8s pause BEFORE a command line appears (time to read whatever came before).
# RUN=0.1s pause between the command line and its output (execution is instant).
READ=1.8; RUN=0.1
def cmd(disp,*a):
    emit(f"\x1b[36m$ wl {disp}\x1b[0m\n",READ); emit(wl(*a)+"\n",RUN)
def add_show(disp,*a):
    emit(f"\x1b[36m$ wl add {disp}\x1b[0m\n",READ)
    out=wl("add",*a); emit(out+"\n",RUN)
    m=re.search(r"#(\d+)",out); return m.group(1) if m else None

say("A day with worklog (wl) - work, learning and life in one tool.")
say("Morning: line up the few things I'll actually do today.")
SUM=add_show('"Write the report summary" --sched today',"Write the report summary","--parent",REP,"--sched","today","-t","work")
add_show('"Do the AI-agents tutorial" --sched today',"Do the AI-agents tutorial","--parent",AI,"--sched","today","-t","personal")
say("Name today's ONE focus - point it at the task that delivers it.")
cmd(f'goal today "Send out the monthly report" {SUM}',"goal","today","Send out the monthly report",SUM)
say("The whole day on one screen: focus on top with its target, plan below.")
cmd("day","day")
say("Made progress - log it, then mark it done.")
cmd(f'log {SUM} "Draft written, sent to the team"',"log",SUM,"Draft written, sent to the team")
cmd(f"done {SUM}","done",SUM)
say("A new idea pops up while working. Don't derail today - push it out.")
add_show('"Build a tiny agent myself" --sched +3d',"Build a tiny agent myself","--parent",AI,"--sched","+3d","-t","personal")
cmd("agenda today +5d","agenda","today","+5d")
say("Evening: recap the day...")
cmd('recap "Summary sent; started the AI tutorial; ran 3km"',"recap","Summary sent; started the AI tutorial; ran 3km")
say("...and set tomorrow's focus, so the morning starts clear.")
today=datetime.date.today().isoformat(); tom=(datetime.date.today()+datetime.timedelta(days=1)).isoformat()
con=sqlite3.connect(DB); row=con.execute("SELECT parent_id FROM node WHERE title=? AND deleted_at IS NULL LIMIT 1",(today,)).fetchone(); con.close()
if row:
    dtom=addid(tom,"--prop","type.date=day","--parent",str(row[0]))
    emit('\x1b[36m$ wl goal set tomorrow "Get the report signed off"\x1b[0m\n',READ)
    emit(wl("goal","set",dtom,"Get the report signed off")+"\n",RUN)
cmd("day","day")
say("^ Focus done: [1/1] up top, linked to the task that delivered it.")
say("^ The reading is still going; the new idea is parked at +3 days.")
say("^ Nothing got lost, today never got derailed -")
say("^ and tomorrow already has its focus. That's the whole point.")
emit('\x1b[36m$ sleep 5 & exit\x1b[0m\n',0.3)
emit("\r\n",1.0)

with open("wl_demo.cast","w") as f:
    f.write(json.dumps({"version":2,"width":88,"height":40})+"\n")
    for e in events: f.write(json.dumps(e)+"\n")
print(f"[cast] {len(events)} events, {round(t[0],1)}s")
PYEOF

agg --font-size 15 --speed 1.0 --idle-time-limit 15 wl_demo.cast wl_demo.gif

python3 <<'PYEOF'
from PIL import Image
p = "wl_demo.gif"
im = Image.open(p)
frames = []; durs = []
for i in range(im.n_frames):
    im.seek(i)
    frames.append(im.convert("RGB").copy())
    durs.append(im.info.get("duration", 100))
durs[-1] = 8000  # hold the closing frame 8s so the comments are readable
frames[0].save(p, save_all=True, append_images=frames[1:], duration=durs, loop=0)
print(f"[gif]  {len(frames)} frames, last frame held {durs[-1]}ms")
PYEOF
echo "[done] demo/wl_demo.gif"
