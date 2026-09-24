# SketchUp → LLM pipeline: CAD to structured JSON with metadata

Tento nástroj vám pomůže přeměnit váš 3D model ze SketchUpu na **strukturovaný JSON soubor**, který může zpracovávat umělá inteligence (LLM), simulace nebo vlastní skripty.  
Pracuje přímo v **Ruby Console** SketchUpu – nepotřebujete instalovat žádné rozšíření, stačí pár řádků kódu.

> **Pro koho je tento README určen?**  
> Pro uživatele SketchUpu, kteří **nejsou programátoři**. Ruby neznáte? Nevadí. Postupujeme krok za krokem.

---

## Obsah

1. [Co tato pipeline dělá](#co-tato-pipeline-dělá)
2. [Co budete potřebovat](#co-budete-potřebovat)
3. [Rychlý start – první metadata za 2 minuty](#rychlý-start--první-metadata-za-2-minuty)
4. [Jak na to – podrobný návod](#jak-na-to--podrobný-návod)
   - 4.1 Otevření Ruby Console
   - 4.2 Výběr objektu
   - 4.3 Přidání první vlastnosti (atributu)
   - 4.4 Ověření, že metadata fungují
   - 4.5 Export jednoho objektu do JSON
5. [Nejčastější problémy a jejich řešení (roadblocks)](#nejčastější-problémy-a-jejich-řešení-roadblocks)
   - 5.1 „Po restartu SketchUpu zmizela metadata“
   - 5.2 „Chyba `undefined local variable or method 'ent'`“
   - 5.3 „`set_attribute` nefunguje – objekt je hrana nebo bod“
   - 5.4 „Soubor se neuložil – cesta C:/temp/ neexistuje“
6. [Jak pokračovat dál – hromadné zpracování a export celého modelu](#jak-pokračovat-dál--hromadné-zpracování-a-export-celého-modelu)
7. [Ukázka výstupního JSON](#ukázka-výstupního-json)
8. [Užitečné zdroje a další plány](#užitečné-zdroje-a-další-plány)

---

## Co tato pipeline dělá

1. Umožňuje vám **přidat k libovolnému objektu** (komponenta, skupina, plocha) vlastní popisná data – například:
   - `energy/type = "battery"`
   - `energy/voltage = 24`
   - `safety/fire_risk = true`
   - `zone/temp_min_c = 15`
2. Tato data jsou **trvale uložena** v souboru `.skp`.
3. Poté můžete vybraný objekt nebo **celý model vyexportovat do JSON** – přehledného formátu, kterému rozumí každý programovací jazyk i LLM.
4. JSON můžete použít pro analýzu energetické bilance, bezpečnostní audity, nebo jako vstup pro AI asistenta.

---

## Co budete potřebovat

- **SketchUp 2024** (funguje i starší verze, ale cesta k Ruby Console se může lišit)
- **Model** (soubor `.skp`), ve kterém chcete metadata spravovat
- Žádné předchozí znalosti Ruby – vše si napíšeme společně

---

## Rychlý start – první metadata za 2 minuty

1. Otevřete svůj model ve SketchUpu.
2. Stiskněte `Alt + F11` (Windows) nebo `Option + F11` (Mac) – otevře se **Ruby Console** (malé okno, kam píšeme příkazy).
3. **Klikněte myší** na libovolnou komponentu (např. baterii, postel, stěnu).
4. Zkopírujte a vložte do konzole následující řádky (po každém stiskněte Enter):

```ruby
ent = Sketchup.active_model.selection.first
ent.set_attribute("energy", "type", "battery")
puts ent.get_attribute("energy", "type")
Konzole vypíše battery – gratuluji, právě jste uložili první metadata!

Jak na to – podrobný návod
4.1 Otevření Ruby Console
Windows: Alt + F11 nebo menu Rozšíření → Vývojové nástroje → Ruby Console

Mac: Option + F11 nebo Extensions → Developer Tools → Ruby Console

4.2 Výběr objektu
Klikněte na objekt myší (komponenta, skupina nebo plocha).

Nyní v konzoli napište:

ruby
ent = Sketchup.active_model.selection.first
Tím uložíte odkaz na vybraný objekt do proměnné ent. (Pokud nic nevyberete, ent bude nil – a další příkazy selžou.)

4.3 Přidání první vlastnosti (atributu)
ruby
ent.set_attribute("energy", "type", "battery")
"energy" = název slovníku (jako kategorie)

"type" = klíč

"battery" = hodnota (může být číslo, pravda/nepravda, text)

Můžete přidat více atributů do stejného slovníku:

ruby
ent.set_attribute("energy", "voltage", 24)
ent.set_attribute("energy", "capacity_ah", 100)
4.4 Ověření, že metadata fungují
ruby
puts ent.get_attribute("energy", "type")
puts ent.get_attribute("energy", "voltage")
Pokud vidíte battery a 24, je vše v pořádku.

Chcete-li zobrazit všechna metadata objektu:

ruby
ent.attribute_dictionaries.each do |dict|
  puts "=== #{dict.name} ==="
  dict.each_pair { |k, v| puts "#{k}: #{v}" }
end
4.5 Export jednoho objektu do JSON
Zkopírujte celý tento blok do konzole (od require 'json' až po export_selected):

ruby
require 'json'

def export_selected
  sel = Sketchup.active_model.selection
  if sel.empty?
    puts "Nic není vybráno"
    return
  end
  data = []
  sel.each do |e|
    bbox = e.bounds
    center = bbox.center
    metadata = {}
    e.attribute_dictionaries&.each do |dict|
      h = {}
      dict.each_pair { |k, v| h[k] = v }
      metadata[dict.name] = h unless h.empty?
    end
    node = {
      id: e.persistent_id,
      name: e.name,
      type: e.typename,
      definition: e.definition.name,
      tag: e.layer.name,
      spatial: {
        center: [center.x.to_f, center.y.to_f, center.z.to_f],
        size: [bbox.width.to_f, bbox.depth.to_f, bbox.height.to_f]
      },
      metadata: metadata
    }
    data << node
  end
  json_str = JSON.pretty_generate(data)
  puts json_str
  File.write("C:/temp/selected_export.json", json_str)
  puts "Export uložen do C:/temp/selected_export.json"
end

export_selected
Po spuštění uvidíte JSON v konzoli a zároveň se uloží soubor na disk.
(Pokud složka C:\temp neexistuje, přečtěte si kapitolu 5.4.)

Nejčastější problémy a jejich řešení (roadblocks)
5.1 „Po restartu SketchUpu zmizela metadata“
Popis: Uložil jsem model, zavřel SketchUp, znovu otevřel, ale když zkouším ent.get_attribute(...), nic nevidím.

Pravda: Metadata nezmizela. Jsou stále v modelu. Problém je v tom, že proměnná ent (odkaz na objekt) po restartu neexistuje. Stejně jako když zavřete poznámkový blok, zapomenete, co jste měli napsáno na papírku.

Řešení: Znovu vyberte objekt myší a napište:

ruby
ent = Sketchup.active_model.selection.first
Pak opět funguje ent.get_attribute(...).
Můžete také objekt najít podle jeho jedinečného ID (které znáte z JSON):

ruby
ent = Sketchup.active_model.entities.find { |e| e.persistent_id == 3369 }
5.2 „Chyba undefined local variable or method 'ent'“
Popis: Napíšu ent.attribute_dictionaries a konzole hlásí NameError.

Příčina: Proměnná ent nikdy nebyla definována – buď jste ji nenastavili, nebo jste restartovali SketchUp.

Řešení: Viz bod 5.1 – znovu přiřaďte ent pomocí výběru myší.

5.3 „set_attribute nefunguje – objekt je hrana nebo bod“
Popis: Když spustím ent.set_attribute(...), dostanu chybu undefined method 'set_attribute' for #<Sketchup::Edge...>.

Příčina: Atributy lze ukládat pouze ke skupinám (Group), komponentám (ComponentInstance) a plochám (Face). Hrany, kóty, pomocné čáry metadata nepodporují.

Řešení: Vyberte jiný typ objektu. Pokud potřebujete metadata k hraně, musíte ji nejprve převést na skupinu nebo komponentu.

5.4 „Soubor se neuložil – cesta C:/temp/ neexistuje“
Popis: Export skončí chybou No such file or directory @ rb_sysopen - C:/temp/selected_export.json.

Příčina: Složka C:\temp na vašem počítači neexistuje.

Řešení:

Vytvořte složku C:\temp ručně (pravým tlačítkem na ploše / nová složka).

Nebo změňte cestu v kódu na něco, co existuje, například na plochu:
File.write(Dir.home + "/Desktop/export.json", json_str)

Nebo použijte dočasnou složku:
File.write(Dir.tmpdir + "/export.json", json_str)

Jak pokračovat dál – hromadné zpracování a export celého modelu
Až zvládnete základ, můžete:

Přidat vlastní funkci pro rychlé tagování (např. tag_battery – viz handoff JSON).

Otagovat všechny komponenty stejné definice najednou.

Exportovat celý model (všechny komponenty) pomocí skriptu LLM.export_json z pluginu.

Validovat data – zda každá baterie má capacity_ah, každá stěna u_value atd.

Všechny hotové skripty a podrobné vysvětlení najdete v souboru handoff.json (anglicky/česky mix, technický popis).

Ukázka výstupního JSON
json
[
  {
    "id": 3369,
    "name": "LFP battery DIY 630 Ah",
    "type": "ComponentInstance",
    "definition": "Main_battery_of_the_house",
    "tag": "energy_nodes",
    "spatial": {
      "center": [92.83, 75.35, 24.70],
      "size": [45.12, 8.07, 6.77]
    },
    "metadata": {
      "energy": {
        "type": "battery",
        "voltage": 24,
        "capacity_ah": 628
      },
      "safety": {
        "fire_risk": true,
        "min_distance_m": 0.5
      },
      "thermal": {
        "type": "battery_cell",
        "heat_loss_w": 0.03
      },
      "zone": {
        "type": "energy_storage",
        "temp_min_c": 5,
        "temp_max_c": 35
      }
    }
  }
]
Tento JSON můžete poslat přímo LLM (např. ChatGPT, Claude) a ptát se:
„Které objekty mají fire_risk = true?“ nebo „Spočítej celkovou kapacitu baterií.“

Užitečné zdroje a další plány
Handoff JSON – kompletní technický výcvikový manuál (včetně abstrakcí, chybových vzorů a fází učení).

GitHub repozitář – bude obsahovat složku /scripts s hotovými skripty pro export, tagování a validaci.

Plánované rozšíření:

Automatické přiřazování metadat podle názvu vrstvy nebo materiálu.

Připojení k Python backendu pro přímé LLM dotazy.

Generování reportů (PDF, Excel) z metadat.

Nyní víte dost na to, abyste začali proměnit svůj SketchUp model na inteligentní data. Pokud narazíte na problém, vraťte se k tabulce „Nejčastější problémy“.
Hodně štěstí!
