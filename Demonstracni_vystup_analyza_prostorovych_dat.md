*Cílem tohoto reportu je ukázat, **jaká data konverzní skript extrahuje** a **jaké analytické závěry** z nich lze (i automatizovaně) vyvodit.*

---

# 📊 Demonstrační výstup: Analýza prostorových dat z JSON

Tento report demonstruje možnosti využití dat vygenerovaných naším konverzním skriptem `dae2json`. Ze surového 3D modelu (`mistnost_2_v7.dae`) byl vygenerován strukturovaný JSON soubor, který nám umožňuje provádět datovou analytiku prostoru bez nutnosti spouštět 3D software.

## 1. Souhrnné informace o scéně
*   **Zdrojový soubor:** `mistnost_2_v7.dae`
*   **Počet extrahovaných uzlů (nodes):** 53
*   **Souřadnicový systém:** Milimetry (mm), pravotočivý
*   **Základní transformace:** Skript úspěšně parsuje `world_position_mm`, `world_rotation_deg`, `world_scale` a globální `bounding_box_mm`.

---

## 2. Architektonická analýza a rozměry (Bounding Box Math)
Díky extrahovaným obálkám (Bounding Boxes) můžeme automatizovaně vypočítat základní proporce místnosti bez vizuálního renderování.

### Výpočet velikosti místnosti
Na základě uzlu `ID210` (`floor_m2`):
*   **Minimální body:** `X: -24`, `Y: -24`, `Z: -20`
*   **Maximální body:** `X: 4524`, `Y: 2024`, `Z: 0`
*   **Vypočtené rozměry (vnitřní půdorys):** ~4,55 m × 2,05 m
*   **Užitná plocha:** **~9,3 m²** (jedná se o velmi kompaktní prostor, pravděpodobně "Tiny House" nebo technický kontejner).

### Výška stropu
Na základě uzlu `ID285` (`Ceiling_m2`):
*   **Výška stropu (Z osa):** ~1 990 mm až 2 090 mm. Spodní hrana stropu se nachází zhruba ve výšce **2,0 m**.

---

## 3. Topologická a sémantická detekce (Co v místnosti je?)
JSON struktura zachovává hierarchii pojmenování z původního návrhového softwaru (např. SketchUp komponenty). Z názvů uzlů a jejich parametrů dokážeme rekonstruovat "příběh" místnosti.

Prostor slouží jako **kombinovaná obytná a vysoce vybavená technická místnost (Off-grid systém)**.

### A. Obytná část
Skript přesně lokalizoval prvky pro bydlení na základě jejich jmen a pozic:
*   **Postel (`Bed_01_mattres`):** Umístěna v zadní části (`X: 2495 - 4495`). S rozměry 2000 mm na délku a 970 mm na šířku odpovídá standardnímu jednolůžku. Z osy Z (`0 - 180 mm`) je patrné, že jde o velmi nízkou matraci nebo postel bez nohou.
*   **Topení (`Kamna_Priti_mini_D`):** Umístěno na souřadnicích `X: 3988`, nedaleko postele. Výška 679 mm napovídá, že jde o kompaktní kamna na tuhá paliva.
*   **Dveře a okna:** Detekovány jedny hlavní dveře (`main_door_DIY`) a 3 okna poskytující světlo ze tří světových stran (západ, jih, jihozápad).

### B. Průmyslový / Off-grid energetický systém
Nejzajímavějším prvkem datového výstupu je detailní rozpis solárního/bateriového úložiště:

| Prvek | Počet v JSON | Analýza pozice (Z osa / výška) |
| :--- | :---: | :--- |
| **LFP Články (314Ah)** | 16 uzlů | Všechny články mají pozici Z = 525 mm s max obálkou 730 mm. |
| **Sekundární LFP (100Ah)** | 2 uzly | Baterie GOKWH 24V. |
| **Invertor (`POW-HVM3.2H`)** | 2 uzly (tagy) | Umístěn na zdi (`Z: 1074 mm - 1424 mm`), tedy nad bateriemi. |
| **Pracovní stůl (`Worktop_01`)**| 2 uzly (tagy) | Výška hrany (`Z: 745 mm`). |

**💡 Datový postřeh (Kolize a vrstvení):** 
Porovnáním dat vidíme, že bateriové články (max Z = 730 mm) se nacházejí přesně *pod* pracovní deskou (min Z = 745 mm). Stůl slouží jako kryt pro bateriové úložiště. Algoritmus pro výpočet kolizí by zde nevyhodnotil chybu, což ukazuje na přesnost exportu.

---

## 4. Technické vlastnosti exportu (Pro vývojáře)

Tento JSON výstup odhaluje několik vlastností původního .dae souboru, se kterými si náš konvertor úspěšně poradil:

1.  **Rozbalení instancí (Instancing):** Model zjevně používal komponenty (např. `lfp_cell_314ah`). Skript vyexportoval každý výskyt jako samostatný uzel s absolutními souřadnicemi (`world_position_mm`), což drasticky zjednodušuje práci pro downstream aplikace (např. WebGL renderery), které nemusí počítat lokální transformační matice.
2.  **Aliasing a duplicitní tagování:** Skript odhalil praxi tvůrce 3D modelu, který jedné fyzické geometrii přiřazoval více sémantických vrstev. Příklad:
    *   Uzel `ID32` = `Worktop_01` (Fyzický objekt)
    *   Uzel `ID33` = `worktop_user` (Funkční zóna)
    *   Oba mají absolutně shodný Bounding Box. Skript tato data uchovává nedestruktivně pro případnou sémantickou filtraci.
3.  **Kamera:** Zachována je i informace o poslední pozici kamery uživatele (`skp_camera`), což se hodí pro automatické nastavení počátečního pohledu v aplikacích typu Three.js nebo Babylon.js.

---

## Shrnutí
Generovaný JSON soubor poskytuje lehký, strojově čitelný, a přitom matematicky přesný obraz 3D prostoru. Bez nutnosti načítat desítky megabytů polygonálních dat můžeme pomocí jednoduchých skriptů provádět inventarizaci (`count(lfp_cell)`), prostorové dotazy (co je výše než 1 metr?) a automatické kontroly kolizí.

**Tento výstup je připraven pro integraci do:**
*   Skladových a inventárních systémů.
*   Webových 3D viewerů (Three.js/React-Three-Fiber).
*   Algoritmů pro kontrolu stavebních předpisů (BIM validace).
