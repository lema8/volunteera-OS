package dev.volunteera.api;

import java.util.Locale;

/**
 * Gameplay category of an ability, used for UI grouping and documentation.
 * An ability can belong to several categories.
 */
public enum AbilityCategory {
	COMBAT,
	UTILITY,
	MOVEMENT,
	SPECIAL;

	public String displayKey() {
		return "abilitycategory.volunteera." + name().toLowerCase(Locale.ROOT);
	}
}
