package dev.volunteera.config;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;

import dev.volunteera.VolunteeraMod;
import net.fabricmc.loader.api.FabricLoader;

/**
 * Server-side balancing config (config/volunteera.json). Only settings that
 * genuinely matter for balancing or server administration are exposed.
 * Missing fields fall back to defaults so an old/partial file never crashes
 * the server.
 */
public final class ModConfig {
	private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();
	private static ModConfig instance = new ModConfig();

	// ------------------------------------------------------------- gameplay

	/** Allow non-operators to switch origin via the menu. */
	public boolean allowOriginSwitch = true;
	/** XP-based auto upgrades of non-world abilities. */
	public boolean autoProgression = true;
	/** Global damage scaling for ability damage (1.0 = balanced default). */
	public float damageMultiplier = 1.0f;
	/** Global cooldown scaling (0.5 = half cooldowns). */
	public float cooldownMultiplier = 1.0f;
	/** Global hunger cost scaling. */
	public float hungerCostMultiplier = 1.0f;
	/** Seconds of "doused" debuff after Emberborn gets wet. */
	public int dousedSeconds = 30;
	/** Allow abilities to damage other players (PvP servers). */
	public boolean allowPvp = false;

	public Flight flight = new Flight();
	public Emberborn emberborn = new Emberborn();
	public Skyborne skyborne = new Skyborne();
	public Deepforge deepforge = new Deepforge();

	/** World-ability trial configuration; empty list = use built-in defaults. */
	public List<String> extraTrials = List.of();

	public static final class Flight {
		public float drainPerSecond = 4.0f;
		public float regenPerSecond = 10.0f;
		/** Drain factor above heat blocks while Thermal Currents is unlocked. */
		public float thermalDrainFactor = 0.15f;
	}

	public static final class Emberborn {
		/** Radiance spawn-suppression radii per level (blocks). */
		public int[] radianceRadii = {8, 12, 16};
		/** Radiance hunger drain per second per level. */
		public float[] radianceDrain = {0.4f, 0.5f, 0.6f};
	}

	public static final class Skyborne {
		/** Gale Burst charge pool size and ground-refill time per level. */
		public int[] galeMaxCharges = {1, 2, 3};
		public int[] galeRefillSeconds = {6, 5, 4};
		/** Seconds of flight energy required for Tempest Dive. */
		public float tempestMinEnergy = 25.0f;
	}

	public static final class Deepforge {
		/** Vein Sense scan radius per level (blocks, horizontal). */
		public int[] veinSenseRadius = {10, 14, 18};
		/** Vein Sense food cost per pulse (per level). */
		public int[] veinSenseCost = {1, 1, 2};
		/** Stonewrought Lantern spawn-suppression radius per level. */
		public int[] lanternRadius = {6, 10, 14};
	}

	public static ModConfig get() {
		return instance;
	}

	public static void load() {
		Path path = path();
		if (Files.exists(path)) {
			try {
				ModConfig read = GSON.fromJson(Files.readString(path), ModConfig.class);
				if (read != null) {
					instance = sanitize(read);
				}
			} catch (Exception e) {
				VolunteeraMod.LOGGER.error("Could not read volunteera.json - using defaults", e);
				instance = new ModConfig();
			}
		}
		save();
	}

	public static void reload() {
		load();
	}

	private static ModConfig sanitize(ModConfig config) {
		// Merge in defaults for missing sub-objects (old config files).
		if (config.flight == null) config.flight = new Flight();
		if (config.emberborn == null) config.emberborn = new Emberborn();
		if (config.skyborne == null) config.skyborne = new Skyborne();
		if (config.deepforge == null) config.deepforge = new Deepforge();
		config.damageMultiplier = clamp(config.damageMultiplier, 0.0f, 10.0f);
		config.cooldownMultiplier = clamp(config.cooldownMultiplier, 0.0f, 10.0f);
		config.hungerCostMultiplier = clamp(config.hungerCostMultiplier, 0.0f, 5.0f);
		config.flight.drainPerSecond = clamp(config.flight.drainPerSecond, 0.1f, 100.0f);
		config.flight.regenPerSecond = clamp(config.flight.regenPerSecond, 0.1f, 100.0f);
		return config;
	}

	private static float clamp(float v, float min, float max) {
		return Float.isNaN(v) ? min : Math.max(min, Math.min(max, v));
	}

	public static void save() {
		try {
			Files.createDirectories(path().getParent());
			Files.writeString(path(), GSON.toJson(instance));
		} catch (IOException e) {
			VolunteeraMod.LOGGER.error("Could not write volunteera.json", e);
		}
	}

	private static Path path() {
		return FabricLoader.getInstance().getConfigDir().resolve("volunteera.json");
	}
}
