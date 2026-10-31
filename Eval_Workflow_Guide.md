# Step-by-step playbook for SocialNav Evaluation Workflow

Do what 知涛 asked: **API-generate** social-nav scenarios → play in **Arena / Isaac + HuNav** with **social yielding on** → report **good/bad** on Feishu.  
Do **not** QA all of `reference_episodes/` (LLM few-shot refs only).

| Item | Path / value |
| ---- | ------------ |
| Generator repo | `~/SocialNav/scenarios_generate` |
| Arena workspace | `~/SocialNav/arena_ws` |
| Arena source | `~/SocialNav/arena_ws/src/Arena` (Arena5.0 tree) |
| **Arena git branch** | `feature/human_interaction` (required for yielding `G=ok` / HI) |
| **Config to edit** | `configs/scenario_generation2.yaml` (**not** `scenario_generation.yaml`) |
| Feishu | https://roboscience.feishu.cn/wiki/CutawUATmiDgInkdFiYcFn09ngg |

**Case (l) Entering Room — required scenes:** `1, 3, 8, 9, 13, 19, 29`

### Why not `scenario_generation.yaml`?

知涛: that file’s Entering Room prompt is **wrong** (roles / directions reversed). Always use `scenario_generation2.yaml`.  
Episodes generated with the old yaml should be **regenerated** before grading as final QA.

Correct Entering Room idea in `scenario_generation2.yaml`:

- Pedestrian starts **inside** the room, faces out, moves **out**
- Robot starts **outside**, faces in, moves **in**
- Conflict at the doorway; ped path continues farther into open space after exit
- Both passive; social yielding enabled (prompt says `启用主动让行` → LLM writes `robot.social_yielding: true`)

### `social_yielding` (required for Entering Room / frontal approach QA)

Two layers — **both** must allow yielding, or LLM replan never runs:

1. **In the generated episode** (`scenario.yaml` → `robot.social_yielding: true`). Check with:  
   `grep social_yielding new_outputs/.../scenario_00X/scenario.yaml`
2. **At Arena launch:** use `social_yielding:=true`.  
   Launch arg **overrides** the file. `social_yielding:=false` forces the pipeline **off**.

Watch the Arena terminal for: `social_yielding ENABLED`, `YIELD TRIGGERED`, `[Replan]`, `REACHED Y`, resume original `G`.

---

## 0. Host vs Arena — where commands run

| Action | Where |
| ------ | ----- |
| Edit generator yaml / run `generate_scenario_variants.py` | Host + `conda activate scenarios_generate` |
| Edit `arena_ws/.env`, `git pull`, `docker start/ps`, `chmod docker.sock` | **Host** (`zy@...`) |
| `source arena` → enter container | Host → drops you into **Arena** (`arena@...:/opt/arena_ws`) |
| `arena build …`, `arena launch …` | **Inside Arena only** |
| `arena_docker_compose` / `source src/Arena/_meta/docker/source` | **Host only** — never inside Arena (poisons `HOST_ARENA_WS_DIR`) |

Rule: **docker / compose / `_meta/docker/source` → host.** **`arena launch` / `arena build` → Arena shell.**

---

## 1. Overview

```text
[Generator] DMX_API_KEY + DMX_BASE_URL
  → edit scenario_generation2.yaml (scene_dir)
  → python ... --config configs/scenario_generation2.yaml
  → confirm robot.social_yielding: true
  → copy into Arena worlds/<scene>/scenarios/<name>/

[Arena] branch feature/human_interaction
  → API_KEY in arena_ws/.env
  → API_URL in social_yielding_selector.py (Responses endpoint)
  → (after pull) arena build task_generator arena_simulation_setup
  → host: docker sock + start isaac
  → source arena → arena launch social_yielding:=true
  → RViz both configs = scenario name → Reset Task
  → Feishu good/bad
```

`scene_dir`, install folder, and `world:=` must use the **same** `grscenes_N`.

---

## 2. APIs — two different keys / URLs

There are **two** LLM paths. Do not mix them.

### 2.1 Scenario generation (host, `scenarios_generate`)

Uses Gemini via DMX chat API (`llm_config.local.yaml`).

