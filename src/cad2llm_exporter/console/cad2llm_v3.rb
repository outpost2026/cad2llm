require 'json'
require 'time'

module CAD2LLM_V3
  def self.export
    model = Sketchup.active_model
    entities = model.entities
    lat = model.shadow_info["Latitude"] rescue 0.0
    lon = model.shadow_info["Longitude"] rescue 0.0

    nodes = []

    # Rekurzivní procházení entit (skupin a komponent)
    # ZMĚNA 1: Přidán parametr parent_id pro vytvoření grafových vazeb
    def self.traverse(ents, current_path, parent_transform, result_nodes, parent_id = nil)
      return unless ents
      ents.each do |entity|
        if entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)

          # --- Základní identifikace ---
          inst_name = entity.name.to_s.strip
          inst_name = "Unnamed" if inst_name.empty?
          def_name = entity.respond_to?(:definition) ? entity.definition.name.to_s : ""
          path = current_path + [inst_name]
          tag = entity.layer.name.to_s
          current_id = entity.persistent_id.to_s

          # --- SBĚR A FILTRACE METADAT ---
          all_metadata = {}
          if entity.attribute_dictionaries
            entity.attribute_dictionaries.each do |dict|
              dict_name = dict.name
              
              # ZMĚNA 2: Filtrování systémového balastu
              # Ignorujeme "SU_InstanceSet", "SU_DefinitionSet", "FormFonts" atd.
              next if dict_name.start_with?("SU_") || dict_name == "FormFonts" || dict_name == "BIM"
              
              all_metadata[dict_name] = {}
              dict.each_pair do |key, value|
                # ZMĚNA 3: Lepší parsování čísel. Odlišení Integer vs Float.
                if value.is_a?(String)
                  if value.match?(/^-?\d+$/)
                    value = value.to_i
                  elsif value.match?(/^-?\d+\.\d+$/)
                    value = value.to_f
                  end
                end
                all_metadata[dict_name][key] = value
              end
            end
          end

          # --- Prostorová data (transformace, bounding box) ---
          wt = parent_transform * entity.transformation
          m = wt.to_a
          
          bounds = entity.respond_to?(:definition) ? entity.definition.bounds : entity.bounds
          corners = (0..7).map { |i| bounds.corner(i).transform(wt) }
          xs, ys, zs = corners.map(&:x), corners.map(&:y), corners.map(&:z)

          inch_m = 0.0254
          # Zaokrouhlení na 4 desetinná místa pro trochu větší přesnost (mm)
          origin_xyz = [(wt.origin.x * inch_m).round(4), (wt.origin.y * inch_m).round(4), (wt.origin.z * inch_m).round(4)]
          center_xyz = [((xs.min + xs.max)/2.0 * inch_m).round(4), ((ys.min + ys.max)/2.0 * inch_m).round(4), ((zs.min + zs.max)/2.0 * inch_m).round(4)]
          size_xyz = [((xs.max - xs.min) * inch_m).round(4), ((ys.max - ys.min) * inch_m).round(4), ((zs.max - zs.min) * inch_m).round(4)]

          # ZMĚNA 4: Přidání transformační matice pro LLM analýzu rotace a scale
          matrix_clean = m.map { |v| v.round(6) }

          # --- Sestavení uzlu (JSON objektu) ---
          node = {
            "id" => current_id,
            "parent_id" => parent_id, # explicitní vazba na rodičovský objekt
            "instance_name" => inst_name,
            "definition_name" => def_name,
            "tag" => tag,
            "hierarchy_path" => path,
            "spatial_data_m" => {
              "origin_xyz" => origin_xyz,
              "center_xyz" => center_xyz,
              "size_xyz" => size_xyz,
              "transform_matrix" => matrix_clean
            }
          }
          
          # ZMĚNA 5: Uložíme metadata pouze pokud po filtraci nezůstala prázdná
          node["metadata"] = all_metadata unless all_metadata.empty?

          result_nodes << node

          # --- Rekurze do vnořených entit ---
          children = entity.is_a?(Sketchup::Group) ? entity.entities : entity.definition.entities
          # ZMĚNA 6: Předání current_id jako parent_id pro další úroveň rekurze
          traverse(children, path, wt, result_nodes, current_id)
        end
      end
    end

    # Inicializace rekurze s parent_id = nil
    traverse(entities, [], Geom::Transformation.new, nodes, nil)

    # --- Finální JSON ---
    output = {
      "project_metadata" => {
        "name" => model.title.empty? ? "Unnamed_Model" : model.title,
        "export_date" => Time.now.utc.iso8601,
        "global_origin_gps" => [lat, lon],
        "units" => "meters",
        "format_version" => "v3"
      },
      "nodes" => nodes
    }

    # ZMĚNA 7: Uložení do souboru se jménem sketchup_model_v3.json
    filename = "sketchup_model_v3.json"
    filepath = model.path.empty? ? File.join(Dir.home, filename) : File.join(File.dirname(model.path), filename)
    
    # Formátování JSONu pro lidskou čitelnost, ale udržení polí na jednom řádku
    json_str = JSON.pretty_generate(output)
    
    # Komprimace polí do 1 řádku (XYZ vektory)
    json_str.gsub!(/\[\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+)\s+\]/m, '[\1, \2, \3]')
    
    # Komprimace 16-prvkové matice do 1 řádku, aby formátování neexplodovalo
    json_str.gsub!(/\[\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+)\s+\]/m, '[\1, \2, \3, \4, \5, \6, \7, \8, \9, \10, \11, \12, \13, \14, \15, \16]')

    File.write(filepath, json_str)

    UI.messagebox("CAD2LLM V3 Export úspěšný!\nUloženo do: #{filepath}\nExportováno uzlů: #{nodes.length}")
    puts "Hotovo: #{filepath} (Uzly: #{nodes.length})"
  end
end

CAD2LLM_V3.export
