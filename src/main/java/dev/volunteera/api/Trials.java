package dev.volunteera.api;

import java.util.Collection;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

import org.jspecify.annotations.Nullable;

import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.block.state.BlockState;

import dev.volunteera.VolunteeraMod;
import dev.volunteera.progress.ProgressManager;

/**
 * Registry of world-ability trials and their progress handlers.
 */
public final class Trials {
	private static final Map<Identifier, Trial> REGISTRY = new LinkedHashMap<>();
	private static final Map<Identifier, Trial.Handler> HANDLERS = new LinkedHashMap<>();

	private Trials() {
	}

	public static void register(Trial trial) {
		if (REGISTRY.putIfAbsent(trial.id(), trial) != null) {
			VolunteeraMod.LOGGER.warn("Duplicate trial registration ignored: {}", trial.id());
		}
	}

	public static void registerHandler(Trial trial, Trial.Handler handler) {
		register(trial);
		HANDLERS.put(trial.id(), handler);
	}

	@Nullable
	public static Trial get(Identifier id) {
		return REGISTRY.get(id);
	}

	public static Collection<Trial> all() {
		return Collections.unmodifiableCollection(REGISTRY.values());
	}

	/** Gameplay events fed by the server event hooks. */
	public static void dispatchKill(ServerPlayer player, Entity killed) {
		dispatch(player, Trial.Event.KILL, new Trial.Context(player, killed, null, null));
	}

	public static void dispatchMine(ServerPlayer player, BlockState state, net.minecraft.core.BlockPos pos) {
		dispatch(player, Trial.Event.MINE, new Trial.Context(player, null, state, pos));
	}

	public static void dispatchGlide(ServerPlayer player) {
		dispatch(player, Trial.Event.GLIDE, new Trial.Context(player, null, null, null));
	}

	private static void dispatch(ServerPlayer player, Trial.Event event, Trial.Context context) {
		for (Trial trial : REGISTRY.values()) {
			if (trial.event() != event) {
				continue;
			}
			Trial.Handler handler = HANDLERS.get(trial.id());
			if (handler == null) {
				continue;
			}
			try {
				int current = ProgressManager.trialProgress(player, trial.id());
				int updated = handler.onProgress(context, current);
				ProgressManager.setTrialProgress(player, trial, updated);
			} catch (Exception e) {
				VolunteeraMod.LOGGER.error("Trial {} handler failed for {}", trial.id(), player.getGameProfile().getName(), e);
			}
		}
	}
}
