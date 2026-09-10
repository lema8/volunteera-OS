package dev.volunteera.api;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

import org.jspecify.annotations.Nullable;

import dev.volunteera.VolunteeraMod;

/**
 * Registry of all abilities across all origins.
 */
public final class Abilities {
	private static final Map<Identifier, Ability> REGISTRY = new LinkedHashMap<>();

	private Abilities() {
	}

	public static void register(Ability ability) {
		if (REGISTRY.putIfAbsent(ability.id(), ability) != null) {
			VolunteeraMod.LOGGER.warn("Duplicate ability registration ignored: {}", ability.id());
			return;
		}
		if (ability.worldTrial() != null) {
			Trials.register(ability.worldTrial());
		}
	}

	@Nullable
	public static Ability get(@Nullable Identifier id) {
		return id == null ? null : REGISTRY.get(id);
	}

	public static Collection<Ability> all() {
		return Collections.unmodifiableCollection(REGISTRY.values());
	}

	public static int count() {
		return REGISTRY.size();
	}
}
