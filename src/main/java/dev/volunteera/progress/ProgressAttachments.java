package dev.volunteera.progress;

import net.minecraft.resources.Identifier;

import dev.volunteera.VolunteeraMod;
import net.fabricmc.fabric.api.attachment.v1.AttachmentRegistry;
import net.fabricmc.fabric.api.attachment.v1.AttachmentType;

/**
 * The persistent progression attachment. {@code copyOnDeath} keeps progress
 * through death and respawn; {@code persistent} writes it to playerdata so it
 * survives restarts.
 */
public final class ProgressAttachments {
	public static final AttachmentType<ProgressData> PROGRESS = AttachmentRegistry.create(
			Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, "progress"),
			builder -> builder
					.persistent(ProgressData.CODEC)
					.copyOnDeath()
					.initializer(() -> ProgressData.DEFAULT));

	private ProgressAttachments() {
	}

	public static void init() {
		// Classloading is the registration.
	}
}
