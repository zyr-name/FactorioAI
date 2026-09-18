-- Persistent, lease-protected companion actions and collision-aware navigation.
local LEASE_TICKS = 180
local STUCK_TICKS = 90
local MAX_DISTANCE = 256
local ARRIVAL_DISTANCE = 0.28
local MAX_REPLANS = 3
local ACTION_HISTORY = 40
local MAX_INSPECT_ENTITIES = 100
local MAX_OBSERVATION_RADIUS = 32
local MAX_MAP_CHUNKS = 256
local MAX_MAP_ENTITIES = 20000
local MAX_RECIPES = 50
local MAX_LOCAL_BUILDINGS = 200
local MAX_TASKS = 100
local just_loaded = false

local function valid_character()
  local companion = storage.bridge.companion
  return companion and companion.entity and companion.entity.valid and companion.entity or nil
end

local function statistics_for(companion, entity)
  companion.statistics = companion.statistics or {}
  local statistics = companion.statistics
  statistics.spawned_tick = statistics.spawned_tick or game.tick
  statistics.connected_ticks = statistics.connected_ticks or 0
  statistics.distance_tiles = statistics.distance_tiles or 0
  statistics.moves_started = statistics.moves_started or 0
  statistics.moves_completed = statistics.moves_completed or 0
  statistics.moves_blocked = statistics.moves_blocked or 0
  statistics.moves_stopped = statistics.moves_stopped or 0
  statistics.replans = statistics.replans or 0
  statistics.items_mined = statistics.items_mined or 0
  statistics.items_crafted = statistics.items_crafted or 0
  statistics.entities_placed = statistics.entities_placed or 0
  statistics.entities_rotated = statistics.entities_rotated or 0
  statistics.items_transferred = statistics.items_transferred or 0
  if entity and not statistics.last_position then
    statistics.last_position = {x = entity.position.x, y = entity.position.y}
  end
  return statistics
end

local function stop_entity(entity)
  if not entity then return end
  entity.walking_state = {walking = false, direction = defines.direction.north}
  entity.mining_state = {mining = false}
  entity.shooting_state = {state = defines.shooting.not_shooting, position = entity.position}
end

local function finish_action(companion, command_id, state, reason)
  local action = command_id and companion and companion.actions and companion.actions[command_id]
  if action and (action.state == "queued" or action.state == "running") then
    action.state = state
    action.finished_tick = game.tick
    action.error = state == "failed" and reason or nil
    action.result = state == "completed" and reason or nil
  end
end

local function halt(reason)
  local entity = valid_character()
  stop_entity(entity)
  local companion = storage.bridge.companion
  if companion and companion.motion and companion.motion.state ~= "idle"
      and not companion.motion.finished_tick then
    local terminal = "cancelled"
    if reason == "arrived" then terminal = "completed" end
    if reason == "blocked" or reason == "unreachable" or reason == "timed_out"
        or reason == "pathfinder_error" then terminal = "failed" end
    finish_action(companion, companion.motion.command_id, terminal, reason)
    companion.motion.state = reason
    companion.motion.finished_tick = game.tick
    companion.motion.path = nil
    companion.motion.path_request_id = nil
    local statistics = statistics_for(companion, entity)
    if reason == "arrived" then
      statistics.moves_completed = statistics.moves_completed + 1
    elseif reason == "blocked" or reason == "unreachable" then
      statistics.moves_blocked = statistics.moves_blocked + 1
    elseif reason ~= "superseded" then
      statistics.moves_stopped = statistics.moves_stopped + 1
    end
  end
  if companion and companion.operation and not companion.operation.finished_tick then
    local operation = companion.operation
    if operation.type == "craft" and entity then
      for _ = 1, operation.started_count or 0 do
        if entity.crafting_queue_size == 0 then break end
        entity.cancel_crafting{index = 1, count = 1}
      end
    end
    finish_action(companion, operation.command_id, "cancelled", reason)
    operation.state = reason
    operation.finished_tick = game.tick
  end
end

local function initialize()
  storage.bridge = storage.bridge or {}
  storage.bridge.schema = 5
  halt("configuration_changed")
  storage.bridge.session = nil
end
script.on_init(initialize)
script.on_configuration_changed(initialize)
script.on_load(function() just_loaded = true end)

local function expire()
  local session = storage.bridge.session
  if session and game.tick >= session.expires_tick then
    halt("lease_expired")
    storage.bridge.session = nil
  end
end

local function inventory_contents(entity)
  local result = {}
  local inventory = entity.get_inventory(defines.inventory.character_main)
  if inventory then
    for _, item in pairs(inventory.get_contents()) do
      result[item.name .. ":" .. item.quality] = item.count
    end
  end
  return result
end

local function action_view(action)
  if not action then return nil end
  return {
    id = action.id, type = action.type, state = action.state,
    created_tick = action.created_tick, started_tick = action.started_tick,
    finished_tick = action.finished_tick, error = action.error,
    result = action.result, target = action.target
  }
end

local function operation_view(operation)
  if not operation then return {state = "idle"} end
  return {
    command_id = operation.command_id, type = operation.type,
    state = operation.state, started_tick = operation.started_tick,
    finished_tick = operation.finished_tick, target = operation.target_summary or operation.target,
    completed_count = operation.completed_count or 0,
    requested_count = operation.requested_count
  }
end

