# Projektový vocabulary overlay

Core slovník (`tools/cad2llm_rules.json` → `vocab`) zůstává společný pro všechny
projekty. Projektové termíny se přidávají **add-only overlay** souborem; core se
nikdy needituje kvůli jednomu projektu.

## Schéma `cad2llm/vocab_overlay/v1`

```json
{
  "schema": "cad2llm/vocab_overlay/v1",
  "project_id": "loft_demo",
  "name": "Loft demo",
  "version": "1.0+loft",
  "extends_version": "1.0",
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
