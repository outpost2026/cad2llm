# Code review CAD2LLM - stav, kontrakty, architektura

**Datum:** 2026-11-01
**Rozsah:** `_github/cad2llm` (commit b3ea6ff), vcetne navazane pipeline `Documents/SU2024/cad2llm_pipeline`
**Metoda:** cteni vsech modulu, porovnani slovniku na trech mistech, beh testu, mereni na zivem modelu a na 19 exportech
**Jistota zaveru:** P>0.9 u vsech cisel (vsechna jsou zmerena, ne odhadnuta)

---

## 1. Verdikt

Kod je ve srovnatelne lepsim stavu, nez odpovida jeho veku a poctu vrstev:
determinismus je realny, zapis do modelu je bezpecny, testy existuji a prochazeji.

Slaba mista jsou presne dve a obe jsou **kontraktni, ne implementacni**:

1. **Existuji dva slovniky roli, ktere se potkavaji jen zcasti.** Zapisovaci strana
   (gate `registry.json`, 29 roli) a ctecí strana (`tools/cad2llm_rules.json`, 23 roli)
   sdileji 13 jmen. Po uzavrene smycce na pergoře ma 21 z 25 entit roli, kterou ctecí
   strana nezna, a v5 je tise klasifikuje jako `DESIGN_DECISION`.
2. **Kontrakt `annotate_plan/v1` ma ctyri producenty a tri validatory, ale jen dva
   z nich jsou vynucene.** JSON Schema existuje a nikdo ji nevaliduje.

Oboji je opravitelne bez prekopani architektury. Zbytek je doladeni.

---

## 2. Mapa repa

| Vrstva | Soubory | Radky | Stav |
|---|---|---|---|
| Exportér (Ruby, SketchUp) | `src/cad2llm_exporter/lib/{exporter,annotate,bridge}.rb` | 1619 | hotove, otestovane |
| Bridge protokol v1 | `bridge.rb` + `tools/cad2llm_bridge.py` | 1118 | hotove, otestovane |
| C++ neni, Python pipeline | `tools/cad2llm*.py` | 9 modulu | hotove |
| Schema a kontrakty | `docs/schema/*.json`, `docs/*.md` | 11 dokumentu | dokumentace, cast nezavazna |
| Testy Python | `tests/test_*.py` | 8 souboru, 133 testu | zelene |
| Testy Ruby | `spec/test_*.rb` + stub | 3 soubory, 298 checku | zelene |
| Data | `examples/` 10 exportu, 1.2 MB | - | sum, ale velky |
| Distributed artefakt | `releases/cad2llm_exporter.rbz` | 6 souboru | **bajtove shodny se src** |

Overeno: vsech 6 souboru v RBZ ma shodny SHA-256 se `src/`. Repo 2.7 MB, 37 commitu.

**Chybi:** zadny CI. Testy existuji, ale nikdo je nespousti automaticky.

---

## 3. Kontrakty a kde se potkavaji

| Kontrakt | Verze | Producent | Konzument | Vynuceni |
|---|---|---|---|---|
| `cad2llm/exporter/v4` | 4.0 | `exporter.rb` | v5, slim, gate | `core.validate` (Python) |
| `cad2llm/geometry/v1` | 1.0 | `exporter.rb` | nikdo v repu | zadne |
| `cad2llm/semantic-patch/v1` | 1.0 | `gate.py` | `cad2llm_patch.py` | `patch_to_plan` (striktni) |
| `cad2llm/annotate_plan/v1` | 1.0 | `derive.py`, `stamp.py`, `patch.py`, `docs/plans/*.json` | `annotate.rb` | **Ruby `normalize_plan` + Python `validate_plan`** |
| `cad2llm/annotate_plan/v1` schema | draft 2020-12 | `docs/schema/` | nikdo | **nevynuceno** |
| `cad2llm/vocab/v1` (rules.json) | 1.0 | rucne | v5, derive, vocab | `vocab.load_vocab` + `merge_vocab` |
| `cad2llm/bridge/v1` | 1.0 | `bridge.rb` | `cad2llm_bridge.py` | protokol + token |
| `cad2llm/gate-index/v1` | 1.0 | `gate.py` | clovek | zadne |
| `cad2llm/slim/v1` | 1.0 | `cad2llm_slim.py` | LLM | `slim_document` |

