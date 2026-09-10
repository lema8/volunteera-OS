package dev.volunteera.content;

import java.util.HashMap;
import java.util.Map;
import java.util.Optional;

import net.minecraft.core.BlockPos;
import net.minecraft.core.particles.ParticleTypes;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.sounds.SoundSource;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.ai.attributes.AttributeModifier;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.Vec3;

import dev.volunteera.VolunteeraMod;
import dev.volunteera.api.Abilities;
import dev.volunteera.api.Ability;
import dev.volunteera.api.ActivationResult;
import dev.volunteera.api.Cost;
import dev.volunteera.api.Origin;
import dev.volunteera.api.Origins;
import dev.volunteera.api.Trial;
import dev.volunteera.api.Trials;
import dev.volunteera.config.ModConfig;
import dev.volunteera.net.StateSync;
import dev.volunteera.progress.ProgressManager;
import dev.volunteera.runtime.PlayerRuntime;
import dev.volunteera.runtime.RuntimeManager;
import dev.volunteera.server.AbilityFx;
import dev.volunteera.server.OriginAttributes;
import dev.volunteera.server.VanillaLookup;

/**
 * EMBERNBORN - born of forge-fire and fury.
 *
 * <p>Identity: aggressive fire magic with real upkeep. Strong area control and
 * mobility, permanent fire immunity at higher investment - but water and rain
 * douse your flames, your best ability is trial-gated, and spam eats your
 * hunger bar alive.
 */
public final class EmberbornContent {
	public static final Identifier RADIANCE_ID = EmberbornContent.id("emberborn_radiance");
	public static final Identifier BURNING_GRIP_ID = EmberbornContent.id("emberborn_burning_grip");
	public static final Identifier FLAME_DASH_ID = EmberbornContent.id("emberborn_flame_dash");
	public static final Identifier CINDER_NOVA_ID = EmberbornContent.id("emberborn_cinder_nova");
	public static final Identifier IMMOLATION_ID = EmberbornContent.id("emberborn_immolation");
	public static final Identifier MAGMA_BLOOD_ID = EmberbornContent.id("emberborn_magma_blood");
	public static final Identifier KINDLED_MEALS_ID = EmberbornContent.id("emberborn_kindled_meals");
	public static final Identifier BLAZING_LEAP_ID = EmberbornContent.id("emberborn_blazing_leap");
	public static final Identifier PYRE_BALL_ID = EmberbornContent.id("emberborn_pyre_ball");
	public static final Identifier BURNOUT_ID = EmberbornContent.id("emberborn_burnout_curse");

	public static final Trial PYRE_TRIAL = new Trial(EmberbornContent.id("emberborn_pyre"), 15, Trial.Event.KILL);

	private EmberbornContent() {
	}

