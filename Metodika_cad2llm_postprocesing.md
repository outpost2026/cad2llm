\# Metodika postprocessingu JSON prostorových dat s využitím LLM



\## 1. Účel a principy



Tato metodika definuje postupy pro práci s LLM při analýze JSON výstupů generovaných z CAD modelů (zejména SketchUp). Cílem je minimalizovat chyby interpretace a zajistit, že LLM poskytne spolehlivé, ověřené výstupy vhodné pro optimalizaci prostoru.



\### 1.1. Základní principy



| Princip | Popis |

|---------|-------|

| \*\*Sekvenční zpracování\*\* | JSON není zpracováván najednou, ale v definovaných krocích, kde každý krok je samostatně ověřen. |

| \*\*Ověření před interpretací\*\* | Žádná interpretace není formulována, dokud neprojde explicitní geometrickou validací. |

| \*\*Oddělení faktů a závěrů\*\* | Výstup striktně rozlišuje mezi přímými daty z JSON a odvozenými závěry. |

| \*\*Zpětná vazba a iterace\*\* | Každý krok umožňuje operátorovi zasáhnout a opravit chyby dříve, než ovlivní další analýzu. |

| \*\*Automatizace kontrolních mechanismů\*\* | Pomocí připravených promptů se minimalizuje subjektivita modelu. |



\---



\## 2. Příklad chyby a její prevence



\### 2.1. Případová studie: Chybná interpretace umístění střídače



\*\*Situace:\*\*  

Model tvrdil, že střídač je \*„pod pracovní deskou“\*, ačkoli bounding boxy ukazovaly:



\- Pracovní deska: `z\_min = 745, z\_max = 795`

\- Střídač: `z\_min = 1075, z\_max = 1425`



\*\*Příčiny:\*\*

\- Rychlé čtení bez numerického porovnání

\- Kontextový bias z předchozích artefaktů (střídač byl dříve umisťován nízko)

\- Nedostatečná křížová kontrola mezi uzly

\- Absence závěrečného sanity checku



\*\*Preventivní opatření začleněná do této metodiky:\*\*

\- Před formulací jakéhokoli tvrzení o vertikálním vztahu se provádí explicitní porovnání `z\_min` a `z\_max`.

\- Každá interpretace je podložena číselnou podmínkou.

\- Výšková tabulka (Krok 1) odhalí nesrovnalosti ještě před interpretací.

\- Sanity check (Krok 5) znovu ověří všechna tvrzení před finálním výstupem.



\### 2.2. Analogické situace



Stejný typ chyby může nastat při:

\- Posuzování, zda prvek blokuje komunikační koridor (chybné vyhodnocení souřadnic y)

\- Hodnocení tepelných odstupů (chybný výpočet vzdálenosti mezi kamny a baterií)

\- Určování, zda je prvek přístupný pro údržbu (přehlédnutí výšky nebo blokování jiným prvkem)



Všechny tyto situace jsou ošetřeny stejnými kontrolními mechanismy.



\---



\## 3. Sekvenční postup práce s LLM



Níže je popsáno \*\*pět povinných kroků\*\*, které operátor provádí v interakci s LLM. Každý krok má svůj prompt, očekávaný výstup a kontrolní bod.



\### Krok 0: Příprava vstupu

\- JSON musí obsahovat `bounding\_box` pro každý uzel.

\- Názvy uzlů by měly být sémantické (např. `POW-HVM3.2H`, nikoli `ID224`).

\- Pokud možno, předem ověřte, že souřadnice jsou v milimetrech a pravotočivém systému (Z‑up).



\---



\### Krok 1: Extrakce a výšková tabulka



\*\*Prompt:\*\*

```

Načti JSON ze souboru \[název]. Proveď extrakci všech uzlů, které obsahují bounding\_box. 

Vytvoř tabulku seřazenou podle z\_min (vzestupně) s následujícími sloupci:

\- name

\- z\_min \[mm]

\- z\_max \[mm]

\- výška (z\_max - z\_min) \[mm]

\- typ (odhadni podle názvu a rozměrů: nábytek, konstrukce, technika, okno/dveře)



U každého řádku přidej poznámku, pokud je umístění nestandardní (např. střídač ve výšce podlahy, postel ve výšce >500 mm).

Výstupem je pouze tato tabulka a stručné shrnutí (max 3 věty) o vertikálním uspořádání.

```



