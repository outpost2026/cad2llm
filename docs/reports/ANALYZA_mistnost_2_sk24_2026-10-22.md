# ANALYZA — mistnost_2_sk24 (deterministický JSON, schema v4)

**Datum:** 2026-10-22 | **Autor:** outpost2026 | **Účel:** ověření, že LLM dokáže analyzovat model pouze z JSON derivátu

## 0. Identifikace zdroje

| | |
|---|---|
| Zdrojový model (primární) | `mistnost_2_sk24.skp` |
| Umístění | `C:\Users\PC\Documents\Sketchup2016\` (SketchUp 2024 projekt) |
| Export, ze kterého analýza vznikla | `mistnost_2_sk24.dae` (166 775 B, 2026-04-15 07:38:57 Z) |
| Kanál | `dae` — **bez GUI**, čistě CLI |
| Nástroj | `tools/cad2llm.py convert` (schema 4.0) |
| Výstup | `mistnost_2_sk24_v4.json` — 46 uzlů, 46 s bbox, 0 errors, 0 warnings |
| Ověření proti Ruby exportu | `examples/outpost_cad2llm.json` (46 uzlů, kanál SketchUp) |

**Co v datech není:** tagy, attribute dictionaries (`energy`/`zone`/`safety`/`thermal`),
`persistent_id`, GPS. DAE tyto informace neobsahuje — `project_metadata.capabilities`
je hlásí jako `false`. Analýza níže je proto **geometrická a topologická**, nikoli
energetická; závěry o zónách jsou odvozené z názvů entit, ne z metadat.

## 1. Geometrie obálky

| Prvek | Rozsah (mm) | Tloušťka |
|---|---|---|
| Podlaha (finální) | z −20 … 0 | 20 |
| Podlaha — izolace ISOVER DOMO 10 cm | z −120 … −20 | 100 |
| Stěny (jádro) | z 0 … 2090 | 24 |
| Izolace stěn | 70 mm na vnější straně jádra | 70 |
| Strop — guttafol + textil | z 1990 … 2091 | 101 |
| Strop — izolace ISOVER DOMO 10 cm | z 1986 … 2196 | 210 |

- **Interiér:** 4 500 × 2 000 mm, světlá výška **1 990 mm** (podlaha 0 → podhled 1990).
- **Obálka včetně izolace:** 4 688 × 2 176 mm; celková výška 2 316 mm (−120 … 2196).
- Osy: `X` = sever (−) → jih (+), `Y` = západ (−) → východ (+), `Z` = výška.
- Terminologie v názvech entit odpovídá skutečnosti: `jižní` stěna leží na x ≈ +4 500,
  `severní` na x ≈ −50, `východní` na y ≈ +2 050, `západní` na y ≈ −50.

**Nález:** obálka je souvislá, izolace sedí na jádru stěn bez mezer; trámy
(`truss_překlad_pozednice` 80 mm) leží **uvnitř** stropní izolace (z 1910–2075),
což je z hlediska tepelné obálky očekávané, ale znamená to tepelný most
přes pozednici na obou podélných stranách.

## 2. Funkční zóny (dle názvů s prefixem `_ZONE`)

| Zóna | Entity | Pozice |
|---|---|---|
| spanek (`_zone_bed`) | `Bed_01_mattres_with_light_frame`, `Kamna_Priti_mini` | jih, x 2 495–4 495 |
| prace/strava (`_zone_dinning_dump`) | `main_table_rectangel`, `Worktop_01` | sever + východ |
| energie (`_zone_…energy…`) | `LFP_battery_DIY_630_Ah`, `Energy_source_invertor_MPPT` | východní stěna, y 1 760–2 000 |
| vstup (`_zone_entry`) | `void_entry_point` (1 650 mm), `wall_inner_zone_entry` | severovýchod, x 45–1 695 |

Dispozice je **lineární**: vstup na severu → pracovní stůl (x 0–673) → technický
pult s baterií a měničem (x 1 520–3 780, hloubka 200–240 mm) → postel (x 2 495–4 495)
→ kamna v jihovýchodním rohu. Volná průchozí šířka mezi postelí (y ≤ 970)
a pultem (y ≥ 1 760) je **790 mm** po celé délce — vyhovuje, ale bez rezervy
pro dvě osoby.

## 3. Vertikální skladba (z-tabulka, výběr)

| Entita | z_min | z_max | výška |
|---|---:|---:|---:|
| Podlaha + izolace | −120 | 0 | 120 |
| Postel (matrace + rám) | 0 | 180 | 180 |
| Baterie 24 V GOKWH 100 Ah | 0 | 220 | 220 |
| Stůl `main_table` | 0 | 665 | 665 |
| Kamna Prity mini | 0 | 680 | 680 |
| Pracovní deska `Worktop_01` | 745 | 795 | 50 |
| Bateriový základ `LFP_main_base` | 490 | 525 | 35 |
| LFP články 16× 314 Ah | 525 | 730 | 205 |
| Měnič/MPPT | 1 075 | 1 425 | 350 |
| Trámy východ | 1 994 | 2 075 | 80 |
| Podhled | 1 990 | 2 091 | 101 |
| Stropní izolace | 1 986 | 2 196 | 210 |

**Nález:** měnič je ve výšce 1 075–1 425 mm nad deskou → kabelové trasy k baterii
(525–730 mm) vedou v pásmu 730–1 075 mm; v modelu nejsou kabely ani nosné lišty.

## 4. Energie a bezpečnost

- **Akumulace:** 16× LFP 314 Ah / 3,2 V zapojené 8S2P do 24 V, 630 Ah ≈ **15,1 kWh**
  (jmenovitě), na `LFP_main_base` (35 mm) ve výšce 490–730 mm; zvlášť 1× GOKWH 100 Ah
  na podlaze (0–220 mm).
- **Zdroj:** `Energy_source_main_power_invertor_MPPT_DC-AC, max load 3,2 kW`.
- **Kamna vs. postel:** mezera **642 mm** ≥ 500 mm (pravidlo Outpost) → **OK**.
  Kamna jsou 80 mm od jižní stěny a 490 mm od okna — u zdi jde o standardní
  odstup s tepelnou izolací, okno je nad kamny (z 600) → riziko sálání do rámu
  doporučuji ověřit v reálu.
- **Chlazení/odvod:** v modelu není komínové těleso ani prostup stropem; kamna
  končí na 680 mm → kouřovod je **nedomodelován**.

## 5. Otvory

| Otvor | Stěna | Rozsah | Poznámka |
|---|---|---|---|
| `void_entry_point` | východní | x 45–1 695, z 0–1 995 | 1 650 mm světlost, **dveřní křídlo není modelováno** |
| `windows_západní_dvojokno_zone_sleeping` | západní | x 3 245–4 500, z 561–1 831 | nad postelí |
| `windows_západní_dvojokno_zona_dinning_dump` | západní | x 330–1 575, z 590–1 811 | nad stolem |
| `windows_jižní_dvojokno_zone_sleeping_stove` | jižní | y 45–1 181, z 600–1 750 | u kamen |

**Nález (nekonzistence):** obě západní okna mají různou výšku parapetu
(561 vs. 590 mm) a různé nadpraží (1 831 vs. 1 811 mm) — rozdíl 20–29 mm.
Pravděpodobně dvě kopie komponenty s ručně posunutým rámem, ne záměr.
Jižní okno (parapet 600) se od obou liší také.

## 6. Překryvy a kontakty

Validace hlásí **19 překryvů**, ale všechny jsou **konstrukční sendviče**, ne chyby:

| Dvojice | Objem | Interpretace |
|---|---:|---|
| `ceiling_guttafol` × `strop izolace` | 909,0 dm³ | sendvič stropu |
| `windows_západní` × `izolace západní stěna` | 31,9 dm³ | rám okna v izolaci |
| `east_wall` × `izolace východní` | 17,2 dm³ | jádro + izolace |
| `truss_01_east` × `izolace východní` | 20,9 dm³ | pozednice v izolaci |
| `main_table` × `wall_inner_zone_entry` | 0,87 dm³ | dotyk (2 mm) |

**Doporučení pro rules:** překryv ignorovat, pokud objem < 2 % menšího z objemů
**nebo** pokud jeden z uzlů nese `wall|izolace|ceiling|floor|beam|truss|windows`.
Aktuální `overlap_min_volume_mm3 = 1000` propouští sendviče do reportu.

## 7. Integrita dat (cross-check kanálů)

Stejný model exportovaný dvakrát: DAE (nový konvertor) vs. Ruby extension (v3).

| Metrika | Výsledek |
|---|---|
| Počet uzlů | 46 vs. 46 ✅ |
| Párování uzlů (normalizované jméno + nejbližší střed) | **45 / 46** ✅ |
| Geometrické změny > 1 mm | **0** ✅ |
| Nepárový uzel | Ruby `Unnamed` vs. DAE `lfp_cell_314ah_3.2V` (stejný střed 2179, 1914, 628 → jen jiné jméno) |
| Neshody názvů (párované) | `#`→`_` (`room#1`, `truss#01`), `,`→`_` (`3,2 kW`, `0,038 W`), mezery→`_`, počáteční číslice→prefix `_` (`_24V_…`) |
| Zkorumpované matice v3 | 0 v novém exportu (`matrix_4x4` validní, translace v mm) |

