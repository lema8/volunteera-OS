package dev.volunteera.content;

import net.minecraft.core.particles.ParticleTypes;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.entity.ai.attributes.AttributeModifier;
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
import dev.volunteera.runtime.FlightController;
import dev.volunteera.runtime.PlayerRuntime;
import dev.volunteera.runtime.RuntimeManager;
import dev.volunteera.server.AbilityFx;
import dev.volunteera.server.OriginAttributes;
import dev.volunteera.server.VanillaLookup;

/**
 * SKYBORNE - child of the open sky.
 *
 * <p>Identity: unmatched mobility. Limited but renewable free flight (energy
 * meter on the HUD), wind bursts, glide, and a devastating trial-gated dive
 * attack. Fragile (low HP) and burns through food quickly - the sky gives,
 * the ground collects.
 */
public final class SkyborneContent {
	public static final Identifier TAILWIND_ID = SkyborneContent.id("skyborne_tailwind");
	public static final Identifier ASCENSION_ID = SkyborneContent.id("skyborne_ascension");
	public static final Identifier GALE_BURST_ID = SkyborneContent.id("skyborne_gale_burst");
	public static final Identifier THERMAL_CURRENTS_ID = SkyborneContent.id("skyborne_thermal_currents");
	public static final Identifier GLIDE_ID = SkyborneContent.id("skyborne_glide");
	public static final Identifier TEMPEST_DIVE_ID = SkyborneContent.id("skyborne_tempest_dive");
	public static final Identifier SKY_MARKSMANSHIP_ID = SkyborneContent.id("skyborne_sky_marksmanship");
	public static final Identifier LIGHT_FRAME_ID = SkyborneContent.id("skyborne_skyborne_reserves");
	public static final Identifier GROUNDING_BARBS_ID = SkyborneContent.id("skyborne_grounding_barbs");
	public static final Identifier WINDBORNE_GRACE_ID = SkyborneContent.id("skyborne_windborne_grace");

	public static final Trial TEMPEST_TRIAL = new Trial(SkyborneContent.id("skyborne_tempest"), 2000, Trial.Event.GLIDE);

	private SkyborneContent() {
	}

