package dev.volunteera.api;

import org.jspecify.annotations.Nullable;

import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.block.state.BlockState;

/**
 * A World Ability trial: an exploration / challenge goal that, once completed,
 * unlocks the owning ability regardless of XP level.
 *
 * <p>Trial progress is stored per player and persisted. Handlers are
 * registered per trial and receive gameplay events via {@link Context};
 * adding a new trial means instantiating one {@link Trial} and one handler -
 * no engine changes.
 */
public final class Trial {
	/** Event kinds a trial handler can be attached to. */
	public enum Event {
		KILL,
		GLIDE,
		MINE
	}

	/** What happened, passed to handlers. */
	public record Context(ServerPlayer player, @Nullable Entity entity,
			@Nullable BlockState state, @Nullable BlockPos pos) {
	}

	@FunctionalInterface
	public interface Handler {
		/**
		 * @param current current progress
		 * @return the new progress (clamped to {@code maxProgress} by the caller)
		 */
		int onProgress(Context context, int current);
	}

	private final Identifier id;
	private final String nameKey;
	private final String descKey;
	private final int maxProgress;
	private final Event event;

	public Trial(Identifier id, int maxProgress, Event event) {
		this.id = id;
		this.maxProgress = maxProgress;
		this.event = event;
		this.nameKey = "trial." + id.getNamespace() + "." + id.getPath() + ".name";
		this.descKey = "trial." + id.getNamespace() + "." + id.getPath() + ".desc";
	}

	public Identifier id() {
		return id;
	}

	public String nameKey() {
		return nameKey;
	}

	public String descKey() {
		return descKey;
	}

	public int maxProgress() {
		return maxProgress;
	}

	public Event event() {
		return event;
	}
}
