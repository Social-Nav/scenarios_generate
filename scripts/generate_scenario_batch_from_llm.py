#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from generate_scenario_from_llm import (
    DEFAULT_BEHAVIOR_TREE,
    DEFAULT_DESIRED_VELOCITY,
    DEFAULT_MODEL,
    TOKEN_USAGE_PREFIX,
    ScenarioYamlDumper,
    agents_from_data,
    build_llm_prompt,
    build_robot_entry,
    build_scenario,
    build_waypoint_lookup,
    call_llm_with_usage,
    load_mapping,
    load_reference_episodes_from_sources,
    load_waypoint_context,
    load_llm_config,
    robot_from_data,
    validate_llm_social_fields,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate one batch of scenarios with a single multimodal LLM call."
    )
    parser.add_argument("--scene-dir", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--llm-config", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--reference-dir", action="append")
    parser.add_argument("--reference-scene-root")
    parser.add_argument(
        "--include-reference-episodes",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--robot-intermediate-waypoints",
        action="store_true",
        help="Require and preserve a multi-waypoint robot route.",
    )
    parser.add_argument("--structured-parameters")
    parser.add_argument(
        "--batch-items",
        required=True,
        help='JSON array of {"variant_number": int, "agent_count": int}.',
    )
    parser.add_argument("--round", type=int, default=5)
    return parser.parse_args()


def parse_batch_items(raw_value: str) -> list[dict[str, int]]:
    value = json.loads(raw_value)
    if not isinstance(value, list) or not value:
        raise ValueError("--batch-items must be a non-empty JSON array")
    items: list[dict[str, int]] = []
    seen_numbers: set[int] = set()
    for index, raw_item in enumerate(value):
        if not isinstance(raw_item, dict):
            raise ValueError(f"batch item {index} must be an object")
        variant_number = raw_item.get("variant_number")
        agent_count = raw_item.get("agent_count")
        if not isinstance(variant_number, int) or variant_number < 1:
            raise ValueError(f"batch item {index} has an invalid variant_number")
        if not isinstance(agent_count, int) or agent_count < 1:
            raise ValueError(f"batch item {index} has an invalid agent_count")
        if variant_number in seen_numbers:
            raise ValueError(f"duplicate variant_number in batch: {variant_number}")
        seen_numbers.add(variant_number)
        items.append(
            {"variant_number": variant_number, "agent_count": agent_count}
        )
    return items


def scenarios_from_batch_data(data: Any, expected_count: int) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), list):
        raise ValueError("batch LLM output must be an object with a scenarios array")
    scenarios = data["scenarios"]
    if len(scenarios) != expected_count:
        raise ValueError(
            f"expected {expected_count} scenarios in batch, got {len(scenarios)}"
        )
    if not all(isinstance(scenario, dict) for scenario in scenarios):
        raise ValueError("every batch scenario must be a JSON object")
    return scenarios


def main() -> None:
    args = parse_args()
    items = parse_batch_items(args.batch_items)
    scene_dir = Path(args.scene_dir).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    llm_config = load_llm_config(Path(args.llm_config).expanduser().resolve())
    mapping = load_mapping(scene_dir / "topdown_mapping.json")
    waypoint_context = load_waypoint_context(scene_dir)
    waypoint_lookup = build_waypoint_lookup(waypoint_context)
    scene_prompt_path = scene_dir / "scene_prompt.txt"
    scene_prompt = (
        scene_prompt_path.read_text(encoding="utf-8").strip()
        if scene_prompt_path.is_file()
        else ""
    )
    reference_episodes: list[dict[str, Any]] = []
    if args.include_reference_episodes:
        reference_dirs = [
            Path(path).expanduser().resolve() for path in (args.reference_dir or [])
        ]
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
        scene_prompt,
        args.prompt,
        reference_episodes=reference_episodes,
        robot_intermediate_waypoints=args.robot_intermediate_waypoints,
        batch_agent_counts=[item["agent_count"] for item in items],
        include_reference_episodes=args.include_reference_episodes,
    )
    image_paths = [scene_dir / "overlay.png", scene_dir / "navmesh_mask.png"]
    for image_path in image_paths:
        if not image_path.is_file():
            raise FileNotFoundError(f"missing required image: {image_path}")

    llm_result = call_llm_with_usage(llm_config, prompt, image_paths)
    print(
        TOKEN_USAGE_PREFIX
        + json.dumps(llm_result.usage.to_dict(), separators=(",", ":")),
        flush=True,
    )
    batch_scenarios = scenarios_from_batch_data(llm_result.data, len(items))
    conversion_args = SimpleNamespace(
        default_model=DEFAULT_MODEL,
        default_behavior_tree=DEFAULT_BEHAVIOR_TREE,
        default_desired_velocity=DEFAULT_DESIRED_VELOCITY,
        round=args.round,
        robot_intermediate_waypoints=args.robot_intermediate_waypoints,
        structured_parameters=json.loads(args.structured_parameters or "{}"),
    )

    prepared: list[tuple[dict[str, Any], dict[str, Any], Path]] = []
    for item, llm_scenario in zip(items, batch_scenarios):
        validate_llm_social_fields(llm_scenario)
        agents = agents_from_data(llm_scenario)
        expected_agents = item["agent_count"]
        if len(agents) != expected_agents:
            raise ValueError(
                f"scenario_{item['variant_number']:03d}: expected "
                f"{expected_agents} agents, got {len(agents)}"
            )
        scenario = build_scenario(
            agents,
            mapping,
            conversion_args,
            waypoint_lookup,
        )
        robot = robot_from_data(llm_scenario)
        if robot is None:
            raise ValueError(
                f"scenario_{item['variant_number']:03d}: missing robot object"
            )
        scenario["robot"] = build_robot_entry(
            robot,
            mapping,
            conversion_args,
            waypoint_lookup,
            waypoint_context,
            args.robot_intermediate_waypoints,
            conversion_args.structured_parameters,
        )
        variant_dir = output_root / f"scenario_{item['variant_number']:03d}"
        prepared.append((llm_scenario, scenario, variant_dir))

    # Write only after every item in the batch has passed conversion.
    for llm_scenario, scenario, variant_dir in prepared:
        variant_dir.mkdir(parents=True, exist_ok=True)
        (variant_dir / "llm_agents.json").write_text(
            json.dumps(llm_scenario, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (variant_dir / "scenario.yaml").write_text(
            "# Generated from topdown pixel waypoints\n\n"
            + yaml.dump(
                scenario,
                Dumper=ScenarioYamlDumper,
                sort_keys=False,
                allow_unicode=False,
            ),
            encoding="utf-8",
        )
    numbers = ", ".join(
        f"scenario_{item['variant_number']:03d}" for item in items
    )
    print(f"Wrote batch: {numbers}", flush=True)


if __name__ == "__main__":
    main()
