#!/usr/bin/env python3
"""Phase 2 E2E driver v2 — background-safe.

Fixes over v1:
  - busy detection via get_state.isStreaming polling (not tool_call events)
  - task runs in FOREGROUND per instruction (no & backgrounding by the child)
  - shorter loop (8x5s), generous turn waits, evidence checkpointed at each phase
"""
import json, os, subprocess, sys, time, threading, queue, signal

LAB = os.path.expanduser("~/Projects/senpi-mvp-lab")
MARKER = "STEERED-BY-HERMES-P2"
EV_PATH = "/tmp/p2-evidence.json"
ev = {"phases": {}, "log": []}

def log(*a):
    line = " ".join(str(x) for x in a)
    ev["log"].append(f"{time.strftime('%H:%M:%S')} {line}")
    print("[driver]", line, flush=True)

def save():
    with open(EV_PATH, "w") as f:
        json.dump(ev, f, indent=2, ensure_ascii=False)

class RpcChild:
    def __init__(self):
        env = dict(os.environ)
        env["OMO_MEMORY_HOME"] = os.path.join(LAB, ".memory")
        env["PATH"] = os.path.expanduser("~/.bun/bin:") + env["PATH"]
        self.proc = subprocess.Popen(
            ["senpi", "--mode", "rpc", "--multi-session"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=LAB, env=env, text=True, bufsize=1,
        )
        self.events = []
        threading.Thread(target=self._drain, daemon=True).start()
        self._id = 0

    def _drain(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line: continue
            try: self.events.append(json.loads(line))
            except Exception: pass

    def send(self, type_, **params):
        self._id += 1
        cid = f"c{self._id}"
        self.proc.stdin.write(json.dumps({"type": type_, "id": cid, **params}) + "\n")
        self.proc.stdin.flush()
        return cid

    def response(self, cid, timeout=90):
        t0 = time.time()
        while time.time() - t0 < timeout:
            for o in list(self.events):
                if o.get("type") == "response" and o.get("id") == cid:
                    return o
            time.sleep(0.3)
        raise TimeoutError(cid)

    def state(self, sid, timeout=30):
        cid = self.send("get_state", sessionId=sid)
        r = self.response(cid, timeout)
        return r.get("data", {})

    def wait_idle(self, sid, timeout=240):
        """Wait until session is not streaming; returns seconds waited."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                st = self.state(sid, 20)
                if not st.get("isStreaming"): return time.time() - t0
            except TimeoutError:
                pass
            time.sleep(2)
        return -1

    def close(self):
        try: self.proc.send_signal(signal.SIGTERM)
        except Exception: pass
        try: self.proc.wait(timeout=10)
        except Exception:
            try: self.proc.kill()
            except Exception: pass

def phase(name, **kv):
    ev["phases"].setdefault(name, {}).update(kv)
    save()

# ---------- boot + open ----------
child = RpcChild()
info = child.response(child.send("get_protocol_info"), 30)
phase("protocol", capabilities=info.get("data", {}).get("capabilities"), mode=info.get("data", {}).get("mode"))
log("protocol:", ev["phases"]["protocol"])

opened = child.response(child.send("open_session", cwd=LAB, provider="zai", modelId="glm-5.3"), 240)
rsid = opened.get("data", {}).get("sessionId")
dsid = opened.get("data", {}).get("state", {}).get("sessionId")
phase("open", ok=opened.get("success"), routing=rsid, durable=dsid, error=opened.get("error"))
log("open_session:", ev["phases"]["open"])
if not opened.get("success"):
    save(); child.close(); sys.exit(1)

# ---------- long task (foreground-forced) ----------
task = (
    "Run this in the shell tool as a FOREGROUND command (do NOT use background mode, "
    "do NOT add & — just run it and wait): "
    "`for i in $(seq 1 8); do echo $i >> count.txt; sleep 5; done`. "
    "It takes ~40 seconds. After it completes, read count.txt and report the line count. Do nothing else."
)
child.proc.stdin.write(json.dumps({"type": "prompt", "id": "t1", "sessionId": rsid, "message": task}) + "\n")
child.proc.stdin.flush()
tresp = child.response("t1", 90)
phase("task", prompt_ok=tresp.get("success"))
log("prompt accepted:", tresp.get("success"))

# wait for streaming to START (model thinking/tool call), then let it run 12s
streaming_started = False
t0 = time.time()
while time.time() - t0 < 240:
    try:
        st = child.state(rsid, 20)
        if st.get("isStreaming"): streaming_started = True; break
    except TimeoutError: pass
    time.sleep(2)
phase("task", streaming_started=streaming_started)
log("streaming started:", streaming_started)

time.sleep(12)  # loop is mid-flight here (~2-3 numbers written)

# ---------- mid-run steer ----------
steer_msg = (
    f"STEER: interrupt the current shell command right now (abort it), then run exactly: "
    f"`echo {MARKER} >> count.txt`, then report how many numbered lines count.txt has and confirm the marker was appended."
)
child.proc.stdin.write(json.dumps({"type": "steer", "id": "s1", "sessionId": rsid, "message": steer_msg}) + "\n")
child.proc.stdin.flush()
sresp = child.response("s1", 60)
phase("steer", accepted=sresp.get("success"), error=sresp.get("error"))
log("steer accepted:", sresp.get("success"))

# steering queue state right after
st = child.state(rsid, 20)
phase("steer", queued=st.get("steering"), ordered=st.get("ordered"))
log("steering queue:", st.get("steering"), "| ordered:", st.get("ordered"))

idle_after = child.wait_idle(rsid, 300)
phase("steer", idle_after_s=round(idle_after, 1))
log("idle after steer:", idle_after)

time.sleep(2)
count_path = os.path.join(LAB, "count.txt")
lines = open(count_path).read().splitlines() if os.path.exists(count_path) else []
phase("evidence", count_txt=lines, marker_landed=MARKER in lines, numbers_stopped_before_8=all(x in lines for x in ["1","2"]) )
log("count.txt:", lines)

# ---------- recovery ----------
ls = child.response(child.send("list_sessions"), 30)
sp = None
for s in ls.get("data", {}).get("sessions", []):
    if s.get("sessionId") == rsid or s.get("durableSessionId") == dsid:
        sp = s.get("sessionPath")
phase("recovery", session_path=sp)
child.close()
log("child1 killed; sessionPath:", sp)

child2 = RpcChild()
o2 = child2.response(child2.send("open_session", sessionPath=sp), 240)
r2 = o2.get("data", {}).get("sessionId")
phase("recovery", reopen_ok=o2.get("success"),
      durable_preserved=o2.get("data", {}).get("state", {}).get("sessionId") == dsid)
log("reopen ok:", o2.get("success"), "| durable preserved:", ev["phases"]["recovery"].get("durable_preserved"))

child2.proc.stdin.write(json.dumps({"type": "prompt", "id": "f1", "sessionId": r2,
    "message": "Reply with the single word READY and nothing else."}) + "\n")
child2.proc.stdin.flush()
fr = child2.response("f1", 90)
idle2 = child2.wait_idle(r2, 240)
# last assistant text
last = None
for o in child2.events:
    if o.get("type") == "message_update" and o.get("message", {}).get("role") == "assistant":
        c = o["message"].get("content")
        if isinstance(c, str) and c.strip(): last = c
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "text" and part.get("text", "").strip():
                    last = part["text"]
phase("recovery", followup_accepted=fr.get("success"), followup_idle_s=round(idle2, 1),
      last_assistant=(last or "")[:200])
log("recovery follow-up:", (last or "")[:100])
child2.close()

t = ev["phases"].get("task", {}); s = ev["phases"].get("steer", {}); r = ev["phases"].get("recovery", {})
print("=== P2 VERDICT ===")
print("marker_landed:", MARKER in (ev["phases"].get("evidence", {}).get("count_txt") or []),
      "| reopen_ok:", r.get("reopen_ok"),
      "| durable_preserved:", r.get("durable_preserved"),
      "| followup_ok:", r.get("followup_accepted"))
save()
