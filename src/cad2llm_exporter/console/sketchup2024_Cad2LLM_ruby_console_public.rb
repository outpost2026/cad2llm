require 'json'
require 'time'

module CAD2LLM
  def self.export
    model = Sketchup.active_model
    entities = model.entities
    lat = model.shadow_info["Latitude"] rescue 0.0
    lon = model.shadow_info["Longitude"] rescue 0.0
    
    nodes = []
    
    def self.traverse(ents, current_path, parent_transform, result_nodes)
      return unless ents
      ents.each do |entity|
        if entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)
          
          inst_name = entity.name.to_s.strip
          inst_name = "Unnamed" if inst_name.empty?
          def_name = entity.respond_to?(:definition) ? entity.definition.name.to_s : ""
          
          path = current_path + [inst_name]
          tag = entity.layer.name.to_s
          
          ifc_type = ""
          attrs = {}
          
          if entity.attribute_dictionaries
            entity.attribute_dictionaries.each do |dict|
              if dict.name == "dynamic_attributes"
                dict.each { |k, v| attrs[k] = v unless k.to_s.start_with?("_") }
              elsif dict.name == "BIM_Ifc" || dict.name == "SU_InstanceSet"
                ifc_type = dict["IfcType"] || dict["ObjectType"] || ifc_type
              end
            end
          end
          
          if ifc_type.empty? && entity.respond_to?(:definition) && entity.definition.attribute_dictionaries
            entity.definition.attribute_dictionaries.each do |dict|
              if dict.name == "SU_DefinitionSet" || dict.name == "BIM_Ifc"
                ifc_type = dict["ObjectType"] || dict["IfcType"] || ifc_type
              end
            end
          end
          
          wt = parent_transform * entity.transformation
          m = wt.to_a
          sx = Math.sqrt(m[0]**2 + m[1]**2 + m[2]**2)
          sy = Math.sqrt(m[4]**2 + m[5]**2 + m[6]**2)
          sz = Math.sqrt(m[8]**2 + m[9]**2 + m[10]**2)
          
          bounds = entity.respond_to?(:definition) ? entity.definition.bounds : entity.bounds
          corners = (0..7).map { |i| bounds.corner(i).transform(wt) }
          xs, ys, zs = corners.map(&:x), corners.map(&:y), corners.map(&:z)
          
          inch_m = 0.0254
          center = [ ((xs.min + xs.max)/2.0 * inch_m).round(3), ((ys.min + ys.max)/2.0 * inch_m).round(3), ((zs.min + zs.max)/2.0 * inch_m).round(3) ]
          size = [ ((xs.max - xs.min) * inch_m).round(3), ((ys.max - ys.min) * inch_m).round(3), ((zs.max - zs.min) * inch_m).round(3) ]
          
          node = {
            "id" => entity.guid,
            "instance_name" => inst_name,
            "definition_name" => def_name,
            "tag" => tag,
            "hierarchy_path" => path,
            "spatial_data_m" => { "origin_xyz" => [(wt.origin.x*inch_m).round(3), (wt.origin.y*inch_m).round(3), (wt.origin.z*inch_m).round(3)], "center_xyz" => center, "size_xyz" => size }
          }
          node["classification_ifc"] = ifc_type unless ifc_type.empty?
          node["dynamic_attributes"] = attrs unless attrs.empty?
          result_nodes << node
          
          children = entity.is_a?(Sketchup::Group) ? entity.entities : entity.definition.entities
          traverse(children, path, wt, result_nodes)
        end
      end
    end
    
    traverse(entities, [], Geom::Transformation.new, nodes)
    
    output = {
      "project_metadata" => { "name" => model.title, "export_date" => Time.now.utc.iso8601, "global_origin_gps" => [lat, lon], "units" => "meters" },
      "nodes" => nodes
    }
    
    filepath = model.path.empty? ? File.join(Dir.home, "sketchup_model.json") : File.join(File.dirname(model.path), "sketchup_model.json")
    
    File.write(filepath, JSON.pretty_generate(output).gsub(/\[\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+)\s+\]/m, '[\1, \2, \3]'))
    
    UI.messagebox("CAD2LLM Export uspesny!\nUlozeno do: #{filepath}")
    puts "Hotovo: #{filepath}"
  end
end

CAD2LLM.export
s
