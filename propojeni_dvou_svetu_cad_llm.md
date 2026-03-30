# Propojení dvou světů (CAD + LLM)

> **Deterministická pipeline: fyzické měření → SketchUp 2016 → COLLADA (.dae) → sémantický JSON → LLM analýza bez halucinací.**

* * *

## Problém - jak zkombinovat moderní LLM s 3D modely “na koleně”

LLM halucinuje při prostorové analýze CAD modelů — a to i když dostane „správný" formát.

Workflow vývoje prošel 4 fázemi:

1.  **Export 2D + narativní text** (obrázek z SketchUpu, jakýkoliv 2D image) → LLM radilo správně, ale na špatném místě. Bez přesných souřadnic jsou rady obecné a nepoužitelné.
    
2.  **Export .dae přímo do LLM** → LLM rétoricky spokojeno, chválí formát. Analýza přesto halucinuje rozměry a polohy. Stop stav.
    
3.  **AI je zde k ničemu, je to lepší celé propočítat ručně** - zamítnuto, automatizace je priorita, když to nejde přímo, půjde se na to od lesa
    
4.  **Pivot:** místo lepšího promptu — deterministický *preprocessing skript*.
    

> „LLM radí dobře, ale na špatném místě." — přesný popis problému před tímto řešením.

* * *

## Řešení

Vlastní konvertor (skript vyvinutý ve spolupráci/cross validací LLM nástrojů, zejména Claude a CLI Open Code) parsuje COLLADA XML, rekurzivně resolvuje 4×4 transformační matice, převádí palce → mm a generuje strukturovaný JSON.  
Talkže LLM dostane čísla v mm s bounding boxy, ne geometrii k interpretaci a halucinacím

```
Fyzické měření (kovový metr + papír)
    ↓
SketchUp 2016 — každý objekt jako pojmenovaný Component
    ↓
Export .dae (COLLADA)
    ↓
convert_dae_to_json_v3.py   ←── deterministický konvertor (lxml + numpy)
    ↓
mistnost_2_v4.json           ←── 30 nodes, bounding boxy v mm
    ↓
LLM analýza + iterace s uživatelem
    ↓
Nález: chybný osový systém X/Y/Z → korekce v SketchUp → re-export → v4
    ↓
Groq validace: žádné nekonzistence
```

* * *

## Reálný kontext — proč to vzniklo

Projekt vznikl jako součást **Outpost 2026** — off-grid modulárního bydlení vlastní výstavby v Praze (dřevěná konstrukce, solární systém 8S2P LiFePO4 630 Ah). Cílem je přesnější prototypování prostoru v součinnosti s LLM nástroji.

Aktuální scope: **Zone 2** — vývojový/obytný uzel, ~450 × 200 cm. Aspirační cíl: celý objekt Outpost (~45 m²).

* * *

## Vývoj konvertoru — jak vznikl v3

Skript vznikl v jednom dni přes tři LLM nástroje (google Gemini je odsunuto do auxiliární, "tvůrčí role")

| Krok | Nástroj | Role |
| --- | --- | --- |
| Průzkum možností + první handoff JSON | Deepseek | rychlý přehled formátu, proof of concept |
| v1, v2 | Claude Sonnet | funkční, ale vertex-level bounding box nedostatečný |
| **v3** | Open Code | v2 jako základ + stejný prompt → 1 iterace = finální verze |

Pivot k Open Code byl klíčový: existující kód jako kontext výrazně zkrátil iterační cyklus.

* * *

## Osová korekce — reálný bug, ne feature

Při LLM analýze `mistnost_2_v3.json` byly identifikovány nekonzistence. Hypotéza uživatele: osový systém X/Y/Z byl chybně nastaven od začátku modelování.

Postup:

1.  Uživatel vyslovil hypotézu na základě anomálií ve výstupu
2.  Groq navrhl metodiku korekce os v SketchUp
3.  Korekce provedena → nový export .dae → konverze přes v3 → `mistnost_2_v4.json`
4.  Groq validace: **žádné nekonzistence nalezeny**

Toto je hlavní iterační cyklus pipeline v praxi: JSON odhalí chybu modelu, model se opraví, JSON se znovu vygeneruje.

