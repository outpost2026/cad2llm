# frozen_string_literal: true
#
# Headless tests for CAD2LLM::Exporter (schema v4) + main.rb entry point.
# Run: ruby spec/run_all.rb

require "json"
require "tmpdir"

ROOT = File.expand_path("..", __dir__)
$LOAD_PATH.unshift File.join(__dir__, "support")

require_relative "sketchup_stub"
require File.join(ROOT, "src", "cad2llm_exporter", "lib", "exporter.rb")
require File.join(ROOT, "src", "cad2llm_exporter", "main.rb")

$failures = []
$checks = 0

def check(label)
  $checks += 1
  ok = yield
  $failures << label unless ok
  puts "#{ok ? 'ok  ' : 'FAIL'} #{label}"
end

def eq(label, actual, expected)
  check("#{label} (got #{actual.inspect}, want #{expected.inspect})") { actual == expected }
end

def near(label, actual, expected, tol = 0.01)
  check("#{label} (got #{actual}, want #{expected})") { (actual - expected).abs <= tol }
end

def model_with(entities, title: "mistnost_2_sk24", path: "")
  m = Sketchup::Model.new
  m.entities = entities
  m.title = title
  m.path = path
  Sketchup.model = m
  m
end

# ---------------------------------------------------------------- 1. groups
group = mk_group(name: "bed_v1", pid: 101, pos: [100, 0, 0], size_in: [10, 10, 10])
model_with([group])
doc = CAD2LLM::Exporter.build(Sketchup.active_model)
bed = doc["nodes"].first

eq("schema_version", doc["schema_version"], "4.0")
eq("units", doc["project_metadata"]["units"], "mm")
eq("group bbox is world-space (no double transform)", bed["spatial"]["bbox_mm"]["min"]["x"], 2540.0)
near("group size mm", bed["spatial"]["size_mm"]["x"], 254.0)
eq("group origin mm", bed["spatial"]["origin_mm"]["x"], 2540.0)
eq("matrix translation == origin", bed["spatial"]["matrix_4x4"][0][3], 2540.0)
eq("matrix row3", bed["spatial"]["matrix_4x4"][3], [0.0, 0.0, 0.0, 1.0])
eq("matrix not corrupted by gsub", bed["spatial"]["matrix_4x4"][0][0], 1.0)
eq("tag exported", bed["tag"], "walls")

# ------------------------------------------------------------ 2. components
comp = mk_component(name: "stove_main", pid: 102, def_name: "Kamna_Priti_mini",
                    pos: [10, 20, 30], size_in: [10, 20, 30],
                    def_description: "Kachlova kamna Prity mini, 4 kW, topeni drevem")
rot = mk_component(name: "beam_rotated", pid: 103, def_name: "Beam",
                   size_in: [10, 20, 30],
                   transformation: Geom::Transformation.rotation_z_deg(90, Geom::Point3d.new(0, 0, 0)))
model_with([comp, rot])
doc = CAD2LLM::Exporter.build(Sketchup.active_model)
stove = doc["nodes"].find { |n| n["name"] == "stove_main" }
rotn = doc["nodes"].find { |n| n["name"] == "beam_rotated" }

eq("component definition_name", stove["definition_name"], "Kamna_Priti_mini")
eq("component definition_description", stove["definition_description"],
   "Kachlova kamna Prity mini, 4 kW, topeni drevem")
check("group definition_description is nil") { bed.key?("definition_description") && bed["definition_description"].nil? }
check("capability definition_description") { doc["project_metadata"]["capabilities"]["definition_description"] == true }
eq("component entity_type", stove["entity_type"], "component")
eq("component origin mm", stove["spatial"]["origin_mm"], { "x" => 254.0, "y" => 508.0, "z" => 762.0 })
eq("component size mm", stove["spatial"]["size_mm"], { "x" => 254.0, "y" => 508.0, "z" => 762.0 })
near("rotation z 90", rotn["spatial"]["rotation_deg"]["z"], 90.0, 0.01)
near("rotated bbox x", rotn["spatial"]["size_mm"]["x"], 508.0)
near("rotated bbox y", rotn["spatial"]["size_mm"]["y"], 254.0)

