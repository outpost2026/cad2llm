# frozen_string_literal: true
#
# Headless tests for CAD2LLM::Annotate (M1 — declarative annotation plan).
# Run: ruby spec/run_all.rb

require "json"
require "tmpdir"

ROOT = File.expand_path("..", __dir__)

require_relative "sketchup_stub"
require File.join(ROOT, "src", "cad2llm_exporter", "lib", "exporter.rb")
require File.join(ROOT, "src", "cad2llm_exporter", "lib", "annotate.rb")

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

def model_with(entities, title: "mistnost_2_v1", path: "")
  m = Sketchup::Model.new
  m.entities = entities
  m.title = title
  m.path = path
  Sketchup.model = m
  m
end

def plan(*operations, name: "test")
  { "schema" => "cad2llm/annotate_plan/v1", "name" => name, "operations" => operations }
end

def op(kind, target, extra = {})
  { "op" => kind, "target" => target }.merge(extra)
end

# ------------------------------------------------- 1. happy path, 3 op types
bed = mk_group(name: "bed_v1", pid: 101, pos: [100, 0, 0], size_in: [10, 10, 10])
zone = mk_group(name: "zone_bed", pid: 102, pos: [0, 0, 0], size_in: [100, 100, 10])
stove = mk_component(name: "stove_main", pid: 201, def_name: "Kamna_Priti_mini", pos: [10, 0, 0], size_in: [5, 5, 5])
model = model_with([bed, zone, stove])

report = CAD2LLM::Annotate.run(plan(
  op("set_attribute", { "persistent_id" => 101 }, "dict" => "zone", "key" => "type", "value" => "sleep"),
  op("set_attribute", { "persistent_id" => 201 }, "dict" => "energy", "key" => "kw", "value" => 4.5),
  op("set_tag", { "persistent_id" => 101 }, "tag" => "bed_m2"),
  op("rename", { "persistent_id" => 102 }, "name" => "zone_sleep")
), model: model)

eq("report schema", report["schema"], "cad2llm/annotate_report/v1")
eq("all applied", report["summary"]["applied"], 4)
eq("no errors", report["summary"]["errors"], 0)
eq("attribute written", bed.get_attribute("zone", "type"), "sleep")
eq("float attribute kept numeric", stove.get_attribute("energy", "kw"), 4.5)
eq("tag applied", bed.layer.name, "bed_m2")
eq("tag created in model", !model.layers["bed_m2"].nil?, true)
eq("rename applied", zone.name, "zone_sleep")
eq("one undo entry per op", model.operation_log.count { |o| o["state"] == "committed" }, 4)

exported = CAD2LLM::Exporter.build(model)
bed_node = exported["nodes"].find { |n| n["persistent_id"] == 101 }
eq("export sees metadata", bed_node["metadata"]["zone"]["type"], "sleep")
eq("export sees tag", bed_node["tag"], "bed_m2")
eq("export sees renamed node", exported["nodes"].any? { |n| n["name"] == "zone_sleep" }, true)

# -------------------------------------------------------- 2. idempotence
again = CAD2LLM::Annotate.run(plan(
  op("set_attribute", { "persistent_id" => 101 }, "dict" => "zone", "key" => "type", "value" => "sleep"),
  op("set_tag", { "persistent_id" => 101 }, "tag" => "bed_m2"),
  op("rename", { "persistent_id" => 102 }, "name" => "zone_sleep")
), model: model)
eq("idempotent: unchanged", again["summary"]["unchanged"], 3)
eq("idempotent: applied", again["summary"]["applied"], 0)
eq("idempotent: no new undo entries", model.operation_log.count { |o| o["state"] == "committed" }, 4)

# ---------------------------------------- 3. resolution by path / normalized
nested = mk_group(name: "lfp_cell", pid: 301, pos: [0, 0, 0], size_in: [1, 1, 1])
zone.entities << nested
zone.name = "zone_sleep"

