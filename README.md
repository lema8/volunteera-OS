# Volunteera: Origins & Abilities

A polished, expandable **Origins & Abilities** framework for **Minecraft Java 26.2** (Fabric).

Choose an origin, unlock unique abilities through XP progression and world trials,
and manage everything through a compact HUD + in-game menu.

![Flame + wings](src/main/resources/assets/volunteera/icon.png)

## Features

- **3 fully playable origins** (30 abilities total), designed to be *genuinely different*:
  - **Emberborn** – fire magic with real upkeep: a spawn-proof light aura, melee
    ignition, flame dash, a nova slam, a burning shroud, self-cooking meals,
    a rocket jump, a trial-gated explosive Pyre Ball – and *Burnout*: water douses
    you and raises your costs.
  - **Skyborne** – the sky is yours: limited free flight with an energy meter
    (land to recharge), ground-charged wind bursts, gliding, aerial archery,
    a trial-gated meteor dive, and honest weaknesses (frail, always hungry,
    knocked out of the sky by heavy hits).
  - **Deepforge** – a one-block-tall master miner: up to **5× mining speed with a
    pickaxe**, huge health pool, ore-sense radar, hammer-fists, lava bathing,
    a trial-gated fortress stance and a real placed **lantern of light**.
- **Ability archetypes**: active, toggle (timed, toggled), passive, charge-based,
  flight-resource, and **World Abilities** gated behind in-world trials.
- **Server-authoritative**: all costs, cooldowns, unlocks, flight and energy live
  on the server. The client renders only.
- **Persistence that survives everything** (death, respawn, restarts) via data
  attachments; volatile session state intentionally resets on login.
- **Real balancing costs**: Minecraft's actual hunger system, non-lethal health
  costs, per-level cooldowns, meaningful tradeoffs.
- **HUD**: origin label, 6 hotkey chips with cooldowns/toggle state, flight
  energy meter with low-energy warning, wind-charge pips.
- **Menu** (`O`): pick origins, browse every ability, read descriptions, levels,
  costs, cooldowns, requirements and trial progress.
- **Commands** (`/volunteera`, permission level 2): info, origin switch, unlock
  ability/all, set level, trial progress/complete, refill resources, reset
  cooldowns, reset progression, test-mode (everything unlocked instantly), config reload.
- **Config** (`config/volunteera.json`): multipliers, flight rates, radii, PvP toggle.
- **One gameplay mixin total** (natural spawn suppression for Radiance) - minimal
  vanilla interference, great mod compatibility.

## Building

```bash
./gradlew build
```

Requires Java 25. The jar lands in `build/libs/`. CI builds every push (see
`.github/workflows/build.yml`) and uploads the artifact.

## Controls

| Key | Action |
|---|---|
| `O` | Open the Origins menu |
| `Z` `X` `C` `V` `B` `N` | HUD ability slots 1–6 |

All keys are rebindable under *Options → Controls → Volunteera*.

## Extending

Adding an origin is one content class + one registration line - the framework
(registry, progression, sync, HUD, menu, commands, config) picks it up
automatically. See `ARCHITECTURE.md` for the full design document.

## License

MIT
