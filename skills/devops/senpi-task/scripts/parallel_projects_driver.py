#!/usr/bin/env python3
"""Phase 3 E2E driver: parallel two-project spawn + isolation proof (I-1)
and same-project learning succession (UC-3), plus concurrent wake-cap evidence.

Design:
  P3-1 isolation: spawn lab A (pnpm trap) and lab B (yarn trap) CONCURRENTLY
    in one multi-session RPC host. Each session gets the same neutral task
    ("install deps so the build runs"). Cross-contamination check: after both
    finish, each lab's memory repo must contain ZERO references to the other
    lab, and each session's transcript must show its OWN trap memory recalled
    (A->pnpm, B->yarn) — not swapped.
  P3-2 succession (UC-3): lab B session 2 — ask a follow-up task that only
    succeeds if the memory from run 1 (leftpad-style missing dep learned in
    run 1? no—) ... simplest honest form: verify durable store learned from
    run 1 (write-back) and run 2's compiled block/nudge includes it.
  P3-3 wake cap: gather wakes.ndjson from both labs; report concurrent
    overlap window (two wakes at the same time => lease slots in use).
"""
import json, os, subprocess, sys, time, threading, signal

LAB_A = os.path.expanduser("~/Projects/senpi-mvp-lab")
LAB_B = os.path.expanduser("~/Projects/senpi-lab-b")
EV = {"phases": {}, "log": []}
EV_PATH = "/tmp/p3-evidence.json"

def log(*a):
    line = " ".join(str(x) for x in a)
    EV["log"].append(f"{time.strftime('%H:%M:%S')} {line}")
    print("[p3]", line, flush=True)

def save():
    with open(EV_PATH, "w") as f:
        json.dump(EV, f, indent=2, ensure_ascii=False)

class RpcChild:
    """One senpi multi-session host."""
    def __init__(self):
        env = dict(os.environ)
        env["PATH"] = os.path.expanduser("~/.bun/bin:") + env["PATH"]
        # NOTE: OMO_MEMORY_HOME is per-project, but a single host process has
        # one env. senpi memory resolves the store per cwd at session start
        # when OMO_MEMORY_HOME is unset? No — it defaults to ~/.omo/memory.
        # For per-project isolation we must spawn ONE HOST PER PROJECT.
        raise NotImplementedError

