# Reference Episodes

Manually reviewed interaction trajectory examples, organized by interaction type:

```text
reference_episodes/
└── <interaction_type>/
    ├── reference_001/
    │   ├── metadata.yaml
    │   ├── llm_agents.json
    │   ├── scenario.yaml
    │   └── scenario_preview.png
    └── reference_002/
        ├── metadata.yaml
        ├── reference_episode.json
        ├── manual_scenario.yaml
        └── scenario_preview.png
```

Each subdirectory describes one complete episode. In `metadata.yaml`, `source_scene_id` records which scene the trajectory was actually collected from, and `interaction_type` records the interaction category. Therefore the same reference set can contain episodes from different scenes. Generated results use `llm_agents.json`; the manual editor uses `reference_episode.json`. Aim for 3 to 5 episodes per interaction type.

For manual annotation, use `--output-folder reference_episodes/<interaction_type>/<episode_id>`.
Pressing `s` writes `manual_scenario.yaml`, `scenario_preview.png`, `reference_episode.json`, and
`metadata.yaml`; the latter two let the scenario generator load the directory directly as a reference episode.

The generator restores pixel coordinates from each episode's source-scene `waypoints_by_region.json` and produces shared `interaction_relative` coordinates. Original pixels keep the source environment geometry; `interaction_relative` removes global translation and uniform scale so path direction, intersection angles, crossing locations, and relative agent order transfer more easily across scenes.

Legacy single-file `*.yaml` / `*.json` reference formats are also supported. Files or directories whose names start with an underscore are not loaded.

Create an episode manually:

```bash
python3 scripts/manual_scenario_editor.py \
  --scene-dir new_grscenes/<scene_id> \
  --output-folder reference_episodes/<interaction_type>/reference_001
```

The manual editor writes `metadata.yaml` automatically. By default it uses the parent directory name of the output folder as the interaction type; you can also set it explicitly with `--interaction-type`.
