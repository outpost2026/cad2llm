#!/usr/bin/env python3
"""Single source of truth for the CAD2LLM role/zone/IFC vocabulary.

Rules live in tools/cad2llm_rules.json under the `vocab` key so that the
validator, the plan generator (derive) and the v5 semantics all agree.
"""

from __future__ import annotations

import copy
import hashlib
import json
from functools import lru_cache
from pathlib import Path

RULES_PATH = Path(__file__).with_name("cad2llm_rules.json")
OVERLAY_SCHEMA = "cad2llm/vocab_overlay/v1"
_OVERLAY_KEYS = {
    "schema",
    "project_id",
    "name",
    "description",
    "version",
    "extends_version",
    "roles",
    "tag_roles",
    "zones",
    "deprecated_roles",
}
_HASH_FIELDS = (
    "project_id",
    "version",
    "base_version",
    "zones",
    "roles",
    "tag_roles",
    "deprecated_roles",
)


@lru_cache(maxsize=8)
def load_rules(path: str | None = None) -> dict:
    target = Path(path) if path else RULES_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def load_vocab(
    rules: dict | None = None,
    path: str | None = None,
    overlay: dict | str | Path | None = None,
) -> dict:
    """Load the shared vocabulary, optionally merged with a project overlay.

    Without an overlay the returned object is the raw `vocab` block from
    rules.json (unchanged). With an overlay a new merged dict is returned;
    the cached core vocab is never mutated.
    """
    data = rules if rules is not None else load_rules(path)
    base = (data or {}).get("vocab")
    if not base:
        base = load_rules(path).get("vocab") or {}
    if overlay is None:
        return base
    return merge_vocab(base, overlay)


def load_overlay(source: dict | str | Path) -> dict:
    """Read and minimally validate a project vocabulary overlay."""
    if isinstance(source, (str, Path)):
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    elif isinstance(source, dict):
        data = copy.deepcopy(source)
    else:
        raise TypeError("overlay must be a dict or a path")

    if not isinstance(data, dict):
        raise ValueError("overlay must be a JSON object")
    unknown = set(data) - _OVERLAY_KEYS
    if unknown:
        raise ValueError(f"overlay has unknown keys: {sorted(unknown)}")
    if data.get("schema") != OVERLAY_SCHEMA:
        raise ValueError(f"overlay schema must be {OVERLAY_SCHEMA!r}")
    project_id = data.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("overlay project_id must be a non-empty string")
    for field in ("roles", "tag_roles"):
        if field in data and not isinstance(data[field], dict):
            raise ValueError(f"overlay {field} must be an object")
    if "zones" in data and not isinstance(data["zones"], list):
        raise ValueError("overlay zones must be a list")
    if "deprecated_roles" in data and not isinstance(data["deprecated_roles"], list):
        raise ValueError("overlay deprecated_roles must be a list")
    return data


def merge_vocab(base: dict, overlay: dict | str | Path) -> dict:
    """Merge add-only project vocabulary into a copy of the core vocabulary.

    Roles and tag aliases may only be added or repeated identically; silent
    redefinition of a core role is refused. Existing roles can be marked
    deprecated without breaking historical exports.
    """
    merged = copy.deepcopy(base or {})
    ov = load_overlay(overlay)

    base_version = merged.get("version")
    extends = ov.get("extends_version")
    if extends and base_version and extends != base_version:
        raise ValueError(
            f"overlay extends {extends!r} but core vocab is {base_version!r}"
        )

    merged_roles = merged.setdefault("roles", {})
    for role, info in (ov.get("roles") or {}).items():
        if not isinstance(role, str) or not role.strip():
            raise ValueError("overlay role names must be non-empty strings")
        if not isinstance(info, dict):
            raise ValueError(f"role '{role}' must be an object")
        ifc = info.get("ifc")
        if not isinstance(ifc, str) or not ifc.startswith("Ifc"):
            raise ValueError(f"role '{role}' needs an Ifc* type")
        if role in merged_roles and merged_roles[role] != info:
            raise ValueError(
                f"role '{role}' already exists with a different definition"
            )
        merged_roles[role] = copy.deepcopy(info)

    merged_zones = list(merged.get("zones") or [])
    for zone in ov.get("zones") or []:
        if not isinstance(zone, str) or not zone.strip():
            raise ValueError("overlay zones must be non-empty strings")
        if zone not in merged_zones:
            merged_zones.append(zone)
    merged["zones"] = merged_zones

    known_zones = set(merged_zones)
    for role, info in merged_roles.items():
        zone = (info or {}).get("zone")
        if zone and zone not in known_zones:
            raise ValueError(f"role '{role}' uses unknown zone '{zone}'")

    merged_tags = dict(merged.get("tag_roles") or {})
    by_lower = {str(k).lower(): (k, v) for k, v in merged_tags.items()}
    for tag, role in (ov.get("tag_roles") or {}).items():
        name = str(tag).strip()
        if not name:
            raise ValueError("overlay tag aliases must be non-empty")
        if role not in merged_roles:
            raise ValueError(f"tag '{name}' maps to unknown role '{role}'")
        prior = by_lower.get(name.lower())
        if prior and prior[1] != role:
            raise ValueError(
                f"tag '{name}' already maps to '{prior[1]}' (overlay wants '{role}')"
            )
        merged_tags[name] = role
        by_lower[name.lower()] = (name, role)
    merged["tag_roles"] = merged_tags

    deprecated = list(merged.get("deprecated_roles") or [])
    for role in ov.get("deprecated_roles") or []:
        if role not in merged_roles:
            raise ValueError(f"deprecated role '{role}' is not in the vocabulary")
        if role not in deprecated:
            deprecated.append(role)
    if deprecated:
        merged["deprecated_roles"] = deprecated

    merged["base_version"] = base_version
    merged["version"] = ov.get("version") or base_version
    merged["project_id"] = ov["project_id"].strip()
    if ov.get("name"):
        merged["overlay_name"] = ov["name"]
    return merged