```bash
cd ~/SocialNav/scenarios_generate
conda activate scenarios_generate

export DMX_BASE_URL=https://www.dmxapi.cn/v1
read -r -s DMX_API_KEY && export DMX_API_KEY && echo

echo "BASE=$DMX_BASE_URL"
test -n "$DMX_API_KEY" && echo "API key is set" || echo "API key MISSING"
```

Do **not** put keys in Feishu, git, or chat logs.

### 2.2 Social yielding selector (Arena / Isaac replan)

Uses **Responses API** (`/v1/responses`) from `social_yielding_selector.py`.

| Setting | Where | Notes |
| ------- | ----- | ----- |
| `API_KEY` | `~/SocialNav/arena_ws/.env` | Loaded into Arena via compose `env_file`. **Not** `DMX_API_KEY`. |
| `API_URL` | Hardcoded in selector (see below) | Full endpoint including `/v1/responses` |

**Set key once on host** (appends if missing — avoid duplicating lines):

```bash
cd ~/SocialNav/arena_ws
grep -q '^API_KEY=' .env || { read -r -s API_KEY && echo && printf 'API_KEY=%s\n' "$API_KEY" >> .env; unset API_KEY; }
grep -E '^API_KEY=' .env | sed 's/=.*/=***set***/'
```

**Set URL** (edit source; no colcon rebuild — run from source at launch):

File:

`~/SocialNav/arena_ws/src/Arena/arena_isaac/arena_isaac/arena_isaac/social_yielding/social_yielding_selector.py`

```python
API_URL = "https://www.dmxapi.cn/v1/responses"   # full Responses path; confirm with 知涛
API_KEY = os.environ.get("API_KEY", "")
```

Upstream default `https://llmapi.roboscience.xyz/v1/responses` may **fail DNS** on some networks (`Name or service not known`). Use the reachable URL 知涛 gives you.

**Verify key inside Arena** (after `source arena`):

```bash
test -n "$API_KEY" && echo "API_KEY ok" || echo "API_KEY MISSING"
```

Host shells will print `API_KEY MISSING` even when `.env` is fine — that is expected.

After changing `.env`, recreate/re-enter Arena so the container picks up env (`source arena` / recreate arena container). Changing only `API_URL` in the `.py` file needs a **relaunch**, not a rebuild.

---

## 3. Arena branch / update / build

### 3.1 Correct branch

```bash
cd ~/SocialNav/arena_ws/src/Arena
git fetch
git status   # should be feature/human_interaction
git pull origin feature/human_interaction
git submodule update --init --recursive
```

After submodule update, **re-check** that your local `API_URL` edit in `social_yielding_selector.py` is still present (submodule checkout can reset the file).

### 3.2 Build after code pulls (inside Arena)

知涛 updates that touch Python packages / nav2 yaml need a rebuild, e.g.:

```bash
# inside arena@...
arena build task_generator arena_simulation_setup
```

Or:

```bash
cd /opt/arena_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install --packages-select task_generator arena_simulation_setup
source install/local_setup.bash
```

- Changing **only** `social_yielding_selector.py` / `.env` → **relaunch**, no build.
- Changing **ROS msgs** (e.g. `isaacsim_msgs`) may also need Isaac **image** rebuild — see Arena `docs/常见问题.md`.

---

## 4. Edit `scenario_generation2.yaml`

Open: `configs/scenario_generation2.yaml` — **Save** after edits. Confirm with `grep`.

### 4.1 GRScene

```yaml
scene_dir: new_grscenes/grscenes_1
```

Allowed Entering Room: `grscenes_1`, `grscenes_3`, `grscenes_8`, `grscenes_9`, `grscenes_13`, `grscenes_19`, `grscenes_29`.

```bash
grep '^scene_dir:' configs/scenario_generation2.yaml
```

### 4.2 Keep Entering Room active

```yaml
reference_sets:
  - entering_room
include_reference_episodes: false
```

Keep **only** the `# Entering Room` `prompt:` active; other case prompts stay commented.

### 4.3 Count

```yaml
count: 6
```

---

## 5. Generate → install → play (one scene)

Example: `grscenes_1`. Replace `1` with `N` everywhere for other scenes.

### 5.1 Generate (host)

```bash
cd ~/SocialNav/scenarios_generate
conda activate scenarios_generate
# Section 2.1 env already set

python3 scripts/generate_scenario_variants.py --config configs/scenario_generation2.yaml
```

Note the RUN folder from:

```text
RUN OUTPUT .../new_outputs/grscenes_1/entering_room_<timestamp>
```

**Artifacts (generator):**

```text
~/SocialNav/scenarios_generate/new_outputs/grscenes_1/entering_room_<timestamp>/
  scenario_001/scenario.yaml
  scenario_001/metadata.yaml
  ...
  scenario_006/...
  scenarios_overview.png
```

### 5.2 Install into Arena (host, outside Arena shell)

```bash
SCENE=grscenes_1
RUN=entering_room_YYYYMMDD_HHMM   # YOUR RUN from 5.1
BASE=~/SocialNav/scenarios_generate/new_outputs/$SCENE/$RUN
WORLD_SRC=~/SocialNav/arena_ws/src/Arena/arena_simulation_setup/worlds/$SCENE/scenarios
WORLD_INSTALL=~/SocialNav/arena_ws/install/arena_simulation_setup/share/arena_simulation_setup/worlds/$SCENE/scenarios

for i in 001 002 003 004 005 006; do
  NAME=entering_room_g1_gen_$i
  mkdir -p "$WORLD_SRC/$NAME" "$WORLD_INSTALL/$NAME"
  cp -a "$BASE/scenario_$i/scenario.yaml" "$WORLD_SRC/$NAME/"
  cp -a "$BASE/scenario_$i/scenario.yaml" "$WORLD_INSTALL/$NAME/"
  echo "installed $NAME"
done
```

For scene `N`: `SCENE=grscenes_N`, `NAME=entering_room_g${N}_gen_$i`.

If Isaac is blank for that world:

```bash
cp -a ~/SocialNav/arena_ws/src/Arena/arena_simulation_setup/worlds/$SCENE \
  ~/SocialNav/arena_ws/install/arena_simulation_setup/share/arena_simulation_setup/worlds/
```

### 5.3 Host prep before launch

```bash
cd ~/SocialNav/arena_ws
sudo chmod 666 /var/run/docker.sock   # Arena user needs docker to start/exec Isaac
docker start arena-arena_ws-isaac-1

# mounts must be SocialNav paths, NOT host /opt/arena_ws/...
docker inspect arena-arena_ws-isaac-1 --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' | grep install
# expect: /home/zy/SocialNav/arena_ws/install -> /opt/arena_ws/install
```

If Isaac was recreated with `/opt/arena_ws/...` mounts (after wrongly running `_meta/docker/source` **inside** Arena), recreate Isaac **from the host**:

```bash
cd ~/SocialNav/arena_ws
source src/Arena/_meta/docker/source   # HOST only
echo "$HOST_ARENA_WS_DIR"              # must be /home/zy/SocialNav/arena_ws
arena_docker_compose up -d --force-recreate isaac
```

### 5.4 Launch (inside Arena)

```bash
cd ~/SocialNav/arena_ws
source arena

test -n "$API_KEY" && echo "API_KEY ok" || echo "API_KEY MISSING"
# Do NOT: source src/Arena/_meta/docker/source

arena launch sim:=isaac robot:=Ai2_Bot2 world:=grscenes_1 \
  tm_robots:=scenario tm_obstacles:=scenario \
  scenario:=entering_room_g1_gen_001 \
  social_yielding:=true \
  save_data:=false
```

RViz:

1. Wait until Isaac is usable / Play.
2. **Obstacles** and **Robots** Configuration File both = `entering_room_g1_gen_00X`.
3. **Reset Task** once.
4. Confirm logs: `social_yielding ENABLED` → `YIELD TRIGGERED` → snapshot → `G=ok` → `selector -> camera=...` → `REACHED Y` → resume `G`.
5. Grade → Feishu; switch dropdowns to next gen → Reset → grade.

Cold-start episode `#0` / `default` may plan from `(0,0)` and fail — ignore for QA; judge the Reset that loads your `entering_room_gN_gen_00X`.

### 5.5 Artifacts during Arena / yielding run

