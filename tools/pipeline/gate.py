#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CAD2LLM gate - deterministicka brana pro exporty z CAD2LLM.

Neni to interaktivni nastroj. Vola se jednim prikazem z pipeline, bez promptu:

    python -X utf8 gate.py --dir <adresar_s_exporty> --out reports

Vystup je strojove citelny, pripraveny pro bridge do SketchUpu:
    <model>.gate.json     - uplny vysledek kontrol
    <model>.patch.json    - co zapsat do modelu (kontrakt cad2llm/semantic-patch/v1)
    <model>.review.json   - co musi rozhodnout clovek (rozhodnuti = soubor, ne chat)
    SUMMARY.md            - souhrn pres modely

Lidska kontrola:
    1. pipeline zapise review.json
    2. clovek doplni "decision": "approve" | "reject" | "value"
    3. pipeline se spusti znovu s --review -> schvalene polozky prejdou do patch.json
    4. bridge zapise patch do .skp

Vystupni kody: 0 = cisto, 1 = varovani, 2 = blokujici nalezy.
"""

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REGISTRY = os.path.join(HERE, "registry.json")

# The vocabulary lives next to the other tools; the gate must not invent its
# own role/system/zone names, otherwise the model gets values no consumer knows.
TOOLS_DIR = os.path.dirname(HERE)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)
import cad2llm_vocab as vocab  # noqa: E402

PATCH_CONTRACT = "cad2llm/semantic-patch/v1"
GENERIC_NAMES = {"", "unnamed", "component", "difference", "group", "solid"}
IFC_PATTERN = re.compile(r"^Ifc[A-Z][A-Za-z0-9]*$")

SEV_BLOCK = "blocking"
SEV_WARN = "warning"
SEV_INFO = "info"

EPS_MM = 0.6
EPS_ROT = 1e-3
EPS_SCALE = 1e-3
ZERO_MM = 0.01


# ---------------------------------------------------------------- pomocne


def norm(text):
    if text is None:
        return ""
    s = re.sub(r"#\d+$", "", str(text))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = "".join(ch if ch.isalnum() else "_" for ch in s)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


def fnum(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def fmt3(v):
    return "[%s]" % ", ".join("%.1f" % fnum(x) for x in v)


def vec(d):
    d = d or {}
    return [fnum(d.get("x")), fnum(d.get("y")), fnum(d.get("z"))]


def mat3_from_4x4(m):
    return [[fnum(m[r][c]) for c in range(3)] for r in range(3)]


def mat_mul(a, b):
    return [
        [sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)
    ]


def rot_x(deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return [[1, 0, 0], [0, c, -s], [0, s, c]]


def rot_y(deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return [[c, 0, s], [0, 1, 0], [-s, 0, c]]


def rot_z(deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def rot_from_deg(rx, ry, rz):
    """Konvence: Rz * Ry * Rx."""
    return mat_mul(rot_z(rz), mat_mul(rot_y(ry), rot_x(rx)))


def quat_to_mat(w, x, y, z):
    n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    w, x, y, z = w / n, x / n, y / n, z / n
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ]


def mat_diff(a, b):
    return max(abs(a[i][j] - b[i][j]) for i in range(3) for j in range(3))


def aabb_extents(rot, size):
    return [sum(abs(rot[i][j]) * size[j] for j in range(3)) for i in range(3)]


def is_identity(rot):
    return mat_diff(rot, [[1, 0, 0], [0, 1, 0], [0, 0, 1]]) < EPS_ROT


def mesh_extents(node):
    verts = (node.get("geometry") or {}).get("vertices")
    if not verts:
        return None
    return [
        max(fnum(v[i]) for v in verts) - min(fnum(v[i]) for v in verts)
        for i in range(3)
    ]


def decompose(m3):
    """Rozklad linear part na meritko a rotaci (sloupcove normy)."""
    scale = [math.sqrt(sum(m3[i][j] ** 2 for i in range(3))) for j in range(3)]
    safe = [s if s > 1e-9 else 1.0 for s in scale]
    rot = [[m3[i][j] / safe[j] for j in range(3)] for i in range(3)]
    return scale, rot


# ---------------------------------------------------------------- registry


class Registry:
    def __init__(self, path):
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        self.version = raw.get("registry_version")
        self.definitions = {
            norm(k): v for k, v in (raw.get("definitions") or {}).items()
        }
        self.tag_map = {norm(k): v for k, v in (raw.get("tag_map") or {}).items()}
        self.meta_types = {
            norm(k): v for k, v in (raw.get("metadata_type_map") or {}).items()
        }
        self.aliases = {
            norm(k): norm(v) for k, v in (raw.get("attribute_aliases") or {}).items()
        }
        self.junk = raw.get("junk_definitions") or []
        self.junk_pids, self.junk_names = set(), set()
        for item in self.junk:
            for pid in item.get("persistent_ids") or []:
                self.junk_pids.add(pid)
            self.junk_names.add(norm(item.get("definition_name")))
        self.variant_tokens = {norm(t) for t in raw.get("variant_tokens") or []}
        self.reference_tokens = [norm(t) for t in raw.get("reference_tokens") or []]
        self.reference_roles = {norm(t) for t in raw.get("reference_roles") or []}
        self.ambiguous_names = {norm(t) for t in raw.get("ambiguous_names") or []}
        self.gps_default = raw.get("gps_template_default")
        self.gps_tol = fnum(raw.get("gps_template_tolerance"), 0.01)
        self.severity_overrides = {
            k: v for k, v in (raw.get("severity_overrides") or {}).items()
        }

        # Vocabulary conformance. The registry may only hand out names that are
        # declared in tools/cad2llm_rules.json, otherwise the gate writes values
        # no consumer understands (column/purlin/... used to be exactly that).
        self.vocab = vocab.load_vocab()
        vocab_roles = vocab.roles(self.vocab)
        self.vocab_roles = set(vocab_roles)
        self.vocab_systems = set(vocab.systems(self.vocab))
        self.vocab_zones = set(vocab.zones(self.vocab))
        self.vocab_ifc = {
            info.get("ifc") for info in vocab_roles.values() if info.get("ifc")
        }

    def conformance(self, role, system, ifc, zone):
        """Return issues for names that are outside the shared vocabulary."""
        issues = []
        if role and role not in self.vocab_roles:
            issues.append(
                (
                    "SEM_VOCAB_ROLE_DRIFT",
                    SEV_BLOCK,
                    "role '%s' neni ve slovniku (tools/cad2llm_rules.json)" % role,
                )
            )
        if system and self.vocab_systems and system not in self.vocab_systems:
            issues.append(
                (
                    "SEM_VOCAB_SYSTEM_DRIFT",
                    SEV_BLOCK,
                    "system '%s' neni ve slovniku" % system,
                )
            )
        if zone and zone not in self.vocab_zones:
            issues.append(
                (
                    "SEM_VOCAB_ZONE_DRIFT",
                    SEV_BLOCK,
                    "zona '%s' neni ve slovniku" % zone,
                )
            )
        if ifc and not IFC_PATTERN.match(str(ifc)):
            issues.append(
                (
                    "SEM_IFC_MALFORMED",
                    SEV_BLOCK,
                    "IFC trida '%s' nema tvar IfcXxx" % ifc,
                )
            )
        elif ifc and ifc not in self.vocab_ifc:
            issues.append(
                (
                    "SEM_IFC_UNDECLARED",
                    SEV_INFO,
                    "IFC trida '%s' neni deklarovana ve slovniku" % ifc,
                )
            )
        return issues

    def zone_for(self, role, zone):
        """Explicit zone wins; otherwise the vocabulary default for the role."""
        if zone:
            return zone, None
        fallback = vocab.default_zone(role, self.vocab) if role else None
        return fallback, "vocab.zone" if fallback else None

    def lookup_definition(self, name):
        key = norm(name)
        if not key:
            return None, None
        if key in self.definitions:
            return key, self.definitions[key]
        best = None
        for cand in self.definitions:
            if cand and key.startswith(cand):
                rest = key[len(cand) :]
                if re.match(r"^_?\d+[a-z]{0,4}$", rest) and (
                    best is None or len(cand) > len(best)
                ):
                    best = cand
        return (best, self.definitions[best]) if best else (None, None)

    def lookup_tag(self, tag):
        return self.tag_map.get(norm(tag))

    def lookup_metadata_type(self, value):
        return self.meta_types.get(norm(value))

    def token_variant(self, tag):
        for tok in norm(tag).split("_"):
            if tok in self.variant_tokens:
                return tok
        return None

    def is_junk(self, node):
        return (
            norm(node.get("definition_name")) in self.junk_names
            or node.get("persistent_id") in self.junk_pids
        )

    def is_reference_role(self, role):
        return norm(role) in self.reference_roles

    def looks_like_reference_name(self, text):
        key = norm(text)
        return any(tok and tok in key for tok in self.reference_tokens)


def compatible_roles(a, b):
    if not a or not b:
        return True
    if norm(a) == norm(b):
        return True
    return set(norm(a).split("_")) <= set(norm(b).split("_")) or set(
        norm(b).split("_")
    ) <= set(norm(a).split("_"))


# ---------------------------------------------------------------- kontroly


def entity_id(node):
    return node.get("uid") or "pid:%s" % node.get("persistent_id")


def canonical_attributes(reg, meta):
    found, issues = {}, []
    if not isinstance(meta, dict):
        return found, issues
    for key, value in meta.items():
        if "cad2llm" in norm(key):
            continue
        nkey = norm(key)
        ckey = reg.aliases.get(nkey, nkey)
        if ckey != nkey:
            issues.append(
                (
                    "DATA_ATTR_KEY_ALIAS",
                    SEV_INFO,
                    "atribut '%s' je preklep/alias, kanonicky '%s'" % (key, ckey),
                )
            )
        if isinstance(value, dict):
            for k2, v2 in value.items():
                found[ckey + "." + norm(k2)] = v2
        else:
            found[ckey] = value
    return found, issues


def analyze_frame(reg, node, has_children=False):
    issues = []
    sp = node.get("spatial") or {}
    if not sp:
        return [("FRAME_MISSING", SEV_BLOCK, "entita nema spatial blok")]

    size, center = vec(sp.get("size_mm")), vec(sp.get("center_mm"))
    bb = sp.get("bbox_mm") or {}
    bmin, bmax = vec(bb.get("min")), vec(bb.get("max"))
    bbox_ext = [bmax[i] - bmin[i] for i in range(3)]
    bbox_ctr = [(bmin[i] + bmax[i]) / 2.0 for i in range(3)]

    if any(abs(bbox_ctr[i] - center[i]) > EPS_MM for i in range(3)):
        issues.append(
            ("FRAME_BBOX_OFF_CENTER", SEV_BLOCK, "stred obalu nesedi se stredem entity")
        )
    if any(abs(bbox_ext[i] - size[i]) > EPS_MM for i in range(3)):
        issues.append(
            (
                "FRAME_BBOX_VS_SIZE",
                SEV_BLOCK,
                "rozmery obalu %s se rozchazeji s rozmery entity %s"
                % (fmt3(bbox_ext), fmt3(size)),
            )
        )

    rd = sp.get("rotation_deg") or {}
    rx, ry, rz = fnum(rd.get("x")), fnum(rd.get("y")), fnum(rd.get("z"))
    rot_stupne = rot_from_deg(rx, ry, rz)

    rot_mat = rot_stupne
    if sp.get("matrix_4x4"):
        scale, rot_mat = decompose(mat3_from_4x4(sp["matrix_4x4"]))
        if any(abs(s - 1.0) > EPS_SCALE for s in scale):
            issues.append(
                (
                    "FRAME_UNDECLARED_SCALE",
                    SEV_WARN,
                    "velikost je zmenena zamerem designera (meritko %s) a neni to "
                    "v exportu deklarovano; rozmery jsou jiz po zmene, matici neaplikovat"
                    % fmt3(scale),
                )
            )
        if mat_diff(rot_mat, rot_stupne) > EPS_ROT:
            issues.append(
                (
                    "FRAME_DEG_VS_MATRIX",
                    SEV_BLOCK,
                    "rotace ve stupnich neodpovida matici (odchylka %.4f)"
                    % mat_diff(rot_mat, rot_stupne),
                )
            )

    if sp.get("quaternion"):
        q = sp["quaternion"]
        rot_q = quat_to_mat(
            fnum(q.get("w"), 1.0), fnum(q.get("x")), fnum(q.get("y")), fnum(q.get("z"))
        )
        if mat_diff(rot_q, rot_stupne) > 1e-2:
            issues.append(
                (
                    "FRAME_QUATERNION_VS_DEG",
                    SEV_WARN,
                    "quaternion neodpovida rotaci ve stupnich",
                )
            )

    if not is_identity(rot_mat):
        predicted = aabb_extents(rot_mat, size)
        # geometry is direct-only while a container's bbox is the union of its
        # children - the two scopes can never match, so no mesh verdict here.
        mesh = None if has_children else mesh_extents(node)
        if mesh is not None:
            if all(abs(mesh[i] - size[i]) <= EPS_MM for i in range(3)):
                issues.append(
                    (
                        "FRAME_ROTATION_VS_GEOMETRY",
                        SEV_INFO,
                        "matice = local->world transformace, bbox/geometry = world; "
                        "matici na geometrii a obal neaplikovat (dvojita "
                        "transformace) - tohle je kontrakt, ne chyba",
                    )
                )
            elif all(abs(mesh[i] - predicted[i]) <= EPS_MM for i in range(3)):
                issues.append(
                    (
                        "FRAME_BBOX_NOT_AABB",
                        SEV_BLOCK,
                        "ulozeny obal neni obal ve svetovych souradnicich: ulozeno=%s, "
                        "spravne=%s" % (fmt3(bbox_ext), fmt3(predicted)),
                    )
                )
            else:
                issues.append(
                    (
                        "FRAME_MESH_VS_BBOX",
                        SEV_BLOCK,
                        "souradnice nesedi ani s obalem, ani s rotaci",
                    )
                )
        else:
            if not all(abs(predicted[i] - size[i]) <= EPS_MM for i in range(3)):
                if all(abs(bbox_ext[i] - size[i]) <= EPS_MM for i in range(3)):
                    why = (
                        "obal je sjednocenim deti a geometrie pokryva jen primo "
                        "polozky, srovnani nelze provest"
                        if has_children
                        else "bez geometrie nelze overit"
                    )
                    issues.append(
                        (
                            "FRAME_UNDECIDABLE",
                            SEV_WARN,
                            "rotace %.1f/%.1f/%.1f st. a obal odpovida neotocenemu "
                            "kvadru; %s (spravny obal by byl %s)"
                            % (rx, ry, rz, why, fmt3(predicted)),
                        )
                    )

    if min(size) <= ZERO_MM:
        issues.append(
            (
                "GEOM_ZERO_THICKNESS",
                SEV_WARN,
                "nulovy rozmer %s -> rovina, ne teleso" % fmt3(size),
            )
        )
    return issues


def classify_semantics(reg, node):
    issues = []
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    cad = meta.get("cad2llm") if isinstance(meta.get("cad2llm"), dict) else {}
    ifc_block = meta.get("ifc") if isinstance(meta.get("ifc"), dict) else {}
    attrs, attr_issues = canonical_attributes(reg, meta)
    issues.extend(attr_issues)

    def_name, tag, name = node.get("definition_name"), node.get("tag"), node.get("name")
    role, system = cad.get("role"), cad.get("system")
    ifc, zone = ifc_block.get("type"), cad.get("zone")
    kind = None
    evidence = ["metadata.cad2llm.role"] if role else []

    if not role:
        _, rec = reg.lookup_definition(def_name)
        if rec:
            role, system = rec.get("role"), system or rec.get("system")
            ifc = ifc or rec.get("ifc")
            kind = rec.get("kind")
            evidence.append("definition_name=%s" % def_name)

    if not role:
        rec = reg.lookup_tag(tag)
        if rec:
            role, system = rec.get("role"), system or rec.get("system")
            ifc = ifc or rec.get("ifc")
            evidence.append("tag=%s" % tag)

    if not role:
        mtype = None
        for key, value in attrs.items():
            if key.endswith(".type") or key.endswith(".konstrukce"):
                mtype = value
                break
        rec = reg.lookup_metadata_type(mtype) if mtype else None
        if rec:
            role, system = rec.get("role"), system or rec.get("system")
            ifc = ifc or rec.get("ifc")
            evidence.append("metadata=%s" % mtype)

    if reg.is_junk(node):
        issues.append(
            (
                "DATA_JUNK_COMPONENT",
                SEV_WARN,
                "knihovni komponenta '%s' (pid %s)"
                % (def_name, node.get("persistent_id")),
            )
        )
        if not role:
            role, system, kind = "junk_component", "non_construction", "junk"
            evidence.append("junk_registry")

    zone, zone_evidence = reg.zone_for(role, zone)
    if zone_evidence:
        evidence.append(zone_evidence)

    if norm(name) in reg.ambiguous_names:
        issues.append(
            ("SEM_NAME_UNNAMED", SEV_WARN, "instance nema jmeno ('%s')" % name)
        )
    if def_name and "#" in str(def_name):
        issues.append(
            (
                "SEM_DEF_NAME_AUTOSUFFIX",
                SEV_INFO,
                "definice '%s' ma SketchUp auto-sufix" % def_name,
            )
        )
    if reg.token_variant(tag):
        issues.append(
            (
                "SEM_TAG_VARIANT_COLLAPSED",
                SEV_WARN,
                "tag '%s' micha variantu '%s' s tridou prvku"
                % (tag, reg.token_variant(tag)),
            )
        )

    cands = {}
    _, rec = reg.lookup_definition(def_name)
    if rec:
        cands["definition_name"] = (rec.get("role"), rec.get("system"))
    rec = reg.lookup_tag(tag)
    if rec:
        cands["tag"] = (rec.get("role"), rec.get("system"))
    mtype = None
    for key, value in attrs.items():
        if key.endswith(".type") and norm(value) not in reg.variant_tokens:
            mtype = value
    if mtype:
        rec = reg.lookup_metadata_type(mtype)
        if rec:
            cands["metadata_type"] = (rec.get("role"), rec.get("system"))

    roles = {k: v[0] for k, v in cands.items() if v and v[0]}
    conflict = False
    if len({norm(v) for v in roles.values()}) > 1:
        vals = list(roles.values())
        if any(not compatible_roles(a, b) for a in vals for b in vals):
            systems = {v[1] for v in cands.values() if v and v[0]}
            conflict = True
            issues.append(
                (
                    "SEM_THREEWAY_CONFLICT",
                    SEV_BLOCK if len(systems) > 1 else SEV_WARN,
                    "role si odporuje (system %s): %s"
                    % ("/".join(sorted(str(x) for x in systems)), roles),
                )
            )
        else:
            issues.append(
                ("SEM_ROLE_REFINEMENT", SEV_INFO, "role se zpresnuje: %s" % roles)
            )

    if not role:
        issues.append(("SEM_ROLE_MISSING", SEV_WARN, "roli nelze odvodit"))
    if not zone:
        issues.append(("SEM_ZONE_MISSING", SEV_INFO, "entita nema zonu"))
    if not ifc and role != "junk_component":
        issues.append(("SEM_IFC_MISSING", SEV_INFO, "entita nema IFC tridu"))

    size = vec((node.get("spatial") or {}).get("size_mm"))
    if min(size) <= ZERO_MM:
        if role and (system == "reference" or reg.is_reference_role(role)):
            kind = "reference"
            issues.append(
                (
                    "SEM_REFERENCE_PLANE",
                    SEV_INFO,
                    "pomocna/merici plocha '%s'" % (def_name or role),
                )
            )
        elif role:
            issues.append(
                (
                    "SEM_ZERO_THICKNESS_ELEMENT",
                    SEV_BLOCK,
                    "prvek '%s' je modelovan jako rovina" % role,
                )
            )
            kind = "incomplete_geometry"
        elif reg.looks_like_reference_name(def_name) or reg.looks_like_reference_name(
            tag
        ):
            kind = "reference"
        else:
            issues.append(
                (
                    "SEM_ZERO_THICKNESS_UNKNOWN",
                    SEV_WARN,
                    "nulova tloustka bez jasneho vyznamu",
                )
            )
    if kind is None:
        kind = "element" if role else None
    if kind == "reference" and system != "reference":
        system = "reference"

    variant = reg.token_variant(tag)
    issues.extend(reg.conformance(role, system, ifc, zone))
    return {
        "uid": node.get("uid"),
        "persistent_id": node.get("persistent_id"),
        "definition_name": def_name,
        "tag": tag,
        "role": role,
        "system": system,
        "ifc": ifc,
        "zone": zone,
        "kind": kind,
        "variant": variant,
        "source": "existing"
        if role and role == cad.get("role")
        else ("registry" if evidence else "none"),
        "evidence": evidence,
        "conflict": conflict,
        "confidence": 0.99
        if role and role == cad.get("role")
        else (0.9 if evidence else 0.0),
    }, issues


def check_model_scope(reg, data, issues):
    pm = data.get("project_metadata") or {}
    gps = pm.get("global_origin_gps")
    if gps and reg.gps_default:
        try:
            same = (
                abs(fnum(gps[0]) - fnum(reg.gps_default[0])) < reg.gps_tol
                and abs(fnum(gps[1]) - fnum(reg.gps_default[1])) < reg.gps_tol
            )
        except (IndexError, TypeError):
            same = False
        if same:
            issues.append(
                (
                    "DATA_GPS_TEMPLATE_DEFAULT",
                    SEV_WARN,
                    "GPS je vychozi umisteni SketchUpu, ne lokalita stavby",
                )
            )
    if not (pm.get("metadata") or {}).get("IfcGUIDs"):
        issues.append(("DATA_NO_IFC_GUIDS", SEV_INFO, "model nema IFC GUIDs"))
    if not data.get("export_profile"):
        issues.append(("DATA_NO_PROFILE", SEV_BLOCK, "chybi export_profile"))


def check_duplicates(nodes, issues):
    for pid, cnt in Counter(n.get("persistent_id") for n in nodes).items():
        if pid is None or cnt <= 1:
            continue  # exports without persistent_id cannot be compared by pid
        issues.append(
            (
                "DATA_DUPLICATE_PID",
                SEV_BLOCK,
                "pid:%s" % pid,
                "persistent_id %s se v modelu opakuje %dx" % (pid, cnt),
            )
        )


# ---------------------------------------------------------------- navrh jmen


def propose_names(proposals, nodes):
    """Deterministicka jmena: zaklad z definice/role + poradove cislo podle polohy.

    Existujici smysluplne jmeno se nikdy neprepisuje - navrh dostane jen
    entita s generickym nebo chybejicim jmenem (stejne pravidlo jako
    tools/cad2llm_derive.py).
    """
    by_uid = {n.get("uid"): n for n in nodes}
    used = {
        norm(n.get("name"))
        for n in nodes
        if norm(n.get("name")) and norm(n.get("name")) not in GENERIC_NAMES
    }
    groups = defaultdict(list)
    for p in proposals:
        node = by_uid.get(p["uid"]) or {}
        current = norm(node.get("name"))
        if current not in GENERIC_NAMES and not current.startswith("component#"):
            p["proposed_name"] = None
            continue
        if p["kind"] == "junk":
            p["proposed_name"] = "junk_%s" % norm(p["definition_name"] or "component")
            continue
        base = norm(p["definition_name"])
        if base in ("", "component", "difference", "group", "solid") or base.startswith(
            "component"
        ):
            base = norm(p["role"]) or "element"
        groups[base].append(p)

    def unique(name):
        if name not in used:
            used.add(name)
            return name
        i = 2
        while "%s_%02d" % (name, i) in used:
            i += 1
        out = "%s_%02d" % (name, i)
        used.add(out)
        return out

    for base, items in groups.items():
        if len(items) == 1:
            items[0]["proposed_name"] = unique(base)
            continue

        def key(p):
            sp = (by_uid.get(p["uid"]) or {}).get("spatial") or {}
            c = vec(sp.get("center_mm"))
            return (round(c[0], 1), round(c[1], 1), round(c[2], 1), p["persistent_id"])

        for i, p in enumerate(sorted(items, key=key), start=1):
            p["proposed_name"] = unique("%s_%02d" % (base, i))
    return proposals


# ---------------------------------------------------------------- patch


def _current_field(node, field):
    """Hodnota, kterou uz model ma (None kdyz chybi)."""
    if field == "name":
        return node.get("name")
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    dictionary, _, key = str(field).partition(".")
    block = meta.get(dictionary) if isinstance(meta.get(dictionary), dict) else {}
    return block.get(key)


def build_patch(res, review=None):
    """Prevede navrhy na zapisovy kontrakt pro bridge do SketchUpu.

    Pole jsou presne ta, ktera exporter cte z atributovych slovniku
    (metadata.<dict>.<key>): cad2llm.role, cad2llm.system, ifc.type, a jmeno.
    Nic jineho se do modelu nepise - odvozene veci (kind, intent, zastupne
    priznaky) si kazdy konzument spocita sam.
    """
    review = review or {}
    cmds, pending = [], []
    nodes = {n.get("uid"): n for n in res["_nodes"]}

    for p in res["proposals"]:
        node = nodes.get(p["uid"]) or {}
        target = {"persistent_id": p["persistent_id"]}
        path = node.get("hierarchy_path")
        if path:
            target["hierarchy_path"] = path
        if node.get("name"):
            target["name"] = node["name"]

        wanted = []
        if p["role"]:
            wanted.append(("cad2llm.role", p["role"]))
        if p["system"]:
            wanted.append(("cad2llm.system", p["system"]))
        if p["ifc"]:
            wanted.append(("ifc.type", p["ifc"]))
        if p.get("zone") and p["zone"] != "unknown":
            # Without an explicit zone every re-export falls back to "unknown"
            # in the v5 semantics, so the zone has to travel to the model too.
            wanted.append(("cad2llm.zone", p["zone"]))
        if p.get("proposed_name") and norm(node.get("name")) in (
            "",
            "unnamed",
            "component",
            "difference",
            "group",
        ):
            wanted.append(("name", p["proposed_name"]))

        decided = review.get(str(p["persistent_id"]))
        if decided and decided.get("decision") == "reject":
            continue
        if decided and decided.get("decision") == "value" and decided.get("field"):
            wanted = [(f, v) for f, v in wanted if f != decided["field"]]
            wanted.append((decided["field"], decided["value"]))

        # Diff, ne snapshot: co uz model ma, se znovu neposila.
        wanted = [(f, v) for f, v in wanted if _current_field(node, f) != v]

        if decided:
            status = "approved"
        elif p["conflict"] or not p["role"]:
            status = "pending_review"
        else:
            status = "auto"

        if status == "pending_review":
            pending.append(
                {
                    "target": target,
                    "definition_name": p["definition_name"],
                    "tag": p["tag"],
                    "fields": {f: v for f, v in wanted},
                    "question": "role si odporuje"
                    if p["conflict"]
                    else "roli nelze odvodit z dostupnych poli",
                    "evidence": p["evidence"],
                    "decision": None,
                }
            )
            continue

        for field, value in wanted:
            cmds.append(
                {
                    "op": "set_attribute",
                    "target": target,
                    "field": field,
                    "value": value,
                    "status": status,
                    "evidence": p["evidence"],
                    "confidence": p["confidence"],
                }
            )

    return {
        "contract": PATCH_CONTRACT,
        "model": res["model"],
        "model_name": res["model_name"],
        "source_sha256": res["input_sha256"],
        "registry_version": res.get("registry_version"),
        "commands": cmds,
        "commands_auto": sum(1 for c in cmds if c["status"] == "auto"),
        "commands_approved": sum(1 for c in cmds if c["status"] == "approved"),
        "pending_count": len(pending),
    }, pending


# ---------------------------------------------------------------- beh


def run_gate(model_path, reg):
    with open(model_path, encoding="utf-8") as fh:
        data = json.load(fh)
    nodes = data.get("nodes") or []

    scope, node_issues, proposals = [], [], []
    check_model_scope(reg, data, scope)
    check_duplicates(nodes, node_issues)
    # container = a node other nodes point at with parent_uid; its bbox is the
    # union of children while geometry covers direct entities only.
    containers = {n.get("parent_uid") for n in nodes if n.get("parent_uid")}

    for node in nodes:
        eid = entity_id(node)
        for code, sev, msg in analyze_frame(
            reg, node, has_children=node.get("uid") in containers
        ):
            node_issues.append((code, sev, eid, msg))
        proposal, sem_issues = classify_semantics(reg, node)
        proposals.append(proposal)
        for code, sev, msg in sem_issues:
            node_issues.append((code, sev, eid, msg))

    proposals = propose_names(proposals, nodes)

    seen, dedup = set(), []
    for item in node_issues:
        key = (item[0], item[2])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)

    all_issues = [(c, s, "model", m) for c, s, m in scope] + dedup
    overrides = reg.severity_overrides
    all_issues = [(c, overrides.get(c, s), e, m) for c, s, e, m in all_issues]

    by_sev = Counter(s for _, s, _, _ in all_issues)
    by_code = Counter(c for c, _, _, _ in all_issues)
    total = len(nodes)

    res = {
        "model": os.path.basename(model_path),
        "model_name": (data.get("project_metadata") or {}).get("name"),
        "export_profile": data.get("export_profile"),
        "schema_version": data.get("schema_version"),
        "registry_version": reg.version,
        "nodes": total,
        "counts": dict(by_sev),
        "codes": dict(sorted(by_code.items())),
        "coverage": {
            "role": sum(
                1 for p in proposals if p["role"] and p["role"] != "junk_component"
            ),
            "zone": sum(
                1 for p in proposals if p.get("zone") and p["zone"] != "unknown"
            ),
            "ifc": sum(1 for p in proposals if p["ifc"]),
            "reference": sum(1 for p in proposals if p["kind"] == "reference"),
            "incomplete": sum(
                1 for p in proposals if p["kind"] == "incomplete_geometry"
            ),
            "junk": sum(1 for p in proposals if p["kind"] == "junk"),
            "total": total,
        },
        "issues": [
            {"code": c, "severity": s, "entity": e, "message": m}
            for c, s, e, m in all_issues
        ],
        "proposals": proposals,
        "input_sha256": hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16],
        "_nodes": nodes,
    }
    res["verdict"] = (
        "BLOCK" if by_sev[SEV_BLOCK] else ("WARN" if by_sev[SEV_WARN] else "PASS")
    )
    return res


# ---------------------------------------------------------------- vystup


def print_report(res, patch_info):
    cov = res["coverage"]
    print("")
    print("=" * 78)
    print(
        "MODEL: %s  (%s, %d entit)"
        % (res["model_name"], res["export_profile"], res["nodes"])
    )
    print("=" * 78)
    print(
        "verdikt: %-6s  blokuje=%d  varovani=%d  info=%d"
        % (
            res["verdict"],
            res["counts"].get(SEV_BLOCK, 0),
            res["counts"].get(SEV_WARN, 0),
            res["counts"].get(SEV_INFO, 0),
        )
    )
    print(
        "role %d/%d, zona %d/%d, IFC %d/%d, reference %d, nedodelane %d, smeti %d"
        % (
            cov["role"],
            cov["total"],
            cov["zone"],
            cov["total"],
            cov["ifc"],
            cov["total"],
            cov["reference"],
            cov["incomplete"],
            cov["junk"],
        )
    )
    patch, pending = patch_info
    print(
        "zapis do modelu: %d prikazu (%d auto, %d schvaleno), %d k rozhodnuti"
        % (
            len(patch["commands"]),
            patch["commands_auto"],
            patch["commands_approved"],
            patch["pending_count"],
        )
    )

    for sev, label in (
        (SEV_BLOCK, "BLOKUJE"),
        (SEV_WARN, "VAROVANI"),
        (SEV_INFO, "INFO"),
    ):
        items = [i for i in res["issues"] if i["severity"] == sev]
        if not items:
            continue
        agg = defaultdict(list)
        for i in items:
            agg[i["code"]].append(i)
        print("\n-- %s (%d) --" % (label, len(items)))
        for code, lst in sorted(agg.items(), key=lambda kv: -len(kv[1])):
            print("  %-30s %3dx  %s" % (code, len(lst), lst[0]["message"][:64]))
            if len(lst) > 1 and sev != SEV_INFO:
                print(
                    "  %-30s      dale: %s%s"
                    % (
                        "",
                        ", ".join(x["entity"] for x in lst[1:4]),
                        " ..." if len(lst) > 4 else "",
                    )
                )
    if pending:
        print("\n-- K ROZHODNUTI (clovek) --")
        for c in pending[:8]:
            fields = ", ".join(
                "%s=%s" % (k, v) for k, v in list(c["fields"].items())[:3]
            )
            print("    %-30s %s" % (str(c["target"].get("persistent_id"))[:30], fields))


def safe_dump(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)


PROFILE_RANK = {"geometry": 3, "spatial": 2}


def export_rank(res):
    """Poradi exportu jednoho modelu: vic dat vyhrava."""
    return (
        PROFILE_RANK.get(res["export_profile"], 1 if res["export_profile"] else 0),
        res["nodes"],
        res["model"],
    )


def write_outputs(results, out_dir, stamp=None):
    os.makedirs(out_dir, exist_ok=True)
    generated = stamp or datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    records = []
    for res, patch_info in results:
        patch, pending = patch_info
        stamp = os.path.splitext(res["model"])[0]
        gate = {k: v for k, v in res.items() if k != "_nodes"}
        safe_dump(os.path.join(out_dir, stamp + ".gate.json"), gate)
        safe_dump(os.path.join(out_dir, stamp + ".patch.json"), patch)
        safe_dump(
            os.path.join(out_dir, stamp + ".review.json"),
            {
                "contract": "cad2llm/semantic-review/v1",
                "model": res["model"],
                "instruction": "U kazde polozky doplnit 'decision': 'approve' | 'reject' | 'value' "
                "(u 'value' jeste 'field' a 'value'). Pote spustit gate znovu s --review <tento soubor>.",
                "items": pending,
            },
        )
        records.append(
            {
                "model_name": res["model_name"] or stamp,
                "source": res["model"],
                "profile": res["export_profile"],
                "schema": res["schema_version"],
                "verdict": res["verdict"],
                "nodes": res["nodes"],
                "blocking": res["counts"].get(SEV_BLOCK, 0),
                "warnings": res["counts"].get(SEV_WARN, 0),
                "roles": res["coverage"]["role"],
                "zones": res["coverage"]["zone"],
                "commands": len(patch["commands"]),
                "pending": patch["pending_count"],
                "sha256": res["input_sha256"],
                "canonical": False,
            }
        )

    groups = defaultdict(list)
    for rec in records:
        groups[rec["model_name"]].append(rec)
    for items in groups.values():
        best = max(
            items,
            key=lambda r: (
                PROFILE_RANK.get(r["profile"], 1 if r["profile"] else 0),
                r["nodes"],
                r["source"],
            ),
        )
        best["canonical"] = True
        items.sort(
            key=lambda r: (
                not r["canonical"],
                -PROFILE_RANK.get(r["profile"], 1 if r["profile"] else 0),
                -r["nodes"],
                r["source"],
            )
        )

    safe_dump(
        os.path.join(out_dir, "INDEX.json"),
        {
            "contract": "cad2llm/gate-index/v1",
            "generated": generated,
            "exports": records,
            "models": len(groups),
            "export_count": len(records),
        },
    )

    head = (
        "| model | export | profil | verdikt | entit | blokuje | varovani "
        "| role | zona | zapis | k rozhodnuti |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|---|"
    lines = [head, sep]
    for name in sorted(groups):
        for r in groups[name]:
            lines.append(
                "| %s | %s%s | %s | %s | %d | %d | %d | %d/%d | %d/%d | %d | %d |"
                % (
                    name,
                    r["source"],
                    " **(K)**" if r["canonical"] else "",
                    r["profile"] or "-",
                    r["verdict"],
                    r["nodes"],
                    r["blocking"],
                    r["warnings"],
                    r["roles"],
                    r["nodes"],
                    r["zones"],
                    r["nodes"],
                    r["commands"],
                    r["pending"],
                )
            )
        if len(groups[name]) > 1:
            others = [r for r in groups[name] if not r["canonical"]]
            lines.append(
                "| | _%d varianta/y tehoz modelu, data se lisi_ | | | | | | | | | |"
                % len(others)
            )

    worst = Counter(r["verdict"] for r in records)
    with open(os.path.join(out_dir, "SUMMARY.md"), "w", encoding="utf-8") as fh:
        fh.write("# CAD2LLM gate - souhrn\n\n")
        fh.write(
            "%d exportu, %d modelu. Model muze mit vic exportu (jiny profil); "
            "kanonicky je ten s nejvic daty a je oznaceny **(K)**.\n\n"
            % (len(records), len(groups))
        )
        fh.write("\n".join(lines) + "\n\n")
        fh.write(
            "Verdikty: %s\n" % ", ".join("%s %d" % (k, worst[k]) for k in sorted(worst))
        )
        needs = sum(1 for r in records if r["pending"])
        fh.write("Exportu s polozkou k lidskemu rozhodnuti: %d\n" % needs)


def load_review(path):
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    out = {}
    for item in data.get("items") or []:
        if item.get("decision"):
            out[str(item["target"]["persistent_id"])] = item
    return out


def discover(directory):
    found = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        if any(x in name for x in (".gate.", ".patch.", ".review.")):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (ValueError, OSError):
            continue
        if isinstance(data, dict) and data.get("nodes") and data.get("schema_version"):
            found.append(path)
    return found


# ---------------------------------------------------------------- selftest


def selftest():
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)
        print("  %-54s %s" % (name, "ok" if cond else "FAIL"))

    check("rotace 90 v Z", abs(rot_from_deg(0, 0, 90)[0][1] + 1) < 1e-9)
    ext = aabb_extents(rot_from_deg(0, 0, 90), [4500.0, 24.0, 1990.0])
    check("obal otocene zdi", abs(ext[0] - 24) < 1e-6 and abs(ext[1] - 4500) < 1e-6)
    check("nazev #1", norm("Prkno_lavicka#1") == "prkno_lavicka")
    check("diakritika", norm("vzpěry") == "vzpery")
    check("mezery v nazvu", norm("Worktop #01 hlavni") == "worktop_01_hlavni")
    m = [[0, -1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    check(
        "matice 4x4 -> 3x3", mat_diff(mat3_from_4x4(m), rot_from_deg(0, 0, 90)) < 1e-9
    )
    q = quat_to_mat(0.653281, 0.270598, -0.270598, -0.653281)
    check("quaternion -> rotace", mat_diff(q, rot_from_deg(45, 0, -90)) < 1e-4)
    check(
        "identita",
        is_identity(rot_from_deg(0, 0, 0)) and not is_identity(rot_from_deg(0, 0, 90)),
    )
    check("bed vs bed_frame", compatible_roles("bed", "bed_frame"))
    check("wall vs space_boundary", not compatible_roles("wall", "space_boundary"))
    sc, rot = decompose([[0.8989, 0, 0], [0, 0.82, 0], [0, 0, 1.0]])
    check(
        "rozklad meritka",
        abs(sc[0] - 0.8989) < 1e-3 and abs(sc[1] - 0.82) < 1e-3 and is_identity(rot),
    )

    reg = Registry(DEFAULT_REGISTRY)
    reg_roles, reg_systems = set(), set()
    for section in (reg.definitions, reg.tag_map, reg.meta_types):
        for rec in section.values():
            if rec.get("role"):
                reg_roles.add(rec["role"])
            if rec.get("system"):
                reg_systems.add(rec["system"])
    reg_roles.update(reg.reference_roles)
    check(
        "slovnik: role registru jsou ve slovniku (%d)" % len(reg_roles),
        not (reg_roles - reg.vocab_roles),
    )
    check(
        "slovnik: systemy registru jsou ve slovniku",
        not (reg_systems - reg.vocab_systems),
    )
    check(
        "slovnik: zona se odvodi z role",
        reg.zone_for("column", None) == ("envelope", "vocab.zone"),
    )
    check(
        "slovnik: zadana zona vyhrava", reg.zone_for("column", "work") == ("work", None)
    )
    check(
        "slovnik: role mimo slovnik blokuje",
        any(
            c == "SEM_VOCAB_ROLE_DRIFT"
            for c, _, _ in reg.conformance("nonsense", None, None, None)
        ),
    )
    check(
        "slovnik: IFC mimo tvar IfcXxx blokuje",
        any(
            c == "SEM_IFC_MALFORMED"
            for c, _, _ in reg.conformance("wall", None, "Wall", None)
        ),
    )
    print("\nselftest: %d selhani" % len(fails))
    return 0 if not fails else 2


# ---------------------------------------------------------------- main


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    ap = argparse.ArgumentParser(description="CAD2LLM gate")
    ap.add_argument("models", nargs="*", help="cesty k exportum")
    ap.add_argument("--dir", help="adresar; vsechny exporty se najdou automaticky")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--out", default=None)
    ap.add_argument("--review", default=None, help="soubor s lidskymi rozhodnutimi")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument(
        "--stamp",
        default=None,
        help="pevny casovy razitko behu (YYYY-MM-DDTHH:MM:SSZ) pro reprodukovatelny vystup",
    )
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    paths = list(args.models)
    if args.dir:
        paths.extend(discover(args.dir))
    if not paths:
        ap.print_help()
        return 2

    reg = Registry(args.registry)
    results = []
    for path in paths:
        if not os.path.isfile(path):
            print("CHYBA: soubor nenalezen: %s" % path)
            return 2
        res = run_gate(path, reg)
        review_path = args.review
        if review_path and len(paths) > 1:
            cand = os.path.join(
                os.path.dirname(review_path),
                os.path.splitext(res["model"])[0] + ".review.json",
            )
            review_path = cand if os.path.isfile(cand) else args.review
        patch_info = build_patch(res, load_review(review_path))
        results.append((res, patch_info))
        if not args.quiet:
            print_report(res, patch_info)

    if args.out:
        write_outputs(results, args.out, args.stamp)
        print("\nreporty: %s" % os.path.abspath(args.out))

    worst = 0
    for res, _ in results:
        worst = max(
            worst,
            2 if res["verdict"] == "BLOCK" else (1 if res["verdict"] == "WARN" else 0),
        )
    return worst


if __name__ == "__main__":
    sys.exit(main())
