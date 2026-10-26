#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import requests
import yaml

from scene_names import canonical_scene_id, resolve_scene_dir


DEFAULT_BEHAVIOR_TREE = "BTRegularNav.xml"
DEFAULT_MODEL = "pedestrian"
DEFAULT_DESIRED_VELOCITY = 1.0
FOLLOWING_HUMAN_SOCIAL_FORCE_FACTOR = 10.0
ROBOT_INTERMEDIATE_WAYPOINTS_PHRASE = "机器人路径由多个waypoints构成"
ROBOT_TOLERANCE_FIELDS = ("waypoint_tolerance", "final_goal_tolerance")
ROBOT_WAYPOINT_TOLERANCE = 0.50
ROBOT_FINAL_GOAL_TOLERANCE = 0.25
ROBOT_SOCIAL_ATTRIBUTES = ("passive", "neutral", "aggressive")
HUMAN_BEHAVIOR_PROFILES: dict[str, dict[str, Any]] = {
    "neutral": {
        "type": 1,
        "behavior_tree": "BTRegularNav.xml",
        "behavior": {"type": 1},
    },
    "passive": {
        "type": 3,
        "behavior_tree": "BTSurprisedNav.xml",
        "behavior": {
            "type": 3,
            "dist": 0.8,
            "duration": 1.0,
            "once": False,
            "vel": 0.0,
            "obstacle_force_factor": 2.0,
            "social_force_factor": 10.0,
        },
        "desired_velocity": 0.8,
    },
}
HUMAN_BEHAVIOR_ALIASES = {
    "regular": "neutral",
    "surprised": "passive",
}
AVAILABLE_AGENT_MODELS = (
    "pedestrian",
    "doctor",
    "police",
    "construction_worker",
)
AGENT_MODEL_LIMITS = {
    "doctor": 2,
    "police": 4,
    "construction_worker": 4,
}
TOKEN_USAGE_PREFIX = "TOKEN_USAGE "


class ScenarioYamlDumper(yaml.SafeDumper):
    pass


def represent_list(dumper: yaml.Dumper, data: list[Any]) -> yaml.SequenceNode:
    flow_style = bool(data) and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow_style)


ScenarioYamlDumper.add_representer(list, represent_list)


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    reported: bool = False

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "reported": self.reported,
        }


@dataclass(frozen=True)
class LLMCallResult:
    data: Any
    usage: TokenUsage


@dataclass(frozen=True)
class CameraRayMapping:
    image_width: int
    image_height: int
    camera_projection: np.ndarray
    camera_world_transform: np.ndarray
    ground_plane_z: float

    def pixel_to_world(self, pixel_x: float, pixel_y: float) -> tuple[float, float]:
        ndc_x = 2.0 * ((pixel_x + 0.5) / self.image_width) - 1.0
        ndc_y = 1.0 - 2.0 * ((pixel_y + 0.5) / self.image_height)

        clip_near = np.array([ndc_x, ndc_y, -1.0, 1.0], dtype=np.float64)
        clip_far = np.array([ndc_x, ndc_y, 1.0, 1.0], dtype=np.float64)

        inv_projection = np.linalg.inv(self.camera_projection)
        near_camera = inv_projection @ clip_near
        far_camera = inv_projection @ clip_far
        near_camera /= near_camera[3]
        far_camera /= far_camera[3]

        near_world = near_camera @ self.camera_world_transform
        far_world = far_camera @ self.camera_world_transform
        near_world /= near_world[3]
        far_world /= far_world[3]

        ray_direction = far_world[:3] - near_world[:3]
        if abs(ray_direction[2]) < 1e-9:
            raise ValueError("Camera ray is parallel to the ground plane")

        t = (self.ground_plane_z - near_world[2]) / ray_direction[2]
        hit_world = near_world[:3] + t * ray_direction
        return float(hit_world[0]), float(hit_world[1])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Ask the LLM to select pedestrian spawn/waypoint pixels on overlay.png, "
            "then convert them into a HuNav scenario YAML."
        )
    )
    parser.add_argument(
        "--scene-dir",
        required=True,
        help="Directory containing overlay.png, topdown.json, and topdown_mapping.json.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path for the generated scenario YAML.",
    )
    parser.add_argument(
        "--mapping",
        help="Optional explicit topdown_mapping.json path. Defaults to <scene-dir>/topdown_mapping.json.",
    )
    parser.add_argument(
        "--llm-config",
        required=True,
        help="YAML config containing Gemini-compatible API settings.",
    )
    parser.add_argument(
        "--prompt",
        help="Extra user request for the LLM, for example desired number of pedestrians and behavior.",
    )
    parser.add_argument(
        "--prompt-file",
        help="Path to a text file containing the extra user request for the LLM.",
    )
    parser.add_argument(
        "--scene-prompt-file",
        help=(
            "Path to scene-specific rules. Defaults to <scene-dir>/scene_prompt.txt "
            "when the file exists."
        ),
    )
    parser.add_argument(
        "--reference-dir",
        action="append",
        help=(
            "Interaction reference set directory. Each episode may declare source_scene_id in "
            "metadata.yaml. Repeat to include multiple sets. Defaults to the legacy "
            "reference_episodes/<target-scene-id> layout."
        ),
    )
    parser.add_argument(
        "--reference-scene-root",
        help=(
            "Directory containing prepared source scenes used to resolve reference waypoint "
            "coordinates. Defaults to the target scene's parent directory."
        ),
    )
    parser.add_argument(
        "--include-reference-episodes",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include reference episodes in the LLM prompt. Default: enabled.",
    )
    parser.add_argument(
        "--save-llm-output",
        help="Optional path to save the raw parsed LLM JSON response before world-coordinate conversion.",
    )
    parser.add_argument(
        "--dry-run-prompt",
        action="store_true",
        help="Build and print the LLM prompt without sending an API request.",
    )
    parser.add_argument(
        "--robot-intermediate-waypoints",
        action="store_true",
        help=(
            "Temporary test mode: ask the LLM to output robot intermediate waypoints "
            "and keep them in the generated YAML."
        ),
    )
    parser.add_argument(
        "--robot-tolerances",
        action="store_true",
        help="Write waypoint_tolerance and final_goal_tolerance to the robot YAML entry.",
    )
    parser.add_argument(
        "--human-social-force-factor",
        type=float,
        help="Optional social_force_factor written to every human YAML entry.",
    )
    parser.add_argument(
        "--round",
        type=int,
        default=5,
        help="Decimal places for generated world coordinates and yaw values.",
    )
    parser.add_argument(
        "--default-model",
        default=DEFAULT_MODEL,
        choices=AVAILABLE_AGENT_MODELS,
        help=(
            "Occupation category used when an agent does not specify model. "
            f"Default: {DEFAULT_MODEL}."
        ),
    )
    parser.add_argument(
        "--default-desired-velocity",
        type=float,
        default=DEFAULT_DESIRED_VELOCITY,
        help=(
            "Desired velocity used when an agent does not specify one. "
            f"Default: {DEFAULT_DESIRED_VELOCITY}."
        ),
    )
    parser.add_argument(
        "--default-behavior-tree",
        default=DEFAULT_BEHAVIOR_TREE,
        help=f"Behavior tree used when an agent does not specify one. Default: {DEFAULT_BEHAVIOR_TREE}.",
    )
    return parser.parse_args()