# -------------------------------------------------------------- 3. metadata
meta_comp = mk_component(name: "lfp_cell", pid: 201, def_name: "LFP_cell", size_in: [1, 1, 1],
                         dicts: {
                           "SU_InstanceSet" => { "Owner" => "", "Status" => "" },
                           "energy" => { "voltage" => "24", "capacity_ah" => "100.5", "note" => "ok" }
                         })
model_with([meta_comp])
node = CAD2LLM::Exporter.build(Sketchup.active_model)["nodes"].first
check("SU_InstanceSet filtered") { !node["metadata"].key?("SU_InstanceSet") }
eq("int coercion", node["metadata"]["energy"]["voltage"], 24)
eq("float coercion", node["metadata"]["energy"]["capacity_ah"], 100.5)
eq("string preserved", node["metadata"]["energy"]["note"], "ok")

# --------------------------------------------- 4. shared definition children
defn = Sketchup::ComponentDefinition.new("battery_def", Sketchup::BoundingBox.sized(10, 10, 10))
defn.entities << mk_group(name: "cell", pid: 999, pos: [0, 0, 0], size_in: [5, 5, 5])
inst_a = Sketchup::ComponentInstance.new(name: "battery_a", pid: 301, transformation: t_in(0, 0, 0), definition: defn)
inst_b = Sketchup::ComponentInstance.new(name: "battery_b", pid: 302, transformation: t_in(20, 0, 0), definition: defn)
inst_a.layer = Sketchup::Layer.new("energy_nodes")
inst_b.layer = Sketchup::Layer.new("energy_nodes")
model_with([inst_a, inst_b])
nodes = CAD2LLM::Exporter.build(Sketchup.active_model)["nodes"]
cells = nodes.select { |n| n["name"] == "cell" }
eq("two children emitted", cells.size, 2)
check("child uid unique despite shared persistent_id") { cells.map { |c| c["uid"] }.uniq.size == 2 }
eq("child parent uid == instance uid", cells[1]["parent_uid"], nodes.find { |n| n["name"] == "battery_b" }["uid"])
eq("child path", cells[1]["hierarchy_path"], ["battery_b", "cell"])

# -------------------------------------------------------- 5. nested geometry
parent = mk_group(name: "zone_bed", pid: 401, pos: [100, 0, 0], size_in: [50, 50, 50])
parent.entities << mk_group(name: "lfp_cell", pid: 402, pos: [50, 0, 0], size_in: [5, 5, 5], world_pos: [150, 0, 0])
model_with([parent])
nested = CAD2LLM::Exporter.build(Sketchup.active_model)["nodes"].find { |n| n["name"] == "lfp_cell" }
eq("nested world bbox", nested["spatial"]["bbox_mm"]["min"]["x"], 3810.0)

# ------------------------------------------------------ 6. geometry profile
box = mk_group(name: "wall_box", pid: 501, pos: [100, 0, 0], size_in: [10, 20, 30])
mk_box_geometry(box, size_in: [10, 20, 30], material: "Brick")
model_with([box])
geo_doc = CAD2LLM::Exporter.build(Sketchup.active_model, profile: "geometry")
geo_node = geo_doc["nodes"].first
geo = geo_node["geometry"]
eq("geometry profile", geo_doc["export_profile"], "geometry")
eq("geometry capability", geo_doc["project_metadata"]["capabilities"]["geometry"], true)
eq("geometry schema capability", geo_doc["project_metadata"]["capabilities"]["geometry_schema"],
   "cad2llm/geometry/v1")
