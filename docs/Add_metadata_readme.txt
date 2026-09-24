## Posouzení bohatosti metadat z hlediska LLM-ready

### Aktuální stav (z vašeho JSON)

```json
{
  "id": 3348,
  "name": "Bed_01_mattres_with_light_frame",
  "type": "ComponentInstance",
  "definition": "sleep_zone",
  "tag": "bed_m2",
  "spatial": { "center": [137.6, 19.09, 3.54], "size": [78.74, 7.09, 38.19] },
  "metadata": { "SU_InstanceSet": { "Owner": "", "Status": "" } }
}
```

**Co chybí / je nedostatečné pro LLM (např. pro simulaci energetiky, bezpečnosti, zónování):**

| Kategorie | Požadavek | Aktuálně | Stav |
|-----------|-----------|----------|------|
| **Energy** | typ (battery/solar/grid), napětí, kapacita | žádné | ❌ |
| **Thermal** | u_value, tepelná setrvačnost, typ konstrukce | žádné | ❌ |
| **Zone** | typ (sleep/work/energy), požadovaná teplota, vlhkost | jen v `definition="sleep_zone"` – nevyužito jako metadata | ⚠️ částečně (jen název) |
| **Safety** | riziko požáru, minimální vzdálenost, úniková cesta | žádné | ❌ |
| **Instance-specific** | jedinečné vlastnosti (např. tato postel má topení?) | žádné | ❌ |
| **Material** | materiál, hořlavost, emisivita | žádné | ❌ |

**Verdikt:** Model **není LLM-ready**. LLM by nevěděl, zda je postel zdroj tepla, spotřebič, nebo jen nábytek. Metadata jsou prakticky prázdná (jen prázdný slovník `SU_InstanceSet`).

---

## Jak postupovat dále v tvorbě metadat (prakticky v konzoli)

### 1. Ruční přiřazení k vybranému objektu (postel)

Vyberte postel myší, pak do konzole:

```ruby
# Zone metadata
sel = Sketchup.active_model.selection.first
sel.set_attribute("zone", "type", "sleep")
sel.set_attribute("zone", "temp_setpoint_c", 21)
sel.set_attribute("zone", "humidity_target", 50)

# Energy metadata (postel má jen zásuvku, žádná baterie)
sel.set_attribute("energy", "type", "socket_load")
sel.set_attribute("energy", "power_w", 50)  # např. nabíječka telefonu

# Thermal (postel jako těleso)
sel.set_attribute("thermal", "type", "furniture")
sel.set_attribute("thermal", "thermal_mass_kg", 40)

# Safety
sel.set_attribute("safety", "fire_risk", false)
sel.set_attribute("safety", "exit_obstacle", false)
```

Ověření:
```ruby
LLM.get_all_metadata(sel)   # pokud máte funkci z pluginu
# nebo
sel.attribute_dictionaries.each { |d| puts "#{d.name}: #{d.to_h}" }
```

### 2. Vytvoření pomocné funkce pro rychlé přiřazení (do konzole)

Můžete si do konzole nahrát krátkou funkci, která nastaví všechny potřebné atributy na vybraném objektu jedním příkazem:

```ruby
def set_bed_metadata
  sel = Sketchup.active_model.selection
  sel.each do |e|
    e.set_attribute("zone", "type", "sleep")
    e.set_attribute("zone", "temp_setpoint_c", 21)
    e.set_attribute("energy", "type", "socket_load")
    e.set_attribute("energy", "power_w", 50)
    e.set_attribute("thermal", "type", "furniture")
    e.set_attribute("safety", "fire_risk", false)
  end
  puts "Metadata postele nastavena na #{sel.length} objektů"
end

set_bed_metadata
```

### 3. Hromadné přiřazení podle názvu vrstvy (tag)

Vaše postel má `"tag": "bed_m2"`. Můžete projít všechny komponenty s tímto tagem a přiřadit metadata:

```ruby
model = Sketchup.active_model
tag_name = "bed_m2"
target_layer = model.layers[tag_name]

if target_layer
  model.entities.grep(Sketchup::ComponentInstance).each do |comp|
    if comp.layer == target_layer
      comp.set_attribute("zone", "type", "sleep")
      comp.set_attribute("energy", "type", "socket_load")
      puts "Upraveno: #{comp.name}"
    end
  end
end
```

---

## Co lze přidat – rozšířený standard pro LLM-ready metadata

Doporučuji vytvořit **jednotný slovník** pro každý objekt, který LLM snadno zpracuje. Příklad pro *sleep_zone*:

```json
"metadata": {
  "zone": {
    "type": "sleep",
    "temp_min_c": 18,
    "temp_max_c": 22,
    "occupancy_schedule": "22:00-07:00"
  },
  "energy": {
    "type": "socket_load",
    "typical_power_w": 50,
    "critical": false
  },
  "thermal": {
    "type": "furniture",
    "thermal_mass_kg": 40,
    "u_value_external": null
  },
  "safety": {
    "fire_risk": false,
    "smoke_detector_nearby": true,
    "exit_clearance_m": 0.8
  }
}
```

### Další užitečné atributy

- **`building`** – podlaží, firemní zóna, konstrukční systém
- **`lighting`** – typ světla, ovládání, příkon
- **`hvac`** – napojen na VZT, lokální topení, chlazení
- **`acoustic`** – zvuková izolace, hluková třída
- **`cost`** – pořizovací cena, údržba

---

## Jak efektivně pracovat s konzolí pro tvorbu metadat

| Úkol | Příkaz v konzoli |
|------|------------------|
| Vypsat všechny atributy vybraného objektu | `Sketchup.active_model.selection.first.attribute_dictionaries&.to_h` |
| Smazat slovník (např. energy) | `Sketchup.active_model.selection.first.attribute_dictionary("energy", false)&.delete` |
| Zkopírovat metadata z jednoho objektu na druhý | `src = sel[0]; dst = sel[1]; src.attribute_dictionaries.each { \|d\| dst.set_attribute(d.name, k, v) }` |
| Uložit celý model s metadaty | `Sketchup.active_model.save` (automaticky se ukládají) |
| Načíst metadata ze souboru JSON a přiřadit | (vyžaduje skript – ukážu na požádání) |

---

## Doporučený postup (roadmapa)

1. **Ručně ometadata alespoň 3 různé typy objektů** (postel, okno, zásuvka) pomocí `set_attribute`.
2. **Ověřte exportem** (`LLM_Export.export_selected`) – JSON musí obsahovat `energy`, `zone`, `thermal`, `safety`.
3. **Vytvořte konzolové makro** pro každý typ (např. `def tag_bed; ...; end`).
4. **Přejděte k hromadnému zpracování** podle názvu vrstvy nebo definice.
5. **Až bude metadata kompletní**, spusťte `LLM.export_json` (z pluginu) pro celý model.

**Okamžitý další krok (teď):**  
Vyberte postel a spusťte ruční přiřazení z bodu 1 výše. Pak exportujte znovu a pošlete výstup – ukážu vám, jak LLM taková data interpretuje.
