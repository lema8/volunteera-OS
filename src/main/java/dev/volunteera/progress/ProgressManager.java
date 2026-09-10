package dev.volunteera.progress;

import java.util.HashMap;
import java.util.Map;

import org.jspecify.annotations.Nullable;

import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.sounds.SoundSource;

import dev.volunteera.VolunteeraMod;
import dev.volunteera.api.Abilities;
import dev.volunteera.api.Ability;
import dev.volunteera.api.Origin;
import dev.volunteera.api.Origins;
import dev.volunteera.api.Trial;
import dev.volunteera.api.Trials;
import dev.volunteera.net.StateSync;
import dev.volunteera.server.OriginAttributes;
import dev.volunteera.runtime.RuntimeManager;

/**
 * Server-authoritative progression logic: origin switching, XP-based ability
 * leveling, trial completion and resets. All mutations funnel through here so
 * persistence + attribute/passive state + network sync stay consistent.
 */
public final class ProgressManager {
	private ProgressManager() {
	}

	// ------------------------------------------------------------ origin

	/** Current origin id of the player, or null. */
	@Nullable
	public static Identifier originOf(ServerPlayer player) {
		return ProgressData.get(player).origin();
	}

	public static boolean setOrigin(ServerPlayer player, @Nullable Identifier originId) {
		Origin origin = Origins.get(originId);
		if (origin == null && originId != null) {
			return false;
		}
		ProgressData data = ProgressData.get(player);
		Origin previous = Origins.get(data.origin());

		// Remove everything from the previous origin first.
		if (previous != null) {
			RuntimeManager.get(player).deactivateAllToggles(player, previous);
			OriginAttributes.clearOrigin(player);
			applyPassives(previous, player, 0, false);
		}

		player.setAttached(ProgressAttachments.PROGRESS, data.withOrigin(originId));

		if (origin != null) {
			applyPassives(origin, player, 0, false); // ensure clean slate
			applyPassives(origin, player, 0, true);
			OriginAttributes.applyOrigin(player, origin);
			player.sendSystemMessage(Component.translatable("message.volunteera.origin_switched", origin.displayName()));
		} else {
			player.sendSystemMessage(Component.translatable("message.volunteera.origin_switched", Origins.noneName()));
		}

		RuntimeManager.get(player).onOriginChanged();
		StateSync.sync(player);
		return true;
	}

	// ------------------------------------------------------- ability levels

	/**
	 * The effective level of an ability for this player: 0 = locked. World
	 * abilities return their level only if the trial was completed (or in
	 * test mode).
	 */
	public static int effectiveLevel(ServerPlayer player, Ability ability) {
		ProgressData data = ProgressData.get(player);
		if (data.testMode()) {
			return ability.maxLevel();
		}
		if (ability.isWorldAbility()) {
			return data.isWorldUnlocked(ability.id()) ? ability.maxLevel() : 0;
		}
		return data.abilityLevel(ability.id());
	}

	/** Normal stored level, ignoring test mode. */
	public static int storedLevel(ServerPlayer player, Identifier abilityId) {
		return ProgressData.get(player).abilityLevel(abilityId);
	}

	public static void setAbilityLevel(ServerPlayer player, Identifier abilityId, int level) {
		Ability ability = Abilities.get(abilityId);
		if (ability == null) {
			VolunteeraMod.LOGGER.warn("setAbilityLevel for unknown ability {}", abilityId);
			return;
		}
		int clamped = Math.max(0, Math.min(ability.maxLevel(), level));
		ProgressData data = ProgressData.get(player);
		player.setAttached(ProgressAttachments.PROGRESS, data.withAbilityLevel(abilityId, clamped));

		Origin origin = Origins.get(ProgressData.get(player).origin());
		if (origin != null && origin.ability(abilityId) != null) {
			if (ability.kind() == dev.volunteera.api.AbilityKind.PASSIVE || ability.kind() == dev.volunteera.api.AbilityKind.TOGGLE) {
				ability.applyPassives(player, Math.max(clamped, 0), false);
				if (clamped > 0) {
					ability.applyPassives(player, clamped, true);
				}
			}
			if (clamped > 0 && ability.kind() == dev.volunteera.api.AbilityKind.PASSIVE) {
				OriginAttributes.applyOrigin(player, origin); // refresh blended profiles
			}
		}
		StateSync.sync(player);
	}

