package dev.volunteera.server;

import java.util.Optional;

import net.minecraft.core.Holder;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.sounds.SoundEvent;
import net.minecraft.world.effect.MobEffect;
import net.minecraft.world.entity.ai.attributes.Attribute;

import dev.volunteera.VolunteeraMod;
import org.jspecify.annotations.Nullable;

/**
 * Resolves vanilla attribute and effect holders through the stable registries
 * instead of relying on constant names that occasionally get renamed between
 * Minecraft versions. Anything missing is logged loudly at boot and the
 * related modifier degrades gracefully instead of crashing the game.
 */
public final class VanillaLookup {
	@Nullable
	public static Holder<Attribute> SCALE;
	@Nullable
	public static Holder<Attribute> BLOCK_BREAK_SPEED;
	@Nullable
	public static Holder<Attribute> ATTACK_KNOCKBACK;
	@Nullable
	public static Holder<Attribute> ARROW_DAMAGE;
	@Nullable
	public static Holder<Attribute> BLOCK_INTERACTION_RANGE;

	@Nullable
	public static Holder<MobEffect> NIGHT_VISION;
	@Nullable
	public static Holder<MobEffect> FIRE_RESISTANCE;
	@Nullable
	public static Holder<MobEffect> SLOW_FALLING;
	@Nullable
	public static Holder<MobEffect> RESISTANCE;
	@Nullable
	public static Holder<MobEffect> SLOWNESS;
	@Nullable
	public static Holder<MobEffect> WEAKNESS;
	@Nullable
	public static Holder<MobEffect> HASTE;
	@Nullable
	public static Holder<MobEffect> JUMP_BOOST;

	@Nullable
	public static Holder<SoundEvent> SND_LEVELUP;
	@Nullable
	public static Holder<SoundEvent> SND_XP;
	@Nullable
	public static Holder<SoundEvent> SND_BLAZE;
	@Nullable
	public static Holder<SoundEvent> SND_EXPLODE;
	@Nullable
	public static Holder<SoundEvent> SND_EXTINGUISH;
	@Nullable
	public static Holder<SoundEvent> SND_LAVA_FILL;
	@Nullable
	public static Holder<SoundEvent> SND_ANVIL;
	@Nullable
	public static Holder<SoundEvent> SND_STONE_PLACE;
	@Nullable
	public static Holder<SoundEvent> SND_STONE_BREAK;
	@Nullable
	public static Holder<SoundEvent> SND_PING;
	@Nullable
	public static Holder<SoundEvent> SND_ELDER;
	@Nullable
	public static Holder<SoundEvent> SND_LIGHTNING;

	private VanillaLookup() {
	}

	public static void init() {
		SCALE = attribute("generic.scale", "scale");
		BLOCK_BREAK_SPEED = attribute("player.block_break_speed", "generic.block_break_speed", "block_break_speed");
		ATTACK_KNOCKBACK = attribute("generic.attack_knockback", "attack_knockback");
		ARROW_DAMAGE = attribute("generic.arrow_damage", "arrow_damage");
		BLOCK_INTERACTION_RANGE = attribute("player.block_interaction_range", "generic.block_interaction_range", "block_interaction_range");

		NIGHT_VISION = effect("night_vision");
		FIRE_RESISTANCE = effect("fire_resistance");
		SLOW_FALLING = effect("slow_falling");
		RESISTANCE = effect("resistance");
		SLOWNESS = effect("slowness");
		WEAKNESS = effect("weakness");
		HASTE = effect("haste");
		JUMP_BOOST = effect("jump_boost");

		SND_LEVELUP = sound("player.levelup");
		SND_XP = sound("entity.experience_orb.pickup");
		SND_BLAZE = sound("entity.blaze.shoot");
		SND_EXPLODE = sound("entity.generic.explode");
		SND_EXTINGUISH = sound("block.fire.extinguish");
		SND_LAVA_FILL = sound("item.bucket.fill_lava");
		SND_ANVIL = sound("block.anvil.use");
		SND_STONE_PLACE = sound("block.stone.place");
		SND_STONE_BREAK = sound("block.stone.break");
		SND_PING = sound("item.bottle.fill");
		SND_ELDER = sound("entity.wither.spawn");
		SND_LIGHTNING = sound("entity.lightning_bolt.thunder");
	}

	@Nullable
	private static Holder<Attribute> attribute(String... candidates) {
		return attrHolder(candidates);
	}

	/** Public lookup used by content classes for profile attributes. */
	@Nullable
	public static Holder<Attribute> attrHolder(String... candidates) {
		for (String candidate : candidates) {
			Optional<? extends Holder<Attribute>> holder =
					BuiltInRegistries.ATTRIBUTE.get(Identifier.withDefaultNamespace(candidate));
			if (holder.isPresent()) {
				return holder.get();
			}
		}
		VolunteeraMod.LOGGER.warn("Attribute not found in 26.2 registry (tried {}); related modifiers disabled.",
				String.join(", ", candidates));
		return null;
	}

	@Nullable
	private static Holder<SoundEvent> sound(String name) {
		Optional<? extends Holder<SoundEvent>> holder =
				BuiltInRegistries.SOUND_EVENT.get(Identifier.withDefaultNamespace(name));
		if (holder.isEmpty()) {
			VolunteeraMod.LOGGER.warn("Sound '{}' not found in registry; using silence.", name);
			return null;
		}
		return holder.get();
	}

	@Nullable
	private static Holder<MobEffect> effect(String name) {
		Optional<? extends Holder<MobEffect>> holder =
				BuiltInRegistries.MOB_EFFECT.get(Identifier.withDefaultNamespace(name));
		if (holder.isEmpty()) {
			VolunteeraMod.LOGGER.warn("Mob effect '{}' not found in 26.2 registry; related modifiers disabled.", name);
			return null;
		}
		return holder.get();
	}
}
