require 'sketchup.rb'
require 'extensions.rb'

module CAD2LLM
  unless file_loaded?(__FILE__)
    ex = SketchupExtension.new('CAD2LLM Exporter', 'cad2llm_exporter/main')
    ex.description = 'Export SketchUp models to spatial JSON optimized for LLMs and RAG systems.'
    ex.version     = '2.0.0'
    ex.copyright   = 'MIT License'
    ex.creator     = 'CAD2LLM Community'
    Sketchup.register_extension(ex, true)
    file_loaded(__FILE__)
  end
end
