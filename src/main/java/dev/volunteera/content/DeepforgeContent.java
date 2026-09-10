package dev.volunteera.content;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import net.minecraft.core.BlockPos;
import net.minecraft.core.particles.ParticleTypes;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.sounds.SoundSource;
import net.minecraft.tags.ItemTags;
import net.minecraft.tags.TagKey;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.entity.ai.attributes.AttributeModifier;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
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
import dev.volunteera.runtime.PlayerRuntime;
import dev.volunteera.runtime.RuntimeManager;
import dev.volunteera.server.AbilityFx;
import dev.volunteera.server.OriginAttributes;
import dev.volunteera.server.SpawnSuppression;
import dev.volunteera.server.VanillaLookup;

/**
 * DEEPFORGE - carved from bedrock by elder hands.
 *
 * <p>Identity: the master miner and stubborn defender. One block tall with a
 * huge health pool, terrifying pickaxe speed, ore senses and anvil-heavy
 * melee. Slow, short-ranged, and terrible at ranged combat.
 */
public final class DeepforgeContent {
	public static final Identifier TITAN_SWINGS_ID = DeepforgeContent.id("deepforge_titan_swings");
	public static final Identifier STONEBLOOD_ID = DeepforgeContent.id("deepforge_stoneblood");
	public static final Identifier VEIN_SENSE_ID = DeepforgeContent.id("deepforge_vein_sense");
	public static final Identifier EARTH_SHATTER_ID = DeepforgeContent.id("deepforge_earth_shatter");
	public static final Identifier DEEP_EYES_ID = DeepforgeContent.id("deepforge_darkvision");
	public static final Identifier ANVIL_FISTS_ID = DeepforgeContent.id("deepforge_anvil_fists");
	public static final Identifier MOLTEN_CORE_ID = DeepforgeContent.id("deepforge_molten_core");
	public static final Identifier GILDED_TREASURY_ID = DeepforgeContent.id("deepforge_gilded_treasury");
	public static final Identifier SHIELDWALL_ID = DeepforgeContent.id("deepforge_shieldwall");
	public static final Identifier LANTERN_ID = DeepforgeContent.id("deepforge_stonewrought_lantern");

	public static final Trial DEPTHS_TRIAL = new Trial(DeepforgeContent.id("deepforge_depths"), 64, Trial.Event.MINE);

	/**
	 * Ore tags scanned by Vein Sense. Built from stable registry keys instead
	 * of BlockTags constants, so renames of the constants cannot break the
	 * lookup (the datapack-facing tag ids are very stable).
	 */
	private static final List<TagKey<Block>> ORE_TAGS = List.of(
			oreTag("iron_ores"), oreTag("gold_ores"), oreTag("diamond_ores"),
			oreTag("emerald_ores"), oreTag("copper_ores"), oreTag("coal_ores"),
			oreTag("redstone_ores"), oreTag("lapis_ores"));

	private static TagKey<Block> oreTag(String name) {
		return TagKey.create(net.minecraft.core.registries.Registries.BLOCK,
				Identifier.withDefaultNamespace(name));
	}

	private DeepforgeContent() {
	}

