## Analýza JSON `outpost_cad2llm.json` – energetický hub / místnost 2

Na základě poskytnutého JSON souboru (export z CAD nástroje, pravděpodobně SketchUp, převedený do jednotného formátu s metry a bounding boxy) provedu detailní rozbor prostoru, jeho vybavení, konstrukce a energetického systému. Výstup slouží jako nezávislá expertíza – bez ohledu na předchozí konverzaci.

---

### 1. Základní informace o projektu

| Položka | Hodnota |
|---------|---------|
| Název | `mistnost_2_sk24` |
| Datum exportu | 2026-04-15T09:23:30Z |
| GPS (WGS84) | 50.150888, 14.42336652 – lokalita u Prahy, Česká republika |
| Jednotky | metry |
| Počet uzlů | 45 (zahrnuje konstrukce, nábytek, techniku, izolace) |

---

### 2. Prostorové parametry místnosti

Rozměry vycházejí z vnitřních dřevěných stěn (tag `walls`) a podlahy:

- **Délka (osa X):** 0,0 m až 4,5 m → **4,5 m**
- **Šířka (osa Y):** 0,0 m až 2,0 m → **2,0 m**
- **Výška (osa Z):** 0,0 m (podlaha) až 1,99 m (spodní hrana stropu) → **1,99 m**

Užitná plocha podlahy: **9,0 m²**  
Obestavěný prostor: **~17,9 m³**

Konstrukční skladba (od vnitřku k vnějšímu plášti):

| Prvek | Tloušťka (m) | Materiál / poznámka |
|-------|--------------|----------------------|
| Podlaha | 0,02 | Dřevěná |
| Podlahová izolace | 0,10 | Minerální vlna, λ=0,038 W/(m·K) |
| Vnitřní stěny | 0,024 | Dřevo |
| Vnější izolace stěn | 0,07 | Minerální vlna |
| Strop (podhled) | 0,101 | Guttafol 135 (textil) |
| Stropní izolace | 0,21 | Minerální vlna, λ=0,038 W/(m·K) |

Celková tloušťka obvodové stěny (včetně izolace) je cca **94 mm**. Světlá výška **1,99 m** je nízká, ale běžná pro mobilní tiny houses.

---

### 3. Rozložení místnosti (půdorys)

Místnost je rozdělena do čtyř zón podle umístění klíčových prvků:

| Zóna | Poloha (souřadnice X, Y) | Prvky |
|------|--------------------------|-------|
| **Severozápadní roh** | X ~0–0,7 m, Y ~0–0,8 m | Hlavní stůl (jídelní/pracovní) |
| **Jihozápadní roh** | X ~2,5–4,5 m, Y ~0–0,97 m | Postel (2,0×0,97 m) |
| **Jihovýchodní roh** | X ~3,99–4,42 m, Y ~1,61–1,92 m | Kamna na dřevo (Priti Mini) |
| **Východní stěna (celá délka)** | X ~1,5–3,8 m, Y ~1,76–2,0 m | Energetický hub – baterie, invertor, pracovní deska, police |
| **Severovýchodní roh** | X ~0,9–2,0 m? | Vstupní otvor (dveře) – šířka 1,65 m |

**Poznámka:** Východní stěna je téměř celá využita pro technické zázemí – sekundární baterie (100 Ah), hlavní bateriová sestava (630 Ah), police, pracovní deska a hybridní invertor.

---

### 4. Energetický systém („energy hub“)

#### 4.1 Hlavní baterie (LiFePO₄)
- **Název:** `LFP battery DIY 630 Ah Cornex 24V 2S2P 16 pcs cells`
- **Rozměry:** 1,146 × 0,172 × 0,205 m (délka × šířka × výška)
- **Umístění:** X 1,785–2,931 m, Y 1,828–2,000 m, Z 0,525–0,730 m
- **Kapacita:** 630 Ah při 24 V → **~15,1 kWh** (teoreticky)
- **Články:** 16× 314 Ah (3,2 V) – z JSON je patrné, že jsou seskupeny pod jedním rodičovským uzlem a každý článek má vlastní bounding box.