def load_yaml_file(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return data


def load_llm_config(path: Path) -> dict[str, Any]:
    config = load_yaml_file(path)
    config["_config_dir"] = str(path.parent)
    return config


def config_value(config: dict[str, Any], key: str, *, required: bool = True) -> str:
    value = config.get(key)
    file_value = config.get(f"{key}_file")
    if not value and file_value:
        value_path = Path(str(file_value)).expanduser()
        if not value_path.is_absolute():
            config_dir = Path(str(config.get("_config_dir") or Path.cwd()))
            value_path = config_dir / value_path
        if not value_path.is_file():
            raise ValueError(f"LLM config {key}_file not found: {value_path}")
        if value_path.suffix.lower() in {".yaml", ".yml"}:
            value = load_yaml_file(value_path).get(key)
        else:
            value = value_path.read_text(encoding="utf-8").strip()
    env_key = config.get(f"{key}_env")
    if not value and env_key:
        value = os.environ.get(str(env_key))
    if required and not value:
        if file_value:
            raise ValueError(
                f"LLM config is missing {key}; set {key} in {value_path}"
                + (f" or env {env_key}" if env_key else "")
            )
        if env_key:
            raise ValueError(f"LLM config is missing {key}; env {env_key} is not set")
        raise ValueError(f"LLM config is missing {key}")
    return str(value or "")


def load_mapping(mapping_path: Path) -> CameraRayMapping:
    data = json.loads(mapping_path.read_text(encoding="utf-8"))
    camera_geometry = data.get("camera_geometry")
    if not camera_geometry or camera_geometry.get("method") != "camera_ray_ground_intersection":
        raise ValueError(
            f"{mapping_path} does not contain camera_geometry.method=camera_ray_ground_intersection"
        )

    return CameraRayMapping(
        image_width=int(data["image_width"]),
        image_height=int(data["image_height"]),
        camera_projection=np.array(camera_geometry["camera_projection"], dtype=np.float64).reshape(
            (4, 4), order="F"
        ),
        camera_world_transform=np.array(
            camera_geometry["camera_world_transform"], dtype=np.float64
        ).reshape((4, 4)),
        ground_plane_z=float(camera_geometry["ground_plane_z"]),
    )


def agents_from_data(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        agents = data
    elif isinstance(data, dict) and isinstance(data.get("agents"), list):
        agents = data["agents"]
    else:
        raise ValueError("LLM output must be either an agents array or an object with an agents array")

    if not agents:
        raise ValueError("LLM output contains no agents")
    if not all(isinstance(agent, dict) for agent in agents):
        raise ValueError("Every agent entry must be a JSON object")
    return agents


def load_region_annotations(scene_dir: Path) -> dict[str, Any] | None:
    topdown_json = scene_dir / "topdown.json"
    if not topdown_json.exists():
        return None
    data = json.loads(topdown_json.read_text(encoding="utf-8"))
    shapes = data.get("shapes")
    if not isinstance(shapes, list):
        return None

    regions: list[dict[str, Any]] = []
    for index, shape in enumerate(shapes):
        if not isinstance(shape, dict):
            continue
        label = shape.get("label")
        points = shape.get("points")
        if not isinstance(label, str) or not isinstance(points, list):
            continue
        regions.append(
            {
                "id": index,
                "label": label,
                "shape_type": shape.get("shape_type", "polygon"),
                "points": points,
            }
        )

    if not regions:
        return None
    return {"regions": regions}


def load_waypoint_context(scene_dir: Path) -> dict[str, Any]:
    waypoint_path = scene_dir / "waypoints_by_region.json"
    if not waypoint_path.exists():
        raise FileNotFoundError(f"Missing required waypoint list: {waypoint_path}")
    data = json.loads(waypoint_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{waypoint_path} must contain a JSON object")
    if not isinstance(data.get("region_areas"), list) or not isinstance(
        data.get("waypoints_by_region"), list
    ):
        raise ValueError(
            f"{waypoint_path} must contain region_areas and waypoints_by_region arrays"
        )
    return data


def build_waypoint_lookup(waypoint_context: dict[str, Any]) -> dict[str, tuple[float, float]]:
    lookup: dict[str, tuple[float, float]] = {}
    for region in waypoint_context.get("waypoints_by_region", []):
        if not isinstance(region, dict):
            continue
        waypoints = region.get("waypoints")
        if not isinstance(waypoints, list):
            continue
        for waypoint in waypoints:
            if not isinstance(waypoint, dict):
                continue
            waypoint_id = waypoint.get("id")
            pixel = waypoint.get("pixel")
            if not isinstance(waypoint_id, str) or not isinstance(pixel, list) or len(pixel) < 2:
                continue
            lookup[waypoint_id] = (float(pixel[0]), float(pixel[1]))
    if not lookup:
        raise ValueError("waypoints_by_region.json does not contain any waypoint ids")
    return lookup


def robot_intermediate_waypoints_requested(prompt: str) -> bool:
    """Return whether the user explicitly requests a multi-waypoint robot route."""
    normalized = "".join(prompt.split()).lower()
    return ROBOT_INTERMEDIATE_WAYPOINTS_PHRASE in normalized


def robot_tolerances_requested(prompt: str) -> bool:
    normalized = "".join(prompt.split()).lower()
    return all(field in normalized for field in ROBOT_TOLERANCE_FIELDS)


def build_waypoint_region_lookup(waypoint_context: dict[str, Any]) -> dict[str, tuple[int | None, str]]:
    lookup: dict[str, tuple[int | None, str]] = {}
    for region in waypoint_context.get("waypoints_by_region", []):
        if not isinstance(region, dict):
            continue
        raw_region_id = region.get("region_id")
        region_id = int(raw_region_id) if isinstance(raw_region_id, int) else None
        label = str(region.get("label") or "")
        waypoints = region.get("waypoints")
        if not isinstance(waypoints, list):
            continue
        for waypoint in waypoints:
            if not isinstance(waypoint, dict):
                continue
            waypoint_id = waypoint.get("id")
            if isinstance(waypoint_id, str):
                lookup[waypoint_id] = (region_id, label)
    return lookup


def transition_waypoint_ids(waypoint_context: dict[str, Any]) -> set[str]:
    waypoint_ids: set[str] = set()
    transitions = waypoint_context.get("region_transitions")
    if not isinstance(transitions, list):
        return waypoint_ids
    for transition in transitions:
        if not isinstance(transition, dict):
            continue
        through_waypoints = transition.get("through_waypoints")
        if not isinstance(through_waypoints, list):
            continue
        waypoint_ids.update(str(waypoint_id) for waypoint_id in through_waypoints if isinstance(waypoint_id, str))
    return waypoint_ids


def format_agent_models_for_prompt(models: tuple[str, ...]) -> str:
    lines: list[str] = []
    for model in models:
        limit = AGENT_MODEL_LIMITS.get(model)
        suffix = "（数量不限）" if limit is None else f"（每个 scenario 最多 {limit} 人）"
        lines.append(f"- {model}{suffix}")
    return "\n".join(lines)


def read_prompt(args: argparse.Namespace) -> str:
    prompt_parts: list[str] = []
    if args.prompt:
        prompt_parts.append(args.prompt)
    if args.prompt_file:
        prompt_parts.append(Path(args.prompt_file).expanduser().read_text(encoding="utf-8").strip())
    return "\n\n".join(part for part in prompt_parts if part.strip())


def read_scene_prompt(scene_dir: Path, args: argparse.Namespace) -> str:
    if args.scene_prompt_file:
        path = Path(args.scene_prompt_file).expanduser().resolve()
    else:
        path = scene_dir / "scene_prompt.txt"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def reference_waypoint_ids(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_name} must be a non-empty waypoint id array")
    ids: list[str] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str):
            ids.append(item["id"])
        else:
            raise ValueError(f"{field_name}[{index}] must be a waypoint id or object with id")
    return ids


def enrich_reference_route(
    route: dict[str, Any],
    waypoint_lookup: dict[str, tuple[float, float]],
    field_name: str,
) -> dict[str, Any]:
    enriched = dict(route)
    for key in ("spawn_waypoint", "start_waypoint", "goal_waypoint"):
        value = enriched.get(key)
        if isinstance(value, str):
            if value not in waypoint_lookup:
                raise ValueError(f"{field_name}.{key} references unknown waypoint: {value}")
            enriched[key] = {"id": value, "pixel": list(waypoint_lookup[value])}

    if "waypoints" in enriched:
        ids = reference_waypoint_ids(enriched["waypoints"], f"{field_name}.waypoints")
        enriched["waypoints"] = [
            {"id": waypoint_id, "pixel": list(waypoint_lookup[waypoint_id])}
            for waypoint_id in ids
        ]
    return enriched


def reference_route_objects(episode: dict[str, Any]) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for role in ("pedestrian", "robot"):
        route = episode.get(role)
        if isinstance(route, dict):
            routes.append(route)
    agents = episode.get("agents")
    if isinstance(agents, list):
        routes.extend(agent for agent in agents if isinstance(agent, dict))
    return routes


def add_portable_reference_geometry(
    episode: dict[str, Any],
    source_scene_id: str,
    image_size: tuple[int, int],
) -> None:
    points: list[dict[str, Any]] = []
    for route in reference_route_objects(episode):
        for key in ("spawn_waypoint", "start_waypoint", "goal_waypoint"):
            point = route.get(key)
            if isinstance(point, dict) and isinstance(point.get("pixel"), list):
                points.append(point)
        waypoints = route.get("waypoints")
        if isinstance(waypoints, list):
            points.extend(
                point
                for point in waypoints
                if isinstance(point, dict) and isinstance(point.get("pixel"), list)
            )

    if not points:
        return
    xs = [float(point["pixel"][0]) for point in points]
    ys = [float(point["pixel"][1]) for point in points]
    center_x = (min(xs) + max(xs)) / 2.0
    center_y = (min(ys) + max(ys)) / 2.0
    scale = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    for point, x, y in zip(points, xs, ys):
        point["interaction_relative"] = [
            round((x - center_x) / scale, 6),
            round((y - center_y) / scale, 6),
        ]

    episode["geometry_frame"] = {
        "source_scene_id": source_scene_id,
        "source_image_size": [image_size[0], image_size[1]],
        "pixel_axes": "x_right_y_down",
        "interaction_relative_origin_pixel": [round(center_x, 6), round(center_y, 6)],
        "interaction_relative_scale_pixel": round(scale, 6),
        "interaction_relative_description": (
            "Translation- and uniform-scale-invariant coordinates shared by all human and robot "
            "path points in this episode; angles, directions, intersections, and relative order "
            "are preserved."
        ),
    }


def load_reference_episodes(
    reference_dir: Path | None,
    waypoint_lookup: dict[str, tuple[float, float]],
    source_scene_id: str | None = None,
    image_size: tuple[int, int] | None = None,
) -> list[dict[str, Any]]:
    if reference_dir is None or not reference_dir.is_dir():
        return []

    episodes: list[dict[str, Any]] = []
    sources: list[Path] = []
    for path in sorted(reference_dir.iterdir()):
        if path.name.startswith("_"):
            continue
        if path.is_dir():
            for filename in ("reference_episode.json", "llm_agents.json"):
                candidate = path / filename
                if candidate.is_file():
                    sources.append(candidate)
                    break
        elif path.suffix.lower() in {".yaml", ".yml", ".json"}:
            sources.append(path)

    for path in sources:
        episodes.append(
            load_reference_episode_file(
                path,
                waypoint_lookup,
                str(path.relative_to(reference_dir)),
                source_scene_id,
                image_size,
            )
        )
    return episodes


def load_reference_episode_file(
    path: Path,
    waypoint_lookup: dict[str, tuple[float, float]],
    source_file: str,
    source_scene_id: str | None = None,
    image_size: tuple[int, int] | None = None,
) -> dict[str, Any]:
    raw = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.suffix.lower() == ".json"
        else load_yaml_file(path)
    )
    if not isinstance(raw, dict):
        raise ValueError(f"Reference episode {path} must contain a YAML object")
    episode = dict(raw)
    episode["source_file"] = source_file
    for role in ("pedestrian", "robot"):
        if role in episode:
            if not isinstance(episode[role], dict):
                raise ValueError(f"Reference episode {path}: {role} must be an object")
            episode[role] = enrich_reference_route(
                episode[role], waypoint_lookup, f"{path.name}.{role}"
            )
    agents = episode.get("agents")
    if agents is not None:
        if not isinstance(agents, list) or not agents:
            raise ValueError(f"Reference episode {path}: agents must be a non-empty array")
        episode["agents"] = [
            enrich_reference_route(agent, waypoint_lookup, f"{path.name}.agents[{index}]")
            for index, agent in enumerate(agents)
            if isinstance(agent, dict)
        ]
        if len(episode["agents"]) != len(agents):
            raise ValueError(f"Reference episode {path}: every agent must be an object")
    if source_scene_id is not None and image_size is not None:
        add_portable_reference_geometry(episode, source_scene_id, image_size)
    return episode


def reference_episode_sources(reference_dir: Path) -> list[Path]:
    sources: list[Path] = []
    for path in sorted(reference_dir.iterdir()):
        if path.name.startswith("_") or path.name == "metadata.yaml":
            continue
        if path.is_dir():
            for filename in ("reference_episode.json", "llm_agents.json"):
                candidate = path / filename
                if candidate.is_file():
                    sources.append(candidate)
                    break
        elif path.suffix.lower() in {".yaml", ".yml", ".json"}:
            sources.append(path)
    return sources


def load_reference_metadata(source: Path, reference_dir: Path) -> dict[str, Any]:
    metadata_path = source.parent / "metadata.yaml"
    if source.parent == reference_dir:
        return {}
    if not metadata_path.is_file():
        raise ValueError(
            f"Reference episode {source.parent} is missing required metadata.yaml"
        )
    metadata = load_yaml_file(metadata_path)
    if not isinstance(metadata, dict):
        raise ValueError(f"{metadata_path} must contain a YAML object")
    return metadata


def load_reference_episodes_from_sources(
    reference_dirs: list[Path],
    reference_scene_root: Path,
) -> list[dict[str, Any]]:
    episodes: list[dict[str, Any]] = []
    scene_cache: dict[str, tuple[dict[str, tuple[float, float]], tuple[int, int]]] = {}
    for reference_dir in reference_dirs:
        if not reference_dir.is_dir():
            continue
        for source in reference_episode_sources(reference_dir):
            metadata = load_reference_metadata(source, reference_dir)
            source_scene_id = metadata.get("source_scene_id", reference_dir.name)
            if not isinstance(source_scene_id, str) or not source_scene_id:
                raise ValueError(
                    f"{source.parent / 'metadata.yaml'} must define a non-empty source_scene_id"
                )
            source_scene_id = canonical_scene_id(source_scene_id)
            if source_scene_id not in scene_cache:
                source_scene_dir = resolve_scene_dir(reference_scene_root / source_scene_id)
                source_context = load_waypoint_context(source_scene_dir)
                source_mapping = load_mapping(source_scene_dir / "topdown_mapping.json")
                scene_cache[source_scene_id] = (
                    build_waypoint_lookup(source_context),
                    (source_mapping.image_width, source_mapping.image_height),
                )
            waypoint_lookup, image_size = scene_cache[source_scene_id]
            episode = load_reference_episode_file(
                source,
                waypoint_lookup,
                str(source.relative_to(reference_dir)),
                source_scene_id,
                image_size,
            )
            if isinstance(metadata.get("interaction_type"), str):
                episode["interaction_type"] = metadata["interaction_type"]
            episode["reference_set"] = reference_dir.name
            episodes.append(episode)
    return episodes


def format_reference_episodes(episodes: list[dict[str, Any]]) -> str:
    if not episodes:
        return "无人工参考 episode。"
    return json.dumps(episodes, ensure_ascii=False, indent=2)


def build_llm_prompt(
    scene_dir: Path,
    mapping: CameraRayMapping,
    scene_prompt: str,
    user_prompt: str,
    reference_episodes: list[dict[str, Any]] | None = None,
    robot_intermediate_waypoints: bool = False,
    robot_tolerances: bool = False,
    batch_agent_counts: list[int] | None = None,
    include_reference_episodes: bool = True,
) -> str:
    waypoint_context = load_waypoint_context(scene_dir)
    waypoint_text = json.dumps(waypoint_context, ensure_ascii=False, indent=2)
    agent_models_text = format_agent_models_for_prompt(AVAILABLE_AGENT_MODELS)
    if include_reference_episodes:
        reference_rule = (
            "- 人工参考 episode 只用于学习交互几何、相对方向和行为选择；"
            "必须将其迁移到当前场景的合法 waypoint，不能照抄参考场景的 waypoint id。\n"
        )
        reference_section = (
            "人工参考 episode：\n"
            + format_reference_episodes(reference_episodes or [])
            + "\n\n"
        )
    else:
        reference_rule = ""
        reference_section = ""
    if robot_intermediate_waypoints:
        robot_waypoint_rule = (
            "- 机器人使用 start_waypoint 作为起点 waypoint id；waypoints 是后续按移动顺序排列的中间点和终点 waypoint id，"
            "用于临时测试，可以包含几个中间 waypoint。"
        )
        robot_behavior_rules = """- 机器人路径必须和行人路径一起考虑，不能只单独生成；这次是临时测试，可以为机器人输出包含中间几个 waypoint 的路径。
- 机器人 waypoints 不要过密，通常 3 到 6 个即可，包含中间点和最终目标点。
- 机器人优先在开阔区域、宽走廊和宽通道中移动，尽量选择离墙、家具、柜台、桌椅、障碍物都有一定距离的 waypoint。
- 机器人半径比行人大，不能把 start_waypoint 或任何 robot waypoint 设计在狭窄位置、贴近墙边、贴近障碍物边缘或人流密集的门口。
- 机器人路线中行人可以稍微出现在机器人视野里，但不要让机器人和行人的路线产生明显碰撞或迎面冲突。
- 机器人路线可以有简单转弯，但不要设计复杂巡逻、过长路线或多段任务。"""
        robot_schema = """"robot": {
    "name": "robot",
    "from_region": {"region_id": 0, "label": "waiting_area"},
    "to_region": {"region_id": 6, "label": "consultation_room"},
    "start_waypoint": "wp_r00_waiting_area_004",
    "waypoints": ["wp_r00_waiting_area_005", "wp_r00_waiting_area_006", "wp_r00_waiting_area_007"],
    "social_attributes": "neutral",
    "social_yielding": false,
    "behavior": "brief robot navigation purpose"
  }"""
    else:
        robot_waypoint_rule = (
            "- 机器人只需要选择 start_waypoint 和 goal_waypoint，不要输出中途 waypoint，不要输出 robot.waypoints。"
        )
        robot_behavior_rules = """- 机器人路径必须和行人路径一起考虑，不能只单独生成；机器人只需要 start 和 goal 表示大致方向，路线不要太长，不要太复杂。
- 机器人路径可以简单一点，但 start 和 goal 不要太近，内部规划时应想象机器人至少经过 2 个候选 waypoint 的距离后才能到达 goal；最终 JSON 仍然只输出 start_waypoint 和 goal_waypoint，不要输出中间经过点，不要设计复杂巡逻、长距离穿越或多段任务。"""
        robot_schema = """"robot": {
    "name": "robot",
    "from_region": {"region_id": 0, "label": "waiting_area"},
    "to_region": {"region_id": 6, "label": "consultation_room"},
    "start_waypoint": "wp_r00_waiting_area_004",
    "goal_waypoint": "wp_r00_waiting_area_005",
    "social_attributes": "neutral",
    "social_yielding": false,
    "behavior": "brief robot navigation purpose"
  }"""

    tolerance_schema = ""
    if robot_tolerances:
        tolerance_schema = f''',
    "waypoint_tolerance": {ROBOT_WAYPOINT_TOLERANCE:.2f},
    "final_goal_tolerance": {ROBOT_FINAL_GOAL_TOLERANCE:.2f}'''
        robot_schema = robot_schema.replace(
            '\n    "behavior": "brief robot navigation purpose"',
            tolerance_schema + '\n    "behavior": "brief robot navigation purpose"',
        )

    scenario_schema = f"""{{
  "agents": [
    {{
      "name": "hunav_1",
      "role": "short semantic role",
      "model": "pedestrian",
      "from_region": {{"region_id": 0, "label": "waiting_area"}},
      "to_region": {{"region_id": 6, "label": "consultation_room"}},
      "spawn_waypoint": "wp_r00_waiting_area_001",
      "waypoints": ["wp_r00_waiting_area_002", "wp_r00_waiting_area_003"],
      "desired_velocity": 1.0,
      "behavior_mode": "neutral",
      "behavior": "brief explanation"
    }}
  ],
  {robot_schema}
}}"""
    if batch_agent_counts:
        count_lines = "\n".join(
            f"  - scenarios[{index}] 必须恰好包含 {count} 个 agents。"
            for index, count in enumerate(batch_agent_counts)
        )
        output_requirement = f"""- 顶层必须是只包含 scenarios 数组的 JSON 对象。
- scenarios 必须恰好包含 {len(batch_agent_counts)} 个相互独立的 scenario，顺序不得改变。
{count_lines}
- scenarios 数组中的每一项都必须包含 agents 和 robot。"""
        output_schema = f"""{{
  "scenarios": [
    {scenario_schema}
  ]
}}"""
    else:
        output_requirement = "- 输出必须包含 agents 和 robot。"
        output_schema = scenario_schema

    return f"""你是室内行人和机器人轨迹生成器。请根据图片、用户需求、region 范围和候选 waypoint 列表生成完整场景轨迹，只返回合法 JSON。

图片说明：
- 图片 1 是 topdown overlay，用于理解场景语义和物体/房间位置，他是场景的俯瞰图并且将行人可以出现的位置用一层绿色的mask做了标记。
- 图片 2 是黑白 navmesh mask：白色区域可行走，黑色区域不可行走。

核心规则：
- 在设计路线前，先想象这个室内场景真实使用时是什么样的，构思好行人和机器人的路线逻辑，再开始选择 waypoint；这个思考过程只在内部完成，不要输出。
- 必须先整体规划场景：有哪些行人、机器人从哪里出发、分别要去哪里、是否会在走廊/门口/狭窄通道相遇或冲突。
- 必须先想清楚每个行人和机器人的场景语义与移动目的，例如为什么移动、从哪里出发、要去哪里。
- 行人路线需要拆成清楚的 region-to-region 分段：每一段都要明确是从哪个 region 去往哪个 region。明确起点和终点在navmesh mask中是连通的
- 对行人路线的每一段先按区域连接规则和门口/transition 规则规划，再从候选 waypoint list 中选择 waypoint id。
- 在同一个 region 内部移动时，要一个一个思考路线的下一个 waypoint，根据当前waypoint选择下一个，按照真实可能路线选择若干 waypoint，保持顺序自然、连贯。
- 每次选取下一个waypoint时需要去navmesh mask图中对比确定当前waypoint和下一个waypoint之间在navmesh中是连通的，如果不在同一片连通区域，则禁止选取这个waypoint
- 只能使用下方 waypoints_by_region 中存在的 waypoint id，严禁生成新坐标、新 id、spawn_pixel 或 waypoint_pixels。
{reference_rule}- 行人使用 spawn_waypoint 作为起点 waypoint id；waypoints 是后续按移动顺序排列的 waypoint id，至少 2 个。
{robot_waypoint_rule}
- 通常应避免重复选择同一个 waypoint；但如果路线语义涉及返回、折返、来回移动或原路返回，可以复用已经选过的 waypoint。
- 路径必须符合真实室内移动逻辑，不能随机选点。
- 路线绝对不能穿墙，不能穿过家具、床、桌椅、柜台、货架等障碍物，也不能穿出 navmesh 可行走区域。
- 相邻两个 waypoint 的连线必须尽量位于图片 2 的白色区域内，绝对不要穿过黑色区域。
- 如果两个 waypoint 的连线会穿墙、穿过黑色区域或穿过非绿色区域，必须选择更多中间 waypoint 绕行。
- 如果进入房间或通过门口/狭窄通道，路线应在门口两侧选择连续 waypoint。
- 如果可用 waypoint JSON 里存在 region_transitions，则跨越两个 region 时必须使用对应 transition 的 through_waypoints。
- through_waypoints 表示门口/通道两侧的固定通过点，不能跳过，不能替换，必须按移动方向连续出现在路线里。
- 如果没有对应 region_transitions，才根据图像和连接图选择最合理的门两侧 waypoint。
- 多个行人分别生成独立 agent，命名为 hunav_1、hunav_2、hunav_3等。
{robot_behavior_rules}
- 机器人 start_waypoint 与最终目标 waypoint 之间的世界坐标直线距离应至少为 2 米；请选择在俯视图上明显分离的起点和终点，不要选择彼此过于接近的 waypoint。
- 必须输出一个 robot 对象；机器人对象的 name 必须是 "robot"；如果用户没有明确机器人数量，默认只生成 1 条机器人路径。
- 根据用户 prompt 为每个行人选择 behavior_mode，且只能是 neutral、passive：
  neutral 表示 regular，沿既定路线行走并对机器人进行常规社交避让；passive 表示 surprised，看到机器人后停下并注视，让机器人先通过。
- 根据 prompt 中明确描述的行人反应选择模式，而不是只根据 interaction 类型猜测：出现“受惊、惊讶、停下、站住、观察机器人、注视机器人、等待机器人先通过、行人主动让行”等语义时选择 passive；仅描述交叉、跟随、超越、对向通行或没有明确特殊反应时选择 neutral。行人不使用 aggressive/threatening 模式。
- 用户未描述特殊行人反应时选择 neutral。不要输出 behavior_tree、behavior.type 或 behavior_parameters；程序会依据 behavior_mode 使用固定的 Arena Jazzy profile，保证不同场景之间参数一致。
- 根据用户 prompt 为机器人选择 social_attributes，且只能是 passive、neutral、aggressive：passive 更早减速并礼让，neutral 为平衡默认值，aggressive 保持路线、间距更小且巡航更快。用户未描述机器人社交风格时选择 neutral；超越或明确强势通行通常选择 aggressive，明确礼让或谨慎通行选择 passive。
- robot.social_yielding 必须是布尔值。默认且通常输出 false；只有用户 prompt 明确要求“机器人主动让行、机器人停车/减速等待行人先通过、启用 social yielding”时才输出 true。不要因为 social_attributes 为 passive 就自动开启 social_yielding。
- 如果 robot schema 中出现 waypoint_tolerance 与 final_goal_tolerance，必须严格使用 schema 给出的数值；未出现时不要输出这两个字段。
- agents[].behavior_mode、robot.social_attributes 和 robot.social_yielding 都是必填字段，不得省略、改名或输出 null。
- 场景专用规则、区域连通规则、region_transitions 和 navmesh 约束同时适用于行人和机器人。
- role 和 behavior 简短英文即可；behavior 不要包含坐标细节。
- model 字段现在表示行人的职业类别，不是具体人物资产；具体人物模型由下层系统决定。
- model 只能是 pedestrian、doctor、police、construction_worker 之一，必须使用完全一致的小写英文值。
- 同一个 scenario 中 doctor 最多 2 人，police 最多 4 人，construction_worker 最多 4 人；pedestrian 数量不限。若人数超过某个职业上限，必须改用其他合法类别，不能突破上限。
- desired_velocity 使用 0.8 到 1.5。passive 行人的最终 desired_velocity 会由程序固定为 0.8。不要输出 velocity。
- 尽量不要让行人会在同一时间走入同一个 waypoint，除非语义上需要交互、碰面、错身而过等，可以选取相邻waypoint表示这一语义。

输出要求：
- 只返回合法 JSON，不要 Markdown，不要解释。
- 不要输出任何像素坐标。
- 符合下面的 JSON schema：
{output_requirement}

{output_schema}

可选职业类别及数量限制：
{agent_models_text}

场景专用规则：
{scene_prompt or "无。"}

{reference_section}可用 region 范围和候选 waypoint 列表：
{waypoint_text}

用户需求：
{user_prompt or "请为这个室内场景生成一个规模较小且合理的行人仿真场景。"}
"""


def encode_image_part(image_path: Path) -> dict[str, Any]:
    image_bytes = image_path.read_bytes()
    return {
        "inlineData": {
            "mimeType": "image/png",
            "data": base64.b64encode(image_bytes).decode("ascii"),
        }
    }


def encode_image_data_url(image_path: Path) -> str:
    image_bytes = image_path.read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/png;base64,{image_b64}"


def extract_json_from_text(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", stripped, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(1))


def response_summary(response: requests.Response, api_key: str, limit: int = 500) -> str:
    content_type = response.headers.get("Content-Type", "<missing>")
    body = response.text
    if api_key:
        body = body.replace(api_key, "<redacted-api-key>")
        content_type = content_type.replace(api_key, "<redacted-api-key>")
    body = body.strip()
    if not body:
        body = "<empty>"
    else:
        body = re.sub(r"\s+", " ", body)
        if len(body) > limit:
            body = f"{body[:limit]}..."
    return f"status={response.status_code}, content-type={content_type}, body={body!r}"


def response_json(
    response: requests.Response,
    *,
    provider: str,
    endpoint: str,
    api_key: str,
) -> dict[str, Any]:
    if api_key:
        endpoint = endpoint.replace(api_key, "<redacted-api-key>")
    try:
        data = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        summary = response_summary(response, api_key)
        raise RuntimeError(
            f"{provider} returned a non-JSON response from {endpoint}: {summary}. "
            "Check api_base_url, API protocol compatibility, and upstream gateway status."
        ) from None
    if not isinstance(data, dict):
        raise RuntimeError(
            f"{provider} returned JSON with type {type(data).__name__} from {endpoint}; "
            "expected an object"
        )
    return data


def request_timeout(config: dict[str, Any]) -> tuple[float, float]:
    fallback = float(config.get("timeout", 180))
    connect_timeout = float(config.get("connect_timeout", min(fallback, 30)))
    read_timeout = float(config.get("read_timeout", fallback))
    if connect_timeout <= 0 or read_timeout <= 0:
        raise ValueError("LLM connect_timeout and read_timeout must be positive")
    return connect_timeout, read_timeout


def parse_gemini_response(response_json: dict[str, Any]) -> Any:
    candidates = response_json.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError(f"LLM response does not contain candidates: {response_json}")

    parts = candidates[0].get("content", {}).get("parts", [])
    text_chunks = [
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    if not text_chunks:
        raise ValueError(f"LLM response candidate does not contain text parts: {response_json}")
    return extract_json_from_text("\n".join(text_chunks))


def token_count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return 0


def token_usage_from_dict(data: Any) -> TokenUsage:
    if not isinstance(data, dict):
        return TokenUsage()
    return TokenUsage(
        input_tokens=token_count(data.get("input_tokens")),
        output_tokens=token_count(data.get("output_tokens")),
        reasoning_tokens=token_count(data.get("reasoning_tokens")),
        cached_tokens=token_count(data.get("cached_tokens")),
        total_tokens=token_count(data.get("total_tokens")),
        reported=bool(data.get("reported")),
    )


def combine_token_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    return TokenUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        reasoning_tokens=left.reasoning_tokens + right.reasoning_tokens,
        cached_tokens=left.cached_tokens + right.cached_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
        reported=left.reported or right.reported,
    )


def format_token_usage(usage: TokenUsage) -> str:
    if not usage.reported:
        return "unavailable (provider did not return token usage)"
    return (
        f"input={usage.input_tokens}, output={usage.output_tokens}, "
        f"reasoning={usage.reasoning_tokens}, cached={usage.cached_tokens}, "
        f"total={usage.total_tokens}"
    )


def parse_gemini_token_usage(response_json: dict[str, Any]) -> TokenUsage:
    usage = response_json.get("usageMetadata")
    if not isinstance(usage, dict):
        return TokenUsage()

    input_tokens = token_count(usage.get("promptTokenCount"))
    output_tokens = token_count(usage.get("candidatesTokenCount"))
    reasoning_tokens = token_count(usage.get("thoughtsTokenCount"))
    total_tokens = token_count(usage.get("totalTokenCount"))
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens + reasoning_tokens
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_tokens=token_count(usage.get("cachedContentTokenCount")),
        total_tokens=total_tokens,
        reported=True,
    )


def call_gemini_llm(
    config: dict[str, Any],
    prompt: str,
    image_paths: list[Path],
) -> LLMCallResult:
    api_base_url = config_value(config, "api_base_url").rstrip("/")
    api_key = config_value(config, "api_key")
    model_id = str(config.get("model_id") or config.get("model_path") or "")
    if not model_id:
        raise ValueError("LLM config is missing model_id/model_path")

    endpoint = f"{api_base_url}/models/{model_id}:generateContent"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}, *[encode_image_part(path) for path in image_paths]],
            }
        ],
        "generationConfig": {
            "temperature": float(config.get("temperature", 0)),
            "responseMimeType": "application/json",
        },
    }
    timeout = float(config.get("timeout", 180))

    try:
        response = requests.post(
            endpoint,
            headers={"x-goog-api-key": api_key},
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        message = str(exc).replace(api_key, "<redacted-api-key>")
        if exc.response is not None:
            message = f"{message}; {response_summary(exc.response, api_key)}"
        raise RuntimeError(f"LLM request failed: {message}") from exc
    response_data = response_json(
        response,
        provider="Gemini-compatible API",
        endpoint=endpoint,
        api_key=api_key,
    )
    return LLMCallResult(
        data=parse_gemini_response(response_data),
        usage=parse_gemini_token_usage(response_data),
    )


def parse_openai_responses_response(response_json: dict[str, Any]) -> Any:
    text_chunks: list[str] = []
    for output_item in response_json.get("output", []):
        if not isinstance(output_item, dict):
            continue
        for content_item in output_item.get("content", []):
            if isinstance(content_item, dict) and isinstance(content_item.get("text"), str):
                text_chunks.append(content_item["text"])

    if not text_chunks:
        raise ValueError(f"OpenAI response does not contain output text: {response_json}")
    return extract_json_from_text("\n".join(text_chunks))


def parse_openai_token_usage(response_json: dict[str, Any]) -> TokenUsage:
    usage = response_json.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage()

    input_details = usage.get("input_tokens_details")
    output_details = usage.get("output_tokens_details")
    cached_tokens = (
        token_count(input_details.get("cached_tokens"))
        if isinstance(input_details, dict)
        else 0
    )
    reasoning_tokens = (
        token_count(output_details.get("reasoning_tokens"))
        if isinstance(output_details, dict)
        else 0
    )
    input_tokens = token_count(usage.get("input_tokens"))
    output_tokens = token_count(usage.get("output_tokens"))
    total_tokens = token_count(usage.get("total_tokens"))
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_tokens=cached_tokens,
        total_tokens=total_tokens,
        reported=True,
    )


