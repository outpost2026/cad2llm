# CAD2LLM Bridge — protokol v1

**Datum:** 2026-10-23 | **Autor:** outpost2026 | **Účel:** lokální obousměrná smyčka agent ↔ běžící SketchUp 2024

Bridge je součástí rozšíření CAD2LLM Exporter 4.0.0 (`src/cad2llm_exporter/lib/bridge.rb`).
Umožňuje LLM agentovi číst model, aplikovat deklarativní anotační plán a ověřit výsledek
**bez copy-paste do Ruby konzole**.

---

## 1. Životní cyklus

1. SketchUp 2024 otevře model (`.skp`).
2. `Extensions → CAD2LLM → Start local bridge (127.0.0.1:9877)`.
3. Bridge vygeneruje token a zapíše `%TEMP%\cad2llm_bridge.json`:
   ```json
   {"app":"cad2llm","protocol":"1.0","host":"127.0.0.1","port":9877,
    "token":"<32 hex>","pid":12345,"started_at":"2026-10-23T08:00:00Z"}
   ```
4. Klient (`tools/cad2llm_bridge.py`) soubor přečte a posílá requesty.
5. `Stop local bridge` zavře socket, klienty i token file.

Model se needituje nikdy mimo hlavní vlákno: server je obsluhován z `UI.start_timer`
(interval 50 ms), takže každá operace běží v UI vlákně a v undo transakci.

## 2. Bezpečnostní model

| Prvek | Pravidlo |
|---|---|
| Bind | pouze `127.0.0.1` (žádný LAN) |
| Autorizace | token v každém requestu kromě `ping`; porovnání v konstantním čase |
| Token file | v `Dir.tmpdir` (per-user), smazán při `stop` |
| Mutace | jen `annotate` operace (metadata / tag / jméno), 1 operace = 1 undo krok |
| Geometrie | bridge v1 geometrii nemění; `model.save` je záměrně mimo API |
| Limity | request ≤ 1 MiB, plán ≤ 500 operací, čtení po řádcích |
| Rollback | `undo` = `Sketchup.send_action("editUndo:")` (poslední operace) |

Bridge neumí ukládat soubor. Uložení zůstává na uživateli — tím je vyloučeno, že agent
přepíše `.skp` neověřeným stavem.

## 3. Transport

- TCP, newline-delimited UTF-8 JSON, 1 request = 1 řádek, 1 response = 1 řádek.
- Klient otevírá spojení per request (žádný keep-alive v v1).

Request:
```json
{"id":1,"method":"info","params":{},"token":"<token>"}
```

Response OK:
```json
{"id":1,"ok":true,"result":{...}}
```

Response error:
```json
{"id":1,"ok":false,"error":{"code":"bad_request","message":"..."}}
```

Kódy: `unauthorized`, `bad_request`, `bad_json`, `payload_too_large`, `internal_error`.

## 4. Metody

### `ping` (bez tokenu)
```json
{"protocol":"1.0","sketchup":"24.0.484","authenticated":true}
```

### `info`
Vrací `title`, `path`, `top_level_entities`, `top_level_groups`,
`top_level_components`, `tags`, `read_only`, `modified`, `sketchup`.

### `attributes`
Read-only inventory **všech** attribute dictionary, včetně těch, které exporter
filtruje (`SU_*`, `BIM_Ifc`, `DynamicComponents`). Slouží k auditu metadat.

Parametry: `limit` (default 200).

```json
{"entities_total":46,"entities_with_dicts":0,"definitions_with_dicts":3,
 "entities":[{"persistent_id":3348,"path":["Bed_01"],"tag":"bed_m2",
              "dicts":{"BIM_Ifc":{"type":"IfcFurniture"}}}],
 "definitions":{"sleep_zone":{"DynamicComponents":{"_length":"200"}}},
 "model_dicts":{}}
```

### `describe`
Read-only náhled anotace pro člověka i agenta: jméno, tag, definition name,
`ComponentDefinition#description` (viditelné v panelu Komponenty) a všechny dicty.

Parametry: `targets` (array) nebo `target`; prázdné = aktuální výběr.
Target je string (= jméno definice), `{"kind":"definition","name":...}` nebo
stejné tvary jako v anotačním plánu (`persistent_id`, `hierarchy_path`, `name`).

```json
{"count":1,"items":[{"kind":"component","persistent_id":3348,"name":"bed_01",
  "tag":"bed","definition_name":"bed_01","definition_description":"Postel | sleep",
  "dicts":{"cad2llm":{"role":"bed","zone":"sleep"},"ifc":{"type":"IfcFurniture"}},
  "summary":"bed | sleep | furniture | IfcFurniture"}]}
```

### `purge_unused_tags`
Smaže tagy bez entit (vratné přes Undo). Vrací `purged`, `before`, `after`, `tags`.

```json
{"purged":["walls","energy_nodes"],"before":26,"after":15,"tags":["wall","battery"]}
```

### `export`
Parametry: `path` (volitelné, default vedle `.skp` jako `<basename>_cad2llm_v4.json`),
`schema` (`"4.0"` | `"3.0"`).

```json
{"path":"C:/.../mistnost_2_v1.0_cad2llm_v4.json","bytes":79592,"schema":"4.0",
 "nodes":46,"issues":2,"errors":0,"warnings":0}
```