* * *

## Reálná data z `mistnost_2_v4.json`

Místnost: ~4 500 × 2 000 mm (Outpost Zone 2)

| Objekt | Pozice XYZ (mm) | Rozměr (mm) |
| --- | --- | --- |
| `bed_v1` | 2495, 0, 0 | 2000 × 970 × 180 |
| `table_main` | 0, 84, 0 | 675 × 745 × 665 |
| `stove_main` | 3930, 1480, 0 | 480 × 380 × 680 |
| `worktop_m2` | 1520, 1760, 745 | 2260 × 200 × 50 |
| `lfp_main_worktop` | 1620, 1760, 490 | 2087 × 240 × 35 |
| `gokw_lfp` | 1987, 1760, 0 | 525 × 240 × 220 |
| `HWP_3.2_kW_invertor` | 1720, 1860, 1075 | 270 × 100 × 350 |
| `lfp_cell` (×16) | x: 1700–2777, y: 1828, z: 525 | 70 × 172 × 205 každý |
| `window_south` | 4495, 0, 0 | – × 1235 × 1780 |
| `window_bed` | –, 2, 600 | 1295 × – × 1260 |
| `windows_table` | 285, 2, 545 | 1325 × – × 1300 |
| `Door` | 50, 2000, 0 | 1440 × 50 × 1430 |

* * *

## Co je v repozitáři

```
sketchup-llm-dae-pipeline/
├── README.md
├── LICENSE                           ← MIT
├── METODIKA_SketchUp_LLM.md          ← replikovatelný postup
├── scripts/
│   └── convert_dae_to_json_v3.py     ← konvertor (lxml + numpy)
└── examples/
    ├── mistnost_2_v4.dae             ← reálný vstup (SketchUp 2016)
    ├── mistnost_2_v4.json            ← reálný výstup (30 nodes, mm)
    └── REPORT_mistnost_2_v3.md       ← reálná LLM analýza výstupu
```

* * *

## Quickstart

```bash
git clone https://github.com/outpost2026/sketchup-llm-dae-pipeline
cd sketchup-llm-dae-pipeline
pip install lxml numpy

python scripts/convert_dae_to_json_v3.py examples/mistnost_2_v4.dae output.json
```

* * *

## Klíčová technická rozhodnutí

| Rozhodnutí | Alternativa (zamítnuta) | Důvod |
| --- | --- | --- |
| Vlastní COLLADA XML parser (`lxml`) | Přímý `.dae` do LLM | LLM halucinuje i na „správném" formátu bez preprocessing |
| Rekurzivní 4×4 matice (`numpy`) | Flat seznam pozic | Správné world-space souřadnice pro zanořené Components |
| Vertex-level bounding box | Pouze transformační matice | Skutečné rozměry objektu, ne jen pivot point |
| Převod palce → mm při parsování | Ponechat native jednotky | SketchUp interně v palcích; LLM pracuje s mm |
| Noise filter (`instance_*`, `skp_camera`) | Parsovat vše | Eliminuje exportní artefakty SketchUpu |
| SketchUp 2016 (lokální) | Cloud CAD | Outpost je off-grid; žádná závislost na připojení |

* * *

## Co tento projekt demonstruje

-   **Selhání jako datový bod** — dvě slepé uličky (2D export, raw DAE) jsou součástí dokumentace, ne skryté
-   **Multi-LLM workflow** — Deepseek pro průzkum, Sonnet pro iteraci, Open Code pro průlom, Groq pro validaci
-   **Architektonické rozhodnutí (lidský mozek)** — kdy přestat psát lepší prompt a postavit preprocessing vrstvu
-   **Iterační korekce** — osový bug byl nalezen analýzou JSON výstupu, ne code review

* * *

## Metodika

Viz [`METODIKA_SketchUp_LLM.md`](METODIKA_SketchUp_LLM.md) — kompletní postup od fyzického měření po validaci výstupu.

* * *

## Licence

MIT — viz [LICENSE](LICENSE)

* * *

*Autor: Ondřej — [outpost2026](https://github.com/outpost2026) · Praha, 2026*
