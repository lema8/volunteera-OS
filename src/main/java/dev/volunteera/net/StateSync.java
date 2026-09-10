package dev.volunteera.net;

import java.util.ArrayList;
import java.util.List;

import net.minecraft.server.level.ServerPlayer;

import dev.volunteera.api.Abilities;
import dev.volunteera.api.Ability;
import dev.volunteera.api.Origin;
import dev.volunteera.api.Origins;
import dev.volunteera.api.Trial;
import dev.volunteera.api.Trials;
import dev.volunteera.progress.ProgressData;
import dev.volunteera.progress.ProgressManager;
import dev.volunteera.runtime.PlayerRuntime;
import dev.volunteera.runtime.RuntimeManager;

/**
 * Builds and sends authoritative snapshots. The client HUD and menu render
 * exclusively from this data - no client-side gameplay simulation anywhere.
 */
public final class StateSync {
	private StateSync() {
	}

	public static void sync(ServerPlayer player) {
		PlayerRuntime rt = RuntimeManager.get(player);
		rt.lastSyncTick = player.tickCount;

		ProgressData data = ProgressData.get(player);
		Origin origin = Origins.get(data.origin());

		List<Payloads.AbilityState> abilities = new ArrayList<>();
		if (origin != null) {
			long now = player.level().getGameTime();
			for (Ability ability : origin.abilities()) {
				int level = ProgressManager.effectiveLevel(player, ability);
				int cooldown = (int) rt.cooldownRemaining(ability.id(), now);
				abilities.add(new Payloads.AbilityState(
						ability.id(), level, rt.activeToggles.contains(ability.id()), cooldown));
			}
		}

		List<Payloads.TrialState> trials = new ArrayList<>();
		for (Trial trial : Trials.all()) {
			trials.add(new Payloads.TrialState(trial.id(),
					ProgressData.get(player).trialProgress(trial.id()), trial.maxProgress()));
		}

		Payloads.SyncStatePayload payload = new Payloads.SyncStatePayload(
				data.origin(),
				abilities,
				trials,
				rt.flightEnergy,
				(byte) rt.flightPhase.ordinal(),
				rt.windCharges,
				AbilityEngine.windChargeMax(player),
				rt.dousedTicks > 0);
		dev.volunteera.net.Networking.send(player, payload);
	}
}