### `apply`
Parametry: `plan` (objekt) nebo `plan_path`, `dry_run` (bool).

```json
{"schema":"cad2llm/annotate_report/v1","dry_run":false,
 "operations":[{"index":0,"op":"set_attribute","status":"applied",
                "uid":"101@bed_v1","previous":null}],
 "summary":{"total":1,"applied":1,"planned":0,"unchanged":0,"errors":0},
 "post_check":{"nodes":46,"union_bbox_mm":{"min":[...],"max":[...]},
               "tag_distribution":{"bed_m2":3},"issues":2}}
```

`post_check` je in-memory re-export (`CAD2LLM::Exporter.build`) — deterministický
otisk stavu po zápisu, který agent porovná s exportem před zápisem.

Stavy operace: `applied`, `planned` (dry run), `unchanged` (idempotence), `error`.
Chybové kódy: `INVALID_TARGET`, `TARGET_UNRESOLVED`, `AMBIGUOUS_TARGET`,
`NO_MATCH_AFTER_FILTER`, `INVALID_OP`, `RESERVED_DICT`, `INVALID_VALUE`, `EXCEPTION`.

### `undo`
```json
{"undone":true,"via":"Sketchup.send_action('editUndo:')"}
```

## 5. Anotační plán

Schéma: `docs/schema/annotate_plan.schema.json` (`cad2llm/annotate_plan/v1`).

```json
{
  "schema": "cad2llm/annotate_plan/v1",
  "name": "mistnost_2 semantics",
  "operations": [
    {"op": "set_attribute", "target": {"persistent_id": 3348},
     "dict": "zone", "key": "type", "value": "sleep"},
    {"op": "set_tag", "target": {"hierarchy_path": ["zone_bed", "bed_v1"]}, "tag": "bed_m2"},
    {"op": "rename", "target": {"name": "bed_v1", "tag": "bed_m2"}, "name": "bed_m2_01"},
{"op": "set_description", "target": {"kind": "definition", "name": "bed_01"},
 "description": "Postel | sleep | furniture | IfcFurniture", "preserve_original": true}
  ]
}
```

### Rozlišení cíle (deterministické)

1. `persistent_id` — primární; pokud je stejné PID u více instancí sdílené definice,
   zužte `hierarchy_path`.
2. `hierarchy_path` — přesná shoda, pak normalizovaná (case, `_`/mezery).
3. `name` — přesná shoda, pak normalizovaná; volitelné filtry `tag`, `definition_name`.

Více kandidátů = `AMBIGUOUS_TARGET`, operace se **neprovede**. Nikdy se nehádá.

### Sémantika operací

| Op | Pole | Chování |
|---|---|---|
| `set_attribute` | `dict`, `key`, `value` | `SU_*` dictionary odmítnuto; stejná hodnota = `unchanged` |
| `set_tag` | `tag` | tag se v modelu vytvoří, pokud chybí |
| `rename` | `name` | mění `entity.name`; doporučeno doplnit `tag`/`persistent_id` |
| `set_description` | `description`, `preserve_original?` | zapisuje `ComponentDefinition#description` (viditelné v UI); `preserve_original` uloží původní text do `cad2llm/original_description` |

## 6. Agentní smyčka (doporučený postup)

```
1. export            -> baseline JSON
2. info              -> ověř title/path (správný model)
3. apply dry_run     -> zkontroluj planned/chyby
4. apply             -> applied + post_check
5. export            -> after JSON
6. python tools/cad2llm.py diff baseline.json after.json
7. rozdíl jen v metadatech/tazích? -> accept
   neočekávaná geometrie?          -> undo
```

Bod 6 je povinný: `post_check.union_bbox_mm` a `nodes` musí odpovídat baseline
(anotace nesmí měnit geometrii).

## 7. CLI klient

```powershell
python tools\cad2llm_bridge.py ping
python tools\cad2llm_bridge.py info
python tools\cad2llm_bridge.py attributes
python tools\cad2llm_bridge.py describe --pid 3348
python tools\cad2llm_bridge.py describe --definition bed_01
python tools\cad2llm_bridge.py purge-tags
python tools\cad2llm_bridge.py export
python tools\cad2llm_bridge.py export out.json --schema 4.0
python tools\cad2llm_bridge.py apply plan.json --dry-run
python tools\cad2llm_bridge.py apply plan.json --report report.json
python tools\cad2llm_bridge.py undo
```

Exit kódy: `0` ok, `2` bridge nedostupný / chybí token file, `3` bridge vrátil chybu.

## 8. Testy

```powershell
C:\Ruby32-x64\bin\ruby.exe spec\run_all.rb        # 3 soubory, 129 checks
python -m unittest discover -s tests -v           # 53 testů
```

`spec/test_bridge.rb` testuje dispatch i reálné TCP linky (ephemeral port,
`auto_poll: false` + ruční `Bridge.poll`). `tests/test_cad2llm_bridge.py`
testuje klienta proti fake serveru.

## 9. Mimo rozsah v1

- geometrické operace (move/scale/create) — M4
- keep-alive, vícenásobné requesty v jednom spojení napříč ticky
- autentizace jiná než lokální token
- dávkové transakce přes více requestů
- undo stack introspection (jen slepé `editUndo:`)
