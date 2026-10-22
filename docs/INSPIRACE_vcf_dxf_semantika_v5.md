# Inspirace z `vcf_parser_b2b` a `dxf_integrace` pro CAD2LLM v5

**Datum:** 2026-10-22 | **Autor:** outpost2026 | **Účel:** přenést ověřené vzory z CAM2LLM/VCF pipeline do sémantické vrstvy CAD2LLM

## 0. Proč se tím inspirovat

`vcf_parser_b2b` (v20) a `dxf_integrace` (v2.3) řeší stejný problém jako CAD2LLM,
jen v jiné doméně: **z proprietárního binárního/DXF vstupu udělat deterministický
JSON, který nese nejen geometrii, ale i význam** (role vrstvy, nesting, riziko).
VCF pipeline to dotáhla dál než CAD2LLM — má sémantickou vrstvu s epistemickou
klasifikací pravidel, topologický graf, feature vektor a rizikové skóre.

Klíčové zjištění: **úspěch VCF pipeline nestál na parseru, ale na tom, že existoval
"answer key"** — fyzikální omezení výroby (nástroj musí řezat po dekoru, panel se
musí vejít do desky). CAD2LLM ekvivalent answer key je obytnost: průchodnost,
odstupy, konstrukční návaznosti. Bez klasifikovaného korpusu pravidel zůstane
"sémantická vrstva" jen dekorace.

## 1. Co přenést 1:1 (ověřené vzory)

| Vzor | Kde ve VCF/DXF | Přínos pro CAD2LLM |
|---|---|---|
| **Epistemická metadata pravidel** | `Knowledge_base/models.py`: `RuleClass` A/B/C/D, `ValidationStatus`, `confidence`, `source` | každé pravidlo (clearance, ergonomie) má důvod a jistotu; LLM neprezentuje hypotézu jako fakt |
| **`traceable_context` u každého nálezu** | `ValidationResult.traceable_context` | nález nese uid, hodnoty a prahové číslo → LLM může citovat, ne hádat |
| **Rizikové skóre** | `risk_score = 0.5×CRITICAL + 0.2×WARNING` | jedno číslo pro triage; dnes máme jen seznam issues |
| **Topologický graf** | `dxf/spatial.py::EntityGraphBuilder`: adjacency / containment / intersection / proximity + components, max_degree, cycles, diameter | z 46 bboxů udělá síť vztahů (co se dotýká, co je v čem, co je blízko) |
| **Containment strom (nejmenší rodič)** | `vcf_parser_v20.py:906-935`: point-in-polygon → nejmenší obsahující prvek → `parent_id`, `children_ids`, `topology_tree` | otvor→stěna, článek→baterie, entita→zóna; `parent_uid` už máme, chybí odvození |
| **Boolean analýza (shapely)** | `dxf/topology.py::BooleanAnalyzer`: union, solidity, num_holes, min feature width přes buffer −50…−0.5 mm | čistá podlaha, min. šířka průchodu (dnes ručně 790 mm), solidity místnosti |
| **Constraint ratio** | `ConstraintDetector`: parallel/perpendicular/orthogonal_ratio | detekce neortogonálního designu a odchylek od mřížky |
| **Repetiční grupy** | `vcf_parser_v20.py:342` `_group_elements`: klíč = (typ, počet bodů, zaokrouhlená délka, aspect ratio / rádius) | 16 LFP článků, 3 okna, 2 pozednice jako třídy instancí |
| **Density grid + symetrie** | `_compute_density_grid` (8×8), `_detect_symmetry` (12 bin histogram, práh 85 %) | obsazenost místnosti, symetrie dispozice, prázdná pole |
| **Klasifikace layoutu** | `_classify_layout`: grid / linear_along_x / linear_along_y / freeform / single_piece (std centroidů, 30mm kvantizace) | typologie dispozice bez LLM |
| **Detekce modulového formátu** | `_detect_panel_format`: ±20 mm tolerance na známé formáty | detekce 600mm modulové mřížky, atypický rozměr |
| **Index složitosti** | prahy `n<50 & len<15 m` → lineární / standardní / složitá / agregovaná | odhad, kolik kontextu má LLM dostat |
| **Feature vektor (`ml_features`)** | `dxf/ml_features.py` + `write_ml_vectors_csv` | plochý vektor pro prahová rozhodnutí a korelaci, **bez trénování modelu** |
| **Kalibrační profil** | `machine_profile.json`: konstanty + `validation_status` + `epistemic_confidence_index` | `user_profile.json`: ergonomie sólo stavitele (délka kroku, dosah, šířka ramen) + index jistoty |
| **Config s `_description` a `_note` u každého pravidla** | `dxf_tool_config.json` (ACI→nástroj, validation_status per barva) | `cad2llm_rules.json` rozšířit na stejný standard |
| **Per-vrstva karta** | `LayerCardBuilder` (barva → počty, délky, hustota, closed/open) | `tag_card` v SU kanálu (tag → počty, plochy, zóny) |
| **Graceful degradation** | `_HAS_SHAPELY` fallback na bbox | shapely volitelná závislost, JSON nesmí spadnout |
| **Golden master + determinism testy** | `tests/test_golden_master.py`, `test_determinism.py` | CAD2LLM má unit testy, chybí golden master na reálném exportu a 2× determinismus |

