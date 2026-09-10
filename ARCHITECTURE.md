# Volunteera: Origins & Abilities — Architecture

A polished, expandable Origins/Abilities framework for **Minecraft Java 26.2** on **Fabric**.

> Target toolchain (verified against the official `fabric-example-mod@26.2` template):
> Minecraft **26.2**, Fabric Loader **0.19.5**, Fabric API **0.159.0+26.2**,
> Loom **1.17-SNAPSHOT** (plugin id `net.fabricmc.fabric-loom`, unobfuscated game,
> Mojang real names), Java **25**, Gradle **9.5.1**.

## 1. Design goals

* **Authoritative server.** Every gameplay-critical decision (activation, costs,
  cooldowns, unlocks, flight permission, energy) is computed on the server. The
  client only renders UI and sends *requests*.
* **Data-driven content.** Origins and Abilities are plain registered objects.
  Adding an origin = one new content class + one registration line. No engine
  changes.
* **Varied ability archetypes.** The framework natively supports:
  *passive* (attribute/event driven), *active* (instant), *toggle* (timed, can be
  switched off), *charged* (charge pool, e.g. Gale Burst), *flight* (custom
  resource + meter), and *world* abilities (trial-gated discovery).
* **Balanced costs.** Abilities consume *real* Minecraft food points
  (`FoodData`), occasionally health (`hurtServer` with a non-lethal magic
  source), and always time (cooldowns with per-level values). Strong abilities
  cost more; spam has consequences.

## 2. Module map

```
dev.volunteera
├── VolunteeraMod            main entrypoint, boot order
├── api/                     FRAMEWORK (no Minecraft gameplay logic inside)
│   ├── Origin               id, display, attribute profile, ordered abilities, HUD slots
│   ├── Origins              origin registry
│   ├── Ability              abstract: kind, categories, max level, unlock XP thresholds,
│   │                        per-level cooldowns/costs, behavior hooks
│   ├── Abilities            ability registry
│   ├── AbilityKind          ACTIVE / TOGGLE / PASSIVE
│   ├── AbilityCategory      COMBAT / UTILITY / MOVEMENT / SPECIAL
│   ├── Cost                 hunger / health / held-item costs with validation
│   ├── ActivationResult     success | typed failure reason (fed back to the player)
│   └── Trial                world-ability trials (kill / break / damage / condition)
├── content/                 CONTENT (one class per origin, registers everything)
│   ├── EmberbornContent     10 abilities (2 world)
│   ├── SkyborneContent      10 abilities (2 world)
│   └── DeepforgeContent     10 abilities (2 world)
├── progress/
│   ├── ProgressData         persisted record: origin, ability levels, trial progress
│   └── ProgressManager      unlock/upgrade/switch logic (server-authoritative)
├── runtime/
│   ├── PlayerRuntime        per-session volatile state: cooldown timestamps, toggles,
│   │                        flight energy, charges, aura state
│   ├── RuntimeManager       lifecycle, ticking, cleanup, dirty flags
│   └── FlightController     mayfly grant/revoke, drain/regen, edge cases
├── server/
│   ├── AbilityEngine        the validation pipeline: unlocked? level? cooldown? cost?
│   │                        environment? -> execute -> charge -> cooldown -> sync
│   ├── OriginAttributes     transient attribute modifiers, resolved via registry keys
│   ├── EventHooks           damage modification, trials, death/respawn, join/mode changes
│   ├── SpawnSuppression     cached suppression queries used by the spawn mixin
│   └── AbilityFx            damage/knockback/particle/sound helpers (server side)
├── net/
│   ├── Payloads             C2S UseAbility/SetOrigin/RequestSync + S2C SyncState
│   ├── Networking           registration + server handlers (all validation server-side)
│   └── StateSync            dirty-flag + throttled full-state snapshots
├── command/ModCommands      /volunteera … (permission-gated, level 2)
├── config/ModConfig         JSON config: multipliers, flight, radii, trials, toggles
├── mixin/MobSpawnRulesMixin the ONLY gameplay mixin (natural spawn suppression)
└── client/                  CLIENT (UI only)
    ├── VolunteeraClient     keymappings, HUD registration, packet receivers
    ├── ClientState          mirror of the last synced server snapshot
    ├── hud/OriginsHud       HUD element: origin, ability chips, cooldowns, energy
    ├── screen/OriginsScreen the Origin/Ability menu
    └── Keybinds             configurable keymappings (Z X C V B N + O)
```

