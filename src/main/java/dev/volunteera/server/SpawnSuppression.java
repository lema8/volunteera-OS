package dev.volunteera.server;

import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.LevelAccessor;

import dev.volunteera.config.ModConfig;
import dev.volunteera.runtime.PlayerRuntime;
import dev.volunteera.runtime.RuntimeManager;

/**
 * Cheap spawn-suppression queries used by the single gameplay mixin. The
 * center list is rebuilt once per second from the players with Radiance
 * active - the mixin itself only does a handful of distance checks per spawn
 * attempt, never a world scan.
 */
public final class SpawnSuppression {
	public record Center(ServerLevel level, BlockPos pos, int radius) {
	}

	private static final List<Center> CENTERS = new CopyOnWriteArrayList<>();

	private SpawnSuppression() {
	}

	/** Rebuilds the cache. Called once per second from the event hooks. */
	public static void rebuild(List<ServerPlayer> players) {
		CENTERS.clear();
		for (ServerPlayer player : players) {
			PlayerRuntime rt = RuntimeManager.get(player);
			if (rt.radianceActive && rt.radianceRadius > 0) {
				CENTERS.add(new Center((ServerLevel) player.level(), player.blockPosition(), rt.radianceRadius));
			}
			if (rt.lanternPlaced && rt.lanternPos != null) {
				int radius = ModConfig.get().deepforge.lanternRadius[0];
				var lantern = dev.volunteera.api.Abilities.get(dev.volunteera.content.DeepforgeContent.LANTERN_ID);
				if (lantern != null) {
					int level = dev.volunteera.progress.ProgressManager.effectiveLevel(player, lantern);
					if (level > 0) {
						radius = ModConfig.get().deepforge.lanternRadius[level - 1];
						CENTERS.add(new Center((ServerLevel) player.level(), rt.lanternPos, radius));
					}
				}
			}
		}
	}

	/**
	 * Called from the spawn mixin: true when the position is inside any active
	 * Radiance ring on the given level.
	 */
	public static boolean suppresses(LevelAccessor level, BlockPos pos) {
		if (CENTERS.isEmpty()) {
			return false;
		}
		for (Center center : CENTERS) {
			if (center.level() != level) {
				continue;
			}
			BlockPos c = center.pos();
			int dx = pos.getX() - c.getX();
			int dy = pos.getY() - c.getY();
			int dz = pos.getZ() - c.getZ();
			if (dx * dx + dy * dy + dz * dz <= center.radius() * center.radius()) {
				return true;
			}
		}
		return false;
	}
}