\*\*Kontrolní bod:\*\* Operátor zkontroluje, zda tabulka odpovídá očekávání (např. pracovní deska je ve výšce 750–900 mm, střídač je výše, postel je na podlaze). Pokud je něco podezřelé, operátor upraví model nebo zadá opravu.



\---



\### Krok 2: Horizontální zónování a kolizní kontroly



\*\*Prompt:\*\*

```

Na základě tabulky z kroku 1 a bounding boxů nyní proveď horizontální analýzu.

Vytvoř mapu místnosti v půdorysu (x\_min..x\_max, y\_min..y\_max) pro každý uzel, který má významný objem (vynech zdi, strop, podlahu). 

Rozděl místnost do logických zón podle souřadnic y (např. 0–1000, 1000–1500, 1500–2000) a u každé zóny vypiš všechny prvky, které do ní zasahují.

Identifikuj:

\- Překryvy (dva prvky na stejné z/y, liší se z)

\- Potenciální kolize (prvky s překrývajícími se bounding boxy v x-y)

\- Prvky, které blokují komunikační koridor (definuj koridor jako y 1000–1500)

Výstupem je tabulka zón, seznam kolizí a komentář k průchodnosti.

```



\*\*Kontrolní bod:\*\* Operátor ověří, zda jsou identifikované kolize reálné a zda koridor zůstává volný. V případě zjištěných kolizí operátor rozhodne o úpravě modelu.



\---



\### Krok 3: Anomálie a pravděpodobnostní kontroly



\*\*Prompt:\*\*

```

Na základě typických rozměrů a umístění běžných prvků (pracovní deska 750–900 mm výška, střídač 0–200 mm nebo 1000–1600 mm, postel 0–500 mm, okna parapet 600–900 mm) proveď kontrolu každého uzlu:

\- Je výška prvku v očekávaném intervalu? Pokud ne, označ jako anomálii.

\- Je prvek umístěn v logické vazbě na ostatní? (např. baterie pod deskou, střídač v blízkosti baterie)

\- Je název konzistentní s rozměry? (např. okno má malou tloušťku, stůl má malou výšku)

Seznam anomálií a podezřelých položek vypiš do tabulky s doporučením k ověření.

```



\*\*Kontrolní bod:\*\* Operátor posoudí, zda anomálie jsou skutečné chyby modelu nebo záměrné nestandardní řešení. Podle toho buď opraví JSON, nebo anomálii potvrdí.



\---



\### Krok 4: Křížová kontrola s textovým kontextem



\*\*Prompt (použijte, pokud existuje textová specifikace):\*\*

```

Porovnej data z JSON s dostupnými textovými specifikacemi (např. půdorysné rozměry, výška stropu, počet oken, umístění dveří).

Vytvoř kontrolní seznam:

\- Půdorys x: očekáváno \[hodnota] / JSON: \[hodnota z bounding boxu podlahy]

\- Půdorys y: očekáváno \[hodnota] / JSON: ...

\- Výška stropu min: očekáváno \[hodnota] / JSON: ...

\- Počet oken: očekáváno \[hodnota] / JSON: \[počet uzlů s "window" v názvu]

\- Umístění dveří: očekáváno \[stěna] / JSON: \[souřadnice door]

U každého bodu uveď, zda souhlasí, a pokud ne, jaký je rozdíl.

```



\*\*Kontrolní bod:\*\* Operátor opraví model nebo aktualizuje textovou specifikaci tak, aby byly konzistentní.



\---



\### Krok 5: Sanity check a finální syntéza



\*\*Prompt:\*\*

```

Nyní proveď závěrečný sanity check všech dříve zjištěných faktů:

1\. Pro každé tvrzení o prostorovém vztahu (např. "A je nad B") explicitně ověř pomocí bounding boxů.

2\. Zkontroluj, že všechny důležité prvky (baterie, střídač, kamna, postel, dveře, okna) jsou přítomny a mají rozumné rozměry.

3\. Ověř, že žádný prvek nezasahuje do dveřního křídla (při interním otevírání) – pokud není k dispozici, označ jako nedostatek.

4\. Shrň do 5 bodů, co je v modelu správně, a do 3 bodů, co vyžaduje opravu nebo další ověření.

Teprve poté, co operátor potvrdí správnost sanity checku, můžeš přistoupit k podrobné optimalizační analýze.

```



