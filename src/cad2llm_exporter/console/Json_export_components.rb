# Json_export_components.rb
# Export vybraných entit z SketchUp do LLM-ready JSON
# Verze: 2.0 – vylepšené pojmenování + ekvivalent f-strings

module LLM_Export

  def self.get_all_metadata(entity)
    result = {}
    entity.attribute_dictionaries&.each do |dict|
      data = {}
      dict.each_pair { |k, v| data[k] = v }
      result[dict.name] = data unless data.empty?
    end
    result
  end

  # ── Pomocná funkce pro chytré pojmenování (ekvivalent Python f-strings) ──
  def self.smart_name(entity)
    # Priorita 1: instance.name (uživatelsky nastavený název komponenty)
    return entity.name if entity.name && !entity.name.empty?

    # Priorita 2: definition.name (název definice komponenty)
    if entity.respond_to?(:definition) && entity.definition.name
      return entity.definition.name
    end

    # Priorita 3: fallback
    return "Unnamed_Entity_#{entity.persistent_id}"
  end

  def self.export_selected
    model = Sketchup.active_model
    sel = model.selection

    if sel.empty?
      puts "❌ Není nic vybráno. Označ alespoň jednu komponentu nebo skupinu."
      return
    end

    data = []

    sel.each do |e|
      bbox = e.bounds
      center = bbox.center

      node = {
        id: e.persistent_id,
        name: smart_name(e),                    # ← hlavní vylepšení
        type: e.typename,
        definition: e.respond_to?(:definition) ? e.definition.name : nil,
        tag: e.layer ? e.layer.name : "Untagged",
        hierarchy_path: [],                     # lze rozšířit později
        spatial_data_m: {
          origin_xyz: [e.transformation.origin.x.to_f,
                       e.transformation.origin.y.to_f,
                       e.transformation.origin.z.to_f],
          center_xyz: [center.x.to_f, center.y.to_f, center.z.to_f],
          size_xyz:   [bbox.width.to_f, bbox.depth.to_f, bbox.height.to_f]
        },
        metadata: get_all_metadata(e)
      }

      data << node
    end

    # ── Výstup ─────────────────────────────────────────────────────────────
    json_str = JSON.pretty_generate(data)

    puts "📋 JSON výstup (#{data.size} entit):"
    puts json_str

    # Uložení do souboru
    path = "C:/Users/PC/Documents/Repozitar_Dev/Skripty_local/Skript_spatial_analysis/Ruby/Sketchup2024_export/selected_export_#{Time.now.strftime('%Y%m%d_%H%M%S')}.json"

    begin
      dir = File.dirname(path)
      Dir.mkdir(dir) unless Dir.exist?(dir)
      File.write(path, json_str)
      puts "✅ Export úspěšně uložen do:"
      puts "   #{path}"
    rescue => e
      puts "⚠️ Chyba při ukládání souboru: #{e.message}"
      puts "   JSON byl vypsán pouze do konzole."
    end
  end

end

# Spuštění skriptu
LLM_Export.export_selected