def parse_openai_chat_response(response_json: dict[str, Any]) -> Any:
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"OpenAI Chat response does not contain choices: {response_json}")

    message = choices[0].get("message", {})
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return extract_json_from_text(content)
    if isinstance(content, list):
        text_chunks = [
            item["text"]
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        if text_chunks:
            return extract_json_from_text("\n".join(text_chunks))
    raise ValueError(f"OpenAI Chat response does not contain message text: {response_json}")


def call_openai_chat_llm(
    config: dict[str, Any],
    prompt: str,
    image_paths: list[Path],
) -> LLMCallResult:
    api_base_url = config_value(config, "api_base_url").rstrip("/")
    api_key = config_value(config, "api_key")
    model_id = config_value(config, "model_id")
    endpoint = f"{api_base_url}/chat/completions"
    image_detail = str(config.get("image_detail", "high"))

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content.extend(
        {
            "type": "image_url",
            "image_url": {
                "url": encode_image_data_url(path),
                "detail": image_detail,
            },
        }
        for path in image_paths
    )
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": [{"role": "user", "content": content}],
    }
    if config.get("temperature") is not None:
        payload["temperature"] = float(config["temperature"])
    if config.get("max_tokens"):
        payload["max_tokens"] = int(config["max_tokens"])
    if bool(config.get("json_mode", False)):
        payload["response_format"] = {"type": "json_object"}

    timeout = request_timeout(config)
    try:
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        raise RuntimeError(
            f"OpenAI Chat LLM request timed out for model {model_id} at {endpoint} "
            f"(connect timeout={timeout[0]}s, read timeout={timeout[1]}s, "
            f"images={len(image_paths)}). Increase read_timeout or confirm that the model "
            "supports image input."
        ) from exc
    except requests.RequestException as exc:
        message = str(exc).replace(api_key, "<redacted-api-key>")
        if exc.response is not None:
            message = f"{message}; {response_summary(exc.response, api_key)}"
        raise RuntimeError(f"OpenAI Chat LLM request failed: {message}") from exc
    response_data = response_json(
        response,
        provider="OpenAI Chat-compatible API",
        endpoint=endpoint,
        api_key=api_key,
    )
    usage = response_data.get("usage")
    if isinstance(usage, dict) and "prompt_tokens" in usage:
        usage = {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "output_tokens_details": usage.get("completion_tokens_details"),
            "input_tokens_details": usage.get("prompt_tokens_details"),
        }
        response_data = {**response_data, "usage": usage}
    return LLMCallResult(
        data=parse_openai_chat_response(response_data),
        usage=parse_openai_token_usage(response_data),
    )