**Tok dat:**

```
SketchUp --export--> v4 JSON --v5--> semantika + brief
                          |
                          +--gate--> gate.json + patch.json + review.json
                                                   |
                                     cad2llm_patch.py (jediny preklad)
                                                   |
                                          annotate_plan/v1
                                                   |
                                     bridge.rb --> zpet do .skp
```

Smycka je uzavrena mechanicky. Neni uzavrena semanticky - viz D1.

---

## 4. Defekty

### D1 - Dva slovniky roli (BLOK)

Severity: **blokujici pro dustojnost smycky.** Jistota P>0.95.

Porovnani zdroju:

| Zdroj | Roli | Pouziva |
|---|---|---|
| `tools/cad2llm_rules.json` -> `vocab.roles` | 23 | cte v5, derive, slim, core.validate |
| `registry.json` -> definitions/tag_map/reference_roles | 29 | pise gate do modelu |

Prunik je 13 roli. **10 roli existuje jen na ctecí strane, 16 jen na zapisovaci.**

Mereno na korpusu (19 patchu, 624 prikazu s `cad2llm.role`):

- 60 prikazu (9.6 %) navrhuje roli, kterou v5 nezna
- z 29 roli, ktere se v patchich vubec objevily, je 12 mimo slovnik
- v celem `registry.json` je 16 roli mimo slovnik:
  `battens`, `bed_frame`, `bed_worktop`, `brace`, `column`, `fastener`,
  `foundation`, `junk_component`, `leg_gap`, `legs`, `plan_reference`,
  `purlin`, `rafter`, `reference`, `seat_area`, `seat_plank`

Mereno na zivem modelu (pergola po zapisu):

```
core.validate  -> UNKNOWN_ROLE 21/25
v5.classify    -> DESIGN_DECISION 21/25   (sloupy, krokve, vzpery, vruty)
```

Dusledek neni chybova hlaska, ale ticha degradace: `v5.classify`
(`tools/cad2llm_v5.py:297`) vezme `cad2llm.role` z metadat doslova a dal
rozhoduje podle `STRUCTURAL = {wall, insulation, floor, ceiling, roof, beam, truss}`
(`cad2llm_v5.py:27`). `column` v teto mnozine neni, takze nosny sloup skonci jako
`DESIGN_DECISION`. Vsechny relacni kontroly (CONNECTS, podpory, obal budovy)
takovou entitu ignoruji.

Dva podruzene nalezy ze stejneho korene:

- `system` ma 8 hodnot v registry, z toho 4 mimo slovnik
  (`structure`, `connector`, `equipment`, `non_construction` vs. `envelope`, `energy`, `furniture`, `space`).
- Projektove specificke role (`bed_frame`, `seat_plank`, `legs`, `battens`)
  patri do **overlay`, ne do core slovniku. Overlay uz existuje
  (`cad2llm_vocab.merge_vocab`, `docs/VOCAB_OVERLAY.md`) a je nevyuzity.

### D2 - Zapisujeme pole, ktera nikdo necte, a neposilame to, ktere ctel chce (VAZNE)

Jistota P>0.95.

- `cad2llm.system` se zapisuje (gate), ale **zadny modul v repu ho necte.**
  `grep` na `cad.get("system")` v v5 vraci 0. Neni to chyba sama o sobe
  (field muze byt pro IFC most), ale neni to nikde receno.
- `cad2llm.zone` se **nezapisuje vubec** - `registry.json` zonu nepouziva.
  Po zapisu ma pergola 20/25 entit zonu `unknown`, protoze
  `ROLE_ZONE_FALLBACK` (`cad2llm_v5.py:64`) pro role `column`/`rafter`/`fastener`
  nema mapovani. Informace, kterou v5 umi odvodit u jinych roli, se zapisem ztraci.

Tohle je stejna trida chyby jako drive opraveny mrtvy `cad2llm.ifc_type`
v `gate.py`: pole projde, ale nikam nevede.

### D3 - Resolver vetuje platny persistent_id (VAZNE)

`src/cad2llm_exporter/lib/annotate.rb:230`

```ruby
if strategy != :path && target.key?("hierarchy_path")
  out = out.select { |e| e["path"] == want || ... }
