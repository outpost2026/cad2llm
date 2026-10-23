# Schema v4 — deterministický JSON pro LLM/RAG

**Verze:** 4.1 (aditivní; `schema_version` zůstává `"4.0"`) | **Datum:** 2026-10-23 | **Jednotky:** mm | **Osy:** Z-up (SketchUp)

Schema v4 sjednocuje dva historické kanály (Python/DAE a Ruby/SketchUp) do jednoho
tvaru a doplňuje data, která metodika vyžaduje, ale starší exporty neměly.

## 1. Kanály a jejich schopnosti

| Schopnost | `channel: sketchup` (extension) | `channel: dae` (Collada) |
|---|:--:|:--:|
| hierarchie (`parent_uid`, `hierarchy_path`) | ✅ | ✅ |
| `bbox_mm` min/max | ✅ | ✅ |
| rotace (Euler + quaternion + matice) | ✅ | ✅ |
| jméno instance vs. jméno definice | ✅ | ✅ |
| **popis definice** (`definition_description`) | ✅ | ❌ |
| **tagy (vrstvy)** | ✅ | ❌ |
| **attribute dictionaries** (`energy`, `zone`, `safety`, `thermal`) | ✅ | ❌ |
| `persistent_id` | ✅ | ❌ |
| GPS z `shadow_info` | ✅ | ❌ |
| přesné názvy | ✅ | ⚠️ mezery → `_` |

`project_metadata.capabilities` nese tyto vlajky strojově, takže konzument (LLM,
validátor) pozná, co v datech chybí, a nesmí to domýšlet.

**Kritické:** DAE kanál **není** náhrada Ruby exportu pro metadata a tagy.
Pro analýzy závislé na `energy`/`zone`/`safety` je nutný export z SketchUpu.

## 2. Dokument

```json
{
  "schema_version": "4.0",
  "project_metadata": {
    "name": "mistnost_2_sk24",
    "export_date": "2026-10-22T18:00:00Z",
    "units": "mm",
    "up_axis": "Z_UP",
    "global_origin_gps": [50.150888, 14.42336652],
    "metadata": { "cad2llm": { "purpose": "bydleni", "phase": "studie" } },
    "source": {
      "channel": "sketchup",
      "file": "mistnost_2_sk24.skp",
      "model_path": "C:/Users/.../mistnost_2_sk24.skp",
      "sketchup_version": "24.0.0"
    },
    "capabilities": { "hierarchy": true, "bbox_mm": true, "rotation": true,
                      "tags": true, "definition_description": true,
                      "metadata_dictionaries": true,
                      "persistent_ids": true, "gps": true }
  },
  "nodes": [ ... ],
  "validation": { ... }
}
```

`project_metadata.metadata` nese slovníky **modelu** (projektová úroveň: účel,
fáze, obsazenost, klima). Model se plní plánem s `target.kind = "model"`; export
i annotátor čtou stejné slovníky, takže round-trip je idempotentní. `SU_*`
slovníky se vynechávají stejně jako u uzlů.

## 3. Uzel

| Pole | Význam |
|---|---|
| `uid` | `persistent_id@hierarchy_path` (SU) nebo DAE `id`. **Stabilní klíč**, unikátní i pro děti sdílené definice. |
| `persistent_id` | SU persistent ID, `null` u DAE |
| `name` | jméno **instance** (to, co uživatel vidí ve SketchUpu) |
| `definition_name` | jméno **definice** komponenty (`null` u skupin) |
| `definition_description` | popis definice z panelu Komponenty (`null` u skupin a u definic bez popisu) — od 4.1 |
| `entity_type` | `group` \| `component` \| `node` |
| `tag` | vrstva/tag, `null` u DAE |
| `hierarchy_path` | pole jmen od kořene, např. `["Battery","cell"]` |
| `parent_uid` | `uid` rodiče nebo `null` |
| `spatial.origin_mm` | pozice pivotu (world) |
| `spatial.bbox_mm` | **min/max** (world) — nezbytné pro `z_min`/`z_max` tabulku |
| `spatial.size_mm` | rozměry bboxu (world, po rotaci) |
| `spatial.center_mm` | střed bboxu |
| `spatial.rotation_deg` | Euler XYZ, konvence `R = Rz·Ry·Rx` (ověřeno rekonstrukcí, chyba < 2e-6) |
| `spatial.quaternion` | `{w,x,y,z}`, bez jednotek |
| `spatial.matrix_4x4` | 4×4 world matice, **translace v mm**, řádky `[xaxis, yaxis, zaxis, origin]` |
| `metadata` | slovníky bez `SU_*` (systémový balast), čísla jako čísla |

