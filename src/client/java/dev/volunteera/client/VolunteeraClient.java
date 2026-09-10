package dev.volunteera.client;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;

import dev.volunteera.client.hud.OriginsHud;
import dev.volunteera.client.screen.OriginsScreen;
import dev.volunteera.net.Payloads;

/**
 * Client entrypoint: keymappings, HUD registration, snapshot receiver and
 * input handling. The client never decides anything - it renders state and
 * sends requests.
 */
public class VolunteeraClient implements ClientModInitializer {
	@Override
	public void onInitializeClient() {
		Keybinds.register();
		OriginsHud.register();

		ClientPlayNetworking.registerGlobalReceiver(Payloads.SyncStatePayload.TYPE, (payload, context) ->
				ClientState.update(payload));

		ClientTickEvents.END_CLIENT_TICK.register(client -> {
			if (client.player == null) {
				return;
			}
			while (Keybinds.MENU.consumeClick()) {
				if (client.gui.screen() == null) {
					client.gui.setScreen(new OriginsScreen());
				}
			}
			for (int slot = 0; slot < Keybinds.SLOTS.length; slot++) {
				var mapping = Keybinds.SLOTS[slot];
				boolean pressed = false;
				while (mapping.consumeClick()) {
					pressed = true;
				}
				if (!pressed) {
					continue;
				}
				var origin = dev.volunteera.client.ClientState.origin();
				if (origin == null) {
					continue;
				}
				net.minecraft.resources.Identifier abilityId = origin.hudSlot(slot);
				if (abilityId != null) {
					ClientPlayNetworking.send(new Payloads.UseAbilityPayload(abilityId));
				}
			}
		});
	}
}
