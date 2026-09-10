package dev.volunteera;

import net.fabricmc.api.ModInitializer;

import dev.volunteera.command.ModCommands;
import dev.volunteera.config.ModConfig;
import dev.volunteera.content.DeepforgeContent;
import dev.volunteera.content.EmberbornContent;
import dev.volunteera.content.SkyborneContent;
import dev.volunteera.net.Networking;
import dev.volunteera.progress.ProgressAttachments;
import dev.volunteera.runtime.RuntimeManager;
import dev.volunteera.server.EventHooks;
import dev.volunteera.server.OriginAttributes;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Volunteera: Origins & Abilities.
 *
 * <p>Main entrypoint. Boot order matters: config first, then content
 * registration, then attachments, networking, commands and event hooks.
 */
public class VolunteeraMod implements ModInitializer {
	public static final String MOD_ID = "volunteera";
	public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

	@Override
	public void onInitialize() {
		ModConfig.load();

		dev.volunteera.server.VanillaLookup.init();
		OriginAttributes.init();
		EmberbornContent.register();
		SkyborneContent.register();
		DeepforgeContent.register();

		ProgressAttachments.init();
		Networking.init();
		RuntimeManager.init();
		EventHooks.init();

		LOGGER.info("Volunteera initialized with {} origins and {} abilities.",
				dev.volunteera.api.Origins.count(),
				dev.volunteera.api.Abilities.count());
	}

	public static net.minecraft.resources.Identifier id(String path) {
		return net.minecraft.resources.Identifier.fromNamespaceAndPath(MOD_ID, path);
	}
}
