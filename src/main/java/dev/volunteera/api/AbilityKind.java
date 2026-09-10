package dev.volunteera.api;

/**
 * How an ability behaves.
 */
public enum AbilityKind {
	/** Instant effect, always costs + cooldown. */
	ACTIVE,
	/** Can be switched on/off; usually drains over time. */
	TOGGLE,
	/** Always on while unlocked. */
	PASSIVE
}
