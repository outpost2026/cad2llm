# frozen_string_literal: true
#
# CAD2LLM Exporter — SketchUp extension loader.
#
require "sketchup.rb"
require "extensions.rb"

module CAD2LLM
  unless file_loaded?(__FILE__)
    ex = SketchupExtension.new("CAD2LLM Exporter", "cad2llm_exporter/main")
    ex.description = "Export SketchUp models to deterministic spatial JSON (schema v4) for LLM/RAG pipelines."
    ex.version     = "4.0.0"
    ex.copyright   = "MIT License"
    ex.creator     = "CAD2LLM Community"
    Sketchup.register_extension(ex, true)

    menu = UI.menu("Extensions")
    submenu = menu.add_submenu("CAD2LLM")
    submenu.add_item("Export spatial JSON (v4)") { CAD2LLM.export }
    submenu.add_item("Export legacy JSON (v3)") { CAD2LLM.export_v3 }
    submenu.add_separator
    submenu.add_item("Export selected entities (v4)") { CAD2LLM.export_selection }

    file_loaded(__FILE__)
  end
end