def call_openai_responses_llm(
    config: dict[str, Any],
    prompt: str,
    image_paths: list[Path],
) -> LLMCallResult:
    api_base_url = config_value(config, "api_base_url").rstrip("/")
    api_key = config_value(config, "api_key")
    model_id = config_value(config, "model_id")
    endpoint = f"{api_base_url}/responses"
    image_detail = str(config.get("image_detail", "high"))

    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    content.extend(
        {
            "type": "input_image",
            "image_url": encode_image_data_url(path),
            "detail": image_detail,
        }
        for path in image_paths
    )
    payload: dict[str, Any] = {
        "model": model_id,
        "input": [{"role": "user", "content": content}],
    }
    if config.get("temperature") is not None:
        payload["temperature"] = float(config["temperature"])
    if config.get("max_output_tokens"):
        payload["max_output_tokens"] = int(config["max_output_tokens"])
    if config.get("reasoning_effort"):
        payload["reasoning"] = {"effort": str(config["reasoning_effort"])}

    timeout = request_timeout(config)
    try:
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        message = str(exc).replace(api_key, "<redacted-api-key>")
        if exc.response is not None:
            message = f"{message}; {response_summary(exc.response, api_key)}"
        raise RuntimeError(f"OpenAI LLM request failed: {message}") from exc
    response_data = response_json(
        response,
        provider="OpenAI-compatible API",
        endpoint=endpoint,
        api_key=api_key,
    )
    return LLMCallResult(
        data=parse_openai_responses_response(response_data),
        usage=parse_openai_token_usage(response_data),
    )