## 2. Co přenést koncepčně (a nedělat doslova)

**a) "Pseudo ML" = feature vektor + prahy, ne model.**
VCF/DXF nikdy netrénovaly síť. Postavily plochý slovník příznaků a deterministické
prahy. Pro sólo stavbu je to správně: každé rozhodnutí je dohledatelné a
debugovatelné. CAD2LLM v5 má udělat totéž — `features` blok, ne klasifikátor.

**b) Semantické zónování ze sousednosti.**
`SemanticAnalyzer._semantic_zone_split` seskupuje entity do zón podle blízkosti
a deduplikuje je. V CAD2LLM to nahradí parsování `_ZONE` z názvů (křehké, v DAE
ztrátové) deterministickým clusteringem + volitelným override z tagů.

**c) Detekce "intentu" jako pojmenovaný příznak.**
`_detect_mounting_flap` je vzor: z geometrie se odvodí funkční prvek a vydá se
jako boolean + kontext. CAD2LLM ekvivalenty: `has_door_leaf`, `has_flue`,
`has_service_access`, `has_sleeping_zone`, `is_wet_zone`.

**d) Narrative summary je šablona, ne LLM.**
`_narrative_summary` skládá věty z čísel deterministicky. LLM má dostat hotový
popis a dělat nad ním úsudek — ne přepisovat data do prózy (tam vznikají
halucinace).

