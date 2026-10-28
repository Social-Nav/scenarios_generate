# SocialNav QA Check Guide

Do what 知涛 asked: **API-generate** social-nav scenarios → play in **Arena / Isaac + HuNav** → report **good/bad** on Feishu.  
Do **not** QA all of `reference_episodes/` (LLM few-shot refs only).


| Item               | Path                                                                                                                             |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| Generator          | `~/SocialNav/scenarios_generate`                                                                                                 |
| Arena              | `~/SocialNav/arena_ws`                                                                                                           |
| **Config to edit** | `configs/scenario_generation2.yaml` (use this; **not** `scenario_generation.yaml`)                                               |
| Feishu             | [https://roboscience.feishu.cn/wiki/CutawUATmiDgInkdFiYcFn09ngg](https://roboscience.feishu.cn/wiki/CutawUATmiDgInkdFiYcFn09ngg) |


**Case (l) Entering Room — required scenes:** `1, 3, 8, 9, 13, 19, 29`

### Why not `scenario_generation.yaml`?

知涛: that file’s Entering Room prompt is **wrong** (roles / directions reversed). Always use `scenario_generation2.yaml`.  
Episodes generated with the old yaml should be **regenerated** before grading as final QA.

Correct Entering Room idea in `scenario_generation2.yaml`:

- Pedestrian starts **inside** the room, faces out, moves **out**
- Robot starts **outside**, faces in, moves **in**
- Conflict at the doorway; ped path continues farther into open space after exit
- Both passive; social yielding enabled

---



## 1. Overview

```text
Paste API key in terminal
  → edit scenario_generation2.yaml (scene_dir only, usually)
  → python ... --config configs/scenario_generation2.yaml
  → copy scenario.yaml into Arena worlds/<scene>/scenarios/<name>/
  → arena launch + RViz Reset Task
  → Feishu good/bad (+ optional screen recording)
```

Rule: `scene_dir`, install folder, and `world:=` must use the **same** `grscenes_N`.

---



## 2. Where to paste the API key

Open a terminal in the generator repo:

```bash
cd ~/SocialNav/scenarios_generate
conda activate scenarios_generate
```

Your `configs/llm_config.local.yaml` is `type: gemini`, so set **both** env vars **every new terminal**:

```bash
export DMX_BASE_URL=https://www.dmxapi.cn/v1
```

Then paste the key **without echoing it**:

```bash
read -r -s DMX_API_KEY && export DMX_API_KEY && echo
```

1. Run that line and press Enter.
2. Cursor sits with **no prompt** — that is normal.
3. **Paste** the API key (Ctrl+Shift+V / middle-click). You will see nothing typed.
4. Press **Enter**. A blank line appears — key is set.

Check:

```bash
echo "BASE=$DMX_BASE_URL"
test -n "$DMX_API_KEY" && echo "API key is set" || echo "API key MISSING"
```

Do **not** put the key in Feishu, git, or chat logs.  
Do **not** type `scene_dir: ...` into the shell — that is yaml for the editor only.

---



## 3. How to edit `scenario_generation2.yaml`

Open in Cursor: `configs/scenario_generation2.yaml`  
**Save after every change.** Confirm with `grep` — do not paste yaml into bash.

### 3.1 Change GRScene (line ~4)

Find (example; your number may differ):

```yaml
scene_dir: new_grscenes/grscenes_19
```

Change only the scene number, e.g.:

```yaml
scene_dir: new_grscenes/grscenes_1
```

Allowed Entering Room values: `grscenes_1`, `grscenes_3`, `grscenes_8`, `grscenes_9`, `grscenes_13`, `grscenes_19`, `grscenes_29`.

Verify:

```bash
grep '^scene_dir:' configs/scenario_generation2.yaml
```



### 3.2 Keep Entering Room active (usually leave as-is)

Keep:

```yaml
reference_sets:
  - entering_room
include_reference_episodes: false
```

Keep **only** the `# Entering Room` block as an active `prompt:` (around lines 47–51). All other `prompt:` blocks must stay **commented** with `#`.

Do **not** copy the Entering Room text from `scenario_generation.yaml` — that prompt is wrong.

### 3.3 How many scenarios

Near the bottom of `scenario_generation2.yaml`:

```yaml
count: 6
```

Default is **6** candidates per run (`scenario_001` … `scenario_006`). Change only if 知涛 asks.

---



## 4. Generate → install → play (one scene)

Example uses `grscenes_1`. For another scene, replace `1` with `3`, `8`, … everywhere.

### 4.1 Generate

```bash
cd ~/SocialNav/scenarios_generate
conda activate scenarios_generate
# API env already exported (Section 2)

python3 scripts/generate_scenario_variants.py --config configs/scenario_generation2.yaml
```

First line must match your scene, e.g.:

```text
RUN OUTPUT .../new_outputs/grscenes_1/entering_room_20261028_1900
```

- NOTE: The `entering_room_20261028_1900` in `RUN OUTPUT .../new_outputs/grscenes_1/entering_room_20261028_1900` is the RUN name that will be used in Section 4.2
- The generated grscenes are found in `/.../scenarios_generate/new_outputs`

- Close preview windows when they appear. Note the **RUN** folder name (timestamp).

Outputs (with `count: 6`):

```text
new_outputs/grscenes_1/entering_room_<timestamp>/
  scenario_001/scenario.yaml
  ...
  scenario_006/scenario.yaml
  scenarios_overview.png
```



### 4.2 Install into Arena (host, outside `source arena`)

```bash
SCENE=grscenes_1
RUN=entering_room_20261028_1900   # paste YOUR RUN name from 4.1
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

For scene `N`, use `SCENE=grscenes_N` and `NAME=entering_room_g${N}_gen_$i` (e.g. `entering_room_g3_gen_001`).  
If you lowered `count`, only loop over the indices you actually generated.

If Isaac is blank for that world:

```bash
cp -a ~/SocialNav/arena_ws/src/Arena/arena_simulation_setup/worlds/$SCENE \
  ~/SocialNav/arena_ws/install/arena_simulation_setup/share/arena_simulation_setup/worlds/
```



### 4.3 Launch and Reset Task

```bash
cd ~/SocialNav/arena_ws
source arena

arena launch sim:=isaac robot:=Ai2_Bot2 world:=grscenes_1 \
  tm_robots:=scenario tm_obstacles:=scenario \
  scenario:=entering_room_g1_gen_001 \
  social_yielding:=false \
  save_data:=false
```

1. Wait until Isaac is **Play**ing.
2. RViz: **Obstacles** and **Robots** Configuration File both = `entering_room_g1_gen_001`.
3. Click **Reset Task** once.
4. Watch agents; note `goal: (x, y)` in the terminal.
5. Grade → Feishu (attach / link screen recording if you have one).
6. Same world: switch both dropdowns to the next `entering_room_g1_gen_00X` → Reset → grade.

Optional: first Reset with `default` to confirm the stack moves; then switch to your gen name.

---



## 5. Test different GRScenes (case l checklist)

For each `N` in `1 → 3 → 8 → 9 → 13 → 19 → 29`:

1. **Editor:** in `scenario_generation2.yaml`, set `scene_dir: new_grscenes/grscenes_N`, Save.
2. **Terminal:** `grep '^scene_dir:' configs/scenario_generation2.yaml`
3. Export API if this is a new shell (Section 2).
4. Generate with `--config configs/scenario_generation2.yaml` — confirm `RUN OUTPUT .../grscenes_N/...`
5. Install with `SCENE=grscenes_N` (Section 4.2).
6. Launch `world:=grscenes_N` (Section 4.3).
7. Feishu one row per episode + note if you have a recording.

Then other cases: comment Entering Room `prompt` in `scenario_generation2.yaml`, uncomment that case’s `prompt`, set `reference_sets`, change `scene_dir` if needed, repeat. Prefer case prompts from `scenario_generation2.yaml` / 知涛’s files, not from the wrong `scenario_generation.yaml`.

---



## 6. What to write on Feishu

Add section **LLM Scenario QA log**. One row per played episode. Prefer rows from `scenario_generation2.yaml` runs.

Note on older work: episodes from `scenario_generation.yaml` used the reversed Entering Room prompt — mark them as *wrong config / regenerate*, or re-QA after regenerating with `scenario_generation2.yaml`.

**Coverage TODO (Entering Room):** `1, 3, 8, 9, 13, 19, 29`


| Date | Scene      | Case              | Config                      | Output path                                              | Arena name                 | Verdict  | Notes                                           |
| ---- | ---------- | ----------------- | --------------------------- | -------------------------------------------------------- | -------------------------- | -------- | ----------------------------------------------- |
| …    | grscenes_N | entering_room (l) | `scenario_generation2.yaml` | `new_outputs/grscenes_N/entering_room_<ts>/scenario_00X` | `entering_room_gN_gen_00X` | good/bad | Recording? motion / stuck / doorway interaction |


Short summary example:

```text
Switched to scenario_generation2.yaml (correct Entering Room prompt per 知涛).
Regenerating Entering Room for scenes 1,3,8,9,13,19,29; older scenario_generation.yaml runs are invalid for final QA.
```

**Verdict tips:** **good** = clear doorway interaction (ped out / robot in) and useful motion; **bad** = stuck, no interaction, or wrong spawn.

---



## 7. Pitfalls


| Problem                         | Fix                                                                        |
| ------------------------------- | -------------------------------------------------------------------------- |
| Used `scenario_generation.yaml` | Switch to `scenario_generation2.yaml` and regenerate                       |
| `DMX_BASE_URL is not set`       | Re-run Section 2                                                           |
| `scene_dir:: command not found` | Edit the yaml **file**; don’t paste into bash                              |
| Wrong scene in `RUN OUTPUT`     | Fix line ~4 `scene_dir` in `scenario_generation2.yaml`, Save, `grep` again |
| Dropdown missing name           | Install under `arena_simulation_setup/.../worlds/$SCENE/scenarios/`        |
| Still looks like `default`      | Set **both** RViz tabs to your gen name                                    |
| Nothing moves                   | Isaac Play, then Reset Task once                                           |
| Ped enters / robot exits        | Wrong prompt — you are still on old yaml or old Arena copy                 |


