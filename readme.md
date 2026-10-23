# Propojení dvou světů (CAD + LLM)

> **Deterministická pipeline: fyzické měření → SketchUp → COLLADA (.dae) → sémantický JSON → LLM analýza bez halucinací.**

Schema **v4** (2026-10) sjednocuje dva exportní kanály a doplňuje data, která
předchozí verze neměly: `bbox_mm` min/max, `parent_uid`, rotace, tagy,
attribute dictionaries. Volitelný **geometry profil** přidává world-space
vertices/edges/faces pro přesné objemy, řezy a collision analýzu.
Detail: [`docs/SCHEMA_v4.md`](docs/SCHEMA_v4.md), [`docs/GEOMETRY_v1.md`](docs/GEOMETRY_v1.md).

Nad v4 staví **v5 (F1 + F2)** (aditivní): deterministická sémantika,
normalizovaná validace, relace mezi objekty, volná podlaha a `brief` pro LLM.
Detail: [`docs/DESIGN_v5.md`](docs/DESIGN_v5.md).

* * *

## Problém — jak zkombinovat LLM s 3D modely „na koleně"

LLM halucinuje při prostorové analýze CAD modelů — a to i když dostane „správný" formát.

| Fáze | Postup | Výsledek |
|---|---|---|
| 1 | Export 2D obrázku + narativní text | rady správné, ale na špatném místě |
| 2 | Raw `.dae` přímo do LLM | rétoricky spokojené, čísla vymyšlená |
| 3 | Počítat ručně | zamítnuto — automatizace je priorita |
| 4 | **Deterministický preprocessing** | JSON s mm a bounding boxy → analýza sedí |

> „LLM radí dobře, ale na špatném místě."

* * *

## Dva kanály, jeden formát

| | Kanál `sketchup` | Kanál `dae` |
|---|---|---|
| Vstup | `.skp` otevřený v GUI | `.dae` (CLI, headless) |
| Nástroj | `src/cad2llm_exporter.rb` (extension) | `tools/cad2llm.py` |
| Hierarchie, bbox, rotace | ✅ | ✅ |
| Tagy (vrstvy) | ✅ | ❌ |
| Attribute dictionaries (`energy`, `zone`, `safety`, `thermal`) | ✅ | ❌ |
| `persistent_id`, GPS | ✅ | ❌ |
| Detailní geometrie (`geometry` profil) | ✅ | ❌ |
| Přesné názvy | ✅ | ⚠️ mezery → `_`, `#` → `_`, `,` → `_` |

`project_metadata.capabilities` hlásí dostupné vlastnosti strojově — konzument
pozná, co v datech chybí, a nesmí to domýšlet.
**Pro energetické a požární analýzy je nutný kanál `sketchup`.**

* * *

## Quickstart — DAE (bez GUI)

```powershell
pip install lxml numpy

python tools/cad2llm.py convert examples/mistnost_2_sk24.dae out.json
python tools/cad2llm.py validate out.json
python tools/cad2llm.py summary  out.json
python tools/cad2llm.py diff old.json new.json
python tools/cad2llm.py enrich out.json out_v5.json
```

`enrich` je sémantická vrstva v5: deterministické role/zóny/intent, normalizovaná
validace, relace (contact / adjacency / opening_in / support / containment / proximity),
volná podlaha s passages, volumes a ~560znakový `brief` pro LLM. Pokud dokument nemá
`validation` (typicky GUI export), `enrich` ji dopočítá projektovými pravidly; uzly bez
role v názvu řeší tag fallback a dědění role z rodiče (`Unnamed` buňka → battery/energy).
Schémata a návrh: [`docs/DESIGN_v5.md`](docs/DESIGN_v5.md), [`docs/schema/`](docs/schema/).

## Quickstart — SketchUp extension

1. `releases/cad2llm_exporter.rbz` → *Window → Extension Manager → Install Extension*
2. *Extensions → CAD2LLM*
   - **Export (v4)** — aktuální schema, tagy + metadata
   - **Export detailed geometry (v4+geometry)** — navíc vertices/edges/faces, normály, objem, hash
   - **Export selection** — jen vybrané entity (spatial i geometry)
   - **Bridge: start/stop, Inspector panel** — živé RPC nad otevřeným modelem

## Bridge + anotace (M1–M3)

Extension naslouchá na `127.0.0.1:9877` (token v `%LOCALAPPDATA%\cad2llm\bridge.json`).
Veškeré úpravy jsou **jen metadata** (atributy, tagy, jména, popisy definic), geometrie se nemění.

