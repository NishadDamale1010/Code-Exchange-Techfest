from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import Dict
import asyncio, subprocess, os, time, sqlite3, uuid, json
from fastapi.middleware.cors import CORSMiddleware

TOTAL_TEAMS = 10


def init_db():
    conn = sqlite3.connect('leaderboard.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS scores 
                      (team_id TEXT, problem_title TEXT, points INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS drafts 
                      (team_id TEXT, player_id TEXT, code TEXT, language TEXT, PRIMARY KEY (team_id, player_id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS assignments 
                       (team_id TEXT PRIMARY KEY, problem_id TEXT)''')
    conn.commit()
    conn.close()

init_db()
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class ConnectionManager:
    def __init__(self):
        self.teams: Dict[str, Dict[str, WebSocket]] = {}
        self.last_seen: Dict[str, float] = {}
        self.timer_tasks: Dict[str, asyncio.Task] = {}
        self.time_left: Dict[str, int] = {}

    async def connect(self, websocket: WebSocket, team_id: str, player_id: str):
        await websocket.accept()
        if team_id not in self.teams: self.teams[team_id] = {}
        self.teams[team_id][player_id] = websocket
        self.last_seen[f"{team_id}_{player_id}"] = time.time()

    def disconnect(self, team_id: str, player_id: str):
        if team_id in self.teams and player_id in self.teams[team_id]:
            del self.teams[team_id][player_id]

    def is_online(self, team_id, player_id):
        return (time.time() - self.last_seen.get(f"{team_id}_{player_id}", 0)) < 60

    async def broadcast_to_team(self, team_id: str, message: dict):
        if team_id in self.teams:
            for conn in list(self.teams[team_id].values()):
                try: await conn.send_json(message)
                except: pass

    async def start_round(self, team_id: str, duration: int):
        try:
            for i in range(duration, -1, -1):
                self.time_left[team_id] = i
                await self.broadcast_to_team(team_id, {"type": "TIME_UPDATE", "seconds": i})
                await asyncio.sleep(1)
            await self.broadcast_to_team(team_id, {"type": "PHASE_END"})
        except asyncio.CancelledError:
            await self.broadcast_to_team(team_id, {"type": "STOP_TIMER"})

manager = ConnectionManager()

def execute_code(code, input_data, lang):
    uid = str(uuid.uuid4())[:8]
    try:
        if lang == "python":
            res = subprocess.run(['python', '-c', f"import sys; {code}"], input=input_data, capture_output=True, text=True, timeout=2)
        else:
            with open(f"{uid}.cpp", "w") as f: f.write(code)
            comp = subprocess.run(['g++', f"{uid}.cpp", '-o', f"{uid}.exe"], capture_output=True, text=True)
            if comp.returncode != 0: return None, comp.stderr
            res = subprocess.run([f"./{uid}.exe" if os.name != 'nt' else f"{uid}.exe"], input=input_data, capture_output=True, text=True, timeout=2)
        return res.stdout.strip(), None
    except Exception as e: return None, str(e)
    finally:
        for f in [f"{uid}.cpp", f"{uid}.exe", f"{uid}.exe.stackdump"]:
            if os.path.exists(f): os.remove(f)

def load_pool():
    path = "rounds/pool.json"
    if os.path.exists(path):
        with open(path, "r") as f: return json.load(f)
    return {"problems": []}

def get_assigned_problem(team_id: str):
    conn = sqlite3.connect('leaderboard.db'); cur = conn.cursor()
    cur.execute("SELECT problem_id FROM assignments WHERE team_id = ?", (team_id,))
    row = cur.fetchone(); conn.close()
    if not row: return None
    pool = load_pool()
    return next((p for p in pool["problems"] if p["id"] == row[0]), None)

@app.websocket("/ws/{tid}/{pid}")
async def websocket_endpoint(websocket: WebSocket, tid: str, pid: str):
    await manager.connect(websocket, tid, pid)
    try:
        while True:
            data = await websocket.receive_json()
            manager.last_seen[f"{tid}_{pid}"] = time.time()
            if data.get("type") == "SWAP":
                await manager.broadcast_to_team(tid, {"type": "INCOMING_CODE", "code": data["code"]})
            elif data.get("type") == "LANG_CHANGE":
                await manager.broadcast_to_team(tid, {"type": "LOCK_LANG", "lang": data["lang"]})
    except WebSocketDisconnect: manager.disconnect(tid, pid)

@app.post("/run/{tid}/{pid}")
async def run_logic(tid: str, pid: str, data: dict):
    p = get_assigned_problem(tid)
    test = p["test_cases"].get(data['problem_title'])
    out, err = execute_code(data['code'], test["input"], data['language'])
    if err: return {"status": "Fail", "message": err}
    return {"status": "Success", "message": f"Output: {out}"} if out == test["expected"] else {"status": "Fail", "message": f"Wrong: {out}"}

@app.post("/submit/{tid}/{pid}")
async def submit(tid: str, pid: str, data: dict):
    p = get_assigned_problem(tid)
    test = p["test_cases"].get(data['problem_title'])
    out, err = execute_code(data['code'], test["input"], data['language'])
    if out == test["expected"]:
        bonus = (manager.time_left.get(tid, 0) // 60) * 10 
        total = p['points'] + bonus
        conn = sqlite3.connect('leaderboard.db'); cur = conn.cursor()
        cur.execute("INSERT INTO scores (team_id, problem_title, points) VALUES (?, ?, ?)", (tid, data['problem_title'], total))
        conn.commit(); conn.close()
        return {"status": "Success", "message": f"Correct! +{total} pts"}
    return {"status": "Fail", "message": "Wrong Answer."}

@app.get("/api/problem/current/{tid}/{pid}")
async def get_current(tid, pid):
    p = get_assigned_problem(tid)
    return {"title": p[f"{pid}_setup"]["title"], "desc": p[f"{pid}_setup"]["desc"], "hint": p.get("hint")} if p else {"title": "None", "desc": "Wait for Admin."}

@app.get("/api/problem/switch/{tid}/{pid}")
async def get_switch(tid, pid):
    p = get_assigned_problem(tid)
    if not p: return {"error": "None"}
    setup_key = f"{pid}_setup"
    switch_key = f"{pid}_switch"
    
    return {
        "title": p[switch_key]["title"], 
        "desc": p[switch_key]["desc"], 
        "partner_goal_recap": p[switch_key].get("partner_goal_recap"),
        "part_a_title": p[setup_key]["title"], 
        "part_a_desc": p[setup_key]["desc"]    
    }
@app.get("/api/problem/pool_list")
async def pool_list():
    p = load_pool()
    return [{"id": x["id"], "title": x["p1_setup"]["title"]} for x in p["problems"]]

@app.post("/api/admin/assign/{tid}/{pid}")
async def admin_assign(tid, pid):
    conn = sqlite3.connect('leaderboard.db'); cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO assignments (team_id, problem_id) VALUES (?, ?)", (tid, pid))
    conn.commit(); conn.close()
    await manager.broadcast_to_team(tid, {"type": "NEW_ASSIGNMENT"})
    return {"status": "ok"}

@app.post("/api/admin/penalty/{tid}")
async def admin_penalty(tid, amount: int = 20):
    conn = sqlite3.connect('leaderboard.db'); cur = conn.cursor()
    cur.execute("INSERT INTO scores (team_id, problem_title, points) VALUES (?, 'PENALTY', ?)", (tid, -amount))
    conn.commit(); conn.close(); return {"status": "ok"}

@app.post("/api/admin/start-all")
async def start_all():
    for tid in range(1, 11):
        tid_str = str(tid)
        if tid_str in manager.timer_tasks: manager.timer_tasks[tid_str].cancel()
        manager.timer_tasks[tid_str] = asyncio.create_task(manager.start_round(tid_str, 600))
    return {"status": "ok"}

@app.post("/api/admin/force-swap/{tid}")
async def force_swap(tid):
    await manager.broadcast_to_team(tid, {"type": "PHASE_END"})
    return {"status": "ok"}

@app.post("/api/admin/reset-db")
async def reset_db():
    for t in manager.timer_tasks.values(): t.cancel()
    manager.timer_tasks = {}; manager.time_left = {}
    conn = sqlite3.connect('leaderboard.db'); cur = conn.cursor()
    cur.execute("DELETE FROM scores"); cur.execute("DELETE FROM drafts"); cur.execute("DELETE FROM assignments")
    conn.commit(); conn.close(); return {"status": "ok"}

@app.get("/api/leaderboard")
async def get_lb():
    conn = sqlite3.connect('leaderboard.db'); cur = conn.cursor()
    cur.execute("SELECT team_id, SUM(points) FROM scores GROUP BY team_id ORDER BY SUM(points) DESC")
    rows = cur.fetchall(); conn.close(); return [{"team": r[0], "score": r[1] or 0} for r in rows]

@app.get("/api/admin/status")
async def get_status():
    conn = sqlite3.connect('leaderboard.db'); cur = conn.cursor()
    cur.execute("SELECT team_id, problem_id FROM assignments"); assigns = dict(cur.fetchall()); conn.close()
    return [{"team_id": str(i), "p1_online": manager.is_online(str(i), "p1"), "p2_online": manager.is_online(str(i), "p2"), "assigned": assigns.get(str(i), "None")} for i in range(1, TOTAL_TEAMS + 1)]

@app.post("/api/save-draft/{tid}/{pid}")
async def save_draft(tid, pid, data: dict):
    conn = sqlite3.connect('leaderboard.db'); cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO drafts (team_id, player_id, code, language) VALUES (?, ?, ?, ?)", (tid, pid, data['code'], data['language']))
    conn.commit(); conn.close(); return {"status": "ok"}

@app.get("/api/get-draft/{tid}/{pid}")
async def get_draft(tid, pid):
    conn = sqlite3.connect('leaderboard.db'); cur = conn.cursor()
    cur.execute("SELECT code, language FROM drafts WHERE team_id=? AND player_id=?", (tid, pid))
    row = cur.fetchone(); conn.close(); return {"code": row[0], "language": row[1]} if row else {"code": None}

@app.post("/api/admin/set-team-count/{count}")
async def set_teams(count: int):
    global TOTAL_TEAMS
    TOTAL_TEAMS = count
    return {"status": "ok", "total_teams": TOTAL_TEAMS}