	private static Identifier id(String path) {
		return Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, path);
	}

	public static void register() {
		Origin origin = Origin.builder("skyborne", 0x7EC8FF)
				.ability(tailwind())
				.ability(ascension())
				.ability(galeBurst())
				.ability(thermalCurrents())
				.ability(glide())
				.ability(tempestDive())
				.ability(skyMarksmanship())
				.ability(lightFrame())
				.ability(groundingBarbs())
				.ability(windborneGrace())
				.build();

		// Profile: fragile frame, quick feet.
		OriginAttributes.registerProfile(origin, java.util.List.of(
				new OriginAttributes.ModSpec(SkyborneContent.id("skyborne_frame"), VanillaLookup.attrHolder("generic.max_health", "max_health"), -2.0,
						AttributeModifier.Operation.ADD_VALUE, null),
				new OriginAttributes.ModSpec(SkyborneContent.id("skyborne_grace"), VanillaLookup.attrHolder("generic.movement_speed", "movement_speed"), 0.02,
						AttributeModifier.Operation.ADD_VALUE, null)));

		Origins.register(origin);
		for (Ability ability : origin.abilities()) {
			Abilities.register(ability);
		}
		// Trial of the Tempest: glide/elytra distance, accumulated in runtime.
		Trials.registerHandler(TEMPEST_TRIAL, (context, current) ->
				current + (int) RuntimeManager.get(context.player()).glideAccum);
	}

	// ------------------------------------------------------------- abilities

	/** TAILWIND - permanent ground speed. Simple, honest, always useful. */
	private static Ability tailwind() {
		return new Ability(Ability.passive(TAILWIND_ID)
				.categories(dev.volunteera.api.AbilityCategory.MOVEMENT)
				.maxLevel(3).unlockXp(0, 10, 25)
				.cooldown(0f)) {
			@Override
			public void applyPassives(ServerPlayer player, int level, boolean apply) {
				OriginAttributes.apply(player, tailwindSpec(level), apply);
			}

			private OriginAttributes.ModSpec tailwindSpec(int level) {
				double amount = switch (level) {
					case 1 -> 0.03;
					case 2 -> 0.05;
					default -> 0.07;
				};
				return new OriginAttributes.ModSpec(SkyborneContent.id("skyborne_tailwind_mod"),
						VanillaLookup.attrHolder("generic.movement_speed", "movement_speed"), amount,
						AttributeModifier.Operation.ADD_VALUE, null);
			}
		};
	}

	/**
	 * ASCENSION - spread wings of wind and fly freely, no rockets. Energy
	 * drains while airborne and only regenerates on the ground (see
	 * FlightController, which owns the vanilla mayfly grant). Touching water
	 * folds your wings.
	 */
	private static Ability ascension() {
		return new Ability(Ability.toggle(ASCENSION_ID)
				.categories(dev.volunteera.api.AbilityCategory.MOVEMENT, dev.volunteera.api.AbilityCategory.SPECIAL)
				.maxLevel(1).unlockXp(0)
				.cooldown(3f)
				.cost(Cost.food(2))
				.hudSlot(0)) {
			@Override
			public ActivationResult activate(ServerPlayer player, int level) {
				PlayerRuntime rt = RuntimeManager.get(player);
				if (rt.flightEnergy < 10.0f) {
					return ActivationResult.fail(ActivationResult.FailureReason.NO_ENERGY);
				}
				FlightController.grantMayFly(player, rt);
				StateSync.sync(player);
				return ActivationResult.OK;
			}

			@Override
			public void toggleTick(ServerPlayer player, int level) {
				PlayerRuntime rt = RuntimeManager.get(player);
				if (player.isInWater() || player.isEyeInFluid(net.minecraft.tags.FluidTags.WATER)) {
					endNextTick(rt, player);
					return;
				}
				if (!rt.mayFlyGranted && !player.getAbilities().flying) {
					// Energy ran out (FlightController revoked the grant).
					endNextTick(rt, player);
				}
			}

			private void endNextTick(PlayerRuntime rt, ServerPlayer player) {
				rt.toggleEnds.put(ASCENSION_ID, player.level().getGameTime());
			}

			@Override
			public void deactivate(ServerPlayer player, int level) {
				PlayerRuntime rt = RuntimeManager.get(player);
				FlightController.revokeMayFly(player, rt);
				StateSync.sync(player);
			}
		};
	}

	/**
	 * GALE BURST - stored wind. Charges build only while standing on the
	 * ground; each burst hurls you forward and shoves nearby enemies.
	 */
	private static Ability galeBurst() {
		return new Ability(Ability.active(GALE_BURST_ID)
				.categories(dev.volunteera.api.AbilityCategory.MOVEMENT, dev.volunteera.api.AbilityCategory.COMBAT)
				.maxLevel(3).unlockXp(10, 25, 45)
				.cooldown(1f, 1f, 1f)
				.cost(Cost.NONE)
				.hudSlot(1)) {
			@Override
			public ActivationResult checkEnvironment(ServerPlayer player, int level) {
				PlayerRuntime rt = RuntimeManager.get(player);
				if (rt.windCharges <= 0) {
					return ActivationResult.fail(ActivationResult.FailureReason.NO_CHARGES);
				}
				return ActivationResult.OK;
			}

			@Override
			public ActivationResult activate(ServerPlayer player, int level) {
				PlayerRuntime rt = RuntimeManager.get(player);
				rt.windCharges--;
				Vec3 look = player.getLookAngle();
				double speed = 1.2 + level * 0.15;
				AbilityFx.setVelocity(player, new Vec3(look.x * speed, Math.max(0.3, look.y * 0.6 + 0.3), look.z * speed));
				player.resetFallDistance();
				if (player.level() instanceof ServerLevel sl) {
					AbilityFx.areaAttack(player, sl, player.position(), 2.0, 1.0f + level, 0.8, 0);
					AbilityFx.burstParticles(sl, player.position(), ParticleTypes.POOF, 20, 0.5);
					AbilityFx.playSound(sl, player.position(), VanillaLookup.SND_PING, 1.0f, 0.6f);
				}
				StateSync.sync(player);
				return ActivationResult.OK;
			}

			@Override
			public void passiveTick(ServerPlayer player, int level, RuntimeManager runtimes) {
				PlayerRuntime rt = runtimes.get(player);
				int max = ModConfig.get().skyborne.galeMaxCharges[level - 1];
				if (rt.windCharges >= max) {
					rt.chargeRefillTicks = 0;
					return;
				}
				if (player.onGround()) {
					rt.chargeRefillTicks += 10;
					int needed = ModConfig.get().skyborne.galeRefillSeconds[level - 1] * 20;
					if (rt.chargeRefillTicks >= needed) {
						rt.windCharges++;
						rt.chargeRefillTicks = 0;
						StateSync.sync(player);
					}
				} else {
					rt.chargeRefillTicks = 0;
				}
			}
		};
	}

	/**
	 * THERMAL CURRENTS - above heat blocks (magma, fire, campfires, lava)
	 * your flight barely drains. Passively read by the FlightController.
	 */
	private static Ability thermalCurrents() {
		return new Ability(Ability.passive(THERMAL_CURRENTS_ID)
				.categories(dev.volunteera.api.AbilityCategory.MOVEMENT, dev.volunteera.api.AbilityCategory.SPECIAL)
				.maxLevel(1).unlockXp(20)
				.cooldown(0f)) {
		};
	}

	/**
	 * GLIDE - toggle: while airborne you fall like a leaf (slow falling).
	 * Level 2 adds gentle forward drift while gliding. Small food upkeep.
	 */
	private static Ability glide() {
		return new Ability(Ability.toggle(GLIDE_ID)
				.categories(dev.volunteera.api.AbilityCategory.MOVEMENT)
				.maxLevel(2).unlockXp(5, 20)
				.cooldown(2f, 2f)
				.cost(Cost.food(1), Cost.food(2))
				.hudSlot(2)) {
			@Override
			public ActivationResult activate(ServerPlayer player, int level) {
				RuntimeManager.get(player).gliding = true;
				return ActivationResult.OK;
			}

			@Override
			public void toggleTick(ServerPlayer player, int level) {
				PlayerRuntime rt = RuntimeManager.get(player);
				boolean airborne = !player.onGround();
				rt.gliding = airborne;
				if (airborne && VanillaLookup.SLOW_FALLING != null
						&& player.getDeltaMovement().y < -0.1
						&& !player.isFallFlying()) {
					player.addEffect(new MobEffectInstance(VanillaLookup.SLOW_FALLING, 60, 0, true, false, true));
					if (level >= 2 && player.tickCount % 10 == 0) {
						Vec3 look = player.getLookAngle();
						Vec3 drift = new Vec3(look.x * 0.08, 0, look.z * 0.08);
						player.setDeltaMovement(player.getDeltaMovement().add(drift));
					}
				}
				if (player.tickCount % 20 == 0 && airborne) {
					player.getFoodData().addExhaustion(1.0f);
				}
			}

			@Override
			public void deactivate(ServerPlayer player, int level) {
				RuntimeManager.get(player).gliding = false;
			}
		};
	}

	/**
	 * TEMPEST DIVE (World Ability) - from high above, fold wings and strike
	 * like a meteor: a plunging attack that shatters the area you land on.
	 * Requires height, flight energy and a real fall - the impact does the
	 * damage, not the fall.
	 */
	private static Ability tempestDive() {
		return new Ability(Ability.active(TEMPEST_DIVE_ID)
				.categories(dev.volunteera.api.AbilityCategory.COMBAT, dev.volunteera.api.AbilityCategory.MOVEMENT)
				.maxLevel(2).unlockXp(-1, 30)
				.cooldown(25f, 20f)
				.cost(Cost.food(3), Cost.food(4))
				.hudSlot(3)
				.worldTrial(TEMPEST_TRIAL)) {
			@Override
			public ActivationResult checkEnvironment(ServerPlayer player, int level) {
				PlayerRuntime rt = RuntimeManager.get(player);
				if (player.onGround()) {
					return ActivationResult.fail(ActivationResult.FailureReason.NEEDS_AIRBORNE);
				}
				if (rt.flightEnergy < ModConfig.get().skyborne.tempestMinEnergy) {
					return ActivationResult.fail(ActivationResult.FailureReason.NO_ENERGY);
				}
				if (!hasHeight(player, 6)) {
					return ActivationResult.fail(ActivationResult.FailureReason.NEEDS_HEIGHT, "6");
				}
				return ActivationResult.OK;
			}

			@Override
			public ActivationResult activate(ServerPlayer player, int level) {
				PlayerRuntime rt = RuntimeManager.get(player);
				rt.flightEnergy -= ModConfig.get().skyborne.tempestMinEnergy;
				rt.tempestDiving = true;
				rt.tempestTicks = 0;
				Vec3 look = player.getLookAngle();
				AbilityFx.setVelocity(player, new Vec3(look.x * 0.4, -2.3, look.z * 0.4));
				player.resetFallDistance();
				if (player.level() instanceof ServerLevel sl) {
					AbilityFx.playSound(sl, player.position(), VanillaLookup.SND_LIGHTNING, 0.6f, 1.8f);
					AbilityFx.burstParticles(sl, player.position(), ParticleTypes.CLOUD, 12, 0.4);
				}
				StateSync.sync(player);
				return ActivationResult.OK;
			}

			@Override
			public void passiveTick(ServerPlayer player, int level, RuntimeManager runtimes) {
				PlayerRuntime rt = runtimes.get(player);
				if (!rt.tempestDiving) {
					return;
				}
				rt.tempestTicks += 10;
				if (player.onGround()) {
					rt.tempestDiving = false;
					player.resetFallDistance();
					if (player.level() instanceof ServerLevel sl) {
						float damage = level >= 2 ? 12.0f : 8.0f;
						double radius = level >= 2 ? 4.0 : 3.5;
						int hits = AbilityFx.areaAttack(player, sl, player.position(), radius, damage, 1.2, 0);
						AbilityFx.burstParticles(sl, player.position().add(0, 0.3, 0), ParticleTypes.EXPLOSION, 3, 0.5);
						AbilityFx.ringParticles(sl, player.position(), radius, ParticleTypes.CLOUD, 18);
						AbilityFx.playSound(sl, player.position(), VanillaLookup.SND_EXPLODE, 1.0f, 0.8f);
						if (hits > 0) {
							AbilityFx.actionbar(player, net.minecraft.network.chat.Component.literal(
									"Impact! " + hits + (hits == 1 ? " enemy" : " enemies") + " crushed"));
						}
					}
				} else if (rt.tempestTicks > 160) {
					rt.tempestDiving = false; // safety timeout (clipped into a wall etc.)
				}
			}

			private boolean hasHeight(ServerPlayer player, int blocks) {
				var level = player.level();
				var pos = player.blockPosition();
				for (int i = 2; i <= blocks; i++) {
					if (!level.getBlockState(pos.below(i)).isAir()) {
						return true;
					}
				}
				return false;
			}
		};
	}

	/** SKY MARKSMANSHIP - arrows loosed by a Skyborne hit harder. */
	private static Ability skyMarksmanship() {
		return new Ability(Ability.passive(SKY_MARKSMANSHIP_ID)
				.categories(dev.volunteera.api.AbilityCategory.COMBAT)
				.maxLevel(3).unlockXp(0, 15, 30)
				.cooldown(0f)) {
			@Override
			public void applyPassives(ServerPlayer player, int level, boolean apply) {
				if (VanillaLookup.ARROW_DAMAGE == null) {
					return;
				}
				double amount = switch (level) {
					case 1 -> 0.15;
					case 2 -> 0.30;
					default -> 0.50;
				};
				OriginAttributes.apply(player, new OriginAttributes.ModSpec(SkyborneContent.id("skyborne_arrows_mod"),
						VanillaLookup.ARROW_DAMAGE, amount, AttributeModifier.Operation.ADD_MULTIPLIED_TOTAL, null), apply);
			}
		};
	}

	/**
	 * LIGHT FRAME - your body burns bright and fast: extra exhaustion over
	 * time, but while well-fed high falls grant a burst of saturation (L2).
	 */
	private static Ability lightFrame() {
		return new Ability(Ability.passive(LIGHT_FRAME_ID)
				.categories(dev.volunteera.api.AbilityCategory.UTILITY, dev.volunteera.api.AbilityCategory.SPECIAL)
				.maxLevel(2).unlockXp(10, 30)
				.cooldown(0f)) {
			@Override
			public void passiveTick(ServerPlayer player, int level, RuntimeManager runtimes) {
				if (player.tickCount % 600 == 0) {
					player.getFoodData().addExhaustion(2.0f); // the tradeoff
				}
				if (level >= 2 && player.tickCount % 100 == 0
						&& player.getFoodData().getFoodLevel() >= 14
						&& player.getHealth() < player.getMaxHealth()) {
					// gentle regeneration while well fed
					player.heal(1.0f);
				}
			}
		};
	}

	/**
	 * GROUNDING BARBS (World Ability) - the sky's curse: a heavy hit while
	 * flying slams you out of the air. Implemented in the damage hook.
	 */
	private static Ability groundingBarbs() {
		return new Ability(Ability.passive(GROUNDING_BARBS_ID)
				.categories(dev.volunteera.api.AbilityCategory.SPECIAL)
				.maxLevel(1).unlockXp(-1)
				.cooldown(0f)
				.worldTrial(TEMPEST_TRIAL)) {
		};
	}

	/**
	 * WINDBORNE GRACE - read the air: small falls cost nothing (L1/L2), and
	 * at L3 you can spend flight energy to feather an imminent heavy landing.
	 * Implemented in the damage hook.
	 */
	private static Ability windborneGrace() {
		return new Ability(Ability.passive(WINDBORNE_GRACE_ID)
				.categories(dev.volunteera.api.AbilityCategory.MOVEMENT, dev.volunteera.api.AbilityCategory.UTILITY)
				.maxLevel(3).unlockXp(15, 35, 55)
				.cooldown(0f)) {
		};
	}
}
