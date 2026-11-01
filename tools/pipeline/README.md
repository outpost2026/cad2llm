# gate - deterministicka brana nad exporty

`gate.py` cte export z extension (schema v4) a vraci tri soubory:

| soubor | kontrakt | co to je |
|---|---|---|
| `<model>.gate.json` | `cad2llm/gate/v1` (interni) | uplny vysledek kontrol, navrhy roli, verdikt |
| `<model>.patch.json` | `cad2llm/semantic-patch/v1` | co se ma zapsat do modelu (jen pole, ktera exporter cte) |
| `<model>.review.json` | `cad2llm/semantic-review/v1` | co musi rozhodnout clovek (rozhodnuti = soubor, ne chat) |

Bez CLI promptu, bez interakce:

```powershell
python -X utf8 tools/pipeline/gate.py --dir <adresar_s_exporty> --out reports
python -X utf8 tools/pipeline/gate.py export.json                    # jen na stdout
python -X utf8 tools/pipeline/gate.py --dir exporty --out reports --review reports/x.review.json
python -X utf8 tools/pipeline/gate.py --selftest                     # bez modelu, 17 checku
```

Vystupni kody: `0` = cisto, `1` = varovani, `2` = blokujici nalezy nebo chyba.

## Zdroj pravdy

Tato slozka je **jediny zdroj pravdy** pro branu. Kopie
`C:\Users\PC\Documents\SU2024\cad2llm_pipeline\` je historicky artefakt:
nesmi se z ni spoustet ani z ni brat `registry.json`, protoze by se slovnik
mohl zmenit bez commitu. Kdyz je potreba stara cesta, ma byt forwarder
(nebo smazana), ne druha verze.

## Dve vrstvy slovniku

| vrstva | soubor | kdo ji pouziva |
|---|---|---|
| role/system/zona/IFC | `tools/cad2llm_rules.json` (`vocab`) | gate, v5, derive, validate, patch |
| projektove terminy | overlay (`docs/VOCAB_OVERLAY.md`) | jen konkretni projekt |

`registry.json` je **mapovani model -> role** (definice, tagy, metadata typy,
junk, reference). Neni to slovnik roli: smi obsahovat jen role, systemy a zony,
ktere jsou deklarovane v `cad2llm_rules.json`. To hlida:

- `--selftest` ("slovnik: role registru jsou ve slovniku"),
- `Registry.conformance` pri kazdem behu (`SEM_VOCAB_ROLE_DRIFT`,
  `SEM_VOCAB_SYSTEM_DRIFT`, `SEM_VOCAB_ZONE_DRIFT`, `SEM_IFC_MALFORMED`
  jsou blokujici; `SEM_IFC_UNDECLARED` je info),
- CI (`.github/workflows/ci.yml`).

## Co gate zapisuje

Jen pole, ktera exporter vrati zpet a nekdo je cte:

| pole | atribut | cte |
|---|---|---|
| role | `cad2llm.role` | v5, validate, gate |
| system | `cad2llm.system` | gate, IFC export (planovano) |
| zona | `cad2llm.zone` | v5 (`semantics.zone`) |
| IFC trida | `ifc.type` | derive, v5, IFC export (planovano) |
| jmeno | instance name | vse |

Zona se odvozuje z `vocab.role_info(role)["zone"]`, pokud entita zadnou nema;
nikdy se nezapisuje hodnota `unknown` (ta nic nerika). `ifc.schema` a
`ifc.source` gate nezapisuje - patri do `cad2llm_derive.py`, viz
`docs/SCHEMA_v4.md` §3.1.

## Lidska kontrola

1. gate zapise `<model>.review.json` s `decision: null`
2. clovek doplni `"decision": "approve" | "reject" | "value"` (u `value` jeste
   `field` a `value`)
3. gate se spusti znovu s `--review` -> schvalene polozky prejdou do `patch.json`
4. `tools/cad2llm_patch.py` prelozi patch na annotate plan, bridge zapise do `.skp`
