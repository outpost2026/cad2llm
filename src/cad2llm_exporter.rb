# frozen_string_literal: true
#
# CAD2LLM Exporter — SketchUp extension loader.
#
require "sketchup.rb"
require "extensions.rb"
require "json"

require_relative "cad2llm_exporter/lib/bridge"

module CAD2LLM
  unless file_loaded?(__FILE__)
    ex = SketchupExtension.new("CAD2LLM Exporter", "cad2llm_exporter/main")
    ex.description = "Export SketchUp models to deterministic spatial JSON (schema v4) for LLM/RAG pipelines."
    ex.version     = "4.2.2"
    ex.copyright   = "MIT License"
    ex.creator     = "CAD2LLM Community"
    Sketchup.register_extension(ex, true)

    menu = UI.menu("Extensions")
    submenu = menu.add_submenu("CAD2LLM")
    submenu.add_item("Export spatial JSON (v4)") { CAD2LLM.export }
    submenu.add_item("Export legacy JSON (v3)") { CAD2LLM.export_v3 }
    submenu.add_separator
    submenu.add_item("Export selected entities (v4)") { CAD2LLM.export_selection }
    submenu.add_separator
    submenu.add_item("Start local bridge (127.0.0.1:9877)") do
      begin
        state = CAD2LLM::Bridge.start
        UI.messagebox("CAD2LLM bridge: #{state['host']}:#{state['port']}\nToken file: #{state['token_file']}")
      rescue StandardError => e
        UI.messagebox("CAD2LLM bridge failed: #{e.class}: #{e.message}")
      end
    end
    submenu.add_item("Stop local bridge") do
      CAD2LLM::Bridge.stop
      UI.messagebox("CAD2LLM bridge stopped.")
    end
    submenu.add_separator
    submenu.add_item("Inspector panel") { CAD2LLM.show_inspector_panel }
    submenu.add_item("Inspector (messagebox)") { CAD2LLM.show_inspector }
    submenu.add_item("Purge unused tags") { CAD2LLM.purge_unused_tags_ui }

    file_loaded(__FILE__)
  end

  def self.show_inspector
    model = Sketchup.active_model
    result = CAD2LLM::Bridge.describe(model, {})
    if result["count"].zero?
      UI.messagebox("CAD2LLM Inspector: nothing selected.")
      return
    end

    text = result["items"].map { |item| format_annotation(item) }.join("\n\n")
    puts text
    UI.messagebox(text.length > 1800 ? "#{text[0, 1800]}\n... (full text in Ruby Console)" : text)
  rescue StandardError => e
    UI.messagebox("CAD2LLM Inspector failed: #{e.class}: #{e.message}")
  end

  def self.show_inspector_panel
    if @inspector_dialog && @inspector_dialog.visible?
      @inspector_dialog.bring_to_front
      return
    end

    html = File.join(__dir__, "cad2llm_exporter", "html", "inspector.html")
    dialog = UI::HtmlDialog.new(
      dialog_title: "CAD2LLM Inspector",
      preferences_key: "cad2llm_inspector",
      width: 430, height: 640, resizable: true
    )
    dialog.set_file(html)
    dialog.add_action_callback("refresh") do |_context|
      data = CAD2LLM::Bridge.describe(Sketchup.active_model, {})
      dialog.execute_script("cad2llmRender(#{JSON.generate(data)})")
    rescue StandardError => e
      dialog.execute_script("cad2llmRender(#{JSON.generate({ 'items' => [{ 'error' => 'EXCEPTION', 'message' => e.message }] })})")
    end
    dialog.set_on_closed { @inspector_dialog = nil }
    dialog.show
    @inspector_dialog = dialog
    puts "CAD2LLM inspector panel opened (#{html})"
  rescue StandardError => e
    UI.messagebox("CAD2LLM Inspector panel failed: #{e.class}: #{e.message}")
  end

  def self.format_annotation(item)
    return "ERROR #{item['error']}: #{item['message']}" if item["error"]

    lines = []
    if item["kind"] == "definition"
      lines << "DEFINICE: #{item['name']}"
      lines << "popis: #{item['description']}" unless item["description"].to_s.empty?
    else
      lines << "#{item['name']} (pid #{item['persistent_id']})"
      lines << "tag: #{item['tag']}"
      lines << "definice: #{item['definition_name']}"
      unless item["definition_description"].to_s.empty?
        lines << "popis: #{item['definition_description']}"
      end
    end
    (item["dicts"] || {}).each do |dict_name, values|
      pairs = values.map { |k, v| "#{k}=#{v.to_s.length > 60 ? "#{v.to_s[0, 60]}..." : v}" }
      lines << "  [#{dict_name}] #{pairs.join(', ')}"
    end
    lines.join("\n")
  end

  def self.purge_unused_tags_ui
    model = Sketchup.active_model
    answer = UI.messagebox("Smazat nepouzité tagy? (vratné přes Undo)", MB_YESNO)
    return unless answer == IDYES

    result = CAD2LLM::Bridge.purge_unused_tags(model)
    purged = result["purged"]
    UI.messagebox("Purged #{purged.size} tag(s): #{purged.empty? ? '-' : purged.join(', ')}\n" \
                  "Tags: #{result['before']} -> #{result['after']}")
  rescue StandardError => e
    UI.messagebox("Purge failed: #{e.class}: #{e.message}")
  end
end
