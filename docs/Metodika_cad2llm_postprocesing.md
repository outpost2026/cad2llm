# Metodika postprocessingu JSON prostorových dat s využitím LLM

Tato metodika definuje postupy pro práci s Large Language Models (LLM) při analýze JSON výstupů generovaných z CAD modelů (zejména SketchUp). Cílem je minimalizovat chyby interpretace a zajistit, že LLM poskytne spolehlivé, ověřené výstupy vhodné pro prostorovou optimalizaci.

## 1. Účel a principy

Základem metodiky je předpoklad, že LLM může chybovat v prostorové představivosti, pokud pracuje s čistými daty bez strukturovaného vedení.

### 1.1. Základní principy

| Princip | Popis |
| :--- | :--- |
| **Sekvenční zpracování** | JSON není zpracováván najednou, ale v definovaných krocích, kde každý krok je samostatně ověřen. |
| **Ověření před interpretací** | Žádná interpretace není formulována, dokud neprojde explicitní geometrickou validací. |
| **Oddělení faktů a závěrů** | Výstup striktně rozlišuje mezi přímými daty z JSON a odvozenými závěry. |
| **Zpětná vazba a iterace** | Každý krok umožňuje operátorovi zasáhnout a opravit chyby dříve, než ovlivní další analýzu. |
| **Automatizace kontrol** | Pomocí připravených promptů se minimalizuje subjektivita a "halucinace" modelu. |

---

## 2. Příklad chyby a její prevence

### 2.1. Případová studie: Chybná interpretace umístění střídače

> [!CAUTION]
> **Situace:** Model tvrdil, že střídač je *"pod pracovní deskou"*, ačkoli bounding boxy v JSON datech ukazovaly:
> * **Pracovní deska:** `z_min = 745, z_max = 795`
> * **Střídač:** `z_min = 1075, z_max = 1425`

**Příčiny chyby:**
* Rychlé čtení bez numerického porovnání.
* Kontextový bias (střídač byl v předchozích verzích umisťován nízko).
* Absence křížové kontroly mezi uzly.

**Preventivní opatření začleněná do metodiky:**
* Před formulací vztahu (nad/pod) se provádí **explicitní numerické porovnání** `z_min` a `z_max`.
* Každá interpretace musí být podložena číselnou podmínkou.
* Výšková tabulka (Krok 1) odhalí nesrovnalosti dříve, než začne analýza.

---

## 3. Sekvenční postup práce s LLM

Postup se skládá z **pěti povinných kroků**. Každý krok obsahuje prompt, očekávaný výstup a kontrolní bod pro operátora.

### Krok 0: Příprava vstupu
* JSON musí obsahovat `bounding_box` pro každý uzel.
* Názvy uzlů by měly být sémantické (např. `POW-HVM3.2H` místo `ID224`).
* Jednotky musí být sjednoceny (standardně milimetry, systém Z-up).

### Krok 1: Extrakce a výšková tabulka
**Prompt:**
```text
Načti JSON ze souboru [název]. Proveď extrakci všech uzlů, které obsahují bounding_box. 
Vytvoř tabulku seřazenou podle z_min (vzestupně) s následujícími sloupci:
- name
- z_min [mm]
- z_max [mm]
- výška (z_max - z_min) [mm]
- typ (odhadni podle názvu a rozměrů: nábytek, konstrukce, technika, okno/dveře)

U každého řádku přidej poznámku, pokud je umístění nestandardní (např. střídač ve výšce podlahy).
Výstupem je pouze tato tabulka a stručné shrnutí (max 3 věty) o vertikálním uspořádání.
```
**Kontrolní bod:** Operátor ověří, zda výšky odpovídají realitě (např. deska v 750–900 mm).

