package dev.volunteera.server;

import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.tags.DamageTypeTags;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.Level;

import dev.volunteera.VolunteeraMod;
import dev.volunteera.api.Abilities;
import dev.volunteera.api.Ability;
import dev.volunteera.api.Origin;
import dev.volunteera.api.Origins;
import dev.volunteera.api.Trial;
import dev.volunteera.api.Trials;
import dev.volunteera.command.ModCommands;
import dev.volunteera.content.EmberbornContent;
import dev.volunteera.content.SkyborneContent;
import dev.volunteera.net.Networking;
import dev.volunteera.net.StateSync;
import dev.volunteera.progress.ProgressData;
import dev.volunteera.progress.ProgressManager;
import dev.volunteera.runtime.PlayerRuntime;
import dev.volunteera.runtime.RuntimeManager;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.entity.event.v1.ServerLivingEntityEvents;
import net.fabricmc.fabric.api.entity.event.v1.ServerPlayerEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.event.player.AttackEntityCallback;
import net.fabricmc.fabric.api.event.player.PlayerBlockBreakEvents;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;

/**
 * All Fabric event wiring in one place: join/respawn restoration, damage
 * reactions (Windborne Grace, Grounding Barbs), Burning Grip, trial
 * progression (kills, mining), per-second passes (XP progression, spawn
 * suppression rebuild, flight sync) and disconnect cleanup.
 */
public final class EventHooks {
	private EventHooks() {
	}

	public static void init() {
		// -------------------------------------------------------- lifecycle

		ServerPlayerEvents.JOIN.register(player -> {
			if (player.level().isClientSide()) {
				return;
			}
			applyOriginState(player);
			StateSync.sync(player);
		});

		ServerPlayerEvents.AFTER_RESPAWN.register((oldPlayer, newPlayer, alive) -> {
			if (newPlayer.level().isClientSide()) {
				return;
			}
			// Session state resets on death: cooldowns clear, flight ends.
			RuntimeManager.get(newPlayer).onOriginChanged();
			applyOriginState(newPlayer);
			StateSync.sync(newPlayer);
		});

		ServerPlayConnectionEvents.DISCONNECT.register((handler, server) -> {
			// Runtime map is cleaned via RuntimeManager; nothing else to do.
		});

		ServerTickEvents.END_SERVER_TICK.register(EventHooks::serverTick);

		// ----------------------------------------------------------- damage

		ServerLivingEntityEvents.ALLOW_DAMAGE.register((entity, source, amount) -> {
			if (entity instanceof ServerPlayer player && !player.level().isClientSide()) {
				return allowDamage(player, source, amount);
			}
			return true;
		});

		ServerLivingEntityEvents.AFTER_DEATH.register((entity, source) -> {
			if (entity.level().isClientSide()) {
				return;
			}
			if (source.getEntity() instanceof ServerPlayer killer) {
				Trials.dispatchKill(killer, entity);
			}
		});

		// ------------------------------------------------------- interaction

		AttackEntityCallback.EVENT.register((player, world, hand, entity, hitResult) -> {
			if (!world.isClientSide() && hand == InteractionHand.MAIN_HAND && player instanceof ServerPlayer sp) {
				burningGrip(sp, entity);
			}
			return InteractionResult.PASS;
		});

		PlayerBlockBreakEvents.AFTER.register((level, player, pos, state, blockEntity) -> {
			if (!level.isClientSide() && player instanceof ServerPlayer sp) {
				Trials.dispatchMine(sp, state, pos);
				gildedTreasury(sp, state, pos);
			}
		});

		// ---------------------------------------------------------- commands

		CommandRegistrationCallback.EVENT.register((dispatcher, buildContext, selection) ->
				ModCommands.register(dispatcher));
	}

	private static void serverTick(MinecraftServer server) {
		if (server.getTickCount() % 20 == 0) {
			SpawnSuppression.rebuild(server.getPlayerList().getPlayers());
		}
		for (ServerPlayer player : server.getPlayerList().getPlayers()) {
			PlayerRuntime rt = RuntimeManager.get(player);
			accumulateGlide(player, rt);
			if (player.tickCount % 20 == 0) {
				ProgressManager.tickProgression(player);
			}
			// Flight energy changes constantly while flying/charging; keep
			// the HUD meter honest with a throttled periodic sync.
			if (rt.flightPhase != PlayerRuntime.FlightPhase.NONE
					|| player.tickCount - rt.lastSyncTick > 40) {
				StateSync.sync(player);
			}
		}
	}

	private static void accumulateGlide(ServerPlayer player, PlayerRuntime rt) {
		var pos = player.position();
		if (rt.prevPos != null) {
			double dist = pos.distanceTo(rt.prevPos);
			boolean gliding = player.isFallFlying() || rt.gliding;
			if (gliding && !player.onGround() && dist < 2.0) {
				rt.glideAccum += dist;
			}
		}
		rt.prevPos = pos;
		if (rt.glideAccum >= 50.0) {
			Trials.dispatchGlide(player);
			rt.glideAccum = 0.0;
		}
	}

