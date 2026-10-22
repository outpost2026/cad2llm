# CAD2LLM v5 — design (návrh, neimplementováno)

Datum: 2026-10-22 | Autor: outpost2026 | Účel: rozhodovací podklad pro implementaci v5

Stav: **návrhové artefakty**. Nic z tohoto dokumentu není v kódu. Rozhodnutí o implementaci
je na uživateli. Machine-readable protějšky:

- `docs/schema/cad2llm_v5.schema.json` — dokument v5
- `docs/schema/cad2llm_rules_v2.schema.json` — epistemická pravidlová KB
- `docs/schema/cad2llm_ops_v1.schema.json` — round-trip patch
- `docs/schema/example_mistnost_2_v5_fragment.json` — reálný výřez (11 uzlů z 46)
- `docs/schema/example_rules_v2.json` — 6 pravidel + 2 suppressions
- `docs/schema/example_ops_v1.json` — ukázkový patch

---

## 1. Cíl

v4 odpovídá na otázku **„co je v modelu a jak je to velké"**. v5 má odpovědět na otázku
**„co to znamená, jak to spolu souvisí a co je špatně"** — a to deterministicky, bez LLM
uvnitř pipeline.

Tři konkrétní cíle:

1. **Sémantika**: každý uzel má roli, zónu, intent a confidence s dohledatelnou evidencí.
2. **Vztahy**: contact / adjacency / opening_in / proximity / containment s mezerami v mm.
3. **Brief**: ~600–900 BMarkdownu, který LLM přečte místo 500 kB JSONu, plus strojová
   `facts` a `top_findings` pro levné filtrování.

Mimo rozsah v5: GUI, 3D vizualizace, ML predikce, automatické opravy modelu.

## 2. Co přenášíme z VCF / CAM2LLM

| Princip | U nás |
|---|---|
| Pravidlo má třídu A–D, status, confidence, source, support_n | `rules.v2.metadata` |
| Hypotéza není fakt; rejected se nevrací do validace, ale zůstává v KB | `validation_status` |
| SNR: false positive stojí víc než mlčení | max 3 varování/objekt, INFO nezobrazovat |
| Konstrukční překryvy nejsou kolize | `suppressions` |
| Layer/group = statistická jednotka | `zone` + `relations` |
| Deterministický výstup, golden masters, fail-safe | sekce 11 |
| Delta log místo plného stavu v handoffu | `brief` + `ops.expectations` |
| Triangulace: replikace + správné chování + mechanistické vysvětlení | `evidence[]` u uzlu i pravidla |

NEPŘENÁŠÍME: binární probe, Streamlit, průměrování diskrétních hodnot, god class,
`except: pass`, always-on warnings, ML nad malým N.

## 3. Architektura (vrstvy, jednosměrný tok)

```
SketchUp (GUI) ──export──▶ v4 JSON (metrická pravda)
                              │
                     facts layer (deterministické)
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
        semantics        relations         metrics
              └───────────────┼────────────────┘
                              ▼
                     validation (rules v2)
                              ▼
                        brief (komprese)
                              ▼
                    v5 JSON  ──▶ LLM konzument
                              │
                              ▼
                    ops patch ──▶ SketchUp (round-trip)
```

Každá vrstva je čistá funkce vstupu (bez náhody, bez času v rozhodování). Čas se
zapisuje jen do `checked_at`, `export_date`.

## 4. Dokument v5

`schema_version: "5.0"`. Sekce:

| Sekce | Povinná | Význam |
|---|---|---|
| `project_metadata` | ano | jednotky, up_axis, capabilities, model_hash, excerpt |
| `nodes` | ano | v4 pole + `semantics`, `metadata` |
| `relations` | ne | hrany mezi uzly |
| `metrics` | ne | souhrny nad celkem i výřezem |
| `validation` | ne | issues + suppressions s dohledatelným kontextem |
| `brief` | ne | Markdown + facts + top_findings |
| `ops` | ne | jen u round-trip odpovědi |

Invarianty:

- `uid` unikátní, `parent_uid` musí existovat nebo být `null`.
- `spatial.bbox_mm` respektuje `size_mm` (max − min = size, tolerance 1 mm).
- `metrics.basis` říká, z čeho jsou čísla: `full` / `excerpt` / `declared`.
  Výřez NIKDY nesmí tvrdit, že je plný model.
- `validation.issues[].traceable_context.uids` musí být podmnožina `nodes`.
- `brief.markdown` ≤ 4000 znaků, `top_findings` ≤ 5.

`capabilities` je explicitní seznam toho, co kanál umí. DAE: `tags=false`,
`metadata_dictionaries=false`, `persistent_ids=false`, `gps=false`. Konzument tak
nevěří tomu, co v datech chybí, ale je to vlastnost kanálu.

### Příklad (výřez z reálných dat)