by_path = CAD2LLM::Annotate.run(plan(
  op("set_attribute", { "hierarchy_path" => ["zone_sleep", "lfp_cell"] },
     "dict" => "battery", "key" => "form", "value" => "lfp")
), model: model)
eq("path resolution applied", by_path["operations"][0]["status"], "applied")
eq("path resolution target", nested.get_attribute("battery", "form"), "lfp")

by_norm = CAD2LLM::Annotate.run(plan(
  op("set_attribute", { "hierarchy_path" => ["Zone_Sleep", "LFP Cell"] },
     "dict" => "battery", "key" => "form", "value" => "lfp")
), model: model)
eq("normalized path resolution", by_norm["operations"][0]["status"], "unchanged")

# ----------------------------------------- 4. ambiguity is refused, not guessed
a = mk_group(name: "cell", pid: 401, pos: [0, 0, 0], size_in: [1, 1, 1])
b = mk_group(name: "cell", pid: 402, pos: [10, 0, 0], size_in: [1, 1, 1])
model = model_with([a, b])

amb = CAD2LLM::Annotate.run(plan(
  op("set_attribute", { "name" => "cell" }, "dict" => "x", "key" => "y", "value" => 1)
), model: model)
eq("ambiguous name -> error", amb["operations"][0]["status"], "error")
eq("ambiguous code", amb["operations"][0]["code"], "AMBIGUOUS_TARGET")
eq("ambiguous: no mutation", a.get_attribute("x", "y"), nil)

dis = CAD2LLM::Annotate.run(plan(
  op("set_attribute", { "name" => "cell", "tag" => "walls", "definition_name" => "nope" },
     "dict" => "x", "key" => "y", "value" => 1)
), model: model)
eq("filter removing all -> error", dis["operations"][0]["status"], "error")

# shared definition: same persistent_id twice -> pid alone is ambiguous
defn = Sketchup::ComponentDefinition.new("battery_def", Sketchup::BoundingBox.sized(10, 10, 10))
defn.entities << mk_group(name: "shared_cell", pid: 999, pos: [0, 0, 0], size_in: [1, 1, 1])
i1 = Sketchup::ComponentInstance.new(name: "bat_a", pid: 501, transformation: t_in(0, 0, 0), definition: defn)
i2 = Sketchup::ComponentInstance.new(name: "bat_b", pid: 502, transformation: t_in(20, 0, 0), definition: defn)
model = model_with([i1, i2])
dup = CAD2LLM::Annotate.run(plan(
  op("set_attribute", { "persistent_id" => 999 }, "dict" => "x", "key" => "y", "value" => 1)
), model: model)
eq("duplicate pid -> ambiguous", dup["operations"][0]["status"], "error")
eq("duplicate pid code", dup["operations"][0]["code"], "AMBIGUOUS_TARGET")

dup_path = CAD2LLM::Annotate.run(plan(
  op("set_attribute", { "persistent_id" => 999, "hierarchy_path" => ["bat_b", "shared_cell"] },
     "dict" => "x", "key" => "y", "value" => 1)
), model: model)
eq("duplicate pid disambiguated by path", dup_path["operations"][0]["status"], "applied")

# --------------------------------------------- 5. errors do not stop the batch
model = model_with([mk_group(name: "bed_v1", pid: 601, pos: [0, 0, 0], size_in: [1, 1, 1])])
mixed = CAD2LLM::Annotate.run(plan(
  op("set_attribute", { "persistent_id" => 60_000 }, "dict" => "x", "key" => "y", "value" => 1),
  op("set_attribute", { "persistent_id" => 601 }, "dict" => "SU_InstanceSet", "key" => "y", "value" => 1),
  op("set_attribute", { "persistent_id" => 601 }, "dict" => "ok", "key" => "y", "value" => 2)
), model: model)
eq("unresolved target reported", mixed["operations"][0]["code"], "TARGET_UNRESOLVED")
eq("reserved dict rejected", mixed["operations"][1]["code"], "RESERVED_DICT")
eq("later op still applied", mixed["operations"][2]["status"], "applied")
eq("batch summary", mixed["summary"], { "total" => 3, "applied" => 1, "planned" => 0, "unchanged" => 0, "errors" => 2 })