`bbox_mm` u kontejnerů (komponenta se dětmi, skupina) = sjednocení potomků —
parita s `definition.bounds` ve SketchUpu.

`metadata` čte slovníky **instance**. Definiční slovníky (`AppliedSchemaTypes`,
`IFC 2x3`) jsou data IFC Managera a exportér je neslévá — u komponent se proto
`ifc/type` drží synchronně na instanci i na definici (viz
`docs/plans/ifc2x3_fix_v2_mistnost_2_v1_1.json`).

## 4. Validační blok

```json
"validation": {
  "checked_at": "...",
  "rules": { ... },
  "counts": { "nodes": 46, "with_bbox": 46, "errors": 0, "warnings": 2 },
  "capabilities": { ... },
  "issues": [ { "code": "CLEARANCE_VIOLATION", "severity": "warn", "uid": "...", "message": "..." } ],
  "overlaps": [ { "a": "...", "b": "...", "a_name": "...", "b_name": "...", "overlap_mm3": 123.4 } ],
  "clearances": [ { "a_name": "...", "b_name": "...", "gap_mm": 210.5, "min_mm": 500, "ok": false } ],
  "z_table": [ { "uid": "...", "name": "...", "z_min": 0.0, "z_max": 180.0, "height": 180.0 } ]
}
```

Kódy: `MISSING_BBOX`, `ZERO_SIZE`, `DUPLICATE_POSITION`, `MISSING_GEOMETRY`,
`MISSING_DEFINITION_REF`, `NAME_PATTERN`, `NAME_ASCII`, `CLEARANCE_VIOLATION`,
`DUPLICATE_PERSISTENT_ID`, `LEGACY_SCHEMA`.

Pravidla (`tools/cad2llm_rules.json`): `name_pattern`, `ascii_strict`,
`min_size_mm`, `overlap_min_volume_mm3`, `clearance_rules[]`.

Překryvy se nehlásí mezi předkem a potomkem (kontejner vždy „překrývá“ své děti).

## 5. Migrace z v3

| v3 | v4 |
|---|---|
| `spatial_data_m.origin_xyz` (m) | `spatial.origin_mm` (mm) |
| `center_xyz`, `size_xyz` | `center_mm`, `size_mm` |
| `transform_matrix` (16 plochých, **často zkorumpované**) | `matrix_4x4` (nested, translace v mm) |
| `id`, `parent_id` | `uid`, `parent_uid` (`persistent_id` zůstává) |
| `instance_name` | `name` |
| `units: "meters"` | `units: "mm"` |
| bez bbox min/max | `bbox_mm.min/max` |

Legacy export lze stále vyrobit: `CAD2LLM.export_v3` (menu: *Export legacy JSON (v3)*).
`tools/cad2llm.py validate` umí načíst i v3 a převést ho na mm v paměti
(výsledek označí `LEGACY_SCHEMA`; bbox se dopočítá z `center ± size/2`).

## 6. Integrita — pravidla

1. `units` je vždy `mm`; převod z palců probíhá při exportu (`× 25.4`).
2. Žádné post-processing `gsub` nad JSONem — histogram hodnot se nesmí měnit.
3. `uid` je unikátní v rámci dokumentu (test).
4. `parent_uid` musí ukazovat na existující `uid` nebo `null` (test).
5. `bbox_mm.min <= bbox_mm.max` po všech osách (test).
6. `matrix_4x4[3] == [0,0,0,1]` a `matrix_4x4[i][3] == origin` (test).