	/**
	 * XP-based auto progression: promotes any ability whose threshold is met.
	 * Called once per second per player from the event hooks.
	 */
	public static void tickProgression(ServerPlayer player) {
		ProgressData data = ProgressData.get(player);
		if (data.testMode() || !dev.volunteera.config.ModConfig.get().autoProgression) {
			return;
		}
		Origin origin = Origins.get(data.origin());
		if (origin == null) {
			return;
		}
		int xp = player.experienceLevel;
		boolean changed = false;
		for (Ability ability : origin.abilities()) {
			if (ability.isWorldAbility()) {
				continue;
			}
			int current = data.abilityLevel(ability.id());
			int next = current + 1;
			if (next > ability.maxLevel()) {
				continue;
			}
			int threshold = ability.unlockXpFor(next);
			if (threshold < 0 || xp < threshold) {
				continue;
			}
			setAbilityLevel(player, ability.id(), next);
			if (dev.volunteera.server.VanillaLookup.SND_LEVELUP != null) {
				player.level().playSound(null, player.blockPosition(),
						dev.volunteera.server.VanillaLookup.SND_LEVELUP.value(), SoundSource.PLAYERS, 0.6f, 1.4f);
			}
			player.sendSystemMessage(Component.translatable(ability.nameKey())
					.append(Component.literal(" - Lv." + next)));
			changed = true;
		}
		if (changed) {
			StateSync.sync(player);
		}
	}

	// --------------------------------------------------------------- trials

	public static int trialProgress(ServerPlayer player, Identifier trialId) {
		return ProgressData.get(player).trialProgress(trialId);
	}

	/** Sets trial progress; completes the trial when reaching the target. */
	public static void setTrialProgress(ServerPlayer player, Trial trial, int progress) {
		ProgressData data = ProgressData.get(player);
		int clamped = Math.min(Math.max(0, progress), trial.maxProgress());
		if (clamped == data.trialProgress(trial.id())) {
			return;
		}
		player.setAttached(ProgressAttachments.PROGRESS, data.withTrialProgress(trial.id(), clamped));

		if (clamped >= trial.maxProgress()) {
			completeTrial(player, trial);
		} else if (clamped > 0 && clamped % Math.max(1, trial.maxProgress() / 3) == 0) {
			player.sendSystemMessage(Component.translatable("message.volunteera.trial_progress",
					Component.translatable(trial.nameKey()), clamped, trial.maxProgress()));
		}
	}

	/** Finds the ability gated by the trial and unlocks it. */
	private static void completeTrial(ServerPlayer player, Trial trial) {
		ProgressData data = ProgressData.get(player);
		Ability gated = null;
		for (Ability ability : Abilities.all()) {
			if (ability.worldTrial() != null && ability.worldTrial().id().equals(trial.id())) {
				gated = ability;
				break;
			}
		}
		if (gated == null) {
			VolunteeraMod.LOGGER.warn("Trial {} completed but no ability references it", trial.id());
			return;
		}
		if (data.isWorldUnlocked(gated.id())) {
			return;
		}
		player.setAttached(ProgressAttachments.PROGRESS, data.withWorldUnlocked(gated.id(), true));
		if (dev.volunteera.server.VanillaLookup.SND_ELDER != null) {
			player.level().playSound(null, player.blockPosition(),
					dev.volunteera.server.VanillaLookup.SND_ELDER.value(), SoundSource.PLAYERS, 0.5f, 1.6f);
		}
		player.sendSystemMessage(Component.translatable("message.volunteera.trial_complete",
				Component.translatable(gated.nameKey())));
		StateSync.sync(player);
	}

	// ---------------------------------------------------------------- misc

	private static void applyPassives(Origin origin, ServerPlayer player, int level, boolean apply) {
		for (Ability ability : origin.abilities()) {
			try {
				ability.applyPassives(player, level, apply);
			} catch (Exception e) {
				VolunteeraMod.LOGGER.error("applyPassives({}) failed for {} on {}", apply, ability.id(), player.getGameProfile().getName(), e);
			}
		}
	}

	/** Clears all progression. */
	public static void reset(ServerPlayer player) {
		Origin origin = Origins.get(ProgressData.get(player).origin());
		if (origin != null) {
			RuntimeManager.get(player).deactivateAllToggles(player, origin);
			OriginAttributes.clearOrigin(player);
			applyPassives(origin, player, 0, false);
		}
		player.removeAttached(ProgressAttachments.PROGRESS);
		RuntimeManager.get(player).onOriginChanged();
		StateSync.sync(player);
	}

	/** Unlocks every ability of the current origin at max level. */
	public static void unlockAll(ServerPlayer player) {
		ProgressData data = ProgressData.get(player);
		Origin origin = Origins.get(data.origin());
		Map<Identifier, Integer> levels = new HashMap<>(data.abilityLevels());
		Map<Identifier, Boolean> world = new HashMap<>(data.unlockedWorld());
		if (origin != null) {
			for (Ability ability : origin.abilities()) {
				if (ability.isWorldAbility()) {
					world.put(ability.id(), true);
				} else {
					levels.put(ability.id(), ability.maxLevel());
				}
			}
		}
		player.setAttached(ProgressAttachments.PROGRESS, new ProgressData(
				data.origin(), Map.copyOf(levels), data.trialProgress(), Map.copyOf(world), data.testMode()));
		if (origin != null) {
			applyPassives(origin, player, 0, false);
			applyPassives(origin, player, 0, true);
			OriginAttributes.applyOrigin(player, origin);
		}
		StateSync.sync(player);
	}
}
