# frozen_string_literal: true
#
# Minimal SketchUp API stub — lets the exporter and console scripts be tested
# headless (no SketchUp installation) with plain `ruby spec/run_all.rb`.
#
# Fidelity notes:
#   * Group#bounds is WORLD space (as in the real API) and Group has no #definition
#   * ComponentInstance#bounds comes from the (local) definition
#   * Transformation#to_a follows the SketchUp order: x-axis,0, y-axis,0, z-axis,0, origin,1
#
module Geom
  class Point3d
    attr_reader :x, :y, :z

    def initialize(x = 0.0, y = 0.0, z = 0.0)
      @x = x.to_f
      @y = y.to_f
      @z = z.to_f
    end

    def transform(t)
      a = t.to_a
      Point3d.new(
        @x * a[0] + @y * a[4] + @z * a[8] + a[12],
        @x * a[1] + @y * a[5] + @z * a[9] + a[13],
        @x * a[2] + @y * a[6] + @z * a[10] + a[14]
      )
    end

    def to_a
      [@x, @y, @z]
    end
  end

  class Transformation
    IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]].freeze

    def initialize(origin = nil)
      @m = Transformation.matrix_from(origin)
    end

    def self.matrix_from(origin)
      ox, oy, oz = origin ? origin.to_a : [0.0, 0.0, 0.0]
      [[1.0, 0.0, 0.0, ox], [0.0, 1.0, 0.0, oy], [0.0, 0.0, 1.0, oz], [0.0, 0.0, 0.0, 1.0]]
    end

    def self.from_matrix(m)
      obj = allocate
      obj.instance_variable_set(:@m, m)
      obj
    end

    def self.rotation_z_deg(deg, origin = nil)
      rad = deg * Math::PI / 180.0
      c = Math.cos(rad)
      s = Math.sin(rad)
      ox, oy, oz = origin ? origin.to_a : [0.0, 0.0, 0.0]
      [[c, -s, 0.0, ox], [s, c, 0.0, oy], [0.0, 0.0, 1.0, oz], [0.0, 0.0, 0.0, 1.0]]
        .then { |m| from_matrix(m) }
    end

    def self.scaled(sx, sy, sz, origin = nil)
      ox, oy, oz = origin ? origin.to_a : [0.0, 0.0, 0.0]
      from_matrix([[sx, 0.0, 0.0, ox], [0.0, sy, 0.0, oy], [0.0, 0.0, sz, oz], [0.0, 0.0, 0.0, 1.0]])
    end

    def origin
      Point3d.new(@m[0][3], @m[1][3], @m[2][3])
    end

    def to_a
      [@m[0][0], @m[1][0], @m[2][0], 0.0,
       @m[0][1], @m[1][1], @m[2][1], 0.0,
       @m[0][2], @m[1][2], @m[2][2], 0.0,
       @m[0][3], @m[1][3], @m[2][3], 1.0]
    end

    def *(other)
      b = other.instance_variable_get(:@m)
      r = Array.new(4) { Array.new(4, 0.0) }
      4.times { |i| 4.times { |j| 4.times { |k| r[i][j] += @m[i][k] * b[k][j] } } }
      Transformation.from_matrix(r)
    end
  end
end

