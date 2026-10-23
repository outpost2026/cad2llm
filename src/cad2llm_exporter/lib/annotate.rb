# frozen_string_literal: true
#
# CAD2LLM::Annotate — apply a declarative annotation plan to the active model.
#
# Only metadata (attribute dictionaries), tags (layers) and names are mutated.
# Geometry is never touched; one operation == one undo step.
#
# Plan format: cad2llm/annotate_plan/v1 (docs/schema/annotate_plan.schema.json)
# Report:      cad2llm/annotate_report/v1
#
# Target resolution is deterministic and refuses ambiguity:
#   1. persistent_id
#   2. hierarchy_path (exact, then normalized)
#   3. name + optional tag / definition_name (exact, then normalized)
# A target that matches more than one entity is an error, never a guess.
#
require "json"
require "time"

module CAD2LLM
  module Annotate
    PLAN_SCHEMA = "cad2llm/annotate_plan/v1"
    REPORT_SCHEMA = "cad2llm/annotate_report/v1"
    OPS = %w[set_attribute set_tag rename set_description].freeze
    TRANSACTIONS = %w[per_op single].freeze
    RESERVED_DICT_PREFIX = "SU_"

    module_function

    # Entry point used by the console / bridge.
    #   plan     - parsed plan Hash
    #   dry_run  - when true nothing is written, changes are reported as "planned"
    #   report_path - optional file path for the JSON report
    def run(plan, model: Sketchup.active_model, dry_run: false, report_path: nil)
      plan = normalize_plan(plan)
      entries = index(model)
      defs = definition_index(model)
      results = []
      single = plan["transaction"] == "single" && !dry_run
      model.start_operation("CAD2LLM #{plan['name'] || 'plan'}", true) if single

      begin
        plan["operations"].each_with_index do |op, index|
          results << apply_one(op, index, entries, defs, model, dry_run, single)
          if !dry_run && results.last["status"] == "applied" && mutates_index?(op)
            entries = index(model)
            defs = definition_index(model)
          end
        end
        model.commit_operation if single
      rescue StandardError
        model.abort_operation if single
        raise
      end

      report = {
        "schema" => REPORT_SCHEMA,
        "plan_name" => plan["name"],
        "model" => model.title.to_s,
        "model_path" => model.path.to_s.empty? ? nil : model.path.to_s,
        "dry_run" => dry_run,
        "transaction" => plan["transaction"],
        "checked_at" => Time.now.utc.iso8601,
        "operations" => results,
        "summary" => summarize(results)
      }
      File.write(report_path, JSON.pretty_generate(report) + "\n") if report_path
      report
    end

    def run_file(path, model: Sketchup.active_model, dry_run: false, report_path: nil)
      plan = JSON.parse(File.read(path, encoding: "UTF-8"))
      run(plan, model: model, dry_run: dry_run, report_path: report_path)
    end

    # ------------------------------------------------------------------
    # plan validation
    # ------------------------------------------------------------------
    def normalize_plan(plan)
      raise ArgumentError, "plan must be a Hash" unless plan.is_a?(Hash)

      schema = plan["schema"] || plan["schema_version"] || PLAN_SCHEMA
      raise ArgumentError, "unsupported plan schema #{schema.inspect}" unless schema == PLAN_SCHEMA

      ops = plan["operations"]
      raise ArgumentError, "plan.operations must be a non-empty Array" unless ops.is_a?(Array) && !ops.empty?

      ops.each_with_index do |op, i|
        raise ArgumentError, "operation #{i} must be a Hash" unless op.is_a?(Hash)

        kind = op["op"]
        raise ArgumentError, "operation #{i}: unknown op #{kind.inspect} (allowed: #{OPS.join(', ')})" unless OPS.include?(kind)
        raise ArgumentError, "operation #{i}: missing target" unless op["target"].is_a?(Hash)
        kind = op["target"]["kind"]
        unless kind.nil? || %w[entity definition].include?(kind)
          raise ArgumentError, "operation #{i}: target.kind must be entity or definition"
        end
      end

      transaction = (plan["transaction"] || "per_op").to_s
      raise ArgumentError, "transaction must be per_op or single" unless TRANSACTIONS.include?(transaction)

      { "schema" => PLAN_SCHEMA, "name" => plan["name"], "operations" => ops,
        "transaction" => transaction }
    end

    # ------------------------------------------------------------------
    # index / resolution
    # ------------------------------------------------------------------
    def index(model)
      out = []
      walk(model.entities, [], out)
      out
    end

    def definition_index(model)
      defs = model.respond_to?(:definitions) ? model.definitions.to_a : []
      defs.map do |definition|
        name = definition.name.to_s.strip
        { "kind" => "definition", "definition" => definition, "name" => name, "tag" => nil }
      end
    end

    def walk(entities, path, out)
      return out unless entities

      entities.each do |entity|
        next unless entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)

        name = entity.name.to_s.strip
        name = "Unnamed" if name.empty?
        node_path = path + [name]
        out << {
          "entity" => entity,
          "persistent_id" => entity.persistent_id,
          "name" => name,
          "path" => node_path,
          "tag" => tag_of(entity),
          "definition_name" => entity.is_a?(Sketchup::ComponentInstance) ? entity.definition.name.to_s : nil
        }

        kids = entity.is_a?(Sketchup::Group) ? entity.entities : entity.definition.entities
        walk(kids, node_path, out)
      end
      out
    end

    def tag_of(entity)
      entity.layer ? entity.layer.name.to_s : nil
    rescue StandardError
      nil
    end

    # Returns [entry, nil] or [nil, [code, message]]
    def resolve(entries, defs, target)
      return resolve_definition(defs, target) if target["kind"] == "definition"

      strategies = []
      strategies << :persistent_id if target.key?("persistent_id")
      strategies << :path if target.key?("hierarchy_path")
      strategies << :name if target.key?("name")

      if strategies.empty?
        return [nil, ["INVALID_TARGET", "target needs persistent_id, hierarchy_path or name"]]
      end

      strategies.each do |strategy|
        candidates = candidates_for(entries, target, strategy)
        next if candidates.empty?

        filtered = filter_candidates(candidates, target, strategy)
        return [filtered.first, nil] if filtered.size == 1
        if filtered.size > 1
          return [nil, ["AMBIGUOUS_TARGET",
                        "ambiguous target via #{strategy}: #{filtered.size} matches (#{describe(filtered)})"]]
        end

        return [nil, ["NO_MATCH_AFTER_FILTER",
                      "target matched via #{strategy} but path/tag/definition filter removed all"]]
      end

      [nil, ["TARGET_UNRESOLVED", "target not found (#{target.inspect})"]]
    end

    def resolve_definition(defs, target)
      raw = target["name"].to_s
      return [nil, ["INVALID_TARGET", "definition target needs a name"]] if raw.empty?

      exact = defs.select { |d| d["name"] == raw }
      candidates = exact.empty? ? defs.select { |d| normalize(d["name"]) == normalize(raw) } : exact
      return [candidates.first, nil] if candidates.size == 1
      if candidates.size > 1
        return [nil, ["AMBIGUOUS_TARGET", "definition name #{raw.inspect} matches #{candidates.size} definitions"]]
      end

      [nil, ["TARGET_UNRESOLVED", "definition #{raw.inspect} not found"]]
    end

    def candidates_for(entries, target, strategy)
      case strategy
      when :persistent_id
        pid = target["persistent_id"]
        entries.select { |e| e["persistent_id"] == pid }
      when :path
        want = Array(target["hierarchy_path"]).map(&:to_s)
        exact = entries.select { |e| e["path"] == want }
        return exact unless exact.empty?

        norm = want.map { |s| normalize(s) }
        entries.select { |e| e["path"].map { |s| normalize(s) } == norm }
      when :name
        want = target["name"].to_s
        exact = entries.select { |e| e["name"] == want }
        return exact unless exact.empty?

        entries.select { |e| normalize(e["name"]) == normalize(want) }
      else
        []
      end
    end

    # pid can repeat across instances of a shared definition -> path narrows it.
    def filter_candidates(candidates, target, strategy)
      out = candidates
      if strategy != :path && target.key?("hierarchy_path")
        want = Array(target["hierarchy_path"]).map(&:to_s)
        norm = want.map { |s| normalize(s) }
        out = out.select { |e| e["path"] == want || e["path"].map { |s| normalize(s) } == norm }
      end
      if target.key?("tag") && !target["tag"].nil?
        out = out.select { |e| normalize(e["tag"].to_s) == normalize(target["tag"].to_s) }
      end
      if target.key?("definition_name") && !target["definition_name"].nil?
        out = out.select { |e| normalize(e["definition_name"].to_s) == normalize(target["definition_name"].to_s) }
      end
      out
    end

    def normalize(value)
      value.to_s.downcase.gsub(/[_\s]+/, " ").strip
    end

    def describe(entries)
      entries.first(5).map { |e| e["path"].join("/") }.join(" | ")
    end

    # ------------------------------------------------------------------
    # operations
    # ------------------------------------------------------------------
    def apply_one(op, index, entries, defs, model, dry_run, in_transaction = false)
      result = { "index" => index, "op" => op["op"], "target" => op["target"] }
      entry, error = resolve(entries, defs, op["target"])
      if entry.nil?
        code, message = error
        return result.merge("status" => "error", "code" => code, "message" => message)
      end

      definition = entry["kind"] == "definition"
      subject = definition ? entry["definition"] : entry["entity"]
      if definition
        result["uid"] = "definition:#{entry['name']}"
        result["resolved_definition"] = entry["name"]
      else
        result["uid"] = "#{entry['persistent_id']}@#{entry['path'].join('/')}"
        result["resolved_path"] = entry["path"]
      end

      case op["op"]
      when "set_attribute"
        validated = validate_set_attribute(op, result)
        return validated if validated

        apply_set_attribute(subject, model, op, result, dry_run, in_transaction)
      when "set_tag"
        if definition
          return result.merge("status" => "error", "code" => "INVALID_OP",
                              "message" => "definitions have no tag; target an instance")
        end

        apply_set_tag(entry, model, op, result, dry_run, in_transaction)
      when "rename"
        apply_rename(entry, subject, model, op, result, dry_run, in_transaction)
      when "set_description"
        target_def = definition ? subject : (subject.respond_to?(:definition) ? subject.definition : nil)
        if target_def.nil?
          return result.merge("status" => "error", "code" => "INVALID_TARGET",
                              "message" => "entity has no component definition")
        end

        result["resolved_definition"] = target_def.name.to_s
        apply_set_description(target_def, model, op, result, dry_run, in_transaction)
      end
    rescue StandardError => e
      result.merge("status" => "error", "code" => "EXCEPTION", "message" => "#{e.class}: #{e.message}")
    end

    def validate_set_attribute(op, result)
      dict = op["dict"].to_s
      key = op["key"].to_s
      if dict.empty? || key.empty?
        return result.merge("status" => "error", "code" => "INVALID_OP", "message" => "dict and key are required")
      end
      if dict.upcase.start_with?(RESERVED_DICT_PREFIX)
        return result.merge("status" => "error", "code" => "RESERVED_DICT",
                            "message" => "#{dict} is reserved by SketchUp")
      end
      unless op.key?("value")
        return result.merge("status" => "error", "code" => "INVALID_OP", "message" => "value is required")
      end

      value = op["value"]
      unless value.nil? || value.is_a?(String) || value.is_a?(Numeric) || value == true || value == false
        return result.merge("status" => "error", "code" => "INVALID_VALUE",
                            "message" => "value must be a JSON scalar, got #{value.class}")
      end
      nil
    end

    def apply_set_attribute(entity, model, op, result, dry_run, in_transaction = false)
      dict = op["dict"].to_s
      key = op["key"].to_s
      value = op["value"]
      current = entity.get_attribute(dict, key, :__missing__)
      if current != :__missing__ && current == value
        return result.merge("status" => "unchanged", "message" => "#{dict}/#{key} already #{value.inspect}")
      end

      return result.merge("status" => "planned", "message" => "would set #{dict}/#{key}") if dry_run

      in_transaction(model, in_transaction, "CAD2LLM set_attribute #{dict}/#{key}") do
        entity.set_attribute(dict, key, value)
      end
      result.merge("status" => "applied", "previous" => current == :__missing__ ? nil : current)
    end

    def apply_set_tag(entry, model, op, result, dry_run, in_transaction = false)
      tag = op["tag"].to_s.strip
      if tag.empty?
        return result.merge("status" => "error", "code" => "INVALID_OP", "message" => "tag is required")
      end
      if normalize(entry["tag"].to_s) == normalize(tag)
        return result.merge("status" => "unchanged", "message" => "already on tag #{tag}")
      end

      return result.merge("status" => "planned", "message" => "would move to tag #{tag}") if dry_run

      layer = model.layers[tag] || model.layers.add(tag)
      in_transaction(model, in_transaction, "CAD2LLM set_tag #{tag}") do
        entry["entity"].layer = layer
      end
      result.merge("status" => "applied", "previous" => entry["tag"], "tag" => tag)
    end

    def apply_rename(entry, subject, model, op, result, dry_run, in_transaction = false)
      name = op["name"].to_s.strip
      if name.empty?
        return result.merge("status" => "error", "code" => "INVALID_OP", "message" => "name is required")
      end
      if entry["name"] == name
        return result.merge("status" => "unchanged", "message" => "already named #{name}")
      end

      return result.merge("status" => "planned", "message" => "would rename to #{name}") if dry_run

      in_transaction(model, in_transaction, "CAD2LLM rename #{name}") do
        subject.name = name
      end
      result.merge("status" => "applied", "previous" => entry["name"], "name" => name)
    end

    def apply_set_description(definition, model, op, result, dry_run, in_transaction = false)
      text = op["description"].to_s
      if text.strip.empty?
        return result.merge("status" => "error", "code" => "INVALID_OP",
                            "message" => "description is required")
      end

      current = definition.respond_to?(:description) ? definition.description.to_s : ""
      if current == text
        return result.merge("status" => "unchanged", "message" => "description already set")
      end

      return result.merge("status" => "planned", "message" => "would set description") if dry_run

      in_transaction(model, in_transaction, "CAD2LLM set_description #{definition.name}") do
        if op["preserve_original"] && !current.strip.empty? &&
           definition.get_attribute("cad2llm", "original_description", :__missing__) == :__missing__
          definition.set_attribute("cad2llm", "original_description", current)
        end
        definition.description = text
      end
      result.merge("status" => "applied", "previous_description" => current)
    end

    def in_transaction(model, in_transaction, name)
      return yield if in_transaction

      with_operation(model, name) { yield }
    end

    def with_operation(model, name)
      model.start_operation(name, true)
      begin
        yield
        model.commit_operation
      rescue StandardError
        model.abort_operation
        raise
      end
    end

    def mutates_index?(op)
      %w[set_tag rename].include?(op["op"])
    end

    def summarize(results)
      counts = Hash.new(0)
      results.each { |r| counts[r["status"]] += 1 }
      {
        "total" => results.size,
        "applied" => counts["applied"],
        "planned" => counts["planned"],
        "unchanged" => counts["unchanged"],
        "errors" => counts["error"]
      }
    end
  end
end
