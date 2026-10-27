# frozen_string_literal: true
#
# CAD2LLM — SketchUp extension entry point.
# Loads the exporter library and exposes CAD2LLM.export.
#
# Console:  CAD2LLM.export(profile: "geometry")
# Menu:     Extensions > CAD2LLM > Export spatial JSON
#
require "json"
require "time"
require "sketchup.rb"

module CAD2LLM
  LIB_PATH = File.join(__dir__, "lib", "exporter.rb")
  Sketchup.load(LIB_PATH) unless defined?(CAD2LLM::Exporter)

  module_function

  def export(profile: Exporter::PROFILE_SPATIAL, model: Sketchup.active_model, path: nil,
             interactive: true, compact: false, tag_audit: true)
    path ||= default_path(model, profile, compact: compact)
    return nil if path.nil?

    doc = Exporter.export_to_file(model, path, profile: profile, compact: compact,
                                 tag_audit: tag_audit)
    nodes = doc["nodes"].size
    mode = compact ? " compact" : ""
    message = [
      "CAD2LLM #{profile}#{mode} export OK (#{nodes} nodes)",
      tag_consistency_line(doc),
      path
    ].compact.join("\n")
    puts message
    UI.messagebox(message) if interactive
    path
  rescue StandardError => e
    warn "CAD2LLM export failed: #{e.class}: #{e.message}"
    UI.messagebox("CAD2LLM export failed:\n#{e.class}: #{e.message}") if interactive
    nil
  end

  def export_selection(profile: Exporter::PROFILE_SPATIAL, model: Sketchup.active_model, path: nil,
                       interactive: true, compact: false, tag_audit: true)
    selected = model.selection.to_a
    if selected.empty?
      UI.messagebox("CAD2LLM: nothing selected.") if interactive
      return nil
    end
    path ||= default_path(model, profile, compact: compact).to_s.sub(".json", "_selection.json")
    doc = Exporter.export_to_file(model, path, profile: profile, entities: selected,
                                 compact: compact, tag_audit: tag_audit)
    mode = compact ? " compact" : ""
    message = [
      "CAD2LLM #{profile}#{mode} selection export OK (#{doc['nodes'].size} nodes)",
      tag_consistency_line(doc),
      path
    ].compact.join("\n")
    puts message
    UI.messagebox(message) if interactive
    path
  end

  def tag_consistency_line(doc)
    data = doc.dig("validation", "tag_consistency")
    return nil if data.nil?

    if data["status"] == "ok"
      "tags OK (#{data['checked_definitions']} definitions, #{data['checked_instances']} instances)"
    else
      "tags WARN #{data['mismatch_definitions']}/#{data['checked_definitions']} definitions, " \
        "#{data['mismatch_instances']}/#{data['checked_instances']} instances"
    end
  end

  def default_path(model, profile, compact: false)
    base = profile == Exporter::PROFILE_GEOMETRY ? "_cad2llm_v4_geometry" : "_cad2llm_v4"
    suffix = "#{base}#{compact ? '_compact' : ''}.json"
    model_path = model.path.to_s
    unless model_path.empty?
      title = File.basename(model_path, ".*")
      return File.join(File.dirname(model_path), title + suffix)
    end

    chosen = UI.savepanel("Save CAD2LLM export", nil, "cad2llm_export#{suffix}")
    return nil if chosen.nil?

    chosen.end_with?(".json") ? chosen : "#{chosen}.json"
  end
end
