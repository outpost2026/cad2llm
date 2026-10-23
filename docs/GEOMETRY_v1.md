# Geometry profile — `cad2llm/geometry/v1`

**Datum:** 2026-10-23 | **Profil:** `export_profile: "geometry"` | **Jednotky:** mm | **Osy:** Z-up

SketchUp exporter má dva profily. `spatial` zůstává beze změny (bbox, rotace,
matice, tagy, metadata). `geometry` přidá ke každému uzlu jeho **přímou**
geometrii v world-space, aby LLM nemusel odvozovat tvary z bboxu.

```powershell
python tools\cad2llm_bridge.py export model_geometry.json --profile geometry
```

V extension menu: **Export detailed geometry JSON (v4+geometry)** a
**Export selected geometry (v4+geometry)**.

## Proč

Původní automatizační návrhy počítaly s tím, že Ruby exporter umí dodat
geometrii pro odvození tloušťky, orientace, overlaps, clearance a výkazů.
Realita byla pouze `bbox_mm`, takže:

- tloušťka stěny a orientace plochy se musely hádat z názvu nebo bboxu,
- překryvy byly jen objemy bboxů (falešné pozitivy u L/T tvarů),
- řez/HLR a výkazy neměly hrany ani plochy,
- LLM nemohlo rozlišit skutečný objem od obálky.

Geometry profil tato data dodává deterministicky a bez změny stávajícího
spatial exportu.

## Dokument

```json
{
  "schema_version": "4.0",
  "export_profile": "geometry",
  "project_metadata": {
    "capabilities": {
      "geometry": true,
      "geometry_schema": "cad2llm/geometry/v1",
      "geometry_coordinate_space": "world_mm"
    }
  },
  "nodes": [
    {
      "uid": "123@wall_01",
      "spatial": { "bbox_mm": { "min": {}, "max": {} } },
      "geometry": {
        "schema": "cad2llm/geometry/v1",
        "scope": "direct",
        "status": "ok",
        "coordinate_space": "world_mm",
        "counts": { "vertices": 8, "edges": 12, "faces": 6, "triangles": 12 },
        "vertices": [[0.0, 0.0, 0.0]],
        "edges": [[0, 1]],
        "faces": [
          {
            "id": "f0000",
            "outer": [0, 1, 2, 3],
            "holes": [],
            "normal": [0.0, 0.0, 1.0],
            "area_mm2": 240000.0,
            "material": "Brick"
          }
        ],
        "closed": true,
        "oriented": true,
        "volume_mm3": 57600000.0,
        "hash": "96af24f995b9308e"
      }
    }
  ]
}
```

## Sémantika

| Pole | Význam |
|---|---|
| `scope` | `direct` = jen plochy/hrany přímo v entitě, ne v dětech. Kontejner může mít prázdnou geometrii. |
| `status` | `ok`, `empty` nebo `error`. `error` je i v `validation.issues` s kódem `GEOMETRY_ERROR`. |
| `vertices` | World-space mm, deterministicky seřazené, 3 desetinná místa. |
| `edges` | Indexy do `vertices`, vždy `[menší, větší]`, unikátní, seřazené. |
| `faces[].outer` | Indexy v pořadí loopu; start rotován na nejmenší index. |
| `faces[].holes` | Vnitřní loopy (stejná pravidla), odečtené z `area_mm2`. |
| `faces[].normal` | Jednotkový normál z Newellovy metody, 6 desetinných míst. |
| `faces[].area_mm2` | Plocha v mm², díry odečtené. |
| `closed` | Každá hrana inciduje právě dvěma plochami. |
| `oriented` | Navíc mají sdílené hrany opačný směr (konzistentní winding). |
| `volume_mm3` | Jen pro `closed && oriented` a plochy bez děr; jinak chybí. |
| `hash` | SHA-256 prvních 16 hex znaků z kanonických `vertices`+`edges`+`faces`; vhodné pro change detection. |

## Deterministická pravidla

1. Souřadnice jsou world-space mm (`palce × 25.4`), stejně jako `bbox_mm`.
2. Vertex klíč je zaokrouhlený na 3 desetinná místa; pořadí je lexikografické.
3. Plochy se řadí podle `outer`/`holes`; `id` je `f0000`, `f0001`, ...
4. Hrany se deduplikují a řadí; směr se normalizuje na `[menší, větší]`.
5. Materiál je `display_name` z přiřazeného materiálu plochy, pokud existuje.
6. Opakovaný export stejného modelu musí dát stejný `hash`.

## Omezení

- Křivky nejsou samostatný typ: SketchUp je reprezentuje jako hrany. Oblouk je
  tedy polygonální posloupnost hran, ne `Arc` objekt.
- Nejsou exportované UV souřadnice, barvy ani textury; jen jméno materiálu.
- Geometrie se opakuje pro každou instanci komponenty (world-space), protože
  cílem je přímá konzumace LLM, ne komprimovaný mesh formát.
- Legacy export v3 už extension nevyrábí. Python `validate` umí staré v3
  dokumenty přečíst a převést v paměti, ale nové exporty jsou vždy v4.