#### 4.2 Sekundární baterie
- **Název:** `24V gokwh 100ah lfp battery`
- **Rozměry:** 0,525 × 0,240 × 0,220 m
- **Umístění:** X 2,055–2,580 m, Y 1,760–2,000 m, Z 0,000–0,220 m
- **Kapacita:** 100 Ah → **~2,4 kWh**

#### 4.3 Hybridní invertor
- **Název:** `HVM3.2H_hybrid_invertor` (3,2 kW)
- **Rozměry:** 0,270 × 0,100 × 0,350 m
- **Umístění:** X 1,790–2,060 m, Y 1,900–2,000 m, Z 1,075–1,425 m (nad pracovní deskou)

#### 4.4 Pracovní deska a police
- **Police pod baterie:** `LFP_main_base` – rozměr 2,087×0,240×0,035 m, ve výšce 0,490 m
- **Pracovní deska:** `worktop_user` – rozměr 2,260×0,200×0,050 m, ve výšce 0,745 m (vhodná jako kuchyňská linka)

**Celková kapacita úložiště:** cca **17,5 kWh** – pro malý dům velmi solidní, umožňuje několik dní autonomie při průměrné spotřebě ~2–3 kWh/den.

---

### 5. Tepelné zdroje a vytápění

- **Kamna na dřevo:** `stove_main` (Priti Mini) – výkon není uveden, ale dle rozměrů (0,431×0,312×0,680 m) se jedná o malá kamna vhodná pro prostor do 20 m³. Umístěna v jihovýchodním rohu, cca 1 m od postele (bezpečnostní odstup?).
- **Izolace:** Minerální vlna 7 cm na stěnách (U≈0,54 W/m²K) – na českou zimu podprůměrná. Strop 21 cm a podlaha 10 cm jsou lepší.
- **Okna:** Troje dvojokna (bez rámu v modelu) – jižní okno 1,136×1,150 m, dvě západní okna ~1,25×1,25 m. Celková plocha oken cca **4,5 m²** – vysoký podíl prosklení (50 % jižní stěny), přínos pro pasivní solární zisky, ale i tepelné ztráty v noci.

---

### 6. Světlost a výhledy

- **Jižní okno** – umístěno v ose X≈4,66 m (jižní stěna), tedy naproti posteli. Postel leží těsně u jižní stěny, okno je nad ní/v její blízkosti – přirozené světlo na spaní, ale možné oslňování.
- **Západní okno jižní** (sleeping zone) – X≈3,24–4,50 m, Y≈-0,09 m – také nad postelí (postel sahá do X=4,495 m).
- **Západní okno severní** (dining zone) – X≈0,33–1,58 m – nad stolem.
- **Východní stěna** – nemá okna, pouze vstupní otvor (dveře) široký 1,65 m, který propouští světlo jen při otevření.

---

### 7. Bezpečnost a ergonomie – identifikované nedostatky

Na základě prostorových dat lze konstatovat:

| Problém | Zdůvodnění |
|---------|-------------|
| **Nízký strop (1,99 m)** | Omezuje pohyb vyšších osob, znemožňuje patrovou postel nebo horní skříňky. |
| **Kamna blízko postele** | Vzdálenost mezi kamny (X 3,99–4,42, Y 1,61–1,92) a postelí (X 2,50–4,50, Y 0–0,97) – nejkratší vzdušná vzdálenost cca **0,7 m**. Chybí nehořlavá zástěna, riziko sálavého tepla na lůžkoviny. |
| **Baterie pod dřevěnou deskou** | LFP články jsou v těsné blízkosti hořlavého materiálu (dřevěná pracovní deska, police). Žádný protipožární oddělovací prvek. |
| **Chybějící větrání bateriového prostoru** | Při rychlém nabíjení/vybíjení může docházet k zahřívání. V JSON není žádný ventilační otvor ani odstup od stěny. |
| **Nedostatečná izolace stěn** | 7 cm minerální vlny v českých podmínkách (zimní teploty -15 °C) vede k vysokým tepelným ztrátám a riziku kondenzace na vnitřním povrchu. |
| **Vstupní otvor bez zádveří** | Přímý vstup z venkovního prostředí – tepelné mosty, průvan. |

