# Projektový vocabulary overlay

Core slovník (`tools/cad2llm_rules.json` → `vocab`) zůstává společný pro všechny
projekty. Projektové termíny se přidávají **add-only overlay** souborem; core se
nikdy needituje kvůli jednomu projektu.

Dělení je věcné, ne projektové: **doménová** role, kterou umí pojmenovat IFC nebo
kterou používá víc staveb (`column`, `purlin`, `rafter`, `foundation`, `brace`,
`fastener`), patří do core. Overlay je pro **klientské** termíny jednoho projektu
(`loft_ladder`, `guardrail`, názvy firemních sestav).

## Verze 1.1 (2026-11-01)

Core byl rozšířen z 23 na 39 rolí. Důvod: zapisovací cesta (gate registry)
generovala role, které čtecí cesta (v5) neznala — 60 z 624 příkazů na korpusu
skončilo mimo slovník. Doplněny byly `column`, `purlin`, `rafter`, `truss`,
`foundation`, `brace`, `fastener`, `battens`, `junk_component`, `reference`,
`plan_reference`, `bed_frame`, `bed_worktop`, `seat_plank`, `seat_area`, `legs`,
`leg_gap`; dále vznikl deklarativní seznam `systems` a `tag_roles` pro česky
pojmenované tagy (`sloupy` → `column`, `zaklady` → `foundation`, …).

Overlay, který má v `extends_version` uvedeno `1.0`, po tomto bumpu **odmítne
merge** — je to záměr, ne regrese. Overlay se musí vědomě přenést na `1.1`
a zkontrolovat, zda jeho role nekolidují s novými core rolemi.

## Schéma `cad2llm/vocab_overlay/v1`

```json
{
  "schema": "cad2llm/vocab_overlay/v1",
  "project_id": "loft_demo",
  "name": "Loft demo",
  "version": "1.0+loft",
  "extends_version": "1.1",
  "roles": {
    "loft_ladder": {
      "system": "furniture",
      "zone": "sleep",
      "ifc": "IfcStair",
      "label_cs": "žebřík"
    },
    "guardrail": {
      "system": "envelope",
      "zone": "envelope",
      "ifc": "IfcRailing",
      "label_cs": "zábradlí"
    }
  },
  "tag_roles": {"zebrik": "loft_ladder", "madlo": "guardrail"},
  "zones": ["sleep"],
  "deprecated_roles": ["marker"]
}
```

Pravidla:

- `schema` a `project_id` jsou povinné; neznámé klíče jsou chyba (ochrana proti typům).
- `roles` mohou jen **přidávat**. Existující roli nelze redefinovat (jiný `ifc`,
  `zone`, `system`) — merge skončí chybou.
- `tag_roles` mohou jen přidávat; kolize aliasu na jinou roli je chyba.
  Lookup je case-insensitive (`ZEBRIK` → `loft_ladder`).
- `zones` se přidávají; role musí odkazovat na existující zónu.
- `deprecated_roles` roli neodebere (historické exporty zůstanou čitelné),
  jen ji `validate` označí `DEPRECATED_ROLE` (warn).
- `extends_version` musí odpovídat `vocab.version` core souboru, jinak merge selže.

## Použití

```powershell
python tools/cad2llm.py validate export.json --vocab loft.vocab.json
python tools/cad2llm.py summary  export.json --vocab loft.vocab.json
python tools/cad2llm_derive.py   export.json --vocab loft.vocab.json --out plan.json
```

Overlay ovlivní validaci, skeleton plánu (`derive`) a tím i role/IFC metadata.
`v5.enrich` zatím používá core slovník; overlay do sémantiky bude doplněn
společně s `ensure_tags`.

## Stamp projektu do modelu

Aby export poznal, jakým slovníkem byl vytvořen, zapiš do modelu čtyři atributy:
`project_id`, `vocab_version`, `vocab_base_version`, `vocab_hash`.

```powershell
python tools/cad2llm_stamp.py export.json --vocab loft.vocab.json --out stamp.json
python tools/cad2llm_bridge.py apply stamp.json
```

Plán je model-target (`{"kind": "model"}`), mění jen `cad2llm` dictionary.
Je idempotentní: pokud už jsou hodnoty stejné, nevygeneruje žádnou operaci.
Po dalším exportu je stamp v `project_metadata.metadata.cad2llm`.

## Hash

`vocab_hash` je prvních 16 hex znaků SHA-256 z kanonického JSONu efektivního
slovníku (`project_id`, `version`, `base_version`, `zones`, `roles`, `tag_roles`,
`deprecated_roles`). Změna aliasu nebo role tedy změní hash; pořadí klíčů ne.

## Doporučený workflow

1. Brief → kandidátní termíny.
2. Klasifikace: `add-alias` (většina), `add-role` (nový IFC koncept), `conflict`.
3. Schválení člověkem, uložení overlaye, `version` bump.
4. `validate --vocab` + `derive --vocab` + `stamp`.
5. Až po stabilizaci zvážit povýšení obecně použitelných rolí do core.