# ------------------------------------------------------------- 6. dry run
committed_before = model.operation_log.count { |o| o["state"] == "committed" }
dry = CAD2LLM::Annotate.run(plan(
  op("set_attribute", { "persistent_id" => 601 }, "dict" => "ok", "key" => "y", "value" => 3),
  op("set_tag", { "persistent_id" => 601 }, "tag" => "other"),
  op("rename", { "persistent_id" => 601 }, "name" => "bed_v2")
), model: model, dry_run: true)
eq("dry run planned", dry["summary"]["planned"], 3)
eq("dry run applied", dry["summary"]["applied"], 0)
eq("dry run no mutation", Sketchup.active_model.entities.first.get_attribute("ok", "y"), 2)
eq("dry run no rename", Sketchup.active_model.entities.first.name, "bed_v1")
eq("dry run no undo entries", model.operation_log.count { |o| o["state"] == "committed" }, committed_before)

# ------------------------------------------- 7. validation + report on disk
begin
  CAD2LLM::Annotate.run({ "schema" => "cad2llm/annotate_plan/v1", "operations" => [{ "op" => "explode", "target" => {} }] })
  check("unknown op raises") { false }
rescue ArgumentError => e
  check("unknown op raises (#{e.message[0, 40]})") { e.message.include?("unknown op") }
end

begin
  CAD2LLM::Annotate.run({ "schema" => "v9", "operations" => [] })
  check("bad schema raises") { false }
rescue ArgumentError => e
  check("bad schema raises (#{e.message[0, 40]})") { e.message.include?("schema") }
end

Dir.mktmpdir do |dir|
  path = File.join(dir, "report.json")
  rep = CAD2LLM::Annotate.run(plan(op("rename", { "persistent_id" => 601 }, "name" => "bed_v9")),
                              model: model, report_path: path)
  check("report file written") { File.exist?(path) }
  eq("report file content", JSON.parse(File.read(path))["schema"], "cad2llm/annotate_report/v1")
  eq("report path result", rep["operations"][0]["status"], "applied")
end

# ------------------------------------------- 7b. single undo transaction
model = model_with([mk_group(name: "g1", pid: 701, pos: [0, 0, 0], size_in: [1, 1, 1])])
before_ops = model.operation_log.size
single_plan = plan(
  op("set_attribute", { "persistent_id" => 701 }, "dict" => "a", "key" => "k", "value" => 1),
  op("rename", { "persistent_id" => 701 }, "name" => "g1_renamed"),
  op("set_tag", { "persistent_id" => 701 }, "tag" => "floor")
)
single_plan["transaction"] = "single"
rep_single = CAD2LLM::Annotate.run(single_plan, model: model)
eq("single: all applied", rep_single["summary"]["applied"], 3)
eq("single: one operation", model.operation_log.size - before_ops, 1)
eq("single: committed", model.operation_log.last["state"], "committed")
eq("single: report transaction", rep_single["transaction"], "single")

# -------------------------------- 7c. component description (visible in SU UI)
comp = mk_component(name: "comp_a", pid: 801, def_name: "def_a", pos: [0, 0, 0], size_in: [1, 1, 1])
model = model_with([comp])
comp.definition.description = "stary popis"
desc_plan = plan(
  op("set_description", { "persistent_id" => 801 },
     "description" => "Wall | envelope | wood 24 mm", "preserve_original" => true)
)
rep_desc = CAD2LLM::Annotate.run(desc_plan, model: model)
eq("description: applied", rep_desc["summary"]["applied"], 1)
eq("description: written", comp.definition.description, "Wall | envelope | wood 24 mm")
eq("description: original kept",
   comp.definition.get_attribute("cad2llm", "original_description"), "stary popis")
