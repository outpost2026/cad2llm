# Metodika SketchUp → JSON (Narativní zrcadlo pro LLM)

Cílem je transformovat SketchUp 2016 z čistě vizuálního nástroje na **zdroj strukturovaných dat**, kde JSON není jen export geometrie, ale sémantický popis záměru architekta.

---

## 1. Vylepšená metodika práce ve SketchUp (Data-First Design)

Pro dosažení vysoké kvality JSON výstupů pro LLM je nutné změnit způsob, jakým modelujeme:

### A. Striktní jmenné konvence (Sémantika)
LLM pracuje lépe s explicitními názvy.
*   **Špatně:** `group_1`, `instance_0`
*   **Správně (Pattern: `Typ_Funkce_ID`):**
    *   `BAT_LiFePO4_16S_01` (Baterie)
    *   `APL_Stridac_3200W_01` (Střídač)
    *   `ZON_Kuchyn_Pracovni_01` (Zóna)
    *   `FIX_Okno_Jih_01` (Fixní prvek)

### B. Hierarchické tagování (Modelování logiky)
Největší problém je "plochá" hierarchie. Modelujte logické celky jako skupiny s příponou, kterou skript rozpozná:
*   **`_COMP` (Komponenta):** Objekt, který má své vlastní vertex-data (např. stůl).
*   **`_ZONE` (Zóna):** Prázdná skupina, která definuje funkční prostor (např. zóna pohybu, servisní přístup).
*   **`_VOID` (Větrání/Servis):** Objem, který nesmí být zastavěn (klíčové pro LLM analýzu kolizí).

### C. Metadata přes "Dynamic Components"
SketchUp 2016 podporuje Dynamic Components. Využijte je k vložení atributů přímo do `.dae` (budou viditelné v XML struktuře):
*   Atribut: `is_serviceable` (true/false)
*   Atribut: `thermal_load` (watts)
*   Atribut: `material_type` (vlastnosti pro simulaci)

---

## 2. Implementace v "Narativním zrcadle" (Vylepšený skript)

Skript `convert_dae_to_json_v4.py` (návrh) by měl být rozšířen o:

1.  **Parsování Custom Atributů:** Extrakce parametrů z Dynamic Components (pokud je SketchUp exportuje do `.dae`).
2.  **Sémantický filtr:** Skript automaticky ignoruje objekty bez prefixu (např. `_`) a bere je jako "šum".
3.  **Výpočet "Servisní vůle":** Detekce `_VOID` skupin a jejich porovnání s `_COMP` skupinami. Pokud je `_COMP` příliš blízko `_VOID` (nebo mu chybí), JSON vygeneruje varování `warning: "low_maintenance_access"`.

---

## 3. Zrychlení procesu: Design → Revize → Výstup

| Fáze | Akce | Výsledek pro LLM |
| :--- | :--- | :--- |
| **Design** | Modelování s prefixy (`BAT_...`, `_VOID`) | LLM ihned chápe funkci objektu. |
| **Export** | Spuštění `convert.py` | JSON obsahuje "inteligentní" metadata. |
| **Revize** | Prompt LLM: "Zkontroluj přístup k `BAT_...` v `mistnost_2`." | LLM vrací: "Objekt `BAT_...` je blokován `_COMP_kuchyn`." |
| **Finální** | Korekce ve SketchUp a potvrzení | Iterace v řádu minut místo hodin. |

---

## 4. Doporučení pro LLM Asistenci

Aby byla integrace LLM maximálně efektivní:

1.  **Vytvořte "Schema Definition":** Definujte pro LLM pravidla (např. "každý střídač musí mít 100mm odstup od stěny"). Skript může po exportu toto pravidlo ověřit a v JSON přidat pole `validation_errors`.
2.  **Využijte JSON jako "Prompt Context":** Celý JSON exportujte do LLM jako `context.json`. LLM pak při dotazu na ergonomii vidí reálné souřadnice a rozměry, nikoliv jen obecné rady.

**Závěr:** Přechod od "modelování tvarů" k "modelování dat a vztahů" ve SketchUpu dramaticky zvýší užitečnost vašeho JSON výstupu pro LLM asistenci.