## 3. Framework concepts

### Origin
Immutable definition: id, display name, lore, color, list of abilities, the six
HUD slot ability ids, and a *core attribute profile* applied whenever the origin
is active (e.g. Deepforge: `generic.scale ×0.55` ⇒ ~1 block tall, +6 max
health, −10% speed, reduced reach).

### Ability
| Field | Meaning |
|---|---|
| `kind` | ACTIVE (instant), TOGGLE (timed on/off), PASSIVE (always on) |
| `maxLevel` | 1–3 (not every ability is upgradeable) |
| `unlockXp` | XP *level* thresholds per upgrade tier (vanilla XP levels gate auto-progression) |
| `cooldownTicks[level]` | per-level cooldown |
| `cost[level]` | hunger / health / held items per level |
| `worldTrial` | if present, the ability is a **World Ability** unlocked by a Trial, not XP |
| `hudSlot` | 0–5 for direct hotkeys, −1 = menu-only casting |

Behavior hooks (overridable): `activate(ctx, level)`, `deactivate`, `toggleTick`,
`applyPassives(player, level, apply)`, `available(ctx)`.

### Progression
* XP-based auto-progression: every 20 ticks the server promotes abilities whose
  next-tier XP threshold is met. No grind items, no custom XP — vanilla levels.
* World Abilities: unlocked by **Trials** (slay 15 blazes, survive a lightning
  strike, mine 64 deepslate below Y=-40, glide 2000 m, …). Trials are registry
  objects with progress stored per player and persist across everything.

### Persistence
`ProgressData` rides on Fabric **data attachments** (`persistent` + `copyOnDeath`):
survives death, respawn, logout, restart, dimension change. Volatile runtime
state intentionally resets between sessions (login = cooldowns finished, energy
full) which removes any client/server desync window.

### Networking
Custom payloads via `ServerPlayNetworking` / `ClientPlayNetworking`:
* `c2s:use_ability` {abilityId} — client request; server validates everything.
* `c2s:set_origin` {originId} — validated (switching may be disabled in config).
* `c2s:request_sync` — client asks for a snapshot after (re)spawn/join.
* `s2c:sync_state` — full authoritative snapshot: origin, unlocked levels,
  cooldown ticks remaining, active toggles, flight energy + phase, charges,
  trial progress. Sent on change and throttled (≥1s) while "live". Cooldowns are
  interpolated client-side between snapshots.

### Spawn suppression (Radiance)
`Mob.checkSpawnRules` is the vanilla convergence point for natural spawns. A
single mixin cancels natural spawns inside an active Radiance radius.
`SpawnSuppression` rebuilds a tiny center/radius list once per second (not per
mob check), so cost is O(players-with-radiance), not O(world).

### Dwarf size
Size uses the vanilla `generic.scale` attribute (synced, affects model +
collision box ⇒ ~0.99 blocks tall) — no mixin, works in multiplayer, and the
mining speed uses `player.block_break_speed`, so client-side break progress
prediction stays correct.

### Flight (Skyborne: Ascension)
Energy drains while `abilities.mayfly` flight is active (server-timed), regenerates
only while on ground. `mayfly` is granted/revoked server-side via the vanilla
abilities packet, so *the server owns flight*. Death, dimension change, gamemode
switch and logout all reset the grant; the client merely renders the meter.

## 4. Extending (adding an origin)

1. Create `content/MyOriginContent.java` — build `Origin` + `Ability`s with the
   builder DSL, registering trial(s) if needed.
2. Add one line in `VolunteeraMod#initialize`: `MyOriginContent.register();`
3. Add lang entries. Done — HUD, menu, sync, commands, config and persistence
   pick it up automatically.
