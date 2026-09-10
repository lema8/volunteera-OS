package dev.volunteera.net;

import net.minecraft.server.level.ServerPlayer;

import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;

import dev.volunteera.api.Abilities;
import dev.volunteera.api.Origin;
import dev.volunteera.api.Origins;
import dev.volunteera.config.ModConfig;
import dev.volunteera.progress.ProgressData;
import dev.volunteera.progress.ProgressManager;
import dev.volunteera.server.AbilityEngine;
import net.fabricmc.fabric.api.networking.v1.PacketSender;

/**
 * Payload registration and the server-side handlers. Handlers only trust the
 * server state; a payload is a request, never a command.
 */
public final class Networking {
	private Networking() {
	}

	public static void init() {
		PayloadTypeRegistry.playC2S().register(Payloads.UseAbilityPayload.TYPE, Payloads.UseAbilityPayload.CODEC);
		PayloadTypeRegistry.playC2S().register(Payloads.SetOriginPayload.TYPE, Payloads.SetOriginPayload.CODEC);
		PayloadTypeRegistry.playC2S().register(Payloads.RequestSyncPayload.TYPE, Payloads.RequestSyncPayload.CODEC);
		PayloadTypeRegistry.playS2C().register(Payloads.SyncStatePayload.TYPE, Payloads.SyncStatePayload.CODEC);

		ServerPlayNetworking.registerGlobalReceiver(Payloads.UseAbilityPayload.TYPE, (payload, context) ->
				AbilityEngine.tryActivate(context.player(), payload.abilityId()));

		ServerPlayNetworking.registerGlobalReceiver(Payloads.SetOriginPayload.TYPE, (payload, context) -> {
			ServerPlayer player = context.player();
			if (!ModConfig.get().allowOriginSwitch && !ProgressData.get(player).testMode()) {
				AbilityEngine.feedback(player,
						dev.volunteera.api.ActivationResult.FailureReason.UNAVAILABLE, null);
				player.sendSystemMessage(net.minecraft.network.chat.Component.translatable("message.volunteera.origin_locked"));
				StateSync.sync(player);
				return;
			}
			ProgressManager.setOrigin(player, payload.originId());
		});

		ServerPlayNetworking.registerGlobalReceiver(Payloads.RequestSyncPayload.TYPE, (payload, context) ->
				StateSync.sync(context.player()));

		// Push a snapshot as soon as play begins (join + dimension change).
		ServerPlayConnectionEvents.JOIN.register((handler, sender, server) ->
				StateSync.sync(handler.getPlayer()));
	}

	public static void send(ServerPlayer player, Payloads.SyncStatePayload payload) {
		if (ServerPlayNetworking.canSend(player, Payloads.SyncStatePayload.TYPE)) {
			ServerPlayNetworking.send(player, payload);
		}
	}
}
