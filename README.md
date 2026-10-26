# GRScenes Scenario Generator

这个仓库用于基于准备好的 GRScenes 俯视图、navmesh、区域和 waypoint，生成 HuNav / Arena 可用的 `scenario.yaml`。

## 近期更新

### 场景与输出命名

- 30 个场景统一使用 `grscenes_1` 到 `grscenes_30`，不再以原始 USD ID 作为工作目录名。
- `new_grscenes/`、`new_outputs/`、reference metadata 和固定数据集计划使用相同的 scene ID。
- 旧 USD ID 仍可由兼容层解析，但所有新输出只写入规范的 `grscenes_N` 目录。
- `grscenes_list/` 只保存 `grscenes_1.png` 到 `grscenes_30.png` 共 30 张原始俯视图。
- preview 始终使用对应场景目录内的 `topdown_mapping.json`、`topdown.png` 和
  `navmesh_mask.png` 投影世界坐标；目录重命名不会改变 waypoint 映射。

### LLM 输入与批量生成

- 输出 schema 强制要求行人的 `behavior_mode`，以及机器人的 `social_attributes` 和
  `social_yielding`。
- 运行配置支持 `reference_root`、`reference_sets` 和 `include_reference_episodes`。
  每条 reference episode 通过 `metadata.yaml` 声明其源场景，并携带可跨场景复用的
  `interaction_relative` 几何关系。
- `count` 控制最终 scenario 总数，`batch_size` 控制一次 LLM 请求生成多少条；支持固定人数或
  `min_pedestrians`/`max_pedestrians` 加 `seed` 的可复现人数范围。
- 行人只输出 `desired_velocity`，不再输出旧的 `velocity` 字段。
- prompt 明确包含“机器人路径由多个waypoints构成”时，机器人保留中间 waypoint；否则仍使用
  start + goal。
- prompt 同时提及 `waypoint_tolerance` 和 `final_goal_tolerance` 时，机器人写入
  `waypoint_tolerance: 0.50` 与 `final_goal_tolerance: 0.25`。

### 社交行为规则

- 行人 `neutral` 使用 `BTRegularNav.xml` 和 `behavior.type: 1`。
- 行人 `passive` 使用固定 profile：`BTSurprisedNav.xml`、`dist: 0.8`、`duration: 1.0`、
  `once: false`、`vel: 0.0`、`obstacle_force_factor: 2.0`、
  `social_force_factor: 10.0`，并强制 `desired_velocity: 0.8`。
- `following_human` 中的 neutral 行人会额外写入顶层 `social_force_factor: 10.0`；passive
  行人已在 behavior profile 内包含该值，因此不会重复写入。
- robot 的 social mode 支持 `passive`、`neutral`、`aggressive`，主动让行由独立的
  `social_yielding` 布尔值控制。

### 手动标注与 reference episode

- 手动编辑器使用 `--output-folder`，按 `s` 一次保存 `manual_scenario.yaml`、
  `scenario_preview.png`、`reference_episode.json` 和 `metadata.yaml`。
- 保存时不再额外产生 Matplotlib 的 `Figure_1.png`。
- `metadata.yaml` 是 episode 子目录的必要元数据，记录 `source_scene_id` 和
  `interaction_type`；生成器会据此读取正确源场景的 waypoint 与图像尺寸。



- `new_grscenes/`：场景资产包，每个 scene 一个目录。
- `scripts/`：标注、检查、生成 scenario 的工具脚本。

## 目录结构

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

每个场景目录中的标准文件含义：

| 文件 | 作用 |
| --- | --- |
| `topdown.png` | 原始俯视图，用于查看场景布局。 |
| `overlay.png` | 带语义/区域的俯视图，便于人工判断家具、通道和房间。 |
| `navmesh_mask.png` | 可通行区域 mask，用于标 waypoint 和判断连通性。 |
| `topdown_mapping.json` | 像素坐标到世界坐标的转换；生成 scenario 必需。 |
| `topdown.json` | 区域 polygon 和标签；用于画区域、分组 waypoint。 |
| `waypoints_by_region.json` | 每个区域内的 waypoint，以及可选的跨区域 `region_transitions`。 |
| `scene_prompt.txt` | 场景连通规则说明，供 LLM 生成路径时参考。 |
| `regions_waypoints_overview.png` | 区域、waypoint、跨区域连接的总览检查图。 |
| `map.yaml` | 导航地图相关元信息。 |

## 安装依赖

```bash
conda create -n scenarios_generate python=3.10 -y
conda activate scenarios_generate
cd ~/scenarios_generate
pip install -r requirements.txt
```

