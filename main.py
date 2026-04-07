from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from typing import Dict
import asyncio, subprocess, os, time, sqlite3, uuid, json, random
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

TOTAL_TEAMS = 10
ROUND_DURATION_SECONDS = 600


class CodeRequest(BaseModel):
    code: str = Field(min_length=1)
    problem_title: str = Field(min_length=1)
    language: str


class DraftRequest(BaseModel):
    code: str
    language: str


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
        if team_id not in self.teams:
            self.teams[team_id] = {}
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
                try:
                    await conn.send_json(message)
                except Exception:
                    pass

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
    py_file = f"{uid}.py"
    cpp_file = f"{uid}.cpp"
    exe_file = f"{uid}.exe"

    try:
        if lang == "python":
            with open(py_file, "w", encoding="utf-8") as f:
                f.write(code)
            res = subprocess.run(['python', py_file], input=input_data, capture_output=True, text=True, timeout=2)
        elif lang == "cpp":
            with open(cpp_file, "w", encoding="utf-8") as f:
                f.write(code)
            comp = subprocess.run(['g++', cpp_file, '-o', exe_file], capture_output=True, text=True)
            if comp.returncode != 0:
                return None, comp.stderr
            res = subprocess.run([f"./{exe_file}" if os.name != 'nt' else exe_file], input=input_data, capture_output=True, text=True, timeout=2)
        else:
            return None, "Unsupported language"

        return res.stdout.strip(), None
    except Exception as e:
        return None, str(e)
    finally:
        for f in [py_file, cpp_file, exe_file, f"{exe_file}.stackdump"]:
            if os.path.exists(f):
                os.remove(f)


def load_pool():
    path = "rounds/pool.json"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"problems": []}


