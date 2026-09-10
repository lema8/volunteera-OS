package dev.volunteera.api;

import java.util.Locale;

import net.minecraft.world.item.Item;
import javax.annotation.Nullable;

/**
 * A resource cost for using an ability. Uses Minecraft's real systems:
 * hunger (food points on the {@code FoodData} bar), health (half-hearts,
 * applied with a non-lethal magic source) and optionally one item from the
 * main hand.
 *
 * @param hunger food points consumed (2 = one shank)
 * @param health half-hearts consumed
 * @param item   item consumed from the main hand, or null
 */
public record Cost(int hunger, float health, @Nullable Item item) {
	public static final Cost NONE = new Cost(0, 0.0f, null);

	public static Cost food(int hunger) {
		return new Cost(hunger, 0.0f, null);
	}

	public static Cost foodHealth(int hunger, float health) {
		return new Cost(hunger, health, null);
	}

	public boolean isEmpty() {
		return hunger <= 0 && health <= 0.0f && item == null;
	}

	public Cost scaled(float hungerMultiplier) {
		if (hunger <= 0 || hungerMultiplier == 1.0f) {
			return this;
		}
		int scaled = Math.round(hunger * hungerMultiplier);
		return new Cost(Math.min(scaled, 20), health, item);
	}

	public String describe() {
		StringBuilder sb = new StringBuilder();
		if (hunger > 0) {
			sb.append(hunger).append(" hunger");
		}
		if (health > 0.0f) {
			if (sb.length() > 0) {
				sb.append(" + ");
			}
			sb.append(trim(health)).append(" HP");
		}
		if (item != null) {
			if (sb.length() > 0) {
				sb.append(" + ");
			}
			sb.append("1 ").append(item).append(" (1 ").append(item.toString().toLowerCase(Locale.ROOT)).append(")");
		}
		return sb.isEmpty() ? "free" : sb.toString();
	}

	private static String trim(float f) {
		return f == Math.floor(f) ? String.valueOf((int) f) : String.valueOf(f);
	}
}