| Artifact | Location (inside containers / mounts) | Notes |
| -------- | ------------------------------------- | ----- |
| Yield snapshots (temp) | `…/src/social_gen/traj_data/grscenes/<world>/snapshots/<timestamp>/` | `head_rgb.png`, `back_rgb.png`, `topdown_rgb.png`, `*_depth.npy`, `*_camera.json` |
| Host path for same | `~/SocialNav/arena_ws/src/social_gen/traj_data/grscenes/<world>/snapshots/` | Bind-mounted from Arena `src` |
| Selector annotation / JSON | Under that snapshot dir (`back_pixel_goal.png`, `yielding_goal.json`) when selector saves | |
| Snapshot retention | Default **discarded** after replan (`save_data:=false` / `ARENA_SAVE_SNAPSHOTS=false`) | Set `save_snapshots:=true` / env to keep for debugging |
| ROS logs | `/home/arena/.ros/log/...` inside Arena | |

Trajectory / dataset dumps under `traj_data/` appear when data logging / `save_data` is enabled — not required for Entering Room QA with `save_data:=false`.

---

## 6. Test different GRScenes (case l checklist)

For each `N` in `1 → 3 → 8 → 9 → 13 → 19 → 29`:

1. Set `scene_dir: new_grscenes/grscenes_N` in `scenario_generation2.yaml`, Save, `grep`.
2. Export generator API if new shell (Section 2.1).
3. Generate; install with `SCENE=grscenes_N`.
4. Launch `world:=grscenes_N`, `social_yielding:=true`.
5. Feishu one row per episode.

Other cases: swap active `prompt` / `reference_sets` in `scenario_generation2.yaml` only.

---

## 7. What to write on Feishu

Add **LLM Scenario QA log**. Prefer `scenario_generation2.yaml` runs.

**Coverage TODO (Entering Room):** `1, 3, 8, 9, 13, 19, 29`

| Date | Scene | Case | Config | Output path | Arena name | Verdict | Notes |
| ---- | ----- | ---- | ------ | ----------- | ---------- | ------- | ----- |
| … | grscenes_N | entering_room (l) | `scenario_generation2.yaml` | `new_outputs/grscenes_N/entering_room_<ts>/scenario_00X` | `entering_room_gN_gen_00X` | good/bad | Yield? `REACHED Y`? Recording? |

**Verdict tips:** **good** = clear doorway interaction (ped out / robot in), yielding replan when required, useful motion; **bad** = stuck, no interaction, wrong spawn, or yielding never runs (`G=MISSING` / selector DNS fail / no `REACHED Y`).

---

## 8. Pitfalls

| Problem | Fix |
| ------- | --- |
| Used `scenario_generation.yaml` | Switch to `scenario_generation2.yaml` and regenerate |
| Wrong Arena branch / `G=MISSING` | `feature/human_interaction` + submodule update + rebuild as needed |
| `social_yielding:=false` | Relaunch with `:=true` |
| Yaml `social_yielding: false` | Prompt must say 启用主动让行; regenerate |
| Host `API_KEY MISSING` | Normal on host; check **inside** Arena after `source arena` |
| Selector DNS fail `llmapi.roboscience.xyz` | Set `API_URL` to reachable Responses URL (e.g. `https://www.dmxapi.cn/v1/responses`) |
| Instant `no valid goal` (~2 ms) | `API_KEY` empty in Arena process |
| ~10 s then `no valid goal` | API reachable but bad response / wrong endpoint path |
| `local_setup.bash: No such file` in Isaac | Isaac recreated with wrong `HOST_ARENA_WS_DIR`; recreate Isaac from **host** |
| `permission denied` docker.sock from Arena | Host: `sudo chmod 666 /var/run/docker.sock` (or add docker GID to compose) |
| Ran `_meta/docker/source` inside Arena | Exit; fix Isaac mounts from host; never run that script in Arena |
| Ped markers move, Isaac people don’t | `NavigatePedestrians` / `pedestrian_update FAILED` — Isaac lag; see Arena FAQ |
| Dropdown missing name | Install under both `src` and `install/.../worlds/$SCENE/scenarios/` |
| Still looks like `default` | Set **both** RViz tabs to your gen name |
| Episode `#0` fails from `(0,0)` | Cold start; Reset again on the intended gen scenario |

---

## 9. Success signals (yielding QA)

```text
social_yielding ENABLED
YIELD TRIGGERED
[Snapshot] results: {'head': True, 'back': True, 'topdown': True}
[Yield] ... G=ok   (from entering_room_gN_gen_00X — not G=MISSING)
selector -> camera=..., pixel=(...)
map goal = (...) cost=... (free) -> accepted
REACHED Y
[Resume] published original goal G=(...)
```
