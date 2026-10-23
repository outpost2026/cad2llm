# frozen_string_literal: true
#
# CAD2LLM::Exporter — SketchUp model -> schema v4 spatial JSON.
# Single source of truth for the Ruby channel; no console copy-paste.
#
# Fixes vs. historical console scripts:
#   * group bounds are already world-space -> no double transform
#   * no gsub "compaction" (it corrupted transform matrices: \10..\16 -> \1 + "0")
#   * SU_* attribute dictionaries filtered
#   * stable uid = persistent_id@path (children of shared definitions no longer collide)
#   * bbox min/max included (required by Metodika krok 1: z_min/z_max table)
#
module CAD2LLM
  module Exporter
    SCHEMA_VERSION = "4.0"
    LEGACY_VERSION = "3.0"
    MM_PER_INCH = 25.4
    SKIP_DICT_PREFIX = "SU_"
    SKIP_DICT_EXACT = ["FormFonts", "BIM", "BIM_Ifc", "DynamicComponents"].freeze

    module_function

    def build(model, entities: nil, schema: SCHEMA_VERSION, now: Time.now.utc)
      @issues = []
      @pids = []
      nodes = []
      traverse(entities || model.entities, [], nil, Geom::Transformation.new, nodes, schema)
      gps = begin
        [model.shadow_info["Latitude"].to_f, model.shadow_info["Longitude"].to_f]
      rescue StandardError
        [0.0, 0.0]
      end
      gps = nil if gps == [0.0, 0.0]

      title = model.title.to_s.strip
      title = "SketchUp_Model" if title.empty?

      if schema == LEGACY_VERSION
        legacy_doc(model, title, nodes, gps, now)
      else
        {
          "schema_version" => SCHEMA_VERSION,
          "project_metadata" => {
            "name" => title,
            "export_date" => now.iso8601,
            "units" => "mm",
            "up_axis" => "Z_UP",
            "global_origin_gps" => gps,
            "source" => {
              "channel" => "sketchup",
              "file" => model.path.to_s.empty? ? nil : File.basename(model.path),
              "model_path" => model.path.to_s.empty? ? nil : model.path,
              "sketchup_version" => Sketchup.version.to_s
            },
            "capabilities" => {
              "hierarchy" => true,
              "bbox_mm" => true,
              "rotation" => true,
              "tags" => true,
              "definition_description" => true,
              "metadata_dictionaries" => true,
              "persistent_ids" => true,
              "gps" => !gps.nil?
            }
          },
          "nodes" => nodes,
          "validation" => { "issues" => @issues }
        }
      end
    end

    def export_to_file(model, path, schema: SCHEMA_VERSION, entities: nil, now: Time.now.utc)
      doc = build(model, entities: entities, schema: schema, now: now)
      File.write(path, JSON.pretty_generate(doc) + "\n")
      doc
    end

    # ------------------------------------------------------------------
    # traversal
    # ------------------------------------------------------------------
    def traverse(entities, path, parent_uid, parent_world, out, schema)
      return unless entities

      entities.each do |entity|
        next unless entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)

        name = entity.name.to_s.strip
        name = "Unnamed" if name.empty?
        type = entity.is_a?(Sketchup::ComponentInstance) ? "component" : "group"
        definition_name = entity.is_a?(Sketchup::ComponentInstance) ? entity.definition.name.to_s : nil
        definition_description = if entity.is_a?(Sketchup::ComponentInstance)
                                  entity.definition.description.to_s.strip
                                end
        definition_description = nil if definition_description&.empty?
        node_path = path + [name]
        uid = "#{entity.persistent_id}@#{node_path.join('/')}"
        world = parent_world * entity.transformation

        box = world_bounds(entity, world)
        spatial = spatial_data(world, box, schema)

        node = {
          "uid" => uid,
          "persistent_id" => entity.persistent_id,
          "name" => name,
          "definition_name" => definition_name,
          "definition_description" => definition_description,
          "entity_type" => type,
          "tag" => tag_of(entity),
          "hierarchy_path" => node_path,
          "parent_uid" => parent_uid
        }
        node.merge!(spatial)
        node["metadata"] = metadata_of(entity)
        out << node
        @issues << { "code" => "DUPLICATE_PERSISTENT_ID", "severity" => "info", "uid" => uid,
                     "message" => "persistent_id #{entity.persistent_id} reused across instances (definition children)" } if @pids&.include?(entity.persistent_id)
        (@pids ||= []) << entity.persistent_id

        kids = entity.is_a?(Sketchup::Group) ? entity.entities : entity.definition.entities
        traverse(kids, node_path, uid, world, out, schema)
      end
    end

    # Group#bounds is already in model space; ComponentInstance bounds must be
    # taken from the definition (local) and transformed by the world matrix.
    def world_bounds(entity, world)
      if entity.is_a?(Sketchup::ComponentInstance)
        local = entity.definition.bounds
        return nil if local.nil? || local.empty?

        corners = (0..7).map { |i| local.corner(i).transform(world) }
      else
        world_bounds = entity.bounds
        return nil if world_bounds.nil? || world_bounds.empty?

        corners = (0..7).map { |i| world_bounds.corner(i) }
      end
      mins = corners.map { |c| [c.x, c.y, c.z] }.transpose.map(&:min)
      maxs = corners.map { |c| [c.x, c.y, c.z] }.transpose.map(&:max)
      [mins, maxs]
    end

    def spatial_data(world, box, schema)
      o = world.origin
      mm = MM_PER_INCH
      euler, quat = rotation_of(world)

      if schema == LEGACY_VERSION
        return { "spatial_data_m" => { "origin_xyz" => [round3(o.x * 0.0254), round3(o.y * 0.0254), round3(o.z * 0.0254)] } } if box.nil?

        mins, maxs = box
        center = mins.each_with_index.map { |v, i| (v + maxs[i]) / 2.0 }
        size = mins.each_with_index.map { |v, i| maxs[i] - v }
        return { "spatial_data_m" => {
          "origin_xyz" => [round3(o.x * 0.0254), round3(o.y * 0.0254), round3(o.z * 0.0254)],
          "center_xyz" => center.map { |v| round3(v * 0.0254) },
          "size_xyz" => size.map { |v| round3(v * 0.0254) },
          "transform_matrix" => legacy_matrix(world)
        } }
      end

      data = {
        "origin_mm" => { "x" => round3(o.x * mm), "y" => round3(o.y * mm), "z" => round3(o.z * mm) },
        "rotation_deg" => { "x" => euler[0], "y" => euler[1], "z" => euler[2] },
        "quaternion" => { "w" => quat[0], "x" => quat[1], "y" => quat[2], "z" => quat[3] },
        "matrix_4x4" => matrix4(world, mm)
      }
      return { "spatial" => data } if box.nil?

      mins, maxs = box
      size = mins.each_with_index.map { |v, i| maxs[i] - v }
      center = mins.each_with_index.map { |v, i| (v + maxs[i]) / 2.0 }
      data["bbox_mm"] = {
        "min" => { "x" => round3(mins[0] * mm), "y" => round3(mins[1] * mm), "z" => round3(mins[2] * mm) },
        "max" => { "x" => round3(maxs[0] * mm), "y" => round3(maxs[1] * mm), "z" => round3(maxs[2] * mm) }
      }
      data["size_mm"] = { "x" => round3(size[0] * mm), "y" => round3(size[1] * mm), "z" => round3(size[2] * mm) }
      data["center_mm"] = { "x" => round3(center[0] * mm), "y" => round3(center[1] * mm), "z" => round3(center[2] * mm) }
      { "spatial" => data }
    end

    def matrix4(world, mm)
      a = world.to_a
      rows = [
        [a[0], a[4], a[8], a[12] * mm],
        [a[1], a[5], a[9], a[13] * mm],
        [a[2], a[6], a[10], a[14] * mm],
        [0.0, 0.0, 0.0, 1.0]
      ]
      rows.map { |r| r.map { |v| v.round(4) } }
    end

    # legacy v3 matrix had the translation in inches while units claimed metres
    def legacy_matrix(world)
      world.to_a.each_with_index.map do |v, i|
        i.between?(12, 14) ? (v * 0.0254).round(6) : v.round(6)
      end
    end

    def rotation_of(world)
      a = world.to_a
      cols = [[a[0], a[1], a[2]], [a[4], a[5], a[6]], [a[8], a[9], a[10]]]
      scales = cols.map { |c| Math.sqrt(c.sum { |v| v * v }) }
      scales = scales.map { |s| s.abs < 1e-9 ? 1.0 : s }
      r = Array.new(3) { |i| [cols[0][i] / scales[0], cols[1][i] / scales[1], cols[2][i] / scales[2]] }

      ry = Math.asin([[-1.0, r[2][0]].max, 1.0].min)
      cy = Math.cos(ry)
      if cy.abs > 1e-9
        rx = Math.atan2(r[2][1], r[2][2])
        rz = Math.atan2(r[1][0], r[0][0])
      else
        rx = Math.atan2(-r[1][2], r[1][1])
        rz = 0.0
      end

      tr = Math.sqrt([0.0, 1.0 + r[0][0] + r[1][1] + r[2][2]].max)
      if tr > 1e-6
        qw = 0.5 * tr
        k = 0.5 / tr
        qx = (r[2][1] - r[1][2]) * k
        qy = (r[0][2] - r[2][0]) * k
        qz = (r[1][0] - r[0][1]) * k
      else
        qw = 0.0
        qx = Math.sqrt([0.0, 1.0 + r[0][0] - r[1][1] - r[2][2]].max) / 2.0
        qy = Math.sqrt([0.0, 1.0 + r[1][1] - r[0][0] - r[2][2]].max) / 2.0
        qz = Math.sqrt([0.0, 1.0 + r[2][2] - r[0][0] - r[1][1]].max) / 2.0
      end

      [[(rx * 180.0 / Math::PI).round(3), (ry * 180.0 / Math::PI).round(3), (rz * 180.0 / Math::PI).round(3)],
       [qw.round(6), qx.round(6), qy.round(6), qz.round(6)]]
    end

    def metadata_of(entity)
      out = {}
      dicts = entity.attribute_dictionaries
      return out if dicts.nil?

      dicts.each do |dict|
        name = dict.name.to_s
        next if name.start_with?(SKIP_DICT_PREFIX) || SKIP_DICT_EXACT.include?(name)

        values = {}
        dict.each_pair do |key, value|
          values[key] = coerce(value)
        end
        out[name] = values unless values.empty?
      end
      out
    end

    def coerce(value)
      return value unless value.is_a?(String)

      if value.match?(/\A-?\d+\z/)
        value.to_i
      elsif value.match?(/\A-?\d+\.\d+\z/)
        value.to_f
      else
        value
      end
    end

    def tag_of(entity)
      layer = entity.layer
      layer ? layer.name.to_s : nil
    rescue StandardError
      nil
    end

    def round3(value)
      value.round(3)
    end

    def legacy_doc(model, title, nodes, gps, now)
      {
        "schema_version" => LEGACY_VERSION,
        "project_metadata" => {
          "name" => title,
          "export_date" => now.iso8601,
          "global_origin_gps" => gps || [0.0, 0.0],
          "units" => "meters",
          "format_version" => "v3"
        },
        "nodes" => nodes,
        "validation" => { "issues" => @issues }
      }
    end
  end
end
