package dev.volunteera.server;

import java.util.HashSet;
import java.util.Iterator;
import java.util.Set;

import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;

import dev.volunteera.VolunteeraMod;
import dev.volunteera.api.Ability;
import dev.volunteera.api.Origin;
import dev.volunteera.api.Origins;
import dev.volunteera.net.StateSync;
import dev.volunteera.progress.ProgressManager;
import dev.volunteera.runtime.PlayerRuntime;
import dev.volunteera.runtime.RuntimeManager;

/**
 * Ticks active toggle abilities: handles timed expiry, per-tick upkeep and
 * deactivation. Deactivation never throws into the tick loop.
 */
public final class ToggleEngine {
	private ToggleEngine() {
	}

	public static void tick(ServerPlayer player, PlayerRuntime rt) {
		if (rt.activeToggles.isEmpty()) {
			return;
		}
		Origin origin = Origins.get(ProgressData.get(player).origin());
		if (origin == null) {
			rt.activeToggles.clear();
			return;
		}
		long now = player.level().getGameTime();
		Set<Identifier> expired = null;
		for (Identifier abilityId : rt.activeToggles) {
			Long end = rt.toggleEnds.get(abilityId);
			if (end != null && now >= end) {
				if (expired == null) {
					expired = new HashSet<>();
				}
				expired.add(abilityId);
			}
		}
		if (expired != null) {
			for (Identifier abilityId : expired) {
				Ability ability = origin.ability(abilityId);
				if (ability != null) {
					deactivate(player, rt, origin, ability,
							ProgressManager.effectiveLevel(player, ability), true);
				}
			}
			StateSync.sync(player);
		}
		// Per-tick upkeep, guarded per ability.
		for (Iterator<Identifier> it = rt.activeToggles.iterator(); it.hasNext(); ) {
			Identifier abilityId = it.next();
			Ability ability = origin.ability(abilityId);
			if (ability == null) {
				it.remove();
				continue;
			}
			try {
				ability.toggleTick(player, ProgressManager.effectiveLevel(player, ability));
			} catch (Exception e) {
				VolunteeraMod.LOGGER.error("toggleTick({}) failed for {}", abilityId, player.getGameProfile().getName(), e);
				deactivate(player, rt, origin, ability, ProgressManager.effectiveLevel(player, ability), false);
			}
		}
	}

	/** Deactivates a toggle: runs the ability's deactivate hook and clears state. */
	public static void deactivate(ServerPlayer player, PlayerRuntime rt, Origin origin,
			Ability ability, int level, boolean announce) {
		if (!rt.activeToggles.remove(ability.id())) {
			return;
		}
		rt.toggleEnds.remove(ability.id());
		try {
			ability.deactivate(player, level);
		} catch (Exception e) {
			VolunteeraMod.LOGGER.error("deactivate({}) failed for {}", ability.id(), player.getGameProfile().getName(), e);
		}
		if (announce && ability.kind() == dev.volunteera.api.AbilityKind.TOGGLE) {
			player.connection.send(new net.minecraft.network.protocol.game.ClientboundSetActionBarTextPacket(
					net.minecraft.network.chat.Component.translatable("message.volunteera.already_inactive",
							net.minecraft.network.chat.Component.translatable(ability.nameKey()))));
		}
	}
}