local function recent_actions(companion)
  local result = {}
  local order = companion.action_order or {}
  for index = #order, math.max(1, #order - 9), -1 do
    local action = companion.actions and companion.actions[order[index]]
    if action then result[#result + 1] = action_view(action) end
  end
  return result
end

local function motion_view(motion)
  if not motion then return {state = "idle"} end
  return {
    command_id = motion.command_id, state = motion.state, target = motion.target,
    started_tick = motion.started_tick, finished_tick = motion.finished_tick,
    waypoint_index = motion.path_index,
    waypoint_count = motion.path and #motion.path or motion.waypoint_count,
    replan_count = motion.replan_count or 0
  }
end

local function status()
  local bridge = storage.bridge
  local companion = bridge.companion
  local entity = valid_character()
  local result = {
    tick = game.tick, protocol = 5, lease_ticks = LEASE_TICKS,
    connected = bridge.session ~= nil,
    lease_remaining_ticks = bridge.session and math.max(0, bridge.session.expires_tick - game.tick) or 0,
    exists = entity ~= nil,
    state = companion and (entity and "ready" or "lost") or "absent"
  }
  if companion then
    companion.actions = companion.actions or {}
    companion.action_order = companion.action_order or {}
    result.id = companion.id
    result.name = companion.name
    result.motion = motion_view(companion.motion)
    result.operation = operation_view(companion.operation)
    result.actions = recent_actions(companion)
    local statistics = statistics_for(companion, entity)
    result.statistics = {
      spawned_tick = statistics.spawned_tick,
      connected_ticks = statistics.connected_ticks,
      distance_tiles = statistics.distance_tiles,
      moves_started = statistics.moves_started,
      moves_completed = statistics.moves_completed,
      moves_blocked = statistics.moves_blocked,
      moves_stopped = statistics.moves_stopped,
      replans = statistics.replans,
      items_mined = statistics.items_mined,
      items_crafted = statistics.items_crafted,
      entities_placed = statistics.entities_placed,
      entities_rotated = statistics.entities_rotated,
      items_transferred = statistics.items_transferred
    }
  end
  if entity then
    result.position = {x = entity.position.x, y = entity.position.y}
    result.surface = entity.surface.name
    result.health = entity.health
    result.inventory = inventory_contents(entity)
    result.walking = entity.walking_state.walking
  end
  return result
end

local function fail(code, message)
  error({code = code, message = message}, 0)
end

local function finite_number(value)
  return type(value) == "number" and value == value and math.abs(value) < 1000000
end

local function identifier(value)
  return type(value) == "string" and #value >= 1 and #value <= 80 and value:match("^[%w_-]+$")
end

local function require_owner(request)
  local session = storage.bridge.session
  if not session or request.session ~= session.token then
    fail("not_owner", "Acquire a controller lease before sending this command.")
  end
  session.expires_tick = game.tick + LEASE_TICKS
end

local function spawn(request)
  local bridge = storage.bridge
  if bridge.companion then
    if not valid_character() then
      fail("character_lost", "The character was destroyed. Restore a checkpoint to recover it.")
    end
    return status()
  end
  local name = request.name or "Ada"
  if type(name) ~= "string" or #name < 1 or #name > 48 or name:find("[%c]") then
    fail("invalid_name", "Name must contain 1..48 bytes and no control characters.")
  end
  local surface = game.get_surface("nauvis")
  if not surface then fail("no_surface", "The nauvis surface is unavailable.") end
  local center = game.forces.player.get_spawn_position(surface)
  surface.request_to_generate_chunks(center, 2)
  surface.force_generate_chunk_requests()
  local position = surface.find_non_colliding_position("character", center, 32, 0.5)
  if not position then fail("spawn_blocked", "No free character position near spawn.") end
  local entity = surface.create_entity{name = "character", position = position, force = "player"}
  if not entity then fail("spawn_failed", "Could not create the companion.") end
  entity.color = {r = 0.15, g = 0.85, b = 0.9}
  bridge.companion = {
    entity = entity, id = tostring(entity.unit_number), name = name,
    actions = {}, action_order = {},
    statistics = {
      spawned_tick = game.tick, connected_ticks = 0, distance_tiles = 0,
      moves_started = 0, moves_completed = 0, moves_blocked = 0,
      moves_stopped = 0, replans = 0, items_mined = 0, items_crafted = 0,
      entities_placed = 0, entities_rotated = 0, items_transferred = 0,
      last_position = {x = entity.position.x, y = entity.position.y}
    }
  }
  bridge.companion.label = rendering.draw_text{
    text = name, surface = surface, target = {entity = entity, offset = {0, -2.5}},
    color = entity.color, alignment = "center", scale = 1.2
  }
  return status()
end

local function remember_action(companion, request, target)
  companion.actions = companion.actions or {}
  companion.action_order = companion.action_order or {}
  local existing = companion.actions[request.id]
  if existing then return existing, false end
  local action = {
    id = request.id, type = request.action, state = "queued",
    created_tick = game.tick, target = target
  }
  companion.actions[request.id] = action
  companion.action_order[#companion.action_order + 1] = request.id
  while #companion.action_order > ACTION_HISTORY do
    local removed = table.remove(companion.action_order, 1)
    companion.actions[removed] = nil
  end
  return action, true
end

local function request_path(companion)
  local entity = valid_character()
  local motion = companion.motion
  if not entity or not motion then return end
  stop_entity(entity)
  motion.state = "pathfinding"
  motion.path = nil
  motion.path_index = nil
  local ok, request_id = pcall(function()
    return entity.surface.request_path{
      bounding_box = entity.prototype.collision_box,
      collision_mask = entity.prototype.collision_mask,
      start = entity.position,
      goal = {x = motion.target.x, y = motion.target.y},
      force = entity.force,
      radius = motion.radius,
      can_open_gates = true,
      path_resolution_modifier = 1,
      max_gap_size = 0,
      entity_to_ignore = entity
    }
  end)
  if not ok then
    halt("pathfinder_error")
    return
  end
  motion.path_request_id = request_id
end

local function begin_move(request)
  local companion = storage.bridge.companion
  local entity = valid_character()
  if not finite_number(request.x) or not finite_number(request.y) then
    fail("invalid_target", "Provide finite x and y coordinates.")
  end
  local radius = request.radius or ARRIVAL_DISTANCE
  if not finite_number(radius) or radius < 0.2 or radius > 10 then
    fail("invalid_radius", "Radius must be between 0.2 and 10 tiles.")
  end
  local target = {x = request.x, y = request.y, radius = radius}
  local action, created = remember_action(companion, request, target)
  if not created then return status() end
  local distance = math.sqrt((request.x - entity.position.x)^2 + (request.y - entity.position.y)^2)
  if distance > MAX_DISTANCE then
    action.state = "failed"
    action.finished_tick = game.tick
    action.error = "target_too_far"
    fail("target_too_far", "Move at most 256 tiles per command.")
  end
  halt("superseded")
  action.state = "running"
  action.started_tick = game.tick
  companion.motion = {
    command_id = request.id, state = "pathfinding", target = target, radius = radius,
    started_tick = game.tick, deadline_tick = game.tick + 7200,
    progress_tick = game.tick,
    progress_position = {x = entity.position.x, y = entity.position.y},
    replan_count = 0, busy_retries = 0
  }
  local statistics = statistics_for(companion, entity)
  statistics.moves_started = statistics.moves_started + 1
  request_path(companion)
  return status()
end

local function integer(value, minimum, maximum)
  return finite_number(value) and value == math.floor(value)
      and value >= minimum and value <= maximum
end

local function position_from(request)
  if not finite_number(request.x) or not finite_number(request.y) then
    fail("invalid_target", "Provide finite x and y coordinates.")
  end
  return {x = request.x, y = request.y}
end

local function entity_at(request, allow_character)
  local character = valid_character()
  local position = position_from(request)
  if request.name ~= nil and (type(request.name) ~= "string" or #request.name < 1
      or #request.name > 200 or request.name:find("[%c]")) then
    fail("invalid_target", "Entity name must contain 1..200 bytes and no control characters.")
  end
  local candidates = character.surface.find_entities_filtered{
    area = {{position.x - 0.75, position.y - 0.75}, {position.x + 0.75, position.y + 0.75}},
    name = request.name
  }
  local best, best_distance
  for _, candidate in ipairs(candidates) do
    if candidate.valid and (allow_character or candidate ~= character) then
      local dx, dy = candidate.position.x - position.x, candidate.position.y - position.y
      local distance = dx * dx + dy * dy
      if not best_distance or distance < best_distance then
        best, best_distance = candidate, distance
      end
    end
  end
  if not best then fail("target_not_found", "No matching entity is near that position.") end
  return best
end

local function entity_summary(entity)
  local result = {
    name = entity.name, type = entity.type,
    position = {x = entity.position.x, y = entity.position.y},
    direction = entity.direction, force = entity.force and entity.force.name or nil
  }
  if entity.health then result.health = entity.health end
  if entity.type == "resource" then result.amount = entity.amount end
  if entity.type == "mining-drill" then
    result.drop_position = {x = entity.drop_position.x, y = entity.drop_position.y}
    if entity.drop_target and entity.drop_target.valid then
      result.drop_target = {
        name = entity.drop_target.name,
        position = {x = entity.drop_target.position.x, y = entity.drop_target.position.y}
      }
    end
    if entity.mining_target and entity.mining_target.valid then
      result.mining_target = {
        name = entity.mining_target.name,
        position = {x = entity.mining_target.position.x, y = entity.mining_target.position.y}
      }
    end
  end
  return result
end

local function rotated_box(prototype, position, direction)
  local box = prototype.collision_box
  local points = {
    {x = box.left_top.x, y = box.left_top.y},
    {x = box.right_bottom.x, y = box.left_top.y},
    {x = box.right_bottom.x, y = box.right_bottom.y},
    {x = box.left_top.x, y = box.right_bottom.y}
  }
  local left, top, right, bottom
  for _, point in ipairs(points) do
    local x, y = point.x, point.y
    if direction == defines.direction.east then
      x, y = -point.y, point.x
    elseif direction == defines.direction.south then
      x, y = -point.x, -point.y
    elseif direction == defines.direction.west then
      x, y = point.y, -point.x
    end
    x, y = x + position.x, y + position.y
    left, top = math.min(left or x, x), math.min(top or y, y)
    right, bottom = math.max(right or x, x), math.max(bottom or y, y)
  end
  return {left_top = {x = left, y = top}, right_bottom = {x = right, y = bottom}}
end

local function boxes_overlap(left, right)
  return left.left_top.x < right.right_bottom.x
      and left.right_bottom.x > right.left_top.x
      and left.left_top.y < right.right_bottom.y
      and left.right_bottom.y > right.left_top.y
end

local function validate_plan(request)
  local character = valid_character()
  local placements = request.placements
  if type(placements) ~= "table" or #placements < 1 or #placements > 20 then
    fail("invalid_plan", "Plan must contain 1..20 placements.")
  end
  local inventory = character.get_inventory(defines.inventory.character_main)
  local required, seen, validated = {}, {}, {}
  for index, placement in ipairs(placements) do
    if type(placement) ~= "table" or not identifier(placement.id) or seen[placement.id] then
      fail("invalid_plan", "Placement " .. index .. " needs a unique id.")
    end
    seen[placement.id] = true
    if type(placement.item) ~= "string" then
      fail("invalid_plan", "Placement " .. placement.id .. " needs an item.")
    end
    local item = prototypes.item[placement.item]
    local placed = item and item.place_result
    if not placed then fail("not_placeable", "Placement " .. placement.id .. " has no placeable item.") end
    local position = {x = placement.x, y = placement.y}
    if not finite_number(position.x) or not finite_number(position.y) then
      fail("invalid_plan", "Placement " .. placement.id .. " has invalid coordinates.")
    end
    local direction = placement.direction or defines.direction.north
    if direction ~= defines.direction.north and direction ~= defines.direction.east
        and direction ~= defines.direction.south and direction ~= defines.direction.west then
      fail("invalid_direction", "Placement " .. placement.id .. " must use a cardinal direction.")
    end
    required[placement.item] = (required[placement.item] or 0) + 1
    if required[placement.item] > inventory.get_item_count(placement.item) then
      fail("missing_item", "The plan needs more " .. placement.item .. " than the character has.")
    end
    local build = {name = placed.name, position = position, direction = direction, force = character.force}
    if not character.can_place_entity(build) then
      fail("cannot_place", "Placement " .. placement.id .. " is blocked, invalid, or out of reach.")
    end
    local candidate = {
      id = placement.id, item = placement.item, entity = placed.name,
      position = position, direction = direction,
      collision_box = rotated_box(placed, position, direction)
    }
    for _, previous in ipairs(validated) do
      if boxes_overlap(candidate.collision_box, previous.collision_box) then
        fail("plan_collision", "Placements " .. previous.id .. " and " .. candidate.id .. " overlap.")
      end
    end
    validated[#validated + 1] = candidate
  end
  return {valid = true, placements = validated}
end

local inventory_defines = {
  chest = defines.inventory.chest,
  fuel = defines.inventory.fuel,
  source = defines.inventory.furnace_source,
  result = defines.inventory.furnace_result,
  input = defines.inventory.assembling_machine_input,
  output = defines.inventory.assembling_machine_output
}

local function inventory_summary(inventory)
  local result = {}
  if inventory then
    for _, item in pairs(inventory.get_contents()) do
      result[item.name .. ":" .. item.quality] = item.count
    end
  end
  return result
end

local function target_inventory(entity, selector)
  local inventory_id = inventory_defines[selector]
  if not inventory_id then
    fail("invalid_inventory", "Inventory must be chest, fuel, source, result, input, or output.")
  end
  local inventory = entity.get_inventory(inventory_id)
  if not inventory then fail("no_inventory", "That entity has no " .. selector .. " inventory.") end
  return inventory
end

local function inspect(request)
  local character = valid_character()
  local position = position_from(request)
  local radius = request.radius or 8
  if not finite_number(radius) or radius < 0 or radius > 32 then
    fail("invalid_radius", "Inspection radius must be between 0 and 32 tiles.")
  end
  local dx, dy = position.x - character.position.x, position.y - character.position.y
  if math.sqrt(dx * dx + dy * dy) + radius > MAX_OBSERVATION_RADIUS then
    fail("out_of_observation_range", "Inspection is limited to 32 tiles around the character.")
  end
  local result = {position = position, radius = radius, entities = {}}
  local entities = character.surface.find_entities_filtered{position = position, radius = radius}
  for _, entity in ipairs(entities) do
    if entity ~= character and #result.entities < MAX_INSPECT_ENTITIES then
      local summary = entity_summary(entity)
      if character.can_reach_entity(entity) then
        summary.reachable = true
        summary.inventories = {}
        for selector, inventory_id in pairs(inventory_defines) do
          local inventory = entity.get_inventory(inventory_id)
          if inventory then summary.inventories[selector] = inventory_summary(inventory) end
        end
      else
        summary.reachable = false
      end
      result.entities[#result.entities + 1] = summary
    end
  end
  result.truncated = #entities - 1 > #result.entities
  return result
end

local function compact_items(items)
  local result = {}
  for _, item in ipairs(items or {}) do
    result[#result + 1] = {
      type = item.type, name = item.name, amount = item.amount,
      amount_min = item.amount_min, amount_max = item.amount_max,
      probability = item.probability
    }
  end
  return result
end

local function sorted_aggregates(values, include_amount)
  local result = {}
  for name, value in pairs(values) do
    local record = {name = name, count = value.count}
    if include_amount then record.amount = value.amount end
    if value.nearest then record.nearest = value.nearest end
    result[#result + 1] = record
  end
  table.sort(result, function(left, right) return left.name < right.name end)
  return result
end

local function aggregate_entity(values, entity, origin, include_amount)
  local value = values[entity.name] or {count = 0, amount = 0}
  value.count = value.count + 1
  if include_amount then value.amount = value.amount + entity.amount end
  local dx, dy = entity.position.x - origin.x, entity.position.y - origin.y
  local distance = dx * dx + dy * dy
  if not value.nearest_distance or distance < value.nearest_distance then
    value.nearest_distance = distance
    value.nearest = {x = entity.position.x, y = entity.position.y}
  end
  values[entity.name] = value
end

local function add_task(tasks, kind, priority, entity, details)
  tasks[#tasks + 1] = {
    kind = kind, priority = priority,
    entity = {name = entity.name, position = {x = entity.position.x, y = entity.position.y}},
    details = details
  }
end

local function diagnose_machine(character, entity, tasks)
  if not character.can_reach_entity(entity) then return end
  local output = entity.get_output_inventory()
  if output and not output.is_empty() and output.is_full() then
    add_task(tasks, "blocked_output", 1, entity, {output = inventory_summary(output)})
  end

  local fuel = entity.get_fuel_inventory()
  local input
  if entity.type == "furnace" then
    input = entity.get_inventory(defines.inventory.furnace_source)
  elseif entity.type == "assembling-machine" or entity.type == "rocket-silo" then
    input = entity.get_inventory(defines.inventory.assembling_machine_input)
  end
  local burning = entity.burner and entity.burner.remaining_burning_fuel > 0
  if fuel and fuel.is_empty() and not burning and input and not input.is_empty() then
    add_task(tasks, "missing_fuel", 2, entity, {input = inventory_summary(input)})
  end

  if entity.type == "assembling-machine" or entity.type == "rocket-silo" then
    local recipe = entity.get_recipe()
    if recipe and input then
      local missing = {}
      for _, ingredient in ipairs(recipe.ingredients) do
        if ingredient.type == "item" then
          local present = input.get_item_count(ingredient.name)
          if present < ingredient.amount then
            missing[#missing + 1] = {name = ingredient.name, count = ingredient.amount - present}
          end
        elseif ingredient.type == "fluid" then
          local present = entity.get_fluid_count(ingredient.name)
          if present < ingredient.amount then
            missing[#missing + 1] = {
              name = ingredient.name, amount = ingredient.amount - present, type = "fluid"
            }
          end
        end
      end
      if #missing > 0 then
        add_task(tasks, "missing_ingredients", 3, entity, {recipe = recipe.name, missing = missing})
      end
    end
  end
end

local function recipe_observation(character)
  local available = {}
  local enabled_count = 0
  local total_available = 0
  for _, recipe in pairs(character.force.recipes) do
    if recipe.enabled and not recipe.hidden then
      enabled_count = enabled_count + 1
      local craftable = character.get_craftable_count(recipe)
      if craftable > 0 then
        total_available = total_available + 1
        available[#available + 1] = {
          name = recipe.name, craftable_count = craftable, energy = recipe.energy,
          ingredients = compact_items(recipe.ingredients), products = compact_items(recipe.products)
        }
      end
    end
  end
  table.sort(available, function(left, right) return left.name < right.name end)
  while #available > MAX_RECIPES do table.remove(available) end
  return {
    available = available, available_count = total_available,
    enabled_count = enabled_count, truncated = total_available > #available
  }
end

local function map_observation(character)
  local surface, force = character.surface, character.force
  local resources, buildings = {}, {}
  local charted, scanned, entity_count = 0, 0, 0
  local truncated = false
  for chunk in surface.get_chunks() do
    if force.is_chunk_charted(surface, chunk) then
      charted = charted + 1
      if scanned < MAX_MAP_CHUNKS and entity_count < MAX_MAP_ENTITIES then
        scanned = scanned + 1
        local area = {{chunk.x * 32, chunk.y * 32}, {(chunk.x + 1) * 32, (chunk.y + 1) * 32}}
        for _, entity in ipairs(surface.find_entities_filtered{area = area, type = "resource"}) do
          if entity_count < MAX_MAP_ENTITIES and math.floor(entity.position.x / 32) == chunk.x
              and math.floor(entity.position.y / 32) == chunk.y then
            aggregate_entity(resources, entity, character.position, true)
            entity_count = entity_count + 1
          elseif entity_count >= MAX_MAP_ENTITIES then
            truncated = true
          end
        end
        for _, entity in ipairs(surface.find_entities_filtered{area = area, force = force}) do
          if entity_count < MAX_MAP_ENTITIES and entity ~= character
              and math.floor(entity.position.x / 32) == chunk.x
              and math.floor(entity.position.y / 32) == chunk.y then
            aggregate_entity(buildings, entity, character.position, false)
            entity_count = entity_count + 1
          elseif entity_count >= MAX_MAP_ENTITIES then
            truncated = true
          end
        end
      else
        truncated = true
      end
    end
  end
  return {
    charted_chunks = charted, scanned_chunks = scanned, entity_count = entity_count,
    truncated = truncated or charted > scanned,
    resources = sorted_aggregates(resources, true),
    buildings = sorted_aggregates(buildings, false)
  }
end

local function observe(request)
  local character = valid_character()
  local radius = request.radius or 16
  if not finite_number(radius) or radius < 1 or radius > MAX_OBSERVATION_RADIUS then
    fail("invalid_radius", "Observation radius must be between 1 and 32 tiles.")
  end
  local resources, buildings, tasks = {}, {}, {}
  local entities = character.surface.find_entities_filtered{position = character.position, radius = radius}
  for _, entity in ipairs(entities) do
    if entity.type == "resource" then
      aggregate_entity(resources, entity, character.position, true)
    elseif entity ~= character and entity.force == character.force then
      local summary = entity_summary(entity)
      summary.reachable = character.can_reach_entity(entity)
      if summary.reachable then
        summary.inventories = {}
        for selector, inventory_id in pairs(inventory_defines) do
          local inventory = entity.get_inventory(inventory_id)
          if inventory then summary.inventories[selector] = inventory_summary(inventory) end
        end
        if entity.type == "furnace" or entity.type == "assembling-machine"
            or entity.type == "rocket-silo" then
          local recipe = entity.get_recipe()
          summary.recipe = recipe and recipe.name or nil
        end
      end
      buildings[#buildings + 1] = summary
      diagnose_machine(character, entity, tasks)
    end
  end
  table.sort(buildings, function(left, right)
    if left.name == right.name then
      if left.position.x == right.position.x then return left.position.y < right.position.y end
      return left.position.x < right.position.x
    end
    return left.name < right.name
  end)
  table.sort(tasks, function(left, right)
    if left.priority == right.priority then return left.kind < right.kind end
    return left.priority < right.priority
  end)
  local building_count, task_count = #buildings, #tasks
  while #buildings > MAX_LOCAL_BUILDINGS do table.remove(buildings) end
  while #tasks > MAX_TASKS do table.remove(tasks) end
  return {
    tick = game.tick,
    boundaries = {
      local_radius = radius, max_local_radius = MAX_OBSERVATION_RADIUS,
      details = "Entity details are local; inventories and diagnoses require interaction reach.",
      map = "Map summaries include only force-charted chunks.",
      map_chunk_cap = MAX_MAP_CHUNKS, map_entity_cap = MAX_MAP_ENTITIES,
      local_building_cap = MAX_LOCAL_BUILDINGS, task_cap = MAX_TASKS
    },
    self = {
      position = {x = character.position.x, y = character.position.y},
      surface = character.surface.name, inventory = inventory_contents(character)
    },
    local_area = {
      resources = sorted_aggregates(resources, true), buildings = buildings,
      entity_count = #entities - 1, building_count = building_count,
      buildings_truncated = building_count > #buildings
    },
    map = map_observation(character),
    recipes = recipe_observation(character),
    tasks = tasks, tasks_truncated = task_count > #tasks
  }
end

local function begin_operation(request, target)
  local companion = storage.bridge.companion
  local action, created = remember_action(companion, request, target)
  if not created then return action, false end
  halt("superseded")
  action.state = "running"
  action.started_tick = game.tick
  return action, true
end

local function begin_mine(request)
  local character = valid_character()
  local target = entity_at(request)
  local resource_amount = target.type == "resource" and target.amount or nil
  local count = request.count or 1
  if not integer(count, 1, 1000) then fail("invalid_count", "Mining count must be an integer from 1 to 1000.") end
  local action, created = begin_operation(request, entity_summary(target))
  if not created then return status() end
  if not target.minable then
    finish_action(storage.bridge.companion, request.id, "failed", "not_minable")
    fail("not_minable", "That entity cannot be mined.")
  end
  if not resource_amount and count ~= 1 then
    finish_action(storage.bridge.companion, request.id, "failed", "invalid_count")
    fail("invalid_count", "Non-resource entities can only be mined once.")
  end
  if not character.can_reach_entity(target) then
    finish_action(storage.bridge.companion, request.id, "failed", "out_of_reach")
    fail("out_of_reach", "Move within mining reach first.")
  end
  local companion = storage.bridge.companion
  local properties = target.prototype.mineable_properties
  local speed = (character.prototype.mining_speed or 0.5)
      * (1 + character.character_mining_speed_modifier + character.force.manual_mining_speed_modifier)
  if speed <= 0 or not properties or properties.required_fluid then
    finish_action(companion, request.id, "failed", "not_hand_minable")
    fail("not_hand_minable", "The character cannot hand-mine that entity.")
  end
  companion.operation = {
    command_id = request.id, type = "mine", state = "mining",
    started_tick = game.tick, deadline_tick = game.tick + 216000,
    target = target, target_summary = entity_summary(target),
    requested_count = count, completed_count = 0,
    last_amount = resource_amount, was_resource = resource_amount ~= nil,
    mining_ticks = math.max(1, math.ceil(properties.mining_time * 60 / speed))
  }
  companion.operation.next_mine_tick = game.tick + companion.operation.mining_ticks
  return status()
end

local function begin_craft(request)
  local character = valid_character()
  local count = request.count or 1
  if type(request.recipe) ~= "string" or not integer(count, 1, 1000) then
    fail("invalid_recipe", "Provide a recipe and an integer count from 1 to 1000.")
  end
  local target = {recipe = request.recipe, count = count}
  local action, created = begin_operation(request, target)
  if not created then return status() end
  if character.crafting_queue_size > 0 then
    finish_action(storage.bridge.companion, request.id, "failed", "crafting_busy")
    fail("crafting_busy", "Wait for the existing crafting queue to finish.")
  end
  if character.get_craftable_count(request.recipe) < count then
    finish_action(storage.bridge.companion, request.id, "failed", "not_craftable")
    fail("not_craftable", "The recipe is unavailable or its ingredients are missing.")
  end
  local started = character.begin_crafting{count = count, recipe = request.recipe, silent = true}
  if started ~= count then
    finish_action(storage.bridge.companion, request.id, "failed", "craft_failed")
    fail("craft_failed", "Factorio did not start the requested crafting count.")
  end
  storage.bridge.companion.operation = {
    command_id = request.id, type = "craft", state = "crafting",
    started_tick = game.tick, deadline_tick = game.tick + 216000,
    target = target, requested_count = count, started_count = started,
    completed_count = 0
  }
  return status()
end

local function instant_action(request, target, callback)
  local companion = storage.bridge.companion
  local action, created = remember_action(companion, request, target)
  if not created then return status() end
  halt("superseded")
  action.state = "running"
  action.started_tick = game.tick
  local ok, result = pcall(callback)
  if not ok then
    local reason = type(result) == "table" and result.code or "action_failed"
    finish_action(companion, request.id, "failed", reason)
    error(result, 0)
  end
  finish_action(companion, request.id, "completed", result)
  return status()
end

local function place(request)
  local character = valid_character()
  local position = position_from(request)
  if type(request.item) ~= "string" then fail("invalid_item", "Provide an item to place.") end
  local direction = request.direction or defines.direction.north
  if not integer(direction, 0, 15) then fail("invalid_direction", "Direction must be an integer from 0 to 15.") end
  local item = prototypes.item[request.item]
  local placed = item and item.place_result
  if not placed then fail("not_placeable", "That item does not place an entity.") end
  return instant_action(request, {item = request.item, position = position, direction = direction}, function()
    local inventory = character.get_inventory(defines.inventory.character_main)
    if inventory.get_item_count(request.item) < 1 then fail("missing_item", "The item is not in the character inventory.") end
    local build = {name = placed.name, position = position, direction = direction, force = character.force}
    if not character.can_place_entity(build) then fail("cannot_place", "The entity cannot be placed there or is out of build reach.") end
    if inventory.remove{name = request.item, count = 1} ~= 1 then fail("missing_item", "The item could not be removed.") end
    local entity = character.surface.create_entity{
      name = placed.name, position = position, direction = direction,
      force = character.force, raise_built = true
    }
    if not entity then
      inventory.insert{name = request.item, count = 1}
      fail("place_failed", "Factorio could not create the entity.")
    end
    local statistics = statistics_for(storage.bridge.companion, character)
    statistics.entities_placed = statistics.entities_placed + 1
    return entity_summary(entity)
  end)
end

local function rotate(request)
  local character = valid_character()
  local target = entity_at(request)
  local summary = entity_summary(target)
  return instant_action(request, summary, function()
    if not character.can_reach_entity(target) then fail("out_of_reach", "Move within interaction reach first.") end
    if not target.rotate{reverse = request.reverse == true} then fail("rotate_failed", "Factorio rejected the rotation.") end
    local statistics = statistics_for(storage.bridge.companion, character)
    statistics.entities_rotated = statistics.entities_rotated + 1
    return entity_summary(target)
  end)
end

local function transfer(request)
  local character = valid_character()
  local target = entity_at(request)
  local count = request.count or 1
  if type(request.item) ~= "string" or not integer(count, 1, 100000) then
    fail("invalid_transfer", "Provide an item and an integer count from 1 to 100000.")
  end
  if not prototypes.item[request.item] then fail("invalid_item", "That item does not exist.") end
  if request.direction ~= "to" and request.direction ~= "from" then
    fail("invalid_transfer", "Direction must be 'to' or 'from'.")
  end
  local description = {entity = entity_summary(target), inventory = request.inventory,
                       item = request.item, count = count, direction = request.direction}
  return instant_action(request, description, function()
    if not character.can_reach_entity(target) then fail("out_of_reach", "Move within interaction reach first.") end
    local character_inventory = character.get_inventory(defines.inventory.character_main)
    local entity_inventory = target_inventory(target, request.inventory)
    local source = request.direction == "to" and character_inventory or entity_inventory
    local destination = request.direction == "to" and entity_inventory or character_inventory
    local removed = source.remove{name = request.item, count = count}
    if removed == 0 then fail("missing_item", "The source inventory has none of that item.") end
    local inserted = destination.insert{name = request.item, count = removed}
    if inserted < removed then source.insert{name = request.item, count = removed - inserted} end
    if inserted == 0 then fail("destination_full", "The destination inventory cannot accept that item.") end
    local statistics = statistics_for(storage.bridge.companion, character)
    statistics.items_transferred = statistics.items_transferred + inserted
    return {item = request.item, count = inserted, direction = request.direction,
            inventory = request.inventory}
  end)
end

local function dispatch(request)
  expire()
  if request.action == "status" then return status() end
  if request.action == "spawn" then return spawn(request) end
  if request.action == "action" then
    local companion = storage.bridge.companion
    local action = companion and companion.actions and companion.actions[request.command_id]
    if not action then fail("unknown_command", "No retained action has that command id.") end
    return action_view(action)
  end
  if request.action == "stop" then
    halt("stopped")
    storage.bridge.session = nil
    return status()
  end
  if not valid_character() then fail("no_character", "Spawn a character first, or restore it if lost.") end
  if request.action == "acquire" then
    if not identifier(request.session) then fail("invalid_session", "Provide a session identifier.") end
    local session = storage.bridge.session
    if session and session.token ~= request.session then
      fail("busy", "Another controller holds the lease. Stop it or wait for its lease to expire.")
    end
    if not session then halt("reconnected") end
    storage.bridge.session = {token = request.session, expires_tick = game.tick + LEASE_TICKS}
    return status()
  end
  require_owner(request)
  if request.action == "heartbeat" then return status() end
  if request.action == "release" then
    halt("disconnected")
    storage.bridge.session = nil
    return status()
  end
  if request.action == "move" then return begin_move(request) end
  if request.action == "inspect" then return inspect(request) end
  if request.action == "observe" then return observe(request) end
  if request.action == "validate_plan" then return validate_plan(request) end
  if request.action == "mine" then return begin_mine(request) end
  if request.action == "craft" then return begin_craft(request) end
  if request.action == "place" then return place(request) end
  if request.action == "rotate" then return rotate(request) end
  if request.action == "transfer" then return transfer(request) end
  fail("unknown_action", "Unknown companion action.")
end

commands.add_command("companion", "Factorio AI companion JSON API (RCON only).", function(event)
  if event.player_index then
    game.get_player(event.player_index).print("Use the Python companion controller through RCON.")
    return
  end
  local request = helpers.json_to_table(event.parameter or "")
  if type(request) ~= "table" or request.version ~= 1 or not identifier(request.id) then
    rcon.print(helpers.table_to_json{ok = false, error = {code = "invalid_request", message = "Expected version 1 and a request id."}})
    return
  end
  local ok, value = pcall(dispatch, request)
  if not ok then
    if type(value) ~= "table" then
      halt("bridge_error")
      storage.bridge.session = nil
      log("Companion bridge error: " .. tostring(value))
      value = {code = "bridge_error", message = "Bridge failed; inspect the server log."}
    end
    rcon.print(helpers.table_to_json{id = request.id, ok = false, error = value})
  else
    rcon.print(helpers.table_to_json{id = request.id, ok = true, result = value})
  end
end)

script.on_event(defines.events.on_script_path_request_finished, function(event)
  local companion = storage.bridge.companion
  local motion = companion and companion.motion
  if not motion or motion.path_request_id ~= event.id or motion.finished_tick then return end
  motion.path_request_id = nil
  if event.try_again_later and motion.busy_retries < 3 then
    motion.busy_retries = motion.busy_retries + 1
    motion.state = "path_retry"
    motion.retry_tick = game.tick + 30
    return
  end
  if not event.path or #event.path == 0 then
    halt("unreachable")
    return
  end
  motion.path = {}
  for _, waypoint in ipairs(event.path) do
    motion.path[#motion.path + 1] = {x = waypoint.position.x, y = waypoint.position.y}
  end
  motion.path_index = 1
  motion.waypoint_count = #motion.path
  motion.state = "moving"
  motion.progress_tick = game.tick
  local entity = valid_character()
  motion.progress_position = entity and {x = entity.position.x, y = entity.position.y} or nil
end)

script.on_event(defines.events.on_tick, function()
  if just_loaded then
    just_loaded = false
    halt("server_restarted")
    storage.bridge.session = nil
  end
  expire()
  local bridge = storage.bridge
  local companion = bridge.companion
  local entity = valid_character()
  if not entity then
    if companion then halt("character_lost") end
    bridge.session = nil
    return
  end
  local statistics = statistics_for(companion, entity)
  statistics.connected_ticks = statistics.connected_ticks + (bridge.session and 1 or 0)
  local last = statistics.last_position
  local moved_x, moved_y = entity.position.x - last.x, entity.position.y - last.y
  statistics.distance_tiles = statistics.distance_tiles + math.sqrt(moved_x * moved_x + moved_y * moved_y)
  statistics.last_position = {x = entity.position.x, y = entity.position.y}

  local operation = companion.operation
  if bridge.session and operation and not operation.finished_tick then
    if game.tick >= operation.deadline_tick then
      stop_entity(entity)
      finish_action(companion, operation.command_id, "failed", "timed_out")
      operation.state = "timed_out"
      operation.finished_tick = game.tick
    elseif operation.type == "craft" then
      if entity.crafting_queue_size == 0 then
        operation.completed_count = operation.started_count
        operation.state = "completed"
        operation.finished_tick = game.tick
        statistics.items_crafted = statistics.items_crafted + operation.started_count
        finish_action(companion, operation.command_id, "completed", {
          recipe = operation.target.recipe, count = operation.started_count
        })
      end
    elseif operation.type == "mine" then
      local target = operation.target
      if target.valid then
        if not entity.can_reach_entity(target) then
          operation.state = "out_of_reach"
          operation.finished_tick = game.tick
          finish_action(companion, operation.command_id, "failed", "out_of_reach")
        elseif game.tick >= operation.next_mine_tick then
          local inventory = entity.get_inventory(defines.inventory.character_main)
          local amount_before = operation.was_resource and target.amount or nil
          local mined = target.mine{inventory = inventory, force = false, raise_destroyed = true}
          local progressed = mined or (amount_before and target.valid and target.amount < amount_before)
              or (amount_before and not target.valid)
          if not progressed then
            operation.state = "inventory_full"
            operation.finished_tick = game.tick
            finish_action(companion, operation.command_id, "failed", "inventory_full")
          else
            operation.completed_count = operation.completed_count + 1
            statistics.items_mined = statistics.items_mined + 1
            if operation.completed_count >= operation.requested_count then
              operation.state = "completed"
              operation.finished_tick = game.tick
              finish_action(companion, operation.command_id, "completed", {
                count = operation.completed_count, target = operation.target_summary
              })
            else
              operation.next_mine_tick = game.tick + operation.mining_ticks
            end
          end
        end
      else
        if operation.completed_count >= operation.requested_count then
          operation.state = "completed"
          operation.finished_tick = game.tick
          finish_action(companion, operation.command_id, "completed", {
            count = operation.completed_count, target = operation.target_summary
          })
        else
          operation.state = "target_lost"
          operation.finished_tick = game.tick
          finish_action(companion, operation.command_id, "failed", "target_lost")
        end
      end
    end
  end

  local motion = companion.motion
  if not bridge.session or not motion or motion.finished_tick then return end
  if motion.state == "path_retry" and game.tick >= motion.retry_tick then
    request_path(companion)
    return
  end
  if motion.state ~= "moving" then return end
  if game.tick >= motion.deadline_tick then halt("timed_out"); return end

  local waypoint = motion.path and motion.path[motion.path_index]
  if not waypoint then halt("arrived"); return end
  local dx, dy = waypoint.x - entity.position.x, waypoint.y - entity.position.y
  if dx * dx + dy * dy <= ARRIVAL_DISTANCE^2 then
    motion.path_index = motion.path_index + 1
    waypoint = motion.path[motion.path_index]
    if not waypoint then halt("arrived"); return end
    dx, dy = waypoint.x - entity.position.x, waypoint.y - entity.position.y
  end

  local progress = motion.progress_position
  if (entity.position.x - progress.x)^2 + (entity.position.y - progress.y)^2 > 0.01 then
    motion.progress_position = {x = entity.position.x, y = entity.position.y}
    motion.progress_tick = game.tick
  elseif game.tick - motion.progress_tick >= STUCK_TICKS then
    if motion.replan_count < MAX_REPLANS then
      motion.replan_count = motion.replan_count + 1
      statistics.replans = statistics.replans + 1
      motion.progress_tick = game.tick
      motion.progress_position = {x = entity.position.x, y = entity.position.y}
      request_path(companion)
      return
    end
    halt("blocked")
    return
  end

  local horizontal = math.abs(dx) > 0.12 and (dx > 0 and 1 or -1) or 0
  local vertical = math.abs(dy) > 0.12 and (dy > 0 and 1 or -1) or 0
  local directions = {
    ["0,-1"] = defines.direction.north, ["1,-1"] = defines.direction.northeast,
    ["1,0"] = defines.direction.east, ["1,1"] = defines.direction.southeast,
    ["0,1"] = defines.direction.south, ["-1,1"] = defines.direction.southwest,
    ["-1,0"] = defines.direction.west, ["-1,-1"] = defines.direction.northwest
  }
  local direction = directions[horizontal .. "," .. vertical]
  if direction then entity.walking_state = {walking = true, direction = direction} end
end)
