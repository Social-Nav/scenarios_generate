# GRScenes Scenario Generator

This repository generates HuNav / Arena-ready `scenario.yaml` files from prepared GRScenes top-down views, navmeshes, regions, and waypoints.

## Recent Updates

### Scene and Output Naming

- All 30 scenes use the unified IDs `grscenes_1` through `grscenes_30`; raw USD IDs are no longer used as working directory names.
- `new_grscenes/`, `new_outputs/`, reference metadata, and the fixed dataset plan all use the same scene IDs.
- Legacy USD IDs can still be resolved by a compatibility layer, but all new outputs are written only under the canonical `grscenes_N` directories.
- `grscenes_list/` stores only the 30 original top-down images `grscenes_1.png` through `grscenes_30.png`.
- Previews always project world coordinates using `topdown_mapping.json`, `topdown.png`, and
  `navmesh_mask.png` inside the corresponding scene directory; renaming directories does not change waypoint mapping.

### LLM Input and Batch Generation

- The output schema requires each pedestrian's `behavior_mode`, plus the robot's `social_attributes` and
  `social_yielding`.
- Runtime config supports `reference_root`, `reference_sets`, and `include_reference_episodes`.
  Each reference episode declares its source scene via `metadata.yaml` and carries reusable
  `interaction_relative` geometry across scenes.
- `count` controls the total number of scenarios; `batch_size` controls how many are generated per LLM request. Supports a fixed pedestrian count or a reproducible range via
  `min_pedestrians`/`max_pedestrians` plus `seed`.
- Pedestrians only output `desired_velocity`; the legacy `velocity` field is no longer emitted.
- When the prompt clearly states that "the robot path consists of multiple waypoints", the robot keeps intermediate waypoints; otherwise it still uses
  start + goal.
- When the prompt mentions both `waypoint_tolerance` and `final_goal_tolerance`, the robot is written with
  `waypoint_tolerance: 0.50` and `final_goal_tolerance: 0.25`.

### Social Behavior Rules

- Pedestrian `neutral` uses `BTRegularNav.xml` and `behavior.type: 1`.
- Pedestrian `passive` uses a fixed profile: `BTSurprisedNav.xml`, `dist: 0.8`, `duration: 1.0`,
  `once: false`, `vel: 0.0`, `obstacle_force_factor: 2.0`,
  `social_force_factor: 10.0`, and forces `desired_velocity: 0.8`.
- Neutral pedestrians in `following_human` additionally get a top-level `social_force_factor: 10.0`; passive
  pedestrians already include that value in their behavior profile, so it is not written twice.
- Robot social modes support `passive`, `neutral`, and `aggressive`; proactive yielding is controlled by the separate
  `social_yielding` boolean.

### Manual Annotation and Reference Episodes

- The manual editor uses `--output-folder` and, on `s`, saves `manual_scenario.yaml`,
  `scenario_preview.png`, `reference_episode.json`, and `metadata.yaml` in one shot.
- Saving no longer produces an extra Matplotlib `Figure_1.png`.
- `metadata.yaml` is required metadata for each episode subdirectory; it records `source_scene_id` and
  `interaction_type`. The generator uses these to load waypoints and image sizes from the correct source scene.



- `new_grscenes/`: scene asset packs, one directory per scene.
- `scripts/`: tooling for annotation, inspection, and scenario generation.

## Directory Structure

```text
scenarios_generate/
├── new_grscenes/
│   └── grscenes_1/ ... grscenes_30/
│       ├── map.yaml
│       ├── navmesh_mask.png
│       ├── overlay.png
│       ├── topdown.png
│       ├── topdown.json
│       ├── topdown_mapping.json
│       ├── waypoints_by_region.json
│       ├── scene_prompt.txt
│       └── regions_waypoints_overview.png
├── scripts/
├── configs/
├── examples/
├── requirements.txt
└── README.md
```

Meaning of the standard files in each scene directory:

| File | Purpose |
| --- | --- |
| `topdown.png` | Original top-down view for inspecting scene layout. |
| `overlay.png` | Top-down view with semantics/regions for judging furniture, corridors, and rooms. |
| `navmesh_mask.png` | Walkable-area mask for placing waypoints and checking connectivity. |
| `topdown_mapping.json` | Pixel-to-world coordinate transform; required for scenario generation. |
| `topdown.json` | Region polygons and labels; used to draw regions and group waypoints. |
| `waypoints_by_region.json` | Waypoints per region, plus optional cross-region `region_transitions`. |
| `scene_prompt.txt` | Scene connectivity notes for the LLM when generating paths. |
| `regions_waypoints_overview.png` | Overview check image of regions, waypoints, and cross-region links. |
| `map.yaml` | Navigation map metadata. |