def get_assigned_problem(team_id: str):
    conn = sqlite3.connect('leaderboard.db')
    cur = conn.cursor()
    cur.execute("SELECT problem_id FROM assignments WHERE team_id = ?", (team_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    pool = load_pool()
    return next((p for p in pool["problems"] if p["id"] == row[0]), None)


def get_valid_test_case(team_id: str, problem_title: str):
    problem = get_assigned_problem(team_id)
    if not problem:
        raise HTTPException(status_code=404, detail="No problem assigned for this team")

    test = problem.get("test_cases", {}).get(problem_title)
    if not test:
        raise HTTPException(status_code=400, detail="Unknown problem title for current assignment")
    return problem, test


@app.websocket("/ws/{tid}/{pid}")
async def websocket_endpoint(websocket: WebSocket, tid: str, pid: str):
    await manager.connect(websocket, tid, pid)
    try:
        while True:
            data = await websocket.receive_json()
            manager.last_seen[f"{tid}_{pid}"] = time.time()
            if data.get("type") == "SWAP":
                await manager.broadcast_to_team(tid, {"type": "INCOMING_CODE", "code": data.get("code", "")})
            elif data.get("type") == "LANG_CHANGE":
                await manager.broadcast_to_team(tid, {"type": "LOCK_LANG", "lang": data.get("lang", "python")})
    except WebSocketDisconnect:
        manager.disconnect(tid, pid)


@app.post("/run/{tid}/{pid}")
async def run_logic(tid: str, pid: str, data: CodeRequest):
    _, test = get_valid_test_case(tid, data.problem_title)
    out, err = execute_code(data.code, test["input"], data.language)
    if err:
        return {"status": "Fail", "message": err}
    if out == test["expected"]:
        return {"status": "Success", "message": f"Output: {out}"}
    return {"status": "Fail", "message": f"Wrong: {out}"}


@app.post("/submit/{tid}/{pid}")
async def submit(tid: str, pid: str, data: CodeRequest):
    problem, test = get_valid_test_case(tid, data.problem_title)
    out, err = execute_code(data.code, test["input"], data.language)
    if err:
        return {"status": "Fail", "message": err}

    if out == test["expected"]:
        conn = sqlite3.connect('leaderboard.db')
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM scores WHERE team_id=? AND problem_title=? LIMIT 1", (tid, data.problem_title))
        existing = cur.fetchone()
        if existing:
            conn.close()
            return {"status": "Success", "message": "Already solved. Score unchanged."}

        bonus = (manager.time_left.get(tid, 0) // 60) * 10
        total = problem['points'] + bonus
        cur.execute("INSERT INTO scores (team_id, problem_title, points) VALUES (?, ?, ?)", (tid, data.problem_title, total))
        conn.commit()
        conn.close()
        return {"status": "Success", "message": f"Correct! +{total} pts"}
    return {"status": "Fail", "message": "Wrong Answer."}


@app.get("/api/problem/current/{tid}/{pid}")
async def get_current(tid, pid):
    p = get_assigned_problem(tid)
    if not p:
        return {"title": "None", "desc": "Wait for Admin."}
    setup_key = f"{pid}_setup"
    if setup_key not in p:
        return {"title": "None", "desc": "Invalid player id."}
    return {"title": p[setup_key]["title"], "desc": p[setup_key]["desc"], "hint": p.get("hint")}


@app.get("/api/problem/switch/{tid}/{pid}")
async def get_switch(tid, pid):
    p = get_assigned_problem(tid)
    if not p:
        return {"error": "None"}

    setup_key = f"{pid}_setup"
    switch_key = f"{pid}_switch"
    if setup_key not in p or switch_key not in p:
        return {"error": "Invalid player id"}

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
    return [{"id": x["id"], "title": x["p1_setup"]["title"], "tier": x.get("tier", "unknown")} for x in p["problems"]]


@app.post("/api/admin/assign/{tid}/{problem_id}")
async def admin_assign(tid, problem_id):
    pool = load_pool()
    problem_exists = any(p["id"] == problem_id for p in pool["problems"])
    if not problem_exists:
        raise HTTPException(status_code=404, detail="Problem id not found")

    conn = sqlite3.connect('leaderboard.db')
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO assignments (team_id, problem_id) VALUES (?, ?)", (tid, problem_id))
    conn.commit()
    conn.close()
    await manager.broadcast_to_team(tid, {"type": "NEW_ASSIGNMENT"})
    return {"status": "ok"}


@app.post("/api/admin/randomize-all/{tier}")
async def admin_randomize_all(tier: str):
    pool = load_pool()["problems"]
    tier_pool = [p for p in pool if p.get("tier") == tier]
    if not tier_pool:
        raise HTTPException(status_code=404, detail=f"No problems found for tier '{tier}'")

    conn = sqlite3.connect('leaderboard.db')
    cur = conn.cursor()
    for tid in range(1, TOTAL_TEAMS + 1):
        chosen = random.choice(tier_pool)
        cur.execute("INSERT OR REPLACE INTO assignments (team_id, problem_id) VALUES (?, ?)", (str(tid), chosen["id"]))
    conn.commit()
    conn.close()

    for tid in range(1, TOTAL_TEAMS + 1):
        await manager.broadcast_to_team(str(tid), {"type": "NEW_ASSIGNMENT"})

    return {"status": "ok", "tier": tier, "teams": TOTAL_TEAMS}


@app.post("/api/admin/penalty/{tid}")
async def admin_penalty(tid, amount: int = 20):
    conn = sqlite3.connect('leaderboard.db')
    cur = conn.cursor()
    cur.execute("INSERT INTO scores (team_id, problem_title, points) VALUES (?, 'PENALTY', ?)", (tid, -amount))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.post("/api/hint/{tid}/{pid}")
async def request_hint(tid: str, pid: str):
    p = get_assigned_problem(tid)
    if not p:
        raise HTTPException(status_code=404, detail="No problem assigned")
    await admin_penalty(tid, 20)
    return {"status": "ok", "hint": p.get("hint") or "No hint for this problem."}


@app.post("/api/admin/start-all")
async def start_all():
    for tid in range(1, TOTAL_TEAMS + 1):
        tid_str = str(tid)
        if tid_str in manager.timer_tasks:
            manager.timer_tasks[tid_str].cancel()
        manager.timer_tasks[tid_str] = asyncio.create_task(manager.start_round(tid_str, ROUND_DURATION_SECONDS))
    return {"status": "ok", "teams_started": TOTAL_TEAMS}


@app.post("/api/admin/force-swap/{tid}")
async def force_swap(tid):
    await manager.broadcast_to_team(tid, {"type": "PHASE_END"})
    return {"status": "ok"}


@app.post("/api/admin/reset-db")
async def reset_db():
    for t in manager.timer_tasks.values():
        t.cancel()
    manager.timer_tasks = {}
    manager.time_left = {}
    conn = sqlite3.connect('leaderboard.db')
    cur = conn.cursor()
    cur.execute("DELETE FROM scores")
    cur.execute("DELETE FROM drafts")
    cur.execute("DELETE FROM assignments")
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.get("/api/leaderboard")
async def get_lb():
    conn = sqlite3.connect('leaderboard.db')
    cur = conn.cursor()
    cur.execute("SELECT team_id, SUM(points) FROM scores GROUP BY team_id")
    rows = cur.fetchall()
    conn.close()

    score_map = {team: total or 0 for team, total in rows}
    full = [{"team": str(i), "score": score_map.get(str(i), 0)} for i in range(1, TOTAL_TEAMS + 1)]
    full.sort(key=lambda x: x["score"], reverse=True)
    return full


@app.get("/api/admin/status")
async def get_status():
    conn = sqlite3.connect('leaderboard.db')
    cur = conn.cursor()
    cur.execute("SELECT team_id, problem_id FROM assignments")
    assigns = dict(cur.fetchall())
    conn.close()

    return [
        {
            "team_id": str(i),
            "p1_online": manager.is_online(str(i), "p1"),
            "p2_online": manager.is_online(str(i), "p2"),
            "assigned": assigns.get(str(i), "None")
        }
        for i in range(1, TOTAL_TEAMS + 1)
    ]


@app.post("/api/save-draft/{tid}/{pid}")
async def save_draft(tid, pid, data: DraftRequest):
    conn = sqlite3.connect('leaderboard.db')
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO drafts (team_id, player_id, code, language) VALUES (?, ?, ?, ?)",
        (tid, pid, data.code, data.language)
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.get("/api/get-draft/{tid}/{pid}")
async def get_draft(tid, pid):
    conn = sqlite3.connect('leaderboard.db')
    cur = conn.cursor()
    cur.execute("SELECT code, language FROM drafts WHERE team_id=? AND player_id=?", (tid, pid))
    row = cur.fetchone()
    conn.close()
    return {"code": row[0], "language": row[1]} if row else {"code": None, "language": "python"}


@app.post("/api/admin/set-team-count/{count}")
async def set_teams(count: int):
    global TOTAL_TEAMS
    if count < 1 or count > 100:
        raise HTTPException(status_code=400, detail="Team count must be between 1 and 100")
    TOTAL_TEAMS = count
    return {"status": "ok", "total_teams": TOTAL_TEAMS}