---

### 8. Návrhy na zlepšení (bez ohledu na předchozí konverzaci)

Na základě výhradně dat z JSON navrhuji následující změny (technicky i dispozičně realizovatelné):

#### 8.1 Zvýšení bezpečnosti energetického hubu
- **Přidat nehořlavou desku (cementotříska, sádrokarton) mezi baterie a dřevěnou pracovní desku.**
- **Namontovat pasivní nebo aktivní větrání** do prostoru baterií (např. malý 12V ventilátor spínaný teplotou).
- **Oddělit invertor od baterií** alespoň 10 cm vzduchové mezery (nyní jsou invertor nad deskou, baterie pod ní – v pořádku, ale chybí průduchy).

#### 8.2 Úprava dispozice postele (bez přesunu na východní stěnu)
- **Zvednout postel na 40–50 cm vysoké nohy** a využít prostor pod ní pro zásuvky na sezónní oblečení nebo pro sklápěcí stůl. (Nyní je postel vysoká jen 18 cm – pod ní se nic nevejde.)
- **Přesunout postel o 20 cm dále od kamen** směrem k západní stěně (současná poloha: X 2,495–4,495; posunout na X 2,0–4,0) – získá se odstup od kamen a uvolní se místo u jižního okna pro květiny nebo židli.

#### 8.3 Zlepšení tepelné pohody
- **Dodatečné zateplení severní a západní stěny zevnitř** 5 cm PIR desky (λ=0,022) – ztráta vnitřního rozměru jen 5 cm, ale výrazné snížení tepelných ztrát.
- **Těžké termální závěsy na všechna okna** – omezí noční prochladnutí.
- **Utěsnit vstupní dveře** (pěnové pásky, práh) a přidat závěs nebo druhé dveře (provizorní zádveří).

#### 8.4 Kamna na dřevo – bezpečnostní opatření (ponechání na místě)
- **Nehořlavá podložka pod kamny** s přesahem 300 mm na každou stranu (ocel + minerální deska).
- **Tepelný štít na stěně za kamny** (vzdálenost od dřevěné stěny dle JSON není explicitní, ale východní stěna je na Y=2,0, kamna sahají do Y=1,924 – tedy mezera ~76 mm – to je málo. Nutný štít s odstupem 20 mm).
- **Detektor CO** na stropě v blízkosti kamen.

#### 8.5 Větrání s rekuperací
- **Instalace malé rekuperační jednotky** (např. do okna nebo do stěny) – průtok 20–30 m³/h. V zimě ušetří teplo oproti větrání okny.

---

### 9. Celkové zhodnocení JSON jako podkladu pro analýzu

Formát je **velmi kvalitní** pro LLM zpracování:
- Jednotky v metrech (přehledné).
- Každý uzel má `center_xyz` a `size_xyz` – není nutné počítat bounding box z extrémů.
- Tagy (`energy_nodes`, `walls`, `insulation` atd.) umožňují filtraci.
- Hierarchie (`hierarchy_path`) zachovává grouping (např. články baterie pod hlavní baterií).

**Drobné nedostatky:**
- Chybí informace o materiálových vlastnostech (kromě λ u izolace v názvu).
- Některé názvy obsahují diakritiku a mezery (pro strojové zpracování nevadí).
- U kamen chybí výkon (kW) a emisní třída.

**Přínos pro autora:** JSON umožňuje přesnou prostorovou analýzu, detekci kolizí (žádné nenalezeny – vše se vejde) a identifikaci rizik (blízkost kamen a postele, chybějící větrání baterií).

---

### Závěr

Místnost 2 je kompaktní, dobře vybavený tiny house s výkonným off-grid systémem (17,5 kWh baterie, 3,2 kW invertor, kamna na dřevo). Hlavní nedostatky spočívají v **bezpečnosti umístění baterií a kamen**, v **nedostatečné tepelné izolaci stěn** a v **absenci řízeného větrání**. Všechna uvedená doporučení jsou realizovatelná bez zásadních stavebních úprav a zvýší bezpečnost, komfort i energetickou účinnost.