	private static Identifier id(String path) {
		return Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, path);
	}

	public static void register() {
		Origin origin = Origin.builder("emberborn", 0xFF7A2A)
				.ability(radiance())
				.ability(burningGrip())
				.ability(flameDash())
				.ability(cinderNova())
				.ability(immolation())
				.ability(magmaBlood())
				.ability(kindledMeals())
				.ability(blazingLeap())
				.ability(pyreBall())
				.ability(burnoutCurse())
				.build();

		// Emberborn profile: slightly tougher than baseline (the weakness is
		// water, not frailty).
		OriginAttributes.registerProfile(origin, java.util.List.of(
				new OriginAttributes.ModSpec(EmberbornContent.id("emberborn_vitality"), VanillaLookup.attrHolder("generic.max_health", "max_health"), 2.0,
						AttributeModifier.Operation.ADD_VALUE, null)));

		Origins.register(origin);
		for (Ability ability : origin.abilities()) {
			Abilities.register(ability);
		}
		Trials.registerHandler(PYRE_TRIAL, (context, current) ->
				context.entity() != null && context.entity().getType() == EntityType.BLAZE ? current + 1 : current);
	}

	private static boolean doused(ServerPlayer player) {
		return RuntimeManager.get(player).dousedTicks > 0;
	}

	// ------------------------------------------------------------- abilities

	/**
	 * RADIANCE - a personal ring of daylight: night vision, light aura and a
	 * zone where hostile mobs simply refuse to appear (via the spawn mixin).
	 * Drains food the whole time; water and rain end it instantly.
	 */
	private static Ability radiance() {
		return new Ability(Ability.toggle(RADIANCE_ID)
				.categories(dev.volunteera.api.AbilityCategory.UTILITY, dev.volunteera.api.AbilityCategory.SPECIAL)
				.maxLevel(3).unlockXp(0, 15, 30)
				.cooldown(2f, 2f, 2f)
				.cost(Cost.food(2), Cost.food(3), Cost.food(3))
				.hudSlot(0)) {
			@Override
			public ActivationResult checkEnvironment(ServerPlayer player, int level) {
				return doused(player) ? ActivationResult.fail(ActivationResult.FailureReason.DOUSED) : ActivationResult.OK;
			}

			@Override
			public ActivationResult activate(ServerPlayer player, int level) {
				PlayerRuntime rt = RuntimeManager.get(player);
				rt.radianceActive = true;
				rt.radianceRadius = ModConfig.get().emberborn.radianceRadii[level - 1];
				applyNightVision(player);
				if (player.level() instanceof ServerLevel sl) {
					AbilityFx.playSound(sl, player.position(), VanillaLookup.SND_LAVA_FILL, 0.8f, 1.2f);
					AbilityFx.ringParticles(sl, player.position(), rt.radianceRadius * 0.6, ParticleTypes.FLAME, 14);
				}
				return ActivationResult.OK;
			}

			@Override
			public void toggleTick(ServerPlayer player, int level) {
				PlayerRuntime rt = RuntimeManager.get(player);
				if (player.tickCount % 40 == 0) {
					applyNightVision(player);
				}
				if (player.tickCount % 20 == 0) {
					player.getFoodData().addExhaustion(ModConfig.get().emberborn.radianceDrain[level - 1] * 4.0f);
					if (player.getFoodData().getFoodLevel() <= 0) {
						// Out of fuel: end next tick via the toggle expiry path.
						rt.toggleEnds.put(RADIANCE_ID, player.level().getGameTime());
					}
					if (player.level() instanceof ServerLevel sl && player.tickCount % 60 == 0) {
						AbilityFx.ringParticles(sl, player.position(), rt.radianceRadius * 0.5, ParticleTypes.FLAME, 6);
					}
				}
			}

			@Override
			public void deactivate(ServerPlayer player, int level) {
				PlayerRuntime rt = RuntimeManager.get(player);
				rt.radianceActive = false;
				rt.radianceRadius = 0;
			}

			private void applyNightVision(ServerPlayer player) {
				if (VanillaLookup.NIGHT_VISION != null) {
					player.addEffect(new MobEffectInstance(VanillaLookup.NIGHT_VISION, 600, 0, true, false, true));
				}
			}
		};
	}

	/**
	 * BURNING GRIP - your melee hits set enemies on fire. Pure passive combat
	 * flavor implemented in the attack event hook.
	 */
	private static Ability burningGrip() {
		return new Ability(Ability.passive(BURNING_GRIP_ID)
				.categories(dev.volunteera.api.AbilityCategory.COMBAT)
				.maxLevel(3).unlockXp(0, 10, 20)
				.cooldown(0f)) {
		};
	}

	/**
	 * FLAME DASH - explosive forward burst. Cheap escape/gap-closer with a
	 * short cooldown; level ups add speed and a tail of speed II.
	 */
	private static Ability flameDash() {
		return new Ability(Ability.active(FLAME_DASH_ID)
				.categories(dev.volunteera.api.AbilityCategory.MOVEMENT, dev.volunteera.api.AbilityCategory.UTILITY)
				.maxLevel(3).unlockXp(5, 20, 40)
				.cooldown(8f, 7f, 6f)
				.cost(Cost.food(2), Cost.food(2), Cost.food(3))
				.hudSlot(1)) {
			@Override
			public ActivationResult checkEnvironment(ServerPlayer player, int level) {
				if (doused(player)) {
					return ActivationResult.fail(ActivationResult.FailureReason.DOUSED);
				}
				return ActivationResult.OK;
			}

			@Override
			public ActivationResult activate(ServerPlayer player, int level) {
				Vec3 look = player.getLookAngle();
				double speed = switch (level) {
					case 1 -> 1.1;
					case 2 -> 1.35;
					default -> 1.6;
				};
				Vec3 velocity = new Vec3(look.x * speed, Math.max(0.2, look.y * 0.5 + 0.25), look.z * speed);
				AbilityFx.setVelocity(player, velocity);
				player.fallDistance = 0.0f;
				if (level >= 3 && VanillaLookup.HASTE != null) {
					player.addEffect(new MobEffectInstance(VanillaLookup.HASTE, 60, 0, true, false, true));
				}
				if (player.level() instanceof ServerLevel sl) {
					AbilityFx.burstParticles(sl, player.position().add(0, 0.9, 0), ParticleTypes.FLAME, 24, 0.4);
					AbilityFx.playSound(sl, player.position(), VanillaLookup.SND_BLAZE, 0.9f, 1.0f);
				}
				return ActivationResult.OK;
			}
		};
	}

	/**
	 * CINDER NOVA - ground slam: a shockwave of heat that damages, knocks back
	 * and ignites everything around you. Costs food AND a bit of health.
	 */
	private static Ability cinderNova() {
		return new Ability(Ability.active(CINDER_NOVA_ID)
				.categories(dev.volunteera.api.AbilityCategory.COMBAT)
				.maxLevel(3).unlockXp(10, 25, 45)
				.cooldown(16f, 15f, 14f)
				.cost(Cost.foodHealth(4, 2), Cost.foodHealth(4, 2), Cost.foodHealth(5, 3))
				.hudSlot(2)) {
			@Override
			public ActivationResult checkEnvironment(ServerPlayer player, int level) {
				if (doused(player)) {
					return ActivationResult.fail(ActivationResult.FailureReason.DOUSED);
				}
				if (!player.onGround()) {
					return ActivationResult.fail(ActivationResult.FailureReason.NEEDS_GROUND);
				}
				return ActivationResult.OK;
			}

			@Override
			public ActivationResult activate(ServerPlayer player, int level) {
				double radius = switch (level) {
					case 1 -> 3.0;
					case 2 -> 3.5;
					default -> 4.0;
				};
				float damage = switch (level) {
					case 1 -> 5.0f;
					case 2 -> 7.0f;
					default -> 10.0f;
				};
				int ignite = switch (level) {
					case 1 -> 3;
					case 2 -> 4;
					default -> 5;
				};
				if (player.level() instanceof ServerLevel sl) {
					int hits = AbilityFx.areaAttack(player, sl, player.position(), radius,
							damage, 0.9 + level * 0.1, ignite);
					AbilityFx.ringParticles(sl, player.position(), radius, ParticleTypes.FLAME, 16);
					AbilityFx.playSound(sl, player.position(), VanillaLookup.SND_EXPLODE, 0.8f, 1.3f);
					if (hits == 0) {
						AbilityFx.actionbar(player,
								net.minecraft.network.chat.Component.literal("The heat disperses unanswered..."));
					}
				}
				return ActivationResult.OK;
			}
		};
	}

	/**
	 * IMMOLATION - a burning shroud: anyone who strikes you catches fire and
	 * nearby enemies slowly cook. Heavy food upkeep. Toggled.
	 */
	private static Ability immolation() {
		return new Ability(Ability.toggle(IMMOLATION_ID)
				.categories(dev.volunteera.api.AbilityCategory.COMBAT, dev.volunteera.api.AbilityCategory.UTILITY)
				.maxLevel(2).unlockXp(15, 35)
				.cooldown(3f, 3f)
				.cost(Cost.food(3), Cost.food(4))
				.hudSlot(3)) {
			@Override
			public ActivationResult checkEnvironment(ServerPlayer player, int level) {
				return doused(player) ? ActivationResult.fail(ActivationResult.FailureReason.DOUSED) : ActivationResult.OK;
			}

			@Override
			public ActivationResult activate(ServerPlayer player, int level) {
				if (player.level() instanceof ServerLevel sl) {
					AbilityFx.burstParticles(sl, player.position().add(0, 1, 0), ParticleTypes.FLAME, 30, 0.6);
					AbilityFx.playSound(sl, player.position(), VanillaLookup.SND_BLAZE, 1.0f, 0.7f);
				}
				return ActivationResult.OK;
			}

			@Override
			public void toggleTick(ServerPlayer player, int level) {
				if (player.tickCount % 40 == 0) {
					double radius = level >= 2 ? 2.5 : 2.0;
					if (player.level() instanceof ServerLevel sl) {
						AbilityFx.areaAttack(player, sl, player.position(), radius, 1.0f, 0.0, 2);
						AbilityFx.burstParticles(sl, player.position().add(0, 0.5, 0), ParticleTypes.FLAME, 6, 0.9);
					}
				}
				if (player.tickCount % 20 == 0) {
					player.getFoodData().addExhaustion(level >= 2 ? 4.0f : 3.0f);
					if (player.getFoodData().getFoodLevel() <= 0) {
						RuntimeManager.get(player).toggleEnds.put(IMMOLATION_ID, player.level().getGameTime());
					}
				}
			}
		};
	}

	/**
	 * MAGMA BLOOD - permanent fire immunity (L1); while on fire the flames
	 * quickly gutter out (L2). Pure passive.
	 */
	private static Ability magmaBlood() {
		return new Ability(Ability.passive(MAGMA_BLOOD_ID)
				.categories(dev.volunteera.api.AbilityCategory.UTILITY, dev.volunteera.api.AbilityCategory.SPECIAL)
				.maxLevel(2).unlockXp(0, 25)
				.cooldown(0f)) {
			@Override
			public void applyPassives(ServerPlayer player, int level, boolean apply) {
				if (VanillaLookup.FIRE_RESISTANCE == null) {
					return;
				}
				if (apply && level >= 1) {
					player.addEffect(new MobEffectInstance(VanillaLookup.FIRE_RESISTANCE, -1, 0, true, false, false));
				} else {
					player.removeEffect(VanillaLookup.FIRE_RESISTANCE);
				}
			}

			@Override
			public void passiveTick(ServerPlayer player, int level, RuntimeManager runtimes) {
				if (level >= 2 && player.getRemainingFireTicks() > 30) {
					player.setRemainingFireTicks(30);
				}
			}
		};
	}

	/**
	 * KINDLED MEALS - raw meat in your inventory slowly cooks itself with your
	 * body heat (faster at higher levels). Stalls while doused.
	 */
	private static Ability kindledMeals() {
		return new Ability(Ability.passive(KINDLED_MEALS_ID)
				.categories(dev.volunteera.api.AbilityCategory.UTILITY)
				.maxLevel(3).unlockXp(5, 15, 30)
				.cooldown(0f)) {
			private static final Map<String, Item> COOKED_CACHE = new HashMap<>();

			@Override
			public void passiveTick(ServerPlayer player, int level, RuntimeManager runtimes) {
				int interval = switch (level) {
					case 1 -> 60; // 3s
					case 2 -> 40; // 2s
					default -> 20; // 1s
				};
				if (player.tickCount % interval != 0 || RuntimeManager.get(player).dousedTicks > 0) {
					return;
				}
				var inventory = player.getInventory();
				for (int i = 0; i < inventory.getContainerSize(); i++) {
					ItemStack stack = inventory.getItem(i);
					if (stack.isEmpty()) {
						continue;
					}
					Identifier rawId = BuiltInRegistries.ITEM.getKey(stack.getItem());
					Item cooked = cookedVersionOf(rawId.getPath());
					if (cooked != null) {
						ItemStack cookedStack = new ItemStack(cooked, 1);
						stack.shrink(1);
						if (!player.getInventory().add(cookedStack)) {
							player.drop(cookedStack, false);
						}
						return; // one item per pulse
					}
				}
			}

			private Item cookedVersionOf(String rawPath) {
				String cookedPath = switch (rawPath) {
					case "beef" -> "cooked_beef";
					case "porkchop" -> "cooked_porkchop";
					case "chicken" -> "cooked_chicken";
					case "cod" -> "cooked_cod";
					case "salmon" -> "cooked_salmon";
					case "mutton" -> "cooked_mutton";
					case "rabbit" -> "cooked_rabbit";
					default -> null;
				};
				if (cookedPath == null) {
					return null;
				}
				return COOKED_CACHE.computeIfAbsent(cookedPath, path -> {
					Optional<? extends net.minecraft.core.Holder<Item>> holder =
							BuiltInRegistries.ITEM.get(Identifier.withDefaultNamespace(path));
					return holder.map(net.minecraft.core.Holder::value).orElse(null);
				});
			}
		};
	}

	/**
	 * BLAZING LEAP - a rocket jump over walls; no fall damage on the way down.
	 */
	private static Ability blazingLeap() {
		return new Ability(Ability.active(BLAZING_LEAP_ID)
				.categories(dev.volunteera.api.AbilityCategory.MOVEMENT)
				.maxLevel(2).unlockXp(10, 25)
				.cooldown(6f, 5f)
				.cost(Cost.food(2), Cost.food(3))
				.hudSlot(4)) {
			@Override
			public ActivationResult checkEnvironment(ServerPlayer player, int level) {
				if (doused(player)) {
					return ActivationResult.fail(ActivationResult.FailureReason.DOUSED);
				}
				if (!player.onGround()) {
					return ActivationResult.fail(ActivationResult.FailureReason.NEEDS_GROUND);
				}
				return ActivationResult.OK;
			}

			@Override
			public ActivationResult activate(ServerPlayer player, int level) {
				Vec3 look = player.getLookAngle();
				double up = level >= 2 ? 1.0 : 0.85;
				AbilityFx.setVelocity(player, new Vec3(look.x * 0.35, up, look.z * 0.35));
				player.fallDistance = 0.0f;
				if (level >= 2 && VanillaLookup.SLOW_FALLING != null) {
					player.addEffect(new MobEffectInstance(VanillaLookup.SLOW_FALLING, 100, 0, true, false, true));
				}
				if (player.level() instanceof ServerLevel sl) {
					AbilityFx.burstParticles(sl, player.position(), ParticleTypes.FLAME, 18, 0.3);
					AbilityFx.playSound(sl, player.position(), VanillaLookup.SND_BLAZE, 0.8f, 1.4f);
				}
				return ActivationResult.OK;
			}
		};
	}

	/**
	 * PYRE BALL (World Ability) - a roaring sphere of fire that explodes on
	 * impact. Unlocked by slaying 15 blazes; upgraded with XP afterwards.
	 * Level 3 doubles the blast. Respects the mobGriefing game rule.
	 */
	private static Ability pyreBall() {
		return new Ability(Ability.active(PYRE_BALL_ID)
				.categories(dev.volunteera.api.AbilityCategory.COMBAT)
				.maxLevel(3).unlockXp(-1, 35, 60)
				.cooldown(30f, 26f, 22f)
				.cost(Cost.foodHealth(5, 3), Cost.foodHealth(5, 4), Cost.foodHealth(6, 5))
				.hudSlot(5)
				.worldTrial(PYRE_TRIAL)) {
			@Override
			public ActivationResult checkEnvironment(ServerPlayer player, int level) {
				return doused(player) ? ActivationResult.fail(ActivationResult.FailureReason.DOUSED) : ActivationResult.OK;
			}

			@Override
			public ActivationResult activate(ServerPlayer player, int level) {
				if (!(player.level() instanceof ServerLevel sl)) {
					return ActivationResult.OK;
				}
				Vec3 look = player.getLookAngle().normalize();
				Vec3 spawn = player.getEyePosition().add(look.x, look.y - 0.1, look.z);
				int power = level >= 3 ? 2 : 1;
				net.minecraft.world.entity.projectile.hurtingprojectile.LargeFireball fireball =
						new net.minecraft.world.entity.projectile.hurtingprojectile.LargeFireball(
								sl, player, look.scale(0.9), power);
				fireball.setPos(spawn.x, spawn.y, spawn.z);
				sl.addFreshEntity(fireball);
				AbilityFx.playSound(sl, spawn, VanillaLookup.SND_BLAZE, 1.2f, 0.6f);
				AbilityFx.burstParticles(sl, spawn, ParticleTypes.FLAME, 20, 0.5);
				return ActivationResult.OK;
			}
		};
	}

	/**
	 * BURNOUT - the Emberborn weakness. Water and rain douse your toggles and
	 * increase ability costs for a while (implemented by the passive engine
	 * and the ability engine's douse penalty).
	 */
	private static Ability burnoutCurse() {
		return new Ability(Ability.passive(BURNOUT_ID)
				.categories(dev.volunteera.api.AbilityCategory.SPECIAL)
				.maxLevel(1).unlockXp(0)
				.cooldown(0f)) {
		};
	}
}
