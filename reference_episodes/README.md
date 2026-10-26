# Reference Episodes

按 interaction 类型保存人工检查过的交互轨迹示例：

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

每个子文件夹描述一条完整 episode。`metadata.yaml` 中的 `source_scene_id` 记录该轨迹实际采集自哪个场景，`interaction_type` 记录交互类别。因此同一个 reference set 可以包含来自不同场景的 episode。生成结果使用 `llm_agents.json`，手动编辑器使用 `reference_episode.json`。推荐每种 interaction type 准备 3 到 5 条。

手动标注时请使用 `--output-folder reference_episodes/<interaction_type>/<episode_id>`。
按 `s` 会写入 `manual_scenario.yaml`、`scenario_preview.png`、`reference_episode.json` 和
`metadata.yaml`；后两者使该目录能被 scenario generator 直接作为 reference episode 读取。

生成器会使用每条 episode 的源场景 `waypoints_by_region.json` 恢复 pixel 坐标，并生成共享的 `interaction_relative` 坐标。原始 pixel 保留源环境几何；`interaction_relative` 去除整体平移和统一缩放，使路径方向、夹角、交叉位置和角色相对顺序更容易跨场景复用。

也兼容旧的 `*.yaml` / `*.json` 单文件参考格式。下划线开头的文件或目录不会被加载。

手动创建 episode：

```bash
python3 scripts/manual_scenario_editor.py \
  --scene-dir new_grscenes/<scene_id> \
  --output-folder reference_episodes/<interaction_type>/reference_001
```

手动编辑器会自动写入 `metadata.yaml`。默认用输出目录的父目录名作为 interaction type，也可以通过 `--interaction-type` 显式指定。
