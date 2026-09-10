package dev.volunteera.server;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.jspecify.annotations.Nullable;

import net.minecraft.core.Holder;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.ai.attributes.Attribute;
import net.minecraft.world.entity.ai.attributes.AttributeInstance;
import net.minecraft.tags.TagKey;
import net.minecraft.world.entity.ai.attributes.AttributeModifier;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;

import dev.volunteera.VolunteeraMod;
import dev.volunteera.api.Origin;

/**
 * Manages transient attribute modifiers for origin profiles and ability
 * passives. All modifiers are transient (not saved) and re-applied on login /
 * origin switch / level change, so stale data can never accumulate.
 *
 * <p>Also supports conditional modifiers that depend on the held item
 * (e.g. Deepforge Titan Swings being stronger with a pickaxe).
 */
public final class OriginAttributes {
	/** One attribute change: attribute holder, amount, operation, optional held-item condition. */
	public record ModSpec(Identifier modId, Holder<Attribute> attribute, double amount,
			AttributeModifier.Operation operation, @Nullable TagKey<Item> toolCondition) {
		public static ModSpec of(Identifier modId, Holder<Attribute> attribute, double amount, AttributeModifier.Operation op) {
			return new ModSpec(modId, attribute, amount, op, null);
		}
	}

	private static final Map<Identifier, List<ModSpec>> ORIGIN_PROFILES = new HashMap<>();

	private OriginAttributes() {
	}

	/** Content classes register their origin attribute profile here. */
	public static void registerProfile(Origin origin, List<ModSpec> specs) {
		ORIGIN_PROFILES.put(origin.id(), List.copyOf(specs));
	}

	public static void init() {
		// Nothing dynamic yet; kept for symmetric boot ordering.
	}

	public static void applyOrigin(ServerPlayer player, Origin origin) {
		List<ModSpec> specs = ORIGIN_PROFILES.get(origin.id());
		if (specs == null) {
			return;
		}
		for (ModSpec spec : specs) {
			if (spec.toolCondition() != null) {
				continue; // conditional specs are handled per-tick by PassiveEngine
			}
			apply(player, spec, true);
		}
	}

	public static void clearOrigin(ServerPlayer player) {
		for (List<ModSpec> specs : ORIGIN_PROFILES.values()) {
			for (ModSpec spec : specs) {
				if (spec.toolCondition() == null) {
					remove(player, spec.modId(), spec.attribute());
				}
			}
		}
	}

	/** Idempotent apply/remove of a single modifier. */
	public static void apply(ServerPlayer player, ModSpec spec, boolean apply) {
		if (spec.attribute() == null) {
			return;
		}
		AttributeInstance instance = player.getAttribute(spec.attribute());
		if (instance == null) {
			return;
		}
		if (!apply) {
			instance.removeModifier(spec.modId());
			return;
		}
		// Replace (not accumulate) if already present.
		instance.removeModifier(spec.modId());
		instance.addTransientModifier(new AttributeModifier(spec.modId(), spec.amount(), spec.operation()));
	}

	public static void remove(ServerPlayer player, Identifier modId, Holder<Attribute> attribute) {
		if (attribute == null) {
			return;
		}
		AttributeInstance instance = player.getAttribute(attribute);
		if (instance != null) {
			instance.removeModifier(modId);
		}
	}

	/** Helper for conditional tool-bound modifiers, run from the 10-tick pass. */
	public static void ensureToolModifier(ServerPlayer player, ModSpec spec, ItemStack heldTool) {
		if (spec.attribute() == null) {
			return;
		}
		AttributeInstance instance = player.getAttribute(spec.attribute());
		if (instance == null) {
			return;
		}
		boolean shouldApply = spec.toolCondition() != null && heldTool.is(spec.toolCondition());
		boolean present = instance.hasModifier(spec.modId());
		if (shouldApply && !present) {
			instance.addTransientModifier(new AttributeModifier(spec.modId(), spec.amount(), spec.operation()));
		} else if (!shouldApply && present) {
			instance.removeModifier(spec.modId());
		}
	}

	/** Removes a conditional modifier regardless of the held item. */
	public static void removeToolModifier(ServerPlayer player, ModSpec spec) {
		remove(player, spec.modId(), spec.attribute());
	}

	/** Convenience factory for passives. */
	public static ModSpec mod(String path, Holder<Attribute> attribute, double amount, AttributeModifier.Operation op) {
		return new ModSpec(Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, path), attribute, amount, op, null);
	}

	public static ModSpec toolMod(String path, Holder<Attribute> attribute, double amount,
			AttributeModifier.Operation op, TagKey<Item> tool) {
		return new ModSpec(Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, path), attribute, amount, op, tool);
	}

	public static List<ModSpec> specsOf(Origin origin) {
		return ORIGIN_PROFILES.getOrDefault(origin.id(), new ArrayList<>());
	}
}