## Install Dependencies

```bash
conda create -n scenarios_generate python=3.10 -y
conda activate scenarios_generate
cd ~/scenarios_generate
pip install -r requirements.txt
```

## Most Common Workflow: Manually Place Robot / Human Trajectories

This is the most stable and controllable approach.

```bash
cd ~/scenarios_generate
python3 scripts/manual_scenario_editor.py \
  --scene-dir new_grscenes/<scene_id> \
  --output-folder reference_episodes/<interaction_type>/<episode_id>
```

Example:

```bash
python3 scripts/manual_scenario_editor.py \
  --scene-dir new_grscenes/grscenes_30 \
  --output-folder reference_episodes/intersection/reference_007
```

Window controls:

- `r`: Switch to / create the robot trajectory.
- `h`: Create a new human trajectory.
- Left mouse button: Add a point to the current trajectory. The first point is the start; later points are path waypoints.
- `u`: Undo the last point on the current trajectory.
- `s`: Save the full episode folder.
- `q`: Quit.

By default, clicks snap to nearby waypoints, which fits the current waypoint workflow. For free-form coordinates:

```bash
python3 scripts/manual_scenario_editor.py \
  --scene-dir new_grscenes/<scene_id> \
  --output-folder reference_episodes/<interaction_type>/<episode_id> \
  --no-snap
```

Output:

```text
reference_episodes/<interaction_type>/<episode_id>/
├── manual_scenario.yaml
├── scenario_preview.png
├── reference_episode.json
└── metadata.yaml
```

## Auto-Generate Multiple Scenarios

Auto-generation depends on LLM configuration. First copy the config template:

The LLM outputs an occupation category for each pedestrian's `model`, not a concrete character asset. Allowed values are
`pedestrian`, `doctor`, `police`, and `construction_worker`. Per scenario,
at most 2 `doctor`, at most 4 each of `police` and `construction_worker`, and
unlimited `pedestrian`; concrete character assets are assigned by a downstream system.

```bash
cp configs/llm_config.example.yaml configs/llm_config.local.yaml
```

The current example config uses DMXAPI's OpenAI Chat Completions-compatible API and
`gpt-6-astra`. Set the API key:

```bash
cp configs/api_key.example.yaml configs/api_key.local.yaml
```

Fill in the key in `configs/api_key.local.yaml`:

```yaml
api_key: "your_api_key"
```

`api_key.local.yaml` is excluded by `.gitignore` and will not be committed. Configure the API endpoint, model, and key file in
`configs/llm_config.local.yaml`:

```yaml
type: openai_chat
api_base_url: https://jkwl.dmxapi.cn/v1
api_key_file: api_key.local.yaml
api_key_env: DMX_API_KEY
model_id: gpt-6-astra
connect_timeout: 30
read_timeout: 600
```

The program prefers `api_key_file`. If that file has no key, it falls back to the `DMX_API_KEY` environment variable;
so `export DMX_API_KEY="your_api_key"` still works.

This generation flow requires a model that supports image input, because each request sends both the scene overlay and
the navmesh mask. If the provider only enables text calls for the model, the API returns an image-unsupported error.
`read_timeout` is the number of seconds to wait for a full model response; increase it for slower multimodal requests.

Then run:

Prefer putting each run's parameters in `configs/scenario_generation.yaml`. After editing that file, just run:

```bash
python3 scripts/generate_scenario_variants.py \
  --config configs/scenario_generation.yaml
```

The config file includes `scene_dir`, `output_root`, `llm_config`, `prompt`, `count`,
`reference_root`, `reference_sets`, `include_reference_episodes`, `pedestrians`, `max_attempts`,
`overwrite`, and `dry_run`. `include_reference_episodes: false` skips reading, validating, and injecting reference episodes
into the prompt. CLI flags still work and override config values; for example, generate one candidate without calling the LLM:

```bash
python3 scripts/generate_scenario_variants.py \
  --config configs/scenario_generation.yaml \
  --count 1 \
  --no-include-reference-episodes \
  --dry-run
```

You can also continue using CLI arguments only:

```bash
python3 scripts/generate_scenario_variants.py --config configs/scenario_generation.yaml
```

Output layout:

```text
new_outputs/<scene_id>/
└── <interaction_type>_<YYYYMMDD_HHMM>/
    ├── scenario_001/
    │   ├── llm_agents.json
    │   ├── scenario.yaml
    │   ├── metadata.yaml
    │   └── scenario_preview.png
    ├── scenario_002/
    └── scenarios_overview.png
```