def vocab_hash(vocab_data: dict | None = None) -> str:
    """Stable short hash of the effective vocabulary (16 hex chars)."""
    data = vocab_data if vocab_data is not None else load_vocab()
    payload = {key: data.get(key) for key in _HASH_FIELDS}
    blob = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def roles(vocab: dict) -> dict:
    return vocab.get("roles") or {}


def zones(vocab: dict) -> list:
    return list(vocab.get("zones") or [])


def role_info(role: str, vocab: dict) -> dict | None:
    return roles(vocab).get(role)


def default_zone(role: str, vocab: dict) -> str | None:
    info = role_info(role, vocab)
    return (info or {}).get("zone")


def tag_role(tag, vocab: dict) -> str | None:
    mapping = vocab.get("tag_roles") or {}
    raw = (tag or "").strip()
    if not raw:
        return None
    return mapping.get(raw) or mapping.get(raw.lower())


def metadata_of(node: dict) -> tuple:
    cad = ((node.get("metadata") or {}).get("cad2llm")) or {}
    ifc = ((node.get("metadata") or {}).get("ifc")) or {}
    return cad.get("role"), cad.get("zone"), ifc.get("type")


def check_node(node: dict, vocab: dict) -> list:
    """Return vocabulary issues for one exported node (no side effects)."""
    known_roles = roles(vocab)
    known_zones = set(zones(vocab))
    if not known_roles:
        return []

    issues = []
    role, zone, ifc_type = metadata_of(node)
    name = node.get("name")

    if role and role not in known_roles:
        issues.append(
            {
                "code": "UNKNOWN_ROLE",
                "severity": "warn",
                "message": f"{name}: role '{role}' not in vocab",
            }
        )
    if role and role in set(vocab.get("deprecated_roles") or []):
        issues.append(
            {
                "code": "DEPRECATED_ROLE",
                "severity": "warn",
                "message": f"{name}: role '{role}' is deprecated in this vocab",
            }
        )
    if zone and zone != "unknown" and zone not in known_zones:
        issues.append(
            {
                "code": "UNKNOWN_ZONE",
                "severity": "warn",
                "message": f"{name}: zone '{zone}' not in vocab",
            }
        )

    info = known_roles.get(role) or {}
    expected_ifc = info.get("ifc")
    if expected_ifc and ifc_type and ifc_type != expected_ifc:
        issues.append(
            {
                "code": "IFC_ROLE_MISMATCH",
                "severity": "warn",
                "message": f"{name}: role '{role}' expects {expected_ifc}, got {ifc_type}",
            }
        )

    mapped = tag_role(node.get("tag"), vocab)
    if mapped and role and mapped != role:
        issues.append(
            {
                "code": "TAG_ROLE_MISMATCH",
                "severity": "info",
                "message": f"{name}: tag '{node.get('tag')}' implies '{mapped}', role is '{role}'",
            }
        )

    return issues
