#!/usr/bin/env python3
"""Single source of truth for the CAD2LLM role/zone/IFC vocabulary.

Rules live in tools/cad2llm_rules.json under the `vocab` key so that the
validator, the plan generator (derive) and the v5 semantics all agree.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

RULES_PATH = Path(__file__).with_name("cad2llm_rules.json")


@lru_cache(maxsize=8)
def load_rules(path: str | None = None) -> dict:
    target = Path(path) if path else RULES_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def load_vocab(rules: dict | None = None, path: str | None = None) -> dict:
    data = rules if rules is not None else load_rules(path)
    vocab = (data or {}).get("vocab")
    if not vocab:
        vocab = load_rules(path).get("vocab") or {}
    return vocab


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
    return (vocab.get("tag_roles") or {}).get((tag or "").strip())


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
