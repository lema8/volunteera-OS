package dev.volunteera.runtime;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;

import dev.volunteera.api.Origin;

/**
 * Volatile, session-scoped per-player state: cooldown timers, active toggles,
 * flight energy and charge pools. Deliberately NOT persisted - logging in
 * always starts with full energy and no cooldowns, which keeps the server as
 * the single source of truth and eliminates any client/server desync window.
 */
public final class PlayerRuntime {
	public enum FlightPhase {
		NONE,
		FLYING,
		CHARGING
	}

	/** Cooldown end time, in server game ticks, keyed by ability id. */
	public final Map<Identifier, Long> cooldownEnds = new HashMap<>();
	/** Toggled abilities currently active. */
	public final Set<Identifier> activeToggles = new HashSet<>();
	/** Game tick at which a timed toggle automatically ends. */
	public final Map<Identifier, Long> toggleEnds = new HashMap<>();

	/** Flight energy 0..max. */
	public float flightEnergy = 100.0f;
	public FlightPhase flightPhase = FlightPhase.NONE;
	/** Set while the server has granted mayfly for Ascension. */
	public boolean mayFlyGranted;

	/** Gale Burst charge pool + refill progress (ticks on ground). */
	public int windCharges;
	public int chargeRefillTicks;

	/** Emberborn douse timer: ticks remaining of "flames doused". */
	public int dousedTicks;
	/** Server-side Radiance state used by the spawn-suppression mixin. */
	public boolean radianceActive;
	public int radianceRadius;

	/** Set when Tempest Dive is falling toward the ground. */
	public boolean tempestDiving;
	/** Set while the Glide toggle is active (for trial accumulation). */
	public boolean gliding;
	/** Glide/elytra distance accumulation for the Tempest trial. */
	public double glideAccum;
	/** Previous tick position for distance accumulation. */
	public net.minecraft.world.phys.Vec3 prevPos;
	/** Lantern of the Deepforge: currently placed light block. */
	public boolean lanternPlaced;
	/** Position of the placed lantern light block. */
	public net.minecraft.core.BlockPos lanternPos;
	/** Ticks since Tempest Dive started (safety timeout). */
	public int tempestTicks;

	public long lastSyncTick;
	public boolean flightLowWarned;

	public void onOriginChanged() {
		activeToggles.clear();
		toggleEnds.clear();
		cooldownEnds.clear();
		flightPhase = FlightPhase.NONE;
		mayFlyGranted = false;
		windCharges = 0;
		radianceActive = false;
		radianceRadius = 0;
		tempestDiving = false;
		tempestTicks = 0;
		lanternPlaced = false;
		lanternPos = null;
		gliding = false;
		glideAccum = 0.0;
		prevPos = null;
		dousedTicks = 0;
		flightEnergy = 100.0f;
		flightLowWarned = false;
	}

	public boolean isOnCooldown(Identifier ability, long now) {
		Long end = cooldownEnds.get(ability);
		return end != null && now < end;
	}

	public long cooldownRemaining(Identifier ability, long now) {
		Long end = cooldownEnds.get(ability);
		if (end == null) {
			return 0;
		}
		return Math.max(0, end - now);
	}

	public void startCooldown(Identifier ability, long now, long durationTicks) {
		if (durationTicks <= 0) {
			cooldownEnds.remove(ability);
		} else {
			cooldownEnds.put(ability, now + durationTicks);
		}
	}

	public void deactivateAllToggles(ServerPlayer player, Origin origin) {
		for (Identifier abilityId : new HashSet<>(activeToggles)) {
			var ability = origin.ability(abilityId);
			if (ability != null) {
				try {
					ability.deactivate(player, dev.volunteera.progress.ProgressManager.effectiveLevel(player, ability));
				} catch (Exception e) {
					dev.volunteera.VolunteeraMod.LOGGER.error("Failed to deactivate {} for {}", abilityId, player.getGameProfile().getName(), e);
				}
			}
		}
		activeToggles.clear();
		toggleEnds.clear();
	}
}
