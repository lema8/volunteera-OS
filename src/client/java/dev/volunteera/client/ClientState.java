package dev.volunteera.client;

import java.util.ArrayList;
import java.util.List;

import org.jspecify.annotations.Nullable;

import net.minecraft.resources.Identifier;
import net.minecraft.util.Mth;

import dev.volunteera.api.Origin;
import dev.volunteera.api.Origins;
import dev.volunteera.net.Payloads;

/**
 * Client-side mirror of the last authoritative snapshot from the server.
 * Pure render data: no gameplay decisions are ever made from this class.
 */
public final class ClientState {
	public record AbilityView(Identifier id, int level, boolean active, int cooldownTicks) {
	}

	public record TrialView(Identifier id, int progress, int target) {
	}

	@Nullable
	public static volatile Identifier originId;
	public static volatile List<AbilityView> abilities = List.of();
	public static volatile List<TrialView> trials = List.of();
	public static volatile float flightEnergy = 100.0f;
	public static volatile int flightPhase;
	public static volatile int windCharges;
	public static volatile int windChargeMax;
	public static volatile boolean doused;
	public static volatile long receivedAtMillis;
	public static volatile int receivedCooldownTicks;

	private ClientState() {
	}

	public static void update(Payloads.SyncStatePayload payload) {
		originId = payload.originId();
		abilities = List.copyOf(payload.abilities());
		trials = List.copyOf(payload.trials());
		flightEnergy = Mth.clamp(payload.flightEnergy(), 0.0f, 100.0f);
		flightPhase = payload.flightPhase();
		windCharges = payload.windCharges();
		windChargeMax = payload.windChargeMax();
		doused = payload.doused();
		receivedAtMillis = Util_now();
		int maxCooldown = 0;
		for (AbilityView view : abilities) {
			maxCooldown = Math.max(maxCooldown, view.cooldownTicks());
		}
		receivedCooldownTicks = maxCooldown;
	}

	/** Cooldown seconds remaining for an ability, interpolated since the snapshot. */
	public static float cooldownSecondsRemaining(AbilityView view) {
		if (view.cooldownTicks() <= 0) {
			return 0;
		}
		float elapsedTicks = (Util_now() - receivedAtMillis) / 50.0f;
		return Math.max(0.0f, view.cooldownTicks() / 20.0f - elapsedTicks);
	}

	@Nullable
	public static Origin origin() {
		return Origins.get(originId);
	}

	public static List<AbilityView> abilitiesSafe() {
		List<AbilityView> snapshot = abilities;
		return snapshot == null ? List.of() : snapshot;
	}

	private static long Util_now() {
		return java.lang.System.currentTimeMillis();
	}

	public static List<AbilityView> copyForRender() {
		return new ArrayList<>(abilitiesSafe());
	}
}
