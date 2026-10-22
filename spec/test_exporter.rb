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
                    pos: [10, 20, 30], size_in: [10, 20, 30])
rot = mk_component(name: "beam_rotated", pid: 103, def_name: "Beam",
                   size_in: [10, 20, 30],
                   transformation: Geom::Transformation.rotation_z_deg(90, Geom::Point3d.new(0, 0, 0)))
model_with([comp, rot])
doc = CAD2LLM::Exporter.build(Sketchup.active_model)
stove = doc["nodes"].find { |n| n["name"] == "stove_main" }
rotn = doc["nodes"].find { |n| n["name"] == "beam_rotated" }

eq("component definition_name", stove["definition_name"], "Kamna_Priti_mini")
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

# ----------------------------------------------------------- 6. legacy schema
model_with([mk_group(name: "bed_v1", pid: 501, pos: [100, 0, 0], size_in: [10, 10, 10])])
legacy = CAD2LLM::Exporter.build(Sketchup.active_model, schema: "3.0")
leq = legacy["nodes"].first["spatial_data_m"]
eq("legacy units", legacy["project_metadata"]["units"], "meters")
eq("legacy origin meters", leq["origin_xyz"][0], 2.54)
eq("legacy matrix length", leq["transform_matrix"].size, 16)
eq("legacy matrix translation intact", leq["transform_matrix"][12], 2.54)

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
  eq("written node count", parsed["nodes"].size, 1)
  eq("source file recorded", parsed["project_metadata"]["source"]["file"], "mistnost_2_sk24.skp")
  eq("source channel", parsed["project_metadata"]["source"]["channel"], "sketchup")
  eq("gps omitted when zero", parsed["project_metadata"]["global_origin_gps"], nil)
end

# --------------------------------------------------------------- 9. loader
require File.join(ROOT, "src", "cad2llm_exporter.rb")
items = UI.menu("Extensions").items
sub = items.find { |name, _| name == "CAD2LLM" }
check("loader registered CAD2LLM submenu") { !sub.nil? }
check("loader has v4 + v3 + selection items") { sub && sub[1].items.count { |i| i[0] != :separator } == 3 }

puts
if $failures.empty?
  puts "ALL OK (#{$checks} checks)"
  exit 0
else
  puts "FAILED #{$failures.size}/#{$checks}"
  $failures.each { |f| puts "  - #{f}" }
  exit 1
end
