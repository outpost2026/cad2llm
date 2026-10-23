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
require "digest"
require "json"
require "time"

module CAD2LLM
  module Exporter
    SCHEMA_VERSION = "4.0"
    PROFILE_SPATIAL = "spatial"
    PROFILE_GEOMETRY = "geometry"
    PROFILES = [PROFILE_SPATIAL, PROFILE_GEOMETRY].freeze
    GEOMETRY_SCHEMA = "cad2llm/geometry/v1"
    MM_PER_INCH = 25.4
    SKIP_DICT_PREFIX = "SU_"
    SKIP_DICT_EXACT = ["FormFonts", "BIM", "BIM_Ifc", "DynamicComponents"].freeze
    # model-level system dictionaries: GPS duplicate, Trimble contributor noise, scratchpad
    SKIP_MODEL_DICT_EXACT = ["GeoReference", "GSU_ContributorsInfo", "temp"].freeze

    module_function

    def build(model, entities: nil, profile: PROFILE_SPATIAL, now: Time.now.utc)
      unless PROFILES.include?(profile)
        raise ArgumentError, "unknown export profile #{profile.inspect} (#{PROFILES.join('|')})"
      end

      @issues = []
      @pids = []
      nodes = []
      traverse(entities || model.entities, [], nil, Geom::Transformation.new, nodes, profile)
      gps = begin
        [model.shadow_info["Latitude"].to_f, model.shadow_info["Longitude"].to_f]
      rescue StandardError
        [0.0, 0.0]
      end
      gps = nil if gps == [0.0, 0.0]

      title = model.title.to_s.strip
      title = "SketchUp_Model" if title.empty?
      geometry = profile == PROFILE_GEOMETRY

      {
        "schema_version" => SCHEMA_VERSION,
        "export_profile" => profile,
        "project_metadata" => {
          "name" => title,
          "export_date" => now.iso8601,
          "units" => "mm",
          "up_axis" => "Z_UP",
          "global_origin_gps" => gps,
          "metadata" => metadata_of(model, skip: SKIP_DICT_EXACT + SKIP_MODEL_DICT_EXACT),
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
            "gps" => !gps.nil?,
            "geometry" => geometry,
            "geometry_schema" => geometry ? GEOMETRY_SCHEMA : nil,
            "geometry_coordinate_space" => geometry ? "world_mm" : nil
          }
        },
        "nodes" => nodes,
        "validation" => { "issues" => @issues }
      }
    end

    def export_to_file(model, path, profile: PROFILE_SPATIAL, entities: nil, now: Time.now.utc)
      doc = build(model, entities: entities, profile: profile, now: now)
      File.write(path, JSON.pretty_generate(doc) + "\n")
      doc
    end

    # ------------------------------------------------------------------
    # traversal
    # ------------------------------------------------------------------
    def traverse(entities, path, parent_uid, parent_world, out, profile)
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
        node.merge!(spatial_data(world, box))
        node["metadata"] = metadata_of(entity)
        node["geometry"] = geometry_of(entity, world, uid) if profile == PROFILE_GEOMETRY
        out << node
        @issues << { "code" => "DUPLICATE_PERSISTENT_ID", "severity" => "info", "uid" => uid,
                     "message" => "persistent_id #{entity.persistent_id} reused across instances (definition children)" } if @pids&.include?(entity.persistent_id)
        (@pids ||= []) << entity.persistent_id

        kids = entity.is_a?(Sketchup::Group) ? entity.entities : entity.definition.entities
        traverse(kids, node_path, uid, world, out, profile)
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

    def spatial_data(world, box)
      o = world.origin
      mm = MM_PER_INCH
      euler, quat = rotation_of(world)

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

    # ------------------------------------------------------------------
    # geometry profile
    # ------------------------------------------------------------------
    def geometry_of(entity, world, uid)
      source = entity.is_a?(Sketchup::ComponentInstance) ? entity.definition.entities : entity.entities
      points = {}
      raw_faces = []
      raw_edges = []

      source.each do |item|
        if item.is_a?(Sketchup::Face)
          outer = loop_points(item.outer_loop, world, points)
          next if outer.size < 3

          holes = []
          item.loops.each do |loop|
            next if loop.equal?(item.outer_loop)

            hole = loop_points(loop, world, points)
            holes << hole if hole.size >= 3
          end
          raw_faces << {
            "outer" => outer,
            "holes" => holes,
            "material" => material_name(item)
          }
        elsif item.is_a?(Sketchup::Edge)
          a = add_point(points, item.start.position, world)
          b = add_point(points, item.end.position, world)
          raw_edges << [a, b] unless a == b
        end
      end

      keys = points.keys.sort
      index = {}
      vertices = keys.each_with_index.map do |key, i|
        index[key] = i
        key
      end

      edge_set = {}
      raw_faces.each do |face|
        [face["outer"], *face["holes"]].each do |loop|
          loop.each_with_index do |key, i|
            nxt = loop[(i + 1) % loop.size]
            next if key == nxt

            a, b = [index[key], index[nxt]].sort
            edge_set[[a, b]] = true
          end
        end
      end
      raw_edges.each do |pair|
        a, b = [index[pair[0]], index[pair[1]]].sort
        edge_set[[a, b]] = true
      end

      faces = raw_faces.map do |face|
        outer = canonical_loop(face["outer"].map { |key| index[key] })
        holes = face["holes"].map { |hole| canonical_loop(hole.map { |key| index[key] }) }
        normal, area = loop_metrics(outer, vertices)
        holes.each do |hole|
          _hole_normal, hole_area = loop_metrics(hole, vertices)
          area -= hole_area
        end
        item = {
          "outer" => outer,
          "holes" => holes,
          "normal" => normal,
          "area_mm2" => round3(area)
        }
        item["material"] = face["material"] unless face["material"].nil?
        item
      end
      faces.sort_by! { |face| [face["outer"], face["holes"]] }
      faces.each_with_index { |face, i| face["id"] = format("f%04d", i) }

      edges = edge_set.keys.sort
      closed, oriented = manifold_state(faces)
      geometry = {
        "schema" => GEOMETRY_SCHEMA,
        "scope" => "direct",
        "status" => faces.empty? && edges.empty? ? "empty" : "ok",
        "coordinate_space" => "world_mm",
        "counts" => {
          "vertices" => vertices.size,
          "edges" => edges.size,
          "faces" => faces.size,
          "triangles" => faces.sum { |face| triangle_count(face["outer"], face["holes"]) }
        },
        "vertices" => vertices,
        "edges" => edges,
        "faces" => faces,
        "closed" => closed,
        "oriented" => oriented
      }
      volume = volume_mm3(faces, vertices) if closed && oriented
      geometry["volume_mm3"] = volume unless volume.nil?
      geometry["hash"] = geometry_hash(geometry)
      geometry
    rescue StandardError => e
      @issues << {
        "code" => "GEOMETRY_ERROR",
        "severity" => "warn",
        "uid" => uid,
        "message" => "#{e.class}: #{e.message}"
      }
      {
        "schema" => GEOMETRY_SCHEMA,
        "scope" => "direct",
        "coordinate_space" => "world_mm",
        "status" => "error",
        "counts" => { "vertices" => 0, "edges" => 0, "faces" => 0, "triangles" => 0 },
        "vertices" => [],
        "edges" => [],
        "faces" => [],
        "closed" => false,
        "oriented" => false,
        "hash" => nil
      }
    end

    def loop_points(loop, world, points)
      clean = []
      loop.vertices.each do |vertex|
        key = add_point(points, vertex.position, world)
        clean << key unless clean.last == key
      end
      clean.pop if clean.size > 1 && clean.first == clean.last
      clean
    end

    def add_point(points, point, world)
      world_point = point.transform(world)
      key = [round3(world_point.x * MM_PER_INCH), round3(world_point.y * MM_PER_INCH), round3(world_point.z * MM_PER_INCH)]
      points[key] = true
      key
    end

    def canonical_loop(ids)
      return ids if ids.empty?

      ids.rotate(ids.index(ids.min) || 0)
    end

    def loop_metrics(ids, vertices)
      return [[0.0, 0.0, 0.0], 0.0] if ids.size < 3

      nx = 0.0
      ny = 0.0
      nz = 0.0
      ids.each_with_index do |id, i|
        a = vertices[id]
        b = vertices[ids[(i + 1) % ids.size]]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
      end
      magnitude = Math.sqrt(nx * nx + ny * ny + nz * nz)
      return [[0.0, 0.0, 0.0], 0.0] if magnitude < 1e-9

      [[(nx / magnitude).round(6), (ny / magnitude).round(6), (nz / magnitude).round(6)], magnitude / 2.0]
    end

    def triangle_count(outer, holes)
      base = [outer.size - 2, 0].max
      base + holes.sum { |hole| [hole.size - 2, 0].max }
    end

    def manifold_state(faces)
      tally = {}
      faces.each do |face|
        [face["outer"], *face["holes"]].each do |loop|
          loop.each_with_index do |a, i|
            b = loop[(i + 1) % loop.size]
            next if a == b

            key = [a, b].sort
            entry = tally[key] ||= { "count" => 0, "balance" => 0 }
            entry["count"] += 1
            entry["balance"] += a < b ? 1 : -1
          end
        end
      end
      return [false, false] if tally.empty?

      closed = tally.values.all? { |entry| entry["count"] == 2 }
      [closed, closed && tally.values.all? { |entry| entry["balance"].zero? }]
    end

    def volume_mm3(faces, vertices)
      return nil if faces.any? { |face| face["holes"].any? }

      total = 0.0
      faces.each do |face|
        outer = face["outer"]
        next if outer.size < 3

        origin = vertices[outer[0]]
        (1...(outer.size - 1)).each do |i|
          a = vertices[outer[i]]
          b = vertices[outer[i + 1]]
          total += origin[0] * (a[1] * b[2] - a[2] * b[1]) -
                   origin[1] * (a[0] * b[2] - a[2] * b[0]) +
                   origin[2] * (a[0] * b[1] - a[1] * b[0])
        end
      end
      (total.abs / 6.0).round(3)
    end

    def geometry_hash(geometry)
      payload = {
        "vertices" => geometry["vertices"],
        "edges" => geometry["edges"],
        "faces" => geometry["faces"]
      }
      Digest::SHA256.hexdigest(JSON.generate(payload))[0, 16]
    end

    def material_name(face)
      material = face.material
      return nil if material.nil?

      name = material.respond_to?(:display_name) ? material.display_name : material.name
      name.to_s.empty? ? nil : name.to_s
    rescue StandardError
      nil
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

    def metadata_of(entity, skip: SKIP_DICT_EXACT)
      out = {}
      dicts = entity.attribute_dictionaries
      return out if dicts.nil?

      dicts.each do |dict|
        name = dict.name.to_s
        next if name.start_with?(SKIP_DICT_PREFIX) || skip.include?(name)

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
  end
end