```powershell
python tools\cad2llm_bridge.py ping                      # stav + verze SU
python tools\cad2llm_bridge.py export out.json           # v4 spatial export živého modelu
python tools\cad2llm_bridge.py export geo.json --profile geometry
python tools\cad2llm_bridge.py apply plan.json           # plán + drift guard (snapshot, rollback)
python tools\cad2llm_bridge.py export baseline.json
python tools\cad2llm_bridge.py verify --baseline baseline.json --live
python tools\cad2llm_derive.py out.json --out plan.json  # skeleton plánu z exportu
python tools\cad2llm_stamp.py out.json --vocab loft.vocab.json --out stamp.json
```

Projektové termíny patří do **add-only overlaye** (`docs/VOCAB_OVERLAY.md`), ne do
společného `rules.json`. Overlay se předává `validate`/`summary`/`derive` přes
`--vocab`; `cad2llm_stamp.py` zapíše `project_id`, `vocab_version`,
`vocab_base_version` a `vocab_hash` do modelového `cad2llm` dictionary, takže
každý další export nese původ slovníku.

Geometry profil (`--profile geometry`, menu *Export detailed geometry*) přidává
ke každému uzlu přímé `vertices`/`edges`/`faces` v world-space mm, normály,
plochu, closed/oriented, objem a deterministický hash. Spatial export zůstává
zpětně kompatibilní a legacy v3 export už extension nevyrábí.
Kontrakt: [`docs/GEOMETRY_v1.md`](docs/GEOMETRY_v1.md).

`apply` před zápisem uloží snapshot exportu a po zápisu porovná geometrii; při driftu
provede undo dávky a report označí `rolled_back`. `verify` vrací exit 3 při rozdílu
invariant (name, tag, role, zóna, IFC) — vhodné pro CI i pro kontrolu po ručním zásahu.

* * *

## Testy

```powershell
python -m unittest discover -s tests -v    # 106 testů (parse, matice, bbox, legacy, v5, vocab, overlay, derive, stamp, bridge)
C:\Ruby32-x64\bin\ruby.exe spec\run_all.rb # 239 checků (exporter, anotace, bridge)
```

Ruby testy běží proti `spec/sketchup_stub.rb` (world-space `Group#bounds`,
`Transformation`, `AttributeDictionary`), takže exportér lze vyvíjet bez GUI.

* * *

## Reálná data — `mistnost_2_sk24` (Outpost Zone 2)

Místnost **4 500 × 2 000 mm**, světlá výška **1 990 mm**, obálka s izolací
4 688 × 2 176 mm. Model obsahuje 46 uzlů (30 instancí ve scéně + 16 LFP článků
uvnitř definice baterie).

| Objekt | Bbox min (mm) | Rozměr (mm) |
|---|---|---|
| `Bed_01_mattres_with_light_frame` | 2495, 0, 0 | 2000 × 970 × 180 |
| `main_table_rectangel_food_work_dump_place` | −2, 84, 0 | 675 × 745 × 665 |
| `Kamna_Priti_mini_D` | 3989, 1612, 0 | 431 × 312 × 680 |
| `Worktop_01` | 1520, 1760, 745 | 2260 × 200 × 50 |
| `LFP_main_base` | 1690, 1760, 490 | 2087 × 240 × 35 |
| `_24V_gokwh_100ah_lfp_battery` | 2055, 1760, 0 | 525 × 240 × 220 |
| `Energy_source_main_power_invertor_MPPT` | 1790, 1900, 1075 | 270 × 100 × 350 |
| `lfp_cell_314ah` (×16) | 2718, 1828, 525 | 70 × 172 × 205 |
| `windows_jižní_dvojokno` | 4654, 45, 600 | 20 × 1136 × 1150 |
| `windows_západní_dvojokno` (spanek) | 3245, −93, 561 | 1255 × 20 × 1270 |
| `windows_západní_dvojokno` (stolování) | 330, −90, 590 | 1245 × 20 × 1221 |

Kompletní analýza: [`docs/reports/ANALYZA_mistnost_2_sk24_2026-10-22.md`](docs/reports/ANALYZA_mistnost_2_sk24_2026-10-22.md).

### Ověření integrity kanálů

Stejný model přes oba kanály: **46 vs. 46 uzlů**, spárováno **45/46**, geometrické
změny > 1 mm **žádné** (`tools/cad2llm.py diff`). Neshody jsou jen v názvech
(ztráta `#`, `,`, mezer v DAE) a v pojmenování jednoho LFP článku
(`unnamed` v Ruby vs. `lfp_cell_314ah_3.2V` v DAE), se shodným středem.

* * *

## Struktura repozitáře

