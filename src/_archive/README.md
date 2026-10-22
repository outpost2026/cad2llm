# _archive — historické skripty (neaktivní)

Tyto soubory jsou **archiv**, ne produkční pipeline. Byly používány ručním
vkládáním do Ruby Console ve SketchUp 2024 (období 2026-04).

| Soubor | Poznámka |
|---|---|
| `console/cad2llm_v3.rb` | **Obsahuje korupci dat**: gsub náhrada `'…\10, \11 … \16'` — Ruby nezná `\10+`, vzniká `\1` + `"0"`, takže každá `transform_matrix` v exportu je poškozená. Nedoporučeno používat. |
| `console/sketchup2024_Cad2LLM_ruby_console_public_v2.rb` | bez `parent_id`, bez filtru `SU_*` |
| `console/sketchup2024_Cad2LLM_ruby_console*.rb` | starší generace (2026-04-16) |
| `console/Json_export_components.rb` | export selekce, hardcoded cesta mimo repo, jednotky v palcích |
| `console/Add_metadata_sleep.rb` | vzor zápisu attribute dictionaries (energy/thermal/zone/safety) — stále použitelné jako inspirace |

**Náhrada:** `src/cad2llm_exporter/` (extension, schema v4) + `tools/cad2llm.py` (validace, CLI).

Poznámka: `RECONSTRUCTION_REPORT.md` (2026-09-24) odkazuje na původní cestu
`src/cad2llm_exporter/console/` — je to historický dokument, cesty v něm už neplatí.
