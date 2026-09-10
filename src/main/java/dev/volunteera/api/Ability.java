package dev.volunteera.api;

import java.util.EnumSet;
import java.util.List;

import org.jspecify.annotations.Nullable;

import net.minecraft.server.level.ServerPlayer;

import dev.volunteera.runtime.RuntimeManager;

/**
 * A single power belonging to an {@link Origin}.
 *
 * <p>Abilities are data + behavior: per-level cooldowns and costs, an unlock
 * path (either XP thresholds or a {@link Trial}), and overridable hooks for
 * the actual effect. Subclasses (usually small lambdas/anonymous classes in
 * the content packages) implement {@link #activate} and friends.
 */
public abstract class Ability {
	/** Maximum supported upgrade tier. */
	public static final int MAX_LEVELS = 3;

	private final Identifier id;
	private final AbilityKind kind;
	private final EnumSet<AbilityCategory> categories;
	private final int maxLevel;
	/** XP level thresholds; index i is the threshold to reach level i+1. */
	private final List<Integer> unlockXp;
	/** Cooldown in seconds per level (index level-1). */
	private final float[] cooldownSeconds;
	/** Cost per level (index level-1). */
	private final Cost[] costs;
	/** Direct HUD hotkey slot 0..5, or -1 for menu-only casting. */
	private final int hudSlot;
	/** If set, the ability is a World Ability unlocked through this trial. */
	@Nullable
	private final Trial worldTrial;

	protected Ability(Builder<?> builder) {
		this.id = builder.id;
		this.kind = builder.kind;
		this.categories = builder.categories;
		this.maxLevel = builder.maxLevel;
		this.unlockXp = builder.unlockXp;
		this.cooldownSeconds = builder.cooldownSeconds;
		this.costs = builder.costs;
		this.hudSlot = builder.hudSlot;
		this.worldTrial = builder.worldTrial;
	}

	public Identifier id() {
		return id;
	}

	public String nameKey() {
		return "ability." + id.getNamespace() + "." + id.getPath();
	}

	public String descKey() {
		return nameKey() + ".desc";
	}

	public AbilityKind kind() {
		return kind;
	}

	public EnumSet<AbilityCategory> categories() {
		return categories;
	}

	public int maxLevel() {
		return maxLevel;
	}

	public boolean isUpgradeable() {
		return maxLevel > 1;
	}

	/** XP threshold to reach the given level (1-based); -1 = no XP gate. */
	public int unlockXpFor(int level) {
		if (level < 1 || level > maxLevel || unlockXp.isEmpty()) {
			return -1;
		}
		int threshold = unlockXp.get(level - 1);
		return threshold >= 0 ? threshold : -1;
	}

	@Nullable
	public Trial worldTrial() {
		return worldTrial;
	}

	public boolean isWorldAbility() {
		return worldTrial != null;
	}

	public float cooldownSeconds(int level) {
		return cooldownSeconds[Math.max(0, Math.min(cooldownSeconds.length - 1, level - 1))];
	}

	public Cost cost(int level) {
		return costs[Math.max(0, Math.min(costs.length - 1, level - 1))];
	}

	public int hudSlot() {
		return hudSlot;
	}

	// ---------------------------------------------------------------- hooks

	/**
	 * Validates anything the generic pipeline cannot know (height, weather,
	 * tool in hand, ...). Return a failure to block activation with a clear
	 * reason. The generic checks (unlock, cooldown, cost) already ran.
	 */
	public ActivationResult checkEnvironment(ServerPlayer player, int level) {
		return ActivationResult.OK;
	}

	/**
	 * Executes the ability. Cost has already been paid and the cooldown has
	 * already started when this runs - throw nothing, return failures only
	 * before paying.
	 */
	public ActivationResult activate(ServerPlayer player, int level) {
		return ActivationResult.OK;
	}

	/** Called when a toggled ability is switched off or expires. */
	public void deactivate(ServerPlayer player, int level) {
	}

	/** Called every server tick while a toggle is active. */
	public void toggleTick(ServerPlayer player, int level) {
	}

	/**
	 * Applies or removes permanent (passive) effects. Must be idempotent:
	 * called with {@code apply=false} before re-applying at a new level, on
	 * origin switch and on unlock/removal.
	 */
	public void applyPassives(ServerPlayer player, int level, boolean apply) {
	}

	/** Optional per-player slow tick (every 10 ticks) for passive behaviors. */
	public void passiveTick(ServerPlayer player, int level, RuntimeManager runtimes) {
	}

	// ------------------------------------------------------------- builder

	@SuppressWarnings("unchecked")
	public static class Builder<B extends Builder<B>> {
		private final Identifier id;
		private final AbilityKind kind;
		private EnumSet<AbilityCategory> categories = EnumSet.of(AbilityCategory.UTILITY);
		private int maxLevel = 1;
		private List<Integer> unlockXp = List.of();
		private float[] cooldownSeconds = {0.0f};
		private Cost[] costs = {Cost.NONE};
		private int hudSlot = -1;
		@Nullable
		private Trial worldTrial = null;

		protected Builder(Identifier id, AbilityKind kind) {
			this.id = id;
			this.kind = kind;
		}

		public static Builder<?> active(Identifier id) {
			return new Builder<>(id, AbilityKind.ACTIVE);
		}

		public static Builder<?> toggle(Identifier id) {
			return new Builder<>(id, AbilityKind.TOGGLE);
		}

		public static Builder<?> passive(Identifier id) {
			return new Builder<>(id, AbilityKind.PASSIVE);
		}

		public B categories(AbilityCategory... cats) {
			this.categories = cats.length == 0 ? EnumSet.of(AbilityCategory.UTILITY) : EnumSet.copyOf(List.of(cats));
			return (B) this;
		}

		public B maxLevel(int levels) {
			this.maxLevel = Math.max(1, Math.min(MAX_LEVELS, levels));
			return (B) this;
		}

		/** XP thresholds for levels 1..maxLevel. Use -1 for "no XP gate". */
		public B unlockXp(int... thresholds) {
			this.unlockXp = List.of(java.util.Arrays.stream(thresholds).boxed().toArray(Integer[]::new));
			return (B) this;
		}

		public B cooldown(float... perLevelSeconds) {
			this.cooldownSeconds = perLevelSeconds;
			return (B) this;
		}

		public B cost(Cost... perLevel) {
			this.costs = perLevel;
			return (B) this;
		}

		public B hudSlot(int slot) {
			this.hudSlot = slot;
			return (B) this;
		}

		public B worldTrial(Trial trial) {
			this.worldTrial = trial;
			return (B) this;
		}

		Identifier id() {
			return id;
		}

		AbilityKind kind() {
			return kind;
		}

		EnumSet<AbilityCategory> categories() {
			return categories;
		}

		int maxLevel() {
			return maxLevel;
		}

		List<Integer> unlockXp() {
			return unlockXp;
		}

		float[] cooldownSeconds() {
			return cooldownSeconds;
		}

		Cost[] costs() {
			return costs;
		}

		int hudSlot() {
			return hudSlot;
		}

		Trial worldTrial() {
			return worldTrial;
		}
	}
}
