package dev.volunteera.api;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

import javax.annotation.Nullable;

import net.minecraft.network.chat.Component;

import dev.volunteera.VolunteeraMod;

/**
 * Registry of all origins. Content packages register here during boot; HUD,
 * menu, commands and sync read from here. Adding an origin = register it.
 */
public final class Origins {
	private static final Map<Identifier, Origin> REGISTRY = new LinkedHashMap<>();

	private Origins() {
	}

	public static void register(Origin origin) {
		if (REGISTRY.putIfAbsent(origin.id(), origin) != null) {
			VolunteeraMod.LOGGER.warn("Duplicate origin registration ignored: {}", origin.id());
			return;
		}
		VolunteeraMod.LOGGER.info("Registered origin {} with {} abilities", origin.id(), origin.abilities().size());
	}

	@Nullable
	public static Origin get(@Nullable Identifier id) {
		return id == null ? null : REGISTRY.get(id);
	}

	@Nullable
	public static Origin byPath(String path) {
		return get(Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, path));
	}

	public static Collection<Origin> all() {
		return Collections.unmodifiableCollection(REGISTRY.values());
	}

	public static int count() {
		return REGISTRY.size();
	}

	public static Component noneName() {
		return Component.translatable("origin.volunteera.none");
	}
}