Párování dělá `tools/cad2llm.py diff` (tolerance 500 mm, práh změny 1 mm),
takže výsledek je reprodukovatelný, ne ruční odhad.

**Závěr:** nový konvertor reprodukuje geometrii Ruby exportu na sub-milimetrové
úrovni; jediná ztráta je v názvech, daná formátem DAE.

## 8. Mezery v datech / doporučení

1. **Metadata chybí** (DAE): pro energetické a požární závěry nutný export
   z GUI SketchUp 2024 přes `Extensions → CAD2LLM → Export (v4)`.
2. **Kouřovod a dveře** nejsou v modelu — doplnit, jinak nelze hodnotit odstup
   od hořlavin a prostup stropem.
3. **Nekonzistentní parapety** západních oken sjednotit.
4. **Rules tuning:** potlačit konstrukční překryvy (viz §6), přidat pravidlo
   `kamna × hořlavina` (dřevo/textil) s odstupem dle výrobce.
5. **`void_entry_point` a `room_1_…_pergola`** jsou nulově tlusté plochy
   (severity `info`) — nejsou chyba, ale geometrický marker bez objemu.

## 9. Reprodukce

```powershell
python tools/cad2llm.py convert C:\Users\PC\Documents\Sketchup2016\mistnost_2_sk24.dae out.json
python tools/cad2llm.py validate out.json
python tools/cad2llm.py summary  out.json
```

Pro metadata a tagy (vyžaduje **GUI** SketchUp 2024):

```
Extensions → CAD2LLM → Export (v4)
```
