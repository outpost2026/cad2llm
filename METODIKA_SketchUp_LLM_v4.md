\# METODIKA: SketchUp → DAE → Sémantický JSON → LLM



> Replikovatelný postup pro deterministickou prostorovou analýzu pomocí LLM.  

> Verze: 1.1 · Datum: 2026-03-30 · Kontext: Outpost Zone 2 (`mistnost\_2\_v4`)



\* \* \*



\## 0\\. Filosofie pipeline



\*\*Základní princip:\*\* LLM je silný v uvažování, slabý v parsování surové geometrie.  

Preprocessing musí být deterministický — LLM dostane \*\*sémantiku a čísla v mm\*\*, ne COLLADA XML k interpretaci.



```

Fyzický svět → CAD model → convert\_dae\_to\_json\_v3.py → JSON (mm) → LLM

&#x20;                                       ↑

&#x20;                    Deterministický krok. Bez něj: halucinace i na "správném" formátu.

```



\*\*Jak se k tomuto závěru dospělo:\*\*



\-   Export 2D z SketchUpu → LLM radí správně, ale bez souřadnic radí na špatném místě

\-   Export `.dae` přímo do LLM → rétoricky spokojeno, analyticky stále halucinuje

\-   Deterministický skript → LLM dostane čísla, ne geometrii k interpretaci → funguje



\* \* \*



\## 1\\. Fyzické měření



\### Nástroje



\-   Standardní kovový svinovací metr — přesnost ±2 mm, offline, bez baterií

\-   Zápisník se schématem půdorysu — papír jako první vrstva záznamu



\### Postup



1\.  Změřit místnost: délka × šířka × výška od stěny ke stěně (hrubá stavba)

2\.  Každý objekt: rozměry + pozice od dvou pevných referenčních bodů

3\.  Dveřní výklenky, okenní špalety, vyčnívající prvky — každý jako samostatný bod

4\.  Přenést do sketchup — papír → digitál



\*\*Pravidlo:\*\* Žádný odhad, žádné zaokrouhlení nad 5 mm. Chyba v měření se promítne do JSON a LLM ji neodhalí.



\* \* \*



\## 2\\. SketchUp 2016 — pravidla modelování



\### Proč SketchUp 2016



\-   Lokální instalace, funkční offline (Outpost je off-grid)

\-   Stabilní COLLADA export bez cloud závislostí

\-   Jednoduché pojmenování Components, které konvertor zachová



\### Každý odlišný objekt = vlastní vrstva + Component



Postup pro každý prvek:



1\.  Nakreslit geometrii

2\.  Převést na \*\*Component\*\* (`Make Component`)

3\.  Pojmenovat explicitně — jméno se stane `name` polem v JSON



\*\*Příklady pojmenování z `mistnost\_2\_v4`:\*\*



```

bed\_v1              ← spací plocha

table\_main          ← pracovní stůl

stove\_main          ← kamna

worktop\_m2          ← pracovní deska nad bateriemi

lfp\_main\_worktop    ← spodní police bateriové sekce

lfp\_cell            ← jednotlivý LiFePO4 článek (×16 instancí stejného Componentu)

gokw\_lfp            ← nosná konstrukce bateriové sekce

HWP\_3.2\_kW\_invertor ← střídač

window\_south        ← okno jih

window\_bed          ← okno u postele

windows\_table       ← okno u stolu

Door                ← dveře

```



\*\*Proč pojmenování záleží:\*\* Konvertor filtruje technický šum SketchUpu (`instance\_\*`, `skp\_camera`, `SketchUp`) — zachová pouze pojmenované uzly. Generické `group\_1` projde, ale LLM ho nebude umět použít při analýze.



\### Osový systém — kritický bod



Chybný osový systém byl hlavní příčinou nekonzistencí v `mistnost\_2\_v3`. Pravidlo:



\-   \*\*Před exportem ověř:\*\* `Camera → Standard Views → Top` — půdorys musí odpovídat zápisníku

\-   X = délka místnosti (větší rozměr), Y = šířka, Z = výška

\-   Pokud LLM analýza reportuje nekonzistence v souřadnicích — první hypotéza je chybná osa, ne chybný skript



\### Export .dae



`File → Export → 3D Model → COLLADA (.dae)`  

Nastavení: výchozí, bez komprese, bez textur.



\* \* \*



\## 3\\. Konvertor `convert\_dae\_to\_json\_v3.py`



\### Závislosti



```bash

pip install lxml numpy

```



\### Co dělá



\*\*Krok 1 — Detekce jednotek\*\*  

Čte `<asset><unit meter="...">` z COLLADA hlavičky. SketchUp 2016 exportuje v palcích (`meter="0.0254"`). Převod: `mm = hodnota × 0.0254 × 1000 = hodnota × 25.4`.



\*\*Krok 2 — Indexování knihoven\*\*  

Buduje slovníky `lib\_nodes` a `lib\_geoms` z `<library\_nodes>` a `<library\_geometries>`. O(1) lookup při průchodu.