	/** Re-applies origin attribute profile and unlocked passives to a player entity. */
	public static void applyOriginState(ServerPlayer player) {
		ProgressData data = ProgressData.get(player);
		Origin origin = Origins.get(data.origin());
		if (origin == null) {
			return;
		}
		try {
			OriginAttributes.applyOrigin(player, origin);
			for (Ability ability : origin.abilities()) {
				int level = ProgressManager.effectiveLevel(player, ability);
				if (level > 0) {
					ability.applyPassives(player, level, true);
				}
			}
		} catch (Exception e) {
			VolunteeraMod.LOGGER.error("Failed to apply origin state for {}", player.getGameProfile().name(), e);
		}
	}

	// ------------------------------------------------------------- combat

	private static boolean allowDamage(ServerPlayer player, net.minecraft.world.damagesource.DamageSource source, float amount) {
		PlayerRuntime rt = RuntimeManager.get(player);
		Origin origin = Origins.get(ProgressManager.originOf(player));
		if (origin == null) {
			return true;
		}

		// Skyborne Windborne Grace L3: spend flight energy to negate a fall.
		int graceLevel = ProgressManager.effectiveLevel(player, Abilities.get(SkyborneContent.WINDBORNE_GRACE_ID));
		if (graceLevel >= 3 && source.is(DamageTypeTags.IS_FALL)
				&& rt.flightEnergy >= 15.0f && !player.onGround()) {
			rt.flightEnergy -= 15.0f;
			AbilityFx.actionbar(player, Component.translatable("message.volunteera.flight_charging"));
			StateSync.sync(player);
			return false;
		}

		// Emberborn Immolation: attackers get singed.
		if (rt.activeToggles.contains(EmberbornContent.IMMOLATION_ID)
				&& source.getEntity() instanceof LivingEntity attacker
				&& source.getEntity() != player) {
			attacker.setRemainingFireTicks(Math.max(attacker.getRemainingFireTicks(), 60));
		}

		// Skyborne Tempest Dive: the impact itself is the damage; cancel dive fall damage.
		if (rt.tempestDiving && source.is(DamageTypeTags.IS_FALL)) {
			return false;
		}

		// Skyborne Windborne Grace L1/L2: small falls are free (judged by the
		// damage amount, which tracks fall height).
		if (graceLevel >= 1 && source.is(DamageTypeTags.IS_FALL)) {
			float threshold = graceLevel >= 2 ? 9.0f : 5.0f;
			if (amount <= threshold) {
				return false;
			}
		}

		// Skyborne Grounding Barbs (world ability): heavy hits slam you out of the sky.
		int barbsLevel = ProgressManager.effectiveLevel(player, Abilities.get(SkyborneContent.GROUNDING_BARBS_ID));
		if (barbsLevel > 0 && rt.mayFlyGranted && player.getAbilities().flying && amount >= 6.0f) {
			FlightController.revokeMayFly(player, rt);
			rt.flightPhase = PlayerRuntime.FlightPhase.NONE;
			AbilityFx.actionbar(player, Component.translatable("message.volunteera.flight_depleted"));
			StateSync.sync(player);
			return true;
		}
		return true;
	}

	private static void burningGrip(ServerPlayer player, net.minecraft.world.entity.Entity target) {
		Origin origin = Origins.get(ProgressManager.originOf(player));
		if (origin == null || !(target instanceof LivingEntity living)) {
			return;
		}
		Ability grip = origin.ability(EmberbornContent.BURNING_GRIP_ID);
		if (grip == null) {
			return;
		}
		int level = ProgressManager.effectiveLevel(player, grip);
		if (level <= 0) {
			return;
		}
		int seconds = 2 + level; // L1 3s, L2 4s, L3 5s
		living.setRemainingFireTicks(Math.max(living.getRemainingFireTicks(), seconds * 20));
	}

	private static void gildedTreasury(ServerPlayer player, net.minecraft.world.level.block.state.BlockState state, net.minecraft.core.BlockPos pos) {
		Origin origin = Origins.get(ProgressManager.originOf(player));
		if (origin == null || pos.getY() >= 0) {
			return;
		}
		Ability treasury = origin.ability(Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, "deepforge_gilded_treasury"));
		if (treasury == null || ProgressManager.effectiveLevel(player, treasury) <= 0) {
			return;
		}
		if (state.is(DeepforgeContent.ORE_TAGS.get(0))
				|| state.is(DeepforgeContent.ORE_TAGS.get(1))
				|| state.is(DeepforgeContent.ORE_TAGS.get(2))
				|| state.is(DeepforgeContent.ORE_TAGS.get(3))
				|| state.is(DeepforgeContent.ORE_TAGS.get(7))) {
			if (player.getRandom().nextFloat() < 0.25f) {
				player.giveExperiencePoints(1 + player.getRandom().nextInt(2));
			}
		}
	}
}
