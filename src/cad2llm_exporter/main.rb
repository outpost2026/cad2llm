# cad2llm_exporter/main.rb
# CAD2LLM spatial JSON export for SketchUp 2024
# Base: sketchup2024_Cad2LLM_ruby_console_public_v3.rb
# Modifications per handoff_ruby.json phase 3 (anonymization + save panel)

require 'json'
require 'time'

module CAD2LLM
  def self.export
    model = Sketchup.active_model
    entities = model.entities
    lat = model.shadow_info["Latitude"] rescue 0.0
    lon = model.shadow_info["Longitude"] rescue 0.0

    nodes = []

    # Rekurzivni prochazeni entit (skupin a komponent)
    def self.traverse(ents, current_path, parent_transform, result_nodes)
      return unless ents
      ents.each do |entity|
        if entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)

          # --- Zakladni identifikace ---
          inst_name = entity.name.to_s.strip
          inst_name = "Unnamed" if inst_name.empty?
          def_name = entity.respond_to?(:definition) ? entity.definition.name.to_s : ""
          path = current_path + [inst_name]
          tag = entity.layer.name.to_s

          # --- SBER VSECH METADAT (attribute dictionaries) ---
          all_metadata = {}
          if entity.attribute_dictionaries
            entity.attribute_dictionaries.each do |dict|
              dict_name = dict.name
              all_metadata[dict_name] = {}
              dict.each_pair do |key, value|
                if value.is_a?(String) && value.match?(/^-?\d+(?:\.\d+)?$/)
                  value = value.to_f
                end
                all_metadata[dict_name][key] = value
              end
            end
          end

          # --- Prostorova data (transformace, bounding box) ---
          wt = parent_transform * entity.transformation
          m = wt.to_a

          bounds = entity.respond_to?(:definition) ? entity.definition.bounds : entity.bounds
          corners = (0..7).map { |i| bounds.corner(i).transform(wt) }
          xs, ys, zs = corners.map(&:x), corners.map(&:y), corners.map(&:z)

          inch_m = 0.0254
          origin_xyz = [(wt.origin.x * inch_m).round(3), (wt.origin.y * inch_m).round(3), (wt.origin.z * inch_m).round(3)]
          center_xyz = [((xs.min + xs.max)/2.0 * inch_m).round(3), ((ys.min + ys.max)/2.0 * inch_m).round(3), ((zs.min + zs.max)/2.0 * inch_m).round(3)]
          size_xyz = [((xs.max - xs.min) * inch_m).round(3), ((ys.max - ys.min) * inch_m).round(3), ((zs.max - zs.min) * inch_m).round(3)]

          # --- Sestaveni uzlu (JSON objektu) ---
          node = {
            "id" => entity.persistent_id.to_s,
            "instance_name" => inst_name,
            "definition_name" => def_name,
            "tag" => tag,
            "hierarchy_path" => path,
            "spatial_data_m" => {
              "origin_xyz" => origin_xyz,
              "center_xyz" => center_xyz,
              "size_xyz" => size_xyz
            },
            "metadata" => all_metadata
          }

          result_nodes << node

          # --- Rekurze do vnorenych entit ---
          children = entity.is_a?(Sketchup::Group) ? entity.entities : entity.definition.entities
          traverse(children, path, wt, result_nodes)
        end
      end
    end

    traverse(entities, [], Geom::Transformation.new, nodes)

    # --- Finalni JSON struktura (anonymized default name) ---
    model_title = model.title.empty? ? "SketchUp_Model" : model.title
    output = {
      "project_metadata" => {
        "name" => model_title,
        "export_date" => Time.now.utc.iso8601,
        "global_origin_gps" => [lat, lon],
        "units" => "meters"
      },
      "nodes" => nodes
    }

    # --- Dynamicke pojmenovani JSONu dle handoff ---
    export_name = model.title.empty? ? "cad2llm_export.json" : "#{model.title}_cad2llm.json"

    # --- Cesta: model.path, nebo UI.savepanel pokud model neni ulozen ---
    if model.path.empty?
      filepath = UI.savepanel("Save CAD2LLM export", nil, export_name)
      return puts "Export zrusen." unless filepath
      filepath = filepath.end_with?(".json") ? filepath : "#{filepath}.json"
    else
      filepath = File.join(File.dirname(model.path), export_name)
    end

    json_string = JSON.pretty_generate(output).gsub(/\[\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+)\s+\]/m, '[\1, \2, \3]')
    File.write(filepath, json_string)

    UI.messagebox("CAD2LLM Export successful!\nSaved to:\n#{filepath}")
    puts "Done: #{filepath}"
  end
end

# Autostart when loaded as extension entry is not required;
# call manually from Ruby Console: CAD2LLM.export
