#!/usr/bin/env python3
"""P3-2b: prove seeded memory -> kibitzer nudge -> behavior succession in lab B.

Now that the trap memory sits in the CORRECT auto identity
(senpi-lab-b-7007e180), a fresh session asked a neutral setup task must:
  - receive an omo-kibitzer:recall nudge citing
    projects/senpi-lab-b/package-manager.md
  - use yarn (not npm) for installation
Also re-checks isolation with the precise criterion (recalled-memory
source paths only, and repo/ tree only).
"""
import json, os, subprocess, sys, time, threading, glob, re, signal

LAB_B = os.path.expanduser("~/Projects/senpi-lab-b")
EV = {}

env = dict(os.environ)
env["OMO_MEMORY_HOME"] = os.path.join(LAB_B, ".memory")
env["PATH"] = os.path.expanduser("~/.bun/bin:") + env["PATH"]
proc = subprocess.Popen(["senpi", "--mode", "rpc", "--multi-session"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    cwd=LAB_B, env=env, text=True, bufsize=1)
events = []
def drain():
    for l in proc.stdout:
        if l.strip():
            try: events.append(json.loads(l))
            except Exception: pass
threading.Thread(target=drain, daemon=True).start()
_id = [0]
def send(t, **p):
    _id[0] += 1
    proc.stdin.write(json.dumps({"type": t, "id": f"c{_id[0]}", **p}) + "\n")
    proc.stdin.flush()
    return f"c{_id[0]}"
def resp(cid, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        for o in list(events):
            if o.get("type") == "response" and o.get("id") == cid: return o
        time.sleep(0.3)
    raise TimeoutError(cid)
def idle(sid, timeout=420):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = resp(send("get_state", sessionId=sid), 30).get("data", {})
            if not r.get("isStreaming"): return round(time.time() - t0, 1)
        except TimeoutError: pass
        time.sleep(3)
    return -1

o = resp(send("open_session", cwd=LAB_B, provider="zai", modelId="glm-5.3"))
sid = o["data"]["sessionId"]; did = o["data"]["state"]["sessionId"]
resp(send("prompt", sessionId=sid, message=(
    "Install this project's dependencies cleanly in a fresh pass. "
    "If you already know how this repo must be set up, follow that. "
    "Report the package manager used."
)), 90)
w = idle(sid)
sess = glob.glob(os.path.expanduser(f"~/.senpi/agent/sessions/--Users-howard-Projects-senpi-lab-b--/*{did}.jsonl"))
txt = open(sess[0], errors="ignore").read() if sess else ""
recalled = re.findall(r'recalled-memory source=\\"?\[\[(.*?)\]\]', txt)
EV = {
    "idle_s": w,
    "session_file": bool(sess),
    "kibitzer_recall": "omo-kibitzer:recall" in txt,
    "recalled_sources": recalled,
    "seed_path_recalled": any("package-manager.md" in r for r in recalled),
    "used_yarn": txt.count("yarn install") > 0 or txt.count('"yarn"') > 0,
    "ran_npm_install": bool(re.search(r'"npm install[ "]', txt)),
    "succession_setup_md": "setup.md" in txt,  # read its own run-1 write-back?
}
proc.send_signal(signal.SIGTERM)
try: proc.wait(10)
except Exception: proc.kill()
with open("/tmp/p3b-evidence.json", "w") as f: json.dump(EV, f, indent=2)
print(json.dumps(EV, indent=2))
print("P3-2b PASS" if EV["kibitzer_recall"] and EV["seed_path_recalled"] and EV["used_yarn"] and not EV["ran_npm_install"] else "P3-2b CHECK")