end
```

Filtr se aplikuje i tehdy, kdyz shoda prisla z `persistent_id`. Kdyz drivejsi
operace v teze davce prejmenuje rodice, cesta prestane platit a operace skonci
`NO_MATCH_AFTER_FILTER` - presne to se stalo u 3 z 87 operaci na pergoře.

`persistent_id` je v modelu unikatni (`walk()` ho cte z `entity.persistent_id`).
Filtr nema co resit. Prace je obejita v prekladu (posila se jen pid), root cause
zustava a kdokoliv posle plan s cestou narazi znovu.

### D4 - `junk_component` propadne do DESIGN_DECISION (VAZNE)

`tools/cad2llm_v5.py:147` a znovu `:327`

```python
if role in {"void", "marker", "boundary"}:
    return "ARTIFACT"
```

`junk_component` (knihovni Teddy) v teto mnozine neni, takze se dostane do
odvozenin jako designove rozhodnuti. Doklad: `junk_teddy` po zapisu ma
`intent=DESIGN_DECISION`. `ARTIFACT_ROLES` na radku 379 ma stejnou diru.

### D5 - Tri validace jednoho kontraktu, zadna zavazna (VAZNE)

| Implementace | Kde | Co kontroluje |
|---|---|---|
| Ruby `normalize_plan` | `annotate.rb:79` | schema, ops, target.kind, transaction |
| Python `validate_plan` | `cad2llm_patch.py:195` | schema, ops, **nazvy klicu**, target keys, SU_ prefix, max 2000 |
| JSON Schema | `docs/schema/annotate_plan.schema.json` | vse vyse + typy hodnot, `additionalProperties: false` |

Ruby verze **neodmita neznama pole** v operaci (schema je zakazuje), nekontroluje
`maxItems: 2000` a nevaliduje typy hodnot. Python lint je prisnejsi nez Ruby -
takze plan, ktery projde Ruby, nemusi projit Pythonem a naopak. Schema se
nevaliduje nikde: `jsonschema` je pouzita jen v `tests/test_cad2llm_v5.py:344`
pro v5 dokument, ne pro plany.

Prakticky dusledek: kdyz se zmeni op mnozina, je potreba zmenit tri mista a
jedno z nich (schema) se zapomene. Uz dnes `OPS` v Ruby a seznam v
`validate_plan` jsou dva rucne udrzovane seznamy.

### D6 - Zona `tech` neni ve slovniku zon (STREDNI)

`tools/cad2llm_v5.py:69-70`

```python
"inverter": "tech",
"appliance": "tech",
```

Slovnik zon (`rules.json`) zna `work, sleep, dining, entry, energy, envelope,
storage, space, unknown`. `tech` mezi nimi neni. Overeno:

```
v5.classify("inverter") -> zone 'tech'
v5.classify("menic_01") -> zone 'tech'
```

`core.validate` to oznaci jako `UNKNOWN_ZONE` (warn), takze to nikoho nezastavi.
Spravna hodnota je `energy`.

### D7 - `truss` je mrtvy clen STRUCTURAL (NIZKE)

`cad2llm_v5.py:27` obsahuje `truss`, ale zadne pravidlo v `ROLE_RULES` roli
`truss` nevygeneruje (text "truss" mapuje na `beam`, radek 38) a ve slovniku
`truss` neni. Mrtva vetev.

### D8 - `ifc.schema` a `ifc.source` se zapisuji, nikdo je necte (NIZKE)

`tools/cad2llm_derive.py:121-122` zapisuje `ifc.schema = "IFC 2x3"` a
`ifc.source = "definition"`. Export je vrati zpet (`metadata_of` dumpuje vse),
ale zadny konzument je nevyhodnocuje. Budto je to priprava na IFC export
(pak to ma byt v dokumentaci), nebo dalsi mrtve pole.

### D9 - Zadny CI (STREDNI)

133 Python testu a 298 Ruby checku existuje a prochazi, ale neni `.github/workflows`,
`Makefile` ani skript, ktery je spoji. Kazdy beh je rucni rozhodnuti. U repa,
ktere ma 9 Python modulu a 3 jazykove vrstvy, je to nejvetsi provozni riziko
po D1.

### D10 - Pipeline lezi mimo repo (STREDNI)

`gate.py` (1148 radku) a `registry.json` (slovnik, ktery ridi zapis) jsou
v `Documents/SU2024/cad2llm_pipeline/`, tedy mimo `_github` a mimo verzovani.
Repozitar, ktery tvrdi, ze je zdroj pravdy, nezahrnuje pulku zapisovaci cesty.
Slovnik se tak muze zmenit bez commitu a nikdo to nezjisti.

### D11 - Sum v repu (NIZKE)

- `examples/` 10 exportu, 1.2 MB ze 2.7 MB repa
- `docs/plans/annotate_mistnost_2_v1_1.json` 6404 radku generovaneho planu
- `.ai_state.json` 633 radku agentniho stavu
- `index_cad2llm.md` 2453 radku

Neni to chyba, ale je to vetsi cast repa nez kod.

---

## 5. Co funguje (pros)

1. **Determinismus je skutecny, ne deklarovany.** Gate je idempotentni
   (59/59 bit-identickych souboru pri stejnem `--stamp`), v5 ma golden master,
   bridge ma snapshot a rollback pri geometrickem driftu.
2. **Zapis do modelu je bezpecny.** Lokalni TCP s 128bitovym tokenem,
   limit 1 MB na pozadavek, vse na hlavnim vlakne v undo transakci,
   pred zapisem snapshot, po zapisu kontrola geometrie, pri odchylce rollback.
   Pri ostrém testu: 87/87 operaci, drift 0.
3. **Preklad patch -> plan je striktni.** Neznama pole, `pending_review`,
   target bez pid = chyba, ne varovani (`cad2llm_patch.py:106-140`).
   Tohle je nejlepsi cast repa: jedine misto, kde se obe schemata potkavaji.
4. **Lidske rozhodnuti je soubor, ne chat.** `review.json` s jednou polozkou
   na entitu, `decision: approve/reject/value`. Reprodukovatelne, diffovatelne.
5. **Slovnik je deklarativni a ma overlay.** `vocab.load_vocab` + `merge_vocab`
   odmitne tichou redefinici role a overi, ze tag mapuje na existujici roli.
   Architektura je spravna - jen se overlay nevyuziva (D1).
6. **RBZ je bajtove shodny se src.** Overeno SHA-256 u vsech 6 souboru.
   Zadny drift mezi zdrojem a distribuovanym artefaktem.
7. **Testy pokryvaji vsechny netrivialni moduly** krome CLI a `verify`.
   133 Python + 298 Ruby checku, vse zelene.

---

## 6. Co je slabe (cons)

1. **Slovnik neni jeden.** D1. Toto je jedina vec, ktera brani tomu, aby se
   z "funguje na pergoře" stalo "funguje obecne".
2. **Kontrakt je rozmnozeny.** D5. Schema bez vynuceni je dokumentace, ne kontrakt.
3. **Zapis je siroky, cteni uzke.** D2. Gate vi o strukturnich rolich, v5 vi
   o designovych. Nikdo necte `system`.
4. **Chybi integracni beh.** Smycka byla overena na jednom modelu rucne.
   Neni skript, ktery by ji pustil pres 19 exportu a selhal, kdyz se rozbije.
5. **Pipeline mimo repo.** D10.
6. **Zadny CI.** D9.
7. **Nekonzistentni uroven dokumentace.** `PROTOCOL_bridge_v1.md` a
   `SCHEMA_v4.md` jsou presne, `GEOMETRY_v1.md` popisuje kontrakt, ktery nikdo
   nekonzumuje.

---

## 7. Architektura po vrstvach

| Vrstva | Verdikt |
|---|---|
| Exportér (SketchUp -> JSON) | **OK.** Jeden smer, zadne rozhodovani, atributy pruchozi. |
| Bridge (JSON -> SketchUp) | **OK.** Bezpecny, transakcni, rollback. Jedna chyba v resolveru (D3). |
| Semantika v5 | **OK uvnitr, rozbita na vstupu.** Pravidla konzistentni, ale dostava role, kterym nerozumi (D1). |
| Preklad patch -> plan | **OK.** Nejlepsi cast. Striktni, testovany. |
| Gate (rozhodovani) | **Funkcne OK, kontraktne mimo.** Vlastni slovnik, mimo repo (D1, D10). |
| Kontrakty a schema | **Nekonzistentni.** Tri validace, jedna nevynucena (D5). |
| Testy a provoz | **Dobre pokryti, zadna automatizace** (D9). |

Souhrn: architektura je spravna. Vrstvy jsou oddelene, tok je jednosmerny,
kazda vrstva ma jasny vstup a vystup. Co chybi, neni navrh, ale **uzavreni
slovniku a vynuceni kontraktu**.

---

## 8. Co implementovat (prioritizovane)

### P0 - pred dalsim modelem

1. **Sjednotit slovnik roli.** Rozsirit `rules.json` o strukturni role
   (`column`, `purlin`, `rafter`, `foundation`, `brace`, `fastener`) a doplnit
   je do `STRUCTURAL`. Projektove role (`bed_frame`, `seat_plank`, `legs`,
   `battens`) presunout do projektoveho overlay.
   *Kriteria hotovo:* `core.validate` na pergoře vraci 0 `UNKNOWN_ROLE`;
   v5 klasifikuje sloupy jako `CONSTRUCTION`.
2. **Pridat conformance check do gate:** kazda role a kazdy system, ktery gate
   navrhne, musi byt ve slovniku. Kdyz neni, gate selze (ne warning).
   *Kriteria hotovo:* pusti se na 19 exportech a nic neprojde mimo slovnik.

### P1 - dokoncit smycku

3. **Zapsat zonu.** Budto ji gate odvodi z `vocab.role_info` a posle
   `cad2llm.zone`, nebo ji v5 odvodi z role pres overlay. Dnes se ztraci (D2).
4. **Opravit resolver (D3).** Pri shode podle `persistent_id` preskocit
   path/tag/definition filtr. Test v `spec/test_annotate.rb`:
   plan, ktery prejmenuje rodice a pak diteti nastavi atribut, musi projit.
5. **Opravit `junk_component` (D4).** Doplnit do `ARTIFACT_ROLES` a do obou
   vetvi `classify`.
6. **Opravit zonu `tech` (D6).** Nahradit `energy`.

### P2 - provoz

7. **Vynutit JSON Schema** v Python lintu (`jsonschema` je uz v testech)
   a v Ruby nebo aspon v CI. Odstranit rucne udrzovane seznamy op.
8. **CI workflow:** `python -m unittest discover -s tests` + `ruby spec/run_all.rb`
   + `gate.py --selftest` + schema validace. Jeden prikaz, ktery rekne ano/ne.
9. **Presunout `gate.py` a `registry.json` do `cad2llm/tools/`** (D10).
   Tim se pipeline dostane pod verzovani a code review.

### P3 - rozsireni

10. **Integracni beh pres korpus.** Skript `tools/cad2llm_corpus.py`:
    pro kazdy export gate -> patch -> plan -> dry-run -> (volitelne apply)
    -> re-export -> gate; uspech = zadny novy `pending_review` a zadny `UNKNOWN_ROLE`.
    Tohle je jediny zpusob, jak dokazat, ze smycka funguje obecne, ne jen na pergoře.
11. **Golden fixtures** s ocekavanym vystupem pro 2-3 male modely.
12. **Residualni LLM vrstva** pro entity, kde role neni odvoditelna
    (dnes 3 v pergoře, 7 v house_test01). Deterministicky fallback zustava,
    LLM dostane jen reziduum s explicitnim kontextem.

---

## 9. Co jsem neoveroval

- Chovani v jinych verzich SketchUpu nez 24.0.484.
- IFC export (kontrakt `metadata.ifc` je pripraveny, ale most do IFC neexistuje).
- `cad2llm_verify.py` ma jen indirect test pres bridge (radek 306 testu).
- CLI `tools/cad2llm.py` nema vlastni test; je tenky, ale neotestovany.
- Vykon na modelech vetsich nez 25 entit (pergola) - nejvetsi testovany export ma 152 operaci.

---

## 10. Jedna veta na zaver

Repozitar ma spravnou architekturu a fungujici smycku, ale dve poloviny pipeline
mluvi odlisnymi jazyky o tom, co je "sloup" - dokud se ten slovnik nesjednoti,
bude kazdy dalsi model vypadat jako uspech a chovat se jako chyba.