eq("geometry schema", geo["schema"], "cad2llm/geometry/v1")
eq("geometry scope", geo["scope"], "direct")
eq("geometry vertex count", geo["counts"]["vertices"], 8)
eq("geometry edge count", geo["counts"]["edges"], 12)
eq("geometry face count", geo["counts"]["faces"], 6)
eq("geometry triangle count", geo["counts"]["triangles"], 12)
eq("geometry closed", geo["closed"], true)
eq("geometry oriented", geo["oriented"], true)
near("geometry volume mm3", geo["volume_mm3"], 98_322_384.0, 1.0)
eq("geometry hash length", geo["hash"].length, 16)
eq("geometry material", geo["faces"].first["material"], "Brick")
check("geometry vertices world mm") { geo["vertices"].any? { |v| v[0] == 2540.0 } }
check("geometry normals unit") do
  geo["faces"].all? { |f| Math.sqrt(f["normal"].sum { |v| v * v }).round(6) == 1.0 }
end
check("geometry keeps spatial") { geo_node["spatial"]["bbox_mm"]["min"]["x"] == 2540.0 }
geo2 = CAD2LLM::Exporter.build(Sketchup.active_model, profile: "geometry")["nodes"].first["geometry"]
eq("geometry hash deterministic", geo2["hash"], geo["hash"])

comp_box = mk_component(name: "box_comp", pid: 502, pos: [10, 20, 30], size_in: [10, 10, 10],
                        def_name: "Box_def")
mk_box_geometry(comp_box, size_in: [10, 10, 10])
model_with([comp_box])
comp_geo = CAD2LLM::Exporter.build(Sketchup.active_model, profile: "geometry")["nodes"].first["geometry"]
near("component geometry world x", comp_geo["vertices"].map { |v| v[0] }.min, 254.0)
near("component geometry world y", comp_geo["vertices"].map { |v| v[1] }.min, 508.0)
near("component geometry world z", comp_geo["vertices"].map { |v| v[2] }.min, 762.0)
eq("component geometry volume", comp_geo["volume_mm3"], 16_387_064.0)

empty_geo = mk_group(name: "empty_geo", pid: 503, size_in: [0, 0, 0])
model_with([empty_geo])
empty_node = CAD2LLM::Exporter.build(Sketchup.active_model, profile: "geometry")["nodes"].first
eq("empty geometry status", empty_node["geometry"]["status"], "empty")
eq("empty geometry faces", empty_node["geometry"]["counts"]["faces"], 0)

hole_group = mk_group(name: "face_with_hole", pid: 504, size_in: [0, 0, 0])
outer_verts = [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]].map do |c|
  Sketchup::Vertex.new(Geom::Point3d.new(*c))
end
hole_verts = [[4, 4, 0], [6, 4, 0], [6, 6, 0], [4, 6, 0]].map do |c|
  Sketchup::Vertex.new(Geom::Point3d.new(*c))
end
hole_group.entities << Sketchup::Face.new(outer_verts, holes: [hole_verts])
model_with([hole_group])
hole_node = CAD2LLM::Exporter.build(Sketchup.active_model, profile: "geometry")["nodes"].first
eq("hole face count", hole_node["geometry"]["counts"]["faces"], 1)
eq("hole edge count", hole_node["geometry"]["counts"]["edges"], 8)
near("hole area mm2", hole_node["geometry"]["faces"][0]["area_mm2"], 61_935.36, 0.1)
eq("hole face closed", hole_node["geometry"]["closed"], false)

check("unknown profile rejected") do
  begin
    CAD2LLM::Exporter.build(Sketchup.active_model, profile: "legacy")
    false
  rescue ArgumentError
    true
  end
end

# --------------------------------------------------------- 7. empty geometry
empty = mk_group(name: "void_entry_point", pid: 601, pos: [0, 0, 0], size_in: [0, 0, 0])
model_with([empty])
node = CAD2LLM::Exporter.build(Sketchup.active_model)["nodes"].first
check("empty group has no bbox") { !node["spatial"].key?("bbox_mm") }
check("empty group keeps origin") { node["spatial"]["origin_mm"]["x"] == 0.0 }

