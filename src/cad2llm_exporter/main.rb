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
             interactive: true, compact: false)
    path ||= default_path(model, profile, compact: compact)
    return nil if path.nil?

    doc = Exporter.export_to_file(model, path, profile: profile, compact: compact)
    nodes = doc["nodes"].size
    mode = compact ? " compact" : ""
    message = "CAD2LLM #{profile}#{mode} export OK (#{nodes} nodes)\n#{path}"
    puts message
    UI.messagebox(message) if interactive
    path
  rescue StandardError => e
    warn "CAD2LLM export failed: #{e.class}: #{e.message}"
    UI.messagebox("CAD2LLM export failed:\n#{e.class}: #{e.message}") if interactive
    nil
  end

  def export_selection(profile: Exporter::PROFILE_SPATIAL, model: Sketchup.active_model, path: nil,
                       interactive: true, compact: false)
    selected = model.selection.to_a
    if selected.empty?
      UI.messagebox("CAD2LLM: nothing selected.") if interactive
      return nil
    end
    path ||= default_path(model, profile, compact: compact).to_s.sub(".json", "_selection.json")
    doc = Exporter.export_to_file(model, path, profile: profile, entities: selected, compact: compact)
    mode = compact ? " compact" : ""
    message = "CAD2LLM #{profile}#{mode} selection export OK (#{doc['nodes'].size} nodes)\n#{path}"
    puts message
    UI.messagebox(message) if interactive
    path
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