## 最常用流程：手动点 robot / human 轨迹

这是最稳定、最可控的方式。

```bash
cd ~/scenarios_generate
python3 scripts/manual_scenario_editor.py \
  --scene-dir new_grscenes/<scene_id> \
  --output-folder reference_episodes/<interaction_type>/<episode_id>
```

示例：

```bash
python3 scripts/manual_scenario_editor.py \
  --scene-dir new_grscenes/grscenes_30 \
  --output-folder reference_episodes/intersection/reference_007
```

窗口操作：

- `r`：切到 / 创建 robot 轨迹。
- `h`：新建一条 human 轨迹。
- 鼠标左键：给当前轨迹加点。第一个点是起点，后续点是路径点。
- `u`：撤销当前轨迹最后一个点。
- `s`：保存完整 episode 文件夹。
- `q`：退出。

默认会把点击位置吸附到附近 waypoint，适合当前这套 waypoint 流程。如果想完全自由点坐标：

```bash
python3 scripts/manual_scenario_editor.py \
  --scene-dir new_grscenes/<scene_id> \
  --output-folder reference_episodes/<interaction_type>/<episode_id> \
  --no-snap
```

输出：

```text
reference_episodes/<interaction_type>/<episode_id>/
├── manual_scenario.yaml
├── scenario_preview.png
├── reference_episode.json
└── metadata.yaml
```

## 自动生成多个 scenario

自动生成依赖 LLM 配置。先复制配置模板：

LLM 为每个行人的 `model` 输出职业类别，而不是具体人物资产。允许的值为
`pedestrian`、`doctor`、`police`、`construction_worker`。每个 scenario 中
`doctor` 最多 2 人，`police` 和 `construction_worker` 各最多 4 人，
`pedestrian` 不限；具体人物资产由下层系统分配。

```bash
cp configs/llm_config.example.yaml configs/llm_config.local.yaml
```

当前示例配置使用 DMXAPI 的 OpenAI Chat Completions 兼容接口和
`gpt-6-astra`。设置 API Key：

```bash
cp configs/api_key.example.yaml configs/api_key.local.yaml
```

在 `configs/api_key.local.yaml` 中填写 key：

```yaml
api_key: "your_api_key"
```

`api_key.local.yaml` 已被 `.gitignore` 排除，不会被 Git 提交。接口地址、模型和 key 文件在
`configs/llm_config.local.yaml` 中配置：

```yaml
type: openai_chat
api_base_url: https://jkwl.dmxapi.cn/v1
api_key_file: api_key.local.yaml
api_key_env: DMX_API_KEY
model_id: gpt-6-astra
connect_timeout: 30
read_timeout: 600
```

程序优先读取 `api_key_file`。文件中没有填写 key 时，会回退读取 `DMX_API_KEY` 环境变量；
因此也仍可使用 `export DMX_API_KEY="your_api_key"`。

该生成流程需要模型支持图片输入，因为每次请求会同时发送场景 overlay 和
navmesh mask。若供应商仅为该模型开通文本调用，接口会返回不支持图片的错误。
`read_timeout` 是等待模型完整响应的秒数；多模态请求较慢时可以继续增大。

然后运行：

推荐把每次运行的参数写在 `configs/scenario_generation.yaml` 中。修改该文件后只需执行：

```bash
python3 scripts/generate_scenario_variants.py \
  --config configs/scenario_generation.yaml
```

配置文件包含 `scene_dir`、`output_root`、`llm_config`、`prompt`、`count`、
`reference_root`、`reference_sets`、`include_reference_episodes`、`pedestrians`、`max_attempts`、
`overwrite` 和 `dry_run`。`include_reference_episodes: false` 会跳过 reference episode
的读取、校验和 prompt 注入。命令行参数仍然可用，且会覆盖配置文件中的值；例如只生成一个候选并且不调用 LLM：

```bash
python3 scripts/generate_scenario_variants.py \
  --config configs/scenario_generation.yaml \
  --count 1 \
  --no-include-reference-episodes \
  --dry-run
```

也可以继续完全使用命令行参数：

```bash
python3 scripts/generate_scenario_variants.py --config configs/scenario_generation.yaml
```

输出结构：

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