# ------------------------------------------------------ 8. main.rb / file I/O
Dir.mktmpdir do |dir|
  m = model_with([mk_group(name: "bed_v1", pid: 701, pos: [100, 0, 0], size_in: [10, 10, 10])],
                 title: "mistnost_2_sk24", path: File.join(dir, "mistnost_2_sk24.skp"))
  path = CAD2LLM.export(model: m, interactive: false)
  eq("main.export path", File.basename(path), "mistnost_2_sk24_cad2llm_v4.json")
  check("main.export wrote file") { File.exist?(path) }
  parsed = JSON.parse(File.read(path))
  eq("written schema", parsed["schema_version"], "4.0")
  eq("written profile", parsed["export_profile"], "spatial")
  eq("written node count", parsed["nodes"].size, 1)
  eq("source file recorded", parsed["project_metadata"]["source"]["file"], "mistnost_2_sk24.skp")
  eq("source channel", parsed["project_metadata"]["source"]["channel"], "sketchup")
  eq("gps omitted when zero", parsed["project_metadata"]["global_origin_gps"], nil)

  geo_path = CAD2LLM.export(model: m, profile: "geometry", interactive: false)
  eq("main.geometry path", File.basename(geo_path), "mistnost_2_sk24_cad2llm_v4_geometry.json")
  geo_parsed = JSON.parse(File.read(geo_path))
  eq("written geometry profile", geo_parsed["export_profile"], "geometry")
  check("written geometry block") { geo_parsed["nodes"].first.key?("geometry") }
  check("spatial export has no geometry") { !parsed["nodes"].first.key?("geometry") }
end

# --------------------------------------------------------------- 9. loader
require File.join(ROOT, "src", "cad2llm_exporter.rb")
items = UI.menu("Extensions").items
sub = items.find { |name, _| name == "CAD2LLM" }
check("loader registered CAD2LLM submenu") { !sub.nil? }
items = sub ? sub[1].items.reject { |i| i[0] == :separator } : []
check("loader has export + bridge items") { items.size == 9 }
check("loader exposes bridge start/stop") { items.count { |i| i[0].to_s.include?("bridge") } == 2 }
check("loader exposes inspector + purge") { items.count { |i| i[0].to_s =~ /Inspector|Purge/ } == 3 }
check("loader exposes geometry export") { items.count { |i| i[0].to_s.include?("geometry") } == 2 }
check("loader removed legacy export") { items.none? { |i| i[0].to_s =~ /legacy/i } }
panel_model = model_with([mk_component(name: "comp_panel", pid: 990, def_name: "panel_def", size_in: [1, 1, 1])])
Sketchup.model = panel_model
panel_model.selection = Sketchup::Selection.new(panel_model.entities.to_a)
CAD2LLM.show_inspector_panel
dlg = CAD2LLM.instance_variable_get(:@inspector_dialog)
check("inspector panel created") { dlg && dlg.shown }
check("inspector panel html exists") { dlg && File.exist?(dlg.file) }
check("inspector panel refresh callback") { dlg && dlg.callbacks.key?("refresh") }
dlg.callbacks["refresh"].call(nil)
js = dlg.scripts.last.to_s
check("inspector panel renders selection") { js.include?("cad2llmRender(") && js.include?("990") }
check("inspector panel no error message") { $ui_messages.none? { |m| m.to_s.include?("Inspector panel failed") } }

# ---------------------------------------------------------------- 10. model metadata
project_model = model_with([mk_group(name: "bed_m", pid: 991, size_in: [1, 1, 1])])
project_model.set_attribute("cad2llm", "purpose", "bydleni")
project_model.set_attribute("cad2llm", "phase", "studie")
project_model.set_attribute("GeoReference", "Latitude", 50.1)
project_model.set_attribute("SU_meta", "x", 1)
proj = CAD2LLM::Exporter.build(project_model)["project_metadata"]
eq("model metadata exported", proj["metadata"]["cad2llm"]["purpose"], "bydleni")
eq("model metadata phase", proj["metadata"]["cad2llm"]["phase"], "studie")
check("model GeoReference skipped") { !proj["metadata"].key?("GeoReference") }
check("model SU_ dict skipped") { !proj["metadata"].key?("SU_meta") }

puts
if $failures.empty?
  puts "ALL OK (#{$checks} checks)"
  exit 0
else
  puts "FAILED #{$failures.size}/#{$checks}"
  $failures.each { |f| puts "  - #{f}" }
  exit 1
end