\*\*Kontrolní bod:\*\* Operátor vydá finální souhlas. Teprve po tomto souhlasu LLM pokračuje k optimalizační analýze, ergonomickým simulacím nebo dalším odvozeným úkolům.



\---



\## 4. Role operátora a LLM v jednotlivých fázích



| Fáze | Role operátora | Role LLM |

|------|----------------|----------|

| Krok 1 | Zadá prompt, zkontroluje tabulku, případně opraví model. | Extrahuje data, vytvoří přehled, identifikuje nestandardní výšky. |

| Krok 2 | Ověří kolize a průchodnost, rozhodne o úpravách. | Identifikuje překryvy a kolize, navrhne zóny. |

| Krok 3 | Posoudí anomálie, potvrdí nebo opraví. | Porovná s typickými hodnotami, označí podezřelé položky. |

| Krok 4 | Ověří konzistenci s textovými podklady. | Porovná JSON s textem, vytvoří kontrolní seznam. |

| Krok 5 | Potvrdí správnost, umožní pokračovat. | Provede finální validaci všech tvrzení, shrne správné body a nedostatky. |



\---



\## 5. Technické doplňky pro robustnější analýzu



\### 5.1. Předzpracování JSON skriptem

Doporučuje se před vstupem do LLM spustit jednoduchý skript, který:

\- Ověří, že každý uzel má `bounding\_box` s číselnými hodnotami.

\- Sjednotí jednotky (převod na mm).

\- Vypočte odvozené hodnoty (např. objem, těžiště) pro pozdější použití.



\### 5.2. Definice sémantických tagů ve zdrojovém modelu

Pro usnadnění analýzy je vhodné ve SketchUp používat tagy:

\- `\_COMP` – komponenta

\- `\_ZONE` – funkční zóna (např. `movement\_corridor\_ZONE`)

\- `\_VOID` – servisní prostor, který musí zůstat volný



Skript pak může tyto tagy převést do JSON jako samostatné pole `semantic\_tags`.



\### 5.3. Automatická validace pravidel

Do JSON lze přidat sekci `validation`, která obsahuje výsledky předem definovaných kontrol:

\- Bezpečnostní vzdálenosti (např. kamna – hořlavé materiály)

\- Přístupnost pro údržbu (prvky s `serviceable=true` musí mít volný přístup)

\- Osvětlení (okna musí být v určité výšce)



LLM pak tyto validace pouze čte a interpretuje, místo aby je sám počítal.



\---



\## 6. Ukázka aplikace metodiky na případu střídače



1\. \*\*Krok 1:\*\* Výšková tabulka ukáže:

&#x20;  - Pracovní deska: z 745–795

&#x20;  - Střídač: z 1075–1425

&#x20;  → LLM si uloží tyto hodnoty do paměti.



2\. \*\*Krok 2:\*\* Horizontální analýza odhalí, že střídač je v zóně y 1900–2000, což je technický hub.



3\. \*\*Krok 3:\*\* Anomálie – střídač ve výšce 1075–1425 je v očekávaném intervalu (1000–1600), není anomálie.



4\. \*\*Krok 4:\*\* Křížová kontrola s textem: původní specifikace uváděla střídač v technickém hubu, souhlasí.



5\. \*\*Krok 5:\*\* Sanity check – LLM explicitně porovná `střídač.z\_min` (1075) s `pracovní deska.z\_max` (795) → 1075 > 795 → střídač je nad deskou. Tvrzení „pod deskou“ by bylo v tomto kroku opraveno.



Pokud by chyba vznikla dříve, sanity check ji zachytí a zabrání jejímu proniknutí do finálního výstupu.



\---



\## 7. Závěr



Tato metodika poskytuje strukturovaný, opakovatelný postup pro analýzu JSON prostorových dat s využitím LLM. Důraz je kladen na sekvenční ověřování, oddělení faktů od interpretací a aktivní roli operátora jako garanta správnosti. Při dodržení těchto zásad se riziko chyb podobných případu se střídačem minimalizuje a LLM se stává spolehlivým nástrojem pro prostorovou optimalizaci.



\*\*Doporučení pro další rozvoj:\*\*  

Implementovat skript, který generuje JSON již s předpočítanými vztahy (např. `is\_above`, `is\_blocking\_corridor`), čímž se LLM zbaví nutnosti tyto vztahy odvozovat a sníží se riziko chybné interpretace na minimum.

