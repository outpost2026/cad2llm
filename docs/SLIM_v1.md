# LLM handoff tier — `cad2llm/slim/v1`

**Datum:** 2026-10-25 | **Nástroj:** `python tools/cad2llm.py slim`

Slim vrstva zmenšuje tokenovou náročnost v4/v5 exportu bez ztráty signálu pro
design LLM: topology (`relations`), souhrny (`brief`, `metrics` bez `z_table`),
uzly s bbox + sémantikou, definice centralizované mimo instance.

```powershell
python tools/cad2llm.py enrich  examples/mistnost_2_v7_v4_gui.json out_v5.json
python tools/cad2llm.py slim    out_v5.json out_slim.json
python tools/cad2llm.py slim    model_geometry.json slim.json --geometry summary --compact
```

## Co zmizí (low SNR)

- `spatial`: `matrix_4x4`, `size_mm`, `center_mm`, `origin_mm`; identitní rotace
- `nodes[]`: `hierarchy_path`, `definition_description`, celý mesh (`geometry`) — viz režim
- `semantics.evidence[]` → `evidence_rules[]` (jen rule_id)
- `relations[].id`, `relations[].evidence`
- `metrics.z_table`, `validation.clearances`, `validation.overlaps`
- `project_metadata`: GPS, capabilities, absolutní `model_path`, IfcGUIDs

## Co zůstane (high SNR)

- `uid`, `persistent_id`, `parent_uid`, tagy, role/zóna/intent
- `bbox_mm` (+ quaternion jen pokud ≠ identita)
- `definitions[]` — popis a metadata definice jednou
- `relations`, `brief`, agregované `metrics`, `validation.counts` + `issues`

## Geometry režimy

| `--geometry` | Význam |
|---|---|
| `drop` (default) | žádný geometry blok na uzlu |
| `summary` | `status`, `counts`, `hash`, volitelně `volume_mm3` |
| `keep` | beze změny (jen spatial/metadata slim) |

## Ruby compact export

Bridge / GUI export může zapsat minifikovaný JSON: RPC parametr `"compact": true`
nebo CLI `cad2llm_bridge.py export out.json --compact`.
---
#CLI commands
# základní
python tools/cad2llm_slim.py examples/mistnost_2_v1.2_cad2llm_v4_geometry.json out_slim.json

# s energetikou + statistikami
python tools/cad2llm_slim.py out_v5.json out_slim_v5.json --keep-domain-dicts --print-stats

# minified, bez zápisu (jen ověření)
python tools/cad2llm_slim.py out_v5.json out_slim.json --compact --dry-run

# pipe (stdin -> stdout)
Get-Content out_v5.json | python tools/cad2llm_slim.py - - --compact > out_slim.json

# help
python tools/cad2llm_slim.py --help