rep_desc2 = CAD2LLM::Annotate.run(desc_plan, model: model)
eq("description: idempotent", rep_desc2["summary"]["unchanged"], 1)

empty = CAD2LLM::Annotate.run(plan(op("set_description", { "persistent_id" => 801 }, "description" => "  ")),
                              model: model)
eq("description: empty refused", empty["operations"][0]["code"], "INVALID_OP")

def_desc = CAD2LLM::Annotate.run(
  plan(op("set_description", { "kind" => "definition", "name" => "def_a" },
          "description" => "Definice popis")), model: model
)
eq("description: definition target", def_desc["summary"]["applied"], 1)
eq("description: def written", comp.definition.description, "Definice popis")

group_only = mk_group(name: "g_noded", pid: 802, pos: [0, 0, 0], size_in: [1, 1, 1])
model_g = model_with([group_only])
no_def = CAD2LLM::Annotate.run(plan(op("set_description", { "persistent_id" => 802 },
                                       "description" => "x")), model: model_g)
eq("description: group without definition refused", no_def["operations"][0]["code"], "INVALID_TARGET")

# ------------------------------------------------ 8. definition-level ops
shared = Sketchup::ComponentDefinition.new("wall_def_shared", Sketchup::BoundingBox.sized(10, 10, 10))
i1 = Sketchup::ComponentInstance.new(name: "wall_a", pid: 801, transformation: t_in(0, 0, 0), definition: shared)
i2 = Sketchup::ComponentInstance.new(name: "wall_b", pid: 802, transformation: t_in(20, 0, 0), definition: shared)
model = model_with([i1, i2])

defs_report = CAD2LLM::Annotate.run(plan(
  op("set_attribute", { "kind" => "definition", "name" => "wall_def_shared" },
     "dict" => "IFC 2x3", "key" => "SchemaType",
     "value" => "http://www.iai-tech.org/ifcXML/IFC2x3/FINAL:IfcWall"),
  op("rename", { "kind" => "definition", "name" => "wall_def_shared" }, "name" => "wall_inner_south")
), model: model)
eq("definition set_attribute applied", defs_report["operations"][0]["status"], "applied")
eq("definition rename applied", defs_report["operations"][1]["status"], "applied")
eq("definition attr written", shared.get_attribute("IFC 2x3", "SchemaType").to_s.split(":").last, "IfcWall")
eq("definition renamed", shared.name, "wall_inner_south")
eq("definition uid", defs_report["operations"][0]["uid"], "definition:wall_def_shared")
exported = CAD2LLM::Exporter.build(model)
eq("instances see renamed definition", exported["nodes"].map { |n| n["definition_name"] }.uniq, ["wall_inner_south"])

def_tag = CAD2LLM::Annotate.run(plan(
  op("set_tag", { "kind" => "definition", "name" => "wall_inner_south" }, "tag" => "wall")
), model: model)
eq("definition set_tag refused", def_tag["operations"][0]["code"], "INVALID_OP")

def_missing = CAD2LLM::Annotate.run(plan(
  op("rename", { "kind" => "definition", "name" => "nope" }, "name" => "x")
), model: model)
eq("definition unresolved", def_missing["operations"][0]["code"], "TARGET_UNRESOLVED")

def_bad_kind = CAD2LLM::Annotate.run(plan(
  op("rename", { "kind" => "mesh", "name" => "x" }, "name" => "y")
), model: model) rescue $!
check("bad target kind raises") { def_bad_kind.is_a?(ArgumentError) }

puts
if $failures.empty?
  puts "ALL OK (#{$checks} checks)"
  exit 0
else
  puts "FAILED #{$failures.size}/#{$checks}"
  $failures.each { |f| puts "  - #{f}" }
  exit 1
end