\*\*Krok 3 — Rekurzivní průchod scene grafu\*\*  

Pro každý `<node>`:



\-   Extrahuje lokální 4×4 matici z `<matrix>`

\-   Násobí s parent maticí: `world\_matrix = parent\_matrix @ local\_matrix`

\-   Rozkládá na world pozici (mm), rotaci (Euler XYZ, stupně), scale

\-   Resolvuje `<instance\_node>` reference (zanořené Components)



\*\*Krok 4 — Vertex-level bounding box\*\*  

Pro každou geometrii extrahuje raw vertex positions z `<float\_array>`, transformuje do world space maticovým násobením, počítá min/max → bounding box v mm. Výsledek: skutečné rozměry objektu, ne jen pivot point.



\*\*Krok 5 — Noise filter\*\*  

Uzly `instance\_\*`, `SketchUp`, `skp\_camera` jsou přeskočeny (exportní artefakty SketchUpu). Jejich potomci jsou zpracováni normálně.



\### Výstupní formát



```json

{

&#x20; "source\_file": "mistnost\_2\_v4.dae",

&#x20; "nodes": \[

&#x20;   {

&#x20;     "id": "ID24",

&#x20;     "name": "lfp\_cell",

&#x20;     "world\_position\_mm": { "x": 2632.9998, "y": 1827.9999, "z": 525.0 },

&#x20;     "world\_rotation\_deg": { "x": 0.0, "y": -0.0, "z": 0.0 },

&#x20;     "world\_scale": { "x": 1.0, "y": 1.0, "z": 1.0 },

&#x20;     "bounding\_box\_mm": {

&#x20;       "min": { "x": 2632.9998, "y": 1827.9999, "z": 525.0 },

&#x20;       "max": { "x": 2702.9998, "y": 1999.9999, "z": 730.0 }

&#x20;     }

&#x20;   }

&#x20; ]

}

```



`bounding\_box\_mm` je klíčový — LLM z něj počítá clearance, kolize a servisní přístupy.



\### Spuštění



```bash

python scripts/convert\_dae\_to\_json\_v3.py <vstup.dae> <vystup.json>

```



\* \* \*



\## 4\\. LLM analýza — jak předat JSON



\### Vstupní prompt (šablona)



Narativní popis + úkol:



```

Toto je JSON místnosti \[název] o rozměrech přibližně \[X×Y mm].

Proveď:

1\. Clearance check klíčových objektů (min. průchod 600 mm)

2\. Identifikuj kolize nebo kritická místa

3\. Popis prostor a jejich funkce

4\. Upozorni na nekonzistence v datech (souřadnice mimo místnost, překrývající se objekty)



<room\_data>

\[celý obsah JSON]

</room\_data>

```



\*\*Proč přidat bod 4 (nekonzistence):\*\* Analýza nekonzistencí je diagnostický nástroj pro odhalení chyb v modelu — jako při osové korekci v `mistnost\_2\_v3 → v4`.



\### Validace výstupu (anti-halucinační check)



\-   \\\[ \\] Každý zmíněný rozměr souhlasí s `bounding\_box\_mm` (±5 mm tolerance)

\-   \\\[ \\] Navrhované souřadnice jsou v rozsahu místnosti (z `group\_0` bounding boxu)

\-   \\\[ \\] Objekty identifikovány svými jmény z JSON, ne vymyšlenými

\-   \\\[ \\] Pokud LLM reportuje nekonzistenci — ověřit v SketchUp, ne jen přijmout



\### Groq jako validační nástroj



Groq (rychlý inference) se osvědčil pro validaci: přidat JSON + otázku „Nalézáš nekonzistence v souřadnicovém systému?" Je rychlý a přímý — vhodný pro binární validaci výstupu po korekci.



\* \* \*



\## 5\\. Iterační cyklus



```

mistnost\_2\_v1 → JSON → analýza → korekce SketchUp → mistnost\_2\_v2 → ...

```



Každá verze `.dae` a JSON je samostatný soubor. Nikdy nepřepisovat — umožňuje porovnání výstupu před a po korekci.



\*\*Konkrétní průběh pro Zone 2:\*\*



\-   `v1`, `v2` — skript nedostatečný (vertex BB chyběl)

\-   `v3` — skript v3, analýza odhalila chybný osový systém

\-   `v3` zůstává — osová korekce provedena, Groq validace: čisto



\* \* \*



\## 6\\. Přenositelnost metodiky



Pipeline funguje kdykoli platí:



\-   Existuje fyzické měření nebo CAD model

\-   Je potřeba LLM analýza prostoru (zástavba, kolize, optimalizace)

\-   No-code export dává nekonzistentní nebo nepoužitelné výsledky



Typická použití: zástavba kanceláří, dílen, skladů; rekonstrukce bytů; průmyslové layouty, a samozřejmě \*\*hra pro geeky, autisty\*\* atd..



\* \* \*



\*Metodika dokumentuje postup použitý v projektu Outpost 2026 · 30. 3. 2026\*

