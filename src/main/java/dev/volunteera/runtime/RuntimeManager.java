package dev.volunteera.runtime;

import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

import net.minecraft.server.level.ServerPlayer;

import dev.volunteera.VolunteeraMod;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;

/**
 * Owns per-player runtime state and drives the per-tick server update:
 * cooldown expiry is lazy, but toggles, flight energy and charge pools tick
 * here. Also runs the cheap 10-tick "passive tick" pass.
 */
public final class RuntimeManager {
	private static final Map<UUID, PlayerRuntime> RUNTIMES = new ConcurrentHashMap<>();

	private RuntimeManager() {
	}

	public static void init() {
		ServerTickEvents.END_SERVER_TICK.register(server -> {
			for (ServerPlayer player : server.getPlayerList().getPlayers()) {
				tickPlayer(player);
			}
		});
		ServerPlayConnectionEvents.DISCONNECT.register((handler, server) ->
				RUNTIMES.remove(handler.getPlayer().getUUID()));
	}

	public static PlayerRuntime get(ServerPlayer player) {
		return RUNTIMES.computeIfAbsent(player.getUUID(), id -> new PlayerRuntime());
	}

	private static void tickPlayer(ServerPlayer player) {
		PlayerRuntime rt = get(player);
		try {
			FlightController.tick(player, rt);
			ToggleTicker.tick(player, rt);
			if (player.tickCount % 10 == 0) {
				PassiveTicker.tick(player, rt);
			}
		} catch (Exception e) {
			VolunteeraMod.LOGGER.error("Runtime tick failed for {}", player.getGameProfile().getName(), e);
		}
	}

	/** One-off aggregator so RuntimeManager stays slim. */
	private static final class ToggleTicker {
		static void tick(ServerPlayer player, PlayerRuntime rt) {
			dev.volunteera.server.ToggleEngine.tick(player, rt);
		}
	}

	private static final class PassiveTicker {
		static void tick(ServerPlayer player, PlayerRuntime rt) {
			dev.volunteera.server.PassiveEngine.tick(player, rt);
		}
	}
}
