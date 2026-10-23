# frozen_string_literal: true
#
# Headless tests for CAD2LLM::Bridge (M2 — loopback JSON-RPC, protocol v1).
# Run: ruby spec/run_all.rb

require "json"
require "socket"
require "tmpdir"

ROOT = File.expand_path("..", __dir__)

require_relative "sketchup_stub"
require File.join(ROOT, "src", "cad2llm_exporter", "lib", "exporter.rb")
require File.join(ROOT, "src", "cad2llm_exporter", "lib", "annotate.rb")
require File.join(ROOT, "src", "cad2llm_exporter", "lib", "bridge.rb")

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

def call(method, params = {}, token: nil, id: 1, model: Sketchup.active_model)
  req = { "id" => id, "method" => method, "params" => params }
  req["token"] = token if token
  CAD2LLM::Bridge.dispatch(req, model: model)
end

Dir.mktmpdir do |dir|
  bed = mk_group(name: "bed_v1", pid: 101, pos: [0, 0, 0], size_in: [10, 10, 10])
  zone = mk_group(name: "zone_bed", pid: 102, pos: [0, 0, 0], size_in: [100, 100, 10])
  model = model_with([bed, zone], path: File.join(dir, "mistnost_2_v1.skp"))

  tok_path = File.join(dir, "bridge.json")
  $token_file = tok_path
  state = CAD2LLM::Bridge.start(model: model, port: 0, auto_poll: false, token_path: tok_path)
  token = CAD2LLM::Bridge.token

  # ------------------------------------------------------------ 1. lifecycle
  eq("bridge running", state["running"], true)
  check("ephemeral port assigned") { CAD2LLM::Bridge.port.positive? }
  check("token generated (32 hex)") { token.match?(/\A[0-9a-f]{32}\z/) }
  check("token file written") { File.exist?(tok_path) }
  eq("token path honored", CAD2LLM::Bridge.token_path, tok_path)
  tok = JSON.parse(File.read(CAD2LLM::Bridge.token_path))
  eq("token file port", tok["port"], CAD2LLM::Bridge.port)
  eq("token file token", tok["token"], token)

  # self-heal: a temp cleaner deleting the file must not kill the bridge
  File.delete(tok_path)
  CAD2LLM::Bridge.ensure_token_file
  check("token file self-heals") { File.exist?(tok_path) }
  eq("self-heal keeps token", JSON.parse(File.read(tok_path))["token"], token)

  # --------------------------------------------------------------- 2. ping
  ping = call("ping")
  eq("ping ok", ping["ok"], true)
  eq("ping protocol", ping["result"]["protocol"], "1.0")
  eq("ping unauthenticated", ping["result"]["authenticated"], false)

  # ---------------------------------------------------- 3. auth + info
  eq("info without token", call("info")["error"]["code"], "unauthorized")
  eq("info with bad token", call("info", token: "deadbeef")["error"]["code"], "unauthorized")
  info = call("info", token: token)
  eq("info ok", info["ok"], true)
  eq("info title", info["result"]["title"], "mistnost_2_v1")
  eq("info top level", info["result"]["top_level_entities"], 2)
  eq("info groups", info["result"]["top_level_groups"], 2)
  eq("info tags", info["result"]["tags"], ["walls"])

  # ------------------------------------------------------------- 4. export
  eq("export without token", call("export")["error"]["code"], "unauthorized")
  out = call("export", {}, token: token)
  eq("export ok", out["ok"], true)
  eq("export schema", out["result"]["schema"], "4.0")
  eq("export nodes", out["result"]["nodes"], 2)
  eq("export default path", File.basename(out["result"]["path"]), "mistnost_2_v1_cad2llm_v4.json")
  check("export file exists") { File.exist?(out["result"]["path"]) }
  eq("export issues", out["result"]["issues"], 0)

  explicit = File.join(dir, "custom.json")
  out2 = call("export", { "path" => explicit }, token: token)
  eq("export explicit path", out2["result"]["path"], explicit)
  check("export explicit file") { File.exist?(explicit) }

  # -------------------------------------------------------------- 5. apply
  plan = {
    "schema" => "cad2llm/annotate_plan/v1",
    "operations" => [
      { "op" => "set_attribute", "target" => { "persistent_id" => 101 },
        "dict" => "zone", "key" => "type", "value" => "sleep" },
      { "op" => "set_tag", "target" => { "persistent_id" => 101 }, "tag" => "bed_m2" }
    ]
  }

  dry = call("apply", { "plan" => plan, "dry_run" => true }, token: token)
  eq("apply dry ok", dry["ok"], true)
  eq("apply dry planned", dry["result"]["summary"]["planned"], 2)
  eq("apply dry no mutation", bed.get_attribute("zone", "type"), nil)
  eq("apply dry no post_check", dry["result"].key?("post_check"), false)

  real = call("apply", { "plan" => plan }, token: token)
  eq("apply ok", real["ok"], true)
  eq("apply applied", real["result"]["summary"]["applied"], 2)
  eq("apply mutation", bed.get_attribute("zone", "type"), "sleep")
  eq("apply tag", bed.layer.name, "bed_m2")
  eq("apply post_check nodes", real["result"]["post_check"]["nodes"], 2)
  eq("apply post_check tags", real["result"]["post_check"]["tag_distribution"], { "bed_m2" => 1, "walls" => 1 })
  check("apply post_check union") { !real["result"]["post_check"]["union_bbox_mm"].nil? }

  # --------------------------------------------------------------- 6. undo
  Sketchup.actions.clear
  undo = call("undo", {}, token: token)
  eq("undo ok", undo["ok"], true)
  check("undo used send_action") { Sketchup.actions.include?("editUndo:") }

  # ------------------------------------------------------------ 7. errors
  eq("unknown method", call("explode", {}, token: token)["error"]["code"], "bad_request")
  eq("bad plan", call("apply", { "plan" => { "schema" => "x", "operations" => [] } }, token: token)["error"]["code"], "bad_request")
  eq("missing plan", call("apply", {}, token: token)["error"]["code"], "bad_request")

  # --------------------------------------- 7b. raw attribute dictionary dump
  wall = mk_component(name: "wall_x", pid: 900, def_name: "Wall_def", size_in: [1, 1, 1],
                      dicts: { "BIM_Ifc" => { "type" => "IfcWall" },
                               "SU_InstanceSet" => { "Owner" => "" } })
  wall.definition.attribute_dictionaries << AttrDict.new("DynamicComponents", { "_length" => "12" })
  attr_model = model_with([wall], title: "attr_probe")
  attr = call("attributes", {}, token: token, model: attr_model)
  eq("attributes total", attr["result"]["entities_total"], 1)
  eq("attributes sees BIM_Ifc", attr["result"]["entities"][0]["dicts"]["BIM_Ifc"]["type"], "IfcWall")
  eq("attributes sees SU_ dict", attr["result"]["entities"][0]["dicts"].key?("SU_InstanceSet"), true)
  eq("attributes sees definition dict", attr["result"]["definitions"]["Wall_def"].key?("DynamicComponents"), true)
  eq("attributes with dicts", attr["result"]["entities_with_dicts"], 1)
  eq("attributes definitions with dicts", attr["result"]["definitions_with_dicts"], 1)

  # ------------------------------------------ 7c. describe + purge unused tags
  wall.definition.description = "Nosná stěna, dřevo 24 mm"
  desc = call("describe", { "targets" => [{ "persistent_id" => 900 }] }, token: token, model: attr_model)
  eq("describe count", desc["result"]["count"], 1)
  eq("describe definition description", desc["result"]["items"][0]["definition_description"],
     "Nosná stěna, dřevo 24 mm")
  eq("describe dicts", desc["result"]["items"][0]["dicts"]["BIM_Ifc"]["type"], "IfcWall")

  attr_model.selection = Sketchup::Selection.new([wall])
  sel = call("describe", {}, token: token, model: attr_model)
  eq("describe selection", sel["result"]["count"], 1)

  def_desc = call("describe", { "targets" => [{ "kind" => "definition", "name" => "Wall_def" }] },
                  token: token, model: attr_model)
  eq("describe definition kind", def_desc["result"]["items"][0]["kind"], "definition")
  eq("describe missing definition", call("describe", { "targets" => ["no_such_def"] }, token: token,
                                         model: attr_model)["result"]["items"][0]["error"],
     "DEFINITION_UNRESOLVED")

  purge_model = Sketchup::Model.new
  tagged = mk_group(name: "g_tag", pid: 951)
  tagged.layer = purge_model.layers.add("used_tag")
  purge_model.entities = [tagged]
  purge_model.layers.add("unused_tag")
  purged = call("purge_unused_tags", {}, token: token, model: purge_model)
  eq("purge removed unused", purged["result"]["purged"], ["unused_tag"])
  eq("purge counts", [purged["result"]["before"], purged["result"]["after"]], [2, 1])
  eq("purge is undoable", purge_model.operation_log.last["state"], "committed")

  # ------------------------------------------------- 8. wire framing (TCP)
  client = TCPSocket.new(CAD2LLM::Bridge::HOST, CAD2LLM::Bridge.port)
  line1 = JSON.generate({ "id" => 11, "method" => "ping", "params" => {} })
  line2 = JSON.generate({ "id" => 12, "method" => "info", "params" => {}, "token" => token })
  client.write("#{line1}\n#{line2}\n")

  received = []
  buffer = +""
  deadline = Time.now + 5
  while received.size < 2 && Time.now < deadline
    CAD2LLM::Bridge.poll
    ready = IO.select([client], nil, nil, 0.05)
    next unless ready

    chunk = client.read_nonblock(65_536, exception: false)
    next unless chunk.is_a?(String)

    buffer << chunk
    while (idx = buffer.index("\n"))
      received << JSON.parse(buffer.slice!(0..idx))
    end
  end
  eq("wire: two responses", received.size, 2)
  eq("wire: first id", received[0] && received[0]["id"], 11)
  eq("wire: second id", received[1] && received[1]["id"], 12)
  eq("wire: second info ok", received[1] && received[1]["ok"], true)
  client.close

  # close/open cycle
  CAD2LLM::Bridge.poll
  nothing = CAD2LLM::Bridge.poll
  eq("poll with no clients is safe", nothing, CAD2LLM::Bridge.poll)
end

# ------------------------------------------------------ 9. stop + restart
after = CAD2LLM::Bridge.stop
eq("stop running=false", after["running"], false)
check("token file removed") { !File.exist?($token_file) }

model = model_with([mk_group(name: "x", pid: 1)], title: "restart")
Dir.mktmpdir do |tmp|
  CAD2LLM::Bridge.start(model: model, port: 0, auto_poll: false,
                        token_path: File.join(tmp, "bridge.json"))
end
check("restart works") { CAD2LLM::Bridge.running? }
CAD2LLM::Bridge.stop

# ------------------------------------------- 10. param validation precedes auth
eq("dispatch requires valid params",
   CAD2LLM::Bridge.dispatch({ "id" => 1, "method" => "info", "params" => "x" })["error"]["code"], "bad_request")

puts
if $failures.empty?
  puts "ALL OK (#{$checks} checks)"
  exit 0
else
  puts "FAILED #{$failures.size}/#{$checks}"
  $failures.each { |f| puts "  - #{f}" }
  exit 1
end