By default each LLM call returns a batch of 10 scenarios; when fewer than 10 remain, only the remaining count is requested.
The console shows per-batch input, output, reasoning, cached, and total tokens, plus the average tokens per
scenario. Averages are statistical estimates; the API only returns exact usage for the whole batch. On
retries, retry tokens are also counted toward that batch. After all candidates finish, it also prints
`TOTAL TOKENS THIS RUN`. These stats appear only in the console and are not written into scenario YAML;
existing scenarios that are not regenerated count as 0 for this run. To restore the old one-at-a-time mode, add
`--batch-size 1`.

### Fixed Generation of 10,000 Samples

`scripts/generate_scenario_dataset.py` hard-codes the size classes and 10,000-sample quotas for the current 30 dense-waypoint scenes
and does not recompute them at runtime:

- Small scenes: 1–5 people.
- Medium scenes: 1–8 people.
- Large scenes: 1–10 people.

The dataset entry point always uses `--batch-size 10`, so images, waypoints, and scene notes are
sent only once per batch. For example, if a scene needs 278 samples, it issues 27 batches of 10 and 1 batch of 8.
Each batch must fully pass count, occupation, route, and duplicate checks; on failure the whole batch is retried.

Pedestrian counts within each scene are balanced under a fixed random seed, so resume runs and single-scene runs still
assign the same target counts to the same scenario indices. Generate the full dataset:

```bash
python3 scripts/generate_scenario_dataset.py \
  --scenes-root new_grscenes \
  --output-root new_outputs \
  --llm-config configs/gpt6.local.yaml \
  --prompt "Generate natural, plausible, and diverse human–robot interaction scenarios"
```

Inspect the fixed quotas without calling the API:

```bash
python3 scripts/generate_scenario_dataset.py \
  --llm-config configs/gpt6.local.yaml \
  --prompt test \
  --dry-run
```

The 10,000-sample run generates a separate `scenario_preview.png` for each scenario by default, but does not stitch hundreds of
previews from the same scene into an overview image. Add `--skip-previews` if you need no previews at all; add
`--render-overview` if you do need the overview. Use `--only-scene <scene_id>` to generate only one scene from the fixed plan.
The program skips existing valid results and previews, and prints a cross-scene token total at the end.

If generation used `--skip-previews`, you can still render each standalone preview offline later without configuring an LLM or calling the API:

```bash
python3 scripts/render_dataset_previews.py \
  --scenes-root new_grscenes \
  --output-root new_outputs_10k
```

Existing previews are skipped; add `--overwrite` to redraw. This command only writes `scenario_preview.png` inside each scenario
directory and does not stitch overview images.

## Annotate / Update Waypoints

If a new scene does not yet have `waypoints_by_region.json`, use:

```bash
python3 scripts/mark_waypoints_by_region.py \
  --scene-dir new_grscenes/<scene_id>
```

Common controls:

- Left click: Add a waypoint.
- `backspace` / `delete`: Undo the most recently added waypoint.
- `s`: Save.
- `q`: Save and quit.

To append waypoints to an existing file:

```bash
python3 scripts/mark_waypoints_by_region.py \
  --scene-dir new_grscenes/<scene_id> \
  --append
```

## Annotate Cross-Region Transition Waypoints

If two regions are only connected by a doorway / narrow passage, add `region_transitions` in `waypoints_by_region.json`:

```bash
python3 scripts/mark_region_transitions.py \
  --scene-dir new_grscenes/<scene_id>
```

Controls:

- Left-click the connecting waypoint in the first region.
- Left-click the connecting waypoint in the other region.
- `s`: Save this transition pair.
- `backspace` / `delete`: Clear the current selection.
- `q`: Save and quit.

If waypoint names clutter the view:

```bash
python3 scripts/mark_region_transitions.py \
  --scene-dir new_grscenes/<scene_id> \
  --hide-waypoint-names
```

## Regenerate the Region Waypoint Overview Image

After changing waypoints or transitions, regenerate the check image:

```bash
python3 scripts/draw_waypoints_by_region.py \
  --scene-dir new_grscenes/<scene_id> \
  --output new_grscenes/<scene_id>/regions_waypoints_overview.png \
  --show-region-ids \
  --hide-waypoint-names
```

## Inspect Trajectories in Existing Scenarios

Draw a `scenario.yaml` back onto the top-down view:

```bash
python3 scripts/topdown_click_to_world.py \
  --mapping new_grscenes/<scene_id>/topdown_mapping.json \
  --view overlay \
  --scenario new_outputs/<scene_id>/manual_scenario.yaml \
  --save-overlay new_outputs/<scene_id>/manual_scenario_check.png \
  --no-show
```