module Sketchup
  VERSION = "24.0.0".freeze

  class Layer
    attr_reader :name

    def initialize(name)
      @name = name
    end
  end

  class BoundingBox
    def initialize(minx, miny, minz, maxx, maxy, maxz)
      @min = [minx.to_f, miny.to_f, minz.to_f]
      @max = [maxx.to_f, maxy.to_f, maxz.to_f]
    end

    def self.sized(width, depth, height, origin = [0.0, 0.0, 0.0])
      new(origin[0], origin[1], origin[2], origin[0] + width, origin[1] + depth, origin[2] + height)
    end

    def corner(i)
      Geom::Point3d.new(
        (i & 1).zero? ? @min[0] : @max[0],
        (i & 2).zero? ? @min[1] : @max[1],
        (i & 4).zero? ? @min[2] : @max[2]
      )
    end

    def empty?
      (@max[0] - @min[0]).abs < 1e-9 && (@max[1] - @min[1]).abs < 1e-9 && (@max[2] - @min[2]).abs < 1e-9
    end

    def width
      @max[0] - @min[0]
    end

    def depth
      @max[1] - @min[1]
    end

    def height
      @max[2] - @min[2]
    end

    def center
      Geom::Point3d.new((@min[0] + @max[0]) / 2.0, (@min[1] + @max[1]) / 2.0, (@min[2] + @max[2]) / 2.0)
    end

    def min
      @min
    end

    def max
      @max
    end
  end

  class ComponentDefinition
    attr_accessor :name, :description, :bounds, :entities, :attribute_dictionaries

    def initialize(name, bounds = nil)
      @name = name
      @description = ""
      @bounds = bounds || BoundingBox.sized(0, 0, 0)
      @entities = []
      @attribute_dictionaries = []
    end

    def set_attribute(dict_name, key, value)
      dict = (@attribute_dictionaries.find { |d| d.name == dict_name } ||
              (@attribute_dictionaries << AttrDict.new(dict_name)).last)
      dict[key] = value
    end

    def get_attribute(dict_name, key, default = nil)
      dict = @attribute_dictionaries.find { |d| d.name == dict_name }
      return default if dict.nil? || !dict.key?(key)

      dict[key]
    end
  end

  class Group
    attr_accessor :name, :layer, :transformation, :persistent_id,
                  :attribute_dictionaries, :entities, :definition

    def initialize(name: "", pid: 0, transformation: nil)
      @name = name
      @persistent_id = pid
      @transformation = transformation || Geom::Transformation.new
      @attribute_dictionaries = []
      @entities = []
      @own_size = [0.0, 0.0, 0.0]
    end

    def typename
      "Group"
    end

    def set_attribute(dict_name, key, value)
      dict = (@attribute_dictionaries.find { |d| d.name == dict_name } ||
              (@attribute_dictionaries << AttrDict.new(dict_name)).last)
      dict[key] = value
    end

    def get_attribute(dict_name, key, default = nil)
      dict = @attribute_dictionaries.find { |d| d.name == dict_name }
      return default if dict.nil? || !dict.key?(key)

      dict[key]
    end

    def own_size=(size)
      @own_size = size
    end

    # real API: Group#bounds is already in world coordinates
    attr_writer :world_origin

    def world_origin
      @world_origin || @transformation.origin
    end

    def bounds
      o = world_origin
      BoundingBox.new(o.x, o.y, o.z, o.x + @own_size[0], o.y + @own_size[1], o.z + @own_size[2])
    end
  end

  class ComponentInstance
    attr_accessor :name, :layer, :transformation, :persistent_id,
                  :attribute_dictionaries, :definition

    def initialize(name: "", pid: 0, transformation: nil, definition: nil)
      @name = name
      @persistent_id = pid
      @transformation = transformation || Geom::Transformation.new
      @attribute_dictionaries = []
      @definition = definition || ComponentDefinition.new(name)
    end

    def typename
      "ComponentInstance"
    end

    def set_attribute(dict_name, key, value)
      dict = (@attribute_dictionaries.find { |d| d.name == dict_name } ||
              (@attribute_dictionaries << AttrDict.new(dict_name)).last)
      dict[key] = value
    end

    def get_attribute(dict_name, key, default = nil)
      dict = @attribute_dictionaries.find { |d| d.name == dict_name }
      return default if dict.nil? || !dict.key?(key)

      dict[key]
    end

    def bounds
      @definition.bounds
    end
  end

  class Selection
    include Enumerable
    def initialize(items = [])
      @items = items
    end

    def each(&blk)
      @items.each(&blk)
    end

    def to_a
      @items.dup
    end

    def empty?
      @items.empty?
    end
  end

  class Layers
    include Enumerable

    def initialize
      @list = []
      @used = Hash.new(0)
    end

    def use(layer)
      return if layer.nil?

      @used[layer.name] += 1
    end

    def purge_unused
      unused = @list.select { |l| @used[l.name].to_i.zero? }
      @list -= unused
      unused
    end

    def add(name)
      existing = self[name]
      return existing if existing

      layer = Layer.new(name)
      @list << layer
      layer
    end

    def [](name)
      @list.find { |l| l.name == name }
    end

    def each(&blk)
      @list.each(&blk)
    end

    def to_a
      @list.dup
    end
  end

  class Model
    attr_accessor :entities, :title, :path, :shadow_info, :selection, :attribute_dictionaries
    attr_reader :operation_log

    def initialize
      @entities = []
      @title = ""
      @path = ""
      @shadow_info = { "Latitude" => 0.0, "Longitude" => 0.0 }
      @selection = Selection.new([])
      @layers = Layers.new
      @operation_log = []
      @attribute_dictionaries = []
    end

    def layers
      @layers ||= Layers.new
    end

    # real SketchUp keeps every layer in model.layers; mirror that on assignment
    def entities=(list)
      @entities = list
      register_layers(list)
    end

    def definitions
      collect = lambda do |list, acc|
        Array(list).each do |entity|
          definition = entity.respond_to?(:definition) ? entity.definition : nil
          if definition
            acc << definition unless acc.include?(definition)
            collect.call(definition.entities, acc)
          elsif entity.is_a?(Group)
            collect.call(entity.entities, acc)
          end
        end
        acc
      end
      collect.call(@entities, [])
    end

    def register_layers(list)
      Array(list).each do |entity|
        next unless entity.respond_to?(:layer)

        if entity.layer
          layers.add(entity.layer.name)
          layers.use(entity.layer)
        end
        kids = if entity.is_a?(Group)
                 entity.entities
               elsif entity.respond_to?(:definition) && entity.definition
                 entity.definition.entities
               end
        register_layers(kids) if kids
      end
    end

    def start_operation(name = nil, *rest)
      @operation_log << { "name" => name, "state" => "open", "args" => rest, "undo_seen" => true }
      true
    end

    def get_attribute(dict_name, key, default = nil)
      dict = @attribute_dictionaries.find { |d| d.name == dict_name }
      return default if dict.nil? || !dict.key?(key)

      dict[key]
    end

    def set_attribute(dict_name, key, value)
      dict = (@attribute_dictionaries.find { |d| d.name == dict_name } ||
              (@attribute_dictionaries << AttrDict.new(dict_name)).last)
      dict[key] = value
    end

    def commit_operation
      entry = @operation_log.reverse.find { |op| op["state"] == "open" }
      entry["state"] = "committed" if entry
      true
    end

    def abort_operation
      entry = @operation_log.reverse.find { |op| op["state"] == "open" }
      entry["state"] = "aborted" if entry
      true
    end
  end

  def self.active_model
    @model ||= Model.new
  end

  def self.model=(m)
    @model = m
  end

  def self.version
    VERSION
  end

  def self.send_action(action)
    (@actions ||= []) << action
    true
  end

  def self.actions
    @actions ||= []
  end

  def self.load(path)
    require path
  end

  def self.register_extension(*)
    @registered_extensions ||= []
    @registered_extensions << true
    true
  end

  def self.registered_extensions
    @registered_extensions || []
  end