	private static Identifier id(String path) {
		return Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, path);
	}

	public static void register() {
		Origin origin = Origin.builder("deepforge", 0xC9A227)
				.ability(titanSwings())
				.ability(stoneblood())
				.ability(veinSense())
				.ability(earthShatter())
				.ability(deepEyes())
				.ability(anvilFists())
				.ability(moltenCore())
				.ability(gildedTreasury())
				.ability(shieldwall())
				.ability(stonewroughtLantern())
				.build();

		// Profile: one block tall, heavy, deliberate.
		OriginAttributes.registerProfile(origin, List.of(
				new OriginAttributes.ModSpec(DeepforgeContent.id("deepforge_small"), VanillaLookup.SCALE, 0.45,
						AttributeModifier.Operation.ADD_MULTIPLIED_BASE, null),
				new OriginAttributes.ModSpec(DeepforgeContent.id("deepforge_reach"), VanillaLookup.BLOCK_INTERACTION_RANGE, -0.5,
						AttributeModifier.Operation.ADD_VALUE, null),
				new OriginAttributes.ModSpec(DeepforgeContent.id("deepforge_squat"), VanillaLookup.attrHolder("movement_speed", "generic.movement_speed"), -0.015,
						AttributeModifier.Operation.ADD_VALUE, null)));

		Origins.register(origin);
		for (Ability ability : origin.abilities()) {
			Abilities.register(ability);
		}
		Trials.registerHandler(DEPTHS_TRIAL, (context, current) -> {
			if (context.state() != null && context.pos() != null
					&& context.pos().getY() < -40
					&& context.state().is(Blocks.DEEPSLATE)
					&& context.player().getMainHandItem().is(ItemTags.PICKAXES)) {
				return current + 1;
			}
			return current;
		});
	}

	/** Conditional pickaxe bonus for Titan Swings, used by the passive engine. */
	public static OriginAttributes.ModSpec pickaxeSpec(int level) {
		if (level <= 0) {
			return null;
		}
		double amount = switch (level) {
			case 1 -> 0.6;
			case 2 -> 1.0;
			default -> 1.5;
		};
		return new OriginAttributes.ModSpec(DeepforgeContent.id("deepforge_titan_pick"),
				VanillaLookup.BLOCK_BREAK_SPEED, amount, AttributeModifier.Operation.ADD_MULTIPLIED_BASE, ItemTags.PICKAXES);
	}

	// ------------------------------------------------------------- abilities

	/**
	 * TITAN SWINGS - a lifetime in the mines: flat bonus everywhere, massive
	 * with a pickaxe (up to 5x total). Base handled here; pickaxe bonus is
	 * conditional (PassiveEngine + pickaxeSpec).
	 */
	private static Ability titanSwings() {
		return new Ability(Ability.passive(TITAN_SWINGS_ID)
				.categories(dev.volunteera.api.AbilityCategory.UTILITY, dev.volunteera.api.AbilityCategory.COMBAT)
				.maxLevel(3).unlockXp(0, 20, 40)
				.cooldown(0f)) {
			@Override
			public void applyPassives(ServerPlayer player, int level, boolean apply) {
				if (VanillaLookup.BLOCK_BREAK_SPEED == null) {
					return;
				}
				double amount = switch (level) {
					case 1 -> 0.3;
					case 2 -> 0.6;
					default -> 1.0;
				};
				OriginAttributes.apply(player, new OriginAttributes.ModSpec(DeepforgeContent.id("deepforge_titan_base"),
						VanillaLookup.BLOCK_BREAK_SPEED, amount, AttributeModifier.Operation.ADD_VALUE, null), apply);
				if (!apply) {
					OriginAttributes.removeToolModifier(player, pickaxeSpec(Math.max(level, 1)));
				}
			}
		};
	}

	/** STONEBLOOD - the mountain in miniature: huge health, steady stance. */
	private static Ability stoneblood() {
		return new Ability(Ability.passive(STONEBLOOD_ID)
				.categories(dev.volunteera.api.AbilityCategory.COMBAT, dev.volunteera.api.AbilityCategory.UTILITY)
				.maxLevel(2).unlockXp(0, 20)
				.cooldown(0f)) {
			@Override
			public void applyPassives(ServerPlayer player, int level, boolean apply) {
				OriginAttributes.apply(player, new OriginAttributes.ModSpec(DeepforgeContent.id("deepforge_stone_hp"),
						VanillaLookup.attrHolder("max_health", "generic.max_health"), level >= 2 ? 8.0 : 6.0,
						AttributeModifier.Operation.ADD_VALUE, null), apply);
				OriginAttributes.apply(player, new OriginAttributes.ModSpec(DeepforgeContent.id("deepforge_stone_kb"),
						VanillaLookup.attrHolder("knockback_resistance", "generic.knockback_resistance"),
						level >= 2 ? 0.4 : 0.2, AttributeModifier.Operation.ADD_VALUE, null), apply);
			}
		};
	}

	/**
	 * VEIN SENSE - you smell ore in the stone. A toggle: every few seconds it
	 * pings nearby ores through the action bar (counts per type at L2, with
	 * direction hints at L3). Small food cost per pulse.
	 */
	private static Ability veinSense() {
		return new Ability(Ability.toggle(VEIN_SENSE_ID)
				.categories(dev.volunteera.api.AbilityCategory.UTILITY, dev.volunteera.api.AbilityCategory.SPECIAL)
				.maxLevel(3).unlockXp(10, 25, 45)
				.cooldown(2f, 2f, 2f)
				.cost(Cost.NONE)
				.hudSlot(0)) {
			@Override
			public ActivationResult activate(ServerPlayer player, int level) {
				pulse(player, level, true);
				return ActivationResult.OK;
			}

			@Override
			public void toggleTick(ServerPlayer player, int level) {
				if (player.tickCount % 100 == 0) {
					pulse(player, level, false);
				}
			}

			private void pulse(ServerPlayer player, int level, boolean force) {
				if (!(player.level() instanceof ServerLevel sl)) {
					return;
				}
				int cost = ModConfig.get().deepforge.veinSenseCost[level - 1];
				if (player.getFoodData().getFoodLevel() < cost) {
					RuntimeManager.get(player).toggleEnds.put(VEIN_SENSE_ID, player.level().getGameTime());
					return;
				}
				int radius = ModConfig.get().deepforge.veinSenseRadius[level - 1];
				BlockPos center = player.blockPosition();
				BlockPos min = center.offset(-radius, -6, -radius);
				BlockPos max = center.offset(radius, 6, radius);

				Map<String, Integer> counts = new HashMap<>();
				List<BlockPos> found = new ArrayList<>();
				for (BlockPos pos : BlockPos.betweenClosed(min, max)) {
					BlockState state = sl.getBlockState(pos);
					if (state.isAir()) {
						continue;
					}
					for (TagKey<Block> tag : ORE_TAGS) {
						if (state.is(tag)) {
							counts.merge(tag.location().getPath(), 1, Integer::sum);
							found.add(pos.immutable());
							break;
						}
					}
				}
				player.getFoodData().setFoodLevel(Math.max(0, player.getFoodData().getFoodLevel() - cost));
				if (counts.isEmpty()) {
					if (force) {
						AbilityFx.actionbar(player, Component.literal("The stone is silent here."));
					}
					return;
				}
				StringBuilder sb = new StringBuilder("Ore: ");
				if (level >= 2) {
					boolean first = true;
					for (var entry : counts.entrySet()) {
						if (!first) {
							sb.append(", ");
						}
						sb.append(entry.getValue()).append(' ')
								.append(entry.getKey().replace("_ores", ""));
						first = false;
					}
				} else {
					int total = counts.values().stream().mapToInt(Integer::intValue).sum();
					sb.append(total).append(" vein").append(total == 1 ? "" : "s").append(" nearby");
				}
				if (level >= 3 && !found.isEmpty()) {
					BlockPos nearest = found.get(0);
					double best = Double.MAX_VALUE;
					for (BlockPos pos : found) {
						double d = pos.distToCenterSqr(player.position());
						if (d < best) {
							best = d;
							nearest = pos;
						}
					}
					sb.append(" | nearest: ").append(direction(player, nearest));
				}
				AbilityFx.actionbar(player, Component.literal(sb.toString()));
				AbilityFx.playSound(sl, player.position(), VanillaLookup.SND_PING, 0.5f, 1.6f);
			}

			private String direction(ServerPlayer player, BlockPos target) {
				double dx = target.getX() + 0.5 - player.getX();
				double dz = target.getZ() + 0.5 - player.getZ();
				double dy = target.getY() - player.getY();
				double angle = Math.toDegrees(Math.atan2(dz, dx)) - Math.toDegrees(player.getYRot()) + 90;
				angle = ((angle % 360) + 360) % 360;
				String[] dirs = {"E", "SE", "S", "SW", "W", "NW", "N", "NE"};
				String horizontal = dirs[(int) Math.round(angle / 45.0) % 8];
				String vertical = dy > 2 ? " up" : dy < -2 ? " down" : "";
				return horizontal + vertical;
			}
		};
	}

	/**
	 * EARTH SHATTER - slam a heavy tool into the ground: a ring of force that
	 * staggers enemies. Costs food and a splinter of health.
	 */
	private static Ability earthShatter() {
		return new Ability(Ability.active(EARTH_SHATTER_ID)
				.categories(dev.volunteera.api.AbilityCategory.COMBAT, dev.volunteera.api.AbilityCategory.UTILITY)
				.maxLevel(3).unlockXp(15, 35, 55)
				.cooldown(18f, 16f, 14f)
				.cost(Cost.foodHealth(3, 2), Cost.foodHealth(3, 2), Cost.foodHealth(4, 2))
				.hudSlot(1)) {
			@Override
			public ActivationResult checkEnvironment(ServerPlayer player, int level) {
				if (!player.onGround()) {
					return ActivationResult.fail(ActivationResult.FailureReason.NEEDS_GROUND);
				}
				ItemStack held = player.getMainHandItem();
				if (!held.is(ItemTags.PICKAXES) && !held.is(ItemTags.AXES) && !held.is(ItemTags.SHOVELS)) {
					return ActivationResult.fail(ActivationResult.FailureReason.NO_ITEM, "a heavy tool");
				}
				return ActivationResult.OK;
			}

			@Override
			public ActivationResult activate(ServerPlayer player, int level) {
				if (!(player.level() instanceof ServerLevel sl)) {
					return ActivationResult.OK;
				}
				double radius = switch (level) {
					case 1 -> 3.0;
					case 2 -> 3.5;
					default -> 4.0;
				};
				float damage = switch (level) {
					case 1 -> 5.0f;
					case 2 -> 7.0f;
					default -> 9.0f;
				};
				AbilityFx.areaAttack(player, sl, player.position(), radius, damage, 1.0 + level * 0.1, 0);
				AbilityFx.ringParticles(sl, player.position(), radius, ParticleTypes.POOF, 14);
				AbilityFx.playSound(sl, player.position(), VanillaLookup.SND_ANVIL, 1.0f, 0.7f);
				return ActivationResult.OK;
			}
		};
	}

	/** DEEP EYES - generations in the dark: permanent night vision. */
	private static Ability deepEyes() {
		return new Ability(Ability.passive(DEEP_EYES_ID)
				.categories(dev.volunteera.api.AbilityCategory.UTILITY)
				.maxLevel(1).unlockXp(0)
				.cooldown(0f)) {
			@Override
			public void applyPassives(ServerPlayer player, int level, boolean apply) {
				if (VanillaLookup.NIGHT_VISION == null) {
					return;
				}
				if (apply && level >= 1) {
					player.addEffect(new MobEffectInstance(VanillaLookup.NIGHT_VISION, -1, 0, true, false, false));
				} else {
					player.removeEffect(VanillaLookup.NIGHT_VISION);
				}
			}
		};
	}

	/** ANVIL FISTS - blows land like a falling hammer: bonus knockback. */
	private static Ability anvilFists() {
		return new Ability(Ability.passive(ANVIL_FISTS_ID)
				.categories(dev.volunteera.api.AbilityCategory.COMBAT)
				.maxLevel(2).unlockXp(10, 25)
				.cooldown(0f)) {
			@Override
			public void applyPassives(ServerPlayer player, int level, boolean apply) {
				OriginAttributes.apply(player, new OriginAttributes.ModSpec(DeepforgeContent.id("deepforge_anvil_mod"),
						VanillaLookup.ATTACK_KNOCKBACK, level >= 2 ? 1.0 : 0.5,
						AttributeModifier.Operation.ADD_VALUE, null), apply);
			}
		};
	}

	/** MOLTEN CORE - lava is a warm bath: fire immunity while immersed. */
	private static Ability moltenCore() {
		return new Ability(Ability.passive(MOLTEN_CORE_ID)
				.categories(dev.volunteera.api.AbilityCategory.UTILITY, dev.volunteera.api.AbilityCategory.SPECIAL)
				.maxLevel(2).unlockXp(20, 45)
				.cooldown(0f)) {
			@Override
			public void applyPassives(ServerPlayer player, int level, boolean apply) {
				// Effect-based, applied per tick below.
			}

			@Override
			public void passiveTick(ServerPlayer player, int level, RuntimeManager runtimes) {
				boolean inLava = player.isInLava();
				if (VanillaLookup.FIRE_RESISTANCE != null && inLava) {
					player.addEffect(new MobEffectInstance(VanillaLookup.FIRE_RESISTANCE, 100, 0, true, false, true));
				}
			}
		};
	}

	/**
	 * GILDED TREASURY - deep ore calls to its own: mining valuable ores below
	 * Y=0 slowly enriches you with experience. Implemented in the block-break
	 * hook (chance scales with level).
	 */
	private static Ability gildedTreasury() {
		return new Ability(Ability.passive(GILDED_TREASURY_ID)
				.categories(dev.volunteera.api.AbilityCategory.UTILITY, dev.volunteera.api.AbilityCategory.SPECIAL)
				.maxLevel(2).unlockXp(15, 40)
				.cooldown(0f)) {
		};
	}

	/**
	 * SHIELDWALL (World Ability) - brace like a fortress wall: 6 seconds of
	 * massive damage resistance at the cost of mobility. Learned in the deep.
	 */
	private static Ability shieldwall() {
		return new Ability(Ability.toggle(SHIELDWALL_ID)
				.categories(dev.volunteera.api.AbilityCategory.COMBAT)
				.maxLevel(1).unlockXp(-1)
				.cooldown(45f)
				.cost(Cost.food(4))
				.hudSlot(2)
				.worldTrial(DEPTHS_TRIAL)) {
			@Override
			public ActivationResult activate(ServerPlayer player, int level) {
				if (VanillaLookup.RESISTANCE == null) {
					return ActivationResult.fail(ActivationResult.FailureReason.UNAVAILABLE);
				}
				player.addEffect(new MobEffectInstance(VanillaLookup.RESISTANCE, 20 * 6, 2, false, true, true));
				if (VanillaLookup.SLOWNESS != null) {
					player.addEffect(new MobEffectInstance(VanillaLookup.SLOWNESS, 20 * 6, 3, false, true, true));
				}
				if (player.level() instanceof ServerLevel sl) {
					AbilityFx.playSound(sl, player.position(), VanillaLookup.SND_ANVIL, 1.0f, 0.9f);
				}
				RuntimeManager.get(player).toggleEnds.put(SHIELDWALL_ID, player.level().getGameTime() + 20 * 6);
				return ActivationResult.OK;
			}
		};
	}

	/**
	 * STONEWROUGHT LANTERN (World Ability) - conjure a floating light that
	 * follows you: real light level 15, hostile spawns keep their distance.
	 * The lantern places/removes a vanilla light block near your head.
	 */
	private static Ability stonewroughtLantern() {
		return new Ability(Ability.toggle(LANTERN_ID)
				.categories(dev.volunteera.api.AbilityCategory.UTILITY, dev.volunteera.api.AbilityCategory.SPECIAL)
				.maxLevel(2).unlockXp(-1, 30)
				.cooldown(5f, 4f)
				.cost(Cost.food(2), Cost.food(2))
				.hudSlot(3)
				.worldTrial(DEPTHS_TRIAL)) {
			@Override
			public ActivationResult activate(ServerPlayer player, int level) {
				if (!(player.level() instanceof ServerLevel sl)) {
					return ActivationResult.OK;
				}
				if (placeLantern(sl, player)) {
					AbilityFx.playSound(sl, player.position(), VanillaLookup.SND_STONE_PLACE, 0.8f, 1.4f);
					StateSync.sync(player);
					return ActivationResult.OK;
				}
				return ActivationResult.fail(ActivationResult.FailureReason.NEEDS_AIRBORNE);
			}

			@Override
			public void toggleTick(ServerPlayer player, int level) {
				if (player.tickCount % 10 != 0 || !(player.level() instanceof ServerLevel sl)) {
					return;
				}
				PlayerRuntime rt = RuntimeManager.get(player);
				BlockPos desired = player.blockPosition().above(2);
				if (!desired.equals(rt.lanternPos) && canPlace(sl, desired)) {
					removeLantern(sl, player, false);
					placeLantern(sl, player);
				}
			}

			@Override
			public void deactivate(ServerPlayer player, int level) {
				if (player.level() instanceof ServerLevel sl) {
					removeLantern(sl, player, true);
				}
			}

			private boolean canPlace(ServerLevel sl, BlockPos pos) {
				BlockState state = sl.getBlockState(pos);
				return state.isAir() || state.canBeReplaced();
			}

			private boolean placeLantern(ServerLevel sl, ServerPlayer player) {
				BlockPos pos = player.blockPosition().above(2);
				if (!canPlace(sl, pos)) {
					return false;
				}
				sl.setBlock(pos, Blocks.LIGHT.defaultBlockState(), 3);
				PlayerRuntime rt = RuntimeManager.get(player);
				rt.lanternPlaced = true;
				rt.lanternPos = pos;
				return true;
			}

			private void removeLantern(ServerLevel sl, ServerPlayer player, boolean announce) {
				PlayerRuntime rt = RuntimeManager.get(player);
				BlockPos pos = rt.lanternPos;
				rt.lanternPlaced = false;
				rt.lanternPos = null;
				if (pos != null && sl.getBlockState(pos).is(Blocks.LIGHT)) {
					sl.removeBlock(pos, false);
					if (announce) {
						AbilityFx.playSound(sl, Vec3.atCenterOf(pos), VanillaLookup.SND_STONE_BREAK, 0.7f, 1.2f);
					}
				}
			}
		};
	}
}