```json
{
  "uid": "ID27",
  "name": "Kamna",
  "spatial": { "bbox_mm": { "min": { "x": 4045.0, "y": 1517.6, "z": 0.0 },
                            "max": { "x": 4476.4, "y": 1829.6, "z": 680.0 } } },
  "semantics": {
    "role": "stove", "zone": "sleep", "intent": "DESIGN_DECISION", "confidence": 0.93,
    "evidence": [{ "rule_id": "R_NAME_ROLE_STOVE", "rule_class": "CLASS_B",
                   "support_n": 1, "detail": "name prefix Kamna + bbox 431x312x680 mm" }]
  }
}
```

## 5. Sémantika

- `role`: uzavřený enum (bed, stove, worktop, storage, window, door, wall, insulation,
  table, void, …). Neznámé = `unknown`, nikdy volný text.
- `zone`: funkční zóna (sleep, work, dining, entry, envelope, energy, …). Zóna se
  odvozuje z názvů/pozice, ne z lidského úsudku.
- `intent`: `ARTIFACT` (marker, pomocná geometrie), `CONSTRUCTION` (sendvič, rám,
  izolace), `DESIGN_DECISION` (nábytek, technologie), `DEFECT` (něco chybí/nesedí),
  `UNKNOWN`.
- `confidence` ∈ ⟨0,1⟩; `evidence[]` je povinná pro každou roli (rule_id + class +
  support_n + detal). Tím je sémantika auditovatelná — stejný aparát jako VCF.

Intent je to, co drží SNR pod kontrolou: `CONSTRUCTION` a `ARTIFACT` nikdy
negenerují WARNING.

## 6. Relations

Deterministická pravidla, žádné hádání:

| kind | vzniká když | pole |
|---|---|---|
| `contact` | gap = 0 mm mezi bboxy (dotyk) | gap_mm |
| `adjacency` | gap 0 < g ≤ 250 mm podél jedné osy | gap_mm |
| `opening_in` | tenká deska (≤ 50 mm) leží v rovině obálky | overlap |
| `proximity` | g ≤ 1000 mm a existuje pravidlo, které dvojici řeší | gap_mm, min_mm |
| `containment` | bbox A je uvnitř bbox B (tolerance 1 mm) | — |

`proximity` se negeneruje „pro všechno" — jen pro dvojice, které mají pravidlo
(např. `R_CLEARANCE_STOVE_BED`). To je přímý důsledek SNR lekce: hrana bez pravidla
je šum.

`relations[].intent` a `confidence` stejně jako u uzlů; `overlap_mm3` jen u
`contact`, kde je potřeba odlišit dotyk od průniku.

## 7. Metrics

- `floor_area_m2`, `clear_height_mm`, `envelope_mm` — z obálky, ne z podlahy
  místnosti (v4 nese `floor_area_m2: 9.0` z deklarovaného obdélníku).
- `z_table` — seřazené intervaly z; slouží k detekci kolizí ve výšce a k výpočtu
  světlé výšky.
- `free_space` — rastr 100 mm nad půdorysem, buňka je obsazená, pokud ji protíná
  bbox s `intent != ARTIFACT`. Výstup: `cells_total`, `cells_free`, `free_ratio`,
  `passages[]` (nejužší místa mezi překážkami). Rastr je deterministický,
  rozlišení je součást konfigurace.
- `clearances[]` — jen dvojice s pravidlem; `ok` je předpočítané, aby konzument
  nemusel počítat.
- `energy` — z attribute dictionaries; v DAE kanálu `null` + `notes`.

`basis` chrání před falešnou přesností: výřez 11 uzlů nemá vydávat `free_ratio`
za celý model. Pokud se počítá nad výřezem, je to v `basis` a v `brief.facts`.

## 8. Rules KB v2

Šest polí, která dělají z pravidel znalostní bázi, ne konfiguraci:

- `rule_class`: CLASS_A fyzikální invariant / CLASS_B empirické pravidlo s malým
  N / CLASS_C heuristika z pozorování / CLASS_D hypotéza.
- `validation_status`: `accepted` (běží), `conditional` (běží, ale nízká jistota),
  `rejected` (neběží, zůstává v KB jako poučení), `unknown`.
- `confidence`, `source`, `support_n`: bez těchto tří se pravidlo nedá revidovat.
- `supersedes`: nová verze pravidla nevymaže starou, jen ji označí.
- `suppressions`: pravidla typu „tenhle kód pro tenhle role-set nikdy
  nereportuj" — s důvodem a volitelnou expirací.
- `on_violation.intent`: DEFECT vs UNKNOWN rozhoduje, zda se má člověk vůbec obtěžovat.

Příklad rejected pravidla v `example_rules_v2.json`: `R_CLEARANCE_ALL_WALLS`
(0 support, 12 false positives) — zůstává v KB, ale neběží. To je přesně VCF
disciplína: zamítnutá hypotéza je součást znalosti.

## 9. llm_brief

Formát: Markdown, ≤ 4000 znaků, sekce pevně dané (rozměry, kanál, layout,
passages, nálezy, suppressed, chybějící data). Pravidla:

- žádné surové seznamy uzlů — jen agregace a nálezy;
- čísla vždy s jednotkou;
- každý nález odkazuje na `uid`;
- `top_findings` max 5, jen WARNING/CRITICAL; INFO jde do `markdown`, ne do pole;
- `facts` je strojová verze briefu (dimensions, floor_area, narrowest_passage,
  nodes, channel, missing_capabilities).

Brief je **komprese**, ne druhý dokument. Když se informace nevejde, vypadne
detail, ne nález.

## 10. ops (round-trip)

- `base_model_hash` — patch platí jen na model, ze kterého vznikl; jinak odmítnout.
- `dry_run` default `true`; zapisovat se smí jen s explicitním `false`.
- Podporované operace: `move`, `resize`, `add`, `remove`, `set_metadata`, `set_tag`.
- `expectations` — co má patch dokázat: `bbox_unchanged`, `issue_resolved`,
  `no_new_issues`, `gap_at_least`, `gap_at_most`. Po aplikaci se znovu spustí
  validace a porovná se s očekáváním.

Tohle je jediná cesta, jak se LLM dostane zpět do modelu — přes patch s
ověřitelným výsledkem, ne přímým zápisem.

## 11. Deterministické zpracování a golden masters

- Pořadí uzlů: `uid` numericky, jinak lexikograficky; pořadí issues: severity
  (CRITICAL > WARNING > INFO), pak kód, pak uid.
- Normalizace: `-0.0` → `0.0`, zaokrouhlení na 3 desetinná místa, stabilní JSON
  (`ensure_ascii=false`, `indent=2`, newline na konci).
- `model_hash` = SHA-256 kanonického JSONu bez `project_metadata.export_date`
  a bez `validation.checked_at`.
- Golden master: každý fixtures model má uložený očekávaný výstup; test selže při
  jakékoli změně, která není vysvětlená v commitu.
- Fail-safe: neznámá geometrie → `role=unknown`, `intent=UNKNOWN`, nikdy pád.

## 12. SNR politika (konkrétní čísla z VCF)

| Problém | Před | Po | Mechanismus |
|---|---|---|---|
| EDGE_MERGE_MISSING | 69 | 0 | oprava geometrie, ne pravidla |
| UNCLOSED_LOOP | 32 | 0 | oprava geometrie |
| UNCONNECTED_INTERNAL_DECOR | 236 | 6 | kd-tree + point-to-segment |
| ORPHAN_ELEMENT | 1028 | 10 | bbox join 10 mm |

Pravidla pro v5:

1. Max 3 varování na objekt; zbytek se agreguje (`… a 7 dalších`).
2. INFO se nezobrazuje v briefu, jen v `validation.issues`.
3. Suppression musí mít `reason`; bez důvodu se neakceptuje.
4. Když pravidlo generuje > 20 % false positives na fixtures, jde do `rejected`.
5. `confidence < 0.5` = pravidlo nesmí být `accepted`.

## 13. Migrace v4 → v5

Aditivní. v4 dokument je platný v5 dokument s prázdnými `relations`, `metrics`,
`validation`, `brief` a `semantics.role = unknown`. Postup:

1. v5 exporter zapíše v4 pole beze změny (bit-identická čísla).
2. Přidá `semantics` (deterministické klasifikátory jmen + geometrie).
3. Přidá `relations` a `metrics`.
4. Validace v2 nahradí v1 pravidla; kódy zůstávají, mění se jen metadata.
5. `brief` se generuje až po validaci.

Zpětná kompatibilita: konzument v4 musí v5 dokument přečíst (neznámá pole
ignoruje), konzument v5 nesmí tvrdit, že v4 dokument je v5.

## 14. Roadmapa

| Fáze | Obsah | Acceptance |
|---|---|---|
| F0 | schémata + příklady (tento krok) | JSONy validují, dokumenty existují |
| F1 | facts + semantics + brief v Pythonu | golden master na mistnost_2, 0 false positives |
| F2 | relations + metrics (free_space, z_table) | deterministický výstup, test idempotence |
| F3 | rules v2 + suppressions | SNR: ≤ 3 varování/objekt, rejected pravidla v KB |
| F4 | ops + round-trip do SketchUp | patch s expectations projde, model_hash sedí |

Každá fáze je samostatně užitečná; F1 už dává LLM brief bez zbytku.

## 15. Otevřená rozhodnutí (pro uživatele)

1. **free_space rastr**: 100 mm (rychlé, hrubé) vs. 50 mm (přesnější, 4× buňky)?
2. **Sémantika**: čistě deterministické klasifikátory, nebo povolit ruční override
   v modelu (attribute dictionary `cad2llm.role`)?
3. **brief jazyk**: čeština (pro tebe) vs. angličtina (pro LLM konzistenci)?
4. **ops**: má být součástí v5 dokumentu, nebo samostatný soubor s vlastní verzí?
5. **scope F1**: stačí Python nad v4 JSON, nebo chceš rovnou Ruby exporter
   s rozšířenou sémantikou?