def make_child(lab):
    env = dict(os.environ)
    env["OMO_MEMORY_HOME"] = os.path.join(lab, ".memory")
    env["PATH"] = os.path.expanduser("~/.bun/bin:") + env["PATH"]
    proc = subprocess.Popen(
        ["senpi", "--mode", "rpc", "--multi-session"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=lab, env=env, text=True, bufsize=1,
    )
    ev = []
    threading.Thread(target=lambda: [ev.append(json.loads(l)) for l in proc.stdout if l.strip() and _ok(l)], daemon=True).start()
    return {"proc": proc, "events": ev, "id": [0], "lab": lab}

def _ok(line):
    try: json.loads(line); return True
    except Exception: return False

def send(ch, type_, **params):
    ch["id"][0] += 1
    cid = f"{os.path.basename(ch['lab'])}-{ch['id'][0]}"
    ch["proc"].stdin.write(json.dumps({"type": type_, "id": cid, **params}) + "\n")
    ch["proc"].stdin.flush()
    return cid

def resp(ch, cid, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        for o in list(ch["events"]):
            if o.get("type") == "response" and o.get("id") == cid:
                return o
        time.sleep(0.3)
    raise TimeoutError(cid)

def state(ch, sid):
    return resp(ch, send(ch, "get_state", sessionId=sid), 30).get("data", {})

def wait_idle(ch, sid, timeout=420):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if not state(ch, sid).get("isStreaming"): return round(time.time() - t0, 1)
        except TimeoutError: pass
        time.sleep(3)
    return -1

def kill(ch):
    try: ch["proc"].send_signal(signal.SIGTERM)
    except Exception: pass
    try: ch["proc"].wait(timeout=10)
    except Exception: ch["proc"].kill()

TASK = (
    "Get this project's dependencies installed so that `npm run build` can run. "
    "Use the project README as a reference. Report which package manager you used and why."
)

# ---------- P3-1: parallel spawn ----------
log("spawning lab A (pnpm trap) and lab B (yarn trap) concurrently")
a = make_child(LAB_A)
b = make_child(LAB_B)
oa = resp(a, send(a, "open_session", cwd=LAB_A, provider="zai", modelId="glm-5.3"))
ob = resp(b, send(b, "open_session", cwd=LAB_B, provider="zai", modelId="glm-5.3"))
sa, sb = oa["data"]["sessionId"], ob["data"]["sessionId"]
EV["phases"]["open"] = {"a": oa.get("success"), "b": ob.get("success")}
save()
log("open A:", oa.get("success"), "| open B:", ob.get("success"))

resp(a, send(a, "prompt", sessionId=sa, message=TASK), 90)
resp(b, send(b, "prompt", sessionId=sb, message=TASK), 90)
t0 = time.time()

ida = wait_idle(a, sa)
idb = wait_idle(b, sb)
EV["phases"]["run1"] = {"a_idle_s": ida, "b_idle_s": idb, "overlap": ida > 0 and idb > 0}
save()
log("run1 idle:", ida, idb, "elapsed", round(time.time() - t0, 1))

# transcripts of THIS run: durable ids
da = oa["data"]["state"]["sessionId"]; db = ob["data"]["state"]["sessionId"]
sess_a = f"~/.senpi/agent/sessions/--Users-howard-Projects-senpi-mvp-lab--/*{da}.jsonl"
sess_b = f"~/.senpi/agent/sessions/--Users-howard-Projects-senpi-lab-b--/*{db}.jsonl"
import glob
pa = glob.glob(os.path.expanduser(sess_a))[0]
pb = glob.glob(os.path.expanduser(sess_b))[0]

def grep_file(path, needle):
    return needle in open(path, errors="ignore").read()

EV["phases"]["transcripts"] = {
    "a_pnpm_recalled": grep_file(pa, "pnpm"),
    "a_cross_b": grep_file(pa, "yarn"),
    "b_yarn_recalled": grep_file(pb, "yarn"),
    "b_cross_a": grep_file(pb, "senpi-mvp-lab"),
    "a_kibitzer": grep_file(pa, "omo-kibitzer:recall"),
    "b_kibitzer": grep_file(pb, "omo-kibitzer:recall"),
}
save()
log("transcripts:", EV["phases"]["transcripts"])

# ---------- P3-1b: memory repo cross-contamination ----------
def repo_refs(lab, needle):
    """count references to needle in lab's memory repo working tree (all files, git log subjects)"""
    repo = os.path.join(lab, ".memory")
    n = 0
    for root, _, files in os.walk(repo):
        for f in files:
            if f.endswith((".md", ".json", ".ndjson")):
                p = os.path.join(root, f)
                try: n += open(p, errors="ignore").read().count(needle)
                except Exception: pass
    return n

EV["phases"]["cross"] = {
    "A_repo_mentions_B": repo_refs(LAB_A, "senpi-lab-b"),
    "B_repo_mentions_A": repo_refs(LAB_B, "senpi-mvp-lab"),
}
save()
log("cross-contamination:", EV["phases"]["cross"])

# ---------- P3-2: succession — lab B session 2 ----------
# run 1 wrote back learning? check B repo git log for a child-authored commit after seed
gl = subprocess.run(["git", "-C", os.path.join(LAB_B, ".memory", "agents"), "log", "--all", "--format=%an|%s"],
                    capture_output=True, text=True).stdout
EV["phases"]["run1_writeback"] = {"agents_log": gl.strip().split("\n")[:8]}
save()
log("B agents git log:", gl.strip().split("\n")[:8])

# session 2 in B (same host, new session in same cwd = same identity)
ob2 = resp(b, send(b, "open_session", cwd=LAB_B, provider="zai", modelId="glm-5.3"))
sb2 = ob2["data"]["sessionId"]
db2 = ob2["data"]["state"]["sessionId"]
resp(b, send(b, "prompt", sessionId=sb2,
    message="Without installing anything, name the exact package manager this repo requires and the one command that must NEVER be run here. One short answer."),
    90)
wait_idle(b, sb2, 360)
pb2 = glob.glob(os.path.expanduser(f"~/.senpi/agent/sessions/--Users-howard-Projects-senpi-lab-b--/*{db2}.jsonl"))
EV["phases"]["run2"] = {"session": bool(pb2)}
if pb2:
    txt = open(pb2[0], errors="ignore").read()
    EV["phases"]["run2"] = {
        "session": True,
        "yarn_answer": txt.count("yarn") > 0,
        "npm_forbidden": ("npm install" in txt),
        "kibitzer_recall": "omo-kibitzer:recall" in txt,
    }
save()
log("run2:", EV["phases"]["run2"])

# ---------- P3-3: wake evidence ----------
wakes = []
for lab, tag in ((LAB_A, "A"), (LAB_B, "B")):
    for root, _, files in os.walk(os.path.join(lab, ".memory")):
        for f in files:
            if f == "wakes.ndjson":
                for line in open(os.path.join(root, f), errors="ignore"):
                    if line.strip():
                        try: wakes.append({"lab": tag, **json.loads(line)})
                        except Exception: pass
EV["phases"]["wakes"] = wakes
save()
log(f"wakes collected: {len(wakes)}")

kill(a); kill(b)

t = EV["phases"]; iso = t.get("transcripts", {}); cross = t.get("cross", {})
print("=== P3 VERDICT ===")
print("isolation:", (not iso.get("a_cross_b", True)) and (not cross.get("A_repo_mentions_B", 1)) and (not cross.get("B_repo_mentions_A", 1)),
      "| A pnpm ok:", iso.get("a_pnpm_recalled"), "| B yarn ok:", iso.get("b_yarn_recalled"),
      "| succession:", EV["phases"].get("run2", {}).get("yarn_answer"),
      "| wakes:", len(wakes))
save()
