from __future__ import annotations

import re
from pathlib import Path


SCENE_ID_MAPPING = (
    ("grscenes_1", "MV4AFHQKTKJZ2AABAAAAADQ8_usd"),
    ("grscenes_2", "MV4AFHQKTKJZ2AABAAAAADY8_usd"),
    ("grscenes_3", "MV4AFHQKTKJZ2AABAAAAAEA8_usd"),
    ("grscenes_4", "MV4AFHQKTKJZ2AABAAAAAEI8_usd"),
    ("grscenes_5", "MV5M25QKTKJZ2AABAAAAAAA8_usd"),
    ("grscenes_6", "MV5M25QKTKJZ2AABAAAAAAI8_usd"),
    ("grscenes_7", "MV5M25QKTKJZ2AABAAAAAAQ8_usd"),
    ("grscenes_8", "MV5M25QKTKJZ2AABAAAAAAY8_usd"),
    ("grscenes_9", "MV5M25QKTKJZ2AABAAAAAEI8_usd"),
    ("grscenes_10", "MV7J6NIKTKJZ2AABAAAAAAA8_usd"),
    ("grscenes_11", "MV7J6NIKTKJZ2AABAAAAAAI8_usd"),
    ("grscenes_12", "MVJWVGYKTLDAYAABAAAAAAQ8_usd"),
    ("grscenes_13", "MVSGSAIKTKJ66AABAAAAADY8_usd"),
    ("grscenes_14", "MVSGSAIKTKJ66AABAAAAAEA8_usd"),
    ("grscenes_15", "MVSYCXYKTKJ66AABAAAAAAA8_usd"),
    ("grscenes_16", "MVSYCXYKTKJ66AABAAAAACY8_usd"),
    ("grscenes_17", "MVSYCXYKTKJ66AABAAAAADA8_usd"),
    ("grscenes_18", "MVSYCXYKTKJ66AABAAAAADI8_usd"),
    ("grscenes_19", "MWF4WLIKTIFZIAABAAAAABY8_usd"),
    ("grscenes_20", "MWF4WLIKTIFZIAABAAAAACA8_usd"),
    ("grscenes_21", "MWF4WLIKTIFZIAABAAAAACI8_usd"),
    ("grscenes_22", "MWF4WLIKTIFZIAABAAAAACQ8_usd"),
    ("grscenes_23", "MWF4WLIKTIFZIAABAAAAACY8_usd"),
    ("grscenes_24", "MWF4WLIKTIFZIAABAAAAADA8_usd"),
    ("grscenes_25", "MWF4WLIKTIFZIAABAAAAADI8_usd"),
    ("grscenes_26", "MWF4WLIKTIFZIAABAAAAADQ8_usd"),
    ("grscenes_27", "MWF4WLIKTIFZIAABAAAAADY8_usd"),
    ("grscenes_28", "MWF4WLIKTIFZIAABAAAAAEA8_usd"),
    ("grscenes_29", "MWF4WLIKTIFZIAABAAAAAEI8_usd"),
    ("grscenes_30", "MWHLEPQKTIFZIAABAAAAAAA8_usd"),
)

LEGACY_TO_CANONICAL = {legacy: canonical for canonical, legacy in SCENE_ID_MAPPING}
CANONICAL_TO_LEGACY = {canonical: legacy for canonical, legacy in SCENE_ID_MAPPING}
CANONICAL_PATTERN = re.compile(r"^grscenes_([1-9]|[12][0-9]|30)$")


def canonical_scene_id(scene_id: str) -> str:
    """Normalize known GRScenes IDs while preserving custom scene names."""
    name = Path(scene_id).name
    if CANONICAL_PATTERN.fullmatch(name):
        return name
    if name in LEGACY_TO_CANONICAL:
        return LEGACY_TO_CANONICAL[name]

    extracted_match = re.match(r"^(grscenes_(?:[1-9]|[12][0-9]|30))_", name)
    if extracted_match:
        return extracted_match.group(1)
    return name


def resolve_scene_dir(path: Path) -> Path:
    """Resolve a scene path and transparently replace a legacy final component."""
    resolved = path.expanduser().resolve()
    if resolved.is_dir():
        return resolved
    canonical = canonical_scene_id(resolved.name)
    if canonical == resolved.name:
        return resolved
    return resolved.with_name(canonical)


def scene_sort_key(scene_id: str) -> int:
    canonical = canonical_scene_id(scene_id)
    match = CANONICAL_PATTERN.fullmatch(canonical)
    return int(match.group(1)) if match else 10_000
