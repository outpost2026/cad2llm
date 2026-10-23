# frozen_string_literal: true
#
# CAD2LLM::Bridge — local loopback JSON-RPC for the running SketchUp session.
#
# Transport: TCP 127.0.0.1:9877, newline-delimited UTF-8 JSON, one request
# per line, one response per line. The token is generated on start and
# written to Dir.tmpdir/cad2llm_bridge.json (readable by the local user only).
#
# SketchUp is single-threaded: the server is polled from a UI timer, so all
# model mutation happens on the main thread inside undo transactions.
#
require "json"
require "socket"
require "securerandom"
require "tmpdir"
require "fileutils"
require "time"

require_relative "exporter"
require_relative "annotate"

module CAD2LLM
  module Bridge
    VERSION = "1.0"
    HOST = "127.0.0.1"
    DEFAULT_PORT = 9877
    TOKEN_FILE = "cad2llm_bridge.json"
    MAX_REQUEST_BYTES = 1_048_576
    MAX_OPS = 2000
    GEOMETRY_TOL_MM = 0.001
    POLL_INTERVAL = 0.05

    module_function

    def default_token_path
      local = ENV["LOCALAPPDATA"].to_s
      if local.empty?
        File.join(Dir.tmpdir, TOKEN_FILE)
      else
        File.join(local, "cad2llm", "bridge.json")
      end
    end

    def token_path
      @token_path ||= default_token_path
    end

    def running?
      !@server.nil?
    end

    def port
      @port
    end

    def token
      @token
    end

    # auto_poll: false is used by the headless spec, which drives #poll itself.
    def start(model: Sketchup.active_model, port: DEFAULT_PORT, token: nil, auto_poll: true, token_path: nil)
      return state(model) if running?

      @model = model
      @token_path = token_path || default_token_path
      FileUtils.mkdir_p(File.dirname(@token_path))
      @token = token || SecureRandom.hex(16)
      @server = TCPServer.new(HOST, port)
      @port = @server.addr[1]
      @clients = []
      @buffers = {}
      write_token_file
      if auto_poll && defined?(UI) && UI.respond_to?(:start_timer)
        @timer = UI.start_timer(POLL_INTERVAL, true) { poll }
      end
      state(model)
    end

    def state(model = @model)
      {
        "running" => running?,
        "host" => HOST,
        "port" => @port,
        "protocol" => VERSION,
        "token_file" => running? ? token_path : nil,
        "clients" => (@clients || []).size,
        "model" => model&.title.to_s,
        "model_path" => model&.path.to_s
      }
    end

    def stop
      UI.stop_timer(@timer) if @timer && defined?(UI) && UI.respond_to?(:stop_timer)
      (@clients || []).each { |c| close(c) }
      @clients = []
      @buffers = {}
      @server&.close
      @server = nil
      @timer = nil
      File.delete(token_path) if File.exist?(token_path)
      @token_path = nil
      { "running" => false }
    end

    # ------------------------------------------------------------------
    # transport (called from the UI timer)
    # ------------------------------------------------------------------
    def poll
      return unless @server

      ensure_token_file
      accept_clients
      @clients.dup.each { |client| read_client(client) }
    end

    # Windows temp cleaners may delete the token file while the bridge lives.
    def ensure_token_file
      return if @token.nil? || File.exist?(token_path)

      FileUtils.mkdir_p(File.dirname(token_path))
      write_token_file
    end

    def write_token_file
      File.write(token_path, JSON.generate(
        "app" => "cad2llm", "protocol" => VERSION, "host" => HOST,
        "port" => @port, "token" => @token, "pid" => Process.pid,
        "started_at" => Time.now.utc.iso8601
      ) + "\n")
    end

    def accept_clients
      loop do
        client = @server.accept_nonblock(exception: false)
        break if client == :wait_readable || client.nil?

        @clients << client
        @buffers[client] = +""
      end
    end

    def read_client(client)
      chunk = client.read_nonblock(65_536, exception: false)
      return if chunk == :wait_readable

      if chunk.nil?
        close(client)
        return
      end

      buffer = (@buffers[client] ||= +"") << chunk
      if buffer.bytesize > MAX_REQUEST_BYTES
        write_line(client, error_response(nil, "payload_too_large", "request exceeds #{MAX_REQUEST_BYTES} bytes"))
        close(client)
        return
      end

      while (newline = buffer.index("\n"))
        line = buffer.slice!(0..newline).strip
        next if line.empty?

        write_line(client, handle_line(line, @model))
      end
    rescue IOError, SystemCallError
      close(client)
    end

    def close(client)
      @buffers.delete(client)
      @clients&.delete(client)
      client.close
    rescue StandardError
      nil
    end

    def write_line(client, payload)
      client.write(JSON.generate(payload) + "\n")
    rescue StandardError
      close(client)
    end

    def handle_line(line, model)
      request = JSON.parse(line)
      raise ArgumentError, "request must be a JSON object" unless request.is_a?(Hash)

      dispatch(request, model: model)
    rescue JSON::ParserError => e
      error_response(nil, "bad_json", e.message)
    rescue ArgumentError => e
      error_response(request_id_of(line), "bad_request", e.message)
    rescue StandardError => e
      error_response(nil, "internal_error", "#{e.class}: #{e.message}")
    end

    def request_id_of(line)
      JSON.parse(line)["id"]
    rescue StandardError
      nil
    end

    # ------------------------------------------------------------------
    # protocol (pure; tested headless)
    # ------------------------------------------------------------------
    def dispatch(request, model: Sketchup.active_model)
      id = request["id"]
      method = request["method"].to_s
      params = request["params"] || {}
      raise ArgumentError, "params must be an object" unless params.is_a?(Hash)

      authorized = method == "ping" ? true : token_ok?(request["token"])
      return error_response(id, "unauthorized", "missing or invalid token") unless authorized

      result =
        case method
        when "ping" then { "protocol" => VERSION, "sketchup" => Sketchup.version.to_s,
                           "authenticated" => token_ok?(request["token"]) }
        when "info" then info(model)
        when "attributes" then attributes(model, params)
        when "describe" then describe(model, params)
        when "purge_unused_tags" then purge_unused_tags(model)
        when "reload" then reload
        when "export" then export(model, params)
        when "apply" then apply(model, params)
        when "undo" then undo
        else raise ArgumentError, "unknown method #{method.inspect} (ping|info|export|apply|undo)"
        end
      { "id" => id, "ok" => true, "result" => result }
    rescue ArgumentError => e
      error_response(id, "bad_request", e.message)
    rescue StandardError => e
      error_response(id, "internal_error", "#{e.class}: #{e.message}")
    end

    # Hot-reload of this extension's own Ruby files (no SketchUp restart).
    def reload
      base = __dir__
      files = %w[exporter.rb annotate.rb bridge.rb].map { |name| File.join(base, name) }
      loaded = files.select { |file| File.exist?(file) && load(file) }
      { "reloaded" => loaded.map { |file| File.basename(file) },
        "protocol" => VERSION,
        "at" => Time.now.utc.iso8601 }
    end

    def error_response(id, code, message)
      { "id" => id, "ok" => false, "error" => { "code" => code, "message" => message } }
    end

    def token_ok?(candidate)
      return false if @token.nil? || candidate.nil?

      a = candidate.to_s
      b = @token
      return false unless a.bytesize == b.bytesize

      diff = 0
      a.bytes.each_with_index { |byte, i| diff |= byte ^ b.getbyte(i) }
      diff.zero?
    end

    # ------------------------------------------------------------------
    # methods
    # ------------------------------------------------------------------
    def info(model)
      groups = 0
      components = 0
      entities = model.entities.to_a
      entities.each do |entity|
        if entity.is_a?(Sketchup::Group)
          groups += 1
        elsif entity.is_a?(Sketchup::ComponentInstance)
          components += 1
        end
      end
      tags = model.respond_to?(:layers) ? model.layers.map { |l| l.name.to_s } : []
      {
        "protocol" => VERSION,
        "sketchup" => Sketchup.version.to_s,
        "title" => model.title.to_s,
        "path" => model.path.to_s.empty? ? nil : model.path.to_s,
        "top_level_entities" => entities.size,
        "top_level_groups" => groups,
        "top_level_components" => components,
        "tags" => tags,
        "read_only" => model.respond_to?(:read_only?) ? model.read_only? : false,
        "modified" => model.respond_to?(:modified?) ? model.modified? : nil
      }
    end

    # Raw inventory of every attribute dictionary, including the ones the
    # exporter filters out (SU_*, BIM_Ifc, DynamicComponents). Read-only.
    def attributes(model, params = {})
      limit = (params["limit"] || 200).to_i
      limit = 1 if limit < 1
      entries = CAD2LLM::Annotate.index(model)
      entities = entries.first(limit).map do |entry|
        {
          "persistent_id" => entry["persistent_id"],
          "path" => entry["path"],
          "tag" => entry["tag"],
          "dicts" => dict_summary(entry["entity"].attribute_dictionaries)
        }
      end
      definitions = {}
      entries.each do |entry|
        entity = entry["entity"]
        next unless entity.is_a?(Sketchup::ComponentInstance)

        name = entity.definition.name.to_s
        definitions[name] ||= dict_summary(entity.definition.attribute_dictionaries)
      end
      {
        "checked_at" => Time.now.utc.iso8601,
        "entities_total" => entries.size,
        "entities_with_dicts" => entities.count { |e| !e["dicts"].empty? },
        "entities" => entities,
        "definitions" => definitions,
        "definitions_with_dicts" => definitions.count { |_, v| !v.empty? },
        "model_dicts" => dict_summary(model.respond_to?(:attribute_dictionaries) ? model.attribute_dictionaries : nil)
      }
    end

    def describe(model = Sketchup.active_model, params = {})
      raw = params["targets"] || params["target"]
      items =
        if raw.nil? || raw == "selection"
          model.selection.to_a.map { |entity| describe_entity(entity, []) }
        else
          list = raw.is_a?(Array) ? raw : [raw]
          list.flat_map { |target| describe_one(model, target) }
        end
      { "checked_at" => Time.now.utc.iso8601, "count" => items.size, "items" => items }
    end

    def describe_one(model, target)
      if target.is_a?(String)
        definition = find_definition(model, target)
        return [describe_definition(definition)] if definition

        return [{ "error" => "DEFINITION_UNRESOLVED", "name" => target }]
      end
      if target["kind"] == "definition"
        definition = find_definition(model, target["name"].to_s)
        return [describe_definition(definition)] if definition

        return [{ "error" => "DEFINITION_UNRESOLVED", "name" => target["name"] }]
      end
      entry, error = CAD2LLM::Annotate.resolve(CAD2LLM::Annotate.index(model),
                                               CAD2LLM::Annotate.definition_index(model), target)
      return [{ "error" => error[0], "message" => error[1] }] if entry.nil?

      if entry["kind"] == "definition"
        [describe_definition(entry["definition"])]
      else
        [describe_entity(entry["entity"], entry["path"])]
      end
    end

    def find_definition(model, name)
      return nil if name.nil? || name.to_s.empty?

      definitions = model.definitions
      Array(definitions).find { |definition| definition.name.to_s == name.to_s }
    end

    def describe_entity(entity, path)
      definition = entity.respond_to?(:definition) ? entity.definition : nil
      {
        "kind" => entity.is_a?(Sketchup::ComponentInstance) ? "component" : "group",
        "persistent_id" => entity.persistent_id,
        "name" => entity.name.to_s,
        "path" => path,
        "tag" => entity.layer ? entity.layer.name.to_s : nil,
        "definition_name" => definition ? definition.name.to_s : nil,
        "definition_description" => definition.respond_to?(:description) ? definition.description.to_s : nil,
        "dicts" => dict_summary(entity.attribute_dictionaries),
        "summary" => annotation_summary(entity)
      }
    end

    def describe_definition(definition)
      {
        "kind" => "definition",
        "name" => definition.name.to_s,
        "description" => definition.respond_to?(:description) ? definition.description.to_s : nil,
        "dicts" => dict_summary(definition.attribute_dictionaries),
        "summary" => annotation_summary(definition)
      }
    end

    def annotation_summary(entity)
      cad = read_dict(entity, "cad2llm")
      ifc = read_dict(entity, "ifc")
      parts = [cad["role"], cad["zone"], cad["system"], ifc["type"]]
      parts.compact.map(&:to_s).reject(&:empty?).join(" | ")
    end

    def read_dict(entity, name)
      dicts = entity.respond_to?(:attribute_dictionaries) ? entity.attribute_dictionaries : nil
      dict = Array(dicts).find { |candidate| candidate.name.to_s == name }
      return {} unless dict

      values = {}
      dict.each_pair { |key, value| values[key.to_s] = value }
      values
    end

    def purge_unused_tags(model = Sketchup.active_model)
      before = model.layers.map { |layer| layer.name.to_s }
      model.start_operation("CAD2LLM purge unused tags", true)
      begin
        purged = model.layers.respond_to?(:purge_unused) ? model.layers.purge_unused : []
        model.commit_operation
      rescue StandardError
        model.abort_operation
        raise
      end
      after = model.layers.map { |layer| layer.name.to_s }
      {
        "purged" => Array(purged).map { |layer| layer.respond_to?(:name) ? layer.name.to_s : layer.to_s },
        "before" => before.size,
        "after" => after.size,
        "tags" => after
      }
    end

    def dict_summary(dicts)
      out = {}
      Array(dicts).each do |dict|
        values = {}
        dict.each_pair { |key, value| values[key.to_s] = scalar_preview(value) }
        out[dict.name.to_s] = values
      end
      out
    end

    def scalar_preview(value)
      case value
      when String
        value.length > 120 ? "#{value[0, 120]}...(#{value.length})" : value
      when Numeric, TrueClass, FalseClass, NilClass
        value
      else
        value.class.name.to_s
      end
    end

    def export(model, params)
      schema = params["schema"].to_s
      schema = CAD2LLM::Exporter::SCHEMA_VERSION if schema.empty?

      if params["inline"] == true
        doc = CAD2LLM::Exporter.build(model, schema: schema)
        issues = doc["validation"]["issues"]
        return {
          "path" => nil,
          "bytes" => nil,
          "schema" => doc["schema_version"],
          "nodes" => doc["nodes"].size,
          "issues" => issues.size,
          "errors" => issues.count { |i| i["severity"] == "error" },
          "warnings" => issues.count { |i| i["severity"] == "warning" },
          "document" => doc
        }
      end

      path = params["path"].to_s
      path = default_export_path(model) if path.empty?
      doc = CAD2LLM::Exporter.export_to_file(model, path, schema: schema)
      issues = doc["validation"]["issues"]
      {
        "path" => path,
        "bytes" => File.size(path),
        "schema" => doc["schema_version"],
        "nodes" => doc["nodes"].size,
        "issues" => issues.size,
        "errors" => issues.count { |i| i["severity"] == "error" },
        "warnings" => issues.count { |i| i["severity"] == "warning" }
      }
    end

    def apply(model, params)
      plan = params["plan"]
      if plan.nil? && params["plan_path"]
        plan = JSON.parse(File.read(params["plan_path"].to_s, encoding: "UTF-8"))
      end
      raise ArgumentError, "plan or plan_path is required" if plan.nil?

      normalized = CAD2LLM::Annotate.normalize_plan(plan)
      if normalized["operations"].size > MAX_OPS
        raise ArgumentError, "plan has #{normalized['operations'].size} operations (max #{MAX_OPS})"
      end

      dry_run = params["dry_run"] == true
      pre = post_check(model)
      snapshot = dry_run ? nil : write_snapshot(model, normalized)
      report = CAD2LLM::Annotate.run(normalized, model: model, dry_run: dry_run)
      return report if dry_run

      post = post_check(model)
      drift = geometry_drift(pre, post)
      report["pre_check"] = pre
      report["snapshot"] = snapshot
      report["snapshot_error"] = @snapshot_error
      if drift
        steps = normalized["transaction"] == "single" ? 1 : report.dig("summary", "applied").to_i
        steps = 1 if steps < 1
        steps.times { undo }
        report["rolled_back"] = true
        report["rollback_reason"] = drift
        report["rollback_undo_steps"] = steps
        report["post_check"] = post_check(model)
      else
        report["rolled_back"] = false
        report["post_check"] = post
      end
      report
    end

    # Annotation plans are metadata-only. Any geometry delta between the
    # pre-apply and post-apply snapshot means something else touched the model
    # (or an op has a side effect) -> the caller rolls the transaction back.
    def geometry_drift(pre, post)
      return "node_count #{pre['nodes']} -> #{post['nodes']}" if pre["nodes"] != post["nodes"]

      a = pre["union_bbox_mm"]
      b = post["union_bbox_mm"]
      return nil if a.nil? && b.nil?
      return "union_bbox appeared/disappeared" if a.nil? || b.nil?

      %w[min max].each do |edge|
        a[edge].each_with_index do |value, axis|
          delta = (b[edge][axis].to_f - value.to_f).abs
          return "union_bbox #{edge}[#{axis}] #{value} -> #{b[edge][axis]}" if delta > GEOMETRY_TOL_MM
        end
      end
      nil
    end

    def write_snapshot(model, plan)
      @snapshot_error = nil
      dir = File.join(File.dirname(default_token_path), "snapshots")
      Dir.mkdir(dir) unless Dir.exist?(dir)
      stamp = Time.now.utc.strftime("%Y%m%dT%H%M%SZ")
      slug = plan["name"].to_s.gsub(/[^A-Za-z0-9._-]+/, "_").sub(/\A_+/, "").slice(0, 40)
      slug = "plan" if slug.nil? || slug.empty?
      path = File.join(dir, "#{stamp}_#{slug}.json")
      CAD2LLM::Exporter.export_to_file(model, path)
      path
    rescue StandardError => e
      @snapshot_error = "#{e.class}: #{e.message}"
      nil
    end

    # In-memory re-export: the agent diffs this against the pre-apply export.
    def post_check(model)
      doc = CAD2LLM::Exporter.build(model)
      boxes = doc["nodes"].map { |n| n.dig("spatial", "bbox_mm") }.compact
      mins = boxes.map { |b| [b["min"]["x"], b["min"]["y"], b["min"]["z"]] }
      maxs = boxes.map { |b| [b["max"]["x"], b["max"]["y"], b["max"]["z"]] }
      union = if boxes.empty?
                nil
              else
                { "min" => mins.transpose.map(&:min).map { |v| v.round(3) },
                  "max" => maxs.transpose.map(&:max).map { |v| v.round(3) } }
              end
      {
        "nodes" => doc["nodes"].size,
        "union_bbox_mm" => union,
        "tag_distribution" => doc["nodes"].group_by { |n| n["tag"] }.transform_values(&:size).sort.to_h,
        "issues" => doc["validation"]["issues"].size
      }
    end

    def undo
      unless defined?(Sketchup) && Sketchup.respond_to?(:send_action)
        raise "undo is not available in this environment"
      end

      Sketchup.send_action("editUndo:")
      { "undone" => true, "via" => "Sketchup.send_action('editUndo:')" }
    end

    def default_export_path(model)
      path = model.path.to_s
      dir = path.empty? ? Dir.pwd : File.dirname(path)
      base = path.empty? ? model.title.to_s.strip : File.basename(path, ".*")
      base = "SketchUp_Model" if base.empty?
      File.join(dir, "#{base}_cad2llm_v4.json")
    end
  end
end
