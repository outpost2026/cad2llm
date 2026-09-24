require 'json'
require 'time'

module CAD2LLM
  def self.export
    model = Sketchup.active_model
    entities = model.entities
    lat = model.shadow_info["Latitude"] rescue 0.0
    lon = model.shadow_info["Longitude"] rescue 0.0

    nodes = []

    # Rekurzivní procházení entit (skupin a komponent)
    def self.traverse(ents, current_path, parent_transform, result_nodes)
      return unless ents
      ents.each do |entity|
        if entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)

          # --- Základní identifikace ---
          inst_name = entity.name.to_s.strip
          inst_name = "Unnamed" if inst_name.empty?
          def_name = entity.respond_to?(:definition) ? entity.definition.name.to_s : ""
          path = current_path + [inst_name]
          tag = entity.layer.name.to_s

          # --- SBĚR VŠECH METADAT (attribute dictionaries) ---
          all_metadata = {}
          if entity.attribute_dictionaries
            entity.attribute_dictionaries.each do |dict|
              dict_name = dict.name
              all_metadata[dict_name] = {}
              dict.each_pair do |key, value|
                # Pokus převést řetězec na číslo (např. "628" → 628)
                if value.is_a?(String) && value.match?(/^-?\d+(?:\.\d+)?$/)
                  value = value.to_f
                end
                all_metadata[dict_name][key] = value
              end
            end
          end

          # --- Prostorová data (transformace, bounding box) ---
          wt = parent_transform * entity.transformation
          m = wt.to_a
          sx = Math.sqrt(m[0]**2 + m[1]**2 + m[2]**2)
          sy = Math.sqrt(m[4]**2 + m[5]**2 + m[6]**2)
          sz = Math.sqrt(m[8]**2 + m[9]**2 + m[10]**2)

          bounds = entity.respond_to?(:definition) ? entity.definition.bounds : entity.bounds
          corners = (0..7).map { |i| bounds.corner(i).transform(wt) }
          xs, ys, zs = corners.map(&:x), corners.map(&:y), corners.map(&:z)

          inch_m = 0.0254
          origin_xyz = [(wt.origin.x * inch_m).round(3), (wt.origin.y * inch_m).round(3), (wt.origin.z * inch_m).round(3)]
          center_xyz = [((xs.min + xs.max)/2.0 * inch_m).round(3), ((ys.min + ys.max)/2.0 * inch_m).round(3), ((zs.min + zs.max)/2.0 * inch_m).round(3)]
          size_xyz = [((xs.max - xs.min) * inch_m).round(3), ((ys.max - ys.min) * inch_m).round(3), ((zs.max - zs.min) * inch_m).round(3)]

          # --- Sestavení uzlu (JSON objektu) ---
          node = {
            "id" => entity.persistent_id.to_s,      # změna: persistent_id místo guid
            "instance_name" => inst_name,
            "definition_name" => def_name,
            "tag" => tag,
            "hierarchy_path" => path,
            "spatial_data_m" => {
              "origin_xyz" => origin_xyz,
              "center_xyz" => center_xyz,
              "size_xyz" => size_xyz
            },
            "metadata" => all_metadata               # klíč – všechna metadata
          }

          result_nodes << node

          # --- Rekurze do vnořených entit ---
          children = entity.is_a?(Sketchup::Group) ? entity.entities : entity.definition.entities
          traverse(children, path, wt, result_nodes)
        end
      end
    end

    traverse(entities, [], Geom::Transformation.new, nodes)

    # --- Finální JSON struktura ---
    output = {
      "project_metadata" => {
        "name" => model.title.empty? ? "untitled_model" : model.title,
        "export_date" => Time.now.utc.iso8601,
        "global_origin_gps" => [lat, lon],
        "units" => "meters"
      },
      "nodes" => nodes
    }

    # --- Generování unikátního názvu souboru s interpolací ---
    timestamp = Time.now.strftime("%Y%m%d_%H%M%S")
    base_name = model.title.empty? ? "untitled_model" : model.title.gsub(/[^0-9A-Za-z.\-]/, '_')
    output_filename = "#{base_name}_export_#{timestamp}.json"

    # --- Sestavení cílové cesty ---
    target_dir = model.path.empty? ? Dir.home : File.dirname(model.path)
    filepath = File.join(target_dir, output_filename)

    # --- Uložení souboru s formátováním souřadnic ---
    json_string = JSON.pretty_generate(output).gsub(/\[\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+)\s+\]/m, '[\1, \2, \3]')
    File.write(filepath, json_string)

    UI.messagebox("CAD2LLM Export úspěšný!\nUloženo do:\n#{filepath}")
    puts "Hotovo: #{filepath}"
  end
end

# Spuštění exportu
CAD2LLM.export