### Krok 2: Horizontální zónování a kolizní kontroly
**Prompt:**
```text
Na základě tabulky z kroku 1 a bounding boxů proveď horizontální analýzu.
Vytvoř mapu místnosti v půdorysu (x, y) pro každý významný uzel. 
Rozděl místnost do logických zón podle souřadnic y a vypiš prvky, které do nich zasahují.
Identifikuj:
- Překryvy (dva prvky na stejné x/y, liší se v z)
- Potenciální kolize (překryv bounding boxů v x-y i z)
- Blokování koridoru (definuj průchod jako y 1000–1500)
Výstupem je tabulka zón, seznam kolizí a komentář k průchodnosti.
```

### Krok 3: Anomálie a pravděpodobnostní kontroly
**Prompt:**
```text
Porovnej rozměry s typickými standardy (deska 750–900 mm, střídač 1000–1600 mm, postel do 500 mm).
Proveď kontrolu:
- Je výška prvku v očekávaném intervalu?
- Je prvek v logické vazbě na ostatní? (např. baterie u střídače)
- Odpovídá název rozměrům? (např. okno vs. tloušťka stěny)
Seznam anomálií vypiš do tabulky s doporučením k ověření.
```

### Krok 4: Křížová kontrola s textovým kontextem
**Prompt:**
```text
Porovnej data z JSON s textovou specifikací (půdorys, výška stropu, počet oken).
Vytvoř kontrolní seznam:
- Celkové rozměry (JSON vs. Specifikace)
- Počet klíčových prvků
- Umístění dveří a oken
Uveď shodu/neshodu a konkrétní rozdíly.
```

### Krok 5: Sanity check a finální syntéza
**Prompt:**
```text
Proveď závěrečný sanity check:
1. Explicitně ověř každý prostorový vztah (A nad B) pomocí čísel z bounding boxů.
2. Ověř přítomnost a rozměry kritických prvků (baterie, střídač, kamna, dveře).
3. Prověř kolizi s dráhou otevírání dveří.
4. Shrň v 5 bodech, co je v modelu správně, a ve 3 bodech, co vyžaduje opravu.
Teprve po mém potvrzení přejdi k optimalizační analýze.
```

---

## 4. Role v procesu

| Fáze | Role operátora | Role LLM |
| :--- | :--- | :--- |
| **Krok 1** | Zadání promptu, kontrola logiky výšek. | Extrakce dat, kategorizace, výpočet výšek. |
| **Krok 2** | Validace kolizí a průchodnosti. | Geometrická analýza překryvů a zónování. |
| **Krok 3** | Rozhodnutí o opravě modelu/dat. | Identifikace statistických anomálií. |
| **Krok 4** | Kontrola externí konzistence. | Porovnání strukturovaných dat s textem. |
| **Krok 5** | Finální schválení ("Go/No-Go"). | Rigorózní validace všech předpokladů. |

---

## 5. Technická doporučení pro robustnost

1.  **Předzpracování:** Doporučuje se použít jednoduchý Python skript pro validaci JSON (kontrola chybějících `bounding_box`, sjednocení jednotek na mm).
2.  **Sémantické tagy:** Ve zdrojovém CAD modelu používejte prefixy:
    * `_COMP` (Komponenta)
    * `_ZONE` (Pohybová zóna)
    * `_VOID` (Servisní/bezpečnostní prostor)
3.  **Automatická validace:** Do JSON lze vložit sekci `validation` s výsledky skriptových kontrol (např. vzdálenost hořlavých materiálů od kamen), které LLM pouze interpretuje.

---

## 6. Závěr

Tato metodika transformuje LLM z "generátoru textu" na "nástroj prostorové verifikace". Důraz na **sekvenční kroky** a **číselné sanity checky** eliminuje halucinace a zajišťuje, že výsledná optimalizace prostoru bude založena na reálných geometrických faktech.

> **Doporučení pro další rozvoj:** Implementovat generování JSON s předpočítanými binárními vztahy (např. `is_above: true`), čímž se zcela eliminuje nutnost geometrických výpočtů na straně LLM.
