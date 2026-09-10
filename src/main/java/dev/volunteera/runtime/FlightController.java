package dev.volunteera.runtime;

import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.player.Abilities;

import dev.volunteera.VolunteeraMod;
import dev.volunteera.config.ModConfig;
import dev.volunteera.net.StateSync;

/**
 * Owns the Ascension flight resource. The server grants/revokes vanilla
 * {@code mayfly} and drains energy while the player is actually flying, so
 * flight permission is always server-side. Energy regenerates only while on
 * the ground, which prevents permanent flight.
 *
 * <p>Edge cases: death, dimension change and gamemode switches revoke the
 * mayfly grant (vanilla resets abilities packets); the toggle itself is
 * cleared by the toggle engine when it observes an invalid state.
 */
public final class FlightController {
	public static final float MAX_ENERGY = 100.0f;

	private FlightController() {
	}

	public static void tick(ServerPlayer player, PlayerRuntime rt) {
		ModConfig.Flight cfg = ModConfig.get().flight;

		// Creative/spectator players fly through vanilla means; don't fight them.
		if (player.getAbilities().instabuild) {
			return;
		}

		boolean flying = rt.mayFlyGranted && player.getAbilities().flying;
		if (rt.flightPhase == PlayerRuntime.FlightPhase.FLYING && !rt.mayFlyGranted) {
			rt.flightPhase = PlayerRuntime.FlightPhase.NONE;
		}

		if (flying) {
			rt.flightPhase = PlayerRuntime.FlightPhase.FLYING;
			float drain = cfg.drainPerSecond / 20.0f;
			if (aboveHeat(player)) {
				drain *= cfg.thermalDrainFactor;
			}
			rt.flightEnergy -= drain;
			if (rt.flightEnergy <= 0.0f) {
				rt.flightEnergy = 0.0f;
				deplete(player, rt);
			}
		} else if (player.onGround()) {
			if (rt.flightPhase == PlayerRuntime.FlightPhase.FLYING) {
				rt.flightPhase = PlayerRuntime.FlightPhase.CHARGING;
			}
			if (rt.flightEnergy < MAX_ENERGY) {
				rt.flightEnergy = Math.min(MAX_ENERGY, rt.flightEnergy + cfg.regenPerSecond / 20.0f);
				if (rt.flightEnergy >= MAX_ENERGY) {
					rt.flightPhase = PlayerRuntime.FlightPhase.NONE;
					rt.flightLowWarned = false;
					StateSync.sync(player);
				}
			} else if (rt.flightPhase == PlayerRuntime.FlightPhase.CHARGING) {
				rt.flightPhase = PlayerRuntime.FlightPhase.NONE;
			}
		}
	}

	/** Called when energy hits zero: revoke flight, tell the player, drop phase. */
	public static void deplete(ServerPlayer player, PlayerRuntime rt) {
		revokeMayFly(player, rt);
		rt.flightPhase = PlayerRuntime.FlightPhase.NONE;
		player.sendSystemMessage(Component.translatable("message.volunteera.flight_depleted"));
		StateSync.sync(player);
	}

	public static boolean grantMayFly(ServerPlayer player, PlayerRuntime rt) {
		if (player.getAbilities().instabuild) {
			return false;
		}
		rt.mayFlyGranted = true;
		Abilities abilities = player.getAbilities();
		abilities.mayfly = true;
		player.onUpdateAbilities();
		return true;
	}

	public static void revokeMayFly(ServerPlayer player, PlayerRuntime rt) {
		if (!rt.mayFlyGranted) {
			return;
		}
		rt.mayFlyGranted = false;
		Abilities abilities = player.getAbilities();
		abilities.mayfly = false;
		abilities.flying = false;
		player.onUpdateAbilities();
	}

	public static boolean hasEnergy(PlayerRuntime rt, float amount) {
		return rt.flightEnergy >= amount;
	}

	/** Thermal Currents: over magma/fire/campfire/lava the drain nearly stops. */
	private static boolean aboveHeat(ServerPlayer player) {
		var level = player.level();
		var below = player.blockPosition().below();
		var state = level.getBlockState(below);
		if (state.is(net.minecraft.world.level.block.Blocks.MAGMA_BLOCK)
				|| state.is(net.minecraft.world.level.block.Blocks.FIRE)
				|| state.is(net.minecraft.world.level.block.Blocks.CAMPFIRE)) {
			return true;
		}
		return level.getFluidState(player.blockPosition().below(2)).is(net.minecraft.world.level.material.Fluids.LAVA);
	}

	static {
		VolunteeraMod.LOGGER.debug("FlightController ready");
	}
}
