package dev.volunteera.server;

import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.game.ClientboundSetActionBarTextPacket;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.item.ItemStack;

import dev.volunteera.api.Abilities;
import dev.volunteera.api.Ability;
import dev.volunteera.api.ActivationResult;
import dev.volunteera.api.Cost;
import dev.volunteera.api.Origin;
import dev.volunteera.api.Origins;
import dev.volunteera.config.ModConfig;
import dev.volunteera.net.StateSync;
import dev.volunteera.progress.ProgressData;
import dev.volunteera.progress.ProgressManager;
import dev.volunteera.runtime.PlayerRuntime;
import dev.volunteera.runtime.RuntimeManager;

/**
 * The authoritative activation pipeline. Everything gameplay-critical is
 * validated here on the server: origin membership, unlock state, cooldown,
 * resource costs and per-ability environment checks. The client can only
 * request an activation.
 *
 * <p>Order of checks (cheapest and clearest first):
 * unlock → cooldown → costs → environment → execute → pay → start cooldown.
 */
public final class AbilityEngine {
	private AbilityEngine() {
	}

	/** Entry point for a client (or command) activation request. */
	public static void tryActivate(ServerPlayer player, Identifier abilityId) {
		Ability ability = Abilities.get(abilityId);
		if (ability == null) {
			feedback(player, ActivationResult.FailureReason.UNAVAILABLE, null);
			return;
		}
		ProgressData data = ProgressData.get(player);
		Origin origin = Origins.get(data.origin());
		if (origin == null || origin.ability(abilityId) == null) {
			feedback(player, ActivationResult.FailureReason.NOT_UNLOCKED, null);
			return;
		}
		int level = ProgressManager.effectiveLevel(player, ability);
		if (level <= 0) {
			feedback(player, ActivationResult.FailureReason.NOT_UNLOCKED, null);
			return;
		}

		PlayerRuntime rt = RuntimeManager.get(player);
		long now = player.level().getGameTime();

		if (ability.kind() == dev.volunteera.api.AbilityKind.TOGGLE && rt.activeToggles.contains(abilityId)) {
			// Toggles deactivate for free (their upkeep already drained resources).
			ToggleEngine.deactivate(player, rt, origin, ability, level, true);
			StateSync.sync(player);
			return;
		}

		if (rt.isOnCooldown(abilityId, now)) {
			long remaining = rt.cooldownRemaining(abilityId, now);
			feedback(player, ActivationResult.FailureReason.ON_COOLDOWN, String.format("%.1fs", remaining / 20.0));
			return;
		}

		Cost cost = ability.cost(level).scaled(ModConfig.get().hungerCostMultiplier);
		if (rt.dousedTicks > 0 && origin.id().getPath().equals("emberborn")) {
			cost = cost.scaled(1.5f); // Burnout: doused flames gutter and bite
		}
		ActivationResult costCheck = checkCost(player, cost);
		if (!costCheck.success()) {
			feedback(player, costCheck.reason(), costCheck.detail());
			return;
		}

		ActivationResult env = ability.checkEnvironment(player, level);
		if (env != null && !env.success()) {
			feedback(player, env.reason(), env.detail());
			return;
		}

		ActivationResult result;
		try {
			result = ability.activate(player, level);
		} catch (Exception e) {
			dev.volunteera.VolunteeraMod.LOGGER.error("Ability {} threw during activation for {}",
					abilityId, player.getGameProfile().name(), e);
			result = ActivationResult.fail(ActivationResult.FailureReason.UNAVAILABLE);
		}
		if (result == null || !result.success()) {
			feedback(player, result == null ? ActivationResult.FailureReason.UNAVAILABLE : result.reason(), result == null ? null : result.detail());
			return;
		}

		payCost(player, cost);
		float cd = ability.cooldownSeconds(level) * ModConfig.get().cooldownMultiplier;
		rt.startCooldown(abilityId, now, Math.round(cd * 20.0f));
		StateSync.sync(player);
	}

	// ---------------------------------------------------------------- cost

	private static ActivationResult checkCost(ServerPlayer player, Cost cost) {
		if (cost.item() != null) {
			ItemStack held = player.getMainHandItem();
			if (!held.is(cost.item())) {
				return ActivationResult.fail(ActivationResult.FailureReason.NO_ITEM,
						held.isEmpty() ? "item" : held.getHoverName().getString());
			}
		}
		if (cost.hunger() > 0 && player.getFoodData().getFoodLevel() < cost.hunger()) {
			return ActivationResult.fail(ActivationResult.FailureReason.NO_HUNGER, String.valueOf(cost.hunger()));
		}
		if (cost.health() > 0.0f && player.getHealth() - cost.health() < 1.0f) {
			return ActivationResult.fail(ActivationResult.FailureReason.NO_HEALTH, String.valueOf((int) cost.health()));
		}
		return ActivationResult.OK;
	}

	private static void payCost(ServerPlayer player, Cost cost) {
		if (cost.hunger() > 0) {
			player.getFoodData().setFoodLevel(Math.max(0, player.getFoodData().getFoodLevel() - cost.hunger()));
		}
		if (cost.health() > 0.0f) {
			player.hurtServer(player.level(), player.level().damageSources().magic(),
					Math.min(cost.health(), Math.max(0.0f, player.getHealth() - 1.0f)));
		}
		if (cost.item() != null) {
			player.getMainHandItem().shrink(1);
		}
	}

	// ------------------------------------------------------------ feedback

	public static void feedback(ServerPlayer player, ActivationResult.FailureReason reason, String detail) {
		Component message;
		if (detail != null) {
			message = Component.translatable(reason.messageKey, Component.literal(detail));
		} else {
			message = Component.translatable(reason.messageKey);
		}
		player.connection.send(new ClientboundSetActionBarTextPacket(message));
	}

	/** Gale Burst charge pool for this player, or 0 if not unlocked. */
	public static int windChargeMax(ServerPlayer player) {
		Ability gale = Abilities.get(dev.volunteera.content.SkyborneContent.GALE_BURST_ID);
		if (gale == null) {
			return 0;
		}
		int level = ProgressManager.effectiveLevel(player, gale);
		if (level <= 0) {
			return 0;
		}
		return ModConfig.get().skyborne.galeMaxCharges[level - 1];
	}
}