默认每次 LLM 调用批量返回 10 个 scenario；最后不足 10 个时只请求实际剩余数量。
控制台会显示每批的 input、output、reasoning、cached、total token 和平均到每条
scenario 的 token。平均值只是统计估算，API 只能返回整批的准确用量。如果发生
重试，重试消耗也会计入该批。全部候选完成后还会输出
`TOTAL TOKENS THIS RUN`。这些统计只显示在控制台，不会写入 scenario YAML；
已存在且未重新生成的 scenario 本次计为 0。需要恢复旧的一次一条模式时添加
`--batch-size 1`。

### 固定生成 10,000 条数据

`scripts/generate_scenario_dataset.py` 固定保存了当前 30 个 dense waypoint 场景的
大小分类和 10,000 条配额，不会在运行时重新计算：

- 小场景：1–5 人。
- 中场景：1–8 人。
- 大场景：1–10 人。

数据集入口固定使用 `--batch-size 10`，所以图片、waypoint 和场景说明在一批内
只发送一次。比如某个场景需要 278 条，会发出 27 个十条批次和 1 个八条批次。
每批必须完整通过人数、职业、路线和重复检查；无效时整批重试。

每个场景中的人数按固定随机种子均衡排列，因此断点续跑和单独运行某个场景时，
相同的 scenario 编号仍会得到相同的目标人数。运行全部数据：

```bash
python3 scripts/generate_scenario_dataset.py \
  --scenes-root new_grscenes \
  --output-root new_outputs \
  --llm-config configs/gpt6.local.yaml \
  --prompt "生成自然、合理并且多样化的人机交互场景"
```

先查看固定配额而不调用 API：

```bash
python3 scripts/generate_scenario_dataset.py \
  --llm-config configs/gpt6.local.yaml \
  --prompt test \
  --dry-run
```

10,000 条数据默认会为每条 scenario 生成独立的 `scenario_preview.png`，但不会把同一
场景的数百张预览拼成总览图。完全不需要预览时可以添加 `--skip-previews`；确实需要
总览时添加 `--render-overview`。使用 `--only-scene <scene_id>` 可以只生成固定计划中的
某个场景。程序会跳过已存在且有效的结果和预览，并在最后输出跨全部场景的 token 总计。

如果生成阶段使用了 `--skip-previews`，之后仍可在不配置 LLM、也不调用 API 的情况下
离线生成每条独立预览：

```bash
python3 scripts/render_dataset_previews.py \
  --scenes-root new_grscenes \
  --output-root new_outputs_10k
```

已有预览会被跳过；需要重画时添加 `--overwrite`。这个命令只生成各 scenario 目录内的
`scenario_preview.png`，不会拼接总览图。

## 标注 / 更新 waypoint

如果一个新场景还没有 `waypoints_by_region.json`，使用：

```bash
python3 scripts/mark_waypoints_by_region.py \
  --scene-dir new_grscenes/<scene_id>
```

常用操作：

- 左键：新增 waypoint。
- `backspace` / `delete`：撤销最近一个新增 waypoint。
- `s`：保存。
- `q`：保存并退出。

如果要在已有文件后面继续追加 waypoint：

```bash
python3 scripts/mark_waypoints_by_region.py \
  --scene-dir new_grscenes/<scene_id> \
  --append
```

## 标注跨区域连接 waypoint

如果两个区域之间只有门口/窄通道可以通过，需要在 `waypoints_by_region.json` 中补 `region_transitions`：

```bash
python3 scripts/mark_region_transitions.py \
  --scene-dir new_grscenes/<scene_id>
```

操作：

- 左键点第一个区域的连接 waypoint。
- 左键点另一个区域的连接 waypoint。
- `s`：保存这一对 transition。
- `backspace` / `delete`：撤销当前选择。
- `q`：保存并退出。

如果 waypoint 名字太挡图：

```bash
python3 scripts/mark_region_transitions.py \
  --scene-dir new_grscenes/<scene_id> \
  --hide-waypoint-names
```

## 重新生成区域 waypoint 总览图

每次改完 waypoint 或 transition 后，建议重新生成检查图：

```bash
python3 scripts/draw_waypoints_by_region.py \
  --scene-dir new_grscenes/<scene_id> \
  --output new_grscenes/<scene_id>/regions_waypoints_overview.png \
  --show-region-ids \
  --hide-waypoint-names
```

## 检查已有 scenario 的轨迹

把 `scenario.yaml` 画回俯视图：

```bash
python3 scripts/topdown_click_to_world.py \
  --mapping new_grscenes/<scene_id>/topdown_mapping.json \
  --view overlay \
  --scenario new_outputs/<scene_id>/manual_scenario.yaml \
  --save-overlay new_outputs/<scene_id>/manual_scenario_check.png \
  --no-show
```
