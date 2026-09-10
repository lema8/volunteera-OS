package dev.volunteera.api;

import javax.annotation.Nullable;

/**
 * Result of an ability activation attempt. On failure carries a reason and an
 * optional pre-formatted detail (seconds, required level, ...) so the player
 * always gets an understandable message.
 */
public record ActivationResult(boolean success, @Nullable FailureReason reason, @Nullable String detail) {
	public static final ActivationResult OK = new ActivationResult(true, null, null);

	public static ActivationResult fail(FailureReason reason) {
		return new ActivationResult(false, reason, null);
	}

	public static ActivationResult fail(FailureReason reason, String detail) {
		return new ActivationResult(false, reason, detail);
	}

	public enum FailureReason {
		NOT_UNLOCKED("message.volunteera.not_unlocked"),
		ON_COOLDOWN("message.volunteera.on_cooldown"),
		NO_HUNGER("message.volunteera.no_hunger"),
		NO_HEALTH("message.volunteera.no_health"),
		NO_ITEM("message.volunteera.no_item"),
		ALREADY_ACTIVE("message.volunteera.already_active"),
		ALREADY_INACTIVE("message.volunteera.already_inactive"),
		NO_CHARGES("message.volunteera.no_charges"),
		NEEDS_GROUND("message.volunteera.needs_ground"),
		NEEDS_AIRBORNE("message.volunteera.needs_airborne"),
		NEEDS_HEIGHT("message.volunteera.needs_height"),
		NO_ENERGY("message.volunteera.no_energy"),
		DOUSED("message.volunteera.wet"),
		UNAVAILABLE("message.volunteera.not_unlocked");

		public final String messageKey;

		FailureReason(String messageKey) {
			this.messageKey = messageKey;
		}
	}
}