def call_llm_with_usage(
    config: dict[str, Any],
    prompt: str,
    image_paths: list[Path],
) -> LLMCallResult:
    llm_type = str(config.get("type", "gemini")).lower()
    if llm_type in {"gemini", "google"}:
        return call_gemini_llm(config, prompt, image_paths)
    if llm_type in {"openai_chat", "chat", "chat_completions"}:
        return call_openai_chat_llm(config, prompt, image_paths)
    if llm_type in {"openai", "openai_responses", "responses"}:
        return call_openai_responses_llm(config, prompt, image_paths)
    raise ValueError(f"Unsupported LLM config type: {llm_type}")


def call_llm(config: dict[str, Any], prompt: str, image_paths: list[Path]) -> Any:
    """Compatibility wrapper returning only parsed model data."""
    return call_llm_with_usage(config, prompt, image_paths).data


def extract_pixel(value: Any, field_name: str) -> tuple[float, float]:
    if isinstance(value, dict):
        if "pixel" in value:
            return extract_pixel(value["pixel"], field_name)
        if "point" in value:
            return extract_pixel(value["point"], field_name)
        if "x" in value and "y" in value:
            return float(value["x"]), float(value["y"])

    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return float(value[0]), float(value[1])

    raise ValueError(f"{field_name} must be [x, y], {{'x': x, 'y': y}}, or {{'pixel': [x, y]}}")


