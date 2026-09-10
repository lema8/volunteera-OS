package dev.volunteera.progress;

import java.util.HashMap;
import java.util.Map;
import java.util.Optional;

import com.mojang.serialization.Codec;
import com.mojang.serialization.codecs.RecordCodecBuilder;

import net.minecraft.resources.Identifier;

import dev.volunteera.VolunteeraMod;

/**
 * Persistent player progression, stored as a Fabric data attachment on the
 * {@code ServerPlayer}: chosen origin, ability levels, trial progress and the
 * operator test-mode flag.
 *
 * <p>The record is immutable; mutations produce copies via {@link #with} helpers.
 */
public record ProgressData(
		Identifier origin,
		Map<Identifier, Integer> abilityLevels,
		Map<Identifier, Integer> trialProgress,
		Map<Identifier, Boolean> unlockedWorld,
		boolean testMode) {

	public static final ProgressData DEFAULT = new ProgressData(
			null, Map.of(), Map.of(), Map.of(), false);

	public static final Codec<ProgressData> CODEC = RecordCodecBuilder.create(instance -> instance.group(
			Identifier.CODEC.optionalFieldOf("origin", null).forGetter(ProgressData::origin),
			Codec.unboundedMap(Identifier.CODEC, Codec.INT).optionalFieldOf("ability_levels", Map.of()).forGetter(ProgressData::abilityLevels),
			Codec.unboundedMap(Identifier.CODEC, Codec.INT).optionalFieldOf("trial_progress", Map.of()).forGetter(ProgressData::trialProgress),
			Codec.unboundedMap(Identifier.CODEC, Codec.BOOL).optionalFieldOf("unlocked_world", Map.of()).forGetter(ProgressData::unlockedWorld),
			Codec.BOOL.optionalFieldOf("test_mode", false).forGetter(ProgressData::testMode))
			.apply(instance, (origin, levels, trials, world, testMode) ->
					new ProgressData(origin.orElse(null), levels, trials, world, testMode)));

	public static ProgressData get(net.minecraft.server.level.ServerPlayer player) {
		ProgressData data = player.getAttached(ProgressAttachments.PROGRESS);
		return data == null ? DEFAULT : data;
	}

	public static void set(net.minecraft.server.level.ServerPlayer player, ProgressData data) {
		player.setAttached(ProgressAttachments.PROGRESS, data);
	}

	public ProgressData withOrigin(Identifier origin) {
		return new ProgressData(origin, abilityLevels, trialProgress, unlockedWorld, testMode);
	}

	public ProgressData withAbilityLevel(Identifier ability, int level) {
		Map<Identifier, Integer> copy = new HashMap<>(abilityLevels);
		if (level <= 0) {
			copy.remove(ability);
		} else {
			copy.put(ability, level);
		}
		return new ProgressData(origin, Map.copyOf(copy), trialProgress, unlockedWorld, testMode);
	}

	public ProgressData withTrialProgress(Identifier trial, int progress) {
		Map<Identifier, Integer> copy = new HashMap<>(trialProgress);
		copy.put(trial, progress);
		return new ProgressData(origin, abilityLevels, Map.copyOf(copy), unlockedWorld, testMode);
	}

	public ProgressData withWorldUnlocked(Identifier ability, boolean unlocked) {
		Map<Identifier, Boolean> copy = new HashMap<>(unlockedWorld);
		copy.put(ability, unlocked);
		return new ProgressData(origin, abilityLevels, trialProgress, Map.copyOf(copy), testMode);
	}

	public ProgressData withTestMode(boolean testMode) {
		return new ProgressData(origin, abilityLevels, trialProgress, unlockedWorld, testMode);
	}

	public int abilityLevel(Identifier ability) {
		return abilityLevels.getOrDefault(ability, 0);
	}

	public int trialProgress(Identifier trial) {
		return trialProgress.getOrDefault(trial, 0);
	}

	public boolean isWorldUnlocked(Identifier ability) {
		return unlockedWorld.getOrDefault(ability, false);
	}
}
