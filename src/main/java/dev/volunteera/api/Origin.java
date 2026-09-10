package dev.volunteera.api;

import net.minecraft.resources.Identifier;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.jspecify.annotations.Nullable;

import net.minecraft.network.chat.Component;

import dev.volunteera.VolunteeraMod;

/**
 * A player's major power set: identity, attribute profile and an ordered list
 * of abilities. Six abilities may be bound to HUD hotkey slots.
 */
public final class Origin {
	private final Identifier id;
	private final int color;
	private final String loreKey;
	private final List<Ability> abilities;
	private final Identifier[] hudSlots;

	public Origin(Identifier id, int color, String loreKey, List<Ability> abilities) {
		this.id = id;
		this.color = color;
		this.loreKey = loreKey;
		this.abilities = List.copyOf(abilities);

		Identifier[] slots = new Identifier[6];
		for (Ability ability : this.abilities) {
			int slot = ability.hudSlot();
			if (slot >= 0 && slot < slots.length) {
				slots[slot] = ability.id();
			}
		}
		this.hudSlots = slots;
	}

	public Identifier id() {
		return id;
	}

	public String nameKey() {
		return "origin." + id.getNamespace() + "." + id.getPath();
	}

	public String descKey() {
		return nameKey() + ".desc";
	}

	public String loreKey() {
		return loreKey;
	}

	public int color() {
		return color;
	}

	public List<Ability> abilities() {
		return abilities;
	}

	@Nullable
	public Ability ability(Identifier abilityId) {
		for (Ability ability : abilities) {
			if (ability.id().equals(abilityId)) {
				return ability;
			}
		}
		return null;
	}

	/** Ability id bound to the given HUD slot, or null. */
	@Nullable
	public Identifier hudSlot(int index) {
		return index >= 0 && index < hudSlots.length ? hudSlots[index] : null;
	}

	public Component displayName() {
		return Component.translatable(nameKey());
	}

	public static Builder builder(String path, int color) {
		return new Builder(Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, path), color);
	}

	public static final class Builder {
		private final Identifier id;
		private final int color;
		private String loreKey;
		private final List<Ability> abilities = new ArrayList<>();

		private Builder(Identifier id, int color) {
			this.id = id;
			this.color = color;
			this.loreKey = "origin." + id.getNamespace() + "." + id.getPath() + ".lore";
		}

		public Builder lore(String key) {
			this.loreKey = key;
			return this;
		}

		public Builder ability(Ability ability) {
			this.abilities.add(ability);
			return this;
		}

		public Builder abilities(Collection<Ability> abilities) {
			this.abilities.addAll(abilities);
			return this;
		}

		public Origin build() {
			return new Origin(id, color, loreKey, abilities);
		}
	}
}