```
cad2llm/
├── tools/
│   ├── cad2llm.py                  ← CLI: convert | validate | summary | diff | enrich
│   ├── cad2llm_core.py             ← DAE parser, schema v4, validace
│   ├── cad2llm_v5.py               ← F1+F2: sémantika, relace, metrics, brief
│   ├── cad2llm_bridge.py           ← bridge klient: ping/export/apply/verify/undo
│   ├── cad2llm_verify.py           ← invariantní diff exportů (exit 3 při driftu)
│   ├── cad2llm_derive.py           ← skeleton plánu z exportu (derive)
│   ├── cad2llm_stamp.py            ← modelový vocab stamp (project_id + hash)
│   ├── cad2llm_vocab.py            ← sdílený slovník + projektový overlay
│   └── cad2llm_rules.json          ← pravidla (name_pattern, clearance, overlaps, vocab)
├── src/
│   ├── cad2llm_exporter.rb         ← loader + menu (Extensions → CAD2LLM)
│   ├── cad2llm_exporter/
│   │   ├── main.rb                 ← entry point
│   │   └── lib/
│   │       ├── exporter.rb         ← exportér (v4 spatial + geometry profil)
│   │       ├── annotate.rb         ← plánovač anotací (entity/definition/model)
│   │       └── bridge.rb           ← TCP RPC server + drift guard
│   ├── legacy_python/              ← historický konvertor v3 (deprecated)
│   └── _archive/console/           ← 7 původních konzolových skriptů (archiv)
├── tests/                          ← Python testy + fixture mini.dae + golden/
├── spec/                           ← Ruby testy + SketchUp stub (headless)
├── docs/
│   ├── SCHEMA_v4.md                ← specifikace formátu
│   ├── DESIGN_v5.md                ← návrh sémantické vrstvy (F1+F2 hotové)
│   ├── schema/                     ← JSON Schemas v5 + příklady
│   ├── METODIKA_SketchUp_LLM_v4.md
│   ├── Metodika_cad2llm_postprocesing.md
│   └── reports/                    ← analýzy výstupů
├── examples/                       ← reálné exporty
└── releases/                       ← instalovatelný .rbz
```

* * *

## Klíčová technická rozhodnutí

| Rozhodnutí | Alternativa (zamítnuta) | Důvod |
|---|---|---|
| Vlastní COLLADA parser (`lxml`) | Raw `.dae` do LLM | LLM halucinuje i na „správném" formátu |
| Rekurzivní 4×4 matice (`numpy`) | Flat seznam pozic | world-space souřadnice pro zanořené komponenty |
| Bbox z geometrie (min/max) | Jen pivot point | skutečné rozměry, nutné pro z-tabulku |
| mm při parsování | Native palce | SketchUp interně v palcích, LLM pracuje v mm |
| Stabilní `uid = persistent_id@path` | Index v poli | unikátní i pro děti sdílené definice |
| `validation` blok součástí JSON | Validace až v LLM | deterministické, opakovatelné, auditovatelné |
| Noise filtr prefixy (`SU_`, `FormFonts`, `BIM*`) | Parsovat vše | eliminace exportních artefaktů SketchUpu |

* * *

## Historie (zkráceně)

- **2026-04** — v1–v3 konvertor (Deepseek → Claude Sonnet → Open Code), ruční vkládání do Ruby Console.
- **2026-04** — osový bug nalezen LLM analýzou JSONu → korekce v modelu → re-export.
- **2026-09-24** — rekonstrukce pipeline z dumpu (`RECONSTRUCTION_REPORT.md`).
- **2026-10-22** — **reaktivace**: schema v4, oprava korupce matic (`gsub` `\10`),
  oprava dvojité transformace u skupin, extension + menu, headless testy obou kanálů.
- **2026-10-22** — **v5 F1**: deterministická sémantika (role/zóna/intent + evidence),
  normalizovaná validace, LLM brief, golden master 46 uzlů.
- **2026-10-22** — **v5 F2**: relace (6 druhů, SNR filtry), volná podlaha 100mm rastr,
  passages, volumes; 30 hran na reálném modelu, 38/38 testů.
- **2026-10-22** — **v5 F2.1**: `enrich` dopočítá validaci, když chybí (GUI export);
  tag + parent-role fallback (`Unnamed` → battery/energy), 46/46 rolí i bez jmen, 42/42 testů.
- **2026-10-23** — **vocab overlay + model stamp**; **geometry profil** v4
  (`vertices`/`edges`/`faces`, normály, objem, hash), legacy v3 export odstraněn.

> Historické konzolové skripty v `src/_archive/console/` obsahují známou korupci
> matic — nepoužívat.

* * *

## Licence

MIT — viz [LICENSE](LICENSE)

*Autor: Ondřej — [outpost2026](https://github.com/outpost2026) · Praha, 2026*
