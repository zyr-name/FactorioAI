-- Persistent, lease-protected companion actions and collision-aware navigation.
local LEASE_TICKS = 180
local STUCK_TICKS = 90
local MAX_DISTANCE = 256
local ARRIVAL_DISTANCE = 0.28
local MAX_REPLANS = 3
local ACTION_HISTORY = 40
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

local function finish_action(companion, state, reason)
  local motion = companion and companion.motion
  local action = motion and companion.actions and companion.actions[motion.command_id]
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
    finish_action(companion, terminal, reason)
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
end

local function initialize()
  storage.bridge = storage.bridge or {}
  storage.bridge.schema = 2
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
    tick = game.tick, protocol = 2, lease_ticks = LEASE_TICKS,
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
      replans = statistics.replans
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
      moves_stopped = 0, replans = 0,
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