**e) RAG query builder.**
`RagQueryBuilder` generuje dotazy z dat (zóna → „co potřebuji vědět"). Pro
CAD2LLM: z `role`/`zone` sestavit dotaz do KB („odstup kamna–hořlavina"), aniž
by ho LLM vymýšlel.

## 3. Anti-vzory, které nekopírovat

| Problém | Kde | Pro CAD2LLM |
|---|---|---|
| God class 2 800 řádků | `vcf_parser_v20.py` | držet `core` (parse) / `semantics` / `topology` / `features` oddělené jako v `dxf/src/dxf/` |
| `except: pass` (~15×) | VCF parser | chyby do `validation.issues`, ne do ticha |
| Nevalidní handoff JSON | `docs/handoffs/RUIDA_VCF_PARSER_HANDOFF_V16.json` — **padá na `json.load`** (ř. 247) | schéma dokumentace musí projít parserem; přidat do CI |
| Duplicita v20 vs v18.3 | 80 % kódu | jedna implementace + legacy režim, ne dvě větve |
| Chybí schéma výstupu | `parsed_data` (40+ klíčů) bez definice | už máme `SCHEMA_v4.md`, držet a verzovat |

## 4. Návrh: CAD2LLM v5 jako `semantics` vrstva

Rozšíření JSONu o tři bloky (zpětně kompatibilní, `schema_version: "5.0"`):

```json
{
  "schema_version": "5.0",
  "nodes": [ ... ],
  "topology": {
    "edges": [
      {"a": "uid1", "b": "uid2", "type": "contact|contained_in|intersects|near",
       "distance_mm": 0.0, "evidence": "bbox|polygon"}
    ],
    "components": 3,
    "max_degree": 9,
    "cycles": 4,
    "diameter": 12,
    "containment_roots": ["uid..."]
  },
  "features": {
    "entity_count": 46, "closed_volume_count": 44,
    "usable_floor_mm2": null, "solidity": null,
    "min_passage_width_mm": null, "num_holes": null,
    "orthogonal_ratio": null, "repetition_classes": 5,
    "max_repetition": 16, "density_grid": [[null]],
    "symmetry": {"vertical": null, "horizontal": null},
    "layout": null, "module_grid": null,
    "complexity_index": null, "risk_score": 0.0
  },
  "semantics": {
    "zones": [{"id": "Z_sleep", "role": "sleep", "entities": ["uid..."], "area_mm2": 3.1e6, "source": "cluster|tag|name"}],
    "roles": {"uid": {"role": "stove", "class": "CLASS_B", "confidence": 0.9}}
  }
}
```

`validation.issues` doplnit o `rule_class`, `confidence`, `traceable_context`.

## 5. Fázový plán (EROI sestupně)

| Fáze | Obsah | Odhad | Přínos |
|---|---|---|---|
| **P0** | `risk_score`, `rule_class`+`confidence`+`traceable_context`, golden master + determinism test | 2–3 h | okamžitá čitelnost pro LLM, regresní pojistka |
| **P1** | `topology` blok (adjacency/containment/contact z bboxů) + `min_passage_width` přes shapely buffer | 1 den | **největší skok**: vztahy místo seznamu |
| **P2** | `features`: repetice, layout, density grid, symetrie, modulová mřížka, solidity | 1 den | typologie a obsazenost bez LLM |
| **P3** | `semantics.zones` + `roles` (clustering + override z tagů) | 1–1,5 dne | nahrazení parsování názvů deterministikou |
| **P4** | `RagQueryBuilder` + šablonový `narrative` + export do CSV (feature vektor) | 0,5 dne | kontext pro LLM s citacemi |

Shapely je volitelná závislost (`_HAS_SHAPELY`), fallback na AABB — stejně jako v DXF.

## 6. Rozhodovací poznámka k názvu

Původní název `CAM2LLM` vznikl z domény (CAM→LLM), ne z formátu. Analogicky
hodnota v5 není v "CAD" (parsování), ale v *semantice prostoru*. Až bude vrstva
hotová, je fér ji pojmenovat podle toho, co realmente dělá — ne `cad2llm_v5`,
ale samostatný modul (`semantics`), s CAD2LLM jako vstupní branou.

## 7. Ověřené reference (cesty v repech)

- `vcf_parser_b2b/src/Knowledge_base/{models,engine,rules}.json|py` — epistemická pravidla
- `vcf_parser_b2b/src/vcf_parser_v20.py:342,386,402,426,465,552,883-935` — grupy, formát, hustota, symetrie, layout, containment
- `dxf_integrace/src/dxf/topology.py` — boolean analýza, constrainty, sanitizace
- `dxf_integrace/src/dxf/spatial.py:286` — `EntityGraphBuilder`
- `dxf_integrace/src/dxf/ml_features.py` — feature vektor
- `dxf_integrace/src/dxf/semantic.py:501,631` — RAG builder, layer card
- `dxf_integrace/src/dxf_tool_config.json` — ACI→nástroj s `validation_status`
- `vcf_parser_b2b/src/machine_profile.json` — kalibrace + `epistemic_confidence_index`
