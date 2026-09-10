package dev.volunteera.server;

import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;

import dev.volunteera.VolunteeraMod;
import dev.volunteera.api.Ability;
import dev.volunteera.api.Origin;
import dev.volunteera.api.Origins;
import dev.volunteera.config.ModConfig;
import dev.volunteera.content.DeepforgeContent;
import dev.volunteera.content.EmberbornContent;
import dev.volunteera.net.StateSync;
import dev.volunteera.progress.ProgressManager;
import dev.volunteera.runtime.PlayerRuntime;
import dev.volunteera.runtime.RuntimeManager;

/**
 * The 10-tick "slow pass" for passive behaviors: environmental reactions
 * (Emberborn dousing), tool-conditional modifiers (Deepforge Titan Swings)
 * and per-ability passive ticks. Deliberately cheap: one origin lookup and a
 * handful of per-ability calls, no world scans.
 */
public final class PassiveEngine {
	private PassiveEngine() {
	}

	public static void tick(ServerPlayer player, PlayerRuntime rt) {
		Origin origin = Origins.get(ProgressManager.originOf(player));

		if (origin != null && origin.id().getPath().equals("emberborn")) {
			tickEmberborn(player, rt, origin);
		}
		if (origin != null && origin.id().getPath().equals("deepforge")) {
			tickDeepforge(player, rt, origin);
		}

		if (origin != null) {
			for (Ability ability : origin.abilities()) {
				int level = ProgressManager.effectiveLevel(player, ability);
				if (level <= 0) {
					continue;
				}
				try {
					ability.passiveTick(player, level, rt);
				} catch (Exception e) {
					VolunteeraMod.LOGGER.error("passiveTick({}) failed", ability.id(), e);
				}
			}
		}

		if (rt.dousedTicks > 0) {
			rt.dousedTicks -= 10;
		}
	}

	private static void tickEmberborn(ServerPlayer player, PlayerRuntime rt, Origin origin) {
		boolean wet = player.isInWater() || (player.level().isRaining()
				&& player.level().canSeeSky(player.blockPosition().above()));
		if (!wet) {
			return;
		}
		boolean dousedAny = false;
		for (Identifier fireToggle : new Identifier[] {EmberbornContent.RADIANCE_ID, EmberbornContent.IMMOLATION_ID}) {
			Ability ability = origin.ability(fireToggle);
			if (ability != null && rt.activeToggles.contains(fireToggle)) {
				ToggleEngine.deactivate(player, rt, origin, ability,
						ProgressManager.effectiveLevel(player, ability), false);
				dousedAny = true;
			}
		}
		if (dousedAny) {
			rt.dousedTicks = ModConfig.get().dousedSeconds * 20;
			AbilityFx.actionbar(player, Component.translatable("message.volunteera.wet"));
			StateSync.sync(player);
		}
	}

	private static void tickDeepforge(ServerPlayer player, PlayerRuntime rt, Origin origin) {
		Ability titan = origin.ability(DeepforgeContent.TITAN_SWINGS_ID);
		if (titan == null) {
			return;
		}
		int level = ProgressManager.effectiveLevel(player, titan);
		var spec = DeepforgeContent.pickaxeSpec(level);
		if (spec != null) {
			OriginAttributes.ensureToolModifier(player, spec, player.getMainHandItem());
		}
	}
}