def extract_waypoint_id(value: Any, field_name: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("id", "waypoint", "waypoint_id"):
            if isinstance(value.get(key), str):
                return value[key]
    raise ValueError(f"{field_name} must be a waypoint id string")


def pixel_from_waypoint_id(
    waypoint_id: str,
    waypoint_lookup: dict[str, tuple[float, float]],
    field_name: str,
) -> tuple[float, float]:
    if waypoint_id not in waypoint_lookup:
        raise ValueError(f"{field_name} references unknown waypoint id: {waypoint_id}")
    return waypoint_lookup[waypoint_id]


def extract_spawn_pixel(
    agent: dict[str, Any],
    agent_name: str,
    waypoint_lookup: dict[str, tuple[float, float]] | None = None,
) -> tuple[float, float]:
    if waypoint_lookup is not None:
        for key in ("spawn_waypoint", "spawn_waypoint_id", "spawn_id", "start_waypoint"):
            if key in agent:
                waypoint_id = extract_waypoint_id(agent[key], f"{agent_name}.{key}")
                return pixel_from_waypoint_id(waypoint_id, waypoint_lookup, f"{agent_name}.{key}")

    for key in ("spawn_pixel", "spawn", "pose_pixel", "start_pixel", "start"):
        if key in agent:
            return extract_pixel(agent[key], f"{agent_name}.{key}")
    raise ValueError(f"{agent_name} is missing spawn_pixel")


def extract_waypoint_pixels(
    agent: dict[str, Any],
    agent_name: str,
    waypoint_lookup: dict[str, tuple[float, float]] | None = None,
) -> list[tuple[float, float]]:
    raw_waypoints = None
    if waypoint_lookup is not None:
        for key in ("waypoints", "waypoint_ids", "path_waypoints", "route_waypoints", "path"):
            if key in agent:
                raw_waypoints = agent[key]
                break

        if raw_waypoints is not None:
            if not isinstance(raw_waypoints, list) or not raw_waypoints:
                raise ValueError(f"{agent_name} must contain a non-empty waypoint id array")
            return [
                pixel_from_waypoint_id(
                    extract_waypoint_id(waypoint, f"{agent_name}.{key}[{index}]"),
                    waypoint_lookup,
                    f"{agent_name}.{key}[{index}]",
                )
                for index, waypoint in enumerate(raw_waypoints)
            ]

    for key in ("waypoint_pixels", "waypoints_pixel", "waypoints", "path_pixels", "path"):
        if key in agent:
            raw_waypoints = agent[key]
            break

    if not isinstance(raw_waypoints, list) or not raw_waypoints:
        raise ValueError(f"{agent_name} must contain a non-empty waypoint_pixels array")

    return [
        extract_pixel(waypoint, f"{agent_name}.waypoint_pixels[{index}]")
        for index, waypoint in enumerate(raw_waypoints)
    ]


def validate_pixel(mapping: CameraRayMapping, pixel: tuple[float, float], field_name: str) -> None:
    x, y = pixel
    if not (0.0 <= x < mapping.image_width and 0.0 <= y < mapping.image_height):
        raise ValueError(
            f"{field_name} pixel ({x}, {y}) is outside image bounds "
            f"0 <= x < {mapping.image_width}, 0 <= y < {mapping.image_height}"
        )


def yaw_degrees(from_xy: tuple[float, float], to_xy: tuple[float, float]) -> float:
    dx = to_xy[0] - from_xy[0]
    dy = to_xy[1] - from_xy[1]
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return 0.0
    return math.degrees(math.atan2(dy, dx))


def rounded(value: float, places: int) -> float:
    return round(float(value), places)


def route_from_agent(
    agent: dict[str, Any],
    name: str,
    mapping: CameraRayMapping,
    args: argparse.Namespace,
    waypoint_lookup: dict[str, tuple[float, float]] | None = None,
) -> tuple[list[float], list[list[float]]]:
    spawn_pixel = extract_spawn_pixel(agent, name, waypoint_lookup)
    waypoint_pixels = extract_waypoint_pixels(agent, name, waypoint_lookup)

    validate_pixel(mapping, spawn_pixel, f"{name}.spawn_pixel")
    for waypoint_index, waypoint_pixel in enumerate(waypoint_pixels):
        validate_pixel(mapping, waypoint_pixel, f"{name}.waypoint_pixels[{waypoint_index}]")

    spawn_world = mapping.pixel_to_world(*spawn_pixel)
    waypoint_worlds = [mapping.pixel_to_world(*pixel) for pixel in waypoint_pixels]
    route_points = [spawn_world, *waypoint_worlds]
    route_yaws = [
        yaw_degrees(route_points[i], route_points[i + 1])
        for i in range(len(route_points) - 1)
    ]
    last_yaw = route_yaws[-1] if route_yaws else 0.0

    spawn_yaw = float(agent.get("yaw", agent.get("spawn_yaw", route_yaws[0] if route_yaws else 0.0)))
    waypoint_yaws = [*route_yaws[1:], last_yaw]
    pose = [
        rounded(spawn_world[0], args.round),
        rounded(spawn_world[1], args.round),
        rounded(spawn_yaw, args.round),
    ]
    waypoints = [
        [
            rounded(world_xy[0], args.round),
            rounded(world_xy[1], args.round),
            rounded(waypoint_yaws[waypoint_index], args.round),
        ]
        for waypoint_index, world_xy in enumerate(waypoint_worlds)
    ]
    return pose, waypoints


def robot_from_data(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    robot = data.get("robot")
    if isinstance(robot, dict):
        return robot
    robots = data.get("robots")
    if isinstance(robots, list) and robots and isinstance(robots[0], dict):
        return robots[0]
    return None


def behavior_mode_from_agent(agent: dict[str, Any]) -> str:
    raw_mode = agent.get("behavior_mode") or agent.get("hunav_behavior")
    if raw_mode is not None:
        mode = str(raw_mode).strip().lower()
        mode = HUMAN_BEHAVIOR_ALIASES.get(mode, mode)
        if mode not in HUMAN_BEHAVIOR_PROFILES:
            raise ValueError(
                f"Unsupported human behavior_mode {raw_mode!r}; expected one of "
                f"{', '.join(HUMAN_BEHAVIOR_PROFILES)}"
            )
        return mode

    behavior_tree = agent.get("behavior_tree")
    if isinstance(behavior_tree, str):
        for mode, profile in HUMAN_BEHAVIOR_PROFILES.items():
            if behavior_tree == profile["behavior_tree"]:
                return mode

    raw_behavior = agent.get("behavior")
    if isinstance(raw_behavior, dict):
        raw_type = raw_behavior.get("type")
        for mode, profile in HUMAN_BEHAVIOR_PROFILES.items():
            if raw_type == profile["type"]:
                return mode
    return "neutral"


def normalize_hunav_behavior(agent: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    mode = behavior_mode_from_agent(agent)
    profile = HUMAN_BEHAVIOR_PROFILES[mode]
    # The three Human modes are intentionally closed profiles. Prompt-level parameter
    # changes would make otherwise comparable interaction scenarios non-reproducible.
    return str(profile["behavior_tree"]), dict(profile["behavior"])


def normalize_robot_social_attributes(robot: dict[str, Any]) -> str:
    raw_attributes = robot.get("social_attributes", "neutral")
    attributes = str(raw_attributes).strip().lower()
    if attributes not in ROBOT_SOCIAL_ATTRIBUTES:
        raise ValueError(
            f"Unsupported robot social_attributes {raw_attributes!r}; expected one of "
            f"{', '.join(ROBOT_SOCIAL_ATTRIBUTES)}"
        )
    return attributes


def normalize_robot_social_yielding(robot: dict[str, Any]) -> bool:
    value = robot.get("social_yielding", False)
    if not isinstance(value, bool):
        raise ValueError("robot.social_yielding must be true or false")
    return value


def validate_llm_social_fields(data: Any) -> None:
    """Require the LLM to make every social-behavior choice explicit."""
    if not isinstance(data, dict):
        raise ValueError("LLM output must be a JSON object")

    agents = data.get("agents")
    if not isinstance(agents, list):
        raise ValueError("LLM output is missing agents")
    for index, agent in enumerate(agents):
        if not isinstance(agent, dict):
            raise ValueError(f"agents[{index}] must be an object")
        if "behavior_mode" not in agent:
            raise ValueError(f"agents[{index}].behavior_mode is required")
        mode = agent["behavior_mode"]
        if not isinstance(mode, str) or mode.strip().lower() not in HUMAN_BEHAVIOR_PROFILES:
            raise ValueError(
                f"agents[{index}].behavior_mode must be one of: "
                f"{', '.join(HUMAN_BEHAVIOR_PROFILES)}"
            )

    robot = data.get("robot")
    if not isinstance(robot, dict):
        raise ValueError("LLM output is missing robot")
    if "social_attributes" not in robot:
        raise ValueError("robot.social_attributes is required")
    if "social_yielding" not in robot:
        raise ValueError("robot.social_yielding is required")
    normalize_robot_social_attributes(robot)
    normalize_robot_social_yielding(robot)


def robot_route_ids(
    robot: dict[str, Any],
    *,
    prefer_waypoints: bool = False,
) -> tuple[str, list[str]] | None:
    start_id: str | None = None
    for key in ("start_waypoint", "start_waypoint_id", "spawn_waypoint", "spawn_waypoint_id", "spawn_id"):
        if key in robot:
            start_id = extract_waypoint_id(robot[key], f"robot.{key}")
            break
    if start_id is None:
        return None

    raw_waypoints = None
    for key in ("waypoints", "waypoint_ids", "path_waypoints", "route_waypoints", "path"):
        if key in robot:
            raw_waypoints = robot[key]
            break
    if prefer_waypoints and isinstance(raw_waypoints, list) and raw_waypoints:
        waypoint_ids = [
            extract_waypoint_id(waypoint, f"robot.waypoints[{index}]")
            for index, waypoint in enumerate(raw_waypoints)
        ]
        return start_id, waypoint_ids

    for key in ("goal_waypoint", "goal_waypoint_id", "goal_id", "target_waypoint", "target_waypoint_id"):
        if key in robot:
            return start_id, [extract_waypoint_id(robot[key], f"robot.{key}")]

    if not isinstance(raw_waypoints, list) or not raw_waypoints:
        return None
    waypoint_ids = [
        extract_waypoint_id(waypoint, f"robot.waypoints[{index}]")
        for index, waypoint in enumerate(raw_waypoints)
    ]
    return start_id, waypoint_ids


def normalize_robot_agent(
    robot: dict[str, Any],
    keep_intermediate_waypoints: bool = False,
) -> dict[str, Any]:
    route = robot_route_ids(robot, prefer_waypoints=keep_intermediate_waypoints)
    if route is None:
        return robot
    start_id, waypoint_ids = route
    route_ids = [start_id, *waypoint_ids]
    if len(route_ids) < 2:
        return robot

    sparse_robot = dict(robot)
    for key in (
        "spawn_waypoint",
        "spawn_waypoint_id",
        "spawn_id",
        "start_waypoint_id",
        "goal_waypoint_id",
        "goal_id",
        "target_waypoint",
        "target_waypoint_id",
        "waypoint_ids",
        "path_waypoints",
        "route_waypoints",
        "path",
    ):
        sparse_robot.pop(key, None)
    sparse_robot["name"] = "robot"
    sparse_robot["start_waypoint"] = route_ids[0]
    sparse_robot["goal_waypoint"] = route_ids[-1]
    sparse_robot["waypoints"] = route_ids[1:] if keep_intermediate_waypoints else [route_ids[-1]]
    return sparse_robot


def validate_robot_intermediate_waypoints(robot: dict[str, Any]) -> None:
    route = robot_route_ids(robot, prefer_waypoints=True)
    if route is None or len(route[1]) < 2:
        raise ValueError(
            "robot.waypoints must contain at least one intermediate waypoint and a final goal"
        )


def build_robot_entry(
    robot: dict[str, Any],
    mapping: CameraRayMapping,
    args: argparse.Namespace,
    waypoint_lookup: dict[str, tuple[float, float]] | None = None,
    waypoint_context: dict[str, Any] | None = None,
    keep_intermediate_waypoints: bool = False,
    include_tolerances: bool = False,
) -> dict[str, Any]:
    if keep_intermediate_waypoints:
        validate_robot_intermediate_waypoints(robot)
    robot = normalize_robot_agent(robot, keep_intermediate_waypoints)
    pose, waypoints = route_from_agent(robot, "robot", mapping, args, waypoint_lookup)
    entry = {
        "name": "robot",
        "pose": pose,
        "social_attributes": normalize_robot_social_attributes(robot),
        "social_yielding": normalize_robot_social_yielding(robot),
        "waypoints": waypoints,
        "behavior": str(robot.get("behavior") or ""),
    }
    if include_tolerances:
        entry["waypoint_tolerance"] = ROBOT_WAYPOINT_TOLERANCE
        entry["final_goal_tolerance"] = ROBOT_FINAL_GOAL_TOLERANCE
    return entry


def validated_agent_models(
    agents: list[dict[str, Any]],
    default_model: str,
) -> list[str]:
    models: list[str] = []
    counts = {model: 0 for model in AVAILABLE_AGENT_MODELS}

    for index, agent in enumerate(agents, start=1):
        name = str(agent.get("name") or f"hunav_{index}")
        raw_model = agent.get("model") or default_model
        if not isinstance(raw_model, str) or raw_model not in AVAILABLE_AGENT_MODELS:
            allowed = ", ".join(AVAILABLE_AGENT_MODELS)
            raise ValueError(
                f"{name}.model must be one of: {allowed}; got {raw_model!r}"
            )
        models.append(raw_model)
        counts[raw_model] += 1

    for model, limit in AGENT_MODEL_LIMITS.items():
        if counts[model] > limit:
            raise ValueError(
                f"scenario contains {counts[model]} {model} agents; maximum is {limit}"
            )
    return models


def build_scenario(
    agents: list[dict[str, Any]],
    mapping: CameraRayMapping,
    args: argparse.Namespace,
    waypoint_lookup: dict[str, tuple[float, float]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    dynamic_agents: list[dict[str, Any]] = []
    agent_models = validated_agent_models(agents, args.default_model)

    for index, (agent, agent_model) in enumerate(zip(agents, agent_models), start=1):
        name = str(agent.get("name") or f"hunav_{index}")
        pose, waypoints = route_from_agent(agent, name, mapping, args, waypoint_lookup)
        behavior_mode = behavior_mode_from_agent(agent)
        behavior_profile = HUMAN_BEHAVIOR_PROFILES[behavior_mode]
        behavior_tree, behavior = normalize_hunav_behavior(agent)
        desired_velocity = behavior_profile.get(
            "desired_velocity",
            agent.get("desired_velocity", args.default_desired_velocity),
        )

        entry = {
            "name": name,
            "model": agent_model,
            "pose": pose,
            "behavior_tree": behavior_tree,
            "behavior": behavior,
            "desired_velocity": rounded(
                float(desired_velocity),
                args.round,
            ),
        }
        social_force_factor = getattr(args, "human_social_force_factor", None)
        if social_force_factor is not None and "social_force_factor" not in behavior:
            entry["social_force_factor"] = float(social_force_factor)
        entry["waypoints"] = waypoints
        dynamic_agents.append(entry)

    return {"dynamic": dynamic_agents}


def main() -> None:
    args = parse_args()
    scene_dir = resolve_scene_dir(Path(args.scene_dir))
    mapping_path = (
        Path(args.mapping).expanduser().resolve()
        if args.mapping
        else scene_dir / "topdown_mapping.json"
    )
    output_path = Path(args.output).expanduser().resolve()

    mapping = load_mapping(mapping_path)
    llm_config_path = Path(args.llm_config).expanduser().resolve()
    llm_config = load_llm_config(llm_config_path)
    overlay_image_path = scene_dir / "overlay.png"
    if not overlay_image_path.exists():
        raise FileNotFoundError(f"Missing required overlay image: {overlay_image_path}")
    navmesh_image_path = scene_dir / "navmesh_mask.png"
    if not navmesh_image_path.exists():
        raise FileNotFoundError(f"Missing required navmesh mask image: {navmesh_image_path}")
    llm_image_paths = [overlay_image_path, navmesh_image_path]

    waypoint_context = load_waypoint_context(scene_dir)
    waypoint_lookup = build_waypoint_lookup(waypoint_context)
    reference_episodes: list[dict[str, Any]] = []
    reference_dirs: list[Path] = []
    if args.include_reference_episodes:
        reference_dirs = (
            [Path(path).expanduser().resolve() for path in args.reference_dir]
            if args.reference_dir
            else [(Path("reference_episodes") / scene_dir.name).resolve()]
        )
        reference_scene_root = (
            Path(args.reference_scene_root).expanduser().resolve()
            if args.reference_scene_root
            else scene_dir.parent
        )
        reference_episodes = load_reference_episodes_from_sources(
            reference_dirs, reference_scene_root
        )
    prompt = build_llm_prompt(
        scene_dir,
        mapping,
        read_scene_prompt(scene_dir, args),
        read_prompt(args),
        reference_episodes=reference_episodes,
        robot_intermediate_waypoints=args.robot_intermediate_waypoints,
        robot_tolerances=getattr(args, "robot_tolerances", False),
        include_reference_episodes=args.include_reference_episodes,
    )
    if args.dry_run_prompt:
        print(prompt)
        print("\nAttached images:")
        for image_index, image_path in enumerate(llm_image_paths, start=1):
            print(f"{image_index}. {image_path}")
        if args.include_reference_episodes:
            print(
                f"\nReference episodes: {len(reference_episodes)} from "
                + ", ".join(str(path) for path in reference_dirs)
            )
        else:
            print("\nReference episodes: disabled")
        return

    llm_result = call_llm_with_usage(llm_config, prompt, llm_image_paths)
    llm_data = llm_result.data
    validate_llm_social_fields(llm_data)
    print(
        TOKEN_USAGE_PREFIX
        + json.dumps(llm_result.usage.to_dict(), separators=(",", ":")),
        flush=True,
    )

    if args.save_llm_output:
        save_path = Path(args.save_llm_output).expanduser().resolve()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(
            json.dumps(llm_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    agents = agents_from_data(llm_data)

    scenario = build_scenario(agents, mapping, args, waypoint_lookup)
    robot = robot_from_data(llm_data)
    if robot is not None:
        scenario["robot"] = build_robot_entry(
            robot,
            mapping,
            args,
            waypoint_lookup,
            waypoint_context,
            args.robot_intermediate_waypoints,
            getattr(args, "robot_tolerances", False),
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "# Generated from topdown pixel waypoints\n\n"
        + yaml.dump(
            scenario,
            Dumper=ScenarioYamlDumper,
            sort_keys=False,
            allow_unicode=False,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {len(scenario['dynamic'])} agents to {output_path}")


if __name__ == "__main__":
    main()
