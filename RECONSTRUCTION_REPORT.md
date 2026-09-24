# RECONSTRUCTION_REPORT — cad2llm Ruby pipeline

**Datum:** 2026-09-24
**Checkpoint baseline:** `08d4874` (clean tree před změnami)
**Zdroj pravdy:** `C:\Users\PC\Documents\Obsidian_03\test03\skritpy_local_context.md` (sekce `## Soubor: Skript_spatial_analysis\Ruby\...`)
**Zadání:** `docs/handoff_ruby.json` (Variant A – plný handoff)

---

## 1. Kontext

Repo `cad2llm` bylo dlouho nevyužíváno. Vyšší vývojová verze (Ruby Console ve SketchUp 2024 → LLM-ready JSON) nikdy nebyla pushnuta do git. Fyzické `.rb` v `Skripty_local\` byly smazány. Rekonstrukce slouží návratu k ETL pipeline pro revizi projektu **Outpost 2026** před zimou 2026.

---

## 2. Výsledek rekonstrukce

| Metrika | Hodnota |
|---|---|
| Extrakce z dumpu | **19/19 OK** |
| Nové soubory | 19 extrahovaných + 2 vytvořených (loader, main) + 1 `.rbz` |
| `git mv` (legacy) | 4 soubory |
| Celkem souborů v repu (excl. `.git`) | **29** |
| Validace JSON (examples + handoff 1–2) | **6/6 OK** |
| `handoff_v3.json` | **INVALID v originálu** (viz §5) |
| Security audit loader+main | **PASS** |
| Security audit legacy Python | **PASS** (bez Outpost/GPS/PII) |
| `.rbz` | 2276 B, entry: `cad2llm_exporter.rb` + `cad2llm_exporter/main.rb` |

---

## 3. Struktura (po restrukturalizaci)

```
cad2llm/
├── src/
│   ├── cad2llm_exporter.rb          ← loader (z handoff, v2.0.0)
│   ├── cad2llm_exporter/
│   │   ├── main.rb                  ← SYNTÉZA: public_v3 + anonymizace handoff
│   │   └── console/                 ← 7 historických .rb (archiv)
│   └── legacy_python/
│       └── convert_dae_to_json_v3.py
├── docs/                            ← 3 handoffy + 5 docs + 3 původní MD
├── examples/                        ← 4 JSON exporty (+ selected_export/)
├── releases/cad2llm_exporter.rbz
├── readme.md, LICENSE, .gitignore
└── RECONSTRUCTION_REPORT.md         ← tento soubor
```

### 3.1 Rekonstruované Ruby soubory (`src/cad2llm_exporter/console/`)

| Soubor | B | Úloha |
|---|---:|---|
| `sketchup2024_Cad2LLM_ruby_console.rb` | 3922 | základní traverse export |
| `..._public.rb` | 3920 | veřejná varianta |
| `..._public_v2.rb` | 4093 | sběr všech attribute dictionaries |
| `..._public_v3.rb` | 4487 | persistent_id, dynamický název, timestamp |
| `cad2llm_v3.rb` | 5734 | parent_id graph, filtr SU_*, transform_matrix, round(4) |
| `Json_export_components.rb` | 2894 | selection-only export, smart_name |
| `Add_metadata_sleep.rb` | 634 | zone/energy/thermal/safety atributy |

### 3.2 `main.rb` — jediný „syntetický“ soubor

- **Základ:** `sketchup2024_Cad2LLM_ruby_console_public_v3.rb`
- **Aplikováno dle handoff fáze 3:**
  1. GPS fallback `rescue 0.0` (již bylo ve v3)
  2. Default title `SketchUp_Model` (místo `untitled_model`/`Outpost_2026`)
  3. Dynamický název: `cad2llm_export.json` / `{title}_cad2llm.json`
  4. Ukládání: `model.path` → dirname, jinak `UI.savepanel`
  5. Bez hardcoded GPS/Outpost/`C:\Users`

---

## 4. Bezpečnostní audit (handoff security_constraints)

| Kontrola | loader+main | console/ (archiv) | examples/ | docs/handoff* |
|---|---|---|---|---|
| GPS `50.150…`/`14.423…` | PASS | PASS | **GPS přítomno** (reálné exporty outpost) | jen text specu |
| `Outpost_2026` | PASS | PASS | názvy souborů | text specu |
| `C:\Users\…` | PASS | **1 hit** `Json_export_components.rb:73` | — | — |

**Poznámky:**
- `examples/*.json` obsahují skutečné GPS projektu – **záměrně ponecháno** (výstupy ETL pro Outpost 2026).
- Hardcoded cesta v `console/Json_export_components.rb` je **historický originál** (archivní věrnost); publikovaný povrch (`loader`+`main`) je čistý.
- `docs/handoff_ruby.json` cituje GPS jako pokyn k anonymizaci – očekáváno.

---

## 5. Známé vady / omezení

| # | Vada | Jistota | Dopad | Doporučení |
|---|---|---|---|---|
| 1 | `handoff_v3.json` **není validní JSON** – v originálu schématické placeholdery (`"voltage": number`, `[x, y, z]`) | P>0.99 | strojový parse selže | ponechán věrně; případně `handoff_v3.schema.md` |
| 2 | `cad2llm_exporter.rbz` **rekonstruován** ze zdrojů, originální binární balíček nedostupný | P>0.99 | může lišit od dřívějšího rbz | přetestovat instalaci v SketchUp 2024 |
| 3 | `.skp`/`.skb` modely **nejsou** v dumpu (binární) | P>0.99 | mimo tento rep | leží v `Documents\Sketchup2016\`, `Documents\*.skp` |
| 4 | `sketchup2024_Cad2LLM_ruby_console.rb` končí artefaktem `s` na vlastním řádku po `CAD2LLM.export` | P>0.9 | konzolový historical only | ponecháno; neimportovat do extension |
| 5 | `main.rb` není 1:1 dump – syntéza dle spec | P>0.8 | chování může mírně lišit od produkčního konzolového běhu | ověřit na modelu místnost_2 / outpost |
| 6 | Ruby CLI v prostředí nedostupný – **syntax check proveden pouze vizuálně** | jistá | riziko drobné syntax chyby | `ruby -c main.rb` po instalaci / spustit v SketchUp Console |
| 7 | GPS v `examples/` = reálná lokalita | jistá | při public push zvážit | dle účelu repa (outpost = soukromý projekt) |

---

## 6. Související artefakty mimo git (Documents)

| Cesta | Stav |
|---|---|
| `Documents\sketchup_model_v3.json` | existuje (pergola_vojta_v3) |
| `Documents\lavicka_mhd_chabry_export_*.json` | existuje |
| `Documents\Sketchup2016\*.skp` | 19 modelů |
| `Documents\*.skp` | pergola ×3, lavicka |
| `Repozitar_Dev\Skripty_local\` | **smazáno** |

---

## 7. Rollback

```powershell
cd C:\Users\PC\Documents\Repozitar_Dev\_github\cad2llm
git reset --hard 08d4874   # pouze se souhlasem autora (destructive gate P84-P89)
```

---

## 8. Doporučený další krok (Outpost 2026 zima 2026)

1. Nainstalovat `releases/cad2llm_exporter.rbz` do SketchUp 2024 (Extension Manager).
2. Otevřít model Outpost → `CAD2LLM.export` z Ruby Console.
3. Ověřit JSON proti schématu `examples/sketchup_model_v3.json` (`parent_id`, `spatial_data_m`, `metadata`).
4. Navázat LLM asistenci: JSON jako context pro revizi dispozic před zimním obdobím.
5. Opsat `readme.md` dle handoff fáze 5 (není součástí této rekonstrukce – jen call to action).