end

# real SketchUp API exposes file_loaded? / file_loaded as global Kernel methods
module Kernel
  def file_loaded?(path)
    ($loaded_sketchup_files ||= []).include?(path)
  end

  def file_loaded(path)
    ($loaded_sketchup_files ||= []) << path
  end
end

class SketchupExtension
  attr_accessor :name, :description, :version, :copyright, :creator, :path

  def initialize(name, path)
    @name = name
    @path = path
  end
end

class AttrDict
  attr_reader :name

  def initialize(name, pairs = {})
    @name = name
    @pairs = pairs
  end

  def each_pair(&blk)
    @pairs.each_pair(&blk)
  end

  def [](key)
    @pairs[key]
  end

  def []=(key, value)
    @pairs[key] = value
  end

  def key?(key)
    @pairs.key?(key)
  end

  def to_h
    @pairs.dup
  end
end

module UI
  def self.messagebox(msg, _type = nil)
    $ui_messages << msg
  end

  def self.savepanel(*)
    $savepanel_path
  end

  def self.start_timer(interval, repeat = false, &blk)
    @timers ||= {}
    id = (@timer_seq ||= 0) + 1
    @timer_seq = id
    @timers[id] = { "interval" => interval, "repeat" => repeat, "block" => blk }
    id
  end

  def self.stop_timer(id)
    @timers&.delete(id)
    true
  end

  def self.timers
    @timers ||= {}
  end

  def self.menu(*)
    @menu ||= Menu.new
  end

  class HtmlDialog
    attr_reader :options, :file, :callbacks, :scripts, :shown, :closed

    def initialize(options = {})
      @options = options
      @callbacks = {}
      @scripts = []
      @shown = false
      @closed = false
    end

    def set_file(path)
      @file = path.to_s
    end

    def add_action_callback(name, &blk)
      @callbacks[name.to_s] = blk
    end

    def show
      @shown = true
    end

    def visible?
      @shown && !@closed
    end

    def bring_to_front
      @front = true
    end

    def front?
      @front
    end

    def execute_script(js)
      @scripts << js.to_s
    end

    def set_on_closed(&blk)
      @on_closed = blk
    end

    def close
      @closed = true
      @shown = false
      @on_closed&.call
    end
  end

  class Menu
    attr_reader :items

    def initialize
      @items = []
    end

    def add_submenu(name)
      s = Menu.new
      @items << [name, s]
      s
    end

    def add_item(name, &blk)
      @items << [name, blk]
    end

    def add_separator
      @items << [:separator]
    end
  end
end

$ui_messages = []
$savepanel_path = nil

# --- test builders -------------------------------------------------------
def t_in(ox, oy, oz)
  Geom::Transformation.new(Geom::Point3d.new(ox, oy, oz))
end

def mk_group(name:, pid:, pos: [0, 0, 0], size_in: [0, 0, 0], dicts: {}, world_pos: nil)
  g = Sketchup::Group.new(name: name, pid: pid, transformation: t_in(*pos))
  g.own_size = size_in
  g.world_origin = Geom::Point3d.new(*world_pos) if world_pos
  dicts.each { |dn, pr| g.attribute_dictionaries << AttrDict.new(dn, pr) }
  g.layer = Sketchup::Layer.new("walls")
  g
end

def mk_component(name:, pid:, pos: [0, 0, 0], size_in: [0, 0, 0], def_name: nil, dicts: {}, transformation: nil, def_description: nil)
  d = Sketchup::ComponentDefinition.new(def_name || name, Sketchup::BoundingBox.sized(*size_in))
  d.description = def_description if def_description
  c = Sketchup::ComponentInstance.new(name: name, pid: pid,
                                      transformation: transformation || t_in(*pos), definition: d)
  dicts.each { |dn, pr| c.attribute_dictionaries << AttrDict.new(dn, pr) }
  c.layer = Sketchup::Layer.new("energy_nodes")
  c
end
